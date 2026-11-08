# Resume memo — Antarctic partial commencement (2026-11-06)

Existing PR: https://github.com/legal-gpt-official/japan-legal-reform-watch/pull/98
Head: af9f2a64baee3e78fb9448692cfc67103e27684b (before this memo). Base main has advanced; GitHub reports mergeable=false. The PR's earlier Pull request checks run 41153099903 succeeded, but it is stale relative to current main and cannot justify merging.

Primary-source verification on 2026-11-06: https://www.env.go.jp/nature/nankyoku/page_00529.html explicitly says the sea-only advance procedures commenced on 2026-06-30; Annex VI-dependent provisions commence one month after Annex VI enters into force for Japan, and their commencement date remains undetermined. The ministry also reports a September 2026 implementing ordinance, which must be assessed before publication. Do not represent Annex VI obligations as already operative.

Next actions: (1) rebase/recreate changes from PR #98 on latest main, resolving conflicts in scripts/explainers.py, tests, English dossier and both translations; (2) check whether the September ordinance requires an update to instruments, key_dates or related raw records; (3) recompute actual English Git blob SHA and set each locale's english_file_sha, english_verified_at and english_source_fingerprint independently; (4) run all validation and PR checks on the new head; (5) merge only after green checks and legal review; (6) confirm Pages deployment and all three language pages. Do not republish PR #107.

This is a recovery memo, not a validated explainer or authorization to merge.
