#!/usr/bin/env python3
"""Per-dossier case file: official-source ledger, structured legal facts, holds.

``data/explainer_state/<dossier_id>.json`` travels with the dossier's branch and
pull request, so the next scheduled writing slot resumes from what the previous
one actually established instead of re-reading everything. It records

* ``sources``: every official URL that was opened, when, with which content hash,
  HTTP outcome and error class (so a permanently blocked source is not retried
  every slot, and a transient one is retried a bounded number of times);
* ``facts``: the legal facts an explainer depends on, each tied to source ids and
  a status (``confirmed`` / ``not_applicable`` / ``unconfirmed`` / ``inference``),
  so verified facts and policy analysis never mix;
* ``hold``: BLOCKED / RETRY_WAIT / REVIEW_REQUIRED with a reason and the condition
  that releases it, so a blocked candidate is not re-researched until it can move;
* ``next_action``: the concrete next operation for whoever resumes.

Nothing here verifies law by itself. The gate is deliberately one-way: a missing
or unconfirmed critical fact can stop publication, but no field in this file can
make an explainer publishable that ``explainers.py validate`` would withhold.
Offline only; no network, no API.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import explainers as ex  # noqa: E402
from alert_common import JST, strict_iso_date  # noqa: E402

STATE_DIR = ex.REPO_ROOT / "data" / "explainer_state"
RESEARCH_DIR = ex.REPO_ROOT / "data" / "explainer_research"
SCHEMA_VERSION = 1

# Forward pipeline stages, in order. A dossier's stage is always COMPUTED from
# files and GitHub facts by explainer_pipeline.py; a writer cannot declare one.
STAGES = (
    "RESEARCH_REQUIRED",
    "SOURCES_VERIFIED",
    "EN_READY",
    "JA_ZH_READY",
    "STAMPED_AND_SYNCED",
    "CI_RUNNING",
    "CI_PASSED",
    "MERGED",
    "PAGES_DEPLOYED",
    "LIVE_VERIFIED",
)
STAGE_RANK = {s: i for i, s in enumerate(STAGES)}
# Auxiliary states a writer (or the reconciler) may put a dossier in.
HOLD_STATES = ("BLOCKED", "RETRY_WAIT", "REVIEW_REQUIRED")
# What releases a hold. ``official_records_change`` = the closure fingerprint
# differs from the one recorded when the hold was set (a new Diet status, a
# promulgation, a results notice ...). ``date`` = ``retry_after`` has passed.
UNBLOCK_CONDITIONS = ("official_records_change", "date", "manual")

SOURCE_KINDS = (
    "gazette",              # 官報
    "egov_law",             # e-Gov 法令検索 / 法令 API
    "egov_public_comment",  # e-Gov パブリック・コメント
    "diet",                 # 衆議院・参議院
    "ministry",             # 所管省庁・委員会
    "self_regulatory",      # JPX / JSDA (their own rules only)
    "other_official",
)
SOURCE_STATUSES = ("fetched", "failed", "not_fetched")
ERROR_CLASSES = (
    "transient_network",  # timeout, connection reset, DNS: retry (bounded)
    "http_429",           # rate limited: retry later (bounded)
    "http_5xx",           # server error: retry (bounded)
    "http_4xx",           # 403/404/410 ...: permanent for this URL
    "blocked_or_waf",     # bot protection / access denied page: use a browser or another source
    "js_required",        # content rendered by JavaScript (e-Gov PCM detail pages)
    "too_large",
    "empty_or_scanned",   # no extractable text (image PDF): never infer its content
    "non_official_url",
    "parse_error",
)
TRANSIENT_ERRORS = frozenset({"transient_network", "http_429", "http_5xx"})
MAX_FETCH_ATTEMPTS = 3

# Legal facts every explainer depends on. ``critical`` categories must be
# ``confirmed`` (with an official source) or ``not_applicable`` (with a stated
# reason) before the dossier counts as SOURCES_VERIFIED or may be published.
FACT_CATEGORIES = (
    "instrument_form",       # 法案 / 成立法 / 公布法令 / 施行法令 / 案 / ガイドライン
    "promulgation_date",     # 公布日 (or not_applicable for a bill / draft)
    "effective_date",        # 施行日 (fixed, or delegated to a Cabinet Order)
    "partial_effective_date",  # 一部施行日
    "amended_provisions",    # 改正対象条文
    "old_vs_new",            # 新旧制度の差異
    "scope",                 # 適用対象
    "obligation_type",       # 義務 / 禁止 / 努力義務 / 推奨
    "transitional_measures",  # 経過措置
    "delegation",            # 政令・省令・告示への委任
    "policy_background",     # 政策背景・立法目的
    "business_impact",       # 企業実務への影響
)
CRITICAL_CATEGORIES = (
    "instrument_form", "promulgation_date", "effective_date", "amended_provisions",
    "old_vs_new", "scope", "obligation_type", "transitional_measures", "delegation",
)
# Only analysis may be labelled inference; a legal fact is either sourced or not.
INFERENCE_CATEGORIES = frozenset({"policy_background", "business_impact"})
FACT_STATUSES = ("confirmed", "not_applicable", "unconfirmed", "inference")
INSTRUMENT_FORMS = (
    "bill", "enacted_act", "promulgated_instrument", "in_force_instrument",
    "draft_instrument", "guideline",
)
OBLIGATION_TYPES = ("obligation", "prohibition", "effort_duty", "recommendation", "none")
# The explainer's ``current_stage`` must agree with the confirmed instrument form.
FORM_STAGES = {
    "bill": {"under_consideration", "bill_submitted", "diet_deliberation", "lapsed", "withdrawn"},
    "enacted_act": {"enacted"},
    "promulgated_instrument": {"promulgated", "partially_in_force"},
    "in_force_instrument": {"in_force", "partially_in_force"},
    "draft_instrument": {
        "under_consideration", "public_comment_open", "public_comment_closed",
        "comment_results_published", "not_adopted",
    },
    "guideline": set(ex.GUIDELINE_STAGES),
}
MAX_TEXT = 1500
MAX_EXCERPT = 1500
MAX_SOURCES = 40
MAX_FACTS = 60
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RAW_ID_RE = re.compile(r"^raw-[0-9a-f]{16}$")
SOURCE_ID_RE = re.compile(r"^s[0-9]{1,3}$")
FACT_ID_RE = re.compile(r"^f[0-9]{1,3}$")

# Explainers that existed before case files did (2026-11-13). They keep
# publishing exactly as before; every dossier created after this needs a case
# file whose critical facts are confirmed before it can be published.
GRANDFATHERED = frozenset({
    "agricultural-modernisation-loan-order-300-2026", "agricultural-modernization-finance-2026",
    "agrivoltaic-farmland-permits-2026", "agrivoltaic-project-recognition-2026",
    "antarctic-environmental-emergencies-2026", "antibiotic-supply-security-plans-2026-ordinance",
    "appi-2026-amendment", "automotive-decarbonization-2026-bill",
    "building-energy-2026-lifecycle-carbon", "building-pfas-drinking-water-tests-2026-draft",
    "chemical-trade-secret-record-custodian-2026", "dc-pension-digital-procedures-2026",
    "economic-security-jbic-2026-amendment", "eia-repowering-thresholds-2026-order",
    "election-online-ai-2026-amendment", "energy-cargo-marine-insurance-2026-bill",
    "environment-ministry-regional-bureaus-2026", "export-control-2026-october-draft",
    "fefta-2026-inward-investment-screening", "food-consumption-tax-temporary-cut-2026-bill",
    "gas-safety-shutoff-advanced-operator-2026", "health-insurance-2026-amendment",
    "high-cost-medical-care-patient-protection-2026-bill", "high-pressure-gas-qualification-cards-2026",
    "industrial-competitiveness-2026-amendment", "inhabitant-tax-inflation-relief-2026-bill",
    "jbic-economic-security-overseas-investment-2026", "jogmec-2026-october-bill",
    "lithium-battery-waste-fire-standards-2026", "long-chain-pfas-firefighting-foam-2026-draft",
    "national-health-insurance-child-levy-2026-draft", "nisa-government-bond-exemption-2026-bill",
    "noto-disaster-employment-secondment-2026", "organized-theft-prosecution-2026-bill",
    "osh-chemical-label-sds-2026-draft", "osh-skin-hazard-chemicals-2026-draft",
    "pcb-waste-2026-low-concentration-amendment", "privacy-security-guidance-2026-draft",
    "regional-finance-strengthening-2026", "sme-subcontractor-support-2026-bill",
    "social-welfare-local-support-rules-2026", "solar-pv-waste-recycling-2026",
    "telecom-interconnection-cost-model-2026", "telecom-number-identity-verification-2026",
    "truck-relay-transport-authority-2026-order", "ventilator-economic-security-supply-plan-2026",
    "waste-management-scrap-yards-2026", "water-heater-energy-efficiency-2026-order",
    "workers-compensation-2026-amendment", "working-tax-social-insurance-relief-2026-bill",
    "youth-harmful-advertising-platforms-2026",
})

OFFICIAL_SUFFIXES = ("go.jp", "lg.jp", "jpx.co.jp", "jsda.or.jp")


def official_url(url: Any) -> bool:
    """HTTPS on a Japanese public-sector (or JPX/JSDA) host; mirrors the evidence collector."""
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return (
        parts.scheme == "https" and bool(host) and not parts.username and not parts.password
        and any(host == s or host.endswith("." + s) for s in OFFICIAL_SUFFIXES)
    )


def state_path(dossier_id: str, directory: Path = STATE_DIR) -> Path:
    return directory / f"{dossier_id}.json"


def load_states(directory: Path = STATE_DIR, problems: list[str] | None = None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            if problems is not None:
                problems.append(f"{path.name}: unreadable case file ({type(exc).__name__})")
            continue
        if isinstance(value, dict):
            out[path.stem] = value
        elif problems is not None:
            problems.append(f"{path.name}: case file must be a JSON object")
    return out


def _text(value: Any, where: str, errors: list[str], *, required: bool = True, limit: int = MAX_TEXT) -> str:
    if value is None or value == "":
        if required:
            errors.append(f"{where} is required")
        return ""
    if not isinstance(value, str):
        errors.append(f"{where} must be a string")
        return ""
    if len(value) > limit:
        errors.append(f"{where} is longer than {limit} characters ({len(value)})")
    if ex._MARKUP_RE.search(value):
        errors.append(f"{where} must be plain text (no HTML, Markdown links or code fences)")
    return value


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate_state(dossier_id: str, state: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Schema and internal-consistency checks for one case file (no repository context)."""
    errors: list[str] = []
    warnings: list[str] = []
    if state.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if state.get("dossier_id") != dossier_id or not ex.DOSSIER_ID_PATTERN.match(dossier_id):
        errors.append("dossier_id must equal the file name (lowercase letters, digits, hyphens)")
    if state.get("kind") not in ("new", "update"):
        errors.append("kind must be 'new' or 'update'")
    seed = state.get("seed_item_id")
    if seed is not None and not (isinstance(seed, str) and RAW_ID_RE.match(seed)):
        errors.append("seed_item_id must be a raw-<16 hex> id")
    if "stage" in state:
        errors.append("stage is computed by explainer_pipeline.py; do not declare it (use hold / next_action)")
    _text(state.get("next_action"), "next_action", errors, required=False, limit=600)

    hold = state.get("hold")
    if hold is not None:
        if not isinstance(hold, Mapping):
            errors.append("hold must be an object or null")
        else:
            if hold.get("state") not in HOLD_STATES:
                errors.append(f"hold.state must be one of {', '.join(HOLD_STATES)}")
            _text(hold.get("reason"), "hold.reason", errors, limit=800)
            condition = hold.get("unblock_condition")
            if condition not in UNBLOCK_CONDITIONS:
                errors.append(f"hold.unblock_condition must be one of {', '.join(UNBLOCK_CONDITIONS)}")
            retry_after = hold.get("retry_after")
            if retry_after is not None and strict_iso_date(retry_after) is None:
                errors.append("hold.retry_after must be YYYY-MM-DD")
            if condition == "date" and retry_after is None:
                errors.append("hold.retry_after is required when unblock_condition is 'date'")
            if hold.get("state") == "RETRY_WAIT" and retry_after is None:
                errors.append("RETRY_WAIT needs hold.retry_after")

    sources = state.get("sources", [])
    known_sources: dict[str, Mapping[str, Any]] = {}
    if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
        errors.append(f"sources must be a list of at most {MAX_SOURCES} entries")
        sources = []
    for n, src in enumerate(sources):
        where = f"sources[{n}]"
        if not isinstance(src, Mapping):
            errors.append(f"{where} must be an object")
            continue
        sid = src.get("id")
        if not (isinstance(sid, str) and SOURCE_ID_RE.match(sid)):
            errors.append(f"{where}.id must look like s1")
        elif sid in known_sources:
            errors.append(f"{where}.id {sid} is duplicated")
        else:
            known_sources[sid] = src
        if not official_url(src.get("url")):
            errors.append(f"{where}.url must be an https URL on an official Japanese public-sector host")
        if src.get("kind") not in SOURCE_KINDS:
            errors.append(f"{where}.kind must be one of {', '.join(SOURCE_KINDS)}")
        status = src.get("status")
        if status not in SOURCE_STATUSES:
            errors.append(f"{where}.status must be one of {', '.join(SOURCE_STATUSES)}")
        attempts = src.get("attempts", 0)
        if not isinstance(attempts, int) or attempts < 0:
            errors.append(f"{where}.attempts must be a non-negative integer")
        if status == "fetched":
            if not (isinstance(src.get("sha256"), str) and SHA256_RE.match(src["sha256"])):
                errors.append(f"{where}.sha256 must be the hex SHA-256 of the fetched content")
            if not _timestamp(src.get("fetched_at")):
                errors.append(f"{where}.fetched_at must be an ISO timestamp")
            if src.get("error_class") is not None:
                errors.append(f"{where}.error_class must be null for a fetched source")
        if status == "failed":
            if src.get("error_class") not in ERROR_CLASSES:
                errors.append(f"{where}.error_class must be one of {', '.join(ERROR_CLASSES)}")
            if not _timestamp(src.get("last_attempt_at")):
                errors.append(f"{where}.last_attempt_at must be an ISO timestamp")
        _text(src.get("excerpt"), f"{where}.excerpt", errors, required=False, limit=MAX_EXCERPT)
        version = src.get("version")
        if version is not None:
            if not isinstance(version, Mapping):
                errors.append(f"{where}.version must be an object")
            elif version.get("as_of") is not None and strict_iso_date(version.get("as_of")) is None:
                errors.append(f"{where}.version.as_of must be YYYY-MM-DD")

    facts = state.get("facts", [])
    if not isinstance(facts, list) or len(facts) > MAX_FACTS:
        errors.append(f"facts must be a list of at most {MAX_FACTS} entries")
        facts = []
    seen_ids: set[str] = set()
    for n, fact in enumerate(facts):
        where = f"facts[{n}]"
        if not isinstance(fact, Mapping):
            errors.append(f"{where} must be an object")
            continue
        fid = fact.get("id")
        if not (isinstance(fid, str) and FACT_ID_RE.match(fid)) or fid in seen_ids:
            errors.append(f"{where}.id must be a unique id like f1")
        else:
            seen_ids.add(fid)
        category = fact.get("category")
        if category not in FACT_CATEGORIES:
            errors.append(f"{where}.category must be one of {', '.join(FACT_CATEGORIES)}")
        status = fact.get("status")
        if status not in FACT_STATUSES:
            errors.append(f"{where}.status must be one of {', '.join(FACT_STATUSES)}")
        _text(fact.get("statement"), f"{where}.statement", errors)
        _text(fact.get("quote"), f"{where}.quote", errors, required=False, limit=600)
        ids = fact.get("source_ids", [])
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            errors.append(f"{where}.source_ids must be a list of source ids")
            ids = []
        for sid in ids:
            if sid not in known_sources:
                errors.append(f"{where}.source_ids: unknown source {sid}")
            elif status == "confirmed" and known_sources[sid].get("status") != "fetched":
                errors.append(f"{where}: a confirmed fact must cite a source with status 'fetched' ({sid})")
        if status == "confirmed" and not ids:
            errors.append(f"{where}: a confirmed fact needs at least one official source id")
        if status == "inference" and category not in INFERENCE_CATEGORIES:
            errors.append(f"{where}: only policy_background / business_impact may be inference, never {category}")
        if category == "instrument_form" and status == "confirmed" and fact.get("value") not in INSTRUMENT_FORMS:
            errors.append(f"{where}.value must be one of {', '.join(INSTRUMENT_FORMS)}")
        if category == "obligation_type" and status == "confirmed":
            value = fact.get("value")
            if not isinstance(value, list) or not value or any(v not in OBLIGATION_TYPES for v in value):
                errors.append(f"{where}.value must list one or more of {', '.join(OBLIGATION_TYPES)}")
    unresolved = state.get("unresolved", [])
    if not isinstance(unresolved, list) or not all(isinstance(u, str) and u.strip() for u in unresolved):
        errors.append("unresolved must be a list of non-empty strings")
    return errors, warnings


