# Japan Legal Reform Watch by LegalOS

**Free Japan Legal & Regulatory Update Monitor.**

A free, browser-based dashboard that summarizes Japanese legal, regulatory, public-comment, and administrative announcements in English for foreign companies, in-house counsel, and compliance teams.

---

## What this is (and isn't)

- A free, English-canonical overview of Japanese legal and regulatory developments with optional Japanese and Simplified-Chinese display.
- A reference starting point — **not an official translation, and not legal advice.**
- See [Legal Notice (EN)](legal/DISCLAIMER_EN.md) and [免責事項 (JA)](legal/DISCLAIMER_JA.md).

## Current status

This repository is the **minimum viable static version**:

- The **canonical published file [`docs/data/legal_updates.json`](docs/data/legal_updates.json) is generated** from fetched data by `scripts/fetch_updates.py` (raw fetch) → `scripts/build_public_data.py` (provisional, rule-based mapping). It is uncapped and contains the complete retained public corpus. `scripts/build_public_archives.py` then creates an uncapped shard for each publication year plus an `undated` shard for efficient browser loading.
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

Searches can also be saved locally from the dashboard. Up to five filter/search definitions are stored in the same browser under `jlrw-saved-searches-v1`; they contain no email address or account data and do not create an email subscription. The dialog includes a separate paid-pilot request form. Contact details and the current search criteria are transmitted only after the visitor completes the required consent checkbox and submits the form.

### Alert-pilot integration

Public integration settings live in [`docs/alerts-config.js`](docs/alerts-config.js). The form submits multipart data to the dedicated `JLRW Alert Pilot` Contact Form 7 REST endpoint (form ID `8175`), which permits requests from the published GitHub Pages origin. Submission uses `credentials: "omit"` and `referrerPolicy: "origin"`; failure reveals a direct contact-page fallback. The form response and submitted personal fields are never logged by the dashboard. Sending the inquiry does not itself create a subscription or charge a fee; the visitor must separately choose the Stripe checkout action.

`alerts-config.js` is publicly served and must never contain an API key, Stripe secret, webhook secret, or other credential. The Pro and Team entries in `stripePaymentLinks` contain their production recurring-price Payment Links. The selected plan's trusted `https://buy.stripe.com/` checkout action appears only after the pilot inquiry is accepted and a valid request reference has been generated; if a link or reference is missing or invalid, the success message instead states that payment details will be confirmed separately. Whenever `alerts-config.js` changes, update its `?v=` cache buster in `docs/index.html` in the same commit; the regression test requires it to match the other dashboard asset versions.

Each accepted inquiry receives a locally generated, non-sensitive request reference. The same reference is included in the Contact Form 7 subject and body, displayed to the visitor, and appended to the Stripe Payment Link as `client_reference_id` for manual reconciliation. Email addresses, company names, monitoring criteria, and other personal or confidential fields are never placed in the checkout URL.

The dedicated Contact Form 7 form currently sends only the administrator notification (Mail). Mail (2) customer autoresponders remain disabled because a user-supplied recipient address can be abused unless bot protection is enforced end to end. The dashboard itself confirms request receipt and displays the request reference; payment and alert activation are confirmed separately.

The request dialog presents the current pilot scope before the form: Pro supports one monitoring criterion and one recipient; Team supports up to five monitoring criteria and five recipients. Both offer a daily or weekly digest and deadline reminders only when structured official deadline data is available. Plan cards synchronize with the form's plan selector. A required monitoring-focus field collects at least ten normalized characters describing the business topic, regulator, keywords, or activity needed to define the request; when the dashboard has no active monitoring filter, the form warns that a broad request may match hundreds of updates. After a successful request, the submitted plan and frequency remain visible and the plan controls are locked while the matching Stripe checkout link is shown. The FAQ explains the manual activation sequence and routes plan-change or cancellation requests to the LegalOS contact page without making an unconfigured refund promise.

The no-index checkout follow-up page is published at `docs/alerts/thank-you.html`. Configure Stripe Payment Link completion redirects to `https://legal-gpt-official.github.io/japan-legal-reform-watch/alerts/thank-you.html?plan=pro` for Pro and the same URL with `plan=team` for Team. The query value is allow-listed only for display; the page does not verify payment and does not claim that alert activation is automatic. It follows the dashboard language preference (`lang=ja` or `lang=zh-Hans` URL override, then `localStorage`, then English).

### Alert digest draft generator

[`scripts/generate_alert_digest.py`](scripts/generate_alert_digest.py) is a private-operations aid that turns a saved dashboard/filter URL into review-required Markdown and HTML email drafts. It applies the same supported URL filters, source slugs, period selection, multilingual search fields, Newly detected rule, and sort choices as the browser dashboard. The default delivery window uses `first_seen_at`, meaning the item was first detected by this dashboard during the requested period; it does not imply a newly enacted or amended law.

The generator does **not** send email, store customer details, call an external service, or write inside `docs/`. Every draft is marked `DRAFT — HUMAN REVIEW REQUIRED`; untrusted record text is escaped for HTML and only HTTP(S) official-source URLs become links. Keep generated drafts in a private operations directory, review every item and source URL, and remove the draft banner only when preparing the final approved email.

Example weekly run from the repository root:

