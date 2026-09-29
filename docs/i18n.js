/* =============================================================
   Japan Legal Reform Watch by LegalOS — i18n layer
   Single namespace (window.JLRW_I18N). English is canonical; Japanese (ja) and
   Simplified Chinese (zh-Hans) are optional overlays. Every English string here mirrors
   the existing UI exactly, so switching back to English is loss-less. All
   localized strings (including localized CSV layouts and copy-summary labels)
   live here so app.js stays ASCII and free of scattered language branches.
   Vanilla JS, no dependencies.
   ============================================================= */

(function () {
  "use strict";

  var DEFAULT_LANG = "en";
  var SUPPORTED = ["en", "ja", "zh-Hans"];
  var STORAGE_KEY = "jlrw-language";

  // -------- Static + dynamic UI string dictionaries --------
  // Keys are shared; English values are byte-identical to the current UI.
  var STRINGS = {
    en: {
      document_title: "Japan Legal Reform Watch by LegalOS",
      brand_h1: "Japan Legal Reform Watch by LegalOS",
      tagline: "Free Japan Legal & Regulatory Update Monitor",
      header_about: "About this tool",

      trust_title: "Reliability note",
      trust_body:
        "This dashboard summarizes official Japanese government and regulator sources. " +
        "AI summaries and rule-based previews are monitoring aids only; original Japanese " +
        "sources remain authoritative and should be reviewed before taking action.",

      ds_heading: "Data status",
      ds_note: "Monitoring aid only. Original Japanese official sources remain authoritative.",
      ds_period: "Period",
      ds_updates: "Updates",
      ds_archive_total: "Archive total",
      ds_sources: "Sources represented",
      ds_english_ai_summaries: "English AI summaries",
      ds_japanese_ai_summaries: "Japanese AI summaries",
      ds_chinese_translations: "Chinese translations",
      ds_open_pc: "Open public comments",
      ds_newly_detected: "Newly detected (7d)",
      ds_latest_checked: "Latest checked",
      ds_unknown: "Unknown",
      ds_status: "Data status",
      ds_unavailable: "Unavailable",

      controls_filters_search: "Filters & Search",
      controls_hide_filters: "Hide filters",
      qf_label: "Quick filters",
      qf_pc_open: "Public Comment Open",
      qf_ai: "AI Summary",
      qf_newly: "Newly detected",
      qf_medium: "Medium Impact",
      qf_reset: "Reset",

      ctl_search: "Search",
      ctl_search_placeholder: "Search title or summary...",
      ctl_period: "Period",
      ctl_area: "Area",
      ctl_stage: "Stage",
      ctl_source: "Source",
      ctl_impact: "Impact Level",
      ctl_sort: "Sort",
      opt_all_areas: "All Areas",
      opt_all_stages: "All Stages",
      opt_all_sources: "All Sources",
      opt_all_impacts: "All Impact Levels",
      sort_relevance: "Relevance",
      sort_published: "Published date",
      sort_checked: "Last checked",
      sort_detected: "First detected",
      period_latest: "Latest ({year})",
      period_all: "All years (slower)",
      period_undated: "Undated",
      period_loading: "Loading selected period...",

      export_button: "Export CSV",
      export_none: "No matching updates to export",
      export_label: "Export {count} matching updates to CSV",
      export_aria_default: "Export matching updates to CSV",
      export_done: "Exported {count} updates",
      export_failed: "Export failed",

      // Saved searches / email-alert pilot
      saved_search_open: "Save this search",
      saved_searches_count: "Saved searches ({count})",
      saved_search_eyebrow: "Monitoring preferences",
      saved_search_title: "Saved searches",
      saved_search_close: "Close saved searches",
      saved_search_current_title: "Current search",
      saved_search_name_label: "Search name",
      saved_search_name_placeholder: "e.g. Japan AI and privacy updates",
      saved_search_save: "Save search",
      saved_search_library_title: "Saved in this browser",
      saved_search_storage_note:
        "Saved searches stay on this device. They do not create an account or email subscription.",
      saved_search_capacity: "{count} of {max}",
      saved_search_empty: "No searches have been saved in this browser.",
      saved_search_default_name: "Monitoring search {count}",
      saved_search_saved: "Search saved in this browser.",
      saved_search_updated: "Saved search updated.",
      saved_search_deleted: "Saved search deleted.",
      saved_search_loaded: "Saved search loaded.",
      saved_search_limit: "You can save up to {max} searches in this browser.",
      saved_search_storage_error: "This browser could not store saved searches.",
      saved_search_unavailable: "This saved search is unavailable.",
      saved_search_load: "Load",
      saved_search_delete: "Delete",
      alert_plan_label: "Email alerts",
      alert_plan_title: "Daily email digest",
      alert_plan_price_monthly: "US$19/month",
      alert_plan_price_yearly: "or US$190/year",
      alert_plan_feature_area: "Choose one area, or all areas, at checkout",
      alert_plan_feature_daily:
        "Each morning (JST): updates newly detected by this dashboard in your area",
      alert_plan_feature_deadlines:
        "Public-comment deadline reminders 7 days, 3 days, and on the closing day, when structured official data is available",
      alert_plan_feature_self_serve:
        "Starts automatically after checkout; cancel or change billing yourself at any time",
      alert_plan_subscribe_monthly: "Subscribe monthly",
      alert_plan_subscribe_yearly: "Subscribe yearly",
      alert_plan_unavailable:
        "Email subscriptions are not open yet. The free feeds below are available now.",
      alert_plan_note:
        "Checkout and billing are handled by Stripe. Alert emails are monitoring aids, not legal advice; original Japanese official sources remain authoritative.",
      alert_plan_manage: "Manage or cancel an existing subscription",
      alert_feeds_title: "Free feeds",
      alert_feeds_body:
        "Follow an area without an account: an RSS feed of newly detected updates and a calendar of open public-comment deadlines.",
      alert_feeds_area: "Area",
      alert_feeds_rss: "RSS feed",
      alert_feeds_ics: "Deadline calendar (.ics)",
      alert_faq_title: "Before you subscribe",
      alert_faq_start_q: "When does the digest start?",
      alert_faq_start_a:
        "From the next daily update after checkout. A digest is sent on mornings with newly detected updates or a deadline reminder in your area, and skipped on mornings with nothing to report.",
      alert_faq_contents_q: "What does the digest contain?",
      alert_faq_contents_a:
        "Updates first detected by this dashboard in your area, with the English title, the original Japanese title, the stage, and a link to the original Japanese official source, plus upcoming public-comment deadlines. “Newly detected” does not mean a new law or regulation. Summaries are for triage and are not legal advice.",
      alert_faq_change_q: "How do I change the area?",
      alert_faq_change_a:
        "The area is chosen at checkout. To follow a different area, cancel the current subscription in the subscription portal and subscribe again with the new area.",
      alert_faq_cancel_q: "How do I cancel or update billing?",
      alert_faq_cancel_a:
        "Use the subscription portal linked in every email and above. You can cancel, switch between monthly and yearly, change the delivery email, update your card, and download invoices. A cancellation takes effect at the end of the current billing period.",

      checkout_thanks_page_title: "After checkout — Japan Legal Reform Watch",
      checkout_thanks_language: "Language",
      checkout_thanks_eyebrow: "Email digest",
      checkout_thanks_title: "After checkout",
      checkout_thanks_intro:
        "If Stripe has confirmed your payment, your daily email digest is active. There is nothing else you need to do.",
      checkout_thanks_plan_label: "Billing",
      checkout_thanks_plan_monthly: "Monthly — US$19/month",
      checkout_thanks_plan_yearly: "Yearly — US$190/year",
      checkout_thanks_next_title: "What happens next",
      checkout_thanks_step1_title: "Stripe emails your receipt",
      checkout_thanks_step1_body:
        "The receipt goes to the email address entered at checkout. The digest is delivered to the same address.",
      checkout_thanks_step2_title: "The first digest arrives with the next daily update",
      checkout_thanks_step2_body:
        "The dashboard updates every morning, Japan time. A digest is sent on mornings with newly detected updates or a deadline reminder in the area you chose, and skipped when there is nothing to report.",
      checkout_thanks_step3_title: "You manage the subscription yourself",
      checkout_thanks_step3_body:
        "Every email links to the subscription portal, where you can cancel, switch between monthly and yearly, change the delivery email, update your card, and download invoices.",
      checkout_thanks_note_title: "This page does not confirm payment",
      checkout_thanks_note_body:
        "Stripe’s receipt email is the confirmation. If you did not receive one, the checkout may not have completed.",
      checkout_thanks_dashboard: "Return to the dashboard",
      checkout_thanks_manage: "Open the subscription portal",
      checkout_thanks_trust:
        "Alert emails are monitoring aids, not legal advice. Original Japanese official sources remain authoritative.",
      checkout_thanks_legal_nav: "Legal information",

      load_more: "Load more updates",
      empty_title: "No matching updates found.",
      empty_hint: "Try clearing filters or adjusting your search terms.",
      error_title: "Unable to load updates.",
      error_body:
        "This dashboard loads its data via fetch(), which requires the page to be served over " +
        "HTTP. If you opened this file directly (file://), please serve the project root with a " +
        "local web server (see the project README.md).",

      // Active filter summary (mobile)
      af_none: "No active filters",
      af_active_prefix: "Active: ",
      af_search: 'Search: "{q}"',
      af_period: "Period: {v}",
      af_area: "Area: {v}",
      af_stage: "Stage: {v}",
      af_source: "Source: {v}",
      af_impact: "Impact: {v}",
      af_sort: "Sort: {v}",
      af_ai: "AI Summary",
      af_newly: "Newly detected",
      af_count: "Active: {n} filters",

      // Card
      card_summary_heading: "Summary",
      card_business_impact_heading: "Business Impact",
      card_recommended_action_heading: "Recommended Action",
      card_source_name_label: "Source name",
      card_view_source: "View Original Japanese Source →",
      card_source_note: "Original Japanese source remains authoritative.",
      copy_summary_label: "Copy summary",
      copy_source_link_label: "Copy source link",
      copy_actions_aria: "Copy actions",
      date_published: "Published",
      date_first_detected: "First detected",
      date_last_checked: "Last checked",
      impact_badge: "{level} Impact",
      newly_detected: "Newly detected",
      newly_detected_title: "First detected by this dashboard on {date}",
      newly_detected_aria: "Newly detected. First detected by this dashboard on {date}.",

      badge_ai_summary: "AI Summary",
      badge_ai_summary_title:
        "This item includes an AI-generated English summary. It is not an official translation or legal advice.",
      badge_rule_based: "Rule-based Preview",
      badge_rule_based_title:
        "This item has not yet been summarized by AI and uses a rule-based placeholder.",
      badge_ai_translation: "AI Translation",
      translation_note:
        "This translation is AI-generated for monitoring only; the Japanese original prevails.",
      translation_unavailable: "Chinese translation unavailable; showing English.",
      japanese_summary_note:
        "This Japanese summary was generated by AI directly from Japanese source metadata; the official source prevails.",
      japanese_summary_unavailable: "Japanese AI summary unavailable; showing English.",

      // Copy feedback
      copy_summary_success: "Summary copied",
      copy_source_success: "Source link copied",
      copy_failed: "Copy failed",

      // Results meta
      meta_showing: "Showing {shown} of {total} matching updates",
      meta_total: "{count} total updates",
      meta_english_ai: "English AI summaries: {count}",
      meta_japanese_ai: "Japanese AI summaries: {count}",
      meta_chinese_translations: "Chinese translations: {count}",
      meta_last_checked: "Last checked: {date}",

      // Copy summary section labels (used by the localized copy builder)
      cs_title: "Chinese title",
      cs_en_ref_title: "English reference title",
      cs_ja_title: "Original Japanese title",
      cs_area: "Area",
      cs_stage: "Stage",
      cs_impact: "Impact",
      cs_source: "Source",
      cs_published: "Published",
      cs_first_seen: "First detected",
      cs_summary: "Summary",
      cs_business: "Business impact",
      cs_action: "Recommended action",
      cs_official_source: "Official Japanese source",
      cs_note:
        "This summary and translation are AI-generated for monitoring only and are not legal advice. The Japanese official source prevails.",
      cs_fallback: "Some content has no Chinese translation and is shown in English.",

      // Modal
      modal_title: "Important Notice — Please Read Before Using",
      modal_p1:
        "Japan Legal Reform Watch by LegalOS provides English-language summaries of Japanese " +
        "legal, regulatory, and administrative developments for general informational purposes only.",
      modal_li1:
        "This site is not an official translation. Always refer to the original Japanese-language " +
        "source for authoritative text.",
      modal_li2:
        "The content is not legal advice and does not create an attorney-client relationship.",
      modal_li3:
        "We do not warrant the accuracy, completeness, or currentness of any information presented.",
      modal_li4:
        "For business or compliance decisions, please consult qualified Japanese legal counsel.",
      modal_p2:
        "By continuing, you acknowledge and accept these terms. To the maximum extent permitted by " +
        "applicable law, LegalOS disclaims all liability arising from use of this site.",
      modal_link: "Read the full Legal Notice & Disclaimer →",
      modal_accept: "I Understand and Agree",

      // Footer
      footer_brand_p: "Created by Legal GPT. Explore more Japan legal updates and legal-tech tools.",
      footer_learn_more: "Learn more about Japan Legal Reform Watch",
      footer_japan_updates: "Japan Legal Updates",
      footer_legal_en: "Legal Notice / Disclaimer (EN)",
      footer_disclaimer_en: "Legal Notice & Disclaimer (EN)",
      footer_disclaimer_ja: "Legal Notice & Disclaimer (Japanese)",
      footer_bottom:
        "This site provides English summaries for informational purposes only. It is not an official " +
        "translation and not legal advice. See the full Legal Notice for details.",

      // Language selector
      lang_selector_label: "Language",
    },

    ja: {
      document_title: "日本法改正ウォッチ by LegalOS",
      brand_h1: "日本法改正ウォッチ by LegalOS",
      tagline: "日本の法令・規制動向を確認できる無料モニター",
      header_about: "このツールについて",

      trust_title: "情報の信頼性について",
      trust_body:
        "本ダッシュボードは、日本の政府機関および規制当局の公式情報を要約して掲載しています。" +
        "AI要約およびルールベースのプレビューはモニタリング補助にすぎず、法的助言ではありません。" +
        "対応を判断する前に、必ず日本語の公式情報源をご確認ください。",

      ds_heading: "データ状況",
      ds_note: "モニタリング補助にすぎません。日本語の公式情報源が優先します。",
      ds_period: "対象期間",
      ds_updates: "更新件数",
      ds_archive_total: "アーカイブ総数",
      ds_sources: "情報源数",
      ds_english_ai_summaries: "英語AI要約数",
      ds_japanese_ai_summaries: "日本語AI要約数",
      ds_chinese_translations: "中国語翻訳数",
      ds_open_pc: "意見募集中",
      ds_newly_detected: "新規検出（7日間）",
      ds_latest_checked: "最終確認日",
      ds_unknown: "不明",
      ds_status: "データ状況",
      ds_unavailable: "利用不可",

      controls_filters_search: "絞り込み・検索",
      controls_hide_filters: "絞り込みを閉じる",
      qf_label: "クイックフィルター",
      qf_pc_open: "意見募集中",
      qf_ai: "AI要約",
      qf_newly: "新規検出",
      qf_medium: "影響度：中",
      qf_reset: "リセット",

      ctl_search: "検索",
      ctl_search_placeholder: "タイトルまたは要約を検索…",
      ctl_period: "対象期間",
      ctl_area: "分野",
      ctl_stage: "段階",
      ctl_source: "情報源",
      ctl_impact: "影響度",
      ctl_sort: "並び順",
      opt_all_areas: "すべての分野",
      opt_all_stages: "すべての段階",
      opt_all_sources: "すべての情報源",
      opt_all_impacts: "すべての影響度",
      sort_relevance: "関連度",
      sort_published: "公表日",
      sort_checked: "最終確認日",
      sort_detected: "初回検出日",
      period_latest: "最新（{year}年）",
      period_all: "全期間（読み込みに時間がかかります）",
      period_undated: "日付不明",
      period_loading: "選択した期間を読み込んでいます…",

      export_button: "CSVを出力",
      export_none: "出力できる該当データがありません",
      export_label: "該当する更新 {count} 件をCSVで出力",
      export_aria_default: "該当する更新をCSVで出力",
      export_done: "更新 {count} 件を出力しました",
      export_failed: "出力に失敗しました",

      saved_search_open: "この検索を保存",
      saved_searches_count: "保存済み検索（{count}）",
      saved_search_eyebrow: "モニタリング設定",
      saved_search_title: "保存済み検索",
      saved_search_close: "保存済み検索を閉じる",
      saved_search_current_title: "現在の検索条件",
      saved_search_name_label: "検索名",
      saved_search_name_placeholder: "例：日本のAI・個人情報保護動向",
      saved_search_save: "検索を保存",
      saved_search_library_title: "このブラウザに保存済み",
      saved_search_storage_note: "検索条件はこの端末にのみ保存されます。アカウントやメール配信は作成されません。",
      saved_search_capacity: "{count} / {max}",
      saved_search_empty: "このブラウザには保存済みの検索がありません。",
      saved_search_default_name: "モニタリング検索 {count}",
      saved_search_saved: "検索条件をこのブラウザに保存しました。",
      saved_search_updated: "保存済み検索を更新しました。",
      saved_search_deleted: "保存済み検索を削除しました。",
      saved_search_loaded: "保存済み検索を読み込みました。",
      saved_search_limit: "このブラウザには最大 {max} 件まで保存できます。",
      saved_search_storage_error: "このブラウザに検索条件を保存できませんでした。",
      saved_search_unavailable: "この保存済み検索は利用できません。",
      saved_search_load: "読み込む",
      saved_search_delete: "削除",
      alert_plan_label: "メールアラート",
      alert_plan_title: "日次メールダイジェスト",
      alert_plan_price_monthly: "月額19米ドル",
      alert_plan_price_yearly: "または年額190米ドル",
      alert_plan_feature_area: "決済時に分野を1つ、またはすべての分野を選択",
      alert_plan_feature_daily: "毎朝（日本時間）：選択した分野で本ダッシュボードが新たに検出した更新",
      alert_plan_feature_deadlines: "公式の構造化データがある場合、意見募集の締切7日前・3日前・当日にリマインダー",
      alert_plan_feature_self_serve: "決済後に自動で開始。解約やお支払い情報の変更はいつでもご自身で可能",
      alert_plan_subscribe_monthly: "月払いで申し込む",
      alert_plan_subscribe_yearly: "年払いで申し込む",
      alert_plan_unavailable: "メール配信のお申し込みはまだ受け付けていません。下の無料フィードは現在ご利用いただけます。",
      alert_plan_note: "決済と請求はStripeが処理します。アラートメールはモニタリング補助であり、法的助言ではありません。日本語の公式情報源が優先します。",
      alert_plan_manage: "ご契約中の方：契約の管理・解約",
      alert_feeds_title: "無料フィード",
      alert_feeds_body: "アカウント不要で分野をフォローできます。新たに検出された更新のRSSフィードと、受付中の意見募集の締切カレンダーです。",
      alert_feeds_area: "分野",
      alert_feeds_rss: "RSSフィード",
      alert_feeds_ics: "締切カレンダー（.ics）",
      alert_faq_title: "お申し込みの前に",
      alert_faq_start_q: "いつから配信されますか？",
      alert_faq_start_a:
        "決済後、次回の日次更新から配信されます。選択した分野で新たに検出された更新や締切リマインダーがある朝に配信し、お知らせする内容がない朝は配信しません。",
      alert_faq_contents_q: "ダイジェストには何が含まれますか？",
      alert_faq_contents_a:
        "選択した分野で本ダッシュボードが初めて検出した更新（英語タイトル、日本語の原題、段階、日本語の公式情報源へのリンク）と、近く締切を迎える意見募集です。「新たに検出」は新しい法令や規制を意味しません。要約は確認の優先順位付けのためのものであり、法的助言ではありません。",
      alert_faq_change_q: "分野を変更するには？",
      alert_faq_change_a: "分野は決済時に選択します。別の分野に変更する場合は、契約管理ページで現在の契約を解約し、新しい分野で改めてお申し込みください。",
      alert_faq_cancel_q: "解約やお支払い情報の変更は？",
      alert_faq_cancel_a:
        "各メールおよび上のリンクから契約管理ページを開けます。解約、月払い・年払いの切替、配信先メールアドレスの変更、カード情報の更新、請求書のダウンロードができます。解約は現在の請求期間の終了時に有効になります。",

      checkout_thanks_page_title: "お申し込み後のご案内 — Japan Legal Reform Watch",
      checkout_thanks_language: "言語",
      checkout_thanks_eyebrow: "メールダイジェスト",
      checkout_thanks_title: "お申し込み後のご案内",
      checkout_thanks_intro: "Stripeで支払いが確認されていれば、日次メールダイジェストは有効になっています。追加のお手続きは不要です。",
      checkout_thanks_plan_label: "お支払い",
      checkout_thanks_plan_monthly: "月払い — 月額19米ドル",
      checkout_thanks_plan_yearly: "年払い — 年額190米ドル",
      checkout_thanks_next_title: "今後の流れ",
      checkout_thanks_step1_title: "Stripeから領収書が届きます",
      checkout_thanks_step1_body: "領収書は決済時に入力したメールアドレスに送信されます。ダイジェストも同じアドレスに配信されます。",
      checkout_thanks_step2_title: "次回の日次更新から配信が始まります",
      checkout_thanks_step2_body:
        "ダッシュボードは毎朝（日本時間）更新されます。選択した分野で新たに検出された更新や締切リマインダーがある朝に配信し、お知らせする内容がない朝は配信しません。",
      checkout_thanks_step3_title: "契約はご自身で管理できます",
      checkout_thanks_step3_body:
        "各メールに記載された契約管理ページから、解約、月払い・年払いの切替、配信先メールアドレスの変更、カード情報の更新、請求書のダウンロードができます。",
      checkout_thanks_note_title: "このページは支払いを確認するものではありません",
      checkout_thanks_note_body: "Stripeからの領収書メールが支払いの確認となります。届いていない場合、決済が完了していない可能性があります。",
      checkout_thanks_dashboard: "ダッシュボードに戻る",
      checkout_thanks_manage: "契約管理ページを開く",
      checkout_thanks_trust: "アラートメールはモニタリング補助であり、法的助言ではありません。日本語の公式情報源が優先します。",
      checkout_thanks_legal_nav: "法的情報",

      load_more: "更新をさらに読み込む",
      empty_title: "該当する更新はありません。",
      empty_hint: "フィルターを解除するか、検索語を変更してください。",
      error_title: "更新を読み込めませんでした。",
      error_body: "本ダッシュボードは fetch() でデータを読み込むため、HTTP経由で表示する必要があります。ファイルを直接（file://）開いた場合は、プロジェクトのルートをローカルWebサーバーで配信してください（README.md参照）。",

      af_none: "適用中のフィルターなし",
      af_active_prefix: "適用中：",
      af_search: "検索：「{q}」",
      af_period: "対象期間：{v}",
      af_area: "分野：{v}",
      af_stage: "段階：{v}",
      af_source: "情報源：{v}",
      af_impact: "影響度：{v}",
      af_sort: "並び順：{v}",
      af_ai: "AI要約",
      af_newly: "新規検出",
      af_count: "適用中：{n}件",

      card_summary_heading: "要約",
      card_business_impact_heading: "事業への影響",
      card_recommended_action_heading: "推奨対応",
      card_source_name_label: "情報源",
      card_view_source: "日本語の公式情報源を見る →",
      card_source_note: "日本語の公式情報源が優先します。",
      copy_summary_label: "要約をコピー",
      copy_source_link_label: "情報源リンクをコピー",
      copy_actions_aria: "コピー操作",
      date_published: "公表日",
      date_first_detected: "初回検出日",
      date_last_checked: "最終確認日",
      impact_badge: "影響度：{level}",
      newly_detected: "新規検出",
      newly_detected_title: "本ダッシュボードでの初回検出日：{date}",
      newly_detected_aria: "新規検出。本ダッシュボードでの初回検出日：{date}。",

      badge_ai_summary: "AI要約",
      badge_ai_summary_title: "AIが生成したモニタリング要約を含みます。公式文書または法的助言ではありません。",
      badge_rule_based: "ルールベースのプレビュー",
      badge_rule_based_title: "AI要約前の項目であり、ルールベースの定型文を表示しています。",
      badge_ai_translation: "AI翻訳",
      translation_note: "この翻訳はモニタリング目的でAIが生成したものです。日本語の公式情報源が優先します。",
      translation_unavailable: "中国語訳を利用できないため、英語を表示しています。",
      japanese_summary_note: "この日本語要約は、日本語の原文メタデータを基にAIが直接作成したものです。日本語の公式情報源が優先します。",
      japanese_summary_unavailable: "この項目の日本語AI要約はまだ生成されていないため、英語要約を表示しています。",

      copy_summary_success: "要約をコピーしました",
      copy_source_success: "情報源リンクをコピーしました",
      copy_failed: "コピーに失敗しました",

      meta_showing: "該当 {total} 件中 {shown} 件を表示",
      meta_total: "全 {count} 件",
      meta_english_ai: "英語AI要約：{count} 件",
      meta_japanese_ai: "日本語AI要約：{count} 件",
      meta_chinese_translations: "中国語翻訳：{count} 件",
      meta_last_checked: "最終確認日：{date}",

      cs_title: "日本語タイトル",
      cs_en_ref_title: "英語参考タイトル",
      cs_ja_title: "日本語原題",
      cs_area: "分野",
      cs_stage: "段階",
      cs_impact: "影響度",
      cs_source: "情報源",
      cs_published: "公表日",
      cs_first_seen: "初回検出日",
      cs_summary: "要約",
      cs_business: "事業への影響",
      cs_action: "推奨対応",
      cs_official_source: "日本語の公式情報源",
      cs_note: "この日本語要約は、日本語の原文メタデータを基にAIが作成したモニタリング情報であり、法的助言ではありません。日本語の公式情報源が優先します。",
      cs_fallback: "日本語AI要約がまだ生成されていない項目は、英語要約で表示しています。",

      modal_title: "重要事項 — ご利用前にお読みください",
      modal_p1: "Japan Legal Reform Watch by LegalOSは、日本の法令・規制・行政動向に関する要約を一般的な情報提供のみを目的として掲載しています。",
      modal_li1: "本サイトの要約および翻訳は公式文書ではありません。権威ある本文として、必ず日本語の公式情報源をご確認ください。",
      modal_li2: "掲載内容は法的助言ではなく、弁護士と依頼者の関係を生じさせるものではありません。",
      modal_li3: "掲載情報の正確性、完全性または最新性を保証しません。",
      modal_li4: "事業上またはコンプライアンス上の判断については、資格を有する日本法の専門家にご相談ください。",
      modal_p2: "続行することにより、これらの条件を確認し同意したものとみなされます。適用法令で認められる最大限の範囲において、LegalOSは本サイトの利用から生じる責任を負いません。",
      modal_link: "法律上の注意事項・免責事項を全文で読む →",
      modal_accept: "内容を確認し、同意します",

      footer_brand_p: "Legal GPTが提供しています。日本の法令動向やリーガルテック関連情報もご覧ください。",
      footer_learn_more: "Japan Legal Reform Watchについて",
      footer_japan_updates: "日本の法令動向",
      footer_legal_en: "法律上の注意事項・免責事項（英語）",
      footer_disclaimer_en: "法律上の注意事項・免責事項（英語）",
      footer_disclaimer_ja: "法律上の注意事項・免責事項（日本語）",
      footer_bottom: "本サイトの要約は情報提供のみを目的とし、公式文書でも法的助言でもありません。詳細は法律上の注意事項をご確認ください。",

      lang_selector_label: "言語",
    },

    "zh-Hans": {
      document_title: "日本法律改革观察 by LegalOS",
      brand_h1: "日本法律改革观察 by LegalOS",
      tagline: "免费的日本法律与监管动态监测",
      header_about: "关于本工具",

      trust_title: "可靠性说明",
      trust_body:
        "本仪表板汇总日本政府及监管机构的官方信息。AI生成的摘要和译文仅用于信息监测，并非官方译文，" +
        "也不构成法律意见。采取行动前应核对日文官方来源。",

      ds_heading: "数据状态",
      ds_note: "仅为监测辅助。以日文官方来源为准。",
      ds_period: "期间",
      ds_updates: "更新总数",
      ds_archive_total: "档案总数",
      ds_sources: "涵盖来源",
      ds_english_ai_summaries: "英文AI摘要数",
      ds_japanese_ai_summaries: "日文AI摘要数",
      ds_chinese_translations: "中文翻译数",
      ds_open_pc: "公开征求意见中",
      ds_newly_detected: "新近收录（7天）",
      ds_latest_checked: "最后确认",
      ds_unknown: "未知",
      ds_status: "数据状态",
      ds_unavailable: "不可用",

      controls_filters_search: "筛选与搜索",
      controls_hide_filters: "隐藏筛选",
      qf_label: "快捷筛选",
      qf_pc_open: "公开征求意见中",
      qf_ai: "AI摘要",
      qf_newly: "新近收录",
      qf_medium: "中等影响",
      qf_reset: "重置",

      ctl_search: "搜索",
      ctl_search_placeholder: "搜索标题或摘要……",
      ctl_period: "期间",
      ctl_area: "领域",
      ctl_stage: "阶段",
      ctl_source: "来源",
      ctl_impact: "影响程度",
      ctl_sort: "排序",
      opt_all_areas: "全部领域",
      opt_all_stages: "全部阶段",
      opt_all_sources: "全部来源",
      opt_all_impacts: "全部影响程度",
      sort_relevance: "相关度",
      sort_published: "发布日期",
      sort_checked: "最后确认",
      sort_detected: "首次收录",
      period_latest: "最新（{year}）",
      period_all: "全部年份（加载较慢）",
      period_undated: "日期不明",
      period_loading: "正在加载所选期间……",

      export_button: "导出CSV",
      export_none: "没有可导出的匹配更新",
      export_label: "导出 {count} 条匹配更新为CSV",
      export_aria_default: "导出匹配更新为CSV",
      export_done: "已导出 {count} 条更新",
      export_failed: "导出失败",

      saved_search_open: "保存此搜索",
      saved_searches_count: "已保存的搜索（{count}）",
      saved_search_eyebrow: "监测偏好",
      saved_search_title: "已保存的搜索",
      saved_search_close: "关闭已保存的搜索",
      saved_search_current_title: "当前搜索",
      saved_search_name_label: "搜索名称",
      saved_search_name_placeholder: "例如：日本AI与隐私动态",
      saved_search_save: "保存搜索",
      saved_search_library_title: "保存在此浏览器中",
      saved_search_storage_note: "搜索条件仅保存在本设备中，不会创建账户或邮件订阅。",
      saved_search_capacity: "{count} / {max}",
      saved_search_empty: "此浏览器中尚未保存搜索。",
      saved_search_default_name: "监测搜索 {count}",
      saved_search_saved: "搜索已保存在此浏览器中。",
      saved_search_updated: "已更新保存的搜索。",
      saved_search_deleted: "已删除保存的搜索。",
      saved_search_loaded: "已加载保存的搜索。",
      saved_search_limit: "此浏览器最多可保存 {max} 个搜索。",
      saved_search_storage_error: "此浏览器无法保存搜索。",
      saved_search_unavailable: "此保存搜索不可用。",
      saved_search_load: "加载",
      saved_search_delete: "删除",
      alert_plan_label: "邮件提醒",
      alert_plan_title: "每日邮件摘要",
      alert_plan_price_monthly: "每月19美元",
      alert_plan_price_yearly: "或每年190美元",
      alert_plan_feature_area: "结账时选择一个领域或全部领域",
      alert_plan_feature_daily: "每天上午（日本时间）：本仪表板在您所选领域新检测到的更新",
      alert_plan_feature_deadlines: "官方来源提供结构化数据时，在公开征求意见截止前7天、前3天及当天提醒",
      alert_plan_feature_self_serve: "结账后自动开始；可随时自行取消或更改付款信息",
      alert_plan_subscribe_monthly: "按月订阅",
      alert_plan_subscribe_yearly: "按年订阅",
      alert_plan_unavailable: "邮件订阅尚未开放。下方的免费订阅源现已可用。",
      alert_plan_note: "结账和计费由 Stripe 处理。提醒邮件仅供监测参考，不构成法律建议；应以日文官方原始来源为准。",
      alert_plan_manage: "管理或取消现有订阅",
      alert_feeds_title: "免费订阅源",
      alert_feeds_body: "无需账户即可关注某一领域：新检测到的更新的 RSS 订阅源，以及正在征求意见的截止日期日历。",
      alert_feeds_area: "领域",
      alert_feeds_rss: "RSS 订阅源",
      alert_feeds_ics: "截止日期日历（.ics）",
      alert_faq_title: "订阅前须知",
      alert_faq_start_q: "摘要何时开始发送？",
      alert_faq_start_a: "从结账后的下一次每日更新开始。当您所选领域有新检测到的更新或截止日期提醒时，于当天上午发送；没有可报告内容时不发送。",
      alert_faq_contents_q: "摘要包含哪些内容？",
      alert_faq_contents_a:
        "本仪表板在您所选领域首次检测到的更新（英文标题、日文原题、阶段及日文官方原始来源链接），以及即将截止的公开征求意见。“新检测到”并不表示新的法律或法规。摘要仅用于初步筛选，不构成法律建议。",
      alert_faq_change_q: "如何更改领域？",
      alert_faq_change_a: "领域在结账时选择。如需关注其他领域，请在订阅管理页面取消当前订阅，然后以新的领域重新订阅。",
      alert_faq_cancel_q: "如何取消订阅或更新付款信息？",
      alert_faq_cancel_a:
        "可通过每封邮件及上方链接打开订阅管理页面，在该页面取消订阅、切换按月或按年付费、更改接收邮箱、更新银行卡及下载发票。取消将在当前计费周期结束时生效。",

      checkout_thanks_page_title: "结账后续 — Japan Legal Reform Watch",
      checkout_thanks_language: "语言",
      checkout_thanks_eyebrow: "邮件摘要",
      checkout_thanks_title: "结账后续",
      checkout_thanks_intro: "如果 Stripe 已确认付款，您的每日邮件摘要即已生效，无需其他操作。",
      checkout_thanks_plan_label: "付费方式",
      checkout_thanks_plan_monthly: "按月 — 每月19美元",
      checkout_thanks_plan_yearly: "按年 — 每年190美元",
      checkout_thanks_next_title: "后续流程",
      checkout_thanks_step1_title: "Stripe 将发送收据",
      checkout_thanks_step1_body: "收据将发送至您在结账时填写的邮箱。摘要也将发送至同一邮箱。",
      checkout_thanks_step2_title: "摘要从下一次每日更新开始发送",
      checkout_thanks_step2_body: "仪表板每天上午（日本时间）更新。当您所选领域有新检测到的更新或截止日期提醒时发送摘要；没有可报告内容时不发送。",
      checkout_thanks_step3_title: "自行管理订阅",
      checkout_thanks_step3_body: "每封邮件都附有订阅管理页面链接，可在该页面取消订阅、切换按月或按年付费、更改接收邮箱、更新银行卡及下载发票。",
      checkout_thanks_note_title: "本页面不代表付款确认",
      checkout_thanks_note_body: "Stripe 发送的收据邮件即为付款确认。如未收到，结账可能尚未完成。",
      checkout_thanks_dashboard: "返回仪表板",
      checkout_thanks_manage: "打开订阅管理页面",
      checkout_thanks_trust: "提醒邮件仅供监测参考，不构成法律建议。应以日文官方原始来源为准。",
      checkout_thanks_legal_nav: "法律信息",

      load_more: "加载更多更新",
      empty_title: "未找到匹配的更新。",
      empty_hint: "请尝试清除筛选条件或调整搜索关键词。",
      error_title: "无法加载更新。",
      error_body:
        "本仪表板通过 fetch() 加载数据，需要以 HTTP 方式访问页面。如果您直接打开了该文件（file://），" +
        "请使用本地 Web 服务器在项目根目录下提供服务（参见项目 README.md）。",

      af_none: "无活动筛选",
      af_active_prefix: "活动：",
      af_search: "搜索：“{q}”",
      af_period: "期间：{v}",
      af_area: "领域：{v}",
      af_stage: "阶段：{v}",
      af_source: "来源：{v}",
      af_impact: "影响程度：{v}",
      af_sort: "排序：{v}",
      af_ai: "AI摘要",
      af_newly: "新近收录",
      af_count: "活动：{n} 个筛选",

      card_summary_heading: "摘要",
      card_business_impact_heading: "业务影响",
      card_recommended_action_heading: "建议措施",
      card_source_name_label: "来源名称",
      card_view_source: "查看日文官方来源 →",
      card_source_note: "以日文官方来源（原文）为准。",
      copy_summary_label: "复制摘要",
      copy_source_link_label: "复制来源链接",
      copy_actions_aria: "复制操作",
      date_published: "发布日期",
      date_first_detected: "首次收录",
      date_last_checked: "最后确认",
      impact_badge: "{level}",
      newly_detected: "新近收录",
      newly_detected_title: "本仪表板于 {date} 首次检测到",
      newly_detected_aria: "新近收录。本仪表板于 {date} 首次检测到。",

      badge_ai_summary: "AI摘要",
      badge_ai_summary_title: "本条目包含AI生成的英文摘要，并非官方译文，也不构成法律意见。",
      badge_rule_based: "规则预览",
      badge_rule_based_title: "本条目尚未由AI摘要，当前使用基于规则的占位内容。",
      badge_ai_translation: "AI翻译",
      translation_note: "本译文由AI生成，仅用于信息监测。应以日文原文为准。",
      translation_unavailable: "中文翻译暂不可用，以下显示英文。",
      japanese_summary_note: "日文摘要由AI直接根据日文来源元数据生成；应以日文官方来源为准。",
      japanese_summary_unavailable: "日文AI摘要暂不可用，以下显示英文。",

      copy_summary_success: "摘要已复制",
      copy_source_success: "来源链接已复制",
      copy_failed: "复制失败",

      meta_showing: "显示 {total} 条匹配更新中的 {shown} 条",
      meta_total: "共 {count} 条更新",
      meta_english_ai: "英文AI摘要：{count}",
      meta_japanese_ai: "日文AI摘要：{count}",
      meta_chinese_translations: "中文翻译：{count}",
      meta_last_checked: "最后确认：{date}",

      cs_title: "中文标题",
      cs_en_ref_title: "英文参考标题",
      cs_ja_title: "日文原题",
      cs_area: "领域",
      cs_stage: "阶段",
      cs_impact: "影响程度",
      cs_source: "来源",
      cs_published: "发布日期",
      cs_first_seen: "首次收录日期",
      cs_summary: "摘要",
      cs_business: "业务影响",
      cs_action: "建议措施",
      cs_official_source: "日文官方来源",
      cs_note: "本摘要及译文由AI生成，仅用于信息监测，不构成法律意见。应以日文官方来源为准。",
      cs_fallback: "部分内容暂无中文翻译，以英文显示。",

      modal_title: "重要提示 — 使用前请阅读",
      modal_p1:
        "Japan Legal Reform Watch by LegalOS 仅出于一般信息目的，提供日本法律、监管及行政动态的英文摘要。",
      modal_li1: "本网站并非官方译文。请始终以日文官方来源作为权威文本。",
      modal_li2: "本内容不构成法律意见，也不建立律师—委托人关系。",
      modal_li3: "我们不保证所呈现信息的准确性、完整性或时效性。",
      modal_li4: "如涉及业务或合规决策，请咨询具备资质的日本法律顾问。",
      modal_p2:
        "继续使用即表示您知悉并接受上述条款。在适用法律允许的最大范围内，LegalOS 对使用本网站所产生的一切责任概不负责。",
      modal_link: "阅读完整法律声明与免责声明 →",
      modal_accept: "我已理解并同意",

      footer_brand_p: "由 Legal GPT 创建。探索更多日本法律动态与法律科技工具。",
      footer_learn_more: "了解更多关于 Japan Legal Reform Watch",
      footer_japan_updates: "日本法律动态",
      footer_legal_en: "法律声明 / 免责声明（英文）",
      footer_disclaimer_en: "法律声明与免责声明（英文）",
      footer_disclaimer_ja: "法律声明与免责声明（日文）",
      footer_bottom:
        "本网站仅出于信息目的提供英文摘要，并非官方译文，也不构成法律意见。详情请参见完整法律声明。",

      lang_selector_label: "语言",
    },
  };

  // -------- Controlled-vocabulary label maps (internal value -> localized) --------
  // English keeps the internal value (already English) or a caller-supplied
  // display name. Unmapped values fall back to the English display name.
  var AREA_LABELS = {
    ja: {
      "Data / Privacy / AI": "データ・プライバシー・AI",
      "Economic Security / FDI": "経済安全保障・対内直接投資",
      "Antitrust / Fair Trade": "独占禁止・公正取引",
      "Finance / AML": "金融・マネーロンダリング対策",
      "Tax / Stamp Duty": "税務・印紙税",
      "Labor / Employment": "労働・雇用",
      "Energy / Environment": "エネルギー・環境",
      "Consumer / Advertising": "消費者・広告",
      "Corporate / Governance": "会社・ガバナンス",
      "Transport / Infrastructure": "運輸・インフラ",
      "Food / Agriculture": "食品・農林水産",
      "Real Estate / Land Use": "不動産・土地利用",
      "Public Safety / Disaster Management": "公共安全・防災",
      "Healthcare / Pharmaceuticals": "医療・医薬品",
      "Other": "その他",
    },
    "zh-Hans": {
      "Data / Privacy / AI": "数据 / 隐私 / AI",
      "Economic Security / FDI": "经济安全 / 外商直接投资",
      "Antitrust / Fair Trade": "反垄断 / 公平交易",
      "Finance / AML": "金融 / 反洗钱",
      "Tax / Stamp Duty": "税务 / 印花税",
      "Labor / Employment": "劳动 / 雇佣",
      "Energy / Environment": "能源 / 环境",
      "Consumer / Advertising": "消费者 / 广告",
      "Corporate / Governance": "公司 / 治理",
      "Transport / Infrastructure": "运输 / 基础设施",
      "Food / Agriculture": "食品 / 农业",
      "Real Estate / Land Use": "房地产 / 土地利用",
      "Public Safety / Disaster Management": "公共安全 / 灾害管理",
      "Healthcare / Pharmaceuticals": "医疗 / 制药",
      "Other": "其他",
    },
  };
  var STAGE_LABELS = {
    ja: {
      "Public Comment Open": "意見募集中",
      "Public Comment Closed": "意見募集終了",
      "Public Comment Results Published": "意見募集結果公示",
      "Draft Guideline": "ガイドライン案",
      "Bill Submitted": "法案提出",
      "Enacted": "成立",
      "Promulgated": "公布",
      "Scheduled to Take Effect": "施行予定",
      "In Force": "施行済み",
      "Government Announcement": "政府発表",
      "Court Decision": "裁判例",
      "Enforcement Action": "執行措置",
    },
    "zh-Hans": {
      "Public Comment Open": "公开征求意见中",
      "Public Comment Closed": "公开征求意见已截止",
      "Public Comment Results Published": "征求意见结果已公布",
      "Draft Guideline": "指南草案",
      "Bill Submitted": "议案已提交",
      "Enacted": "已制定",
      "Promulgated": "已公布",
      "Scheduled to Take Effect": "预定生效",
      "In Force": "已生效",
      "Government Announcement": "政府公告",
      "Court Decision": "法院裁判",
      "Enforcement Action": "执法行动",
    },
  };
  var IMPACT_LABELS = {
    ja: { "High": "高", "Medium": "中", "Low": "低" },
    "zh-Hans": { "High": "高影响", "Medium": "中等影响", "Low": "低影响" },
  };
  var SOURCE_LABELS = {
    ja: {
      "e-Gov Public Comment (意見募集案件一覧)": "e-Gov パブリックコメント",
      "House of Representatives (衆議院) 議案情報": "衆議院 — 議案情報",
      "e-Gov Law Search (法令更新一覧)": "e-Gov法令検索 — 法令更新一覧",
      "Japan Exchange Group (JPX) Public Comments": "日本取引所グループ（JPX）— パブリックコメント",
      "Tokyo Stock Exchange (JPX) Rule Revisions": "東京証券取引所（JPX）— 規則改正",
      "Pharmaceuticals and Medical Devices Agency (PMDA) Safety Updates": "医薬品医療機器総合機構（PMDA）— 安全性情報",
      "Japan Securities Dealers Association (JSDA) Public Comments": "日本証券業協会（JSDA）— パブリックコメント",
      "Japan Securities Dealers Association (JSDA) Public Comment Results": "日本証券業協会（JSDA）— パブリックコメント結果",
      "Courts in Japan (裁判所) Recent Supreme Court Decisions": "裁判所 — 最近の最高裁判例",
      "Securities and Exchange Surveillance Commission (SESC) Enforcement Updates": "証券取引等監視委員会（SESC）— 執行情報",
      "Financial Services Agency (金融庁) 新着情報": "金融庁（FSA）",
      "経済産業省 (METI) ニュースリリース": "経済産業省（METI）",
      "Ministry of Health, Labour and Welfare (厚生労働省) 新着情報": "厚生労働省（MHLW）",
      "Digital Agency (デジタル庁) 新着・更新": "デジタル庁",
      "消費者庁 (CAA) 新着情報": "消費者庁（CAA）",
      "個人情報保護委員会 (PPC) 新着情報": "個人情報保護委員会（PPC）",
      "公正取引委員会 (JFTC) 報道発表": "公正取引委員会（JFTC）",
      "法務省 (MOJ) 新着情報": "法務省（MOJ）",
      "環境省 (MOE) 報道発表": "環境省（MOE）",
      "財務省 (MOF) 新着情報": "財務省（MOF）",
      "国税庁 (NTA) 新着・通達": "国税庁（NTA）",
      "総務省 (MIC) 新着情報": "総務省（MIC）",
      "国土交通省 (MLIT) 報道発表": "国土交通省（MLIT）",
      "農林水産省 (MAFF) 報道発表": "農林水産省（MAFF）",
    },
    "zh-Hans": {
      "e-Gov Public Comment (意見募集案件一覧)": "e-Gov 公开征求意见",
      "Financial Services Agency (金融庁) 新着情報": "金融厅（FSA）",
      "経済産業省 (METI) ニュースリリース": "经济产业省（METI）",
      "Ministry of Health, Labour and Welfare (厚生労働省) 新着情報": "厚生劳动省（MHLW）",
      "Digital Agency (デジタル庁) 新着・更新": "数字厅（Digital Agency）",
      "消費者庁 (CAA) 新着情報": "消费者厅（CAA）",
      "個人情報保護委員会 (PPC) 新着情報": "个人信息保护委员会（PPC）",
      "公正取引委員会 (JFTC) 報道発表": "公平交易委员会（JFTC）",
      "法務省 (MOJ) 新着情報": "法务省（MOJ）",
      "環境省 (MOE) 報道発表": "环境省（MOE）",
      "財務省 (MOF) 新着情報": "财务省（MOF）",
      "総務省 (MIC) 新着情報": "总务省（MIC）",
      "国土交通省 (MLIT) 報道発表": "国土交通省（MLIT）",
      "農林水産省 (MAFF) 報道発表": "农林水产省（MAFF）",
      "Japan Securities Dealers Association (JSDA) Public Comments": "日本证券业协会（JSDA）— 公开征求意见",
      "Japan Securities Dealers Association (JSDA) Public Comment Results": "日本证券业协会（JSDA）— 征求意见结果",
      "Courts in Japan (裁判所) Recent Supreme Court Decisions": "日本法院 — 近期最高法院裁判",
      "Securities and Exchange Surveillance Commission (SESC) Enforcement Updates": "证券交易等监视委员会（SESC）— 执法动态",
    },
  };

  // Fixed 17-column Chinese CSV header layout (Internal ID stays last). The
  // English CSV (16 columns) is owned by app.js and is intentionally unchanged.
  var CSV_HEADERS_ZH = [
    "中文标题",
    "英文参考标题",
    "日文原题",
    "领域",
    "阶段",
    "影响程度",
    "来源",
    "日文官方来源URL",
    "发布日期",
    "首次收录日期",
    "最后确认日期",
    "摘要类型",
    "摘要",
    "业务影响",
    "建议措施",
    "排序分数",
    "内部ID",
  ];
  var CSV_HEADERS_JA = [
    "日本語原題",
    "英語参考タイトル",
    "分野",
    "段階",
    "影響度",
    "情報源",
    "日本語公式情報源URL",
    "公表日",
    "初回検出日",
    "最終確認日",
    "要約種別",
    "要約",
    "事業への影響",
    "推奨対応",
    "ランキングスコア",
    "内部ID",
  ];

  // -------- Core helpers --------
  var currentLang = DEFAULT_LANG;

  function normalize(lang) {
    return SUPPORTED.indexOf(lang) >= 0 ? lang : DEFAULT_LANG;
  }
  function getLang() {
    return currentLang;
  }
  function setLang(lang) {
    currentLang = normalize(lang);
    return currentLang;
  }

  function interpolate(template, params) {
    if (!params) return template;
    return template.replace(/\{(\w+)\}/g, function (match, key) {
      return Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : match;
    });
  }

  function t(key, params) {
    var table = STRINGS[currentLang] || STRINGS.en;
    var value = table[key];
    if (value == null) value = STRINGS.en[key];
    if (value == null) value = key;
    return interpolate(value, params);
  }

  function labelFrom(map, value, fallback) {
    var table = map[currentLang];
    if (table && Object.prototype.hasOwnProperty.call(table, value)) {
      return table[value];
    }
    return fallback != null ? fallback : value;
  }

  function areaLabel(value) {
    return labelFrom(AREA_LABELS, value, value);
  }
  function stageLabel(value) {
    return labelFrom(STAGE_LABELS, value, value);
  }
  function impactLabel(value) {
    return labelFrom(IMPACT_LABELS, value, value);
  }
  // englishFallback is the caller's English display name (e.g. formatSourceDisplayName).
  function sourceLabel(value, englishFallback) {
    var fallback = englishFallback != null ? englishFallback : value;
    return labelFrom(SOURCE_LABELS, value, fallback);
  }
  function summaryTypeLabel(summarySource) {
    return summarySource === "claude" ? t("badge_ai_summary") : t("badge_rule_based");
  }
  function csvHeadersZh() {
    return CSV_HEADERS_ZH.slice();
  }
  function csvHeadersLocalized(lang) {
    return normalize(lang) === "ja" ? CSV_HEADERS_JA.slice() : CSV_HEADERS_ZH.slice();
  }
  function disclaimerPath() {
    return currentLang === "ja" ? "legal/disclaimer_ja.html" : "legal/disclaimer_en.html";
  }

  // -------- Static DOM application --------
  // English is restored from the captured original markup (loss-less). Non-English
  // overlays are written as textContent so dictionary strings can never inject HTML.
  var ATTR_KEYS = ["placeholder", "aria-label", "title"];

  function applyStatic(root) {
    var scope = root || document;
    var lang = currentLang;

    scope.querySelectorAll("[data-i18n]").forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      if (el.__jlrwOrigHTML === undefined) el.__jlrwOrigHTML = el.innerHTML;
      if (lang === DEFAULT_LANG) {
        el.innerHTML = el.__jlrwOrigHTML;
      } else {
        el.textContent = t(key);
      }
    });

    ATTR_KEYS.forEach(function (attr) {
      var selector = "[data-i18n-" + attr + "]";
      scope.querySelectorAll(selector).forEach(function (el) {
        var key = el.getAttribute("data-i18n-" + attr);
        var store = "__jlrwOrigAttr_" + attr;
        if (el[store] === undefined) el[store] = el.getAttribute(attr);
        if (lang === DEFAULT_LANG) {
          if (el[store] != null) el.setAttribute(attr, el[store]);
        } else {
          el.setAttribute(attr, t(key));
        }
      });
    });
  }

  window.JLRW_I18N = {
    DEFAULT_LANG: DEFAULT_LANG,
    SUPPORTED: SUPPORTED.slice(),
    STORAGE_KEY: STORAGE_KEY,
    normalize: normalize,
    getLang: getLang,
    setLang: setLang,
    t: t,
    areaLabel: areaLabel,
    stageLabel: stageLabel,
    impactLabel: impactLabel,
    sourceLabel: sourceLabel,
    summaryTypeLabel: summaryTypeLabel,
    csvHeadersZh: csvHeadersZh,
    csvHeadersLocalized: csvHeadersLocalized,
    disclaimerPath: disclaimerPath,
    applyStatic: applyStatic,
  };
})();