def critical_gaps(state: Mapping[str, Any]) -> list[str]:
    """Critical categories that are missing or not yet confirmed / not_applicable."""
    settled: dict[str, bool] = {c: False for c in CRITICAL_CATEGORIES}
    blocked: set[str] = set()
    for fact in state.get("facts") or []:
        if not isinstance(fact, Mapping) or fact.get("category") not in settled:
            continue
        if fact.get("status") in ("confirmed", "not_applicable"):
            settled[fact["category"]] = True
        else:
            blocked.add(fact["category"])
    # One unconfirmed statement in a category keeps it open even if another is confirmed:
    # a partly confirmed effective-date picture is not a confirmed one.
    return [c for c in CRITICAL_CATEGORIES if not settled[c] or c in blocked]


def instrument_form(state: Mapping[str, Any]) -> str | None:
    for fact in state.get("facts") or []:
        if isinstance(fact, Mapping) and fact.get("category") == "instrument_form" and fact.get("status") == "confirmed":
            return fact.get("value")
    return None


def requires_case_file(dossier_id: str) -> bool:
    return dossier_id not in GRANDFATHERED


def publication_gate(dossier_id: str, explainer: Mapping[str, Any], state: Mapping[str, Any] | None) -> list[str]:
    """Errors that must stop a NEW dossier's publication. Never relaxes explainers.validate."""
    if explainer.get("status") != "published":
        return []
    if state is None:
        return [] if not requires_case_file(dossier_id) else [
            "published dossier needs data/explainer_state/<dossier_id>.json with its confirmed legal facts "
            "(run: python scripts/explainer_state.py init <dossier_id> --seed <raw-id>)"
        ]
    errors = []
    gaps = critical_gaps(state)
    if gaps:
        errors.append("critical legal facts not confirmed: " + ", ".join(gaps))
    hold = state.get("hold")
    if isinstance(hold, Mapping) and hold.get("state") in ("BLOCKED", "REVIEW_REQUIRED"):
        errors.append(f"case file is on hold ({hold.get('state')}): {hold.get('reason')}")
    form = instrument_form(state)
    stage = explainer.get("current_stage")
    if form and stage and stage not in FORM_STAGES.get(form, set()):
        errors.append(f"current_stage {stage!r} contradicts the confirmed instrument form {form!r}")
    related = set(explainer.get("related_item_ids") or [])
    seed = state.get("seed_item_id")
    if seed and related and seed not in related:
        errors.append(f"case file seed {seed} is not among the explainer's related_item_ids")
    return errors


