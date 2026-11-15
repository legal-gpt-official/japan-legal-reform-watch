#!/usr/bin/env python3
"""Three-language legal-meaning cross-check for reform explainers (deterministic, offline).

    python scripts/explainer_meaning.py report            # calibrate on every published dossier
    python scripts/explainer_meaning.py check <dossier>

``explainer_facts`` already proves a translation states no date or number the
English does not. Dates are not the riskiest drift, though: a Japanese or
Chinese sentence that turns "may" into "must", drops "draft", or says a bill is
in force changes the legal meaning while every number still matches. This
module aligns each English segment with the same-position Japanese and Chinese
segment (same paragraph, same list entry, same quick-take field) and compares
legal concepts found by fixed per-language cue lists:

* ``obligation`` (must / shall / required -> しなければならない / 義務 -> 必须 / 应当 / 义务)
* ``prohibition`` (prohibit / may not -> 禁止 / してはならない -> 禁止 / 不得)
* ``effort_duty`` (endeavour -> 努める / 努力義務 -> 努力 / 尽力)
* ``draft`` (draft / proposed / consultation -> 案 / 意見募集 -> 草案 / 征求意见)
* ``promulgated`` / ``in_force`` / ``enacted`` (promulgated / in force / enacted ->
  公布 / 施行 / 成立 -> 公布 / 施行·生效 / 通过·成立)
* ``not_yet`` (not yet / has not / undetermined -> 未 / まだ / 未定 -> 尚未 / 未定)

A concept present on one side and absent on the other is listed with the segment
path, for the writer to compare those segments by reading them.

Calibration (2026-11-15, all 51 published dossiers, 2,274 aligned EN-JA/EN-ZH
segments): every concept fired on 0.4-14% of segments (obligation 207, prohibition
54, effort_duty 8, draft 318, promulgated 64, in_force 193, enacted 179, not_yet
96). Reading the hits showed mostly faithful rephrasing -- "should not describe X
as Y" rendered as 「…してはならない」, "obligations" as 義務 -- with a few genuine
strengthenings. Lexical cues therefore cannot decide legal equivalence, so this
check BLOCKS nothing: it is a review list in the preflight report. Blocking stays
with the checks that proved exact: dates and numbers (explainer_facts), structure
and freshness stamps (explainer_locales) and e-Gov record data (explainer_evidence:
36/36 altered key dates and 17/17 altered stages caught, 0 false positives on the
published corpus). It never rewrites text and does not replace reading the three
versions side by side.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import explainer_locales as el  # noqa: E402
import explainers as ex  # noqa: E402

CUES: dict[str, dict[str, re.Pattern]] = {
    # A legal duty stated as such -- not advice ("should check"), which translators
    # legitimately render with 须 / 必要 / 应 and which is not a legal obligation.
    "obligation": {
        "en": re.compile(r"\b(legal(?:ly)? (?:obligation|duty|required)|statutory (?:obligation|duty)|(?:is|are|be|being|was|were) "
                         r"(?:legally )?(?:obliged|obligated|required by law) to|mandatory|obligation to|duty to|"
                         r"must (?:file|submit|notify|report|obtain|register|keep|prepare|disclose|pay|apply))\b", re.I),
        "ja": re.compile(r"義務(?!的)|しなければならない|義務付け|義務づけ"),
        "zh": re.compile(r"义务|必须(?:提交|申报|报告|取得|登记|保存|编制|披露|缴纳|申请|通知)|法定(?:要求|责任)"),
    },
    "prohibition": {
        "en": re.compile(r"\b(prohibit(?:s|ed|ion|ing)?|forbid(?:s|den)?|banned?|(?:is|are) not (?:permitted|allowed))\b", re.I),
        "ja": re.compile(r"禁止|してはならない"),
        "zh": re.compile(r"禁止|不得"),
    },
    "effort_duty": {
        "en": re.compile(r"\b(endeavou?r(?:s|ed|ing)?|duty to make (?:best )?efforts?|effort obligation|best[- ]efforts? (?:duty|obligation))\b", re.I),
        "ja": re.compile(r"努めなければならない|努めるものと|努力義務|努めること"),
        "zh": re.compile(r"努力义务|尽力|应当努力|须努力"),
    },
    "draft": {
        "en": re.compile(r"\b(draft\w*|proposed|proposal)\b", re.I),
        "ja": re.compile(r"(?<!法)案(?!内)|改正案|草案"),
        "zh": re.compile(r"草案|拟议|方案"),
    },
    "promulgated": {
        "en": re.compile(r"\bpromulgat\w*\b", re.I),
        "ja": re.compile(r"公布"),
        "zh": re.compile(r"(?<!日本农林水产省)公布(?!的个人)|颁布"),
    },
    "in_force": {
        "en": re.compile(r"\b(in force|into force|took effect|takes effect|take effect|came into effect|comes into effect|"
                         r"commence\w*|effective (?:date|from|on|as of)|became effective|become effective)\b", re.I),
        "ja": re.compile(r"施行(?!令|規則|規程|細則)|効力を生|発効"),
        "zh": re.compile(r"施行(?!令|规则|细则)|生效"),
    },
    "enacted": {
        "en": re.compile(r"\b(enact(?:ed|ment|s)?|passed (?:the|by the) (?:diet|house)|became law)\b", re.I),
        "ja": re.compile(r"成立|可決"),
        "zh": re.compile(r"(?:获|获得|经)?(?:国会)?(?:审议)?通过(?!贴息|符合|金融)|成立(?!的)"),
    },
    "not_yet": {
        "en": re.compile(r"\b(not yet|has not been|have not been|undetermined|undecided|not (?:been )?(?:fixed|determined|set|confirmed|decided))\b", re.I),
        "ja": re.compile(r"未定|未確定|未施行|未公布|未成立|まだ|定まっていない|決まっていない|確定していない|確定されていない"),
        "zh": re.compile(r"尚未|未定|未确定|未施行|未公布|未成立|还没有|尚不确定|待定"),
    },
}
CONCEPTS = tuple(CUES)
# Calibration (see the module docstring) found no concept precise enough to block on.
# Add one here only with a new corpus calibration that justifies it.
BLOCKING: frozenset[str] = frozenset()
REVIEW_PRIORITY = ("obligation", "prohibition", "effort_duty", "not_yet", "enacted", "in_force", "promulgated", "draft")
LANG_KEY = {"ja": "ja", "zh-Hans": "zh"}


def concepts(text: str, lang: str) -> set[str]:
    return {c for c, cues in CUES.items() if cues[lang].search(text or "")}


def aligned_segments(en: Mapping[str, Any], tr: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """(path, English text, translated text) for every same-position pair."""
    out: list[tuple[str, str, str]] = []
    if isinstance(en.get("title_en"), str) and isinstance(tr.get("title"), str):
        out.append(("title", en["title_en"], tr["title"]))
    qe, qt = en.get("quick_take") or {}, tr.get("quick_take") or {}
    for key in ("what_changed", "who_is_affected", "what_to_do_now"):
        if isinstance(qe.get(key), str) and isinstance(qt.get(key), str):
            out.append((f"quick_take.{key}", qe[key], qt[key]))
    for key in ("background", "history", "business_impact"):
        for n, (a, b) in enumerate(zip((en.get(key) or {}).get("paragraphs") or [], tr.get(key) or [])):
            out.append((f"{key}[{n}]", a, b))
    for n, (a, b) in enumerate(zip((en.get("business_impact") or {}).get("checklist") or [], tr.get("checklist") or [])):
        out.append((f"checklist[{n}]", a, b))
    steps_en, steps_tr = en.get("next_steps") or {}, tr.get("next_steps") or {}
    for n, (a, b) in enumerate(zip(steps_en.get("confirmed") or [], steps_tr.get("confirmed") or [])):
        out.append((f"next_steps.confirmed[{n}]", (a or {}).get("text", "") if isinstance(a, Mapping) else str(a), b))
    for key in ("expected", "uncertainties"):
        for n, (a, b) in enumerate(zip(steps_en.get(key) or [], steps_tr.get(key) or [])):
            out.append((f"next_steps.{key}[{n}]", a, b))
    for n, (a, b) in enumerate(zip(en.get("key_dates") or [], tr.get("key_dates") or [])):
        out.append((f"key_dates[{n}]", (a or {}).get("label", "") if isinstance(a, Mapping) else str(a), b))
    return [(p, a, b) for p, a, b in out if isinstance(a, str) and isinstance(b, str)]


def findings(en: Mapping[str, Any], tr: Mapping[str, Any], lang: str) -> list[dict[str, str]]:
    """Concepts present in one language and absent in the other, per aligned segment."""
    key = LANG_KEY[lang]
    out = []
    for path, a, b in aligned_segments(en, tr):
        ce, ct = concepts(a, "en"), concepts(b, key)
        for concept in sorted(ce ^ ct):
            out.append({"path": path, "concept": concept,
                        "direction": "missing_in_translation" if concept in ce else "added_in_translation",
                        "english": a[:160], "translation": b[:160]})
    return out


def blocking(found: Iterable[Mapping[str, str]]) -> list[str]:
    return [f"{f['path']}: legal concept '{f['concept']}' {f['direction'].replace('_', ' ')}"
            for f in found if f["concept"] in BLOCKING]


def corpus_report() -> dict[str, Any]:
    """Firing rate per concept over every published dossier's aligned segments."""
    english = ex.load_explainers()
    bundles = el.load_locales()
    segments = 0
    hits: Counter = Counter()
    examples: dict[str, list[dict]] = {}
    for dossier_id, en in sorted(english.items()):
        if en.get("status") != "published" or dossier_id not in bundles:
            continue
        for lang in el.LOCALES:
            tr = (bundles[dossier_id].get("translations") or {}).get(lang)
            if not isinstance(tr, Mapping):
                continue
            segments += len(aligned_segments(en, tr))
            for f in findings(en, tr, lang):
                hits[f["concept"]] += 1
                examples.setdefault(f["concept"], [])
                if len(examples[f["concept"]]) < 4:
                    examples[f["concept"]].append({"dossier": dossier_id, "lang": lang, **f})
    return {"segments": segments,
            "rates": {c: {"hits": hits[c], "rate": round(hits[c] / segments, 4) if segments else 0} for c in CONCEPTS},
            "examples": examples}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args[:1] == ["report"]:
        print(json.dumps(corpus_report(), ensure_ascii=False, indent=2))
        return 0
    if len(args) == 2 and args[0] == "check":
        en = ex.load_explainers().get(args[1])
        bundle = el.load_locales().get(args[1])
        if not en or not bundle:
            raise SystemExit("unknown dossier or no translations")
        bad = 0
        for lang in el.LOCALES:
            for f in findings(en, (bundle.get("translations") or {}).get(lang) or {}, lang):
                level = "error" if f["concept"] in BLOCKING else "warning"
                bad += level == "error"
                print(f"{level}: {lang} {f['path']} {f['concept']} {f['direction']}")
        return 1 if bad else 0
    print("usage: explainer_meaning.py report | check <dossier>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
