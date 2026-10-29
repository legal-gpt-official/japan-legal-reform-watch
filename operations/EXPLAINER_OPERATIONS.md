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

### 日次処理量を増やす運用（2026-10-28 オーナー決定）

- **新規解説の公開目標は原則5案件/日、最大10案件/日**。対象数はキューの行数ではなく、同一案件を重複統合したdossier単位で数える。新規5件は目標であり、事実確認を省略して埋める義務ではない。既存の重大誤記・検証エラー・一次資料による状況変化の是正があれば新規より優先する。
- 最新の日次更新が完了したことを確認し、候補15〜20件をスクリーニングする。関連記録、法案の再提出・成立法・下位法令、既存解説との重複を束ね、一次資料と政策背景の充実度で公開可能性を判断する。
- 一次資料調査・英語正本・日本語/中国語翻訳・個別の意味チェックを複数案件で整理し、PR/CI/Pagesの共通作業をバッチ化する。案件ごとの一次資料照合、数値・義務・施行日、全文翻訳、各言語の同期キーは独立に検証する。問題案件を分離して正常案件のみPRに載せる。
- 必須CI成功後のみマージし、Pagesデプロイと個別の英日中ページの到達可能性を実確認する。新規従量課金AI APIの導入や公開基準の独断変更はしない。
- 毎日の報告は候補確認数、重複統合後の案件数、既存更新数、新規完成・公開数（目標5との差）、保留/見送り数と理由、英日中の完成・失効・未翻訳数、PR/CI/Pagesを区別する。

## 2. 解説候補の抽出（自動）

日次ワークフローの `Build explainer queue` ステップが `data/explainer_queue.json` を更新する。追加の API 呼び出しはしない。

- `kind: "new"`：まだ解説のない案件。PR #64 のタイムラインで束ねた記録のうち、公開中の記録が監視分野（経済安全保障、エネルギー・環境、労働、個人情報・AI、金融、独禁など）に入るもの。法案と成立法、パブコメと公布された政省令がつながっている案件を優先する。何年も前の法令が新しい施行日を迎えただけのものは順位を下げる。
- `kind: "update"`：既存の解説の再確認候補。理由（`reasons`）は次のとおり。
  - `validation_errors`：検証エラー（公開は止まっている）
  - `official_records_changed`：関連する公式記録が追加・変更された（fingerprint 不一致）
  - `stage_overtaken:*`：記載した日付を過ぎ、段階が進んだ可能性がある（施行日経過、意見募集終了、公布の記録）
  - `scheduled_recheck`：手続途中の案件（全部施行・廃案・撤回以外）で、最終確認から 14 日を過ぎた。**新しい公式記録が取り込まれていなくても**、`watch_pages` に挙げた所管省庁のページ等を定期的に再確認するための理由
  - `verification_old`：確定段階（全部施行など）の案件で、最終確認から 180 日を過ぎた
  - `awaiting_review:<区分>`：内容は有効だが、公開基準を満たすレビューを待っている
- 一度「見送り」（`status: "skipped"`）や「調査不能」（`status: "blocked"`）と記録した案件は、公式記録が変わるまで再提示しない。
- 同じ案件の `first_queued_on` は保たれるので、毎日不要な差分は出ない。

### 定期再確認（`watch_pages`）

手続を進める情報の多くは、ダッシュボードが取得していないページに出る。例：審議会の資料・答申、政令案・省令案のパブコメ、結果公示、施行期日政令の閣議決定、ガイドライン・Q&A。そこで手続途中の解説には `watch_pages`（所管省庁・国会・e-Gov など公式ページの URL と説明）を記録する。キューは 14 日ごとに `scheduled_recheck` として再確認を求めるので、そのつど次を行い、新しい公表があれば解説を更新する。なければ確認したことを `stamp` で記録する。公式ページが見つからない場合は警告 `no_watch_pages` が出る。

1. `watch_pages` を確認する。過去の個別報道発表ではなく、**新しい情報が載る一覧ページ**を登録する。
   - 所管省庁の法改正特設ページ（例：厚生労働省の「労働者災害補償保険法等の一部を改正する法律（令和８年法律第60号）について」、国土交通省の「建築物省エネ法のページ」）
   - 審議会の会議一覧（例：労働政策審議会 労災保険部会）
   - 所管省庁の報道発表一覧・新着情報
   - e-Gov 法令検索の改正履歴（`https://laws.e-gov.go.jp/law/<法令ID>`）
   - e-Gov パブリック・コメントの一覧