def citation_warnings(explainer: Mapping[str, Any], state: Mapping[str, Any]) -> list[str]:
    """Confirmed critical facts should be backed by a source the explainer itself cites."""
    cited = {s.get("url") for s in explainer.get("official_sources") or [] if isinstance(s, Mapping)}
    by_id = {s.get("id"): s for s in state.get("sources") or [] if isinstance(s, Mapping)}
    out = []
    for fact in state.get("facts") or []:
        if not isinstance(fact, Mapping) or fact.get("status") != "confirmed":
            continue
        if fact.get("category") not in CRITICAL_CATEGORIES:
            continue
        urls = {by_id.get(i, {}).get("url") for i in fact.get("source_ids") or []}
        if urls and not urls & cited:
            out.append(f"{fact.get('id')} ({fact.get('category')}) is not backed by any URL in official_sources")
    return out


def hold_active(hold: Mapping[str, Any] | None, *, today: date, fingerprint_now: str | None,
                fingerprint_at_hold: str | None) -> bool:
    """Whether a hold still applies. Releases are automatic and conservative."""
    if not isinstance(hold, Mapping) or hold.get("state") not in HOLD_STATES:
        return False
    retry_after = strict_iso_date(hold.get("retry_after"))
    condition = hold.get("unblock_condition")
    if condition == "official_records_change":
        if fingerprint_now and fingerprint_at_hold and fingerprint_now != fingerprint_at_hold:
            return False
        if retry_after and today >= retry_after:
            return False
        return True
    if condition == "date":
        return not (retry_after and today >= retry_after)
    return True  # manual


