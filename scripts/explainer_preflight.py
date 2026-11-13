#!/usr/bin/env python3
"""One-pass, actionable dossier check before (and at the start of) CI.

    python scripts/explainer_preflight.py check <dossier_id> [...]
    python scripts/explainer_preflight.py check --changed-from origin/main
    python scripts/explainer_preflight.py sync-stamps <dossier_id> --lang ja --lang zh-Hans

Why: on 2026-11-10..12, 7 of 8 failed explainer CI runs were deterministic
dossier problems (a translation stating a number the English does not: 5; a
quick take over 360 characters: 1; an instrument number format: 1) found only
after a full ~60-second test run, reported as a unittest diff without the
sentence to fix, and each cost a push/wait round trip. ``check`` runs exactly
the dossier-level gates CI applies (explainers.validate, the case-file
publication gate, translation completeness, unsupported dates/numbers, freshness
stamps) and prints each problem with the field path and the text to change.

``sync-stamps`` writes a locale's ``english_verified_at`` /
``english_source_fingerprint`` / ``english_file_sha`` from the current English
file, so nobody computes a Git blob SHA by hand again. It refuses unless that
locale already passes every content check, and only for the languages named:
naming a language asserts that its full text was checked against this English.

Offline; no network, no API. Never relaxes a gate: anything this reports as an
error also fails ``pr-checks.yml``.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import explainer_facts as ef  # noqa: E402
import explainer_locales as el  # noqa: E402
import explainer_state as es  # noqa: E402
import explainers as ex  # noqa: E402
from alert_common import JST  # noqa: E402

QUICK_TAKE_LIMIT = 360
STALE_SOURCE_DAYS = 14


def changed_dossiers(base: str, cwd: Path = ex.REPO_ROOT) -> list[str]:
    """Dossier ids whose English, locale or case file changed against ``base``.

    Compares against the merge base when Git can compute one (a local branch
    against origin/main), else against ``base`` itself (CI: ``HEAD^1`` of the
    pull-request merge commit, in a depth-2 checkout).
    """
    probe = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=cwd, capture_output=True, text=True)
    start = probe.stdout.strip() if probe.returncode == 0 and probe.stdout.strip() else base
    out = subprocess.run(
        ["git", "diff", "--name-only", start, "HEAD", "--",
         "data/explainers", "data/explainer_locales", "data/explainer_state"],
        cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout
    ids = set()
    for line in out.splitlines():
        path = Path(line.strip())
        if path.suffix == ".json" and path.parent.name in ("explainers", "explainer_locales", "explainer_state"):
            ids.add(path.stem)
    return sorted(ids)


def _fix(message: str) -> str:
    """A short hint for the messages writers hit most often."""
    if "longer than 360" in message and "quick_take" in message:
        return "shorten that quick_take field to 360 characters or fewer"
    if "instrument number" in message:
        return "write the number exactly as e-Gov does, e.g. 令和八年法律第三十号 (kanji numerals)"
    if "stale canonical English file SHA" in message or "stale English verification" in message \
            or "stale official-record fingerprint" in message:
        return "after checking this language against the final English, run: sync-stamps <id> --lang <lang>"
    if "official_records_changed" in message:
        return "re-read the new related records, then: python scripts/explainers.py stamp <id> --verified-on <date>"
    if "critical legal facts not confirmed" in message:
        return "confirm each listed category in data/explainer_state/<id>.json from an official source (or mark not_applicable with a reason)"
    return ""


def check_dossier(
    dossier_id: str,
    *,
    explainers: Mapping[str, Mapping[str, Any]],
    index: ex.OfficialIndex,
    today: date,
    bundles: Mapping[str, Mapping[str, Any]] | None = None,
    states: Mapping[str, Mapping[str, Any]] | None = None,
    english_sha: str | None = None,
) -> dict[str, Any]:
    """Every dossier-level gate for one dossier, as {errors, warnings} with locations."""
    errors: list[str] = []
    warnings: list[str] = []
    bundles = el.load_locales() if bundles is None else bundles
    states = es.load_states() if states is None else states
    data = explainers.get(dossier_id)
    state = states.get(dossier_id)
    if state is not None:
        e, w = es.validate_state(dossier_id, state)
        errors += [f"case file: {m}" for m in e]
        warnings += [f"case file: {m}" for m in w]
    if data is None:
        if state is None:
            errors.append("no English explainer and no case file")
        return {"dossier_id": dossier_id, "errors": errors, "warnings": warnings}

    reports = ex.validate_all(explainers, index, today)
    report = reports[dossier_id]
    errors += report.errors
    warnings += report.warnings
    quick = data.get("quick_take") if isinstance(data.get("quick_take"), Mapping) else {}
    for key, value in quick.items():
        if isinstance(value, str) and len(value) > QUICK_TAKE_LIMIT:
            errors[:] = [m for m in errors if not m.startswith(f"quick_take.{key}: longer than")]
            errors.append(f"quick_take.{key}: {len(value)} characters, limit {QUICK_TAKE_LIMIT} "
                          f"(cut {len(value) - QUICK_TAKE_LIMIT})")
    errors += es.publication_gate(dossier_id, data, state)
    if state is not None:
        warnings += es.citation_warnings(data, state)
        verified = data.get("verified_at")
        by_id = {s.get("id"): s for s in state.get("sources") or [] if isinstance(s, Mapping)}
        for fact in state.get("facts") or []:
            if not isinstance(fact, Mapping) or fact.get("status") != "confirmed":
                continue
            for sid in fact.get("source_ids") or []:
                src = by_id.get(sid) or {}
                fetched = str(src.get("fetched_at") or "")[:10]
                if src.get("changed_since_previous"):
                    warnings.append(f"{fact.get('id')} cites {sid}, whose content changed at the last fetch: re-check it")
                if verified and fetched and (date.fromisoformat(verified) - date.fromisoformat(fetched)).days > STALE_SOURCE_DAYS:
                    warnings.append(f"{fact.get('id')} cites {sid} fetched {fetched}, more than {STALE_SOURCE_DAYS} days "
                                    "before verified_at: refetch to confirm current status and later amendments")

    if data.get("status") == "published":
        bundle = bundles.get(dossier_id)
        if bundle is None:
            warnings.append("ja/zh-Hans translations pending (English-only until added)")
        else:
            for lang in el.LOCALES:
                problems = el.translation_problems(data, bundle, lang, english_sha=english_sha)
                tr = (bundle.get("translations") or {}).get(lang)
                for problem in problems:
                    if problem in el.STALE_MARKERS:
                        warnings.append(f"{lang}: {problem} -> language page withheld until synced")
                    elif "states a date or number" in problem and isinstance(tr, Mapping):
                        for fact, path, snippet in ef.unsupported_fact_locations(
                                el.english_texts(data), el.local_texts_with_paths(tr)):
                            errors.append(f"{lang}: {path} states {fact} not in the English: …{snippet}…")
                    else:
                        errors.append(f"{lang}: {problem}")
    return {"dossier_id": dossier_id, "errors": errors, "warnings": warnings}


def render(results: Sequence[Mapping[str, Any]]) -> str:
    lines = []
    for result in results:
        state = "OK" if not result["errors"] else f"{len(result['errors'])} ERROR(S)"
        lines.append(f"## {result['dossier_id']}: {state}")
        for message in result["errors"]:
            hint = _fix(message)
            lines.append(f"- error: {message}" + (f"  → fix: {hint}" if hint else ""))
        for message in result["warnings"]:
            hint = _fix(message)
            lines.append(f"- warning: {message}" + (f"  → {hint}" if hint else ""))
    return "\n".join(lines) + "\n"


def sync_stamps(dossier_id: str, langs: Sequence[str], *, explainers: Mapping[str, Mapping[str, Any]],
                index: ex.OfficialIndex, today: date, locale_dir: Path = el.TRANSLATIONS_DIR) -> list[str]:
    """Stamp the named locales to the current English, refusing unless content checks pass."""
    data = explainers.get(dossier_id)
    if data is None:
        raise SystemExit(f"unknown explainer {dossier_id}")
    current = index.fingerprint(data.get("related_item_ids") or [])
    if data.get("source_fingerprint") != current or not data.get("verified_at"):
        raise SystemExit("English is not stamped to the current official records: run "
                         f"python scripts/explainers.py stamp {dossier_id} --verified-on <date> first")
    path = locale_dir / f"{dossier_id}.json"
    if not path.is_file():
        raise SystemExit(f"{path} does not exist")
    bundle = ex.load_json(path)
    sha = el.canonical_file_sha(dossier_id)
    done = []
    for lang in langs:
        tr = (bundle.get("translations") or {}).get(lang)
        if not isinstance(tr, dict):
            raise SystemExit(f"{lang}: no translation in {path}")
        trial = json.loads(json.dumps(bundle))
        trial["translations"][lang].update({
            "english_verified_at": data["verified_at"], "english_source_fingerprint": current,
            "english_file_sha": sha,
        })
        problems = el.translation_problems(data, trial, lang, english_sha=sha)
        if problems:
            raise SystemExit(f"{lang}: refusing to stamp a translation that fails checks:\n  " + "\n  ".join(problems))
        tr.update({"english_verified_at": data["verified_at"], "english_source_fingerprint": current,
                   "english_file_sha": sha})
        done.append(lang)
    ex.write_if_changed(path, ex.dump(bundle))
    return done


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--today", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check")
    c.add_argument("dossiers", nargs="*")
    c.add_argument("--changed-from", default=None, help="git base ref; checks dossiers changed since it")
    c.add_argument("--json", type=Path, default=None, help="also write the results as JSON")
    s = sub.add_parser("sync-stamps")
    s.add_argument("dossier_id")
    s.add_argument("--lang", action="append", choices=el.LOCALES, required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    today = date.fromisoformat(args.today) if args.today else datetime.now(JST).date()
    index = ex.OfficialIndex.build(ex.load_json(ex.RAW_PATH))
    explainers = ex.load_explainers()
    if args.command == "sync-stamps":
        done = sync_stamps(args.dossier_id, args.lang, explainers=explainers, index=index, today=today)
        print(f"stamped {args.dossier_id}: {', '.join(done)} -> {el.canonical_file_sha(args.dossier_id)}")
        return 0
    ids = list(args.dossiers)
    if args.changed_from:
        ids = sorted(set(ids) | set(changed_dossiers(args.changed_from)))
    if not ids:
        print("No changed dossiers to check.")
        return 0
    results = [check_dossier(i, explainers=explainers, index=index, today=today) for i in ids]
    text = render(results)
    print(text, end="")
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("### Dossier preflight\n" + text)
    return 1 if any(r["errors"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
