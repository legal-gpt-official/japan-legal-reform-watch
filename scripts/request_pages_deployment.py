"""Route a Pages request by the repository's actual publishing mode.

No site settings, DNS, permissions or published data are changed here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def _api(path: str) -> dict:
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def same_docs(repo: str, sha: str, deployed_sha: str) -> bool:
    """Only identical or ancestor-related revisions with no changed docs count."""
    if not all(re.fullmatch(r"[a-f0-9]{40}", v or "") for v in (sha, deployed_sha)):
        return False
    if sha == deployed_sha:
        return True
    cmp = _api(f"repos/{repo}/compare/{deployed_sha}...{sha}")
    if cmp.get("status") == "behind":
        cmp = _api(f"repos/{repo}/compare/{sha}...{deployed_sha}")
    files = cmp.get("files")
    return (cmp.get("status") in ("ahead", "identical") and isinstance(files, list)
            and len(files) < 300 and not any(
                str(f.get("filename") or "").startswith("docs/")
                or str(f.get("previous_filename") or "").startswith("docs/") for f in files))


def reusable_deployment(repo: str, mode: str, sha: str) -> int | None:
    """Reuse only the latest run in the configured publishing mode.

    If a lookup fails, history is truncated, a newer deployment is pending,
    failed or skipped, or the docs differ, request a fresh deploy instead.
    """
    try:
        rows = _api(f"repos/{repo}/actions/runs?branch=main&per_page=100")["workflow_runs"]
        path = ("dynamic/pages/pages-build-deployment" if mode == "legacy"
                else ".github/workflows/deploy-static-pages.yml")
        candidates = [r for r in rows if r.get("path") == path and r.get("head_branch") == "main"]
        if not candidates:
            return None
        run = max(candidates, key=lambda r: (r.get("created_at") or "", int(r["id"])))
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            return None
        if mode == "workflow":
            jobs = _api(f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=latest&per_page=100")["jobs"]
            if not any(j.get("name") == "deploy" and j.get("conclusion") == "success"
                       and any(s.get("name") == "Deploy to GitHub Pages" and s.get("conclusion") == "success"
                               for s in j.get("steps", [])) for j in jobs):
                return None
        if same_docs(repo, sha, run.get("head_sha")):
            return int(run["id"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        return None
    return None


def request_deployment(repo: str, *, reuse_current: bool = False) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    mode = subprocess.check_output(
        ["gh", "api", f"repos/{repo}/pages", "--jq", ".build_type"], text=True
    ).strip()
    if mode not in ("legacy", "workflow"):
        raise RuntimeError(f"Unknown Pages publishing mode: {mode!r}; refusing to guess")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as out:
            out.write(f"mode={mode}\n")
    if reuse_current:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        existing = reusable_deployment(repo, mode, sha)
        if existing is not None:
            print(f"Pages deployment reused: mode={mode} run_id={existing} docs_sha={sha}")
            return mode
    if mode == "legacy":
        subprocess.run(
            ["gh", "api", "--method", "POST", f"repos/{repo}/pages/builds"],
            check=True,
        )
    elif mode == "workflow":
        subprocess.run(
            ["gh", "workflow", "run", "deploy-static-pages.yml", "--repo", repo, "--ref", "main"],
            check=True,
        )
    else:
        raise RuntimeError(f"Unknown Pages publishing mode: {mode!r}; refusing to guess")
    print(f"Pages deployment requested via {mode}")
    return mode


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-current", action="store_true")
    args = parser.parse_args()
    request_deployment(os.environ["GITHUB_REPOSITORY"], reuse_current=args.reuse_current)