```
python scripts/generate_alert_digest.py --dashboard-url "https://legal-gpt-official.github.io/japan-legal-reform-watch/?area=Data%20%2F%20Privacy%20%2F%20AI&sort=detected" --since 2026-08-04 --until 2026-08-10 --frequency weekly --max-items 10 --output-dir "C:\private\JLRW-alert-drafts"
```

Omit `--since` and `--until` to use the current Asia/Tokyo calendar day for a daily digest or the latest seven Asia/Tokyo calendar days for a weekly digest. Explicit `--since` / `--until` values continue to override those defaults. `--date-field published` is available for an explicit publication-date backfill, but routine monitoring should keep the safer `first_seen` default. When more records match than `--max-items`, the drafts state how many were omitted so the operator can review the remaining dashboard results before delivery.

## Ingestion (raw fetch — Stage 1)

`scripts/fetch_updates.py` fetches 24 curated official Japanese public-sector and self-regulatory sources and stores **raw, de-duplicated** items in [`data/raw_items.json`](data/raw_items.json). Coverage: **e-Gov Public Comment, House of Representatives current-session bills, e-Gov Law Search updated laws, JPX public comments, Tokyo Stock Exchange rule revisions, PMDA safety updates, JSDA public comments, JSDA public-comment results, recent Supreme Court decisions, SESC enforcement updates, FSA, METI, MHLW, Digital Agency, CAA, PPC, JFTC, MOJ, MOE, MOF, NTA, MIC, MLIT, and MAFF**. JPX, TSE, PMDA, JSDA, the Courts site, SESC, PPC, JFTC, MOE, MLIT, METI, NTA, and the House of Representatives use lightweight official HTML parsing; the Diet and NTA pages are Shift_JIS. NTA reads only the two current `index_news` tables on its official updates page, excludes collapsed archive tables, and retains the latest 550 days. SESC resolves its current-year page from the stable press-release archive and retains focused enforcement, market-monitoring policy, and public-comment items. The e-Gov Law adapter checks the previous seven JST dates and treats a per-date HTTP 404 as no updates. PMDA is limited to safety-category rows from the latest 550 days. The Courts adapter uses the official recent Supreme Court filter, which covers listed decisions from the past three months only and is not comprehensive case-law coverage. METI retains its escalating timeouts, backoff, requests→urllib fallback, and warning-only Source Health status. The remaining feed sources use RSS/RDF/Atom. JPO was investigated again, but its official update page remains unstable for automation (AWS WAF responses vary between blocked and empty responses); its patent-data API is not an agency-news or regulatory-update feed. JPO therefore remains deferred until a stable, focused official feed or API is available. Stage 1 performs fetching, normalization, de-duplication, and logging **only**; it does not summarize, call an LLM, or modify the published JSON.

`data/raw_items.json` is the **accumulated source history**: re-runs only append genuinely new items, and the file is never trimmed. New items appended after the Newly detected feature is deployed receive `first_seen_at` as the current Asia/Tokyo date (`YYYY-MM-DD`). Existing legacy items are not backfilled, and missing `first_seen_at` means the first detection date is unknown.

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

Each raw item has: `id`, `title_ja`, `source_name`, `source_url`, `published_at` (ISO; empty string when the source gives no date — never guessed), `fetched_at`, `source_language`, `raw_summary`, `raw_content_hash`, and `source_type`. Newer records may also have optional `first_seen_at`, a controlled `stage_hint` from the Diet, e-Gov Law, PMDA, JSDA-results, Courts, or SESC adapters, and a structured `comment_deadline` from e-Gov RSS metadata or the JPX/JSDA public-comment tables. Diet, e-Gov Law, and recurring PMDA records use a source-specific event identity so a genuine lifecycle/content-date change can be appended while the official `source_url` remains unchanged; URL de-duplication remains unchanged for all other sources.

Deadlines inherited from a related e-Gov item are a Stage 2 public-data
derivation only. They are never written back to `data/raw_items.json`.

## Newly detected

`first_seen_at` means "First detected by this dashboard" and is assigned only when a raw item is actually appended as new under the existing ID/source URL merge rules. It is not a legal status and must not be described as a new law, new regulation, recently enacted law, recently amended rule, or breaking legal update.

Legacy records without `first_seen_at` are treated as unknown and are not shown as Newly detected. Local tests that do not run `fetch_updates.py` may therefore show `Newly detected (7d): 0`; that is expected.

The public dashboard treats an item as Newly detected when `first_seen_at` is a valid non-future `YYYY-MM-DD` within the last 7 calendar days including today. The Quick filter uses URL parameter `new=7`; other `new` values are ignored. Sort supports `sort=detected` for First detected order. CSV export includes a `First detected` column, blank for legacy or invalid dates.

## Source Health Monitor

The Source Health Monitor is an administrator-facing GitHub Actions check. It is designed to detect silent per-source fetch failures, especially for official HTML parsers such as MOE and MLIT. It is **not** displayed in `docs/app.js`, public dashboard cards, CSV export, URL state, or the Data status UI.

Health is based on raw source fetch results from `scripts/fetch_updates.py`, not on how many records survive Stage 2 publication filtering. For example, if MOJ returns 10 raw parsed items but all are later excluded as administrative noise, source fetch health is still healthy and published items may be 0.

