# Handoff to Codex — Japan Legal Reform Watch

## Project

Project name: Japan Legal Reform Watch by LegalOS  
Purpose: A free English web dashboard for monitoring Japanese legal and regulatory updates, public comments, government announcements, and business compliance developments based on official Japanese sources.

This project is intentionally built as a static dashboard first. It is not yet a full SaaS.

## Current completed stages

### Stage 1 — Static dashboard

Completed.

- `docs/index.html`
- `docs/style.css`
- `docs/app.js`
- `docs/legal/disclaimer_en.html`
- `docs/legal/disclaimer_ja.html`
- `docs/data/legal_updates.json`

Key points:
- GitHub Pages is intended to serve from `/docs`.
- The dashboard reads `./data/legal_updates.json`.
- Disclaimer modal appears on first visit.
- Full disclaimer pages are HTML, not raw Markdown.
- External data is treated as untrusted.
- Text fields are escaped before rendering.
- `source_url` is passed through `safeUrl()` and `escapeHtml()` before being used as an href attribute.

### Stage 2 — Raw ingestion

Completed.

- `scripts/fetch_updates.py`
- `data/raw_items.json`
- `logs/fetch.log`

The script fetches Japanese government-related RSS feeds and stores normalized raw items.

Current sources:
- e-Gov Public Comment
- FSA
- MHLW
- Digital Agency

Current raw snapshot:
- 152 raw items
- Stable IDs
- Duplicate removal verified
- Idempotency verified
- `docs/` is not touched by this stage

### Stage 2.5 — Public data build and ranking

Completed.

- `scripts/build_public_data.py`
- `docs/data/legal_updates.json`
- `docs/data/legal_updates.backup.json`

The script reads `data/raw_items.json`, applies rule-based relevance scoring, filters noise, maps to the public dashboard schema, and writes `docs/data/legal_updates.json`.

Current public snapshot:
- 34 items
- Top items are public comments and regulatory drafts
- Noise such as meeting minutes, statistics, web magazines, recruitment, procurement, and simple page updates is filtered out or demoted
- `relevance_score` is a technical heuristic, not a legal judgment
- Stage 3 script exists, but AI summaries are generated only after `scripts/summarize_updates.py` is run with `ANTHROPIC_API_KEY`
- A Stage 2-only `docs/data/legal_updates.json` may still contain only rule-based placeholder English and may not yet include `summary_source`

## Current task

Harden and test Stage 3 AI summarization before the first real API run.

Existing / expected Stage 3 files:

- `scripts/summarize_updates.py` exists
- `data/summary_cache.json` may exist and may be empty until the first successful Stage 3 run
- `docs/data/legal_updates.before_ai.json` is created once by the first non-dry-run Stage 3 execution
- `logs/summarize.log` is created when Stage 3 runs with an API key
- `requirements.txt` includes the Anthropic SDK
- `README.md` and `CLAUDE.md` should describe the same Stage 3 status and guardrails

## Important rules

1. Do not hardcode any API key.
2. Read the API key from environment variable:

   `ANTHROPIC_API_KEY`

3. If no API key is present, exit cleanly with usage instructions. Do not crash.
4. Use the official Anthropic Python SDK if available.
5. The Claude model defaults to the script default but may be overridden with `ANTHROPIC_MODEL` or `--model`.
6. Do not change `id` or `source_url`.
7. Do not remove existing fields required by the UI.
8. External source data is untrusted.
9. AI output must be validated before writing.
10. If AI summarization fails for an item, leave the rule-based fields unchanged.
11. Write logs to `logs/summarize.log`.
12. Preserve `docs/data/legal_updates.before_ai.json` as an initial pre-AI baseline created once, not as a per-run backup.
13. Cache summaries in `data/summary_cache.json` so repeated runs do not call the API again for unchanged items.

## Input

Primary input:

- `docs/data/legal_updates.json`

Supplementary input if available:

- `data/raw_items.json`

Use raw fields such as:

- `raw_summary`
- `raw_content_hash`
- `source_type`

if they can be matched by `id`.

## Output

Update the same file:

- `docs/data/legal_updates.json`

Before writing, create if it does not already exist:

- `docs/data/legal_updates.before_ai.json`

This file is the initial pre-AI baseline from the first non-dry-run Stage 3 execution. It is not a per-run timestamped backup.

Each AI-summarized item should include:

- `title_en`
- `summary_en`
- `business_impact_en`
- `recommended_action_en`
- `confidence`
- `ai_notes`
- `summary_source: "claude"`
- `summarized_at`
- `summary_model`

Items not summarized should include:

- `summary_source: "rule_based"`

Before Stage 3 has ever run, `summary_source` may be absent from Stage 2-only output. During a Stage 3 run, keep the existing policy: summarized items become `summary_source: "claude"` and untouched / failed items become `summary_source: "rule_based"`.

## Required public schema fields

Every item must retain at least:

- `id`
- `title_en`
- `title_ja`
- `area`
- `stage`
- `impact_level`
- `summary_en`
- `business_impact_en`
- `recommended_action_en`
- `source_name`
- `source_url`
- `published_at`
- `last_checked`

Existing additional fields such as `relevance_score` may remain.

## AI guardrails

The AI prompt must require:

- Do not invent facts.
- Do not provide legal advice.
- Do not state that a law has been enacted, promulgated, or entered into force unless the provided source text clearly supports it.
- If the source indicates a public comment, draft, proposal, consultation, guideline draft, or government announcement, clearly label it as such.
- Treat `area`, `stage`, and `impact_level` as preliminary rule-based labels, not legally verified conclusions.
- Do not make definitive compliance recommendations.
- Preserve the caution that the Japanese official source is authoritative.
- Return only valid JSON.

Validation should check required structure and log caution warnings for obvious definitive/legal-advice wording such as "you must comply", "is legally required", "has been enacted", "is in force", and "will definitely". Warnings should prompt review without automatically rejecting source-supported statements.

The AI should return only:

- `title_en`
- `summary_en`
- `business_impact_en`
- `recommended_action_en`
- `confidence`
- `ai_notes`

Allowed confidence values:

- `high`
- `medium`
- `low`

## Initial test plan

First test without an API key:

```bash
python scripts/summarize_updates.py --limit 3

Expected:

Clean usage message
Exit without crashing
No modification to the real published file

Then test with API key:

PowerShell:

$env:ANTHROPIC_API_KEY = "<your-anthropic-api-key>"
python scripts/summarize_updates.py --limit 3

Expected:

3 target items
3 API calls on first run
3 summarized items
docs/data/legal_updates.before_ai.json created
data/summary_cache.json created
top 3 items have summary_source: "claude"
remaining items have summary_source: "rule_based"
id unchanged
source_url unchanged

Second run:

python scripts/summarize_updates.py --limit 3

Expected:

3 cache hits
0 API calls
Same output
Local dashboard verification

After summarization:

python -m http.server 8000 --directory docs

Open:

http://localhost:8000/

Check:

Dashboard still loads
Top items display natural English summaries
Disclaimer modal still works
Disclaimer links resolve
External source links still work
Browser console has no critical errors
Do not implement yet

Do not implement these until Stage 3 is stable:

GitHub Actions
GitHub Pages setup
New sources
Pagination
Full SaaS backend
Login
Email alerts
Payment
