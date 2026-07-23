"""Structured public-comment deadline parsing and stage resolution.

Only the fixed deadline label supplied by the e-Gov public-comment RSS feed is
accepted as a fallback for legacy raw records. General prose and unrelated date
fields are deliberately ignored.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
EGOV_PUBLIC_COMMENT_SOURCE_TYPE = "public_comment_rss"
EGOV_DEADLINE_LABEL = "受付締切日時："
EGOV_DEADLINE_FOLLOWING_LABEL = " カテゴリー："

_EGOV_DEADLINE_FORMATS = (
    ("%Y/%m/%d %H:%M:%S", True),
    ("%Y/%m/%d %H:%M", True),
    ("%Y/%m/%d", False),
)


def normalize_comment_deadline(value: object) -> str | None:
    """Return a strict ISO date/date-time deadline, or None when invalid.

    Date-times without an explicit zone are interpreted as JST because the
    source is a Japanese public-comment service. Explicit offsets and ``Z`` are
    preserved as instants.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()

    if len(text) == 10:
        try:
            parsed_date = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            return None
        return parsed_date.isoformat()

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.isoformat()


def extract_egov_comment_deadline(raw_summary: object, source_type: object) -> str | None:
    """Extract the fixed e-Gov RSS deadline field from a legacy raw summary.

    This is intentionally source-gated and label-gated. It is not a general
    date extractor and will not inspect arbitrary prose.
    """
    if source_type != EGOV_PUBLIC_COMMENT_SOURCE_TYPE or not isinstance(raw_summary, str):
        return None
    before, marker, after = raw_summary.partition(EGOV_DEADLINE_LABEL)
    if not marker or not before:
        return None
    deadline_text, following_marker, _ = after.partition(EGOV_DEADLINE_FOLLOWING_LABEL)
    if not following_marker:
        return None
    deadline_text = deadline_text.strip()

    for date_format, has_time in _EGOV_DEADLINE_FORMATS:
        try:
            parsed = datetime.strptime(deadline_text, date_format)
        except ValueError:
            continue
        if has_time:
            return parsed.replace(tzinfo=JST).isoformat()
        return parsed.date().isoformat()
    return None


def has_egov_deadline_label(raw_summary: object, source_type: object) -> bool:
    """Whether a raw record claims to contain the fixed e-Gov deadline field."""
    return (
        source_type == EGOV_PUBLIC_COMMENT_SOURCE_TYPE
        and isinstance(raw_summary, str)
        and EGOV_DEADLINE_LABEL in raw_summary
    )


def resolve_public_comment_stage(
    stage: str,
    comment_deadline: object,
    *,
    now: datetime,
) -> str:
    """Close an open public comment at a valid structured deadline.

    A date-only deadline remains open through the end of that date in JST.
    An explicit date-time is compared as an instant, respecting its offset.
    """
    if stage != "Public Comment Open":
        return stage

    normalized = normalize_comment_deadline(comment_deadline)
    if normalized is None:
        return stage

    current = now if now.tzinfo is not None else now.replace(tzinfo=JST)
    if len(normalized) == 10:
        deadline_date = datetime.strptime(normalized, "%Y-%m-%d").date()
        closes_at = datetime.combine(deadline_date + timedelta(days=1), datetime.min.time(), JST)
        return "Public Comment Closed" if current.astimezone(JST) >= closes_at else stage

    deadline_at = datetime.fromisoformat(normalized)
    return "Public Comment Closed" if current >= deadline_at else stage
