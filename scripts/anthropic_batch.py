"""Small, content-agnostic helpers for Anthropic Message Batches.

The pipeline scripts keep all prompt construction and result validation locally;
this module only submits requests, polls without exposing payloads, and returns
each independent message (or a classified per-item error) by ``custom_id``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone


DEFAULT_POLL_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 60.0 * 60.0


# Statuses that mean the batch is still running provider-side. `canceling` is a
# live state, not a terminal one: requests already completed inside it are billed
# and remain retrievable once it ends.
_PENDING_STATUSES = ("in_progress", "canceling")

_ERROR_STATUS = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "billing_error": 402,
    "permission_error": 403,
    "not_found_error": 404,
    "rate_limit_error": 429,
    "gateway_timeout_error": 504,
    "api_error": 500,
    "overloaded_error": 529,
    "canceled": 499,
    "expired": 504,
}


class BatchItemError(Exception):
    """A single request inside an otherwise completed batch did not succeed."""

    def __init__(self, error_type: str, message: str = "") -> None:
        self.error_type = error_type or "unknown_batch_error"
        self.status_code = _ERROR_STATUS.get(self.error_type)
        self.message = message or self.error_type
        super().__init__(self.message)


@dataclass(frozen=True)
class BatchRun:
    batch_id: str
    results: dict[str, object]


def _batch_error(result) -> BatchItemError:
    result_type = getattr(result, "type", "unknown_batch_error")
    if result_type == "errored":
        response = getattr(result, "error", None)
        error = getattr(response, "error", None)
        error_type = getattr(error, "type", "unknown_batch_error")
        message = getattr(error, "message", "")
        return BatchItemError(error_type, message)
    return BatchItemError(result_type, f"batch request {result_type}")


# --------------------------------------------------------------------------- #
# Self-describing custom ids
# --------------------------------------------------------------------------- #
#
# A batch keeps running and billing after the caller dies. On GitHub Actions the
# runner's filesystem dies with it, and the daily workflow commits only at the
# very end, so a batch id written to a local file is NOT recoverable — the next
# run starts from a fresh checkout that never saw it.
#
# Encoding the identity of the work INTO the custom_id makes recovery independent
# of any local state: the next run lists recent batches server-side, reads the
# custom_ids straight off the results, and can tell both what each result is for
# and whether it is still valid. Results stay retrievable for 29 days.
#
# Layout: "{kind}-{item_id}-{hash_prefix}-{mask}". item_id may itself contain "-",
# so parse from the right. Anthropic allows 64 characters; this project's ids are
# 20, so a 16-hex-digit prefix lands at 41-42 characters with room to spare.
#
# The prefix is what proves a recovered result still belongs to the item's current
# source. 16 hex digits is 64 bits: with a corpus of a few thousand items the
# chance of two different sources sharing a prefix is negligible. Reading is
# length-agnostic (`startswith`), so ids written with the earlier 12-digit prefix
# still validate correctly.

CUSTOM_ID_MAX_LEN = 64
SOURCE_HASH_PREFIX_LEN = 16


def format_custom_id(kind: str, item_id: str, source_hash: str, mask: str = "f") -> str:
    """Build a custom_id that identifies the item and the source it was built from."""
    custom_id = f"{kind}-{item_id}-{source_hash[:SOURCE_HASH_PREFIX_LEN]}-{mask}"
    if len(custom_id) > CUSTOM_ID_MAX_LEN or not item_id:
        raise ValueError("custom_id does not fit the provider limit")
    return custom_id


def parse_custom_id(custom_id: str, kind: str) -> dict | None:
    """Decode one of our custom_ids, or None if it belongs to something else."""
    if not isinstance(custom_id, str) or not custom_id.startswith(f"{kind}-"):
        return None
    parts = custom_id.split("-")
    if len(parts) < 4:
        return None
    return {
        "kind": parts[0],
        "item_id": "-".join(parts[1:-2]),
        "source_hash_prefix": parts[-2],
        "mask": parts[-1],
    }


class BatchDiscoveryUnavailable(Exception):
    """The provider batch list could not be read.

    Distinct from "the list is empty". In batch mode this is fatal: without the
    list we can neither reclaim a batch we already paid for nor tell whether one
    is still running, so submitting new work risks paying twice.
    """


DEFAULT_DISCOVERY_MAX_AGE_DAYS = 3


def batch_age_days(batch, now=None) -> float | None:
    """Age of a batch in days, or None when the provider gave no timestamp."""
    created = getattr(batch, "created_at", None)
    if created is None:
        return None
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(created, datetime):
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return (reference - created).total_seconds() / 86400.0


def list_recent_batches(client, *, limit: int = 20, logger=None) -> list:
    """Recent Message Batch objects, newest first.

    Raises BatchDiscoveryUnavailable when the list cannot be read, so callers can
    fail closed rather than silently continue without recovery or interlock.
    """
    try:
        page = client.messages.batches.list(limit=limit)
    except Exception as exc:
        if logger:
            logger.warning("BATCH discovery unavailable (%s)", type(exc).__name__)
        raise BatchDiscoveryUnavailable(type(exc).__name__) from exc
    try:
        return list(page)[:limit]
    except TypeError:
        return list(getattr(page, "data", []) or [])[:limit]


def pending_batches(client, *, limit: int = 20, logger=None) -> list[str]:
    """Ids of batches that are still running in this API key's workspace.

    A batch that has not ended yet cannot be attributed to a caller: its results
    — and therefore its custom_ids — are only readable once it ends. So a run
    that lost its local record cannot tell whether an in-flight batch is its own.
    The only safe reading is "something is still running here", which is why the
    caller must refuse to submit rather than guess.

    Precise on a workspace dedicated to one pipeline; conservative (it may block
    on someone else's batch) on a shared one, which is the safe direction.
    """
    running = []
    for batch in list_recent_batches(client, limit=limit, logger=logger):
        if getattr(batch, "processing_status", None) in _PENDING_STATUSES:
            batch_id = getattr(batch, "id", "")
            if batch_id:
                running.append(batch_id)
    return running


def read_batch_results(client, batch_id: str, *, logger=None) -> dict[str, object]:
    """Decode an ended batch's results by custom_id without waiting."""
    decoded: dict[str, object] = {}
    try:
        rows = client.messages.batches.results(batch_id)
    except Exception as exc:
        if logger:
            logger.info("BATCH results unavailable id=%s (%s)", batch_id, type(exc).__name__)
        return decoded
    for row in rows:
        custom_id = getattr(row, "custom_id", "")
        result = getattr(row, "result", None)
        if not custom_id or result is None:
            continue
        if getattr(result, "type", None) == "succeeded":
            decoded[custom_id] = getattr(result, "message", None)
        else:
            decoded[custom_id] = _batch_error(result)
    return decoded


# --------------------------------------------------------------------------- #
# Pre-flight spend bound
# --------------------------------------------------------------------------- #
#
# The measured cost cap works by reading `usage` off each response, which a batch
# does not give back until it finishes — so `--max-cost-usd` and `--batch` are
# mutually exclusive. Bounding the spend BEFORE submitting removes that conflict.
#
# `count_tokens` is free but, per Anthropic's own documentation, an estimate that
# "may differ slightly" from the input tokens actually billed. It is therefore
# multiplied by a safety margin, and the output side is bounded by the hard
# `max_tokens` ceiling (the model cannot exceed it). This is a conservative
# bound, not a guarantee: Anthropic notes that highly parallel batch processing
# can overshoot a configured workspace spend limit slightly.

DEFAULT_TOKEN_ESTIMATE_MARGIN = 1.10


def _fallback_input_tokens(params: dict) -> int:
    """Character-based upper estimate used when count_tokens is unavailable."""
    text = ""
    system = params.get("system")
    if isinstance(system, str):
        text += system
    elif isinstance(system, list):
        text += "".join(block.get("text", "") for block in system if isinstance(block, dict))
    for message in params.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str):
            text += content
        elif isinstance(content, list):
            text += "".join(b.get("text", "") for b in content if isinstance(b, dict))
    cjk = sum(1 for ch in text if "　" <= ch <= "鿿" or "＀" <= ch <= "￯")
    return int(cjk + (len(text) - cjk) / 3)  # /3 rather than /4: deliberately high