`scripts/fetch_updates.py` writes [`logs/source_fetch_report.json`](logs/source_fetch_report.json) after each run. This transient run report includes `schema_version`, run timestamps, configured source count, and one row per source with `source_key`, `source_name`, `source_url`, `status`, `fetched_count`, `new_count`, `latest_published_at`, `duration_ms`, `error_type`, and `error_message`.

`scripts/source_health.py evaluate` validates the report, emits GitHub warning annotations for one-off zero/error results, and appends a Markdown table to `GITHUB_STEP_SUMMARY` when available. On scheduled runs only, it also updates [`data/source_health_state.json`](data/source_health_state.json). Manual `workflow_dispatch` runs generate the same report and Summary but do not increment or reset persistent streaks. The state file stores only minimal streak state: consecutive zero runs, consecutive error runs, last status, last problem timestamp, and last recovered timestamp. It deliberately does not store every checked-at timestamp or per-run counts, so healthy runs do not create daily diffs.

`scripts/source_health.py gate` runs after the automated commit step and fails scheduled runs only for serious conditions: report/schema problems, configured/report source mismatch, all 24 sources returning zero or error in the same run, or the same **gate-required** source reaching 3 consecutive zero-result or error runs. Manual runs still fail for fatal report/config/all-source failures, but an existing 3-run streak alone does not fail a manual run. If a newly configured source is absent from the previous state file, a manual gate adds its default state in memory only, without changing persistent streaks. One or two isolated zero/error runs are warnings, not workflow failures. **Warning-only sources** (marked `gate_required=False` in `SOURCES`, currently METI) are exempt from the 3-run streak failure — their streaks are still tracked and surfaced as `::warning::` annotations / Step Summary notes, but they do not turn the workflow red; every other source remains gate-required and the all-sources-failed condition still fails regardless.

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
- **Public comment status.** Public-comment items are classified as `Public Comment Open`, `Public Comment Closed`, or `Public Comment Results Published` using title/status keywords. When an Open e-Gov item has a valid structured `comment_deadline`, Stage 2 changes it to Closed at that deadline instant. Stage 2 may also derive that deadline for a duplicate ministry/regulator record, but only when the conservatively normalized Japanese title is exact, publication dates are within one day, the e-Gov contact agency matches the target's configured official source name and domain, both sides are unique, and no deadline conflicts or independent target deadline exist. It never uses fuzzy title similarity. Date-only deadlines remain Open through 23:59:59 JST; explicit date-times retain their stated offset. Missing, invalid, ambiguous, or conflicting deadlines never cause a guessed closure, and raw history is not modified. Closed public comments are retained as useful regulatory history but slightly demoted in ranking so open consultations and draft guidelines generally appear first; strong legal/regulatory signals can soften that demotion.
- **METI / CAA classification.** Additional keyword rules map METI and CAA items into areas such as `Economic Security / FDI`, `Energy / Environment`, `Data / Privacy / AI`, `Antitrust / Fair Trade`, `Consumer / Advertising`, and `Corporate / Governance` where the Japanese title supports that provisional classification.
- **PPC / JFTC classification.** PPC items are biased toward `Data / Privacy / AI`; JFTC items are biased toward `Antitrust / Fair Trade`. Committee meetings, recruitment, procurement, events, and public-relations-only updates are excluded or heavily downranked unless strong legal/regulatory keywords are present.
- **MLIT / MAFF classification.** MLIT helps cover land, infrastructure, transport, construction, real estate, logistics, and related regulatory updates; MAFF helps cover food, agriculture, forestry, fisheries, quarantine, import/export, and related regulatory updates. Ranking and exclusion rules intentionally filter out events, procurement, hiring, statistics-only items, and general publicity.
- **Additional area classification.** Rule-based area labels also cover `Healthcare / Pharmaceuticals`, `Food / Agriculture`, `Transport / Infrastructure`, `Real Estate / Land Use`, and `Public Safety / Disaster Management` where source titles contain clear signals.
- **Display order.** `build_public_data.py` owns the canonical relevance ranking/order. The dashboard defaults to Published date sorting and also offers Relevance, Last checked, and First detected; Relevance preserves the build-owned array order.
- **Exclusion.** Drops obvious administrative noise (recruitment, procurement, bidding, events, press conferences, web magazines) and items with no net legal / regulatory signal — **unless** a strong keyword (改正 / 施行 / 案 / ガイドライン / 意見募集 / 法律 …) is present. Public-comment items are always kept.
- Backs up the current `docs/data/legal_updates.json` to `docs/data/legal_updates.backup.json` before overwriting.
- Preserves existing Stage 3 Claude summary fields when the rebuilt item has the same `id` and unchanged `source_url`; fresh build metadata such as `area`, `stage`, `impact_level`, `relevance_score`, titles, source, and dates still comes from Stage 2.
- Keeps the canonical published dataset **uncapped**. Items with no / invalid date rank last. `--limit N` remains available only as an explicit diagnostic option; the default `--limit 0` means unlimited.
- Prints a console summary including input/candidate/output counts, direct and inherited deadline counts, inherited Open/Closed counts, ambiguous/conflicting/invalid related matches, unmatched Open public comments, preservation counts, ranking bounds, backup state, and output path.
- Keeps `source_url` verbatim and treats all input as untrusted (the browser escapes every field on render).

The `relevance_score` is a **technical heuristic to decide what to surface — not a legal judgement** of importance. The output remains a provisional, AI-free preview: `summary_en` explicitly states it "has not yet been reviewed or summarized by AI."