2. 公式サイトに絞って、**案件名・法令番号（漢数字と算用数字の両方）・所管省庁の新着情報**で検索し、前回の `verified_at` 以降の公表を探す。
3. 見つかった資料は本文を読んでから反映する。検索結果の要約だけで事実を書かない。

**未検出を不存在と扱わない。** ダッシュボードが取得していない資料（各省の結果公示ページ、官報、審議会資料、Cabinet Order の施行期日政令など）で状況が変わっていることがある。キューは手掛かりであり、事実の判定は必ず一次資料で行う。

## 3. source fingerprint（再確認が必要かの判定）

fingerprint は、解説の `related_item_ids` から次の範囲（closure）の生データ全件の内容を SHA-256 でまとめたもの。

- PR #64 のタイムラインで同じ法令とされた記録（同じ改正法令番号の e-Gov 記録、その法案、その政令案のパブコメ）
- 同じ公式 URL の記録（衆議院の審議状況の変化、e-Gov の段階の変化は新しい記録として追加される）
- 会期をまたいで URL が変わった同じ法案（種別・提出回次・番号が同じもの）

各記録の件名・メタデータ・公表日・URL・段階・締切・内容ハッシュのどれかが変わる、または関連記録が増えると fingerprint が変わり、`official_records_changed` として再確認に回る。公開中の解説には「新しい公式記録が見つかった」旨の注記が自動で表示される。

再確認したら `stamp` で `verified_at` と fingerprint を更新する。内容を直さずに stamp だけ押すことはしない。

## 4. 解説の作成手順

1. 最新キューの候補15〜20件を調査・重複統合し、`update` の `validation_errors`、`official_records_changed`、`stage_overtaken`、`scheduled_recheck` を先に処理する。新規解説の完成・公開は原則5件/日を標準目標、一次資料・3言語の意味照合・必須CIが揃う日だけ最大10件/日を上限目標とする。質を下げて目標件数を埋めず、実績と不足理由を報告する。
2. `context <item-id>` で、ダッシュボードが持つ公式記録・タイムライン・e-Gov 法令 API の URL を確認する。
3. 一次資料を調べる（優先順）：
   - **国の法令の成立・公布・施行**：衆議院・参議院の議案情報と議案要旨、官報、e-Gov 法令検索・法令 API（`https://laws.e-gov.go.jp/api/2/law_revisions/<法令ID>` で改正ごとの公布日・施行日がわかる）、所管省庁の公表資料（法案概要、公布通知など）
   - **政省令・告示**：e-Gov パブリック・コメント、e-Gov 法令検索、所管省庁
   - **JPX・日本証券業協会**：それぞれの自主規制規則についてだけの一次資料（`kind: "self_regulatory"`）。国の法令の成立・公布・施行の根拠にはできない。
   - ニュースや法律事務所の解説は `kind: "secondary"` として補助的に使うだけ。重要な法的事実は一次資料と照合する。
   - **最新の公表資料を必ず確認する。** 法案段階の資料だけで書かない。所管省庁の報道発表一覧、審議会のページ、e-Gov パブリック・コメント（結果公示を含む）、e-Gov 法令 API の改正履歴を、確認日（`verified_at`）時点まで調べる。
   - 取得方法の注意：衆議院のページは Shift_JIS。経済産業省のサイトは自動取得を拒否する（403）ことがあり、e-Gov パブリック・コメントの詳細ページは JavaScript が必要。その場合はブラウザで開いて読む。読めなかった資料は出典に挙げない。
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
| `watch_pages` | 定期再確認する公式ページ（`label`・`url`。*.go.jp、JPX、JSDA のみ） |
| `data/explainer_locales/<dossier_id>.json` | 英文を基準とした日本語（`ja`）・簡体字中国語（`zh-Hans`）の全文翻訳。各言語の `translations.<lang>.english_verified_at`・`english_source_fingerprint`・`english_file_sha` が英語版に一致し、各文章・箇条書き・年月日の項目数も一致し、英語版にない日付・数値を書いていない限り、その言語ページを生成しない。出典URL・施行日の基準は英語版を継承する |
| `quick_take` | `what_changed` / `who_is_affected` / `what_to_do_now` の3点を、各360字以内の簡潔な英語で必須記載（公開する解説）。事実・影響・今行う準備を区別し、未施行の義務を施行済みと書かない |
| `background` / `history` / `business_impact` | `paragraphs`（平文）と `source_ids` |
| `next_steps` | `confirmed`（出典つき・日付任意）、`expected`、`uncertainties`（必須） |
| `key_dates` | 種別・日付・説明・出典。法的事実の日付は `primary` 出典が必須。意見募集中の締切は確認日より後でも `comment_deadline` で書く（`other` にしない。締切の記録照合と締切後の再確認が働かなくなる） |
| `official_sources` | `id`（s1…）・`kind`・発行者・題名・URL・アクセス日 |
| `verified_at` / `source_fingerprint` | `stamp` が設定する |

