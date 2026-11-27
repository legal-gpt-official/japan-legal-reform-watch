"""Route a Pages request by the repository's actual publishing mode.

No site settings, DNS, permissions or published data are changed here.
"""
from __future__ import annotations

import os
import re
import subprocess


def request_deployment(repo: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    mode = subprocess.check_output(
        ["gh", "api", f"repos/{repo}/pages", "--jq", ".build_type"], text=True
    ).strip()
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
    request_deployment(os.environ["GITHUB_REPOSITORY"])