## Yearly public archives (post-processing)

After Stage 3 summarization and Stage 4 translation, run:

```
python scripts/build_public_archives.py
```

This deterministic step reads the uncapped canonical `docs/data/legal_updates.json` and writes [`docs/data/legal_updates_manifest.json`](docs/data/legal_updates_manifest.json) plus `docs/data/archive/YYYY.json` and, when needed, `docs/data/archive/undated.json`. There is **no per-year item limit**. The manifest has no generated timestamp, so an unchanged corpus does not create a daily diff.

The dashboard loads only the latest year initially. Its **Period** selector can load any archived year, `Undated`, or `All years (slower)`. Search, filters, sorting, Load more, Data status counts, and CSV export apply to the selected period. Non-default selections are shareable through `year=YYYY`, `year=undated`, or `year=all`; Reset returns to the latest year. Selecting All years loads the uncapped canonical file, so it can become slower as the corpus grows.

## AI summarization (Stage 3 — Claude)

`scripts/summarize_updates.py` is the optional Stage 3 script. When run with `ANTHROPIC_API_KEY`, it considers the **top-N items by `relevance_score`** (default 10) and writes English `title_en`, `summary_en`, `business_impact_en`, and `recommended_action_en` back into [`docs/data/legal_updates.json`](docs/data/legal_updates.json). `--api-limit` can separately cap cache-miss API calls within that pool; cache hits remain free. Items it summarizes are marked `summary_source: "claude"` (and gain `summarized_at`, `summary_model`, `confidence`, `ai_notes`); untouched, budget-skipped, or failed items keep the rule-based template and are marked `summary_source: "rule_based"`. If Stage 3 has not yet been run, `summary_source` may be absent. `source_url` and `id` are never changed.

> **Still not legal advice.** The model runs under strict guardrails: no invented facts, no legal advice or definitive compliance recommendations, no claiming a law is enacted / promulgated / in force unless the provided source text clearly supports it, public comments / drafts / proposals / consultations / draft guidelines / government announcements labelled as such, and the official Japanese source treated as authoritative. `area`, `stage`, and `impact_level` are preliminary rule-based labels and must not be treated as legally verified conclusions. The result is an unofficial summary to verify against the primary source.

**Set your API key** — it is read **only** from the environment and is never stored in the repo:

```
# PowerShell
$env:ANTHROPIC_API_KEY = "<your-anthropic-api-key>"
# bash / zsh
read -s ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY
```

If the key is not set, the script prints usage and exits cleanly without calling the API. The key is read only from `ANTHROPIC_API_KEY`; it is never written to code, logs, cache, or documentation.

**Optional model override** — the default summary model remains `claude-opus-4-8` for English summary quality, but you can override it without changing code:

```
# PowerShell
$env:ANTHROPIC_SUMMARY_MODEL = "your-summary-model-id"
# bash / zsh
export ANTHROPIC_SUMMARY_MODEL="your-summary-model-id"
```

**Run** (after `build_public_data.py` has produced the published file):

```
python -m pip install -r requirements.txt                # installs the anthropic SDK
python scripts/summarize_updates.py --limit 10           # summarize the top 10
python scripts/summarize_updates.py --limit 100 --api-limit 30 --batch  # wider pool, bounded new calls
python scripts/summarize_updates.py --all-items --japanese-only --api-limit 10 --parallel 1 --max-cost-usd 0.50  # measured-cost canary
python scripts/summarize_updates.py --limit 3 --dry-run   # preview without writing
```

Behaviour:

- **Baseline snapshot.** The pre-AI file is snapshotted to `docs/data/legal_updates.before_ai.json` **once**, on the first non-dry-run Stage 3 execution. This is an initial pre-AI baseline, not a per-run backup. Stage 2 still creates `docs/data/legal_updates.backup.json` before each rule-based rebuild.
- **Cache.** Successful summaries are cached in [`data/summary_cache.json`](data/summary_cache.json), keyed by item `id` + content hash. An unchanged English or Japanese result is never regenerated. Japanese fields are generated directly from `title_ja` / Japanese `raw_summary`, preserve the English text, and never replace `title_ja`. Their independent `summary_ja_source`, `ja_summarized_at`, and `ja_summary_model` provenance allows a Japanese AI summary to coexist truthfully with a rule-based English preview.
- **Resilience.** A failed API call leaves that item's rule-based copy intact (`summary_source: "rule_based"`) and is logged to [`logs/summarize.log`](logs/summarize.log); one failure never stops the run.
- **Validation.** Before writing, the output is checked: required UI fields present and non-empty, `confidence` is `high` / `medium` / `low`, and `id` / `source_url` unchanged. Obvious definitive/legal-advice phrases such as "you must comply", "is legally required", "has been enacted", "is in force", and "will definitely" are logged as caution warnings for review.
- Console summary: `input_items`, `target_items`, `cache_hits`, `api_calls`, `summarized_items`, `failed_items`, `caution_warnings`, `output_path`, `backup_created`.

The default summary model is `claude-opus-4-8` (override with `--model` or `ANTHROPIC_SUMMARY_MODEL`; legacy `ANTHROPIC_MODEL` is still accepted at lower priority). All input is treated as untrusted: item metadata is sent to the model clearly delimited as data, with an explicit instruction never to follow instructions embedded in it.

## Japanese source summaries (Stage 3) and Simplified-Chinese translation (Stage 4)

