"""OpenAI Responses API adapter for the Stage 3 / Stage 4 direct-call path.

The summarizer and translator were written against Anthropic's Messages API,
and their parsing, refusal handling, usage accounting and cost guard all read a
Messages-shaped response. Rather than fork that logic per provider, this module
sends the same system prompt, user content and JSON schema to OpenAI and wraps
the reply in a small object with the same shape (``content[0].text``,
``stop_reason``, ``usage.input_tokens`` ...), so everything downstream --
validation, the title gate, the cache, the cost cap -- runs unchanged.

Provider selection is by model id alone (``gpt-*``). The default model in each
stage stays a Claude model, so nothing changes unless a run is explicitly given
an OpenAI model, and reverting is the same one-line model change.

Scope, deliberately: direct (synchronous) calls only. The Anthropic Message
Batch path carries provider-side recovery and a submission interlock that took
several iterations to get right; an OpenAI Batch path is only worth building if
the A/B evaluation shows the model is worth adopting. Until then the stages
refuse ``--batch`` with an OpenAI model instead of silently falling back.

Facts used here (developers.openai.com, checked 2026-10-16):
- gpt-6.1-sol: $2 input / $10 output / $0.10 cached read / $2.50 cache write
  per MTok; Batch and Flex 50% lower; reasoning effort low|medium|high|xhigh|max
  (default medium) -- `none` and `minimal` are NOT accepted, so reasoning
  cannot be switched off.
- Reasoning tokens are billed as output and count against max_output_tokens;
  a response that runs out is `status: incomplete` and may carry no text.
- Usage: input_tokens INCLUDES cached reads and cache writes
  (input_tokens_details.cached_tokens / .cache_write_tokens).
- temperature / top_p are rejected when reasoning is on; none are sent.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

OPENAI_MODEL_PREFIXES = ("gpt-",)

# Lowest effort GPT-6.1 Sol accepts. Every request here renders a notice into a
# fixed JSON schema, so -- as with Claude's lowest thinking setting -- the least
# reasoning the model allows is asked for. Overridable for the evaluation only.
DEFAULT_REASONING_EFFORT = "low"
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# max_output_tokens covers reasoning AND the visible JSON. The stages size their
# ceilings for visible text only (1500 for a summary), so that same number here
# would let reasoning starve the answer and bill for an empty response. This is
# added on top of the stage's ceiling; the evaluation measures what is used.
DEFAULT_REASONING_HEADROOM_TOKENS = 4000

_INSUFFICIENT_QUOTA_CODES = ("insufficient_quota", "billing_hard_limit_reached")


def is_openai_model(model: str | None) -> bool:
    return bool(model) and str(model).startswith(OPENAI_MODEL_PREFIXES)


def reasoning_effort() -> str:
    effort = (os.environ.get("OPENAI_REASONING_EFFORT") or DEFAULT_REASONING_EFFORT).strip()
    if effort not in REASONING_EFFORTS:
        raise ValueError(f"OPENAI_REASONING_EFFORT must be one of {', '.join(REASONING_EFFORTS)}")
    return effort


def reasoning_headroom_tokens() -> int:
    raw = os.environ.get("OPENAI_REASONING_HEADROOM_TOKENS")
    if not raw:
        return DEFAULT_REASONING_HEADROOM_TOKENS
    value = int(raw)
    if value < 0:
        raise ValueError("OPENAI_REASONING_HEADROOM_TOKENS must be >= 0")
    return value


def max_output_tokens(visible_ceiling: int) -> int:
    return int(visible_ceiling) + reasoning_headroom_tokens()


def api_key_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def make_client():
    """Construct an OpenAI client. Reads OPENAI_API_KEY from the environment."""
    import openai  # local import: Claude-only runs need no OpenAI SDK
    return openai.OpenAI()


def is_insufficient_quota(exc) -> bool:
    """OpenAI reports exhausted credit as HTTP 429 `insufficient_quota`.

    Left to the generic status mapping that is a retryable `rate_limit`, so a
    run with no credit would keep calling instead of stopping at the first
    failure. Classification only; the body is never logged.
    """
    codes = [getattr(exc, "code", None)]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        codes.append(error.get("code"))
        codes.append(error.get("type"))
    return any(code in _INSUFFICIENT_QUOTA_CODES for code in codes if isinstance(code, str))


def _system_text(system) -> str:
    """Accept a plain string or Anthropic-style text blocks (cache_control dropped).

    OpenAI caches a stable prefix automatically, so the explicit breakpoint the
    translator sets for Claude has no equivalent to carry over.
    """
    if isinstance(system, str):
        return system
    return "\n\n".join(block.get("text", "") for block in system if isinstance(block, dict))


def _usage(resp) -> SimpleNamespace:
    usage = getattr(resp, "usage", None)
    total_in = int(getattr(usage, "input_tokens", 0) or 0)
    in_details = getattr(usage, "input_tokens_details", None)
    cached = int(getattr(in_details, "cached_tokens", 0) or 0)
    written = int(getattr(in_details, "cache_write_tokens", 0) or 0)
    out_details = getattr(usage, "output_tokens_details", None)
    # Anthropic semantics, which the cost estimators expect: input_tokens is the
    # uncached remainder, and cache reads/writes are priced separately.
    return SimpleNamespace(
        input_tokens=max(0, total_in - cached - written),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_input_tokens=cached,
        cache_creation_input_tokens=written,
        reasoning_tokens=int(getattr(out_details, "reasoning_tokens", 0) or 0),
    )


class IncompleteResponse(ValueError):
    """The response stopped before the JSON was complete.

    A ValueError so both stages classify it as an item validation failure:
    the item keeps its fallback and the run continues.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason or "unknown"
        super().__init__(f"incomplete response (reason={self.reason})")


def to_message(resp, model: str) -> SimpleNamespace:
    """Wrap a Responses API result in the Messages shape the stages parse."""
    texts, refusal = [], False
    for out in getattr(resp, "output", None) or []:
        if getattr(out, "type", None) != "message":
            continue
        for part in getattr(out, "content", None) or []:
            kind = getattr(part, "type", None)
            if kind == "output_text":
                texts.append(getattr(part, "text", "") or "")
            elif kind == "refusal":
                refusal = True
    status = getattr(resp, "status", None)
    reason = getattr(getattr(resp, "incomplete_details", None), "reason", None)
    if refusal or reason == "content_filter":
        stop_reason = "refusal"
    elif status == "incomplete":
        raise IncompleteResponse(reason)
    else:
        stop_reason = "end_turn"
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="".join(texts))],
        model=getattr(resp, "model", None) or model,
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="openai_refusal") if stop_reason == "refusal" else None,
        usage=_usage(resp),
    )


def request_params(
    *, model: str, system, user: str, schema: dict, schema_name: str,
    visible_max_tokens: int, cache_key: str,
) -> dict:
    """Responses API parameters equivalent to one Messages structured-output call."""
    return {
        "model": model,
        "instructions": _system_text(system),
        "input": user,
        "reasoning": {"effort": reasoning_effort()},
        "max_output_tokens": max_output_tokens(visible_max_tokens),
        "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        # Groups requests that share the stable system prompt so the prefix cache
        # is reused; carries no item data.
        "prompt_cache_key": cache_key,
        # Responses are not retained by OpenAI for later retrieval; nothing here
        # needs them after the call returns.
        "store": False,
    }


def create_structured(client, **kwargs) -> SimpleNamespace:
    """One synchronous structured-output call, returned in the Messages shape."""
    params = request_params(**kwargs)
    return to_message(client.responses.create(**params), params["model"])
