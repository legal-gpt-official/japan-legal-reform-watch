"""Small, content-agnostic helpers for Anthropic Message Batches.

The pipeline scripts keep all prompt construction and result validation locally;
this module only submits requests, polls without exposing payloads, and returns
each independent message (or a classified per-item error) by ``custom_id``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


DEFAULT_POLL_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 60.0 * 60.0


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


def run_message_batch(
    client,
    requests: list[dict],
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    logger=None,
) -> BatchRun:
    """Submit a Message Batch, wait for completion, and return every result.

    ``requests`` uses the SDK's standard ``[{custom_id, params}, ...]`` shape.
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

    return _wait_for_message_batch(
        client,
        batch,
        requests,
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
        logger=logger,
    )


def _request_count(batch) -> int:
    counts = getattr(batch, "request_counts", None)
    return sum(
        int(getattr(counts, field, 0) or 0)
        for field in ("processing", "succeeded", "errored", "canceled", "expired")
    )


def resume_latest_message_batch(
    client,
    requests: list[dict],
    *,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    logger=None,
) -> BatchRun:
    """Resume the newest provider batch with the expected request count.

    Data-writing workflows are serialized, so the newest matching batch is the
    one submitted by the immediately preceding timed-out run. No new requests
    are submitted by this function.
    """
    if not requests:
        return BatchRun(batch_id="", results={})
    page = client.messages.batches.list(limit=100)
    batches = list(getattr(page, "data", page) or [])
    batch = next((row for row in batches if _request_count(row) == len(requests)), None)
    if batch is None:
        raise RuntimeError(f"no resumable message batch found for {len(requests)} requests")
    if logger:
        logger.info("BATCH resuming id=%s requests=%d", getattr(batch, "id", ""), len(requests))
    return _wait_for_message_batch(
        client,
        batch,
        requests,
        poll_seconds=poll_seconds,
        timeout_seconds=timeout_seconds,
        logger=logger,
    )


def _wait_for_message_batch(
    client,
    batch,
    requests: list[dict],
    *,
    poll_seconds: float,
    timeout_seconds: float,
    logger=None,
) -> BatchRun:
    """Wait for an already-created Message Batch and decode its results."""
    batch_id = getattr(batch, "id", "")
    if not batch_id:
        raise RuntimeError("provider returned a batch without an id")
    deadline = time.monotonic() + timeout_seconds
    while getattr(batch, "processing_status", None) == "in_progress":
        remaining = deadline - time.monotonic()
        if remaining <= 0:
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