Japanese mode does not translate the English dashboard record. It displays the official `title_ja` and the three Stage 3 `*_ja` fields summarized directly from Japanese source metadata. During an incomplete or failed backfill, only the still-missing item falls back to English and is described as “not yet generated,” not as unsupported.

`scripts/translate_updates.py` remains the optional **Simplified-Chinese-only** Stage 4 script. English stays canonical; it adds `translations.zh-Hans` (`title`, `summary`, `business_impact`, `recommended_action`) and never writes `translations.ja` or modifies the Stage 3 Japanese fields.

> **Unofficial machine translation.** The translator runs under strict guardrails: translate the provided English faithfully and nothing else, add no obligations / deadlines / penalties / scope that are not in the English, give no legal advice, do not map Japanese legal concepts onto Chinese-law concepts, and preserve numbers, dates, institution names, and statute names. The Japanese official source remains authoritative.

**Run it after Stage 3** so it translates the final English (AI where available, rule-based otherwise):

```
python scripts/translate_updates.py --locale zh-Hans --limit 30
python scripts/translate_updates.py --locale zh-Hans --limit 30 --no-api   # apply cached translations only
```

Behaviour:

- **`--limit N` bounds NEW API calls per run, not items inspected.** The script scans the published file in order; cache hits and valid translations are free and do not consume the limit, so successive daily runs translate the whole corpus incrementally. The first bulk translation of the full corpus is a separate, deliberate operation (raise `--limit` once, off the daily schedule).
- **Cache.** [`data/translation_cache.json`](data/translation_cache.json) stores `entries.zh-Hans`. Each item entry contains `{ source_hash, prompt_version, translated_at, model, title, summary, business_impact, recommended_action }`. `source_hash` includes the locale, prompt version, canonical English fields, and Japanese reference context. A matching cache hit makes no API call and does not rewrite `translated_at`.
- **Stale removal.** Each run re-checks every item against the current English and **removes** any translation that no longer matches, so the dashboard never shows a translation of outdated English. Stage 2 may carry translations forward across rebuilds, but Stage 4 is authoritative.
- **Model.** Translation resolves the model as `--model` > `ANTHROPIC_TRANSLATION_MODEL` > `DEFAULT_TRANSLATION_MODEL` (code default `claude-haiku-4-5-20251001`; `ANTHROPIC_MODEL` is not consulted for translation). The daily and backfill zh-Hans steps use Sonnet 5 while summarization stays on Opus 4.8. The model is stored per cache entry but is not part of `source_hash`; switching models does not invalidate valid cached translations. Translation disables thinking and sends no sampling parameters.
- **No-API / no key.** `--no-api` (or a missing `ANTHROPIC_API_KEY`) applies only valid cached translations, removes stale ones, and exits 0 without calling the API.
- **Resilience.** A failed or invalid translation leaves that item in English (no translation), is logged to [`logs/translate.log`](logs/translate.log), and never stops the run. Translations must be non-empty, contain no HTML/Markdown, and stay within length caps (title ≤ 90, summary ≤ 800, business_impact / recommended_action ≤ 500) or they are rejected and not cached.
- **Title quality (prompt `zh-hans-v3`).** The current prompt version is `zh-hans-v3`, which asks for short, complete, scannable Chinese titles by stage (`公开征求意见：…`, `（已结束）公开征求意见：…`, `公开征求意见结果：…`, `指南草案：…`, `法案提交：…`). A dedicated title check rejects titles that are over 90 chars, end with or contain an ellipsis, contain Japanese kana, repeat a stage phrase or a word/fragment (e.g. `规则、则`, `修订修订`), have duplicated punctuation, unbalanced `《》`/`（）`/`()`, or line breaks, plus a small exact set of **known mistranslated statute names** (e.g. `外来入侵物种法`, `开发与雇佣适当实施及保护法`) — title-only, conservative to avoid false positives. If the title is rejected, the whole item falls back to English, is not cached, and is counted as `quality_rejected_items` (separate from `failed_items`). Bumping `PROMPT_VERSION` makes every older-version cache entry a cache miss, so the next run re-translates them.
- **Japanese reference context + dates (v3).** To keep Japan-specific statute/system names accurate, v3 sends the Japanese `title_ja` / `stage` / `source_name` to the model **as reference only** (the four English fields are still the only translation targets; the reference is never translated or returned, and only the four Chinese fields are written back). When the English and `title_ja` differ, the formal Japanese name in `title_ja` wins, then the English meaning, then Chinese brevity — without adding any legal effect not already present. After translation, numeric dates in the four Chinese fields are normalized (`YYYY/MM/DD` and `YYYY.MM.DD` → `YYYY-MM-DD`); ambiguous formats and all metadata/source fields are left untouched.

On the dashboard, a header **language selector (English / 日本語 / 简体中文)** switches the display. Precedence is **URL (`lang=ja` or `lang=zh-Hans`) > `localStorage` (`jlrw-language`) > English**. Japanese cards identify source-based AI summaries and fall back to English when unavailable; Chinese cards show the AI-translation notice.

## Scheduled updates (GitHub Actions)

`.github/workflows/daily-update.yml` can be run manually from the GitHub Actions tab (`workflow_dispatch`) and is scheduled daily at `0 21 * * *` UTC (06:00 JST).

