# 法改正解説（Reform explainers）運用ガイド

対象：`data/explainers/<dossier_id>.json` に保存する英語の法改正解説。
解説は日次パイプラインの外で作成する（AI API の従量課金は使わない）。日次ワークフローは解説を**生成しない**。行うのは候補の抽出・検証・公開ページへの反映だけ。

関連ファイル：

| ファイル | 役割 |
|---|---|
| `scripts/explainers.py` | スキーマ・検証・公開判定・キュー・運用コマンド（すべてオフライン） |
| `scripts/law_timeline.py` | 公式記録の保守的な関連付け（PR #64 のタイムライン） |
| `scripts/build_public_pages.py` | 個別ページ `docs/u/<id>/` への解説・タイムラインの描画 |
| `data/explainer_queue.json` | 解説候補・再確認候補（日次で自動更新） |
| `.github/workflows/pr-checks.yml` | PR ごとの全テストと解説検証（読み取り専用・秘密情報なし） |
| `operations/CHATGPT_EXPLAINER_TASK_PROMPT_JA.md` | ChatGPT 等の定期作業に登録するプロンプト |

## 1. コマンド一覧

```
python scripts/explainers.py queue --build --top 10   # キューを更新し上位を表示
python scripts/explainers.py queue --top 10           # 書き込まずに表示
python scripts/explainers.py context <item-id|dossier-id>   # 1案件の公式記録・タイムライン・e-Gov API URL
python scripts/explainers.py scaffold <item-id> --id <dossier-id>   # 下書きファイルを作成
python scripts/explainers.py validate                 # 全解説の検証（エラーなら exit 1）
python scripts/explainers.py stamp <dossier-id> --verified-on YYYY-MM-DD   # 再確認の記録
python scripts/explainers.py preview <dossier-id> --out page.html   # ローカルで表示確認（全セクション表示）
```

`--today YYYY-MM-DD` で基準日（JST）を指定できる。

## 2. 解説候補の抽出（自動）

日次ワークフローの `Build explainer queue` ステップが `data/explainer_queue.json` を更新する。追加の API 呼び出しはしない。

- `kind: "new"`：まだ解説のない案件。PR #64 のタイムラインで束ねた記録のうち、公開中の記録が監視分野（経済安全保障、エネルギー・環境、労働、個人情報・AI、金融、独禁など）に入るもの。法案と成立法、パブコメと公布された政省令がつながっている案件を優先する。何年も前の法令が新しい施行日を迎えただけのものは順位を下げる。
- `kind: "update"`：既存の解説の再確認候補。理由（`reasons`）は次のとおり。
  - `validation_errors`：検証エラー（公開は止まっている）
  - `official_records_changed`：関連する公式記録が追加・変更された（fingerprint 不一致）
  - `stage_overtaken:*`：記載した日付を過ぎ、段階が進んだ可能性がある（施行日経過、意見募集終了、公布の記録）
  - `verification_old`：最終確認から日数が経過した（手続途中の段階は 30 日、確定段階は 180 日）
  - `awaiting_review:<区分>`：内容は有効だが、公開基準を満たすレビューを待っている
- 一度「見送り」（`status: "skipped"`）や「調査不能」（`status: "blocked"`）と記録した案件は、公式記録が変わるまで再提示しない。
- 同じ案件の `first_queued_on` は保たれるので、毎日不要な差分は出ない。

**未検出を不存在と扱わない。** ダッシュボードが取得していない資料（各省の結果公示ページ、官報、審議会資料、Cabinet Order の施行期日政令など）で状況が変わっていることがある。キューは手掛かりであり、事実の判定は必ず一次資料で行う。

## 3. source fingerprint（再確認が必要かの判定）

fingerprint は、解説の `related_item_ids` から次の範囲（closure）の生データ全件の内容を SHA-256 でまとめたもの。

- PR #64 のタイムラインで同じ法令とされた記録（同じ改正法令番号の e-Gov 記録、その法案、その政令案のパブコメ）
- 同じ公式 URL の記録（衆議院の審議状況の変化、e-Gov の段階の変化は新しい記録として追加される）
- 会期をまたいで URL が変わった同じ法案（種別・提出回次・番号が同じもの）

各記録の件名・メタデータ・公表日・URL・段階・締切・内容ハッシュのどれかが変わる、または関連記録が増えると fingerprint が変わり、`official_records_changed` として再確認に回る。公開中の解説には「新しい公式記録が見つかった」旨の注記が自動で表示される。

再確認したら `stamp` で `verified_at` と fingerprint を更新する。内容を直さずに stamp だけ押すことはしない。

## 4. 解説の作成手順

