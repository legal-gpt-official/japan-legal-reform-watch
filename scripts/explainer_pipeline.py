#!/usr/bin/env python3
"""Explainer production ledger: computed per-dossier stage, daily counts, next work.

    python scripts/explainer_pipeline.py reconcile --snapshot snapshot.json [--live-report r.json --live-run-id N]
    python scripts/explainer_pipeline.py next [--top 8]
    python scripts/explainer_pipeline.py report

``reconcile`` (run by .github/workflows/explainer-pipeline.yml at the start of
every writing slot and after every CI / Publish / LIVE run) combines

* GitHub facts collected into ``snapshot.json`` (pull requests and their changed
  dossier files, ``Pull request checks`` runs per head SHA, Publish runs, LIVE
  verification runs), and
* repository files (English explainers, locale bundles, case files, the queue,
  the research packets, the official-record index),

into ``data/explainer_pipeline.json``. Every forward stage is COMPUTED from
evidence -- file content that passes the same checks CI applies, a CI run on the
exact head SHA, a merge, a successful Publish run after the merge (Publish fails
unless Pages deployed its generated docs SHA), a successful LIVE run after that
-- so a free-text "STATE: JA_ZH_READY" in a PR body can never move a dossier on.

The ledger keeps only what evidence cannot recompute: when each stage was first
reached, holds and the fingerprint they were set against, and recovery dispatch
counts. Re-running on the same inputs writes byte-identical output (idempotent);
an interrupted slot resumes from ``last_success_stage``.

``next`` prints the slot plan: unfinished dossiers, most advanced first, then
broken published explainers, then new queue candidates that nobody has claimed
and that are not on hold -- with the exact next operation for each.

Never merges, labels, or edits explainers. The only side effect it can request
is re-dispatching the existing Publish / LIVE workflows, at most
``MAX_DISPATCHES_PER_DOSSIER`` times per dossier per stage.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import explainer_locales as el  # noqa: E402
import explainer_state as es  # noqa: E402
import explainers as ex  # noqa: E402
from alert_common import JST  # noqa: E402

LEDGER_PATH = ex.REPO_ROOT / "data" / "explainer_pipeline.json"
SCHEMA_VERSION = 1
DAILY_TARGET = 5
DAILY_MAX = 10
WINDOW_DAYS = 14
KEEP_DONE_DAYS = 14
MAX_DISPATCHES_PER_DOSSIER = 3
PUBLISH_GRACE_MINUTES = 20
LIVE_GRACE_MINUTES = 15
LIVE_WINDOW_DAYS = 2  # verify_live_explainers.py default --days
DOSSIER_DIRS = ("data/explainers/", "data/explainer_locales/", "data/explainer_state/")
ALLOWED_AUTOMERGE_DIRS = DOSSIER_DIRS
SLOTS_JST = ("11:00", "12:00", "13:00", "14:00", "15:00", "21:00")
STAGES = es.STAGES
RANK = es.STAGE_RANK


# ------------------------------------------------------------------ small helpers


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if value else None


def jst_day(value: Any) -> str | None:
    parsed = _ts(value)
    return parsed.astimezone(JST).date().isoformat() if parsed else None


def dossier_of(path: str) -> str | None:
    for prefix in DOSSIER_DIRS:
        if path.startswith(prefix) and path.endswith(".json") and "/" not in path[len(prefix):]:
            return path[len(prefix):-5]
    return None


def _json(text: Any) -> dict | None:
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# ------------------------------------------------------------------ content stage


def content_stage(
    dossier_id: str,
    *,
    en_text: str | None,
    bundle: Mapping[str, Any] | None,
    state: Mapping[str, Any] | None,
    index: ex.OfficialIndex,
    main_explainers: Mapping[str, Mapping[str, Any]],
    today: date,
    seed: str | None = None,
) -> tuple[str, list[str]]:
    """Highest pre-CI stage the dossier's files reach, with the problems blocking the next one.

    Uses the very checks CI applies (explainers.validate with the repository's
    official records, the case-file gate, translation completeness and stamps
    against this exact English content), never a writer's own claim.
    """
    blockers: list[str] = []
    if state is not None:
        state_errors, _ = es.validate_state(dossier_id, state)
        gaps = es.critical_gaps(state)
        if state_errors:
            blockers += [f"case file: {m}" for m in state_errors[:5]]
        if gaps:
            blockers.append("confirm critical facts: " + ", ".join(gaps))
        if state_errors or gaps:
            return "RESEARCH_REQUIRED", blockers
    elif es.requires_case_file(dossier_id):
        return "RESEARCH_REQUIRED", [
            f"no case file: python scripts/explainer_state.py init {dossier_id} --seed {seed or '<raw-id>'}, "
            "then confirm the critical facts from official sources"
        ]
    en = _json(en_text)
    if en is None:
        return "SOURCES_VERIFIED", ["write the English explainer data/explainers/%s.json" % dossier_id]
    explainers = dict(main_explainers)
    explainers[dossier_id] = en
    report = ex.validate_all(explainers, index, today)[dossier_id]
    gate = es.publication_gate(dossier_id, {**en, "status": "published"}, state)
    if report.errors or (state is not None and gate):
        return "SOURCES_VERIFIED", (report.errors + gate)[:6]
    if en.get("status") != "published":
        blockers.append("English status is not 'published'")
    sha = el.blob_sha(en_text.encode("utf-8"))
    if bundle is None:
        return "EN_READY", blockers + ["add data/explainer_locales/%s.json (ja and zh-Hans)" % dossier_id]
    content_problems, stale = [], []
    for lang in el.LOCALES:
        for problem in el.translation_problems(en, bundle, lang, english_sha=sha):
            (stale if problem in el.STALE_MARKERS else content_problems).append(f"{lang}: {problem}")
    if content_problems:
        return "EN_READY", blockers + content_problems[:6]
    if stale or "official_records_changed" in report.warnings or blockers:
        return "JA_ZH_READY", blockers + sorted(set(stale))[:6] + (
            ["English stamp is behind the official records (explainers.py stamp)"]
            if "official_records_changed" in report.warnings else [])
    return "STAMPED_AND_SYNCED", []


# ------------------------------------------------------------------ GitHub facts


def _runs_for(pr: Mapping[str, Any], ci_runs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    number = pr.get("number")
    return [r for r in ci_runs if number in (r.get("pull_requests") or []) or r.get("head_sha") == pr.get("head_sha")]


def ci_facts(pr: Mapping[str, Any], ci_runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    runs = _runs_for(pr, ci_runs)
    head = [r for r in runs if r.get("head_sha") == pr.get("head_sha")]
    head.sort(key=lambda r: (str(r.get("created_at")), int(r.get("id") or 0)), reverse=True)
    latest = head[0] if head else None
    failures = sorted({int(r["id"]) for r in runs if r.get("conclusion") == "failure" and r.get("id")})
    out: dict[str, Any] = {"failed_runs": len(failures)}
    if latest:
        started, ended = _ts(latest.get("run_started_at") or latest.get("created_at")), _ts(latest.get("updated_at"))
        out.update({
            "run_id": latest.get("id"),
            "head_sha": latest.get("head_sha"),
            "status": latest.get("status"),
            "conclusion": latest.get("conclusion"),
            "tests_ok": bool(latest.get("tests_ok")),
            "failed_steps": list(latest.get("failed_steps") or []),
            "completed_at": latest.get("updated_at") if latest.get("status") == "completed" else None,
            "duration_s": int((ended - started).total_seconds()) if started and ended and latest.get("status") == "completed" else None,
        })
    return out


def first_success_after(runs: Sequence[Mapping[str, Any]], after: datetime | None) -> Mapping[str, Any] | None:
    if after is None:
        return None
    ok = [r for r in runs if r.get("status") == "completed" and r.get("conclusion") == "success"
          and (_ts(r.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= after]
    ok.sort(key=lambda r: (str(r.get("created_at")), int(r.get("id") or 0)))
    return ok[0] if ok else None


def live_covers(run: Mapping[str, Any], dossier_id: str, verified_at: str | None) -> bool:
    """A successful LIVE run verified this dossier.

    Exact when the run's report lists dossiers. Otherwise the verifier's own
    rule: it checks every published dossier with verified_at within
    LIVE_WINDOW_DAYS (JST) of the run and fails if any one of them fails.
    """
    listed = run.get("dossiers")
    if isinstance(listed, list):
        return dossier_id in listed
    day = jst_day(run.get("created_at"))
    if not day or not verified_at:
        return False
    # Only the version stamped by then can have been checked: a later re-stamp gets no
    # credit from an earlier run (its content may differ), so history is never back-dated.
    return (date.fromisoformat(day) - timedelta(days=LIVE_WINDOW_DAYS)).isoformat() <= verified_at <= day


def automerge_eligible_files(files: Iterable[Mapping[str, Any]]) -> bool:
    names = [str(f.get("filename")) for f in files]
    return any(n.startswith("data/explainers/") for n in names) and all(
        n.endswith(".json") and any(n.startswith(d) for d in ALLOWED_AUTOMERGE_DIRS) for n in names)


# ------------------------------------------------------------------ reconcile


def _cycles(
    pulls: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, tuple[Mapping[str, Any], dict[str, bool]]], dict[str, Mapping[str, Any]]]:
    """(dossier id -> (current PR, file flags), dossier id -> merged PR that first added its English).

    The current PR drives the stage: an open PR wins over merged ones, newest wins otherwise.
    The introducing PR drives the NEW-dossier counts, so a later update PR to the same
    dossier neither turns it into an "update" nor moves its first-publication date.
    """
    best: dict[str, tuple[Mapping[str, Any], dict[str, bool]]] = {}
    introduced: dict[str, Mapping[str, Any]] = {}

    def key(pr: Mapping[str, Any]) -> tuple:
        return (pr.get("state") == "open", str(pr.get("merged_at") or pr.get("created_at") or ""), int(pr.get("number") or 0))

    for pr in pulls:
        if pr.get("state") == "closed" and not pr.get("merged_at"):
            continue  # abandoned; a hold it carried is remembered by the ledger
        flags: dict[str, dict[str, bool]] = {}
        for f in pr.get("files") or []:
            name = str(f.get("filename"))
            dossier = dossier_of(name)
            if not dossier:
                continue
            fl = flags.setdefault(dossier, {"en_added": False, "en_touched": False, "state_added": False})
            if name.startswith("data/explainers/"):
                fl["en_touched"] = True
                fl["en_added"] = fl["en_added"] or f.get("status") == "added"
            if name.startswith("data/explainer_state/") and f.get("status") == "added":
                fl["state_added"] = True
        for dossier, fl in flags.items():
            if dossier not in best or key(pr) > key(best[dossier][0]):
                best[dossier] = (pr, fl)
            if fl["en_added"] and pr.get("merged_at"):
                old = introduced.get(dossier)
                if old is None or str(pr["merged_at"]) < str(old.get("merged_at")):
                    introduced[dossier] = pr
    return best, introduced


def first_live_after(live_runs: Sequence[Mapping[str, Any]], done: datetime | None, dossier_id: str,
                     verified_at: str | None) -> Mapping[str, Any] | None:
    if done is None:
        return None
    for run in sorted(live_runs, key=lambda r: (str(r.get("created_at")), int(r.get("id") or 0))):
        if run.get("status") == "completed" and run.get("conclusion") == "success" \
                and (_ts(run.get("created_at")) or done) >= done and live_covers(run, dossier_id, verified_at):
            return run
    return None


def reconcile(
    snapshot: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    *,
    index: ex.OfficialIndex,
    main_explainers: Mapping[str, Mapping[str, Any]],
    main_bundles: Mapping[str, Mapping[str, Any]],
    main_states: Mapping[str, Mapping[str, Any]],
    now: datetime,
    run_id: str | None = None,
    live_report: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (ledger, recovery actions). Deterministic for the same inputs and ``now``."""
    today = now.astimezone(JST).date()
    prev_dossiers: Mapping[str, Any] = (previous or {}).get("dossiers") or {}
    ci_runs = snapshot.get("ci_runs") or []
    publish_runs = snapshot.get("publish_runs") or []
    live_runs = [dict(r) for r in snapshot.get("live_runs") or []]
    if live_report and live_report.get("run_id") is not None:
        for run in live_runs:
            if str(run.get("id")) == str(live_report["run_id"]) and isinstance(live_report.get("dossiers_verified"), list):
                run["dossiers"] = list(live_report["dossiers_verified"])
    publish_busy = any(r.get("status") in ("queued", "in_progress", "waiting", "requested") for r in publish_runs)
    live_busy = any(r.get("status") in ("queued", "in_progress", "waiting", "requested") for r in live_runs)

    cycles, introduced = _cycles(snapshot.get("pulls") or [])
    dossiers: dict[str, Any] = {}
    actions: list[dict[str, Any]] = []
    ids = set(cycles) | set(prev_dossiers) | {d for d, s in main_states.items() if s.get("hold")}
    for dossier_id in sorted(ids):
        prev = prev_dossiers.get(dossier_id) or {}
        pr, flags = cycles.get(dossier_id, (None, None))
        if dossier_id in introduced or prev.get("introduced"):
            kind = "new"
        elif flags is None:
            kind = prev.get("kind") or "new"
        elif flags["en_added"]:
            kind = "new"
        elif flags["en_touched"]:
            kind = "update"
        else:  # case file / translations only: new unless the English already exists on main
            kind = (prev.get("kind") or "update") if dossier_id in main_explainers else "new"
        entry: dict[str, Any] = {"dossier_id": dossier_id, "kind": kind}
        contents = (pr or {}).get("contents") or {}
        is_open = bool(pr) and pr.get("state") == "open"
        if is_open:
            en_text = contents.get(f"data/explainers/{dossier_id}.json")
            bundle = _json(contents.get(f"data/explainer_locales/{dossier_id}.json"))
            state = _json(contents.get(f"data/explainer_state/{dossier_id}.json"))
            # Files the PR does not touch come from main.
            if en_text is None and dossier_id in main_explainers:
                path = ex.EXPLAINERS_DIR / f"{dossier_id}.json"
                en_text = path.read_text(encoding="utf-8") if path.is_file() else None
            bundle = bundle if bundle is not None else main_bundles.get(dossier_id)
            state = state if state is not None else main_states.get(dossier_id)
        else:
            en_text, bundle = None, None
            state = main_states.get(dossier_id)
        en = _json(en_text) if en_text else main_explainers.get(dossier_id)
        related = [i for i in (en or {}).get("related_item_ids") or [] if isinstance(i, str)]
        seed = (state or {}).get("seed_item_id") or prev.get("seed_item_id") or (related[0] if related else None)
        closure = index.closure(related or ([seed] if seed else []))
        entry.update({
            "queue_id": (state or {}).get("queue_id") or prev.get("queue_id"),
            "seed_item_id": seed,
            "related_item_ids": closure[:40],
            "source_fingerprint": index.fingerprint(closure) if closure else None,
        })
        if not pr:  # out of the snapshot window: keep the last known GitHub facts
            for k in ("pr", "branch", "head_sha", "pr_state", "pr_draft", "labels", "pr_created_at",
                      "automerge_eligible_files", "ci", "merged_at", "publish", "live"):
                if k in prev:
                    entry[k] = prev[k]
        history: dict[str, str] = dict(prev.get("stage_history") or {})
        blockers: list[str] = []
        stage = prev.get("stage") or "RESEARCH_REQUIRED"
        if pr:
            entry.update({
                "pr": pr.get("number"), "branch": pr.get("head_ref"), "head_sha": pr.get("head_sha"),
                "pr_state": "merged" if pr.get("merged_at") else pr.get("state"),
                "pr_draft": bool(pr.get("draft")), "labels": sorted(pr.get("labels") or []),
                "pr_created_at": pr.get("created_at"),
                "automerge_eligible_files": automerge_eligible_files(pr.get("files") or []),
            })
            history.setdefault("DRAFTED", pr.get("created_at"))
            ci = ci_facts(pr, ci_runs)
            entry["ci"] = ci
        if is_open:
            stage, blockers = content_stage(
                dossier_id, en_text=en_text, bundle=bundle, state=state, index=index,
                main_explainers={k: v for k, v in main_explainers.items() if k != dossier_id}, today=today,
                seed=seed,
            )
            ci = entry["ci"]
            if stage == "STAMPED_AND_SYNCED" and ci.get("run_id"):
                if ci.get("status") != "completed":
                    stage = "CI_RUNNING"
                elif ci.get("conclusion") == "success" and ci.get("tests_ok"):
                    stage = "CI_PASSED"
                    history.setdefault("CI_PASSED", ci.get("completed_at"))
        elif pr and pr.get("merged_at"):
            merged = _ts(pr["merged_at"])
            stage = "MERGED"
            history.setdefault("CI_PASSED", (entry.get("ci") or {}).get("completed_at") or pr["merged_at"])
            history.setdefault("MERGED", pr["merged_at"])
            entry["merged_at"] = pr["merged_at"]
            publish = first_success_after(publish_runs, merged)
            if publish:
                stage = "PAGES_DEPLOYED"
                entry["publish"] = {"run_id": publish.get("id"), "completed_at": publish.get("updated_at")}
                history.setdefault("PAGES_DEPLOYED", publish.get("updated_at"))
                verified_at = (main_explainers.get(dossier_id) or {}).get("verified_at")
                live = first_live_after(live_runs, _ts(publish.get("updated_at")), dossier_id, verified_at)
                # A per-dossier LIVE report is read only by the run its LIVE workflow triggers;
                # keep that evidence while it postdates this cycle's Publish.
                prev_live = prev.get("live") or {}
                if live is None and _ts(prev_live.get("completed_at")) and \
                        _ts(prev_live["completed_at"]) >= _ts(publish.get("updated_at")):
                    live = {"id": prev_live.get("run_id"), "updated_at": prev_live["completed_at"]}
                if live:
                    stage = "LIVE_VERIFIED"
                    entry["live"] = {"run_id": live.get("id"), "completed_at": live.get("updated_at")}
                    history.setdefault("LIVE_VERIFIED", live.get("updated_at"))
        # Content stages of open work get a first-observed time; merged history is never back-filled.
        if is_open:
            top = min(RANK.get(stage, 0), RANK["STAMPED_AND_SYNCED"])
            for s in STAGES[: top + 1]:
                history.setdefault(s, history.get("DRAFTED") if s == "RESEARCH_REQUIRED" and history.get("DRAFTED") else _iso(now))
        intro_pr = introduced.get(dossier_id)
        intro: dict[str, Any] = dict(prev.get("introduced") or {})
        if intro_pr and intro.get("pr") not in (None, intro_pr.get("number")):
            intro = {}
        if intro_pr:
            merged = _ts(intro_pr.get("merged_at"))
            ici = ci_facts(intro_pr, ci_runs)
            pub = first_success_after(publish_runs, merged)
            lv = first_live_after(live_runs, _ts((pub or {}).get("updated_at")), dossier_id,
                                  (main_explainers.get(dossier_id) or {}).get("verified_at")) if pub else None
            facts = {
                "pr": intro_pr.get("number"), "drafted_at": intro_pr.get("created_at"),
                "ci_passed_at": ici.get("completed_at") if ici.get("conclusion") == "success" else None,
                "merged_at": intro_pr.get("merged_at"),
                "pages_at": (pub or {}).get("updated_at"), "live_at": (lv or {}).get("updated_at"),
            }
            for k, v in facts.items():
                if v is not None and intro.get(k) is None:
                    intro[k] = v
        if intro:
            entry["introduced"] = {k: intro[k] for k in ("pr", "drafted_at", "ci_passed_at", "merged_at", "pages_at", "live_at")
                                   if intro.get(k) is not None}
        entry["stage"] = stage
        best = max([RANK.get(prev.get("last_success_stage"), -1), RANK.get(stage, 0)])
        entry["last_success_stage"] = STAGES[best] if best >= 0 else stage
        entry["stage_history"] = {k: history[k] for k in sorted(history, key=lambda k: (RANK.get(k, -1), k)) if history[k]}

        # Holds: the case file asks, the ledger remembers the fingerprint it was set against.
        hold = (state or {}).get("hold") if state else prev.get("hold")
        if isinstance(hold, Mapping) and hold.get("state") in es.HOLD_STATES:
            prev_hold = prev.get("hold") or {}
            same = prev_hold.get("state") == hold.get("state") and prev_hold.get("reason") == hold.get("reason")
            at_hold = prev_hold.get("fingerprint_at_hold") if same else entry["source_fingerprint"]
            held = {k: hold.get(k) for k in ("state", "reason", "unblock_condition", "retry_after", "since") if hold.get(k)}
            held["fingerprint_at_hold"] = at_hold
            held["active"] = es.hold_active(hold, today=today, fingerprint_now=entry["source_fingerprint"],
                                            fingerprint_at_hold=at_hold)
            entry["hold"] = held
        if state:
            entry["sources"] = es.source_summary(state)
            entry["unconfirmed"] = es.critical_gaps(state)
            if state.get("unresolved"):
                entry["unresolved"] = list(state["unresolved"])[:10]
        entry["blockers"] = blockers[:6]
        dispatches = dict(prev.get("dispatches") or {})
        entry["next_action"] = next_action(entry, state)

        # Recovery: re-run the failed publication step, bounded.
        if stage == "MERGED" and not publish_busy:
            merged = _ts(entry.get("merged_at"))
            if merged and now - merged > timedelta(minutes=PUBLISH_GRACE_MINUTES) \
                    and dispatches.get("publish", 0) < MAX_DISPATCHES_PER_DOSSIER:
                dispatches["publish"] = dispatches.get("publish", 0) + 1
                actions.append({"workflow": "publish-explainer-pages.yml", "dossier_id": dossier_id,
                                "reason": f"merged {entry['merged_at']} without a successful Publish run"})
        if stage == "PAGES_DEPLOYED" and not live_busy:
            done = _ts((entry.get("publish") or {}).get("completed_at"))
            if done and now - done > timedelta(minutes=LIVE_GRACE_MINUTES) \
                    and dispatches.get("live", 0) < MAX_DISPATCHES_PER_DOSSIER:
                dispatches["live"] = dispatches.get("live", 0) + 1
                actions.append({"workflow": "verify-live-explainers.yml", "dossier_id": dossier_id,
                                "reason": f"Pages deployed {entry['publish']['completed_at']} without LIVE verification"})
        if dispatches:
            entry["dispatches"] = dispatches

        changed_keys = ("stage", "hold", "pr", "head_sha", "ci", "next_action", "blockers")
        if prev and all(prev.get(k) == entry.get(k) for k in changed_keys):
            entry["updated_at"], entry["run_id"] = prev.get("updated_at"), prev.get("run_id")
        else:
            entry["updated_at"], entry["run_id"] = _iso(now), run_id
        # Forget finished work after the metrics window; keep holds until released.
        finished = _ts(history.get("LIVE_VERIFIED")) or (_ts(history.get("MERGED")) if not pr else None)
        if stage == "LIVE_VERIFIED" and finished and now - finished > timedelta(days=KEEP_DONE_DAYS):
            continue
        if not pr and not entry.get("hold") and stage not in ("MERGED", "PAGES_DEPLOYED", "LIVE_VERIFIED"):
            continue  # a cycle whose PR was closed unmerged and that is not on hold
        dossiers[dossier_id] = entry

    # Collapse duplicate dispatches: one workflow run serves every dossier.
    merged_actions: dict[str, dict[str, Any]] = {}
    for action in actions:
        slot = merged_actions.setdefault(action["workflow"], {"workflow": action["workflow"], "dossiers": [], "reasons": []})
        slot["dossiers"].append(action["dossier_id"])
        slot["reasons"].append(action["reason"])
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "note": ("Generated by scripts/explainer_pipeline.py reconcile. Stages are computed from repository files "
                 "and GitHub runs; edit data/explainer_state/<id>.json (hold, next_action), never this file."),
        "daily_target": DAILY_TARGET,
        "dossiers": dossiers,
        "daily": daily_counts(dossiers, today),
        "research_notes": research_notes(snapshot.get("issues") or []),
    }
    return ledger, list(merged_actions.values())