The workflow uses `ubuntu-latest`, Python 3.11, installs `requirements.txt`, then runs the **offline regression tests as a gate before any network access**:

```
python -m py_compile scripts/fetch_updates.py scripts/build_public_data.py scripts/summarize_updates.py scripts/translate_updates.py scripts/source_health.py
python -m unittest discover -s tests
python scripts/fetch_updates.py
python scripts/source_health.py evaluate
python scripts/build_public_data.py
python scripts/summarize_updates.py --limit 100 --api-limit 30 --batch
python scripts/translate_updates.py --locale zh-Hans --limit 30
python scripts/source_health.py gate
```

If compilation or any test fails, the job stops there — no fetch, no rebuild, no API call, and no commit. The test steps do not receive `ANTHROPIC_API_KEY`; the secret is exposed only to the summarize and translate steps. The translate step runs after summarize; yearly archive generation then runs before the change check.

Configure the repository secret `ANTHROPIC_API_KEY` before relying on AI summaries or translations. If the secret is missing, both scripts exit cleanly (the translator still applies any cached translations).

The workflow commits when any tracked data artifact changes. The staged commit scope is limited to `data/raw_items.json`, `data/summary_cache.json`, `data/translation_cache.json`, `data/source_health_state.json`, and `docs/data/legal_updates.json`; generated backups and logs stay out of commits. After a data commit is pushed, the workflow explicitly requests a GitHub Pages build from the latest default-branch revision so bot-authored daily updates are published without requiring another commit. The final source-health gate runs after the commit and Pages-request steps, so healthy-source updates and health-state changes can be preserved before a serious source-health failure marks the workflow red.

A second, manual-only workflow [`translation-backfill.yml`](.github/workflows/translation-backfill.yml) accumulates the zh-Hans corpus **translate-only** — it never fetches, runs Stage 2, summarizes, or touches source-health. It counts translations before/after, runs `translate_updates.py --locale zh-Hans --limit 30`, and enforces a semantic integrity gate: neither artifact may shrink, and every published translation must match a valid current-hash cache entry. Cache-only stale/history entries are allowed because changed English source text invalidates publication without requiring destructive cache cleanup. It then regenerates deterministic yearly browser shards, checks `origin/main` has not advanced, and commits only translation-derived artifacts. Both workflows share `concurrency.group: japan-legal-reform-data-writer`.

The manual [`japanese-summary-backfill.yml`](.github/workflows/japanese-summary-backfill.yml) workflow fills the complete published corpus without translating English. Each dispatch is one bounded checkpoint with explicit call-count, parallelism, and measured estimated-cost caps; its safe defaults are a 10-item, single-call canary capped at USD 0.50. Successful responses report input/output tokens and estimated USD cost before the checkpoint is committed. It rebuilds browser archives and verifies that every published item has complete Japanese content plus independent Claude provenance before the workflow can finish successfully. Cache hits are free, so interrupted runs resume without repeating completed items. It shares the same data-writer concurrency group.

The translator classifies provider (Anthropic) errors and **fails fast** on a run-fatal one — `insufficient_credit`, `authentication_error`, or `permission_error` — stopping further API calls on the first occurrence (the rest become `provider_aborted_items` / `api_calls_avoided`, not 30 repeated failures). Error bodies, request ids, API keys, and source/translation text are never logged. `--provider-failure-mode` chooses the policy: the **daily** workflow uses `warn` (a credit outage does not stop fetch/build/summarize/commit; it raises a `::warning::Translation provider unavailable: <type>` annotation and adds no translations), while the **backfill** workflow uses `fail` (a credit/auth outage exits 1 before any commit). Rate-limit / transient / network errors stay per-item (no fail-fast), and a translation of changed Japanese source is still dropped to English even during an outage. (Running out of credit is resolved on the Anthropic platform — Plans & Billing — not in this code.)

## Tests

Offline regression tests live under [`tests/`](tests/). They cover Stage 2 classification, rule-based English title generation, Stage 3 English-cache preservation and Japanese-source enrichment, Stage 4 zh-Hans translation/cache behavior, the published-file schema, Source Health Monitor behavior, Stage 1 adapters, URL language state, multilingual search, and the pinned English/Japanese/Chinese CSV layouts (16/16/17 columns). They make no network calls; the published data is validated read-only.

```
python -m unittest discover -s tests     # standard library, no extra dependencies
python -m pytest tests                   # equivalent, if pytest is installed
```

When changing classification rules, ranking, or the `SOURCES` list, update these tests in the same change so the expected behavior stays pinned.