1. `queue --top 10` で対象を選ぶ（1日 3〜5 件。`update` の `validation_errors` と `official_records_changed` を先に処理する）。
2. `context <item-id>` で、ダッシュボードが持つ公式記録・タイムライン・e-Gov 法令 API の URL を確認する。
3. 一次資料を調べる（優先順）：
   - **国の法令の成立・公布・施行**：衆議院・参議院の議案情報と議案要旨、官報、e-Gov 法令検索・法令 API（`https://laws.e-gov.go.jp/api/2/law_revisions/<法令ID>` で改正ごとの公布日・施行日がわかる）、所管省庁の公表資料（法案概要、公布通知など）
   - **政省令・告示**：e-Gov パブリック・コメント、e-Gov 法令検索、所管省庁
   - **JPX・日本証券業協会**：それぞれの自主規制規則についてだけの一次資料（`kind: "self_regulatory"`）。国の法令の成立・公布・施行の根拠にはできない。
   - ニュースや法律事務所の解説は `kind: "secondary"` として補助的に使うだけ。重要な法的事実は一次資料と照合する。
4. `scaffold <item-id> --id <dossier-id>` で下書きを作り（既存なら編集）、各項目を書く。
5. `validate` と `preview` で確認する。
6. ブランチを作り、PR を出す。`Pull request checks` が全テストと検証を実行する。

### 案件単位（dossier）の考え方

- 一つの法改正を一つの解説にする（同じ記録を二つの解説が扱うと検証エラー）。
- 法律と、その施行のための政令・省令は、公式資料で関係が確認できる場合に同じ解説にまとめてよい（例：外為法改正法と施行に伴う整備政令）。`related_item_ids` に両方の記録を入れる。
- 同じ法律を対象にしていても、別の改正（別の改正法令番号）は別の解説にする。
- 解説は束ねた全ページに表示される。各ページには、そのページの法令自体の状態（e-Gov 記録上の公布日・施行日、未確定の施行日の有無）が別に表示されるので、本文で「全体が施行済み」のように個々の法令の状態を一括で断定しない。

## 5. JSON の書き方

| 項目 | 内容 |
|---|---|
| `dossier_id` | ファイル名と同じ。英小文字・数字・ハイフン |
| `status` | `draft` / `published` / `withdrawn` / `skipped` / `blocked` |
| `review_state` | `unreviewed` / `ai_checked` / `human_reviewed`（全区分の基準値） |
| `section_review` | 区分ごとの上書き（任意）：`{"facts": "human_reviewed"}` など |
| `human_review` | `human_reviewed` を使うときに必須：`{"reviewer": "...", "reviewed_at": "YYYY-MM-DD"}` |
| `instrument_type` | `statute` / `cabinet_order` / `ministerial_ordinance` / `notice` / `guideline` / `other` |
| `current_stage` | 法形式ごとに許される段階（下表） |
| `related_item_ids` | `data/raw_items.json` の ID |
| `instruments` | 法令番号・正式名称（日本語）・英語の説明的名称。e-Gov 記録にある番号は名称が一致しないとエラー |
| `background` / `history` / `business_impact` | `paragraphs`（平文）と `source_ids` |
| `next_steps` | `confirmed`（出典つき・日付任意）、`expected`、`uncertainties`（必須） |
| `key_dates` | 種別・日付・説明・出典。法的事実の日付は `primary` 出典が必須 |
| `official_sources` | `id`（s1…）・`kind`・発行者・題名・URL・アクセス日 |
| `verified_at` / `source_fingerprint` | `stamp` が設定する |

段階（`current_stage`）：

- 法律：`under_consideration`（検討中）、`bill_submitted`（法案提出）、`diet_deliberation`（国会審議中）、`lapsed`（審議未了）、`withdrawn`（撤回）、`enacted`（成立・未公布）、`promulgated`（公布・未施行）、`partially_in_force`（一部施行）、`in_force`（全部施行）
- 政令・省令・告示：`under_consideration`、`public_comment_open`、`public_comment_closed`、`comment_results_published`、`promulgated`、`partially_in_force`、`in_force`、`not_adopted`
- ガイドライン：`under_consideration`、`public_comment_open`、`public_comment_closed`、`comment_results_published`、`published`、`in_effect`、`not_adopted`

### 守るべき記述ルール

- 「成立」「公布」「施行」を同義に使わない。
- **施行日が政令に委任されている場合、法定の最終期限を確定日として書かない。** e-Gov の改正履歴は、施行日未定の改正に「法律が許す最終日」を仮の日付として付けている（例：「公布の日から起算して二年を超えない範囲内において政令で定める日」→ 2028-07-30）。この日付を `effective` などの key date に入れると検証エラーになる。未確定事項（`uncertainties`）に「遅くとも○年○月まで」と書く。
- `in_force`（全部施行）は、未来の施行日や未確定の施行日が残っていればエラー。その場合は `partially_in_force`。
- 既に公布・施行済みの案件に、法案提出やパブコメがこれから行われるかのように書かない（段階と記録が矛盾すると検証エラー）。
- `next_steps` の3区分を混ぜない：
  - `confirmed`：公式資料で確認できた予定（出典必須）
  - `expected`：その法形式で通常行われる手続（予測ではない旨が自動表示される）
  - `uncertainties`：未確定・不明な事項（必須）
