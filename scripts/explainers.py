#!/usr/bin/env python3
"""Hand-written reform explainers: storage, validation, queue, and operator CLI.

An explainer is a short plain-English account of ONE reform -- why it is
being made, how it got here, what is confirmed to come next, and what a
company may need to check -- written by a person or by a chat assistant
working from official sources, never by the daily pipeline's API stages.
Each lives in ``data/explainers/<dossier_id>.json``. No stage writes these
files, so the summary and translation stages can never overwrite one.

The facts the pipeline already knows are used as a check, not as content:

* ``related_item_ids`` tie an explainer to the raw records it covers. The
  **source fingerprint** is a hash over those records plus every record the
  conservative timeline (``law_timeline.py``) links to them and every
  lifecycle record sharing their official URL. A new related record -- a
  promulgation, a Diet status change, a new e-Gov entry -- changes the
  fingerprint, and the explainer is queued for re-verification.
* **Consistency checks** compare the explainer's stated stage and key dates
  with the official records: a promulgation date that differs from e-Gov, a
  pre-enactment stage on a bill the House lists as enacted, a pre-promulgation
  stage on a draft that e-Gov shows as promulgated, "in force" while a stated
  effective date is still ahead. Any contradiction withholds the explainer.

Publication is decided per content category (``PUBLISH_POLICY``): objective
facts from official sources, the general legislative procedure, the
reform-specific outlook, and the assessment of business impact may each
require a different review state. An explainer is shown only when its
``status`` is ``published``, validation finds no error, and its facts meet
the policy; any other category that does not meet the policy is left out of
the page. Everything else stays in the repository and is never rendered.
Rendering (``build_public_pages.py``) escapes every value; nothing here
emits HTML.

Subcommands (all offline, no API calls)::

    python scripts/explainers.py queue [--build] [--top N]
    python scripts/explainers.py context <item-id | dossier-id>
    python scripts/explainers.py scaffold <item-id> --id <dossier-id>
    python scripts/explainers.py validate [paths ...]
    python scripts/explainers.py stamp <dossier-id> --verified-on YYYY-MM-DD
    python scripts/explainers.py preview <dossier-id> --out page.html
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from alert_common import JST, safe_http_url, strict_iso_date, write_text_atomic  # noqa: E402
import law_timeline as lt  # noqa: E402

REPO_ROOT = SCRIPTS_DIR.parent
EXPLAINERS_DIR = REPO_ROOT / "data" / "explainers"
QUEUE_PATH = REPO_ROOT / "data" / "explainer_queue.json"
RAW_PATH = REPO_ROOT / "data" / "raw_items.json"
PUBLISHED_PATH = REPO_ROOT / "docs" / "data" / "legal_updates.json"

SCHEMA_VERSION = 1
DOSSIER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
INSTRUMENT_NUMBER_PATTERN = re.compile(
    r"^(令和|平成|昭和)(元|[〇一二三四五六七八九十百]+)年[^\s]{1,60}第[〇一二三四五六七八九十百千]+号$"
)
SOURCE_ID_PATTERN = re.compile(r"^s[0-9]{1,3}$")

STATUSES = ("draft", "published", "withdrawn", "skipped", "blocked")
# ``skipped``: judged not worth an explainer; ``blocked``: research was not
# possible. Both are decisions recorded against a fingerprint, so the queue
# stops offering the reform until its official records change.
DECISION_STATUSES = ("skipped", "blocked")
REVIEW_STATES = ("unreviewed", "ai_checked", "human_reviewed")
# Content categories, each published under its own minimum review state:
#   facts       -- background, history, key dates: what official sources state
#   procedure   -- "Expected procedural steps": the general procedure for the
#                  instrument type, not a forecast
#   outlook     -- "Confirmed next steps" and "Outstanding uncertainties" for
#                  this reform
#   assessment  -- "Business impact": who may be affected and what to check
# ``ai_checked`` means the writer completed the checklist in
# operations/EXPLAINER_OPERATIONS.md; ``human_reviewed`` needs a dated
# ``human_review`` record by a named reviewer, made no earlier than the
# explainer's ``verified_at``. Owner decision 2026-10-20: every category needs
# human review until the first explainers have been read. The intended later
# setting is facts/procedure/outlook at ai_checked and assessment at
# human_reviewed. The repository variable EXPLAINER_PUBLISH_POLICY (for
# example "facts=ai_checked,procedure=ai_checked") overrides single
# categories in the daily workflow without a code change.
CATEGORIES = ("facts", "procedure", "outlook", "assessment")
PUBLISH_POLICY = {
    "facts": "human_reviewed",
    "procedure": "human_reviewed",
    "outlook": "human_reviewed",
    "assessment": "human_reviewed",
}
POLICY_ENV = "EXPLAINER_PUBLISH_POLICY"

INSTRUMENT_TYPES = ("statute", "cabinet_order", "ministerial_ordinance", "notice", "guideline", "other")
STATUTE_STAGES = (
    "under_consideration",
    "bill_submitted",
    "diet_deliberation",
    "lapsed",
    "withdrawn",
    "enacted",
    "promulgated",
    "partially_in_force",
    "in_force",
)
SUBORDINATE_STAGES = (
    "under_consideration",
    "public_comment_open",
    "public_comment_closed",
    "comment_results_published",
    "promulgated",
    "partially_in_force",
    "in_force",
    "not_adopted",
)
GUIDELINE_STAGES = (
    "under_consideration",
    "public_comment_open",
    "public_comment_closed",
    "comment_results_published",
    "published",
    "in_effect",
    "not_adopted",
)
STAGES_BY_TYPE = {
    "statute": STATUTE_STAGES,
    "cabinet_order": SUBORDINATE_STAGES,
    "ministerial_ordinance": SUBORDINATE_STAGES,
    "notice": SUBORDINATE_STAGES,
    "guideline": GUIDELINE_STAGES,
    "other": tuple(dict.fromkeys(STATUTE_STAGES + SUBORDINATE_STAGES + GUIDELINE_STAGES)),
}
STAGE_LABELS = {
    "under_consideration": "Under consideration by the government",
    "bill_submitted": "Bill submitted to the Diet",
    "diet_deliberation": "Under Diet deliberation",
    "lapsed": "Bill not concluded (lapsed) in the Diet",
    "withdrawn": "Bill withdrawn",
    "enacted": "Enacted by the Diet, not yet promulgated",
    "promulgated": "Promulgated, not yet in force",
    "partially_in_force": "Partially in force",
    "in_force": "In force",
    "public_comment_open": "Draft open for public comment",
    "public_comment_closed": "Public comment period closed",
    "comment_results_published": "Public comment results published",
    "not_adopted": "Not adopted",
    "published": "Published, not yet in effect",
    "in_effect": "In effect",
}
PRE_ENACTMENT = frozenset({"under_consideration", "bill_submitted", "diet_deliberation"})
ENDED_BILL = frozenset({"lapsed", "withdrawn"})
PRE_PROMULGATION = frozenset(
    {"under_consideration", "public_comment_open", "public_comment_closed", "comment_results_published"}
)

KEY_DATE_KINDS = (
    "bill_submitted",
    "passed_house_of_representatives",
    "passed_house_of_councillors",
    "enacted",
    "promulgated",
    "effective",
    "partially_effective",
    "comment_opened",
    "comment_deadline",
    "comment_results_published",
    "other",
)

# Source kinds. ``primary``: government, Diet, Official Gazette and e-Gov
# (any *.go.jp host) -- the only kind that can support facts about a statute,
# Cabinet Order or ministerial ordinance being enacted, promulgated or in
# force. ``self_regulatory``: JPX and JSDA, primary for their own rules only.
# ``secondary``: anything else (commentary, news); never enough on its own.
GOVERNMENT_HOST_SUFFIXES = (".go.jp",)
SELF_REGULATORY_HOSTS = ("jpx.co.jp", "jsda.or.jp")
SOURCE_KINDS = ("primary", "self_regulatory", "secondary")
# Key-date kinds that state a legal fact about a state instrument: each must
# cite at least one ``primary`` source.
LEGAL_FACT_DATE_KINDS = frozenset(
    {"bill_submitted", "passed_house_of_representatives", "passed_house_of_councillors", "enacted",
     "promulgated", "effective", "partially_effective"}
)

SECTION_KEYS = ("background", "history", "business_impact")
MAX_PARAGRAPH_CHARS = 1200
MAX_PARAGRAPHS = 8
MAX_LIST_ITEM_CHARS = 600
MAX_LIST_ITEMS = 10
MAX_TITLE_CHARS = 140
STALE_VERIFICATION_DAYS = 180
# A reform still moving through the procedure is re-verified sooner: a status
# this dashboard does not ingest (comment results on a ministry page, a Diet
# vote, a Cabinet Order fixing a date) may have changed without any linked
# record arriving, and absence of a record is never evidence that nothing
# happened.
MOVING_STAGE_VERIFICATION_DAYS = 30
SETTLED_STAGES = frozenset({"in_force", "in_effect", "lapsed", "withdrawn", "not_adopted"})
_MARKUP_RE = re.compile(r"<\s*[A-Za-z/!]|```|\]\(")

QUEUE_NEW_LIMIT = 25
QUEUE_ITEM_IDS_SHOWN = 12  # the full list comes from ``context``; this keeps the queue file small
PRIORITY_AREAS = (
    "Economic Security / FDI",
    "Energy / Environment",
    "Corporate Governance",
    "Labor / Employment",
    "Data / Privacy / AI",
    "Finance / AML",
    "Antitrust / Fair Trade",
)


# ------------------------------------------------------------------ loading


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_explainers(directory: Path = EXPLAINERS_DIR) -> dict[str, dict]:
    """Every ``*.json`` under ``directory`` keyed by its file stem (unparseable files keep a marker)."""
    out: dict[str, dict] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            data = load_json(path)
        except (OSError, ValueError) as exc:
            data = {"__load_error__": f"{type(exc).__name__}"}
        out[path.stem] = data if isinstance(data, dict) else {"__load_error__": "not an object"}
    return out


@dataclass
class OfficialIndex:
    """Raw history indexed for fingerprints and consistency checks."""

    raw_by_id: dict[str, Mapping[str, Any]]
    by_url: dict[str, list[str]]
    timeline: dict[str, lt.Dossier]
    by_bill: dict[tuple[str, str, str], list[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, raw_items: Sequence[Mapping[str, Any]]) -> "OfficialIndex":
        raw_by_id = {r["id"]: r for r in raw_items if isinstance(r, Mapping) and isinstance(r.get("id"), str)}
        by_url: dict[str, list[str]] = defaultdict(list)
        by_bill: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        for item_id, raw in raw_by_id.items():
            if isinstance(raw.get("source_url"), str):
                by_url[raw["source_url"]].append(item_id)
            key = bill_key(raw)
            if key:
                by_bill[key].append(item_id)
        return cls(raw_by_id, dict(by_url), lt.build_dossiers(list(raw_by_id.values())), dict(by_bill))

    def closure(self, item_ids: Iterable[str]) -> list[str]:
        """The given records plus every record of the same reform this dashboard can link exactly.

        Timeline members (the same instrument, the bill it came from, the
        consultation on its draft), lifecycle records sharing an official URL
        (a Diet status change, an e-Gov stage change), and the same bill under
        another House page (a bill carried over into the next session keeps its
        category, session and number but gets a new URL).
        """
        seen: set[str] = set()
        frontier = [i for i in item_ids if i in self.raw_by_id]
        while frontier:
            item_id = frontier.pop()
            if item_id in seen:
                continue
            seen.add(item_id)
            raw = self.raw_by_id[item_id]
            dossier = self.timeline.get(item_id)
            if dossier is not None:
                frontier += [m for m in dossier.member_ids if m not in seen]
            frontier += [m for m in self.by_url.get(raw.get("source_url"), ()) if m not in seen]
            frontier += [m for m in self.by_bill.get(bill_key(raw), ()) if m not in seen]
        return sorted(seen)

    def fingerprint(self, item_ids: Iterable[str]) -> str:
        """Hash of every fact-bearing field of every record in the closure.

        A new related record (promulgation, Diet status change, new e-Gov
        entry with an effective date) and any change to an existing record's
        title, summary metadata, dates, stage hint, URL or deadline both change
        the hash. The raw history is append-only, but hashing the content
        rather than trusting that keeps the check honest.
        """
        rows = [
            [item_id, *(str(self.raw_by_id[item_id].get(key) or "") for key in FINGERPRINT_FIELDS)]
            for item_id in self.closure(item_ids)
        ]
        digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()
        return "sha256:" + digest[:32]

    def page_status(self, item_id: str) -> dict[str, Any] | None:
        """The legal status of the single instrument behind one e-Gov Law record, for its own page.

        A reform explainer is shared by several pages; this keeps each page
        from implying that its own instrument shares the reform's stage.
        """
        raw = self.raw_by_id.get(item_id)
        if raw is None or not str(raw.get("source_name", "")).startswith(lt.EGOV_LAW_PREFIX):
            return None
        fields = lt.egov_law_fields(raw.get("raw_summary"))
        number = fields.get("改正法令番号") or fields.get("法令番号")
        if not number:
            return None
        records = [
            self.raw_by_id[m]
            for m in self.closure([item_id])
            if str(self.raw_by_id[m].get("source_name", "")).startswith(lt.EGOV_LAW_PREFIX)
            and (lt.egov_law_fields(self.raw_by_id[m].get("raw_summary")).get("改正法令番号")
                 or lt.egov_law_fields(self.raw_by_id[m].get("raw_summary")).get("法令番号")) == number
        ]
        target = fields.get("対象法令", "")
        own = [r for r in records if lt.egov_law_fields(r.get("raw_summary")).get("対象法令", "") == target]
        dates = sorted({d for r in own if (d := lt.parse_date(lt.egov_law_fields(r.get("raw_summary")).get("施行日")))})
        return {
            "instrument": fields.get("改正法令") or fields.get("対象法令") or "",
            "number": number,
            "target": target,
            "promulgated": lt.parse_date(fields.get("公布日")),
            "effective_dates": dates,
            "pending": any(not lt.egov_law_fields(r.get("raw_summary")).get("施行日") for r in own),
        }

    # --- facts read from the official records ---------------------------

    def diet_statuses(self, item_ids: Iterable[str]) -> set[str]:
        out = set()
        for item_id in self.closure(item_ids):
            raw = self.raw_by_id[item_id]
            if str(raw.get("source_name", "")).startswith(lt.DIET_PREFIX):
                status = lt.diet_fields(raw.get("raw_summary")).get("審議状況")
                if status:
                    out.add(status)
        return out

    def instruments(self, item_ids: Iterable[str]) -> dict[str, dict[str, set]]:
        """number -> {"promulgated": {dates}, "effective": {dates}, "pending": {bool}} from e-Gov Law records.

        The fetcher stores no 施行日 when e-Gov has not fixed one (a provision
        whose date is still to be set by Cabinet Order), so a record without it
        marks an effective date that is still pending.
        """
        out: dict[str, dict[str, set]] = {}
        for item_id in self.closure(item_ids):
            raw = self.raw_by_id[item_id]
            if not str(raw.get("source_name", "")).startswith(lt.EGOV_LAW_PREFIX):
                continue
            fields = lt.egov_law_fields(raw.get("raw_summary"))
            number = fields.get("改正法令番号") or fields.get("法令番号")
            if not number:
                continue
            facts = out.setdefault(
                number,
                {"promulgated": set(), "effective": set(), "pending": set(), "placeholder": set(), "names": set()},
            )
            name = fields.get("改正法令") or fields.get("対象法令")
            if name:
                facts["names"].add(lt.normalize_title(name))
            if not fields.get("施行日"):
                facts["pending"].add(True)
                # The e-Gov revision id still carries a date: the latest date
                # the Act allows, used as a placeholder until a Cabinet Order
                # fixes the real one. It must never be presented as fixed.
                placeholder = _lawid_date(raw.get("source_url"))
                if placeholder:
                    facts["placeholder"].add(placeholder)
            for key, bucket in (("公布日", "promulgated"), ("施行日", "effective")):
                parsed = lt.parse_date(fields.get(key))
                if parsed:
                    facts[bucket].add(parsed)
        return out

    def comment_deadlines(self, item_ids: Iterable[str]) -> set[date]:
        out = set()
        for item_id in self.closure(item_ids):
            raw = self.raw_by_id[item_id]
            if not str(raw.get("source_name", "")).startswith(lt.EGOV_PUBLIC_COMMENT_PREFIX):
                continue
            for event in lt._comment_events(raw):
                if event.rank == 1 and event.when:
                    out.add(event.when)
        return out


FINGERPRINT_FIELDS = (
    "title_ja", "raw_summary", "published_at", "source_url", "stage_hint", "comment_deadline", "raw_content_hash",
)


def bill_key(raw: Mapping[str, Any]) -> tuple[str, str, str] | None:
    """(category, session, number) of a House of Representatives bill record."""
    if not str(raw.get("source_name", "")).startswith(lt.DIET_PREFIX):
        return None
    fields = lt.diet_fields(raw.get("raw_summary"))
    key = (fields.get("種別", ""), fields.get("提出回次", ""), fields.get("番号", ""))
    return key if all(key) else None


def _lawid_date(url: Any) -> date | None:
    match = re.search(r"lawid=[0-9A-Za-z]+_(\d{8})_", url if isinstance(url, str) else "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def publish_policy(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """``PUBLISH_POLICY`` with any per-category override from ``EXPLAINER_PUBLISH_POLICY``.

    An unknown category or review state in the override raises, so a typo in
    the repository variable stops the run instead of silently publishing.
    """
    import os

    policy = dict(PUBLISH_POLICY)
    raw = (env if env is not None else os.environ).get(POLICY_ENV, "").strip()
    for part in filter(None, (p.strip() for p in raw.split(","))):
        category, sep, state = part.partition("=")
        if not sep or category.strip() not in CATEGORIES or state.strip() not in REVIEW_STATES[1:]:
            raise ValueError(f"{POLICY_ENV}: invalid entry {part!r}")
        policy[category.strip()] = state.strip()
    return policy


# ------------------------------------------------------------------ validation


@dataclass
class Report:
    dossier_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # never block; drive the queue and page notices
    notices: list[str] = field(default_factory=list)  # reader-facing English sentences
    current_fingerprint: str = ""
    review: dict[str, str] = field(default_factory=dict)  # effective review state per category

    @property
    def ok(self) -> bool:
        return not self.errors

    def dedupe(self) -> "Report":
        self.errors[:] = list(dict.fromkeys(self.errors))
        self.warnings[:] = list(dict.fromkeys(self.warnings))
        self.notices[:] = list(dict.fromkeys(self.notices))
        return self


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _is_government(url: str) -> bool:
    host = _host(url)
    return any(host.endswith(suffix) for suffix in GOVERNMENT_HOST_SUFFIXES)


def _is_self_regulatory(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in SELF_REGULATORY_HOSTS)


def _check_text(value: Any, where: str, limit: int, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: must be a non-empty string")
        return ""
    if len(value) > limit:
        errors.append(f"{where}: longer than {limit} characters")
    if _MARKUP_RE.search(value):
        errors.append(f"{where}: plain text only (no HTML, Markdown links or code fences)")
    return value


def _check_list(value: Any, where: str, errors: list[str], *, required: bool, limit: int, items: int) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        errors.append(f"{where}: must be a {'non-empty ' if required else ''}list of strings")
        return []
    if len(value) > items:
        errors.append(f"{where}: more than {items} entries")
    return [_check_text(v, f"{where}[{i}]", limit, errors) for i, v in enumerate(value)]


def _check_source_ids(value: Any, where: str, known: set[str], errors: list[str], *, required: bool) -> None:
    if value is None and not required:
        return
    if not isinstance(value, list) or (required and not value):
        errors.append(f"{where}: must list the supporting official source ids")
        return
    for source_id in value:
        if source_id not in known:
            errors.append(f"{where}: unknown source id {source_id!r}")


def _review_rank(state: Any) -> int:
    return REVIEW_STATES.index(state) if state in REVIEW_STATES else -1


def validate(
    dossier_id: str,
    data: Mapping[str, Any],
    index: OfficialIndex,
    today: date,
    *,
    claimed: Mapping[str, str] | None = None,
) -> Report:
    """Check one explainer. ``claimed`` maps item ids to the published explainer already covering them."""
    report = Report(dossier_id)
    errors, warnings = report.errors, report.warnings
    if "__load_error__" in data:
        errors.append(f"file could not be read as a JSON object ({data['__load_error__']})")
        return report

    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if data.get("dossier_id") != dossier_id or not DOSSIER_ID_PATTERN.match(dossier_id):
        errors.append("dossier_id must equal the file name and use lowercase letters, digits and hyphens")
    status = data.get("status")
    if status not in STATUSES:
        errors.append(f"status must be one of {', '.join(STATUSES)}")
    if data.get("review_state") not in REVIEW_STATES:
        errors.append(f"review_state must be one of {', '.join(REVIEW_STATES)}")
    _check_review(data, report)

    related = data.get("related_item_ids")
    if not isinstance(related, list) or not related or not all(isinstance(i, str) for i in related):
        errors.append("related_item_ids must be a non-empty list of raw item ids")
        related = []
    unknown = [i for i in related if i not in index.raw_by_id]
    if unknown:
        errors.append(f"related_item_ids not found in data/raw_items.json: {', '.join(sorted(unknown))}")
    if len(set(related)) != len(related):
        errors.append("related_item_ids contains duplicates")
    report.current_fingerprint = index.fingerprint(related)

    verified = strict_iso_date(data.get("verified_at"))
    if verified is None:
        errors.append("verified_at must be a YYYY-MM-DD date")
    elif verified > today:
        errors.append("verified_at is in the future")

    if data.get("source_fingerprint") != report.current_fingerprint:
        warnings.append("official_records_changed")
        report.notices.append(
            "New or changed official records related to this reform have been detected since this explainer "
            "was last verified. Check the timeline below and the original sources."
        )
    if verified:
        stage_now = data.get("current_stage")
        limit = STALE_VERIFICATION_DAYS if stage_now in SETTLED_STAGES else MOVING_STAGE_VERIFICATION_DAYS
        if (today - verified).days > limit:
            warnings.append("verification_old")
    _effective_review(data, report, verified)

    for item_id in index.closure(related):
        owner = (claimed or {}).get(item_id)
        if owner and owner != dossier_id:
            errors.append(f"{item_id} is already covered by explainer {owner} (one reform, one explainer)")

    if status in DECISION_STATUSES or status == "withdrawn":
        _check_text(data.get("review_notes"), "review_notes", 2000, errors)
        return report

    _check_text(data.get("title_en"), "title_en", MAX_TITLE_CHARS, errors)
    instrument_type = data.get("instrument_type")
    if instrument_type not in INSTRUMENT_TYPES:
        errors.append(f"instrument_type must be one of {', '.join(INSTRUMENT_TYPES)}")
        instrument_type = "other"
    stage = data.get("current_stage")
    if stage not in STAGES_BY_TYPE[instrument_type]:
        errors.append(f"current_stage {stage!r} is not a valid stage for {instrument_type}")

    instruments = data.get("instruments")
    if not isinstance(instruments, list) or not instruments:
        errors.append("instruments must list each instrument's number and formal Japanese name")
        instruments = []
    for i, entry in enumerate(instruments):
        where = f"instruments[{i}]"
        if not isinstance(entry, Mapping):
            errors.append(f"{where}: must be an object")
            continue
        number = entry.get("number")
        if not isinstance(number, str) or not INSTRUMENT_NUMBER_PATTERN.match(number):
            errors.append(f"{where}.number must be a Japanese instrument number such as 令和八年法律第三十号")
        _check_text(entry.get("name_ja"), f"{where}.name_ja", 300, errors)
        _check_text(entry.get("name_en"), f"{where}.name_en", 300, errors)

    sources = data.get("official_sources")
    known: set[str] = set()
    if not isinstance(sources, list) or not sources:
        errors.append("official_sources must be a non-empty list")
        sources = []
    primary = 0
    kinds: dict[str, str] = {}
    for i, source in enumerate(sources):
        where = f"official_sources[{i}]"
        if not isinstance(source, Mapping):
            errors.append(f"{where}: must be an object")
            continue
        source_id = source.get("id")
        if not isinstance(source_id, str) or not SOURCE_ID_PATTERN.match(source_id) or source_id in known:
            errors.append(f"{where}: id must be a unique value like s1")
        else:
            known.add(source_id)
        _check_text(source.get("title"), f"{where}.title", 300, errors)
        _check_text(source.get("publisher"), f"{where}.publisher", 120, errors)
        url = safe_http_url(source.get("url"))
        if not url:
            errors.append(f"{where}.url must be an absolute http(s) URL")
        kind = source.get("kind")
        if kind not in SOURCE_KINDS:
            errors.append(f"{where}.kind must be one of {', '.join(SOURCE_KINDS)}")
        elif kind == "primary":
            if url and not _is_government(url):
                errors.append(
                    f"{where}: a primary source must be a government, Diet, Official Gazette or e-Gov page "
                    "(*.go.jp); cite JPX or JSDA as self_regulatory"
                )
            else:
                primary += 1
        elif kind == "self_regulatory" and url and not _is_self_regulatory(url):
            errors.append(f"{where}: self_regulatory sources are JPX or JSDA pages")
        if isinstance(source_id, str) and isinstance(kind, str):
            kinds[source_id] = kind
        accessed = strict_iso_date(source.get("accessed_at"))
        if accessed is None:
            errors.append(f"{where}.accessed_at must be a YYYY-MM-DD date")
        elif verified and accessed > verified:
            errors.append(f"{where}.accessed_at is after verified_at")
    if sources and not primary and instrument_type != "guideline":
        errors.append("at least one primary government source (*.go.jp) is required")
    if sources and instrument_type == "guideline" and not any(k in ("primary", "self_regulatory") for k in kinds.values()):
        errors.append("at least one primary or self_regulatory source is required")

    for key in SECTION_KEYS:
        section = data.get(key)
        if not isinstance(section, Mapping):
            errors.append(f"{key}: must be an object with paragraphs and source_ids")
            continue
        _check_list(section.get("paragraphs"), f"{key}.paragraphs", errors, required=True,
                    limit=MAX_PARAGRAPH_CHARS, items=MAX_PARAGRAPHS)
        _check_source_ids(section.get("source_ids"), f"{key}.source_ids", known, errors, required=True)
    business = data.get("business_impact")
    if isinstance(business, Mapping):
        _check_list(business.get("checklist"), "business_impact.checklist", errors, required=False,
                    limit=MAX_LIST_ITEM_CHARS, items=MAX_LIST_ITEMS)

    next_steps = data.get("next_steps")
    if not isinstance(next_steps, Mapping):
        errors.append("next_steps must be an object with confirmed, expected and uncertainties")
        next_steps = {}
    confirmed = next_steps.get("confirmed")
    if not isinstance(confirmed, list):
        errors.append("next_steps.confirmed must be a list (it may be empty)")
        confirmed = []
    for i, step in enumerate(confirmed):
        where = f"next_steps.confirmed[{i}]"
        if not isinstance(step, Mapping):
            errors.append(f"{where}: must be an object")
            continue
        _check_text(step.get("text"), f"{where}.text", MAX_LIST_ITEM_CHARS, errors)
        _check_source_ids(step.get("source_ids"), f"{where}.source_ids", known, errors, required=True)
        if step.get("date") is not None and strict_iso_date(step.get("date")) is None:
            errors.append(f"{where}.date must be YYYY-MM-DD when present")
    _check_list(next_steps.get("expected"), "next_steps.expected", errors, required=False,
                limit=MAX_LIST_ITEM_CHARS, items=MAX_LIST_ITEMS)
    _check_list(next_steps.get("uncertainties"), "next_steps.uncertainties", errors, required=True,
                limit=MAX_LIST_ITEM_CHARS, items=MAX_LIST_ITEMS)

    key_dates = data.get("key_dates")
    if not isinstance(key_dates, list):
        errors.append("key_dates must be a list")
        key_dates = []
    parsed_dates: list[tuple[str, date, str]] = []
    for i, entry in enumerate(key_dates):
        where = f"key_dates[{i}]"
        if not isinstance(entry, Mapping):
            errors.append(f"{where}: must be an object")
            continue
        kind = entry.get("kind")
        if kind not in KEY_DATE_KINDS:
            errors.append(f"{where}.kind must be one of {', '.join(KEY_DATE_KINDS)}")
        when = strict_iso_date(entry.get("date"))
        if when is None:
            errors.append(f"{where}.date must be YYYY-MM-DD")
            continue
        _check_text(entry.get("label"), f"{where}.label", 200, errors)
        _check_source_ids(entry.get("source_ids"), f"{where}.source_ids", known, errors, required=True)
        number = entry.get("instrument_number") if isinstance(entry.get("instrument_number"), str) else ""
        if kind in LEGAL_FACT_DATE_KINDS and not any(
            kinds.get(i) == "primary" for i in entry.get("source_ids") or [] if isinstance(i, str)
        ):
            errors.append(f"{where}: a {kind} date must cite a primary government source")
        if kind in ("bill_submitted", "passed_house_of_representatives", "passed_house_of_councillors",
                    "enacted", "promulgated", "comment_opened", "comment_deadline",
                    "comment_results_published") and verified and when > verified:
            errors.append(f"{where}: a past-event date ({kind}) cannot be after verified_at")
        parsed_dates.append((str(kind), when, number))

    _consistency(report, stage, related, parsed_dates, index, today)
    recorded = index.instruments(related)
    for entry in instruments:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("number"), str):
            continue
        facts = recorded.get(entry["number"])
        if facts is None:
            warnings.append(f"instrument_not_in_records:{entry['number']}")
        elif facts["names"] and lt.normalize_title(entry.get("name_ja")) not in facts["names"]:
            errors.append(f"instruments: name of {entry['number']} differs from e-Gov Law Search")
    return report.dedupe()


def _consistency(
    report: Report,
    stage: Any,
    related: Sequence[str],
    key_dates: Sequence[tuple[str, date, str]],
    index: OfficialIndex,
    today: date,
) -> None:
    """Contradictions with the official records are errors; dates overtaken since verification are warnings."""
    errors, warnings = report.errors, report.warnings
    instruments = index.instruments(related)
    statuses = index.diet_statuses(related)
    deadlines = index.comment_deadlines(related)
    statute_promulgated = any(lt.is_statute(n) and f["promulgated"] for n, f in instruments.items())
    any_promulgated = any(f["promulgated"] for f in instruments.values())

    for kind, when, number in key_dates:
        facts = instruments.get(number)
        if kind == "promulgated" and facts and facts["promulgated"] and when not in facts["promulgated"]:
            shown = ", ".join(sorted(d.isoformat() for d in facts["promulgated"]))
            errors.append(f"promulgation date {when} for {number} differs from e-Gov Law Search ({shown})")
        if kind == "comment_deadline" and deadlines and when not in deadlines:
            shown = ", ".join(sorted(d.isoformat() for d in deadlines))
            errors.append(f"comment deadline {when} differs from the e-Gov public comment record ({shown})")
        if number and not facts and kind in ("promulgated", "effective", "partially_effective"):
            warnings.append(f"instrument_not_in_records:{number}")
        if (
            kind in ("effective", "partially_effective")
            and facts
            and when in facts["placeholder"]
            and when not in facts["effective"]
        ):
            errors.append(
                f"{when} for {number} is e-Gov's placeholder (the latest date the Act allows, pending a Cabinet "
                "Order), not a fixed effective date; describe it as an outer limit under uncertainties"
            )

    if stage in PRE_ENACTMENT and ("成立" in statuses or statute_promulgated):
        errors.append(f"stage {stage} contradicts the official records (the bill is enacted or promulgated)")
    if stage in ENDED_BILL and ("成立" in statuses or statute_promulgated):
        errors.append(f"stage {stage} contradicts the official records (the bill is enacted or promulgated)")
    if stage in PRE_PROMULGATION and any_promulgated:
        errors.append(f"stage {stage} contradicts e-Gov Law Search, which records a promulgation")
    if stage == "enacted" and any_promulgated:
        warnings.append("stage_overtaken:promulgated")
        report.notices.append("e-Gov Law Search now records the promulgation of this reform.")

    future_effective = sorted(
        {when for kind, when, _ in key_dates if kind in ("effective", "partially_effective") and when > today}
        | {d for facts in instruments.values() for d in facts["effective"] if d > today}
    )
    pending = any(facts["pending"] for facts in instruments.values())
    if stage == "in_force" and future_effective:
        errors.append(f"stage in_force but an effective date is still ahead ({future_effective[0]})")
    elif stage == "in_force" and pending:
        errors.append("stage in_force but e-Gov Law Search lists a provision whose effective date is not yet fixed")
    stated_effective = sorted(when for kind, when, _ in key_dates if kind in ("effective", "partially_effective"))
    passed = [d for d in stated_effective if d <= today]
    overtaken = (stage == "promulgated" and passed) or (
        stage in ("partially_in_force", "published") and passed and not future_effective and not pending
    )
    if overtaken:
        warnings.append("stage_overtaken:effective_date_passed")
        report.notices.append(
            f"An effective date listed below ({passed[-1].isoformat()}) has passed since this explainer "
            "was last verified; the current stage may have advanced."
        )
    if stage == "public_comment_open":
        closing = sorted(d for k, d, _ in key_dates if k == "comment_deadline") or sorted(deadlines)
        if closing and closing[-1] < today:
            warnings.append("stage_overtaken:comment_closed")
            report.notices.append(
                f"The public comment period closed on {closing[-1].isoformat()}, after this explainer was verified."
            )


def _check_review(data: Mapping[str, Any], report: Report) -> None:
    section_review = data.get("section_review", {})
    if not isinstance(section_review, Mapping):
        report.errors.append("section_review must be an object keyed by category")
        return
    for category, state in section_review.items():
        if category not in CATEGORIES or state not in REVIEW_STATES:
            report.errors.append(f"section_review.{category}: unknown category or review state {state!r}")
    states = [data.get("review_state"), *section_review.values()]
    if "human_reviewed" in states:
        review = data.get("human_review")
        if not isinstance(review, Mapping):
            report.errors.append("human_reviewed requires a human_review object with reviewer and reviewed_at")
            return
        _check_text(review.get("reviewer"), "human_review.reviewer", 80, report.errors)
        if strict_iso_date(review.get("reviewed_at")) is None:
            report.errors.append("human_review.reviewed_at must be a YYYY-MM-DD date")


def _effective_review(data: Mapping[str, Any], report: Report, verified: date | None) -> None:
    """Effective review state per category.

    A human review counts only while it is at least as recent as the last
    verification: rewriting or re-verifying the explainer moves
    ``verified_at`` forward and sends it back for review.
    """
    base = data.get("review_state") if data.get("review_state") in REVIEW_STATES else "unreviewed"
    section_review = data.get("section_review") if isinstance(data.get("section_review"), Mapping) else {}
    review = data.get("human_review") if isinstance(data.get("human_review"), Mapping) else {}
    reviewed_at = strict_iso_date(review.get("reviewed_at"))
    current = bool(reviewed_at and verified and reviewed_at >= verified)
    for category in CATEGORIES:
        state = section_review.get(category, base)
        state = state if state in REVIEW_STATES else "unreviewed"
        if state == "human_reviewed" and not current:
            state = "ai_checked"
            if "human_review_outdated" not in report.warnings:
                report.warnings.append("human_review_outdated")
        report.review[category] = state


def visible_categories(report: Report, policy: Mapping[str, str] | None = None) -> set[str]:
    policy = policy or publish_policy()
    return {
        category
        for category in CATEGORIES
        if _review_rank(report.review.get(category)) >= _review_rank(policy.get(category, "human_reviewed"))
    }


def publishable(data: Mapping[str, Any], report: Report, policy: Mapping[str, str] | None = None) -> bool:
    """Shown at all only when valid, published, and its facts meet the policy."""
    return report.ok and data.get("status") == "published" and "facts" in visible_categories(report, policy)


def awaiting_review(data: Mapping[str, Any], report: Report, policy: Mapping[str, str] | None = None) -> list[str]:
    """Categories of a valid published explainer held back by the review policy."""
    if not report.ok or data.get("status") != "published":
        return []
    return [c for c in CATEGORIES if c not in visible_categories(report, policy)]


@dataclass
class Published:
    dossier_id: str
    data: Mapping[str, Any]
    report: Report
    visible: frozenset[str] = frozenset(CATEGORIES)


def validate_all(
    explainers: Mapping[str, Mapping[str, Any]], index: OfficialIndex, today: date
) -> dict[str, Report]:
    """Validate every explainer; an item may be covered by one non-decision explainer only."""
    claimed: dict[str, str] = {}
    for dossier_id in sorted(explainers):
        data = explainers[dossier_id]
        if data.get("status") in ("draft", "published") and isinstance(data.get("related_item_ids"), list):
            for item_id in index.closure(i for i in data["related_item_ids"] if isinstance(i, str)):
                claimed.setdefault(item_id, dossier_id)
    return {
        dossier_id: validate(dossier_id, explainers[dossier_id], index, today, claimed=claimed)
        for dossier_id in sorted(explainers)
    }


def published_by_item(
    explainers: Mapping[str, Mapping[str, Any]],
    index: OfficialIndex,
    today: date,
    policy: Mapping[str, str] | None = None,
) -> tuple[dict[str, Published], dict[str, Report]]:
    """Item id -> publishable explainer, plus every validation report.

    An explainer is mapped to its records' closure, so a record the timeline
    later links to the same instrument shows it too -- together with the
    "official records changed" notice until the explainer is re-verified.
    """
    policy = policy or publish_policy()
    reports = validate_all(explainers, index, today)
    by_item: dict[str, Published] = {}
    for dossier_id, report in reports.items():
        data = explainers[dossier_id]
        if publishable(data, report, policy):
            entry = Published(dossier_id, data, report, frozenset(visible_categories(report, policy)))
            # Shown on every record the fingerprint covers: the listed records
            # plus those the conservative timeline links to the same instrument.
            for item_id in index.closure(data["related_item_ids"]):
                by_item[item_id] = entry
    return by_item, reports


# ------------------------------------------------------------------ queue


def _published_items(path: Path) -> dict[str, Mapping[str, Any]]:
    if not path.exists():
        return {}
    data = load_json(path)
    return {i["id"]: i for i in data if isinstance(i, Mapping) and isinstance(i.get("id"), str)}


def build_queue(
    index: OfficialIndex,
    published: Mapping[str, Mapping[str, Any]],
    explainers: Mapping[str, Mapping[str, Any]],
    today: date,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic list of reforms to explain or re-verify. No API calls.

    Update entries come from explainers whose official records changed, whose
    stated stage was overtaken by a listed date, whose verification is old, or
    which now contradict the records. New entries are timeline dossiers with
    published members in a monitored area, not yet covered by any explainer or
    recorded decision. A queue id keeps its ``first_queued_on`` across runs.
    """
    policy = publish_policy()
    reports = validate_all(explainers, index, today)
    previous_first = {
        e.get("queue_id"): e.get("first_queued_on")
        for e in (previous or {}).get("entries", [])
        if isinstance(e, Mapping)
    }
    entries: list[dict[str, Any]] = []

    covered: set[str] = set()
    for dossier_id, data in sorted(explainers.items()):
        related = [i for i in data.get("related_item_ids") or [] if isinstance(i, str)]
        report = reports[dossier_id]
        closure = index.closure(related)
        if data.get("status") != "withdrawn":
            covered.update(closure)
        reasons = []
        if report.errors and data.get("status") in ("published", "draft"):
            reasons.append("validation_errors")
        reasons += [w for w in report.warnings if w == "official_records_changed" or w.startswith("stage_overtaken")]
        if "verification_old" in report.warnings and data.get("status") == "published":
            reasons.append("verification_old")
        held = awaiting_review(data, report, policy)
        if held:
            reasons.append("awaiting_review:" + "+".join(held))
        if data.get("status") in DECISION_STATUSES and "official_records_changed" not in reasons:
            continue
        if not reasons or data.get("status") == "withdrawn":
            continue
        priority = 0
        priority += 60 if "validation_errors" in reasons else 0
        priority += 40 if "official_records_changed" in reasons else 0
        priority += 30 if any(r.startswith("stage_overtaken") for r in reasons) else 0
        priority += 5 if "verification_old" in reasons else 0
        priority += 10 if held else 0
        new_records = sorted(set(closure) - set(related))
        entries.append(
            {
                "queue_id": f"explainer:{dossier_id}",
                "kind": "update",
                "explainer_id": dossier_id,
                "explainer_status": data.get("status"),
                "priority": priority,
                "reasons": sorted(set(reasons)),
                "errors": report.errors[:10],
                "review": dict(report.review),
                "record_count": len(closure),
                "item_ids": closure[:QUEUE_ITEM_IDS_SHOWN],
                "new_related_item_ids": new_records[:QUEUE_ITEM_IDS_SHOWN],
                "current_fingerprint": report.current_fingerprint,
            }
        )

    candidates = []
    seen_dossiers: set[int] = set()
    for item_id, dossier in index.timeline.items():
        if id(dossier) in seen_dossiers:
            continue
        seen_dossiers.add(id(dossier))
        members = [m for m in dossier.member_ids if m in published]
        if not members or set(dossier.member_ids) & covered:
            continue
        areas = sorted({str(published[m].get("area", "")) for m in members})
        if not set(areas) & set(PRIORITY_AREAS):
            continue
        kinds = set()
        for m in dossier.member_ids:
            source = str(index.raw_by_id[m].get("source_name", ""))
            kinds.add(
                "bill" if source.startswith(lt.DIET_PREFIX)
                else "comment" if source.startswith(lt.EGOV_PUBLIC_COMMENT_PREFIX)
                else "instrument"
            )
        statute = dossier.key.startswith("bill:") or lt.is_statute(dossier.key.partition(":")[2])
        reasons = []
        if "bill" in kinds:
            reasons.append("diet_bill")
        if "comment" in kinds:
            reasons.append("public_comment_on_draft")
        if "instrument" in kinds:
            reasons.append("statute_promulgated" if statute else "instrument_promulgated")
        if dossier.cross_source:
            reasons.append("linked_across_sources")
        newest = max((str(index.raw_by_id[m].get("first_seen_at") or "") for m in dossier.member_ids), default="")
        promulgated = [e.when for e in dossier.events if e.rank == 2 and e.when]
        newest_date = lt.parse_date(newest)
        # An instrument promulgated long before its newest record is an older
        # reform reaching a later effective date: worth a note, not first in line.
        older = bool(promulgated and newest_date and (newest_date - min(promulgated)).days > 365)
        if older:
            reasons.append("older_instrument_taking_effect")
        priority = (
            20 * statute
            + 10 * dossier.cross_source
            + 10 * ("comment" in kinds and dossier.next_step != "")
            + 5 * any(published[m].get("impact_level") == "Medium" for m in members)
            - 20 * older
        )
        first_member = min(members)
        candidates.append(
            (
                priority,
                newest,
                dossier.key,
                {
                    "queue_id": dossier.key,
                    "kind": "new",
                    "explainer_id": None,
                    "priority": priority,
                    "reasons": reasons,
                    "record_count": len(index.closure(dossier.member_ids)),
                    "item_ids": index.closure(dossier.member_ids)[:QUEUE_ITEM_IDS_SHOWN],
                    "areas": areas,
                    "title_ja": lt._text(index.raw_by_id[first_member].get("title_ja")),
                    "stage": published[first_member].get("stage"),
                    "newest_record_first_seen": newest or None,
                    "suggested_seed_item_id": first_member,
                },
            )
        )
    candidates.sort(key=lambda c: c[2])  # stable tie-break, then newest first, then priority
    candidates.sort(key=lambda c: c[1], reverse=True)
    candidates.sort(key=lambda c: c[0], reverse=True)
    entries += [c[3] for c in candidates[:QUEUE_NEW_LIMIT]]

    for entry in entries:
        entry["first_queued_on"] = previous_first.get(entry["queue_id"]) or today.isoformat()
    entries.sort(key=lambda e: (-e["priority"], e["kind"] != "update", e["queue_id"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "note": (
            "Generated by scripts/explainers.py queue --build. Reforms to explain or re-verify; "
            "nothing here is published. See operations/EXPLAINER_OPERATIONS.md."
        ),
        "entries": entries,
    }


# ------------------------------------------------------------------ context / scaffold


def _law_ids(url: Any) -> list[str]:
    """e-Gov law ids in an elaws URL (target law and amending instrument), for the Law API."""
    match = re.search(r"lawid=([0-9A-Za-z_]+)", url if isinstance(url, str) else "")
    if not match:
        return []
    parts = match.group(1).split("_")
    return [p for p in (parts[0], parts[-1] if len(parts) == 3 else "") if p]


def context(index: OfficialIndex, item_or_dossier: str, explainers: Mapping[str, Mapping[str, Any]]) -> dict:
    """Everything the official records say about one reform, for the writer."""
    if item_or_dossier in explainers:
        existing = explainers[item_or_dossier]
        seed = [i for i in existing.get("related_item_ids") or [] if isinstance(i, str)]
    else:
        existing = None
        seed = [item_or_dossier]
        for dossier_id, data in explainers.items():
            if item_or_dossier in (data.get("related_item_ids") or []):
                existing = data
    closure = index.closure(seed)
    if not closure:
        raise SystemExit(f"unknown item or explainer: {item_or_dossier}")
    records = []
    api = set()
    for item_id in closure:
        raw = index.raw_by_id[item_id]
        records.append(
            {
                "id": item_id,
                "source_name": raw.get("source_name"),
                "title_ja": raw.get("title_ja"),
                "published_at": raw.get("published_at"),
                "first_seen_at": raw.get("first_seen_at"),
                "stage_hint": raw.get("stage_hint"),
                "source_url": raw.get("source_url"),
                "raw_summary": raw.get("raw_summary"),
            }
        )
        for law_id in _law_ids(raw.get("source_url")):
            api.add(f"https://laws.e-gov.go.jp/api/2/law_revisions/{law_id}")
    timelines = []
    seen: set[int] = set()
    for item_id in closure:
        dossier = index.timeline.get(item_id)
        if dossier is None or id(dossier) in seen:
            continue
        seen.add(id(dossier))
        timelines.append(
            {
                "key": dossier.key,
                "events": [
                    {
                        "date": e.when.isoformat() if e.when else None,
                        "label": e.label,
                        "detail_ja": e.detail_ja,
                        "item_ids": list(e.item_ids),
                        "source_url": e.source_url,
                    }
                    for e in dossier.events
                ],
                "general_next_step": dossier.next_step or None,
            }
        )
    return {
        "item_ids": closure,
        "current_fingerprint": index.fingerprint(seed),
        "existing_explainer": existing,
        "records": records,
        "timelines": timelines,
        "e_gov_law_api": sorted(api),
        "reminder": (
            "These are the records this dashboard holds, not the whole history. Confirm every fact in the "
            "official sources before writing; absence here does not mean a step did not happen."
        ),
    }


def scaffold(index: OfficialIndex, item_id: str, dossier_id: str, today: date) -> dict:
    if not DOSSIER_ID_PATTERN.match(dossier_id):
        raise SystemExit("dossier id: lowercase letters, digits and hyphens, 3-80 characters")
    related = index.closure([item_id])
    if not related:
        raise SystemExit(f"unknown item id: {item_id}")
    return {
        "schema_version": SCHEMA_VERSION,
        "dossier_id": dossier_id,
        "status": "draft",
        "review_state": "unreviewed",
        "title_en": "",
        "instrument_type": "statute",
        "current_stage": "",
        "related_item_ids": related,
        "background": {"paragraphs": [], "source_ids": []},
        "history": {"paragraphs": [], "source_ids": []},
        "next_steps": {"confirmed": [], "expected": [], "uncertainties": []},
        "business_impact": {"paragraphs": [], "checklist": [], "source_ids": []},
        "key_dates": [],
        "official_sources": [],
        "verified_at": today.isoformat(),
        "source_fingerprint": index.fingerprint(related),
        "review_notes": "",
    }


def dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, text)
    return True


# ------------------------------------------------------------------ CLI


def _today(value: str | None) -> date:
    return date.fromisoformat(value) if value else datetime.now(JST).date()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, default=RAW_PATH)
    parser.add_argument("--published", type=Path, default=PUBLISHED_PATH)
    parser.add_argument("--dir", type=Path, default=EXPLAINERS_DIR)
    parser.add_argument("--today", default=None, help="JST date (YYYY-MM-DD)")
    sub = parser.add_subparsers(dest="command", required=True)
    q = sub.add_parser("queue", help="print (and with --build, write) the explainer queue")
    q.add_argument("--build", action="store_true")
    q.add_argument("--queue", type=Path, default=QUEUE_PATH)
    q.add_argument("--top", type=int, default=10)
    c = sub.add_parser("context", help="official records for an item or explainer")
    c.add_argument("target")
    s = sub.add_parser("scaffold", help="write a draft explainer for an item")
    s.add_argument("item_id")
    s.add_argument("--id", required=True)
    v = sub.add_parser("validate", help="validate explainers (all when no path is given)")
    v.add_argument("paths", nargs="*", type=Path)
    st = sub.add_parser("stamp", help="record re-verification: set verified_at and the current fingerprint")
    st.add_argument("dossier_id")
    st.add_argument("--verified-on", required=True)
    p = sub.add_parser("preview", help="render one item page with its explainer to a local file")
    p.add_argument("dossier_id")
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    today = _today(args.today)
    index = OfficialIndex.build(load_json(args.raw))
    explainers = load_explainers(args.dir)

    if args.command == "queue":
        previous = load_json(args.queue) if args.queue.exists() else None
        queue = build_queue(index, _published_items(args.published), explainers, today, previous)
        if args.build:
            changed = write_if_changed(args.queue, dump(queue))
            print(f"queue entries={len(queue['entries'])} changed={str(changed).lower()}")
        print(dump(queue["entries"][: args.top]), end="")
        return 0
    if args.command == "context":
        print(dump(context(index, args.target, explainers)), end="")
        return 0
    if args.command == "scaffold":
        path = args.dir / f"{args.id}.json"
        if path.exists():
            raise SystemExit(f"{path} already exists; edit it instead")
        write_if_changed(path, dump(scaffold(index, args.item_id, args.id, today)))
        print(path)
        return 0
    if args.command == "validate":
        reports = validate_all(explainers, index, today)
        policy = publish_policy()
        print("policy: " + ", ".join(f"{c}>={policy[c]}" for c in CATEGORIES))
        wanted = {p.stem for p in args.paths} if args.paths else set(reports)
        failed = 0
        for dossier_id in sorted(wanted):
            if dossier_id not in reports:
                print(f"{dossier_id}: not found under {args.dir}")
                failed += 1
                continue
            report = reports[dossier_id]
            data = explainers[dossier_id]
            if publishable(data, report, policy):
                held = awaiting_review(data, report, policy)
                state = "PUBLISHED" + (f" without {', '.join(held)} (awaiting review)" if held else "")
            elif report.ok and data.get("status") == "published":
                state = "WITHHELD: facts awaiting review"
            else:
                state = "not published"
            review = ", ".join(f"{c}={report.review.get(c)}" for c in CATEGORIES)
            print(f"{dossier_id}: {'OK' if report.ok else 'ERROR'} ({state}; status={data.get('status')}; {review})")
            for message in report.errors:
                print(f"  error: {message}")
            for message in report.warnings:
                print(f"  warning: {message}")
            failed += not report.ok
        return 1 if failed else 0
    if args.command == "stamp":
        path = args.dir / f"{args.dossier_id}.json"
        data = load_json(path)
        verified = date.fromisoformat(args.verified_on)
        if verified > today:
            raise SystemExit("--verified-on cannot be in the future")
        data["verified_at"] = verified.isoformat()
        data["source_fingerprint"] = index.fingerprint(data.get("related_item_ids") or [])
        write_if_changed(path, dump(data))
        print(f"{path}: verified_at={data['verified_at']} fingerprint={data['source_fingerprint']}")
        return 0
    if args.command == "preview":
        import build_public_pages as bpp

        reports = validate_all(explainers, index, today)
        data = explainers.get(args.dossier_id)
        if data is None:
            raise SystemExit(f"unknown explainer: {args.dossier_id}")
        published = _published_items(args.published)
        item_id = next((i for i in data.get("related_item_ids", []) if i in published), None)
        if item_id is None:
            raise SystemExit("none of the related items is in the published file")
        entry = Published(args.dossier_id, data, reports[args.dossier_id])
        html = bpp.render_item_page(published[item_id], index.timeline.get(item_id), set(published), entry)
        write_if_changed(args.out, html)
        print(f"{args.out} ({'publishable' if publishable(data, entry.report) else 'NOT publishable under the current policy; preview shows every section'})")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
