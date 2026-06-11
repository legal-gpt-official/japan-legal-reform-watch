# Japan Legal Reform Watch by LegalOS

**Free Japan Legal & Regulatory Update Monitor.**

A free, browser-based dashboard that summarizes Japanese legal, regulatory, public-comment, and administrative announcements in English for foreign companies, in-house counsel, and compliance teams.

---

## What this is (and isn't)

- A free, English-language overview of Japanese legal and regulatory developments.
- A reference starting point — **not an official translation, and not legal advice.**
- See [Legal Notice (EN)](legal/DISCLAIMER_EN.md) and [免責事項 (JA)](legal/DISCLAIMER_JA.md).

## Current status

This repository is the **minimum viable static version**:

- The **published file [`docs/data/legal_updates.json`](docs/data/legal_updates.json) is generated** from fetched data by `scripts/fetch_updates.py` (raw fetch) → `scripts/build_public_data.py` (provisional, rule-based mapping).
- **Stage 3 script exists, but AI summaries are generated only after `scripts/summarize_updates.py` is run with `ANTHROPIC_API_KEY`.** Before Stage 3 has been run, `docs/data/legal_updates.json` may contain only fixed-template English and may not yet include `summary_source`. When Stage 3 runs, top-N items receive `summary_source: "claude"`; untouched or failed items are marked `summary_source: "rule_based"`. `area` / `stage` / `impact_level` and ranking remain keyword rules (a technical heuristic, not a legal judgement), and there are no scheduled jobs.
- The original hand-curated sample is kept at [`data/legal_updates.json`](data/legal_updates.json) as a schema reference only.
- The dashboard runs entirely in the browser from the `docs/` folder.

## Running locally

