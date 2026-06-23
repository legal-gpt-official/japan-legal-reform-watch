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
- **Stage 3 script exists, but AI summaries are generated only after `scripts/summarize_updates.py` is run with `ANTHROPIC_API_KEY`.** Before Stage 3 has been run, `docs/data/legal_updates.json` may contain only fixed-template English and may not yet include `summary_source`. When Stage 3 runs, top-N items receive `summary_source: "claude"`; untouched or failed items are marked `summary_source: "rule_based"`. `area` / `stage` / `impact_level` and ranking remain keyword rules (a technical heuristic, not a legal judgement).
- **GitHub Actions daily update workflow exists** at `.github/workflows/daily-update.yml` for manual runs and a daily 21:00 UTC schedule (06:00 JST).
- **Source Health Monitor exists for administrators in GitHub Actions.** It evaluates per-source raw fetch counts, writes a minimal health state file, and can fail the workflow only after serious source-health conditions. It is not shown in the public dashboard UI.
- **Newly detected exists at item level.** It identifies records first appended to this dashboard's continuing raw history after the feature is deployed; it does not mean a new law, new regulation, enactment date, amendment date, or first government publication date.
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

`scripts/fetch_updates.py` fetches a curated set of Japanese public-sector feeds and official update pages (the `SOURCES` list inside the script) and stores **raw, de-duplicated** items in [`data/raw_items.json`](data/raw_items.json). Official-source coverage: **e-Gov Public Comment, FSA, METI, MHLW, Digital Agency, CAA, PPC, JFTC, MOJ (法務省), MOE (環境省), MOF (財務省), MIC (総務省), MLIT (国土交通省), and MAFF (農林水産省)**. PPC, JFTC, MOE, MLIT, and METI use lightweight official HTML page parsing because stable press/update RSS endpoints were not found, failed, or the official page uses an HTML list; MAFF uses the official press-release RSS. (METI moved from its Atom feed to the official press-release index `https://www.meti.go.jp/press/index.html` after the feed became unreliable and repeatedly failed the Source Health gate. Because that host ReadTimeouts from CI, the METI source uses escalating per-attempt timeouts `(20, 35, 50)`, longer backoff, and a requests→urllib transport fallback; the other sources keep the 20s default. METI is also a **warning-only / non-gating source** — still fetched and reported, but a METI-only continued failure does not fail the Source Health gate.) The others are RSS/RDF/Atom (the MIC feed is Shift_JIS, handled by both parser paths). NTA (国税庁) was investigated and deferred — no RSS, and its legacy news page is dominated by statistics/PDF notices. It performs fetching, normalization, de-duplication, and logging **only** — it does **not** summarize, call any LLM, or modify the published `docs/data/legal_updates.json`.

`data/raw_items.json` is the **accumulated source history**: re-runs only append genuinely new items, and the file is never trimmed to match the published cap. New items appended after the Newly detected feature is deployed receive `first_seen_at` as the current Asia/Tokyo date (`YYYY-MM-DD`). Existing legacy items are not backfilled, and missing `first_seen_at` means the first detection date is unknown.

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

Each raw item has: `id`, `title_ja`, `source_name`, `source_url`, `published_at` (ISO; empty string when the feed gives no date — never guessed), `fetched_at`, `source_language`, `raw_summary`, `raw_content_hash`, and `source_type`. Newer records may also have optional `first_seen_at`.

## Newly detected

`first_seen_at` means "First detected by this dashboard" and is assigned only when a raw item is actually appended as new under the existing ID/source URL merge rules. It is not a legal status and must not be described as a new law, new regulation, recently enacted law, recently amended rule, or breaking legal update.

Legacy records without `first_seen_at` are treated as unknown and are not shown as Newly detected. Local tests that do not run `fetch_updates.py` may therefore show `Newly detected (7d): 0`; that is expected.