def research_notes(issues: Sequence[Mapping[str, Any]]) -> dict[str, list[int]]:
    """raw id -> open issue numbers that hold research on it (handoffs written before case files)."""
    notes: dict[str, set[int]] = {}
    for issue in issues:
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        for raw in issue.get("raw_ids") or []:
            if isinstance(raw, str) and raw.startswith("raw-"):
                notes.setdefault(raw, set()).add(number)
    return {raw: sorted(nums) for raw, nums in sorted(notes.items())}


def next_action(entry: Mapping[str, Any], state: Mapping[str, Any] | None) -> str:
    dossier_id = entry["dossier_id"]
    hold = entry.get("hold") or {}
    if hold.get("active"):
        until = {"official_records_change": "its official records change", "date": f"{hold.get('retry_after')}",
                 "manual": "someone releases it"}.get(hold.get("unblock_condition"), "released")
        return f"ON HOLD ({hold.get('state')}): {hold.get('reason')} Skip until {until}; work on another candidate."
    stage = entry.get("stage")
    blockers = entry.get("blockers") or []
    first = f" First: {blockers[0]}" if blockers else ""
    own = (state or {}).get("next_action")
    ci = entry.get("ci") or {}
    if stage == "RESEARCH_REQUIRED":
        return ("Confirm the critical legal facts from official sources "
                f"(python scripts/explainer_sources.py fetch {dossier_id}).{first}" + (f" Writer note: {own}" if own else ""))
    if stage == "SOURCES_VERIFIED":
        return f"Write / fix the English explainer, then run explainer_preflight.py check {dossier_id}.{first}"
    if stage == "EN_READY":
        return f"Complete the ja and zh-Hans translations (every section, same counts).{first}"
    if stage == "JA_ZH_READY":
        return (f"Stamp English (explainers.py stamp {dossier_id} --verified-on <date>), check each language against it, "
                f"then explainer_preflight.py sync-stamps {dossier_id} --lang ja --lang zh-Hans.{first}")
    if stage == "STAMPED_AND_SYNCED":
        if ci.get("conclusion") == "failure":
            steps = ", ".join(ci.get("failed_steps") or []) or "unknown step"
            return f"Fix the failed Pull request checks run {ci.get('run_id')} ({steps}); run explainer_preflight.py check {dossier_id} before pushing."
        return "Push the branch / open the PR so Pull request checks run on the current head."
    if stage == "CI_RUNNING":
        return f"Wait for Pull request checks run {ci.get('run_id')} on {str(entry.get('head_sha'))[:12]}."
    if stage == "CI_PASSED":
        if not entry.get("automerge_eligible_files"):
            return "PR mixes non-dossier files; automerge will not take it. Split dossier files into their own PR."
        if entry.get("pr_draft"):
            return ("Finish the substantive legal and three-language review against the official sources; then mark the PR "
                    "ready and add legal-content-verified.")
        if "legal-content-verified" not in (entry.get("labels") or []):
            return "Add legal-content-verified only after the substantive legal review is complete."
        return "Automerge will merge on the next event or the hourly safety run (:10)."
    if stage == "MERGED":
        return "Publish pending: the pipeline re-dispatches Publish if it has not succeeded within 20 minutes."
    if stage == "PAGES_DEPLOYED":
        return "LIVE verification pending: the pipeline re-dispatches it if it has not succeeded within 15 minutes."
    return "Done (LIVE_VERIFIED)."