def estimate_request_input_tokens(client, params: dict, *, logger=None) -> int:
    """Best available input-token estimate for one request, never raising."""
    try:
        counted = client.messages.count_tokens(
            model=params["model"],
            system=params.get("system"),
            messages=params.get("messages") or [],
        )
        value = int(getattr(counted, "input_tokens", 0) or 0)
        if value > 0:
            return value
    except Exception as exc:
        if logger:
            logger.info("PREFLIGHT count_tokens unavailable (%s); using a high estimate",
                        type(exc).__name__)
    return _fallback_input_tokens(params)


def preflight_batch_cost_usd(
    client,
    requests: list[dict],
    price: dict,
    *,
    max_output_tokens: int,
    batch: bool = True,
    margin: float = DEFAULT_TOKEN_ESTIMATE_MARGIN,
    logger=None,
) -> list[float]:
    """Per-request conservative cost bound, in submission order.

    Input is counted (with a margin, because count_tokens is an estimate); output
    is charged at the full `max_tokens` ceiling because that is the only figure
    knowable before generation.
    """
    multiplier = 0.5 if batch else 1.0
    bounds = []
    for request in requests:
        params = request.get("params") or {}
        tokens = estimate_request_input_tokens(client, params, logger=logger)
        ceiling = int(params.get("max_tokens") or max_output_tokens)
        bounds.append(
            multiplier
            * (tokens * margin * price["input"] + ceiling * price["output"])
            / 1_000_000
        )
    return bounds


