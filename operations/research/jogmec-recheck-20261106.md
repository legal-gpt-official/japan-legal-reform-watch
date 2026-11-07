# JOGMEC amendment recheck — 2026-11-06 (work in progress)

Dossier: `jogmec-2026-october-bill`. Queue reason: `scheduled_recheck`; English dossier last verified 2026-10-22. This is a research checkpoint, **not** a validated explainer update or publication authorization.

## Official evidence checked

- House of Representatives, 222nd Diet, Cabinet Bill No. 13, amendment to the Japan Organization for Metals and Energy Security Act: https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/keika/1DE351E.htm . As displayed on 2026-11-06, submitted 2026-10-13; the House deliberation, votes, promulgation and Act number fields are blank. Blank fields are not affirmative proof of no action elsewhere.
- Cabinet Legislation Bureau bill listing referenced in existing watch pages: https://www.clb.go.jp/recent-laws/diet_bill/detail/id=5367 . Needs fresh full-text check.
- METI law list: https://www.meti.go.jp/intro/law/index.html . Needs fresh full-text check.

## Outstanding before any stamp / merge

1. Read all three watch pages and latest ministry announcements and parliamentary records; verify the current bill text, policy background, and whether there are any subsequent deliberations or revisions.
2. Compare existing canonical dossier and translated paragraphs, including every next step and date, with confirmed primary sources.
3. If no change is found, use the repository's stamp workflow with actual verification evidence; recalculate English blob SHA and update **each** JA and zh-Hans translation's stamps only after checking semantic alignment. If changes are found, revise all three languages fully.
4. Run explainer and locale validation and all PR CI; merge only after passing. Confirm Pages deployment and three locale URLs.

Do not mark `human_reviewed`, infer an enactment date, or present this note as a published article.

## Additional primary-source verification — 2026-11-06 14:55 JST

- House of Representatives official progress page re-read: Cabinet Bill 13 in Diet 222 was received on 2026-10-13; committee referral, House votes, upper-House final reception, promulgation and Act number remain unfilled on the displayed official record. The page itself cautions that blank cells may indicate pending or definitively absent information; no affirmative conclusion from a blank alone. https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/keika/1DE351E.htm
- Cabinet Legislation Bureau bill page re-read: Cabinet decision and submission both 2026-10-13; responsible ministry METI. Its stated rationale is strengthened security of energy and metal mineral supplies in response to international conditions; proposed new JOGMEC functions include equity participation for oil transport and grants for CO2 capture, transport and storage. No enacted-law claim. https://www.clb.go.jp/recent-laws/diet_bill/detail/id=5367
- Cabinet Legislation Bureau legislative status overview (updated 2026-11-02) lists zero Cabinet bills enacted in the 222nd Diet as of that date; this is a cross-check only, not proof of the position after 2026-11-02. https://www.clb.go.jp/recent-laws/id=187
- METI legislative index returned HTTP 403 in automated retrieval. e-Gov JOGMEC Act page rendered without extractable body. These are **not** claimed to have been substantively read. https://www.meti.go.jp/intro/law/index.html ; https://laws.e-gov.go.jp/law/414AC0000000094

## Existing three-language dossier reviewed

- Canonical: data/explainers/jogmec-2026-october-bill.json (main blob e57af4b1699574edeaad66045896f0c4f39df9a1), current_stage=bill_submitted, verified_at=2026-10-22, source_fingerprint=sha256:8daab1f64ce25579b92535236157d685.
- Translation: data/explainer_locales/jogmec-2026-october-bill.json (main blob 30f259b7c441c4466eeaefaf8ad992187fe690cf). Both locale stamps match the *existing* English canonical blob, date and fingerprint; neither was independently re-stamped during this partial recheck.
- English/JA/zh-Hans each contain the same 2 background paragraphs, 1 history paragraph, 1 expected next step, 2 uncertainties, 1 business impact paragraph, 3 checklist entries and 1 key date. Meaning and legislative stage remain materially aligned with the accessible primary sources. This manual comparison does not replace executable validation.
- No legally material change confirmed in the accessible sources. **Do not refresh verified_at, claim complete review, or merge yet**: ministry updates, bill text, and the daily update job/CI need independent verification. Next operator should finish these checks, then stamp the canonical and re-synchronize each locale using its exact newly committed English blob SHA; run all required PR checks and verify Pages deployment.

## 2026-11-07 official recheck and handoff

- The Cabinet Legislation Bureau bill page was substantively read on 2026-11-07. It records the 222nd Diet, Cabinet Bill No. 13, Cabinet approval and submission on 2026-10-13, METI responsibility, and a legislative rationale of strengthening stable energy and metal-mineral supply amid international changes. The proposed JOGMEC activities include investment relating to oil transportation and grants funding CO2 capture, transport and storage. The enactment-status field is blank; this does not itself prove non-enactment. Source: https://www.clb.go.jp/recent-laws/diet_bill/detail/id=5367
- The House official progress table was read on 2026-11-07: House receipt on 2026-10-13, upper-House preliminary receipt on the same day; committee assignment, final votes and promulgation/Act number are not populated. The page explicitly says blanks may represent either not-yet-determined or definitively absent data. Source: https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/keika/1DE351E.htm
- Existing English/JA/zh-Hans dossier remains semantically consistent with these two official sources. It is **already published on main**; PR #112 is only a scheduled-recheck research checkpoint and must not be counted as a new publication. No new legislative stage is affirmatively verified.
- **State: BLOCKED_ON_PRIMARY_SOURCE_COMPLETENESS.** Before updating verified_at or synchronizing locale stamps, check the full current bill text and latest METI announcements by a readable official route; also check the daily update job. The inaccessible ministry index must not be presented as verified. If no legally relevant change is confirmed after complete checks, use native stamp, commit English, fetch actual English blob SHA, then independently update both translation stamps and validate. Do not merge this research-only PR as an explainer release.