def daily_counts(dossiers: Mapping[str, Mapping[str, Any]], today: date) -> dict[str, Any]:
    """Unique NEW dossiers per JST day reaching each milestone; updates counted separately."""
    milestones = (("drafted", "DRAFTED"), ("ci_passed", "CI_PASSED"), ("merged", "MERGED"),
                  ("pages_deployed", "PAGES_DEPLOYED"), ("live_verified", "LIVE_VERIFIED"))
    days = [(today - timedelta(days=n)).isoformat() for n in range(WINDOW_DAYS - 1, -1, -1)]
    out: dict[str, Any] = {d: {"new": {m: 0 for m, _ in milestones}, "updates_merged": 0} for d in days}
    intro_keys = {"drafted": "drafted_at", "ci_passed": "ci_passed_at", "merged": "merged_at",
                  "pages_deployed": "pages_at", "live_verified": "live_at"}
    for entry in dossiers.values():
        intro = entry.get("introduced")
        history = entry.get("stage_history") or {}
        for label, stage in milestones:
            # A published dossier counts as new on the days ITS introducing PR reached each
            # milestone; an unmerged new dossier counts from its own stage history. Pages and
            # LIVE count on the MERGE day (the day's cohort reached publication), so verifying
            # older dossiers later never inflates today's count against the target.
            if intro and label in ("pages_deployed", "live_verified"):
                when = intro.get("merged_at") if intro.get(intro_keys[label]) else None
            else:
                when = intro.get(intro_keys[label]) if intro else (history.get(stage) if entry.get("kind") == "new" else None)
            day = jst_day(when)
            if day in out:
                out[day]["new"][label] += 1
        # The current cycle is an update when it is not the PR that introduced the dossier.
        if entry.get("pr_state") == "merged" and entry.get("merged_at") and (
                entry.get("kind") != "new" or (intro and intro.get("pr") != entry.get("pr"))):
            day = jst_day(entry["merged_at"])
            if day in out:
                out[day]["updates_merged"] += 1
    return out