The public dashboard treats an item as Newly detected when `first_seen_at` is a valid non-future `YYYY-MM-DD` within the last 7 calendar days including today. The Quick filter uses URL parameter `new=7`; other `new` values are ignored. Sort supports `sort=detected` for First detected order. CSV export includes a `First detected` column, blank for legacy or invalid dates.

## Source Health Monitor

The Source Health Monitor is an administrator-facing GitHub Actions check. It is designed to detect silent per-source fetch failures, especially for official HTML parsers such as MOE and MLIT. It is **not** displayed in `docs/app.js`, public dashboard cards, CSV export, URL state, or the Data status UI.

Health is based on raw source fetch results from `scripts/fetch_updates.py`, not on how many records survive Stage 2 publication filtering. For example, if MOJ returns 10 raw parsed items but all are later excluded as administrative noise, source fetch health is still healthy and published items may be 0.

`scripts/fetch_updates.py` writes [`logs/source_fetch_report.json`](logs/source_fetch_report.json) after each run. This transient run report includes `schema_version`, run timestamps, configured source count, and one row per source with `source_key`, `source_name`, `source_url`, `status`, `fetched_count`, `new_count`, `latest_published_at`, `duration_ms`, `error_type`, and `error_message`.

`scripts/source_health.py evaluate` validates the report, emits GitHub warning annotations for one-off zero/error results, and appends a Markdown table to `GITHUB_STEP_SUMMARY` when available. On scheduled runs only, it also updates [`data/source_health_state.json`](data/source_health_state.json). Manual `workflow_dispatch` runs generate the same report and Summary but do not increment or reset persistent streaks. The state file stores only minimal streak state: consecutive zero runs, consecutive error runs, last status, last problem timestamp, and last recovered timestamp. It deliberately does not store every checked-at timestamp or per-run counts, so healthy runs do not create daily diffs.

`scripts/source_health.py gate` runs after the automated commit step and fails scheduled runs only for serious conditions: report/schema problems, configured/report source mismatch, all 14 sources returning zero or error in the same run, or the same **gate-required** source reaching 3 consecutive zero-result or error runs. Manual runs still fail for fatal report/config/all-source failures, but an existing 3-run streak alone does not fail a manual run. One or two isolated zero/error runs are warnings, not workflow failures. **Warning-only sources** (marked `gate_required=False` in `SOURCES`, currently METI) are exempt from the 3-run streak failure — their streaks are still tracked and surfaced as `::warning::` annotations / Step Summary notes, but they do not turn the workflow red; every other source remains gate-required and the all-sources-failed condition still fails regardless.

Raw fetch uses limited retry for transient network failures before a source is marked failed: timeouts, connection errors, HTTP 429, and HTTP 5xx are attempted up to 3 total tries with short backoff. Permanent 4xx responses are not retried.

When adding a source, add a stable `key` in `fetch_updates.py`, keep the existing `source_name`/URL identity behavior intact, add the English display mapping in `source_health.py`, and update the health tests. In GitHub Actions, open the run's **Summary** tab and review the **Source Health Summary** table for per-source fetched counts, new counts, latest published date, and streaks.

## Build published data (Stage 2 — provisional, rule-based)

`scripts/build_public_data.py` maps the raw items in `data/raw_items.json` into the schema the dashboard expects and writes [`docs/data/legal_updates.json`](docs/data/legal_updates.json).

> **This stage does not use AI and does not provide official translations or legal interpretation.** It is a provisional, rule-based placeholder so the dashboard can show *that* an item exists and prompt the reader to check the official Japanese source. `title_en` is a conservative rule-based English label for triage; `summary_en`, `business_impact_en`, and `recommended_action_en` are fixed template sentences. `area`, `stage`, and `impact_level` are assigned by simple keyword rules (conservative — `impact_level` never exceeds `Medium` at this stage).

**Run** (after `fetch_updates.py` has produced `data/raw_items.json`):

```
python scripts/build_public_data.py
```

Behaviour:

- **Relevance ranking.** Each item gets an internal keyword-based `relevance_score` that rewards law-reform / regulation / public-comment / guideline signals (改正, 施行, 公布, 法律 / 政令 / 省令, 意見募集, 指針 / ガイドライン, 義務, 個人情報, 金融, 労働, …) and penalises minutes, statistics, web magazines, and bare page updates. e-Gov public comments get a source bonus. Output is ordered by `relevance_score`, then impact weight (Medium > Low), then recency (older items get a light penalty so they don't linger at the top). The score is written to each record as an optional `relevance_score` field (the UI ignores unknown fields).
- **Public comment status.** Public-comment items are classified as `Public Comment Open`, `Public Comment Closed`, or `Public Comment Results Published` using title/status keywords. Closed public comments are retained as useful regulatory history but slightly demoted in ranking so open consultations and draft guidelines generally appear first; strong legal/regulatory signals can soften that demotion.
- **METI / CAA classification.** Additional keyword rules map METI and CAA items into areas such as `Economic Security / FDI`, `Energy / Environment`, `Data / Privacy / AI`, `Antitrust / Fair Trade`, `Consumer / Advertising`, and `Corporate / Governance` where the Japanese title supports that provisional classification.
- **PPC / JFTC classification.** PPC items are biased toward `Data / Privacy / AI`; JFTC items are biased toward `Antitrust / Fair Trade`. Committee meetings, recruitment, procurement, events, and public-relations-only updates are excluded or heavily downranked unless strong legal/regulatory keywords are present.
- **MLIT / MAFF classification.** MLIT helps cover land, infrastructure, transport, construction, real estate, logistics, and related regulatory updates; MAFF helps cover food, agriculture, forestry, fisheries, quarantine, import/export, and related regulatory updates. Ranking and exclusion rules intentionally filter out events, procurement, hiring, statistics-only items, and general publicity.
- **Additional area classification.** Rule-based area labels also cover `Healthcare / Pharmaceuticals`, `Food / Agriculture`, `Transport / Infrastructure`, `Real Estate / Land Use`, and `Public Safety / Disaster Management` where source titles contain clear signals.
- **Display order.** The dashboard displays records in the array order of `docs/data/legal_updates.json`. `build_public_data.py` owns the ranking/order; `docs/app.js` must not re-sort records by `published_at` or any other field.
- **Exclusion.** Drops obvious administrative noise (recruitment, procurement, bidding, events, press conferences, web magazines) and items with no net legal / regulatory signal — **unless** a strong keyword (改正 / 施行 / 案 / ガイドライン / 意見募集 / 法律 …) is present. Public-comment items are always kept.
- Backs up the current `docs/data/legal_updates.json` to `docs/data/legal_updates.backup.json` before overwriting.
- Preserves existing Stage 3 Claude summary fields when the rebuilt item has the same `id` and unchanged `source_url`; fresh build metadata such as `area`, `stage`, `impact_level`, `relevance_score`, titles, source, and dates still comes from Stage 2.
- Caps the published dataset at **1000 items**; items with no / invalid date rank last. (If the dataset eventually outgrows this, the planned enhancement is year-based archive JSON files for older data.)
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

## Simplified Chinese translation (Stage 4 — Claude)

`scripts/translate_updates.py` is the optional Stage 4 script. **English stays the canonical data**; this only adds an unofficial Simplified-Chinese (`zh-Hans`) translation under each item's `translations.zh-Hans` (`title`, `summary`, `business_impact`, `recommended_action`). It never touches `id`, `title_ja`, `source_name`, `source_url`, `area`, `stage`, `impact_level`, dates, `first_seen_at`, `relevance_score`, or any summary metadata. Items without a translation omit the block entirely, and the dashboard falls back to English per field.

> **Unofficial machine translation.** The translator runs under strict guardrails: translate the provided English faithfully and nothing else, add no obligations / deadlines / penalties / scope that are not in the English, give no legal advice, do not map Japanese legal concepts onto Chinese-law concepts, and preserve numbers, dates, institution names, and statute names. The Japanese official source remains authoritative.

**Run it after Stage 3** so it translates the final English (AI where available, rule-based otherwise):

```
python scripts/translate_updates.py --locale zh-Hans --limit 30
python scripts/translate_updates.py --locale zh-Hans --limit 30 --no-api   # apply cached translations only
```

Behaviour:

- **`--limit N` bounds NEW API calls per run, not items inspected.** The script scans the published file in order; cache hits and valid translations are free and do not consume the limit, so successive daily runs translate the whole corpus incrementally. The first bulk translation of the full corpus is a separate, deliberate operation (raise `--limit` once, off the daily schedule).
- **Cache.** [`data/translation_cache.json`](data/translation_cache.json) is `{ "schema_version": 1, "entries": { "zh-Hans": { "<id>": { source_hash, prompt_version, translated_at, model, title, summary, business_impact, recommended_action } } } }`. `source_hash` is a SHA-256 over `locale | prompt_version | title_en | summary_en | business_impact_en | recommended_action_en | title_ja | stage | source_name` (the item id is the outer key, not hashed). A cached translation is adopted only when its `source_hash` and `prompt_version` still match; a cache hit makes **no** API call and does not rewrite `translated_at`. Changing the English, the Japanese original name / stage / source, or bumping `PROMPT_VERSION` is a cache miss and re-translates.
- **Stale removal.** Each run re-checks every item against the current English and **removes** any translation that no longer matches, so the dashboard never shows a translation of outdated English. Stage 2 may carry translations forward across rebuilds, but Stage 4 is authoritative.
- **Model.** Precedence is `--model` > `ANTHROPIC_TRANSLATION_MODEL` > `ANTHROPIC_MODEL` > the same default as the summarizer (`claude-opus-4-8`).
- **No-API / no key.** `--no-api` (or a missing `ANTHROPIC_API_KEY`) applies only valid cached translations, removes stale ones, and exits 0 without calling the API.
- **Resilience.** A failed or invalid translation leaves that item in English (no translation), is logged to [`logs/translate.log`](logs/translate.log), and never stops the run. Translations must be non-empty, contain no HTML/Markdown, and stay within length caps (title ≤ 90, summary ≤ 800, business_impact / recommended_action ≤ 500) or they are rejected and not cached.
- **Title quality (prompt `zh-hans-v3`).** The current prompt version is `zh-hans-v3`, which asks for short, complete, scannable Chinese titles by stage (`公开征求意见：…`, `（已结束）公开征求意见：…`, `公开征求意见结果：…`, `指南草案：…`, `法案提交：…`). A dedicated title check rejects titles that are over 90 chars, end with or contain an ellipsis, contain Japanese kana, repeat a stage phrase or a word/fragment (e.g. `规则、则`, `修订修订`), have duplicated punctuation, unbalanced `《》`/`（）`/`()`, or line breaks, plus a small exact set of **known mistranslated statute names** (e.g. `外来入侵物种法`, `开发与雇佣适当实施及保护法`) — title-only, conservative to avoid false positives. If the title is rejected, the whole item falls back to English, is not cached, and is counted as `quality_rejected_items` (separate from `failed_items`). Bumping `PROMPT_VERSION` makes every older-version cache entry a cache miss, so the next run re-translates them.
- **Japanese reference context + dates (v3).** To keep Japan-specific statute/system names accurate, v3 sends the Japanese `title_ja` / `stage` / `source_name` to the model **as reference only** (the four English fields are still the only translation targets; the reference is never translated or returned, and only the four Chinese fields are written back). When the English and `title_ja` differ, the formal Japanese name in `title_ja` wins, then the English meaning, then Chinese brevity — without adding any legal effect not already present. After translation, numeric dates in the four Chinese fields are normalized (`YYYY/MM/DD` and `YYYY.MM.DD` → `YYYY-MM-DD`); ambiguous formats and all metadata/source fields are left untouched.

On the dashboard, a header **language selector (English / 简体中文)** switches the display. Precedence is **URL (`lang=zh-Hans`) > `localStorage` (`jlrw-language`) > English**; `lang=en` is omitted from shared URLs, switching language keeps all filters / sort / Load more state, and a translated card shows a subtle `AI翻译` badge plus an unofficial-translation note (English-fallback notice when a translation is unavailable).

## Scheduled updates (GitHub Actions)

`.github/workflows/daily-update.yml` can be run manually from the GitHub Actions tab (`workflow_dispatch`) and is scheduled daily at `0 21 * * *` UTC (06:00 JST).

The workflow uses `ubuntu-latest`, Python 3.11, installs `requirements.txt`, then runs the **offline regression tests as a gate before any network access**:

```
python -m py_compile scripts/fetch_updates.py scripts/build_public_data.py scripts/summarize_updates.py scripts/translate_updates.py scripts/source_health.py
python -m unittest discover -s tests
python scripts/fetch_updates.py
python scripts/source_health.py evaluate
python scripts/build_public_data.py
python scripts/summarize_updates.py --limit 30
python scripts/translate_updates.py --locale zh-Hans --limit 30
python scripts/source_health.py gate
```

If compilation or any test fails, the job stops there — no fetch, no rebuild, no API call, and no commit. The test steps do not receive `ANTHROPIC_API_KEY`; the secret is exposed only to the summarize and translate steps. The translate step runs after summarize and before the change check.

Configure the repository secret `ANTHROPIC_API_KEY` before relying on AI summaries or translations. If the secret is missing, both scripts exit cleanly (the translator still applies any cached translations).

The workflow commits when any tracked data artifact changes. The staged commit scope is limited to `data/raw_items.json`, `data/summary_cache.json`, `data/translation_cache.json`, `data/source_health_state.json`, and `docs/data/legal_updates.json`; generated backups and logs stay out of commits. The final source-health gate runs after this commit step, so healthy-source updates and health-state changes can be preserved before a serious source-health failure marks the workflow red.

A second, manual-only workflow [`translation-backfill.yml`](.github/workflows/translation-backfill.yml) accumulates the zh-Hans corpus **translate-only** — it never fetches, builds, summarizes, or touches source-health. It counts translations before/after, runs `translate_updates.py --locale zh-Hans --limit 30`, enforces a semantic integrity gate (cache/published must not shrink and must agree), checks `origin/main` has not advanced (aborting cleanly rather than rebasing/force-pushing), and commits only `data/translation_cache.json` and `docs/data/legal_updates.json`. Both workflows share `concurrency.group: japan-legal-reform-data-writer` so they never write data at the same time. The translator itself also fails (exit 1) if it reports translations that did not actually persist to the saved cache / published file, so a "30 translated but nothing changed" run can no longer pass silently.

The translator classifies provider (Anthropic) errors and **fails fast** on a run-fatal one — `insufficient_credit`, `authentication_error`, or `permission_error` — stopping further API calls on the first occurrence (the rest become `provider_aborted_items` / `api_calls_avoided`, not 30 repeated failures). Error bodies, request ids, API keys, and source/translation text are never logged. `--provider-failure-mode` chooses the policy: the **daily** workflow uses `warn` (a credit outage does not stop fetch/build/summarize/commit; it raises a `::warning::Translation provider unavailable: <type>` annotation and adds no translations), while the **backfill** workflow uses `fail` (a credit/auth outage exits 1 before any commit). Rate-limit / transient / network errors stay per-item (no fail-fast), and a translation of changed Japanese source is still dropped to English even during an outage. (Running out of credit is resolved on the Anthropic platform — Plans & Billing — not in this code.)

## Tests

Offline regression tests live under [`tests/`](tests/). They cover Stage 2 classification (stage / area / impact), rule-based English title generation, the Public Comment Closed ordering demotion, Stage 3 AI-summary preservation, **Stage 4 translation (cache hits, `--limit` bounding only new API calls, stale-translation removal, `--no-api` / no-key fallback, metadata invariance) and Stage 2 translation preservation**, the published-file JSON schema (including the optional `translations` contract), Source Health Monitor behavior, the Stage 1 parsers / `SOURCES` configuration, and **static checks for `docs/app.js` / `docs/i18n.js` (URL `lang` state, language-switch behavior, Chinese search, and both English and Chinese CSV column orders)**. They make **no network calls**; the published data is validated read-only.

```
python -m unittest discover -s tests     # standard library, no extra dependencies
python -m pytest tests                   # equivalent, if pytest is installed
```

When changing classification rules, ranking, or the `SOURCES` list, update these tests in the same change so the expected behavior stays pinned.

The same suite runs in the daily GitHub Actions workflow as a gate: a failure aborts the run **before** the network fetch and the Claude API step (see [Scheduled updates](#scheduled-updates-github-actions)).

## Deployment (GitHub Pages)

This project is designed to be published with **GitHub Pages set to serve from the `/docs` folder**. Everything required at runtime lives under `docs/`, so the published site is self-contained:

- `docs/index.html`, `docs/style.css`, `docs/i18n.js`, `docs/app.js` (`i18n.js` loads before `app.js`)
- `docs/data/legal_updates.json` — the data the dashboard fetches (relative path `./data/legal_updates.json`)
- `docs/legal/disclaimer_en.html`, `docs/legal/disclaimer_ja.html` — the published disclaimer pages
- `docs/.nojekyll` — disables Jekyll so all files are served verbatim

No files outside `docs/` are needed to run the published dashboard.

## Published data

The **published data file is [`docs/data/legal_updates.json`](docs/data/legal_updates.json)** — this is what the live dashboard loads. It is **generated** by `scripts/build_public_data.py` from `data/raw_items.json`; the previous version is saved to `docs/data/legal_updates.backup.json` on each Stage 2 rebuild.

If `scripts/summarize_updates.py` is run with `ANTHROPIC_API_KEY`, Stage 3 post-processes the same published file. AI-summarized records are marked `summary_source: "claude"`; non-summarized records are marked `summary_source: "rule_based"`. A file that has only gone through Stage 2 may not yet contain `summary_source`.

If `scripts/translate_updates.py` is run afterwards, Stage 4 adds an optional `translations.zh-Hans` block to translated records. English fields stay canonical and untranslated records omit the block; a file that has not gone through Stage 4 simply has no `translations`.

Display order is part of the published data contract: the browser respects the JSON array order, including after filtering and searching.

The original hand-curated sample is retained at [`data/legal_updates.json`](data/legal_updates.json) as a **schema reference only** — it is no longer kept in sync with, and no longer feeds, the published file.

## Security & data handling

All record fields are rendered through HTML escaping in `docs/app.js`, and `source_url` is **both** scheme-checked (`safeUrl()`) **and** attribute-escaped (`escapeHtml()`) before it is placed in an `href`. **Treat all ingested/source data as untrusted, and do not remove or bypass the escaping in `renderCard()` / `escapeHtml()` / `safeUrl()`.** This is the boundary that prevents a malicious or malformed source field from injecting markup once automated ingestion is added.

Card source buttons use each record's `source_url` and open the original Japanese source in a new tab. These links are part of the source-of-truth workflow: English summaries and previews are triage aids only, and the original Japanese official source remains authoritative.

## File layout

```
japan-legal-reform-watch/
├── README.md
├── CLAUDE.md                     # Notes for Claude Code working on this repo
├── requirements.txt              # Dependencies (requests, feedparser, anthropic)
├── .gitignore
├── .github/
│   └── workflows/
│       └── daily-update.yml      # Manual/daily data update workflow
├── scripts/
│   ├── fetch_updates.py          # Stage 1 raw ingestion (fetch → normalize → dedupe → log)
│   ├── build_public_data.py      # Stage 2 provisional rule-based build of the published data
│   ├── summarize_updates.py      # Stage 3 Claude AI summarization of the top-N items
│   └── translate_updates.py      # Stage 4 Claude Simplified-Chinese (zh-Hans) translation
├── tests/
│   ├── test_build_public_data.py     # Stage 2 classification / titles / ranking / AI & translation preservation
│   ├── test_published_data_schema.py # Schema checks for docs/data/legal_updates.json (read-only)
│   ├── test_translate_updates.py     # Stage 4 cache / limit / stale-removal / fallback + workflow (offline)
│   ├── test_app_js_url_state.py      # Static checks for app.js/i18n.js: URL state, language switch, CSV
│   └── test_fetch_updates.py         # Stage 1 SOURCES config, parsers, id/hash stability (offline)
├── data/
│   ├── legal_updates.json        # Original hand-curated sample (schema reference only)
│   ├── raw_items.json            # Raw fetched items (output of fetch_updates.py)
│   ├── summary_cache.json        # Claude summary cache (created/updated by Stage 3)
│   └── translation_cache.json    # zh-Hans translation cache (created/updated by Stage 4)
├── logs/
│   ├── fetch.log                 # Stage 1 ingestion run log
│   ├── summarize.log             # Stage 3 summarization run log
│   └── translate.log             # Stage 4 translation run log
├── docs/                         # ← GitHub Pages publish root (serve from /docs)
│   ├── index.html                # Dashboard entry point
│   ├── style.css
│   ├── i18n.js                   # i18n layer (window.JLRW_I18N): EN canonical + zh-Hans overlay
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
- **Paged rendering with Load more**: the public dataset can hold up to **1000 updates**, but the dashboard initially renders **50 cards** and adds 50 per **Load more updates** click — it never renders the full dataset at once. The button hides when every matching update is shown.
- **Dashboard-level trust notice** clarifying that AI summaries and rule-based previews are monitoring aids, not legal advice, and that original Japanese official sources remain authoritative.
- **Data status summary** computed client-side from the public JSON: total updates, sources represented, AI summary count, open public comment count, Newly detected count for the last 7 days, and latest checked date. It does not claim complete coverage or successful checking of every source.
- **Summary-source badges** on each card: `AI Summary` means the English summary was generated by AI but is not an official translation or legal advice; `Rule-based Preview` means the item is still using a rule-based placeholder before AI summarization.
- **Official-source buttons** on each card link to the original Japanese source.
- **Copy actions** on each card: `Copy summary` copies a plain-text English monitoring summary with the official source URL and includes `First detected by this dashboard: YYYY-MM-DD` only when a valid date exists. `Copy source link` copies the original Japanese official source URL. These are client-side UI helpers only and do not change data; the original Japanese official source remains authoritative.
- **CSV export** for the current filtered dataset. Export includes all matching updates, not only currently rendered cards, respects the current filters and sort order, uses English display labels for sources, includes official Japanese source URLs, and includes a `First detected` column. CSV is generated client-side from the public JSON as a convenience feature; original Japanese official sources remain authoritative.
- **Filters** by Area, Stage, Source, and Impact Level, plus Quick filters for Public Comment Open, AI Summary, Newly detected, Medium Impact, and Reset. Filters, quick filters, and filter options apply to the **full public dataset**, not only the currently rendered cards; any filter change (or Reset) returns the visible window to 50.
- **Sort** by Relevance, Published date, Last checked, or First detected. Sorting applies to the full filtered dataset before the 50-card render window; URL state supports `sort=relevance`, `sort=published`, `sort=checked`, and `sort=detected`. Load more state is not persisted, and Reset returns Sort to Relevance while clearing URL query parameters.
- **Mobile controls** collapse filters/search behind a compact `Filters & Search` toggle. Active filters are summarized so shared URLs remain understandable; desktop keeps the full filter layout, and the mobile open/closed state is not persisted in the URL.
- **Shareable filter URLs**: filter state is reflected in query parameters (`q`, `area`, `stage`, `source`, `impact`, `ai`, `new`, `sort`). `new=7` is the only valid Newly detected URL value. `source` uses compact slugs such as `jftc`, `moe`, `ppc`, `mlit`, `maff`, and `egov`; Load more state is not persisted, and Reset clears both filters and URL query parameters.
- **English-first source labels**: the Source filter and each card's source name display English-first labels (e.g. `Japan Fair Trade Commission (JFTC)`, `Ministry of the Environment (MOE)`) via a display-name map in `docs/app.js`. Source filter URLs use compact slugs such as `jftc`, `mlit`, and `maff`. The underlying `source_name` values in the published JSON — and the official Japanese `source_url` links — are unchanged.
- **Language selector (English / 简体中文)** in the header for an optional Simplified-Chinese display. English is canonical; Chinese is an unofficial AI translation that falls back to English per field. Precedence is URL (`lang=zh-Hans`) > `localStorage` > English; switching language preserves filters, sort, and the Load more window, and translated cards carry a subtle `AI翻译` badge and unofficial-translation note.
- **Free-text search** across English title, original Japanese title, English summary, and the Simplified-Chinese translation fields (regardless of the active UI language) — Japanese keywords such as 排除措置命令 or 食品表示, and Chinese keywords, match even when the English title does not contain them.
- **Status line** in the form `Showing X of Y matching updates · Z total updates · AI summaries: N · Last checked: D` — the AI-summary count and latest `last_checked` date cover the full filtered set, not just the rendered cards.
- **Disclaimer modal** on first visit, with acceptance persisted in `localStorage`.
- **Footer links** to Legal GPT, the Japan Legal Reform Watch landing page, Japan legal updates, and the full Legal Notice / Disclaimer at all times.

## Data schema

Each entry in `docs/data/legal_updates.json` (and its source copy `data/legal_updates.json`) has the following fields:

| Field                    | Description                                                              |
| ------------------------ | ------------------------------------------------------------------------ |
| `id`                     | Stable identifier (e.g. `jlrw-2026-001`).                                |
| `title_en`               | Unofficial English title label; rule-based at Stage 2, AI-generated only after Stage 3. |
| `title_ja`               | Original Japanese title.                                                 |
| `area`                   | Rule-based subject area (e.g. Data / Privacy / AI, Finance / AML, Healthcare / Pharmaceuticals). |
| `stage`                  | Legislative / regulatory stage (e.g. Public Comment Open, Public Comment Closed, Public Comment Results Published, Bill Submitted). |
| `impact_level`           | `High` \| `Medium` \| `Low`.                                             |
| `summary_en`             | Short factual summary in English.                                        |
| `business_impact_en`     | Short note on practical impact on businesses.                            |
| `recommended_action_en`  | Short note on suggested next steps.                                      |
| `source_name`            | Issuing authority (e.g. FSA, METI, PPC, JFTC).                           |
| `source_url`             | URL of the original Japanese source.                                     |
| `published_at`           | Date published / announced (ISO `YYYY-MM-DD`).                           |
| `first_seen_at`          | Optional date first appended to this dashboard's raw history (`YYYY-MM-DD`); absent for legacy or unknown items. |
| `last_checked`           | Date this entry was last verified (ISO `YYYY-MM-DD`).                    |
| `translations`           | Optional per-locale AI translations, e.g. `translations.zh-Hans = { title, summary, business_impact, recommended_action }` (Simplified Chinese). Unofficial aid added by Stage 4; English stays canonical and untranslated items omit the block. |

## Roadmap (not yet implemented)

- **A four-stage pipeline exists** (`fetch_updates.py` → `build_public_data.py` → `summarize_updates.py` → `translate_updates.py`), and `.github/workflows/daily-update.yml` can run it manually or daily once GitHub Secrets are configured.
- First real Stage 3 API run, review of generated summaries, and expansion beyond top-N once the guardrails are accepted.
- First bulk Stage 4 `zh-Hans` translation of the full corpus (raise `--limit` once, off the daily schedule) and review before broader use; additional UI locales only with explicit approval.
- Public hosting on GitHub Pages.

## License & use

This tool is provided free of charge for informational purposes. See [Legal Notice (EN)](legal/DISCLAIMER_EN.md) for terms, including limitation of liability.
