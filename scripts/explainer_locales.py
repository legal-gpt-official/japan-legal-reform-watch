"""Official-source-based localized reform explainers (Japanese and Simplified Chinese).

English is canonical. A translation is displayed ONLY if it covers every
paragraph, next step, checklist and dated label, states no date or number the
English does not (``explainer_facts``), AND its OWN freshness stamp matches the
canonical verification date, fingerprint and English file. Each locale carries
its own stamp (``translations.<lang>.english_*``), so refreshing one language
never revalidates the other. Never silently serve stale translations.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

import explainer_facts as ef
import explainers as ex

LOCALES = ("ja", "zh-Hans")
URL_SLUGS = {"ja": "ja", "zh-Hans": "zh"}
LANG_LABELS = {"en": "English", "ja": "日本語", "zh-Hans": "简体中文"}
TRANSLATIONS_DIR = ex.REPO_ROOT / "data" / "explainer_locales"
_PLAIN = re.compile(r"<[^>]+>|\[[^\]\n]{1,80}\]\([^)\s]{1,200}\)|```")
_MAX_TEXT = 2600
STAMP_KEYS = ("english_verified_at", "english_source_fingerprint", "english_file_sha")


def load_locales(directory: Path = TRANSLATIONS_DIR, problems: list[str] | None = None) -> dict[str, dict]:
    """Translation bundles by dossier id. An unreadable file is skipped (English stays
    publishable) and, when ``problems`` is given, reported there so CI can flag it."""
    out = {}
    if directory.is_dir():
        for p in sorted(directory.glob("*.json")):
            try:
                value = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                if problems is not None:
                    problems.append(f"{p.name}: unreadable translation file ({type(exc).__name__})")
                continue
            if isinstance(value, dict) and value.get("dossier_id") == p.stem:
                out[p.stem] = value
            elif problems is not None:
                problems.append(f"{p.name}: dossier_id must equal the file name")
    return out


def canonical_file_sha(dossier_id: str) -> str:
    """Git's blob SHA-1 of the exact canonical English JSON, including same-day edits.

    CRLF is folded to LF first, as Git does when it stores a text file, so a
    Windows checkout (core.autocrlf) computes the same value as CI and
    ``git hash-object``; without that every translation looked stale locally
    and a local page build deleted every Japanese and Chinese page.
    """
    path = ex.EXPLAINERS_DIR / (dossier_id + ".json")
    if not path.is_file():
        return ""
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + bytes([0]) + raw).hexdigest()


def _good_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= _MAX_TEXT and not _PLAIN.search(value)


def _list_matches(local: Any, canonical: Any) -> bool:
    return isinstance(local, list) and isinstance(canonical, list) and len(local) == len(canonical) and all(_good_text(s) for s in local)


def english_texts(en: Mapping[str, Any]) -> list[str]:
    """Every English string a translation renders, plus the structured dates beside them."""
    out = [en.get("title_en"), *((en.get("quick_take") or {}).values())]
    for key in ("background", "history", "business_impact"):
        out += (en.get(key) or {}).get("paragraphs") or []
    out += (en.get("business_impact") or {}).get("checklist") or []
    steps = en.get("next_steps") or {}
    for step in steps.get("confirmed") or []:
        if isinstance(step, Mapping):
            out += [step.get("text"), step.get("date")]
    out += (steps.get("expected") or []) + (steps.get("uncertainties") or [])
    for row in en.get("key_dates") or []:
        if isinstance(row, Mapping):
            out += [row.get("label"), row.get("date")]
    return [s for s in out if isinstance(s, str)]


def local_texts(tr: Mapping[str, Any]) -> list[str]:
    quick = tr.get("quick_take") if isinstance(tr.get("quick_take"), Mapping) else {}
    out = [tr.get("title"), *quick.values()]
    for key in ("background", "history", "business_impact", "checklist", "key_dates"):
        out += tr.get(key) if isinstance(tr.get(key), list) else []
    steps = tr.get("next_steps") if isinstance(tr.get("next_steps"), Mapping) else {}
    for key in ("confirmed", "expected", "uncertainties"):
        out += steps.get(key) if isinstance(steps.get(key), list) else []
    return [s for s in out if isinstance(s, str)]


def translation_problems(en: Mapping[str, Any], bundle: Mapping[str, Any], lang: str) -> list[str]:
    problems = []
    if lang not in LOCALES:
        return ["unsupported locale"]
    if bundle.get("dossier_id") != en.get("dossier_id"):
        problems.append("dossier id mismatch")
    tr = (bundle.get("translations") or {}).get(lang)
    if not isinstance(tr, Mapping):
        return problems + [f"{lang}: no translation"]
    # The stamp belongs to the locale, not the bundle: one shared stamp let a
    # refreshed Japanese text re-certify a Chinese text written for older English.
    if tr.get("english_verified_at") != en.get("verified_at"):
        problems.append("stale English verification date")
    if tr.get("english_source_fingerprint") != en.get("source_fingerprint"):
        problems.append("stale official-record fingerprint")
    expected_blob = canonical_file_sha(str(en.get("dossier_id", "")))
    if not expected_blob or tr.get("english_file_sha") != expected_blob:
        problems.append("stale canonical English file SHA")
    if not _good_text(tr.get("title")):
        problems.append(f"{lang}: invalid title")
    quick = tr.get("quick_take")
    if not isinstance(quick, Mapping):
        problems.append(f"{lang}: quick take missing")
    else:
        for key in ("what_changed", "who_is_affected", "what_to_do_now"):
            if not _good_text(quick.get(key)) or len(quick[key]) > 550:
                problems.append(f"{lang}: invalid {key}")
    for key in ("background", "history", "business_impact"):
        if not _list_matches(tr.get(key), (en.get(key) or {}).get("paragraphs")):
            problems.append(f"{lang}: {key} paragraphs do not match English")
    for key in ("confirmed", "expected", "uncertainties"):
        source = (en.get("next_steps") or {}).get(key) or []
        if not _list_matches((tr.get("next_steps") or {}).get(key), source):
            problems.append(f"{lang}: {key} entries do not match English")
    if not _list_matches(tr.get("checklist"), (en.get("business_impact") or {}).get("checklist")):
        problems.append(f"{lang}: checklist entries do not match English")
    if not _list_matches(tr.get("key_dates"), en.get("key_dates")):
        problems.append(f"{lang}: date labels do not match English")
    unsupported = ef.unsupported_facts(english_texts(en), local_texts(tr))
    if unsupported:
        problems.append(f"{lang}: states a date or number the English does not: " + ", ".join(unsupported))
    return problems


def localized_entry(entry: ex.Published, bundle: Mapping[str, Any], lang: str) -> ex.Published | None:
    if translation_problems(entry.data, bundle, lang):
        return None
    t = bundle["translations"][lang]
    data = copy.deepcopy(entry.data)
    data["title_en"] = t["title"]
    data["quick_take"] = t["quick_take"]
    for key in ("background", "history", "business_impact"):
        data[key]["paragraphs"] = t[key]
    data["business_impact"]["checklist"] = t["checklist"]
    for key in ("expected", "uncertainties"):
        data["next_steps"][key] = t["next_steps"][key]
    for old, txt in zip(data["next_steps"]["confirmed"], t["next_steps"]["confirmed"]):
        old["text"] = txt
    for old, txt in zip(data["key_dates"], t["key_dates"]):
        old["label"] = txt
    return ex.Published(entry.dossier_id, data, entry.report, entry.visible)


STALE_MARKERS = (
    "stale English verification date",
    "stale official-record fingerprint",
    "stale canonical English file SHA",
)


def validate_translations() -> tuple[list[str], list[str]]:
    """Return blocking errors and non-blocking absence/staleness warnings.

    English publication must remain possible while a translation catches up.
    The page builder checks these same freshness keys and withholds outdated
    locale pages, so a stale or absent locale can never be shown as current.
    A translation that *claims* current English keys but has broken sections
    is invalid and fails CI to prevent publishing malformed locale content.
    """
    english = ex.load_explainers()
    errors, warnings = [], []
    bundles = load_locales(problems=errors)
    for dossier_id, data in sorted(english.items()):
        if data.get("status") != "published":
            continue
        bundle = bundles.get(dossier_id)
        if bundle is None:
            warnings.append(f"{dossier_id}: Japanese and Chinese translations pending; English-only")
            continue
        for lang in LOCALES:
            problems = translation_problems(data, bundle, lang)
            stale = [p for p in problems if p in STALE_MARKERS]
            invalid = [p for p in problems if p not in STALE_MARKERS]
            if stale:
                warnings.append(f"{dossier_id} ({lang}): outdated translation withheld; English-only until refreshed")
            elif invalid:
                errors.extend(f"{dossier_id} ({lang}): {p}" for p in invalid)
    for dossier_id, bundle in sorted(bundles.items()):
        if any(key in bundle for key in STAMP_KEYS):
            errors.append(f"{dossier_id}: move english_* stamps into translations.<lang> (one per locale)")
        if dossier_id not in english:
            errors.append(f"{dossier_id}: translation has no canonical English explainer")
    return errors, warnings


def validate_all_translations() -> list[str]:
    """Compatibility: return only problems severe enough to block publication."""
    return validate_translations()[0]


def main() -> int:
    import sys
    if sys.argv[1:] != ["validate"]:
        print("usage: python scripts/explainer_locales.py validate")
        return 2
    errors, warnings = validate_translations()
    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"Translation validation failed: {len(errors)} errors ({len(warnings)} pending)")
        return 1
    print(f"Translation integrity passed; pending/outdated locales: {len(warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