def trim_requests_to_budget(requests: list[dict], bounds: list[float], budget: float) -> int:
    """How many leading requests fit inside `budget`. 0 means submit nothing."""
    total = 0.0
    for index, bound in enumerate(bounds):
        if total + bound > budget:
            return index
        total += bound
    return len(requests)


def submit_message_batch(client, requests: list[dict], *, logger=None) -> str:
    """Create a Message Batch and return its id WITHOUT waiting for completion.

    Split out from run_message_batch so a caller can durably record the batch id
    before blocking. A batch keeps running (and billing) provider-side even if the
    caller dies, so a caller that has not persisted the id cannot collect what it
    already paid for and would submit the same work again on the next run.
    """
    if not requests:
        return ""
    batch = client.messages.batches.create(requests=requests)
    batch_id = getattr(batch, "id", "")
    if not batch_id:
        raise RuntimeError("provider returned a batch without an id")
    if logger:
        logger.info("BATCH submitted id=%s requests=%d", batch_id, len(requests))
    return batch_id


def collect_message_batch(
    client,
    batch_id: str,
    custom_ids: list[str],
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    logger=None,
    cancel_on_timeout: bool = False,
) -> BatchRun:
    """Wait for an already-submitted batch id and decode its results.

    Used both for the batch this run submitted and, on a later run, to reclaim a
    batch whose results were never collected (cancelled workflow, runner loss).
    """
    if not batch_id:
        return BatchRun(batch_id="", results={})
    if poll_seconds < 0 or timeout_seconds <= 0:
        raise ValueError("batch poll/timeout values must be positive")
    batch = client.messages.batches.retrieve(batch_id)
    return _wait_for_message_batch(
        client,
        batch,
        [{"custom_id": custom_id} for custom_id in custom_ids],
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
        logger=logger,
        cancel_on_timeout=cancel_on_timeout,
    )