# ------------------------------------------------------------------ slot plan


WORK_ORDER = ("CI_PASSED", "CI_RUNNING", "STAMPED_AND_SYNCED", "JA_ZH_READY", "EN_READY", "SOURCES_VERIFIED",
              "RESEARCH_REQUIRED")


def slot_plan(
    ledger: Mapping[str, Any],
    queue: Mapping[str, Any],
    *,
    index: ex.OfficialIndex,
    research_dir: Path,
    today: date,
    top: int = 8,
) -> list[dict[str, Any]]:
    """What the next writing slot should do, most advanced unfinished work first."""
    plan: list[dict[str, Any]] = []
    dossiers = ledger.get("dossiers") or {}
    claimed: set[str] = set()
    for entry in dossiers.values():
        if entry.get("stage") in ("MERGED", "PAGES_DEPLOYED", "LIVE_VERIFIED") and not (entry.get("hold") or {}).get("active"):
            continue
        claimed.update(entry.get("related_item_ids") or [])
    active = [e for e in dossiers.values()
              if e.get("stage") in WORK_ORDER and not (e.get("hold") or {}).get("active")]
    active.sort(key=lambda e: (WORK_ORDER.index(e["stage"]), str(e.get("pr_created_at") or ""), e["dossier_id"]))
    for e in active:
        plan.append({"tier": "resume", "dossier_id": e["dossier_id"], "stage": e["stage"], "pr": e.get("pr"),
                     "branch": e.get("branch"), "action": e.get("next_action")})
    for e in dossiers.values():
        if e.get("stage") in ("MERGED", "PAGES_DEPLOYED") and (e.get("dispatches") or {}) and \
                max((e.get("dispatches") or {}).values()) >= MAX_DISPATCHES_PER_DOSSIER:
            plan.append({"tier": "investigate", "dossier_id": e["dossier_id"], "stage": e["stage"],
                         "action": "Automatic Publish/LIVE recovery exhausted: read the failed run logs and fix the cause."})
    for q in queue.get("entries") or []:
        if q.get("kind") == "update" and "validation_errors" in (q.get("reasons") or []):
            if q.get("explainer_id") in dossiers and dossiers[q["explainer_id"]].get("stage") in WORK_ORDER:
                continue
            plan.append({"tier": "repair", "dossier_id": q.get("explainer_id"), "stage": "PUBLISHED_INVALID",
                         "action": "A published explainer fails validation and is withheld: fix " + "; ".join(q.get("errors") or [])[:300]})
    for q in queue.get("entries") or []:
        if q.get("kind") != "new":
            continue
        seed = q.get("suggested_seed_item_id") or (q.get("item_ids") or [None])[0]
        if not seed:
            continue
        closure = set(index.closure([seed])) or {seed}
        if closure & claimed:
            continue
        packet = research_dir / f"{seed}.json"
        notes = sorted({n for raw in closure for n in (ledger.get("research_notes") or {}).get(raw, [])})
        prior = (f"Prior research exists in issue(s) {', '.join('#' + str(n) for n in notes)}: read it first, "
                 "carry its verified sources into the case file, and do not repeat what it already established. ") if notes else ""
        plan.append({
            "tier": "new", "queue_id": q.get("queue_id"), "seed_item_id": seed, "priority": q.get("priority"),
            "title_ja": q.get("title_ja"), "branch": f"explainer/{seed}",
            "research_packet": f"data/explainer_research/{seed}.json" if packet.is_file() else None,
            "prior_research_issues": notes,
            "action": prior + (f"Claim by pushing branch explainer/{seed} with a case file "
                       f"(python scripts/explainer_state.py init <dossier-id> --seed {seed} --queue-id {q.get('queue_id')}) "
                       "and opening a draft PR at once; then fetch and confirm the sources."),
        })
        claimed |= closure
    for q in queue.get("entries") or []:
        reasons = q.get("reasons") or []
        if q.get("kind") != "update" or "validation_errors" in reasons:
            continue
        if not ("official_records_changed" in reasons or any(r.startswith("stage_overtaken") for r in reasons)):
            continue
        if q.get("explainer_id") in dossiers and dossiers[q["explainer_id"]].get("stage") in WORK_ORDER:
            continue
        plan.append({"tier": "update", "dossier_id": q.get("explainer_id"), "stage": "PUBLISHED",
                     "action": "Official records changed: read new_related_item_ids and the official sources, fix what "
                               "changed, stamp. Counts as an update, not a new dossier: " + ", ".join(reasons)})
    return plan[:top] if top else plan