段階（`current_stage`）：

- 法律：`under_consideration`（検討中）、`bill_submitted`（法案提出）、`diet_deliberation`（国会審議中）、`lapsed`（審議未了）、`withdrawn`（撤回）、`enacted`（成立・未公布）、`promulgated`（公布・未施行）、`partially_in_force`（一部施行）、`in_force`（全部施行）
- 政令・省令・告示：`under_consideration`、`public_comment_open`、`public_comment_closed`、`comment_results_published`、`promulgated`、`partially_in_force`、`in_force`、`not_adopted`
- ガイドライン：`under_consideration`、`public_comment_open`、`public_comment_closed`、`comment_results_published`、`published`、`in_effect`、`not_adopted`

### 編集方針：社会・政策の変化を因果関係で説明する（2026-10-27 オーナー方針）

このサイトの解説は単なる法改正速報・コンプライアンス対応リストではない。**中心的な問いは「なぜこの時期に、日本社会・経済・技術・国際関係の変化がこの制度改正につながったのか」**である。「法律が改正されたので対象企業は確認する」といった定型文では、この問いに答えられない。背景と政策形成過程の叙述を記事の主役とし、義務・影響・実務対応は根拠があるものを補助的に述べる。

- **問題が顕在化した背景（`background.paragraphs`）**：旧制度の目的と仕組みを説明し、その後に生じた社会構造、技術、産業、人口、財政、国際関係、市場行動等の変化のうち、当該改正に実際に関係する要因を明らかにする。一般的な「社会情勢の変化に対応」ではなく、何がいつ・どう変わり、誰にどのような問題が生じたのかを示す。統計・事例を使うなら出典、時点、対象範囲を明記する。
- **従来制度の限界・政策課題（`background.paragraphs`）**：従来の規制・支援では何を扱えなかったのか、制度の空白、コスト、利害の衝突、執行上の問題を明示する。ただし「従来制度は失敗した」などの評価を、公式資料の根拠なく断定しない。
- **政策形成の経緯（`history.paragraphs`）**：審議会の議論、答申・中間整理、政府方針、関係省庁の検討、意見公募とその論点、法案提出・修正・公布等を、単なる日付の羅列ではなく「どの問題認識が、どの政策判断や改正条項に結びついたか」の流れで整理する。論点や方向性が変化した場合も、確認できた資料に基づいて説明する。
- **なぜその手段なのか（`background`／`history`）**：規制強化、情報開示、補助・金融支援、対象拡張、義務化、努力義務等のうち、なぜ当該手段が採用・提案されたのかを、政府が公表した政策目的と制度設計の因果関係で説明する。利害関係者の懸念や政策上のトレードオフ、代替案は、議事録等で裏付けられる場合に限り扱う。
- **政策の意味と今後（`next_steps`）**：公式に決まった工程（`confirmed`）、通常の手続（`expected`）、まだ解けていない制度設計上の論点（`uncertainties`）を分ける。大きな規制・産業政策の方向性を読み解く場合は、資料で確認できる政策方針と筆者の推論を明確に区別し、未確定の動きを既定路線として描かない。
- **事業への影響（`business_impact.paragraphs`）**：まず「制度変更がどの経路で、どの企業・業界の行動・コスト・リスク・機会に作用するのか」を具体的に説明する。影響の根拠、前提条件、時点を示す。影響が特定できないときは無理に推測しない。単なる「対象企業は影響を確認すべき」は書かない。
- **推奨対応（`business_impact.checklist` と `quick_take.what_to_do_now`）**：案件固有の、実際に確認・判断できる事項に限る。「最新情報を確認する」「社内体制を見直す」「専門家に相談する」だけの汎用表現を項目数稼ぎに使わない。立法趣旨の解説をチェックリストより優先し、法的義務と任意の備えを区別する。
- **出典と推論の境界**：所管省庁の立法理由、政策評価、白書、公式統計、審議会配布資料・議事録・答申、国会議事録、パブコメ結果等の一次資料を優先して実際に読む。ニュースは補助資料であり、政策目的・因果関係・利害を裏付けなしに断定しない。政府の説明は「政府は～を課題と説明する」と帰属を明らかにする。独立した分析は「～と考えられる」「～という構造がある」等と位置づけ、事実と混ぜない。理由が確認できない場合は「公表資料では政策判断の理由までは確認できない」と明記する。
- **品質ゲート（執筆者による実質確認）**：各記事で「社会・経済上の何が変わったか」「旧制度のどこが不十分とされたか」「なぜ今回の制度設計なのか」「いつ、どこで議論が進んだか」「この変化は何を意味するか」の5問に具体的に答えられるか確認する。確認できない問いを架空の説明で埋めない。重要な根拠が不足すれば `draft` とすることを検討し、理由を報告する。自動CIの成功は、この編集品質の確認に代わらない。