def retry_due(source: Mapping[str, Any]) -> bool:
    """A failed source is worth another fetch only for transient errors, within the budget."""
    return (
        source.get("status") == "failed"
        and source.get("error_class") in TRANSIENT_ERRORS
        and int(source.get("attempts") or 0) < MAX_FETCH_ATTEMPTS
    )


def source_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    by_error: dict[str, int] = {}
    fetched = failed = pending = 0
    for src in state.get("sources") or []:
        if not isinstance(src, Mapping):
            continue
        status = src.get("status")
        if status == "fetched":
            fetched += 1
        elif status == "failed":
            failed += 1
            key = str(src.get("error_class"))
            by_error[key] = by_error.get(key, 0) + 1
        else:
            pending += 1
    return {"fetched": fetched, "failed": failed, "not_fetched": pending, "failed_by_error": dict(sorted(by_error.items()))}


def new_state(dossier_id: str, *, seed: str | None, kind: str, queue_id: str | None,
              packet: Mapping[str, Any] | None, today: date) -> dict[str, Any]:
    """A case file with every fact category open and the research packet's official URLs listed."""
    sources = []
    if packet:
        urls: list[str] = []
        for record in packet.get("records") or []:
            url = record.get("source_url") if isinstance(record, Mapping) else None
            if official_url(url) and url not in urls:
                urls.append(url)
        for url in packet.get("e_gov_law_api") or []:
            if official_url(url) and url not in urls:
                urls.append(url)
        for n, url in enumerate(urls[:MAX_SOURCES], start=1):
            host = (urlsplit(url).hostname or "").lower()
            kind_guess = (
                "egov_law" if host.startswith("laws.e-gov") or "elaws" in host
                else "egov_public_comment" if host.startswith("public-comment.e-gov")
                else "diet" if host.endswith("shugiin.go.jp") or host.endswith("sangiin.go.jp")
                else "gazette" if "kanpo" in host
                else "self_regulatory" if host.endswith("jpx.co.jp") or host.endswith("jsda.or.jp")
                else "ministry"
            )
            sources.append({"id": f"s{n}", "url": url, "kind": kind_guess, "status": "not_fetched", "attempts": 0})
    facts = [
        {"id": f"f{n}", "category": c, "status": "unconfirmed", "statement": "To be confirmed from the official text.",
         "source_ids": []}
        for n, c in enumerate(FACT_CATEGORIES, start=1)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "dossier_id": dossier_id,
        "kind": kind,
        "queue_id": queue_id,
        "seed_item_id": seed,
        "hold": None,
        "next_action": "Open each listed official source (scripts/explainer_sources.py fetch) and confirm the critical facts.",
        "sources": sources,
        "facts": facts,
        "unresolved": [],
        "created_on": today.isoformat(),
    }