# ------------------------------------------------------------------ report


def report_markdown(ledger: Mapping[str, Any], plan: Sequence[Mapping[str, Any]], actions: Sequence[Mapping[str, Any]],
                    ci_runs: Sequence[Mapping[str, Any]] = (), now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(JST).date().isoformat()
    daily = ledger.get("daily") or {}
    dossiers = ledger.get("dossiers") or {}
    lines = [f"## Explainer production status ({today} JST)", ""]
    t = (daily.get(today) or {}).get("new") or {}
    live, merged = t.get("live_verified", 0), t.get("merged", 0)
    lines.append(f"**Today (new dossiers):** drafted {t.get('drafted', 0)} / CI passed {t.get('ci_passed', 0)} / "
                 f"merged {merged} / Pages deployed {t.get('pages_deployed', 0)} / LIVE verified {live} "
                 f"— target {DAILY_TARGET} (max {DAILY_MAX}); shortfall {max(0, DAILY_TARGET - live)}.")
    lines += ["", "| JST day | drafted | CI passed | merged | Pages | LIVE | updates merged |", "|---|---|---|---|---|---|---|"]
    for day in sorted(daily)[-7:]:
        n = daily[day]["new"]
        lines.append(f"| {day} | {n['drafted']} | {n['ci_passed']} | {n['merged']} | {n['pages_deployed']} | "
                     f"{n['live_verified']} | {daily[day]['updates_merged']} |")
    backlog = Counter(e.get("stage") for e in dossiers.values() if not (e.get("hold") or {}).get("active")
                      and e.get("stage") != "LIVE_VERIFIED")
    held = [e for e in dossiers.values() if (e.get("hold") or {}).get("active")]
    lines += ["", "**Backlog by stage:** " + (", ".join(f"{s} {backlog[s]}" for s in STAGES if backlog.get(s)) or "none")
              + f"; on hold {len(held)}."]
    # Durations from evidence.
    dwell = []
    to_live = []
    for e in dossiers.values():
        h = e.get("stage_history") or {}
        a, m, lv = _ts(h.get("DRAFTED")), _ts(h.get("MERGED")), _ts(h.get("LIVE_VERIFIED"))
        if a and m:
            dwell.append((m - a).total_seconds() / 3600)
        if m and lv:
            to_live.append((lv - m).total_seconds() / 60)
    ci_d = sorted(int((_ts(r.get("updated_at")) - _ts(r.get("run_started_at") or r.get("created_at"))).total_seconds())
                  for r in ci_runs if r.get("status") == "completed" and _ts(r.get("updated_at"))
                  and _ts(r.get("run_started_at") or r.get("created_at")))
    # Only runs of dossier PRs carry failed_steps (the collector reads their jobs); ops PRs are not counted.
    ci_fail = Counter(step for r in ci_runs if r.get("conclusion") == "failure" and "failed_steps" in r
                      for step in (r.get("failed_steps") or ["unknown"]))

    def med(values):
        values = sorted(values)
        return values[len(values) // 2] if values else None

    lines.append(f"**Durations (median):** PR open→merge {med(dwell):.1f} h; merge→LIVE {med(to_live):.0f} min; CI {med(ci_d)} s."
                 if dwell and to_live and ci_d else "**Durations:** not enough data in the window yet.")
    if ci_fail:
        lines.append("**CI failures by step (14 days):** " + ", ".join(f"{k} {v}" for k, v in ci_fail.most_common()))
    lines += ["", "### Unfinished dossiers", "", "| dossier | stage | last success | PR | CI fails | next action |", "|---|---|---|---|---|---|"]
    for e in sorted(dossiers.values(), key=lambda e: (-RANK.get(e.get("stage"), 0), e["dossier_id"])):
        if e.get("stage") == "LIVE_VERIFIED":
            continue
        pr = f"#{e['pr']}" if e.get("pr") else ""
        lines.append(f"| {e['dossier_id']} | {e.get('stage')}{' (HOLD)' if (e.get('hold') or {}).get('active') else ''} | "
                     f"{e.get('last_success_stage')} | {pr} | {(e.get('ci') or {}).get('failed_runs', 0)} | "
                     f"{str(e.get('next_action')).replace('|', '/')[:220]} |")
    lines += ["", "### Next slot plan", ""]
    for n, step in enumerate(plan, start=1):
        target = step.get("dossier_id") or step.get("queue_id")
        lines.append(f"{n}. [{step['tier']}] {target} — {step['action'][:300]}")
    if actions:
        lines += ["", "### Recovery dispatched", ""]
        lines += [f"- {a['workflow']} for {', '.join(a['dossiers'])}: {'; '.join(a['reasons'])[:300]}" for a in actions]
    if live < DAILY_TARGET:
        reasons = []
        if held:
            reasons.append(f"{len(held)} on hold ({', '.join(sorted(e['dossier_id'] for e in held))[:200]})")
        stuck = [e for e in dossiers.values() if e.get("stage") in ("RESEARCH_REQUIRED", "SOURCES_VERIFIED")]
        if stuck:
            reasons.append(f"{len(stuck)} still in research/English drafting")
        if not any(p["tier"] == "new" for p in plan):
            reasons.append("no unclaimed new queue candidates")
        lines += ["", "**Why the target is not met yet:** " + ("; ".join(reasons) or "not enough dossiers started today") + "."]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ CLI


def dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _load_repo(raw_path: Path) -> tuple[ex.OfficialIndex, dict, dict, dict]:
    index = ex.OfficialIndex.build(ex.load_json(raw_path))
    return index, ex.load_explainers(), el.load_locales(), es.load_states()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--raw", type=Path, default=ex.RAW_PATH)
    parser.add_argument("--now", default=None, help="ISO timestamp (tests)")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("reconcile")
    r.add_argument("--snapshot", type=Path, required=True)
    r.add_argument("--live-report", type=Path, default=None)
    r.add_argument("--actions-out", type=Path, default=None)
    r.add_argument("--report-out", type=Path, default=None)
    n = sub.add_parser("next")
    n.add_argument("--top", type=int, default=8)
    n.add_argument("--json", action="store_true")
    sub.add_parser("report")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    now = _ts(args.now) if args.now else datetime.now(timezone.utc)
    previous = ex.load_json(args.ledger) if args.ledger.is_file() else None
    index, explainers, bundles, states = _load_repo(args.raw)
    queue = ex.load_json(ex.QUEUE_PATH) if ex.QUEUE_PATH.is_file() else {}
    if args.command == "reconcile":
        snapshot = ex.load_json(args.snapshot)
        live_report = ex.load_json(args.live_report) if args.live_report and args.live_report.is_file() else None
        ledger, actions = reconcile(snapshot, previous, index=index, main_explainers=explainers,
                                    main_bundles=bundles, main_states=states, now=now,
                                    run_id=os.environ.get("GITHUB_RUN_ID"), live_report=live_report)
        changed = ex.write_if_changed(args.ledger, dump(ledger))
        plan = slot_plan(ledger, queue, index=index, research_dir=es.RESEARCH_DIR, today=now.astimezone(JST).date())
        text = report_markdown(ledger, plan, actions, snapshot.get("ci_runs") or [], now)
        if args.actions_out:
            args.actions_out.write_text(json.dumps(actions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.report_out:
            args.report_out.write_text(text, encoding="utf-8")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as out:
                out.write(text)
        for e in ledger["dossiers"].values():
            if (e.get("ci") or {}).get("failed_runs", 0) >= 3 and e.get("stage") in WORK_ORDER:
                print(f"::warning::{e['dossier_id']}: {e['ci']['failed_runs']} failed CI runs on PR #{e.get('pr')} — {e['next_action']}")
        print(f"ledger dossiers={len(ledger['dossiers'])} changed={str(changed).lower()} actions={len(actions)}")
        return 0
    if previous is None:
        raise SystemExit(f"{args.ledger} does not exist yet; it is written by the explainer-pipeline workflow")
    plan = slot_plan(previous, queue, index=index, research_dir=es.RESEARCH_DIR, today=now.astimezone(JST).date(),
                     top=getattr(args, "top", 8))
    if args.command == "next":
        if args.json:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            for n_, step in enumerate(plan, start=1):
                print(f"{n_}. [{step['tier']}] {step.get('dossier_id') or step.get('queue_id')}: {step['action']}")
        return 0
    print(report_markdown(previous, plan, [], [], now), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
