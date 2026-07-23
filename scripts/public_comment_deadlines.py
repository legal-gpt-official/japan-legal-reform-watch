"""Structured public-comment deadline parsing and stage resolution.

Only the fixed deadline label supplied by the e-Gov public-comment RSS feed is
accepted as a fallback for legacy raw records. General prose and unrelated date
fields are deliberately ignored.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
EGOV_PUBLIC_COMMENT_SOURCE_TYPE = "public_comment_rss"
EGOV_DEADLINE_LABEL = "受付締切日時："
EGOV_DEADLINE_FOLLOWING_LABEL = " カテゴリー："
EGOV_CONTACT_LABEL = "問合せ先（所管省庁・部局名等）："

DEADLINE_SOURCE_METADATA = "source_metadata"
DEADLINE_SOURCE_RELATED_EGOV = "related_egov_item"
MAX_RELATED_PUBLICATION_DATE_DELTA_DAYS = 1

_PUBLIC_COMMENT_STAGES = {"Public Comment Open", "Public Comment Closed"}
_TITLE_IGNORED_RE = re.compile(
    r"[\s\u3000、。，．・:：;；!！?？「」『』【】〔〕（）()\[\]<>〈〉《》]+"
)


@dataclass(frozen=True)
class DeadlinePropagationStats:
    """Auditable Stage 2 counts for conservative related-item propagation."""

    direct_deadline_items: int = 0
    inherited_deadline_items: int = 0
    inherited_and_closed_items: int = 0
    inherited_but_still_open_items: int = 0
    ambiguous_related_matches: int = 0
    conflicting_deadline_matches: int = 0
    unmatched_open_public_comments: int = 0
    invalid_related_deadlines: int = 0


@dataclass(frozen=True)
class _AgencyRule:
    key: str
    contact_markers: tuple[str, ...]
    source_markers: tuple[str, ...]
    official_domains: tuple[str, ...]


_AGENCY_RULES = (
    _AgencyRule(
        "fsa",
        ("金融庁",),
        ("Financial Services Agency", "金融庁", "FSA"),
        ("fsa.go.jp",),
    ),
    _AgencyRule(
        "meti",
        ("経済産業省",),
        ("経済産業省", "METI"),
        ("meti.go.jp",),
    ),
    _AgencyRule(
        "mhlw",
        ("厚生労働省",),
        ("Ministry of Health, Labour and Welfare", "厚生労働省", "MHLW"),
        ("mhlw.go.jp",),
    ),
    _AgencyRule(
        "digital-agency",
        ("デジタル庁",),
        ("Digital Agency", "デジタル庁"),
        ("digital.go.jp",),
    ),
    _AgencyRule(
        "caa",
        ("消費者庁",),
        ("消費者庁", "CAA"),
        ("caa.go.jp",),
    ),
    _AgencyRule(
        "ppc",
        ("個人情報保護委員会",),
        ("個人情報保護委員会", "PPC"),
        ("ppc.go.jp",),
    ),
    _AgencyRule(
        "jftc",
        ("公正取引委員会",),
        ("公正取引委員会", "JFTC"),
        ("jftc.go.jp",),
    ),
    _AgencyRule(
        "moj",
        ("法務省",),
        ("法務省", "MOJ"),
        ("moj.go.jp",),
    ),
    _AgencyRule(
        "moe",
        ("環境省",),
        ("環境省", "MOE"),
        ("env.go.jp",),
    ),
    _AgencyRule(
        "mof",
        ("財務省",),
        ("財務省", "MOF"),
        ("mof.go.jp",),
    ),
    _AgencyRule(
        "mic",
        ("総務省",),
        ("総務省", "MIC"),
        ("soumu.go.jp",),
    ),
    _AgencyRule(
        "mlit",
        ("国土交通省",),
        ("国土交通省", "MLIT"),
        ("mlit.go.jp",),
    ),
    _AgencyRule(
        "maff",
        ("農林水産省",),
        ("農林水産省", "MAFF"),
        ("maff.go.jp",),
    ),
)

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


def normalize_public_comment_title(title: object) -> str:
    """Return a conservative key for exact public-comment title matching.

    Only Unicode width compatibility, whitespace, and light punctuation are
    normalized. Legal terms, amendment actions, years, identifiers, and public
    comment boilerplate are deliberately retained. This is not fuzzy matching.
    """
    if not isinstance(title, str):
        return ""
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return _TITLE_IGNORED_RE.sub("", normalized)


def _hostname(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        return ""
    try:
        return (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return ""


def _hostname_matches(hostname: str, domains: tuple[str, ...]) -> bool:
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)


def _egov_contact_agencies(raw: dict) -> frozenset[str]:
    summary = raw.get("raw_summary")
    if not isinstance(summary, str):
        return frozenset()
    _, marker, contact = summary.partition(EGOV_CONTACT_LABEL)
    if not marker or not contact:
        return frozenset()
    return frozenset(
        rule.key
        for rule in _AGENCY_RULES
        if any(contact_marker in contact for contact_marker in rule.contact_markers)
    )


def _official_target_agency(raw: dict) -> str | None:
    source_name = raw.get("source_name")
    hostname = _hostname(raw.get("source_url"))
    if not isinstance(source_name, str) or not hostname:
        return None
    matches = [
        rule.key
        for rule in _AGENCY_RULES
        if any(marker in source_name for marker in rule.source_markers)
        and _hostname_matches(hostname, rule.official_domains)
    ]
    return matches[0] if len(matches) == 1 else None


def _publication_date(item: dict) -> date | None:
    value = item.get("published_at")
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _related_pair_is_high_confidence(
    target_item: dict,
    target_raw: dict,
    source_item: dict,
    source_raw: dict,
) -> bool:
    target_title = normalize_public_comment_title(target_item.get("title_ja"))
    source_title = normalize_public_comment_title(source_item.get("title_ja"))
    if not target_title or target_title != source_title:
        return False

    target_date = _publication_date(target_item)
    source_date = _publication_date(source_item)
    if target_date is None or source_date is None:
        return False
    if abs((target_date - source_date).days) > MAX_RELATED_PUBLICATION_DATE_DELTA_DAYS:
        return False

    target_agency = _official_target_agency(target_raw)
    if target_agency is None or target_agency not in _egov_contact_agencies(source_raw):
        return False

    return _hostname_matches(
        _hostname(source_raw.get("source_url")),
        ("public-comment.e-gov.go.jp",),
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


def propagate_public_comment_deadlines(
    items: list[dict],
    *,
    raw_by_id: dict[str, dict],
    deadline_state_by_id: dict[str, tuple[str | None, str]],
    now: datetime,
) -> tuple[list[dict], DeadlinePropagationStats]:
    """Inherit trusted e-Gov deadlines only across unique high-confidence pairs.

    The function returns shallow copies and never modifies raw records. Matching
    requires an exact conservative title key, publication dates within one day,
    an e-Gov contact agency aligned with the target source name and official
    domain, and a one-to-one relationship in both directions. Invalid,
    ambiguous, or conflicting candidates remain unchanged.
    """
    propagated = [dict(item) for item in items]
    item_by_id = {
        item.get("id"): item
        for item in propagated
        if isinstance(item.get("id"), str) and item.get("id")
    }

    # Normalize direct-deadline provenance before considering inheritance.
    for item_id, item in item_by_id.items():
        deadline, status = deadline_state_by_id.get(item_id, (None, "missing"))
        if status == "valid" and deadline and item.get("comment_deadline") == deadline:
            item["comment_deadline_source"] = DEADLINE_SOURCE_METADATA
            item["comment_deadline_inherited"] = False
            item.pop("comment_deadline_source_id", None)

    source_rows: list[tuple[dict, dict, str | None, str]] = []
    for item_id, item in item_by_id.items():
        raw = raw_by_id.get(item_id, {})
        if raw.get("source_type") != EGOV_PUBLIC_COMMENT_SOURCE_TYPE:
            continue
        if item.get("stage") not in _PUBLIC_COMMENT_STAGES:
            continue
        if not _egov_contact_agencies(raw):
            continue
        if not _hostname_matches(
            _hostname(raw.get("source_url")),
            ("public-comment.e-gov.go.jp",),
        ):
            continue
        deadline, status = deadline_state_by_id.get(item_id, (None, "missing"))
        if status in {"valid", "invalid"}:
            source_rows.append((item, raw, deadline, status))

    related_target_rows: list[tuple[dict, dict, str]] = []
    for item_id, item in item_by_id.items():
        raw = raw_by_id.get(item_id, {})
        if raw.get("source_type") == EGOV_PUBLIC_COMMENT_SOURCE_TYPE:
            continue
        if item.get("stage") not in _PUBLIC_COMMENT_STAGES:
            continue
        if _official_target_agency(raw) is None:
            continue
        _, target_deadline_status = deadline_state_by_id.get(item_id, (None, "missing"))
        related_target_rows.append((item, raw, target_deadline_status))

    target_rows = [
        row
        for row in related_target_rows
        if row[0].get("stage") == "Public Comment Open"
        and not row[0].get("comment_deadline")
    ]

    invalid_targets: set[str] = set()
    conflicting_targets: set[str] = set()
    ambiguous_targets: set[str] = set()
    tentative_matches: dict[str, tuple[dict, str]] = {}

    for target_item, target_raw, target_deadline_status in target_rows:
        target_id = target_item["id"]
        structural_candidates = [
            (source_item, deadline, status)
            for source_item, source_raw, deadline, status in source_rows
            if _related_pair_is_high_confidence(
                target_item,
                target_raw,
                source_item,
                source_raw,
            )
        ]
        if not structural_candidates:
            continue
        if target_deadline_status == "invalid" or any(
            status == "invalid" for _, _, status in structural_candidates
        ):
            invalid_targets.add(target_id)
            continue

        valid_candidates = [
            (source_item, deadline)
            for source_item, deadline, status in structural_candidates
            if status == "valid" and deadline
        ]
        distinct_deadlines = {deadline for _, deadline in valid_candidates}
        if len(distinct_deadlines) > 1:
            conflicting_targets.add(target_id)
            continue
        if len(valid_candidates) != 1:
            ambiguous_targets.add(target_id)
            continue
        source_item, deadline = valid_candidates[0]
        tentative_matches[target_id] = (source_item, deadline)

    # A source matching multiple ministry records is also ambiguous, including
    # a second official record that already has its own deadline or Closed
    # stage. Do not hide that ambiguity by considering eligible targets only.
    for target_id, (source_item, _) in tentative_matches.items():
        source_raw = raw_by_id.get(source_item["id"], {})
        related_targets = [
            target_item["id"]
            for target_item, target_raw, _ in related_target_rows
            if _related_pair_is_high_confidence(
                target_item,
                target_raw,
                source_item,
                source_raw,
            )
        ]
        if len(related_targets) > 1:
            ambiguous_targets.add(target_id)

    for target_id, (source_item, deadline) in tentative_matches.items():
        if target_id in ambiguous_targets:
            continue
        target_item = item_by_id[target_id]
        target_item["comment_deadline"] = deadline
        target_item["comment_deadline_source"] = DEADLINE_SOURCE_RELATED_EGOV
        target_item["comment_deadline_source_id"] = source_item["id"]
        target_item["comment_deadline_inherited"] = True
        target_item["stage"] = resolve_public_comment_stage(
            target_item["stage"],
            deadline,
            now=now,
        )

    direct_items = [
        item
        for item in propagated
        if item.get("comment_deadline_source") == DEADLINE_SOURCE_METADATA
        and item.get("comment_deadline_inherited") is False
    ]
    inherited_items = [
        item
        for item in propagated
        if item.get("comment_deadline_source") == DEADLINE_SOURCE_RELATED_EGOV
        and item.get("comment_deadline_inherited") is True
    ]
    stats = DeadlinePropagationStats(
        direct_deadline_items=len(direct_items),
        inherited_deadline_items=len(inherited_items),
        inherited_and_closed_items=sum(
            item.get("stage") == "Public Comment Closed" for item in inherited_items
        ),
        inherited_but_still_open_items=sum(
            item.get("stage") == "Public Comment Open" for item in inherited_items
        ),
        ambiguous_related_matches=len(ambiguous_targets),
        conflicting_deadline_matches=len(conflicting_targets),
        unmatched_open_public_comments=sum(
            item.get("stage") == "Public Comment Open"
            and normalize_comment_deadline(item.get("comment_deadline")) is None
            for item in propagated
        ),
        invalid_related_deadlines=len(invalid_targets),
    )
    return propagated, stats
