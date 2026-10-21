"""Official-source-based localized reform explainers (Japanese and Simplified Chinese).

English is canonical. A translation is displayed ONLY if it covers every
paragraph, next step, checklist and dated label AND matches the canonical
verification date/fingerprint. Never silently serve stale translations.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

import explainers as ex

LOCALES = ("ja", "zh-Hans")
URL_SLUGS = {"ja": "ja", "zh-Hans": "zh"}
LANG_LABELS = {"en": "English", "ja": "日本語", "zh-Hans": "简体中文"}
TRANSLATIONS_DIR = ex.REPO_ROOT / "data" / "explainer_locales"
_PLAIN = re.compile(r"<[^>]+>|\\[.{0,80}\\]\\(.{0,200}\\)|```")
_MAX_TEXT = 2600


def load_locales(directory: Path = TRANSLATIONS_DIR) -> dict[str, dict]:
    out = {}
    if directory.is_dir():
        for p in sorted(directory.glob("*.json")):
            try:
                value = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(value, dict) and value.get("dossier_id") == p.stem:
                out[p.stem] = value
    return out


def canonical_file_sha(dossier_id: str) -> str:
    """Git's blob SHA-1 of the exact canonical English JSON, including same-day edits."""
    path = ex.EXPLAINERS_DIR / (dossier_id + ".json")
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    return hashlib.sha1(f"blob {len(raw)}\\0".encode("ascii") + raw).hexdigest()


def _good_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= _MAX_TEXT and not _PLAIN.search(value)


def _list_matches(local: Any, canonical: Any) -> bool:
    return isinstance(local, list) and isinstance(canonical, list) and len(local) == len(canonical) and all(_good_text(s) for s in local)


def translation_problems(en: Mapping[str, Any], bundle: Mapping[str, Any], lang: str) -> list[str]:
    problems = []
    if lang not in LOCALES:
        return ["unsupported locale"]
    if bundle.get("dossier_id") != en.get("dossier_id"):
        problems.append("dossier id mismatch")
    if bundle.get("english_verified_at") != en.get("verified_at"):
        problems.append("stale English verification date")
    if bundle.get("english_source_fingerprint") != en.get("source_fingerprint"):
        problems.append("stale official-record fingerprint")
    expected_blob = canonical_file_sha(str(en.get("dossier_id", "")))
    if not expected_blob or bundle.get("english_file_sha") != expected_blob:
        problems.append("stale canonical English file SHA")
    tr = (bundle.get("translations") or {}).get(lang)
    if not isinstance(tr, Mapping):
        return problems + [f"{lang}: no translation"]
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


def validate_all_translations() -> list[str]:
    """Fail CI for any published canonical explainer without up-to-date full translations."""
    english = ex.load_explainers()
    bundles = load_locales()
    errors = []
    for dossier_id, data in sorted(english.items()):
        if data.get("status") != "published":
            continue
        bundle = bundles.get(dossier_id)
        if bundle is None:
            errors.append(f"{dossier_id}: translations are missing")
            continue
        for lang in LOCALES:
            errors += [f"{dossier_id}: {message}"
                       for message in translation_problems(data, bundle, lang)]
    for dossier_id in bundles:
        if dossier_id not in english:
            errors.append(f"{dossier_id}: translation has no canonical English explainer")
    return errors


def main() -> int:
    import sys
    if sys.argv[1:] != ["validate"]:
        print("usage: python scripts/explainer_locales.py validate")
        return 2
    errors = validate_all_translations()
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"Translation validation failed: {len(errors)} errors")
        return 1
    print("Japanese and Chinese translations validated against all published English explainers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