def validate_all(
    explainers: Mapping[str, Mapping[str, Any]] | None = None,
    directory: Path = STATE_DIR,
) -> tuple[list[str], list[str]]:
    """Every case file plus the publication gate for every published explainer."""
    explainers = ex.load_explainers() if explainers is None else explainers
    errors: list[str] = []
    warnings: list[str] = []
    states = load_states(directory, problems=errors)
    for dossier_id, state in sorted(states.items()):
        e, w = validate_state(dossier_id, state)
        errors += [f"{dossier_id}: {m}" for m in e]
        warnings += [f"{dossier_id}: {m}" for m in w]
        if dossier_id in explainers:
            warnings += [f"{dossier_id}: {m}" for m in citation_warnings(explainers[dossier_id], state)]
    for dossier_id, data in sorted(explainers.items()):
        errors += [f"{dossier_id}: {m}" for m in publication_gate(dossier_id, data, states.get(dossier_id))]
    return errors, warnings


def dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=STATE_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create a case file (from the bounded research packet when present)")
    init.add_argument("dossier_id")
    init.add_argument("--seed", required=True, help="raw-<16 hex> seed item (queue suggested_seed_item_id)")
    init.add_argument("--kind", choices=("new", "update"), default="new")
    init.add_argument("--queue-id", default=None)
    sub.add_parser("validate", help="validate every case file and the publication gate")
    hold = sub.add_parser("hold", help="put a dossier on hold so later slots move on instead of re-researching")
    hold.add_argument("dossier_id")
    hold.add_argument("--state", choices=HOLD_STATES, required=True)
    hold.add_argument("--reason", required=True)
    hold.add_argument("--until", choices=UNBLOCK_CONDITIONS, default="official_records_change")
    hold.add_argument("--retry-after", default=None, help="YYYY-MM-DD")
    hold.add_argument("--next-action", default=None)
    rel = sub.add_parser("release", help="clear a hold")
    rel.add_argument("dossier_id")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    today = datetime.now(JST).date()

    if args.command == "init":
        path = state_path(args.dossier_id, args.dir)
        if path.exists():
            raise SystemExit(f"{path} already exists: resume it instead of starting again")
        if not RAW_ID_RE.match(args.seed):
            raise SystemExit("--seed must be a raw-<16 hex> id")
        packet_path = RESEARCH_DIR / f"{args.seed}.json"
        packet = ex.load_json(packet_path) if packet_path.is_file() else None
        state = new_state(args.dossier_id, seed=args.seed, kind=args.kind,
                          queue_id=args.queue_id or (packet or {}).get("queue_id"), packet=packet, today=today)
        ex.write_if_changed(path, dump(state))
        print(f"{path} ({len(state['sources'])} official URLs from {'packet' if packet else 'no packet'})")
        return 0
    if args.command == "validate":
        errors, warnings = validate_all(directory=args.dir)
        for message in warnings:
            print(f"warning: {message}")
        for message in errors:
            print(f"error: {message}")
        print(f"case files: {len(load_states(args.dir))}; errors={len(errors)} warnings={len(warnings)}")
        return 1 if errors else 0
    path = state_path(args.dossier_id, args.dir)
    if not path.is_file():
        raise SystemExit(f"{path} does not exist")
    state = ex.load_json(path)
    if args.command == "hold":
        state["hold"] = {
            "state": args.state, "reason": args.reason, "unblock_condition": args.until,
            "retry_after": args.retry_after, "since": today.isoformat(),
        }
        if args.next_action:
            state["next_action"] = args.next_action
    else:
        state["hold"] = None
    errors, _ = validate_state(args.dossier_id, state)
    if errors:
        raise SystemExit("refusing to write an invalid case file:\n  " + "\n  ".join(errors))
    ex.write_if_changed(path, dump(state))
    print(f"{path}: hold={state['hold']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