def run_message_batch(
    client,
    requests: list[dict],
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    logger=None,
    on_submit=None,
    cancel_on_timeout: bool = False,
) -> BatchRun:
    """Submit a Message Batch, wait for completion, and return every result.

    ``requests`` uses the SDK's standard ``[{custom_id, params}, ...]`` shape.
    ``on_submit(batch_id)``, when given, is called as soon as the batch id exists
    and before any waiting, so the caller can persist it for later reclamation.
    Prompt or source content is deliberately never logged here.
    """
    if not requests:
        return BatchRun(batch_id="", results={})
    if poll_seconds < 0 or timeout_seconds <= 0:
        raise ValueError("batch poll/timeout values must be positive")

    batch = client.messages.batches.create(requests=requests)
    batch_id = getattr(batch, "id", "")
    if not batch_id:
        raise RuntimeError("provider returned a batch without an id")
    if logger:
        logger.info("BATCH submitted id=%s requests=%d", batch_id, len(requests))
    if on_submit is not None:
        on_submit(batch_id)

    return _wait_for_message_batch(
        client,
        batch,
        requests,
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
        logger=logger,
        cancel_on_timeout=cancel_on_timeout,
    )


def _wait_for_message_batch(
    client,
    batch,
    requests: list[dict],
    *,
    poll_seconds: float,
    timeout_seconds: float,
    logger=None,
    cancel_on_timeout: bool = False,
) -> BatchRun:
    """Wait for an already-created Message Batch and decode its results."""
    batch_id = getattr(batch, "id", "")
    if not batch_id:
        raise RuntimeError("provider returned a batch without an id")
    deadline = time.monotonic() + timeout_seconds
    while getattr(batch, "processing_status", None) in _PENDING_STATUSES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # A local polling timeout is NOT a reason to cancel. Anthropic runs a
            # batch for up to 24 hours and keeps its results retrievable for 29
            # days, so a batch that has not finished within our local wait is
            # simply still working. Cancelling would throw away requests already
            # completed and already billed, and would have to be re-submitted and
            # re-paid. Stop waiting, leave the batch running, and let the next run
            # collect it (see list_recent_batches / read_batch_results).
            # `cancel_on_timeout=True` restores the old behaviour for a caller
            # that really does want to stop provider-side work.
            if cancel_on_timeout:
                cancel_error = None
                try:
                    client.messages.batches.cancel(batch_id)
                except Exception as exc:  # retain the primary timeout failure
                    cancel_error = type(exc).__name__
                if logger:
                    logger.error("BATCH timeout id=%s cancel_requested=true cancel_failed=%s",
                                 batch_id, cancel_error)
            elif logger:
                logger.warning(
                    "BATCH timeout id=%s left running; results stay retrievable and "
                    "will be collected on a later run.", batch_id,
                )
            raise TimeoutError(f"message batch {batch_id} did not finish within {timeout_seconds:g}s")
        time.sleep(min(poll_seconds, remaining))
        batch = client.messages.batches.retrieve(batch_id)

    if getattr(batch, "processing_status", None) != "ended":
        raise RuntimeError(
            f"message batch {batch_id} ended in unexpected state "
            f"{getattr(batch, 'processing_status', None)!r}"
        )

    decoded: dict[str, object] = {}
    for row in client.messages.batches.results(batch_id):
        custom_id = getattr(row, "custom_id", "")
        result = getattr(row, "result", None)
        if not custom_id or result is None:
            continue
        if getattr(result, "type", None) == "succeeded":
            decoded[custom_id] = getattr(result, "message", None)
        else:
            decoded[custom_id] = _batch_error(result)

    for request in requests:
        custom_id = request.get("custom_id", "")
        if custom_id and custom_id not in decoded:
            decoded[custom_id] = BatchItemError("missing_result", "batch result was missing")
    if logger:
        succeeded = sum(not isinstance(value, Exception) for value in decoded.values())
        logger.info("BATCH ended id=%s succeeded=%d failed=%d", batch_id, succeeded, len(decoded) - succeeded)
    return BatchRun(batch_id=batch_id, results=decoded)
