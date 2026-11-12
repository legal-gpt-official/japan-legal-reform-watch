"""Require exact generated-docs SHA before the Publish workflow dispatches LIVE."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/publish-explainer-pages.yml"


def handoff_script() -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    candidates = [
        i for i, line in enumerate(lines)
        if "Dispatch strict LIVE verification after matching Pages success" in line
    ]
    if len(candidates) != 1:
        raise AssertionError("Exactly one strict LIVE handoff expected")
    script_positions = [
        i for i, line in enumerate(lines[candidates[0] + 1:], candidates[0] + 1)
        if line.strip() == "script: |"
    ]
    if not script_positions:
        raise AssertionError("Missing LIVE handoff JavaScript")
    out = []
    for line in lines[script_positions[0] + 1:]:
        if not line.strip():
            out.append("")
        elif line.startswith("            "):
            out.append(line[12:])
        else:
            break
    return "\n".join(out) + "\n"


class PublishHandoffTests(unittest.TestCase):
    def test_syntax(self):
        # actions/github-script executes this body inside an AsyncFunction.
        # Parsing it as a bare module would reject its valid top-level return.
        wrapped = "async function githubScriptBody() {\n" + handoff_script() + "\n}\n"
        result = subprocess.run(
            ["node", "--input-type=module", "--check"],
            input=wrapped, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_policy_guards(self):
        w = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("actions: write", w)
        self.assertIn("id: docs_sha", w)
        self.assertIn("steps.docs_sha.outputs.sha", w)
        js = handoff_script()
        for required in (
            "run.head_sha === docsSha",
            "run.conclusion === 'success'",
            "verify-live-explainers.yml",
            "createWorkflowDispatch",
        ):
            self.assertIn(required, js)

    def test_real_javascript_only_dispatches_matching_success(self):
        js = handoff_script()
        harness = """
          const source = JS_SOURCE;
          const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
          const sha = "f".repeat(40);
          const other = "a".repeat(40);
          let runs = [];
          const dispatched = [];
          const failures = [];
          const github = { rest: { actions: {
            listWorkflowRunsForRepo:async args=>({data:{workflow_runs:runs}}),
            createWorkflowDispatch:async args=>{dispatched.push(args);}
          }}};
          const core={
            info:()=>{},warning:()=>{},
            setFailed:message=>{failures.push(message);}
          };
          const fn=new AsyncFunction("github","context","core","process","setTimeout",source);
          const run=()=>fn(github,{repo:{owner:"unit",repo:"repo"}},core,
            {env:{DOCS_SHA:sha}},callback=>callback());
          runs=[{id:1,name:"pages build and deployment",head_sha:other,
                 status:"completed",conclusion:"success"}];
          await run();
          if(dispatched.length!==0||failures.length!==1)
            throw Error("Other SHA must not authorize LIVE");
          failures.length=0;
          runs=[{id:2,name:"pages build and deployment",head_sha:sha,
                 status:"completed",conclusion:"failure"}];
          await run();
          if(dispatched.length!==0||failures.length!==1)
            throw Error("Failed Pages must not authorize LIVE");
          failures.length=0;
          runs=[{id:3,name:"pages build and deployment",head_sha:sha,
                 status:"completed",conclusion:"success"}];
          await run();
          if(dispatched.length!==1||failures.length!==0||
             dispatched[0].workflow_id!=="verify-live-explainers.yml")
            throw Error("Matching successful Pages must dispatch LIVE exactly once");
        """.replace("JS_SOURCE", json.dumps(js))
        result = subprocess.run(
            ["node", "--input-type=module"],
            input=harness, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