既存のJSONスキーマとPR #72の翻訳整合性ルールは維持する。`background` と `history` の文章を増補する場合は、英語正本で事実を確定し、日本語・簡体字中国語の対応する段落を忠実に翻訳してから、各言語の同期キーを更新する。既存の完全な段落は必要なく再生成しない。

### 守るべき記述ルール

- 「成立」「公布」「施行」を同義に使わない。
- 基準値の表現は条文どおりにする：「以上」= at or above（meets or exceeds）、「超える」= exceeds / more than、「未満」= below、「以下」= at or below。基準値ちょうどが含まれるかどうかを英語で取り違えない。
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
- 基準は `scripts/explainers.py` の `PUBLISH_POLICY`。**2026-10-21 オーナー決定：4区分すべて `ai_checked` で事前の人間レビューなしに公開可**。ただし `status: "published"`、一次資料に基づく確認、`quick_take`、解説全体の検証エラー0件を必須とする。
- 公開後に誤りが発見された場合は速やかに修正し、必要なら `status: "withdrawn"` として非表示にする。誤りを疑う未確認事項は公開せず `uncertainties` に明記する。
- リポジトリ変数 `EXPLAINER_PUBLISH_POLICY` が設定されている場合はそれが優先する。旧設定（`human_reviewed`）が残っていれば削除またはAI基準へ変更する。例：

  ```
  EXPLAINER_PUBLISH_POLICY=facts=ai_checked,procedure=ai_checked,outlook=ai_checked,assessment=ai_checked
  ```

  不正な値は実行を止める。

- `human_reviewed` は、人が実際に内容を読んだときだけ設定する。`human_review.reviewer` と `reviewed_at` が必須。`reviewed_at` が `verified_at` より古い場合（解説を直して再 stamp した場合）は自動的に `ai_checked` 扱いに戻り、再レビュー待ちになる。
- 一部の区分だけ人が確認した場合は `section_review` で区分ごとに上げる。

### 任意の人間レビューの手順（オーナー）

1. PR の `data/explainers/*.json` と `preview` の HTML、出典リンクを読む。
2. 問題がなければ、該当ファイルに次を加える（全区分なら `review_state`、一部なら `section_review`）。

   ```json
   "review_state": "human_reviewed",
   "human_review": {"reviewer": "<GitHub ユーザー名>", "reviewed_at": "YYYY-MM-DD"}
   ```

3. `validate` と PR チェックが通れば、必要に応じてPRをマージする。**人間によるレビューを公開の必須要件にはしない**。

### 公開ページへの反映

新規・更新案件は所管省庁・国会・e-Govの一次資料と照合し、`stamp`・`validate`・PR上の全CIに成功した場合に限ってマージする。明確な事実関係の矛盾・根拠の欠落・CI失敗があれば公開しない。毎日の担当AIによる自動マージはオーナーの2026-10-21の承認によるが、`main` への直接pushは禁止。

マージ後、次の日次実行（06:00 JST開始・遅延する場合あり）で `docs/u/<id>/` が再生成される。急ぐ場合は Actions の `Daily update legal reform data` を手動実行する（取得・要約・翻訳も走る点に注意）。sitemap の `lastmod` は解説の `verified_at` を反映する。

## 7. 更新

