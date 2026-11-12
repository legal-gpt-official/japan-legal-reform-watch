"""Guard the privileged safe-auto-merge workflow against JS syntax regressions.

The PR checks run this offline with Node (installed on GitHub-hosted runners).
No external GitHub API calls and no actual merges are performed.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/explainer-safe-automerge.yml"


def github_script() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    candidates = [i for i, line in enumerate(lines) if line.strip() == "script: |"]
    if len(candidates) != 1:
        raise AssertionError("Expected exactly one inline github-script JavaScript block")
    out = []
    for line in lines[candidates[0] + 1:]:
        if not line.strip():
            out.append("")
        elif line.startswith("            "):
            out.append(line[12:])
        else:
            break
    if not out:
        raise AssertionError("No auto-merge JavaScript")
    return "\n".join(out) + "\n"


class SafeAutoMergeWorkflowTests(unittest.TestCase):
    def test_javascript_parses_before_release(self):
        result = subprocess.run(
            ["node", "--input-type=module", "--check"],
            input=github_script(), text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_approval_and_sha_pinned_ci_guard(self):
        source = github_script()
        for fragment in (
            "legal-content-verified",
            "pr.head.sha",
            "checks[0].conclusion !== 'success'",
            "j.conclusion === 'success'",
            "sha:pr.head.sha",
            "pr.draft",
            "pr.head.repo?.full_name",
        ):
            self.assertIn(fragment, source)

    def test_policy_with_mocked_github_api(self):
        source = github_script()
        # Run the real script through mocked API methods. A labelled, same-repo,
        # non-draft PR with an exact head SHA and successful tests may merge;
        # stale CI, draft, foreign code and no label must never merge.
        harness = """
          const source = SCRIPT_SOURCE;
          const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
          const calls = [];
          const head = "1111111111111111111111111111111111111111";
          const pr = {
            number: 999, draft: false, state: "open",
            base: {ref:"main"}, head: {sha:head, repo:{full_name:"unit/repo"}},
            labels:[{name:"legal-content-verified"}]
          };
          const github = {
            rest: {
              pulls:{
                list:()=>{},listFiles:()=>{},
                get:async()=>({data:pr}),
                merge:async(args)=>{calls.push(args);}
              },
              actions:{listWorkflowRunsForRepo:()=>{},listJobsForWorkflowRun:()=>{}}
            },
            paginate:async function(action) {
              if(action===this.rest.pulls.list)return [pr];
              if(action===this.rest.pulls.listFiles)return [{filename:"data/explainers/mock.json"}];
              if(action===this.rest.actions.listWorkflowRunsForRepo)return [{
                id:777,name:"Pull request checks",head_sha:head,
                conclusion:"success",status:"completed",created_at:"2026-11-12T00:00:00Z"
              }];
              if(action===this.rest.actions.listJobsForWorkflowRun)return [{name:"tests",conclusion:"success"}];
              throw Error("Unexpected API");
            }
          };
          const fn=new AsyncFunction("github","context","core",source);
          const run=async()=>fn(github,{repo:{owner:"unit",repo:"repo"}},{info:()=>{},warning:()=>{}});
          await run();
          if(calls.length!==1 || calls[0].sha!==head)throw Error("Valid PR should merge once at exact head");
          calls.length=0;
          pr.labels=[];
          await run();
          if(calls.length)throw Error("Unlabelled PR merged");
          pr.labels=[{name:"legal-content-verified"}];
          pr.draft=true;
          await run();
          if(calls.length)throw Error("Draft PR merged");
          pr.draft=false;
          pr.head.repo.full_name="fork/repo";
          await run();
          if(calls.length)throw Error("Foreign PR merged");
        """.replace("SCRIPT_SOURCE", json.dumps(source))
        result = subprocess.run(
            ["node", "--input-type=module"],
            input=harness, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