The page loads its data via `fetch()`, so it must be served over HTTP. Opening `index.html` directly via `file://` will not work in modern browsers. Everything the published site needs lives inside `docs/`, so you can serve either the project root (and browse to `/docs/`) or the `docs/` folder directly — serving `docs/` mirrors the GitHub Pages setup described under [Deployment](#deployment-github-pages).

### Option 1 — Python (no install required)

From the project root:

```
python -m http.server 8000
```

Then open <http://localhost:8000/docs/> in your browser.

### Option 2 — Node.js

From the project root:

```
npx serve .
```

Then navigate to `/docs/` on the URL printed by `serve`.

### Option 3 — Any other static server

Serve the project root with the static server of your choice and visit `/docs/index.html`.

## On first load

A disclaimer modal appears on first visit. After reading it, click **"I Understand and Agree"** to dismiss the modal. Your acceptance is remembered in `localStorage` (key: `jlrw_disclaimer_accepted_v1`); clearing site data for this origin will reset it.

## Ingestion (raw fetch — Stage 1)

`scripts/fetch_updates.py` fetches a small set of Japanese public-sector feeds (the `SOURCES` list inside the script) and stores **raw, de-duplicated** items in [`data/raw_items.json`](data/raw_items.json). It performs fetching, normalization, de-duplication, and logging **only** — it does **not** summarize, call any LLM, or modify the published `docs/data/legal_updates.json`.

**Install dependencies** (optional but recommended — the script falls back to the Python standard library if they are missing):

```
python -m pip install -r requirements.txt
```

**Run:**

```
python scripts/fetch_updates.py            # fetch and append only new items
python scripts/fetch_updates.py --dry-run  # fetch and report, but do not write
```

The script prints a console summary (`checked_sources`, `fetched_items`, `new_items`, `total_items`, `failed_sources`), writes details and errors to [`logs/fetch.log`](logs/fetch.log), and appends only **new** items to `data/raw_items.json` (existing items are preserved; re-running is safe and idempotent). A failing source is logged and skipped without stopping the run.

**Untrusted input:** every fetched field — especially `source_url`, `title_ja`, and `raw_summary` — is treated as untrusted external data. It is stored verbatim (with light text normalization) and is **never** rendered, executed, or trusted by the fetch step.

Each raw item has: `id`, `title_ja`, `source_name`, `source_url`, `published_at` (ISO; empty string when the feed gives no date — never guessed), `fetched_at`, `source_language`, `raw_summary`, `raw_content_hash`, and `source_type`.

## Build published data (Stage 2 — provisional, rule-based)

`scripts/build_public_data.py` maps the raw items in `data/raw_items.json` into the schema the dashboard expects and writes [`docs/data/legal_updates.json`](docs/data/legal_updates.json).

> **This stage does not use AI and does not translate or interpret anything.** It is a provisional, rule-based placeholder so the dashboard can show *that* an item exists and prompt the reader to check the official Japanese source. `title_en` is a labelled passthrough of the Japanese title; `summary_en`, `business_impact_en`, and `recommended_action_en` are fixed template sentences. `area`, `stage`, and `impact_level` are assigned by simple keyword rules (conservative — `impact_level` never exceeds `Medium` at this stage).

**Run** (after `fetch_updates.py` has produced `data/raw_items.json`):

```
python scripts/build_public_data.py
```

Behaviour:

- **Relevance ranking.** Each item gets an internal keyword-based `relevance_score` that rewards law-reform / regulation / public-comment / guideline signals (改正, 施行, 公布, 法律 / 政令 / 省令, 意見募集, 指針 / ガイドライン, 義務, 個人情報, 金融, 労働, …) and penalises minutes, statistics, web magazines, and bare page updates. e-Gov public comments get a source bonus. Output is ordered by `relevance_score`, then impact weight (Medium > Low), then recency (older items get a light penalty so they don't linger at the top). The score is written to each record as an optional `relevance_score` field (the UI ignores unknown fields).
- **Display order.** The dashboard displays records in the array order of `docs/data/legal_updates.json`. `build_public_data.py` owns the ranking/order; `docs/app.js` must not re-sort records by `published_at` or any other field.
- **Exclusion.** Drops obvious administrative noise (recruitment, procurement, bidding, events, press conferences, web magazines) and items with no net legal / regulatory signal — **unless** a strong keyword (改正 / 施行 / 案 / ガイドライン / 意見募集 / 法律 …) is present. Public-comment items are always kept.
- Backs up the current `docs/data/legal_updates.json` to `docs/data/legal_updates.backup.json` before overwriting.
- Caps output at 50 items; items with no / invalid date rank last.
- Prints a console summary: `input_items`, `excluded_items`, `candidate_items`, `output_items`, `backup_created`, `top_relevance_score`, `lowest_output_relevance_score`, `output_path`.
- Keeps `source_url` verbatim and treats all input as untrusted (the browser escapes every field on render).

The `relevance_score` is a **technical heuristic to decide what to surface — not a legal judgement** of importance. The output remains a provisional, AI-free preview: `summary_en` explicitly states it "has not yet been reviewed or summarized by AI."

## AI summarization (Stage 3 — Claude)

`scripts/summarize_updates.py` is the optional Stage 3 script. When run with `ANTHROPIC_API_KEY`, it sends the **top-N items by `relevance_score`** (default 10) to the Claude API and writes English `title_en`, `summary_en`, `business_impact_en`, and `recommended_action_en` back into [`docs/data/legal_updates.json`](docs/data/legal_updates.json). Items it summarizes are marked `summary_source: "claude"` (and gain `summarized_at`, `summary_model`, `confidence`, `ai_notes`); untouched or failed items keep the rule-based template and are marked `summary_source: "rule_based"`. If Stage 3 has not yet been run, `summary_source` may be absent. `source_url` and `id` are never changed.

> **Still not legal advice.** The model runs under strict guardrails: no invented facts, no legal advice or definitive compliance recommendations, no claiming a law is enacted / promulgated / in force unless the provided source text clearly supports it, public comments / drafts / proposals / consultations / draft guidelines / government announcements labelled as such, and the official Japanese source treated as authoritative. `area`, `stage`, and `impact_level` are preliminary rule-based labels and must not be treated as legally verified conclusions. The result is an unofficial summary to verify against the primary source.

**Set your API key** — it is read **only** from the environment and is never stored in the repo:

```
# PowerShell
$env:ANTHROPIC_API_KEY = "<your-anthropic-api-key>"
# bash / zsh
read -s ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY
```

If the key is not set, the script prints usage and exits cleanly without calling the API. The key is read only from `ANTHROPIC_API_KEY`; it is never written to code, logs, cache, or documentation.

**Optional model override** — the default model is `claude-opus-4-8`, but you can override it without changing code:

```
# PowerShell
$env:ANTHROPIC_MODEL = "your-model-id"
# bash / zsh
export ANTHROPIC_MODEL="your-model-id"
```

**Run** (after `build_public_data.py` has produced the published file):

```
python -m pip install -r requirements.txt                # installs the anthropic SDK
python scripts/summarize_updates.py --limit 10           # summarize the top 10
python scripts/summarize_updates.py --limit 3 --dry-run   # preview without writing
```

Behaviour:

- **Baseline snapshot.** The pre-AI file is snapshotted to `docs/data/legal_updates.before_ai.json` **once**, on the first non-dry-run Stage 3 execution. This is an initial pre-AI baseline, not a per-run backup. Stage 2 still creates `docs/data/legal_updates.backup.json` before each rule-based rebuild.
- **Cache.** Successful summaries are cached in [`data/summary_cache.json`](data/summary_cache.json), keyed by item `id` + content hash. Re-running never re-summarizes an unchanged item — a cache hit makes **no** API call — so repeat runs are cheap and idempotent. Delete the cache (or an entry) to force a refresh.
- **Resilience.** A failed API call leaves that item's rule-based copy intact (`summary_source: "rule_based"`) and is logged to [`logs/summarize.log`](logs/summarize.log); one failure never stops the run.
- **Validation.** Before writing, the output is checked: required UI fields present and non-empty, `confidence` is `high` / `medium` / `low`, and `id` / `source_url` unchanged. Obvious definitive/legal-advice phrases such as "you must comply", "is legally required", "has been enacted", "is in force", and "will definitely" are logged as caution warnings for review.
- Console summary: `input_items`, `target_items`, `cache_hits`, `api_calls`, `summarized_items`, `failed_items`, `caution_warnings`, `output_path`, `backup_created`.

The default model is `claude-opus-4-8` (override with `--model` or `ANTHROPIC_MODEL`). All input is treated as untrusted: item metadata is sent to the model clearly delimited as data, with an explicit instruction never to follow instructions embedded in it.

## Deployment (GitHub Pages)

This project is designed to be published with **GitHub Pages set to serve from the `/docs` folder**. Everything required at runtime lives under `docs/`, so the published site is self-contained:

- `docs/index.html`, `docs/style.css`, `docs/app.js`
- `docs/data/legal_updates.json` — the data the dashboard fetches (relative path `./data/legal_updates.json`)
- `docs/legal/disclaimer_en.html`, `docs/legal/disclaimer_ja.html` — the published disclaimer pages
- `docs/.nojekyll` — disables Jekyll so all files are served verbatim

No files outside `docs/` are needed to run the published dashboard.

## Published data

The **published data file is [`docs/data/legal_updates.json`](docs/data/legal_updates.json)** — this is what the live dashboard loads. It is **generated** by `scripts/build_public_data.py` from `data/raw_items.json`; the previous version is saved to `docs/data/legal_updates.backup.json` on each Stage 2 rebuild.

If `scripts/summarize_updates.py` is run with `ANTHROPIC_API_KEY`, Stage 3 post-processes the same published file. AI-summarized records are marked `summary_source: "claude"`; non-summarized records are marked `summary_source: "rule_based"`. A file that has only gone through Stage 2 may not yet contain `summary_source`.

Display order is part of the published data contract: the browser respects the JSON array order, including after filtering and searching.

The original hand-curated sample is retained at [`data/legal_updates.json`](data/legal_updates.json) as a **schema reference only** — it is no longer kept in sync with, and no longer feeds, the published file.

## Security & data handling

All record fields are rendered through HTML escaping in `docs/app.js`, and `source_url` is **both** scheme-checked (`safeUrl()`) **and** attribute-escaped (`escapeHtml()`) before it is placed in an `href`. **Treat all ingested/source data as untrusted, and do not remove or bypass the escaping in `renderCard()` / `escapeHtml()` / `safeUrl()`.** This is the boundary that prevents a malicious or malformed source field from injecting markup once automated ingestion is added.

## File layout

```
japan-legal-reform-watch/
├── README.md
├── CLAUDE.md                     # Notes for Claude Code working on this repo
├── requirements.txt              # Dependencies (requests, feedparser, anthropic)
├── .gitignore
├── scripts/
│   ├── fetch_updates.py          # Stage 1 raw ingestion (fetch → normalize → dedupe → log)
│   ├── build_public_data.py      # Stage 2 provisional rule-based build of the published data
│   └── summarize_updates.py      # Stage 3 Claude AI summarization of the top-N items
├── data/
│   ├── legal_updates.json        # Original hand-curated sample (schema reference only)
│   ├── raw_items.json            # Raw fetched items (output of fetch_updates.py)
│   └── summary_cache.json        # Claude summary cache (created/updated by Stage 3)
├── logs/
│   ├── fetch.log                 # Stage 1 ingestion run log
│   └── summarize.log             # Stage 3 summarization run log
├── docs/                         # ← GitHub Pages publish root (serve from /docs)
│   ├── index.html                # Dashboard entry point
│   ├── style.css
│   ├── app.js
│   ├── .nojekyll                 # Serve files verbatim (no Jekyll)
│   ├── data/
│   │   ├── legal_updates.json            # Published data (GENERATED; optionally AI-summarized after Stage 3)
│   │   ├── legal_updates.backup.json     # Previous published data (backup before each rebuild)
│   │   └── legal_updates.before_ai.json  # Pre-AI snapshot (created once by summarize_updates.py)
│   └── legal/
│       ├── disclaimer_en.html    # Published English disclaimer page
│       └── disclaimer_ja.html    # Published Japanese disclaimer page
└── legal/
    ├── DISCLAIMER_EN.md          # Disclaimer source (English)
    └── DISCLAIMER_JA.md          # Disclaimer source (Japanese / 免責事項)
```

## Features

- **Card-based feed** of regulatory updates with English and original Japanese titles.
- **Filters** by Area, Stage, and Impact Level.
- **Free-text search** across English title and summary.
- **Disclaimer modal** on first visit, with acceptance persisted in `localStorage`.
- **Footer link** to the full Legal Notice / Disclaimer at all times.

## Data schema

Each entry in `docs/data/legal_updates.json` (and its source copy `data/legal_updates.json`) has the following fields:

| Field                    | Description                                                              |
| ------------------------ | ------------------------------------------------------------------------ |
| `id`                     | Stable identifier (e.g. `jlrw-2026-001`).                                |
| `title_en`               | Informal English title.                                                  |
| `title_ja`               | Original Japanese title.                                                 |
| `area`                   | Subject area (e.g. Data Privacy, Financial Regulation).                  |
| `stage`                  | Legislative / regulatory stage (e.g. Public Comment, Diet Submission).   |
| `impact_level`           | `High` \| `Medium` \| `Low`.                                             |
| `summary_en`             | Short factual summary in English.                                        |
| `business_impact_en`     | Short note on practical impact on businesses.                            |
| `recommended_action_en`  | Short note on suggested next steps.                                      |
| `source_name`            | Issuing authority (e.g. FSA, METI, PPC, JFTC).                           |
| `source_url`             | URL of the original Japanese source.                                     |
| `published_at`           | Date published / announced (ISO `YYYY-MM-DD`).                           |
| `last_checked`           | Date this entry was last verified (ISO `YYYY-MM-DD`).                    |

## Roadmap (not yet implemented)

- **A three-stage pipeline exists** (`fetch_updates.py` → `build_public_data.py` → `summarize_updates.py`), but Stage 3 requires an explicit API-key-backed run and is not scheduled.
- First real Stage 3 API run, review of generated summaries, and expansion beyond top-N once the guardrails are accepted.
- Scheduled updates via GitHub Actions.
- Public hosting on GitHub Pages.

## License & use

This tool is provided free of charge for informational purposes. See [Legal Notice (EN)](legal/DISCLAIMER_EN.md) for terms, including limitation of liability.