- `update` 候補は、差分のある公式記録（`new_related_item_ids`）と一次資料を確認し、必要な箇所だけを直す。全文を作り直さない。
- `scheduled_recheck` 候補は、`watch_pages` と e-Gov 法令 API の改正履歴を確認する。新しい公表（審議会の答申、政令の閣議決定・公布、施行期日の確定、パブコメ結果、ガイドラインなど）があれば反映する。施行期日が確定したら、仮の最終期限ではなく確定日を `key_dates` と `confirmed` に書く。
- 関係する公式記録がダッシュボードにあれば、`related_item_ids` に加える（例：所管省庁の報道発表、関連する政省令の e-Gov 記録、パブコメ）。そのページにも解説が表示され、fingerprint の対象になる。
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


## 9. 日本語・中国語の詳細解説（2026-10-21追加）

- 英語を唯一の事実上の正本とする。国会・所管省庁・e-Gov等の一次資料で英語版の事実を確認し、その英語版から日本語版と中国語（簡体字）版を作る。翻訳サービスへの追加の従量課金API呼び出しは行わない。
- 翻訳ファイルは `data/explainer_locales/<dossier_id>.json`。構造：`{dossier_id,translations:{ja:{english_verified_at,english_source_fingerprint,english_file_sha,...},"zh-Hans":{english_verified_at,english_source_fingerprint,english_file_sha,...}}}`。**同期キー（英語版一致キー）は言語ごとに記録する**。日本語だけを更新した場合は日本語の同期キーだけを更新し、中国語の同期キーは中国語を英語版に合わせて直すまで変更しない（1つの共通キーでは、日本語の更新で古い中国語まで最新扱いになっていた）。ファイル直下に同期キーを置くとCIエラーになる。各言語には `title`, `quick_take`, `background[]`, `history[]`, `next_steps:{confirmed[],expected[],uncertainties[]}`, `business_impact[]`, `checklist[]`, `key_dates[]` を含める。配列の各要素は英語の同じ位置の要素を忠実に翻訳し、箇条書きは省略しない。
- 翻訳では公布・成立・施行、未確定の政令指定日、努力義務と強制義務、金額・割合・日付・閾値、以上と超えるを英語版と照合する。省庁の公式名称・法律の日本語正式名称や一次資料の名称を勝手に改変しない。
- 翻訳対象の英語JSONを書き終えた後、`git hash-object data/explainers/<dossier_id>.json` でそのファイルのblob SHAを求め、更新した言語の `translations.<lang>.english_file_sha` に記録する。同じ日の英文改稿でも翻訳が自動的に失効する。Windowsで改行がCRLFでも、Git同様にLFへ正規化して計算する。
- `scripts/explainer_locales.py` は翻訳全項目の有無、英語版と同じ文章・リスト数、マークアップ（HTML・Markdownリンク）禁止、言語ごとの `english_verified_at` と `english_source_fingerprint` および英語blobの一致を判定する。さらに `scripts/explainer_facts.py` が、**英語版のどこにも書かれていない日付・数値**（法律番号、閾値、期間、日付）を翻訳が述べていないかを機械的に検査する。令和8年＝2026年、50万＝500,000、4割＝40%、2027年1月4日＝4 January 2027 等の同値表記は誤検出しない。英語版の別の箇所に同じ数値がある取り違えは検出できないため、執筆者による照合は引き続き必須。**翻訳が未作成または古い場合は警告とし、英語版のPR・公開を妨げず、該当言語ページのみ非表示**にする。最新の英語blobと一致すると主張しているのに項目欠落・不正な翻訳データがある場合はCIエラーで公開を止める。言語指定`?lang=ja` / `?lang=zh-Hans`で翻訳未完了なら、英語版に翻訳更新中のメッセージを表示する。
- `scripts/build_public_pages.py` が同一法改正の各記録ページに `/u/<id>/`（英語）、`/u/<id>/ja/`（日本語）、`/u/<id>/zh/`（簡体字中国語）を生成し、相互言語リンクと `hreflang` を付ける。該当言語の翻訳が未整備または英語版変更により失効した場合は、その言語ページを生成せず旧ページも削除する。日本語・中国語版の法改正解説本文は静的HTMLとして読める。法律の原記録・時系列は英語版に引き続き掲載する。
- 毎日の解説作成タスクでは原則として**英・日・中国語の3言語が揃った段階でPRを出す**。英語版更新後は、変更部分だけを両言語に反映し、英語版一致キー（確認日・公式記録fingerprint・英語本文のgit blob SHA）も更新する。翻訳が追いつかない場合はその理由を報告し、古い翻訳の公開を避ける。
- 翻訳ファイル単体のPRのマージでも `publish-explainer-pages.yml` が静的ページを再生成する。GitHub Pagesの更新完了前に公開済みと報告しない。
