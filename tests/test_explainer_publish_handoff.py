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
          let compare = {status:"diverged", files:[]};
          let customStep = "skipped";
          const dispatched = [];
          const failures = [];
          const github = { rest: {
            actions: {
              listWorkflowRunsForRepo:async args=>({data:{workflow_runs:
                args.head_sha ? runs.filter(r=>r.head_sha===args.head_sha) : runs}}),
              createWorkflowDispatch:async args=>{dispatched.push(args);},
              listJobsForWorkflowRun:async args=>({data:{jobs:[{name:"deploy",conclusion:"success",
                steps:[{name:"Deploy to GitHub Pages",conclusion:customStep}]}]}})
            },
            repos: { compareCommits:async args=>({data:compare}) }
          }};
          const core={
            info:()=>{},warning:()=>{},
            setFailed:message=>{failures.push(message);}
          };
          const fn=new AsyncFunction("github","context","core","process","setTimeout",source);
          const run=()=>fn(github,{repo:{owner:"unit",repo:"repo"},runId:123},core,
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
             dispatched[0].workflow_id!=="verify-live-explainers.yml" ||
             dispatched[0].inputs.docs_sha!==sha || dispatched[0].inputs.publish_run_id!=="123" ||
             dispatched[0].inputs.pages_run_id!=="3")
            throw Error("Matching successful Pages must dispatch LIVE exactly once");
          // 2026-11-15 race: the docs SHA's builds were cancelled by a later bot commit.
          dispatched.length=0;
          const cancelled={id:4,name:"pages build and deployment",head_sha:sha,
                 status:"completed",conclusion:"cancelled"};
          const later={id:5,name:"pages build and deployment",head_sha:other,
                 status:"completed",conclusion:"success"};
          runs=[cancelled,later];
          compare={status:"ahead",files:[{filename:"data/explainer_pipeline.json"}]};
          await run();
          if(dispatched.length!==1||failures.length!==0)
            throw Error("A later deploy containing the docs SHA with docs/ unchanged must count");
          dispatched.length=0;
          compare={status:"ahead",files:[{filename:"docs/u/x/index.html"}]};
          await run();
          if(dispatched.length!==0||failures.length!==1)
            throw Error("A later deploy that changed docs/ must not count");
          failures.length=0;
          compare={status:"diverged",files:[]};
          await run();
          if(dispatched.length!==0||failures.length!==1)
            throw Error("A deploy not containing the docs SHA must not count");
          failures.length=0;
          compare={status:"ahead",files:new Array(300).fill({filename:"data/x.json"})};
          await run();
          if(dispatched.length!==0||failures.length!==1)
            throw Error("A truncated compare cannot prove docs/ unchanged");
          failures.length=0;
          runs=[{id:6,name:"Deploy static Pages",path:".github/workflows/deploy-static-pages.yml",
                 head_branch:"main",head_sha:sha,status:"completed",conclusion:"success"}];
          await run();
          if(dispatched.length!==0||failures.length!==1)
            throw Error("A migration no-op cannot authorize LIVE");
          failures.length=0;
          customStep="success";
          await run();
          if(dispatched.length!==1||failures.length!==0||dispatched[0].inputs.pages_run_id!=="6")
            throw Error("An actual custom deployment must authorize LIVE");
        """.replace("JS_SOURCE", json.dumps(js))
        result = subprocess.run(
            ["node", "--input-type=module"],
            input=harness, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


    def test_live_handoff_validates_success_and_trusted_main_before_checkout(self):
        w = (ROOT / ".github/workflows/verify-live-explainers.yml").read_text(encoding="utf-8")
        self.assertNotIn("  workflow_run:", w)
        self.assertNotIn("  push:", w)
        self.assertIn("cancel-in-progress: false", w)
        lines = w.splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip() == "script: |")
        js = []
        for line in lines[start + 1:]:
            if not line.strip():
                js.append("")
            elif line.startswith("            "):
                js.append(line[12:])
            else:
                break
        harness = """
          const source = JS_SOURCE;
          const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
          const sha = "f".repeat(40), other = "a".repeat(40);
          const pub = {path:".github/workflows/publish-explainer-pages.yml",head_branch:"main",status:"completed",conclusion:"success"};
          const page = {name:"pages build and deployment",path:"dynamic/pages/pages-build-deployment",head_branch:"main",
                        head_sha:sha,status:"completed",conclusion:"success"};
          let main = {status:"ahead"}, compared = {status:"ahead",files:[]};
          let deployStep = "skipped";
          let out;
          const github = {rest:{
            actions:{getWorkflowRun:async ({run_id})=>({data:run_id===123?pub:page}),
              listJobsForWorkflowRun:async()=>({data:{jobs:[{name:"deploy",conclusion:"success",
                steps:[{name:"Deploy to GitHub Pages",conclusion:deployStep}]}]}})},
            repos:{compareCommits:async ({head})=>({data:head==="main"?main:compared})}
          }};
          const fn = new AsyncFunction("github","context","core","process","setTimeout",source);
          const run = async (env, expected) => {
            out = undefined; let failed = false;
            try {await fn(github,{repo:{owner:"unit",repo:"repo"}},{setOutput:(k,v)=>out=v},
                       {env},callback=>callback());} catch(e){failed=true;}
            if(expected===null ? !failed||out!==undefined : failed||out!==expected)
              throw Error("Unexpected handoff outcome: "+out+" "+failed);
          };
          const env={DOCS_SHA:sha,PUBLISH_RUN_ID:"123",PAGES_RUN_ID:"456"};
          await run({}, "main");
          await run({DOCS_SHA:sha}, null);
          await run(env,sha);
          pub.conclusion="failure"; await run(env,null); pub.conclusion="success";
          page.conclusion="failure"; await run(env,null); page.conclusion="success";
          page.head_sha=other; await run(env,sha);
          compared.files=[{filename:"other/x",previous_filename:"docs/u/x"}]; await run(env,null);
          compared.files=[]; main.status="diverged"; await run(env,null);
          main.status="ahead"; page.head_sha=sha; page.name="Deploy static Pages";
          page.path=".github/workflows/deploy-static-pages.yml";
          await run(env,null);
          deployStep="success"; await run(env,sha);
        """.replace("JS_SOURCE", json.dumps("\n".join(js)))
        result = subprocess.run(["node", "--input-type=module"], input=harness, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