- 対象企業や義務が資料で確認できないときは影響を断定しない。義務の記述は資料の根拠がある範囲にとどめる。
- HTML・Markdown・コードフェンスは使わない（検証エラー）。表示時はすべてエスケープされる。

## 6. レビューと公開

公開は**区分ごと**に判定する。

| 区分（category） | 対象 | 内容の性質 |
|---|---|---|
| `facts` | 背景、経緯、key dates | 公式資料で確認できる客観的事実 |
| `procedure` | Expected procedural steps | 一般的な制定・改正手続の説明 |
| `outlook` | Confirmed next steps、Outstanding uncertainties | 案件固有の今後の見通し |
| `assessment` | Business impact | 企業への法的義務・実務対応の評価 |

- 解説そのものが表示されるのは、`status: "published"`、検証エラーなし、`facts` が基準を満たす場合だけ。
- `facts` 以外で基準に届かない区分はページから省かれ、「Not shown until reviewed」と区分名が表示される。
- 基準は `scripts/explainers.py` の `PUBLISH_POLICY`。**現在（2026-10-20 オーナー決定）は全区分 `human_reviewed`**。
- リポジトリ変数 `EXPLAINER_PUBLISH_POLICY` で区分ごとに上書きできる（コード変更不要）。将来の想定設定：

  ```
  EXPLAINER_PUBLISH_POLICY=facts=ai_checked,procedure=ai_checked,outlook=ai_checked
  ```

  （`assessment` は `human_reviewed` のまま）。不正な値は実行を止める。

- `human_reviewed` は、人が実際に内容を読んだときだけ設定する。`human_review.reviewer` と `reviewed_at` が必須。`reviewed_at` が `verified_at` より古い場合（解説を直して再 stamp した場合）は自動的に `ai_checked` 扱いに戻り、再レビュー待ちになる。
- 一部の区分だけ人が確認した場合は `section_review` で区分ごとに上げる。

### 人によるレビューの手順（オーナー）

1. PR の `data/explainers/*.json` と `preview` の HTML、出典リンクを読む。
2. 問題がなければ、該当ファイルに次を加える（全区分なら `review_state`、一部なら `section_review`）。

   ```json
   "review_state": "human_reviewed",
   "human_review": {"reviewer": "<GitHub ユーザー名>", "reviewed_at": "YYYY-MM-DD"}
   ```

3. `validate` と PR チェックが通れば、PR をマージする。

### 公開ページへの反映

マージ後、次の日次実行（06:00 JST）で `docs/u/<id>/` が再生成される。急ぐ場合は Actions の `Daily update legal reform data` を手動実行する（取得・要約・翻訳も走る点に注意）。sitemap の `lastmod` は解説の `verified_at` を反映する。

## 7. 更新

- `update` 候補は、差分のある公式記録（`new_related_item_ids`）と一次資料を確認し、必要な箇所だけを直す。全文を作り直さない。
- 内容を直したら `stamp --verified-on <確認日>` を実行する。`human_reviewed` だった区分は再レビュー待ちに戻る。
- 内容を直す必要がなかった場合も、資料を確認したうえで `stamp` する（fingerprint と確認日の更新）。

## 8. 障害時の復旧

| 状況 | 対応 |
|---|---|
| 誤った解説を公開した | 該当コミットを `git revert` するか、`status` を `withdrawn` にして PR をマージする。次の日次実行で表示が消える |
| 検証エラーの解説がある | 日次実行は止まらず、その解説だけ表示されない（Step Summary に表示）。修正して PR を出す |
| `data/explainer_queue.json` が壊れた | 削除して `python scripts/explainers.py queue --build` で再生成する（解説本体は影響を受けない） |
| `EXPLAINER_PUBLISH_POLICY` の設定誤り | キュー作成・ページ生成のステップが失敗する。変数を直して再実行する |
| fingerprint が全件変わった（仕組みの変更時） | 全解説が再確認候補になる。資料を確認し `stamp` する |
| PR チェックが失敗 | ログの `error:` 行を直す。テストを無効化して通さない |

過去の解説は Git の履歴でいつでも戻せる。

## 9. ChatGPT など外部の定期作業との分担

- 解説の調査・執筆は、ChatGPT の定期タスク、Codex、Work などの外部の作業者が行う想定。プロンプトは `operations/CHATGPT_EXPLAINER_TASK_PROMPT_JA.md`。
- GitHub への書き込み（ブランチ作成・コミット・PR 作成）や Python の実行ができるかは、利用する環境と権限による。**できることを前提にしない。** できない場合の代替：
  1. ChatGPT が解説 JSON を返答として出力し、オーナー（または Work 等のエージェント）が GitHub の Web 画面で `data/explainers/` にファイルを追加して PR を作る。`Pull request checks` が検証する。
  2. リポジトリのコードを実行できる環境（Codex のクラウドタスク、Claude Code の定期タスクなど）で、コマンドの実行から PR 作成までを行う。
- どの方式でも、公開の最終判断は PR のマージと公開基準（`PUBLISH_POLICY`）による。外部の作業者に `main` への直接 push 権限を与えない運用を推奨する。