The same suite runs in the daily GitHub Actions workflow as a gate: a failure aborts the run **before** the network fetch and the Claude API step (see [Scheduled updates](#scheduled-updates-github-actions)).

## Deployment (GitHub Pages)

This project is designed to be published with **GitHub Pages set to serve from the `/docs` folder**. Everything required at runtime lives under `docs/`, so the published site is self-contained:

- `docs/index.html`, `docs/style.css`, `docs/i18n.js`, `docs/alerts-config.js`, `docs/app.js` (`i18n.js` and the public alert configuration load before `app.js`)
- `docs/data/legal_updates.json` — the data the dashboard fetches (relative path `./data/legal_updates.json`)
- `docs/legal/disclaimer_en.html`, `docs/legal/disclaimer_ja.html` — the published disclaimer pages
- `docs/.nojekyll` — disables Jekyll so all files are served verbatim

No files outside `docs/` are needed to run the published dashboard.

## Published data

The **published data file is [`docs/data/legal_updates.json`](docs/data/legal_updates.json)** — this is what the live dashboard loads. It is **generated** by `scripts/build_public_data.py` from `data/raw_items.json`; the previous version is saved to `docs/data/legal_updates.backup.json` on each Stage 2 rebuild.

If `scripts/summarize_updates.py` is run with `ANTHROPIC_API_KEY`, Stage 3 post-processes the same published file. AI-summarized records are marked `summary_source: "claude"`; non-summarized records are marked `summary_source: "rule_based"`. A file that has only gone through Stage 2 may not yet contain `summary_source`.

If `scripts/translate_updates.py` is run afterwards, Stage 4 adds an optional `translations.zh-Hans` block to translated records. Japanese Stage 3 summaries remain top-level `*_ja` fields; Stage 4 never creates `translations.ja`.

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
│   ├── summarize_updates.py      # Stage 3 Claude English/Japanese-source summarization
│   ├── translate_updates.py      # Stage 4 Claude Simplified-Chinese translation
│   └── generate_alert_digest.py  # Review-required Markdown/HTML alert draft generator
├── tests/
│   ├── test_build_public_data.py     # Stage 2 classification / titles / ranking / AI & translation preservation
│   ├── test_published_data_schema.py # Schema checks for docs/data/legal_updates.json (read-only)
│   ├── test_translate_updates.py     # Stage 4 cache / limit / stale-removal / fallback + workflow (offline)
│   ├── test_app_js_url_state.py      # Static checks for app.js/i18n.js: URL state, language switch, CSV
│   ├── test_generate_alert_digest.py  # Alert URL filtering, windowing, escaping, and draft output
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
│   ├── i18n.js                   # i18n layer: EN canonical + ja / zh-Hans overlays
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
- **Year-scoped loading and paged rendering**: the canonical public JSON and every yearly archive are uncapped. The dashboard loads the latest year by default, then initially renders **50 cards** and adds 50 per **Load more updates** click — it never renders every matching card at once. The Period selector can load another year, undated records, or the complete all-years corpus; the button hides when every matching update in that selection is shown.
- **Dashboard-level trust notice** clarifying that AI summaries and rule-based previews are monitoring aids, not legal advice, and that original Japanese official sources remain authoritative.
- **Data status summary** computed client-side from the public JSON: total updates, sources represented, AI summary count, open public comment count, Newly detected count for the last 7 days, and latest checked date. It does not claim complete coverage or successful checking of every source.
- **Summary-source badges** on each card: `AI Summary` means the English summary was generated by AI but is not an official translation or legal advice; `Rule-based Preview` means the item is still using a rule-based placeholder before AI summarization.
- **Official-source buttons** on each card link to the original Japanese source.
- **Copy actions** on each card: `Copy summary` copies a plain-text English monitoring summary with the official source URL and includes `First detected by this dashboard: YYYY-MM-DD` only when a valid date exists. `Copy source link` copies the original Japanese official source URL. These are client-side UI helpers only and do not change data; the original Japanese official source remains authoritative.
- **CSV export** for the current filtered dataset. Export includes all matching updates, not only currently rendered cards, respects the current filters and sort order, uses English display labels for sources, includes official Japanese source URLs, and includes a `First detected` column. CSV is generated client-side from the public JSON as a convenience feature; original Japanese official sources remain authoritative.
- **Filters** by Area, Stage, Source, and Impact Level, plus Quick filters for Public Comment Open, AI Summary, Newly detected, Medium Impact, and Reset. Filters, quick filters, and filter options apply to the **full public dataset**, not only the currently rendered cards; any filter change (or Reset) returns the visible window to 50.
- **Sort** by Relevance, Published date, Last checked, or First detected. Published date is the default so the newest official updates appear first; Relevance preserves the composite ranking generated by `build_public_data.py`, including stage, impact, and recency adjustments. Sorting applies to the full filtered dataset before the 50-card render window; URL state supports `sort=relevance`, `sort=published`, `sort=checked`, and `sort=detected`. Load more state is not persisted, and Reset returns Sort to Published date while clearing URL query parameters.
- **Mobile controls** collapse filters/search behind a compact `Filters & Search` toggle. Active filters are summarized so shared URLs remain understandable; desktop keeps the full filter layout, and the mobile open/closed state is not persisted in the URL.
- **Shareable filter URLs**: filter state is reflected in query parameters (`q`, `area`, `stage`, `source`, `impact`, `ai`, `new`, `sort`). `new=7` is the only valid Newly detected URL value. `source` uses compact slugs such as `jftc`, `moe`, `ppc`, `mlit`, `maff`, and `egov`; Load more state is not persisted, and Reset clears both filters and URL query parameters.
- **Saved searches and alert-pilot request**: up to five current filter/search definitions can be named, stored in `localStorage`, restored, and deleted. Values are rendered with DOM `textContent`; the saved-search feature stores no personal data and does not imply that an email alert or account has been created. A separate consent-gated form can submit the visitor's contact details and current monitoring criteria to the existing Legal GPT inquiry endpoint, with a contact-page fallback and optional post-acceptance Stripe Payment Link.
- **English-first source labels**: the Source filter and each card's source name display English-first labels (e.g. `Japan Fair Trade Commission (JFTC)`, `Ministry of the Environment (MOE)`) via a display-name map in `docs/app.js`. Source filter URLs use compact slugs such as `jftc`, `mlit`, and `maff`. The underlying `source_name` values in the published JSON — and the official Japanese `source_url` links — are unchanged.
- **Language selector (English / 日本語 / 简体中文)** in the header. Japanese mode uses the official original title and optional Stage 3 Japanese-source summaries; Chinese uses unofficial Stage 4 translations. Missing localized body fields fall back to English. Precedence is URL (`lang=ja` or `lang=zh-Hans`) > `localStorage` > English; switching language preserves filters, sort, and the Load more window.
- **Free-text search** always covers the English title, original Japanese title, and Simplified-Chinese title. English AI summaries and their Simplified-Chinese summary / business-impact / recommended-action fields are searchable when `summary_source` is `claude`; complete Japanese `*_ja` summaries are searchable independently of the English provenance. Rule-based placeholder bodies are excluded.
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
| `stage`                  | Legislative / regulatory / enforcement / judicial stage (e.g. Public Comment Open, Public Comment Results Published, Bill Submitted, Enforcement Action, Court Decision). |
| `impact_level`           | `High` \| `Medium` \| `Low`.                                             |
| `summary_en`             | Short factual summary in English.                                        |
| `business_impact_en`     | Short note on practical impact on businesses.                            |
| `recommended_action_en`  | Short note on suggested next steps.                                      |
| `summary_ja`             | Stage 3 AI summary generated directly from Japanese source metadata; all three `*_ja` fields appear together. |
| `business_impact_ja`     | Optional tentative business-impact summary generated directly from Japanese source metadata. |
| `recommended_action_ja`  | Optional cautious review suggestion generated directly from Japanese source metadata. |
| `summary_ja_source`      | Independent Japanese-summary provenance (`claude`); does not relabel a rule-based English preview. |
| `ja_summarized_at`       | UTC timestamp for the Japanese AI summary. |
| `ja_summary_model`       | Model used for the Japanese AI summary. |
| `source_name`            | Issuing authority (e.g. FSA, METI, PPC, JFTC).                           |
| `source_url`             | URL of the original Japanese source.                                     |
| `published_at`           | Date published / announced (ISO `YYYY-MM-DD`).                           |
| `first_seen_at`          | Optional date first appended to this dashboard's raw history (`YYYY-MM-DD`); absent for legacy or unknown items. |
| `comment_deadline`       | Optional structured public-comment deadline (ISO date or offset date-time). Used only to close an otherwise Open item after the deadline; absent when no trusted value exists. |
| `comment_deadline_source` | Optional deadline provenance: `source_metadata` for the item's own structured metadata or `related_egov_item` for a conservatively matched e-Gov record. |
| `comment_deadline_source_id` | Present only for an inherited deadline; stable ID of the related e-Gov source record. |
| `comment_deadline_inherited` | Optional boolean provenance flag; `true` only when the deadline came from a related e-Gov item. |
| `last_checked`           | Date this entry was last verified (ISO `YYYY-MM-DD`).                    |
| `translations`           | Optional `translations.zh-Hans` AI translation with `{ title, summary, business_impact, recommended_action }`. Stage 4 never writes `translations.ja`. |

## Operating status and roadmap

The four-stage pipeline (`fetch_updates.py` → `build_public_data.py` → `summarize_updates.py` → `translate_updates.py`) plus deterministic yearly archive generation (`build_public_archives.py`) is live in the daily GitHub Actions workflow. Stage 3 AI summaries and Stage 4 `zh-Hans` translations are present in the published corpus, with English remaining canonical and per-field fallback for untranslated items. Daily summarization uses a selective relevance-ranked pool of 100 items with at most 30 new Opus calls; cache hits are free, so the pool fills incrementally without turning the full archive into AI-generated analysis. The dashboard is publicly hosted on GitHub Pages from `/docs`, and a successful daily data commit explicitly requests a Pages build from the latest `main` revision.

Current roadmap status:

- The initial `zh-Hans` backfill, including the NTA source expansion, is complete. Daily Stage 4 runs continue incremental maintenance for new or changed items; if a future translation fails the quality gate, the documented English fallback remains in place until a later valid translation is produced.
- The relevance-ranked 100-item AI-summary pool has been reviewed for source balance and triage quality. It remains relevance-only: forced source quotas could promote lower-signal updates. Re-review it periodically before expanding the pool, and do not bulk-summarize the full archive by default.
- NTA has been added through its stable official current-updates page with a bounded 550-day window. JPO remains deferred until a stable, sufficiently focused official feed or API becomes available.
- The application-level corpus and yearly shards remain uncapped, but the hosting platform still has file and site limits. Start the next storage-design review before either the canonical JSON reaches **20,000 items** or `docs/data/legal_updates.json` reaches **40 MiB**. The intended next step is to stop publishing a duplicated all-years file and assemble the all-years view from yearly shards (or move the canonical artifact to external storage), before GitHub's [50 MiB file warning / 100 MiB hard block](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github) or the [1 GiB Pages site limit](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) becomes relevant.
- Do not add another UI locale without explicit approval and a reviewable translation workflow.

## License & use

This tool is provided free of charge for informational purposes. See [Legal Notice (EN)](legal/DISCLAIMER_EN.md) for terms, including limitation of liability.
