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
      alert_pilot_label: "Paid pilot",
      alert_pilot_title: "Receive matching updates by email",
      alert_pilot_body:
        "Receive personalized alerts based on saved searches, including public-comment deadline reminders when structured official data is available.",
      alert_pilot_request: "Request pilot access →",
      alert_pilot_form_eyebrow: "Founding pilot",
      alert_pilot_form_title: "Request personalized alerts",
      alert_pilot_price_note: "Pilot plans start at US$29/month.",
      alert_pilot_form_intro:
        "Submitting this form does not create a subscription or charge a fee. After sending, you can continue to secure checkout; we review the requested monitoring scope before activation.",
      alert_pilot_scope_warning:
        "No topic or filter is active in the dashboard. A broad request may match hundreds of updates; describe a specific focus below before submitting.",
      alert_pilot_plans_title: "Choose a monitoring scope",
      alert_pilot_plans_billing: "Recurring monthly subscription, billed in US dollars.",
      alert_pilot_plan_name_pro: "Pro",
      alert_pilot_plan_name_team: "Team",
      alert_pilot_plan_pro_price: "US$29/month",
      alert_pilot_plan_team_price: "US$149/month",
      alert_pilot_plan_pro_audience: "For an individual monitoring need",
      alert_pilot_plan_team_audience: "For a small legal or compliance team",
      alert_pilot_plan_pro_criteria: "1 monitoring criterion",
      alert_pilot_plan_team_criteria: "Up to 5 monitoring criteria",
      alert_pilot_plan_pro_recipients: "1 email recipient",
      alert_pilot_plan_team_recipients: "Up to 5 email recipients",
      alert_pilot_plan_delivery: "Daily or weekly digest",
      alert_pilot_plan_deadlines:
        "Public-comment deadline reminders when structured official data is available",
      alert_pilot_select_pro: "Select Pro",
      alert_pilot_select_team: "Select Team",
      alert_pilot_scope_note:
        "Monitoring criteria, sources, and delivery frequency are confirmed before activation; additional Team criteria and recipients are collected during that review. Alert emails are monitoring aids, not legal advice.",
      alert_pilot_name: "Name",
      alert_pilot_email: "Work email",
      alert_pilot_company: "Company / organization",
      alert_pilot_plan: "Plan of interest",
      alert_pilot_plan_pro: "Pro — US$29/month",
      alert_pilot_plan_team: "Team — US$149/month",
      alert_pilot_frequency: "Preferred frequency",
      alert_pilot_frequency_daily: "Daily digest",
      alert_pilot_frequency_weekly: "Weekly digest",
      alert_pilot_focus: "Monitoring focus / business context",
      alert_pilot_focus_placeholder:
        "e.g. AI governance rules affecting cloud services in Japan",
      alert_pilot_focus_help:
        "Describe the topic, regulator, keywords, or business activity that should define this monitoring criterion.",
      alert_pilot_consent:
        "I agree to send my contact details and current search criteria for this pilot request.",
      alert_pilot_privacy: "Privacy policy",
      alert_pilot_submit: "Send pilot request",
      alert_pilot_submitting: "Sending request...",
      alert_pilot_success_checkout:
        "Request received. Continue to secure checkout to start the selected subscription; alerts are activated after the monitoring scope is reviewed.",
      alert_pilot_success_manual:
        "Request received. We will contact you to confirm payment details and activation.",
      alert_pilot_reference: "Request reference",
      alert_pilot_validation: "Please complete the required fields.",
      alert_pilot_focus_validation:
        "Please describe the monitoring focus in at least 10 characters.",
      alert_pilot_failed: "The request could not be sent. Please use the contact page instead.",
      alert_pilot_fallback: "Use the contact page instead",
      alert_pilot_checkout: "Continue to secure checkout",
      alert_pilot_checkout_plan: "Continue to secure checkout — {plan}",
      alert_pilot_faq_title: "Before you subscribe",
      alert_pilot_faq_criterion_q: "What is a monitoring criterion?",
      alert_pilot_faq_criterion_a:
        "A criterion is one saved-search configuration: its keywords, filters, and selected period. We confirm the practical scope before activation.",
      alert_pilot_faq_start_q: "When do alerts start?",
      alert_pilot_faq_start_a:
        "Alerts start after payment is matched to your request, the monitoring scope is reviewed, and LegalOS sends an activation email. Checkout alone does not activate monitoring.",
      alert_pilot_faq_contents_q: "What does an alert contain?",
      alert_pilot_faq_contents_a:
        "Each alert identifies matching dashboard updates and links to the original Japanese official source. Summaries are for triage and are not legal advice.",
      alert_pilot_faq_cancel_q: "How do I change or cancel a plan?",
      alert_pilot_faq_cancel_a:
        "Use the LegalOS contact page to request a plan change or cancellation. We will confirm the effective timing and any billing effect.",
      alert_pilot_contact: "Contact LegalOS",

      checkout_thanks_page_title: "Checkout follow-up — Japan Legal Reform Watch",
      checkout_thanks_language: "Language",
      checkout_thanks_eyebrow: "Checkout follow-up",
      checkout_thanks_title: "Checkout follow-up",
      checkout_thanks_intro:
        "If Stripe has confirmed payment, your subscription request is ready for our monitoring-scope review.",
      checkout_thanks_plan_label: "Selected plan",
      checkout_thanks_plan_pro: "Pro — US$29/month",
      checkout_thanks_plan_team: "Team — US$149/month",
      checkout_thanks_next_title: "What happens next",
      checkout_thanks_step1_title: "We match the payment to your pilot request",
      checkout_thanks_step1_body:
        "We use the non-sensitive request reference attached to checkout and the pilot inquiry submitted before payment.",
      checkout_thanks_step2_title: "We review the requested monitoring scope",
      checkout_thanks_step2_body:
        "We confirm that the selected sources, topics, and delivery frequency can be supported for the pilot.",
      checkout_thanks_step3_title: "You receive an activation email",
      checkout_thanks_step3_body:
        "Alerts begin only after LegalOS sends an activation message to the work email in your pilot request.",
      checkout_thanks_note_title: "Activation is not automatic",
      checkout_thanks_note_body:
        "Payment confirms the subscription request. It does not mean that monitoring has already started.",
      checkout_thanks_dashboard: "Return to the dashboard",
      checkout_thanks_contact: "Contact LegalOS",
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
      alert_pilot_label: "有料パイロット",
      alert_pilot_title: "該当する更新をメールで受け取る",
      alert_pilot_body: "保存した検索条件に基づく個別アラートを受け取れます。公式情報に構造化された期限データがある場合は、意見募集期限のリマインダーも含まれます。",
      alert_pilot_request: "パイロット利用を申し込む →",
      alert_pilot_form_eyebrow: "初期パイロット",
      alert_pilot_form_title: "個別アラートを申し込む",
      alert_pilot_price_note: "パイロットプランは月額29米ドルからです。",
      alert_pilot_form_intro: "このフォームの送信だけでは契約や課金は発生しません。送信後、安全なStripe決済へ進めます。アラートを有効にする前に、ご希望のモニタリング範囲を確認します。",
      alert_pilot_scope_warning: "現在、トピックまたはフィルターが指定されていません。条件が広すぎると数百件に該当する可能性があるため、送信前に具体的な対象を以下に記載してください。",
      alert_pilot_plans_title: "モニタリング範囲を選択",
      alert_pilot_plans_billing: "米ドル建ての月額継続課金です。",
      alert_pilot_plan_name_pro: "Pro",
      alert_pilot_plan_name_team: "Team",
      alert_pilot_plan_pro_price: "月額29米ドル",
      alert_pilot_plan_team_price: "月額149米ドル",
      alert_pilot_plan_pro_audience: "個人のモニタリング向け",
      alert_pilot_plan_team_audience: "小規模な法務・コンプライアンスチーム向け",
      alert_pilot_plan_pro_criteria: "モニタリング条件 1件",
      alert_pilot_plan_team_criteria: "モニタリング条件 最大5件",
      alert_pilot_plan_pro_recipients: "メール受信者 1名",
      alert_pilot_plan_team_recipients: "メール受信者 最大5名",
      alert_pilot_plan_delivery: "日次または週次ダイジェスト",
      alert_pilot_plan_deadlines: "公式の構造化データがある場合、意見募集期限を通知",
      alert_pilot_select_pro: "Proを選択",
      alert_pilot_select_team: "Teamを選択",
      alert_pilot_scope_note: "モニタリング条件、情報源および配信頻度は有効化前に確認します。Teamの追加条件・受信者はその確認時にお伺いします。アラートメールはモニタリング補助であり、法的助言ではありません。",
      alert_pilot_name: "氏名",
      alert_pilot_email: "勤務先メールアドレス",
      alert_pilot_company: "会社・組織名",
      alert_pilot_plan: "希望プラン",
      alert_pilot_plan_pro: "Pro — 月額29米ドル",
      alert_pilot_plan_team: "Team — 月額149米ドル",
      alert_pilot_frequency: "希望する配信頻度",
      alert_pilot_frequency_daily: "日次ダイジェスト",
      alert_pilot_frequency_weekly: "週次ダイジェスト",
      alert_pilot_focus: "モニタリング対象・事業上の背景",
      alert_pilot_focus_placeholder: "例：日本国内のクラウドサービスに影響するAIガバナンス規制",
      alert_pilot_focus_help: "モニタリング条件を定めるトピック、規制当局、キーワードまたは事業活動を記載してください。",
      alert_pilot_consent: "本パイロット申込のため、連絡先および現在の検索条件を送信することに同意します。",
      alert_pilot_privacy: "プライバシーポリシー",
      alert_pilot_submit: "パイロット申込を送信",
      alert_pilot_submitting: "送信しています…",
      alert_pilot_success_checkout: "申込を受け付けました。選択した契約を開始するには安全な決済へお進みください。モニタリング範囲の確認後にアラートを有効化します。",
      alert_pilot_success_manual: "申込を受け付けました。支払方法と有効化についてご連絡します。",
      alert_pilot_reference: "申込参照番号",
      alert_pilot_validation: "必須項目を入力してください。",
      alert_pilot_focus_validation: "モニタリング対象を10文字以上で記載してください。",
      alert_pilot_failed: "申込を送信できませんでした。お問い合わせページをご利用ください。",
      alert_pilot_fallback: "お問い合わせページを利用",
      alert_pilot_checkout: "安全な決済へ進む",
      alert_pilot_checkout_plan: "安全な決済へ進む — {plan}",
      alert_pilot_faq_title: "お申し込み前の確認事項",
      alert_pilot_faq_criterion_q: "モニタリング条件とは何ですか？",
      alert_pilot_faq_criterion_a: "モニタリング条件とは、キーワード、フィルターおよび対象期間を含む1つの保存済み検索設定です。有効化前に、実際に対応可能な範囲を確認します。",
      alert_pilot_faq_start_q: "アラートはいつ始まりますか？",
      alert_pilot_faq_start_a: "支払いと申込の照合、モニタリング範囲の確認を経て、LegalOSが有効化メールを送信した後に開始します。決済だけでは自動的に有効化されません。",
      alert_pilot_faq_contents_q: "アラートには何が含まれますか？",
      alert_pilot_faq_contents_a: "各アラートには該当するダッシュボード更新と、日本語の公式情報源へのリンクが含まれます。要約は確認対象を絞るための補助であり、法的助言ではありません。",
      alert_pilot_faq_cancel_q: "プランの変更・解約はどうすればよいですか？",
      alert_pilot_faq_cancel_a: "LegalOSのお問い合わせページから変更または解約をご依頼ください。適用時期および請求への影響をご案内します。",
      alert_pilot_contact: "LegalOSに問い合わせる",

      checkout_thanks_page_title: "決済後のご案内 — Japan Legal Reform Watch",
      checkout_thanks_language: "言語",
      checkout_thanks_eyebrow: "決済後のご案内",
      checkout_thanks_title: "決済後のご案内",
      checkout_thanks_intro: "Stripeで支払いが確認された場合、契約申込はモニタリング範囲の確認へ進みます。",
      checkout_thanks_plan_label: "選択したプラン",
      checkout_thanks_plan_pro: "Pro — 月額29米ドル",
      checkout_thanks_plan_team: "Team — 月額149米ドル",
      checkout_thanks_next_title: "今後の流れ",
      checkout_thanks_step1_title: "支払いとパイロット申込を照合します",
      checkout_thanks_step1_body: "決済に付された機微情報を含まない申込参照番号と、支払前に送信されたパイロット申込を使用して照合します。",
      checkout_thanks_step2_title: "ご希望のモニタリング範囲を確認します",
      checkout_thanks_step2_body: "選択された情報源、トピックおよび配信頻度がパイロットで対応可能か確認します。",
      checkout_thanks_step3_title: "有効化メールをお送りします",
      checkout_thanks_step3_body: "LegalOSがパイロット申込時の勤務先メールアドレスへ有効化通知を送信した後に、アラートが始まります。",
      checkout_thanks_note_title: "自動的には有効化されません",
      checkout_thanks_note_body: "支払いによって契約申込が確認されますが、モニタリングがすでに開始したことを意味しません。",
      checkout_thanks_dashboard: "ダッシュボードに戻る",
      checkout_thanks_contact: "LegalOSに問い合わせる",
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
      alert_pilot_label: "付费试点",
      alert_pilot_title: "通过邮件接收匹配的更新",
      alert_pilot_body: "根据保存的搜索接收个性化提醒；如官方来源提供结构化数据，还可包括公开征求意见截止日期提醒。",
      alert_pilot_request: "申请试点使用 →",
      alert_pilot_form_eyebrow: "创始试点",
      alert_pilot_form_title: "申请个性化提醒",
      alert_pilot_price_note: "试点方案每月29美元起。",
      alert_pilot_form_intro: "提交此表单不会创建订阅或产生费用。发送后，您可以继续前往 Stripe 安全结账；我们会在启用提醒前审核您请求的监测范围。",
      alert_pilot_scope_warning: "仪表板当前没有启用主题或筛选条件。过于宽泛的申请可能匹配数百条更新；请在提交前于下方说明具体监测重点。",
      alert_pilot_plans_title: "选择监测范围",
      alert_pilot_plans_billing: "按月自动续订，以美元计费。",
      alert_pilot_plan_name_pro: "Pro",
      alert_pilot_plan_name_team: "Team",
      alert_pilot_plan_pro_price: "每月29美元",
      alert_pilot_plan_team_price: "每月149美元",
      alert_pilot_plan_pro_audience: "适合个人监测需求",
      alert_pilot_plan_team_audience: "适合小型法务或合规团队",
      alert_pilot_plan_pro_criteria: "1项监测条件",
      alert_pilot_plan_team_criteria: "最多5项监测条件",
      alert_pilot_plan_pro_recipients: "1名邮件收件人",
      alert_pilot_plan_team_recipients: "最多5名邮件收件人",
      alert_pilot_plan_delivery: "每日或每周摘要",
      alert_pilot_plan_deadlines: "官方来源提供结构化数据时，包含公开征求意见截止日期提醒",
      alert_pilot_select_pro: "选择 Pro",
      alert_pilot_select_team: "选择 Team",
      alert_pilot_scope_note: "监测条件、信息来源和发送频率将在启用前确认；Team 的其他条件和收件人将在审核时收集。提醒邮件仅供监测参考，不构成法律建议。",
      alert_pilot_name: "姓名",
      alert_pilot_email: "工作邮箱",
      alert_pilot_company: "公司 / 机构",
      alert_pilot_plan: "意向方案",
      alert_pilot_plan_pro: "Pro — 每月29美元",
      alert_pilot_plan_team: "Team — 每月149美元",
      alert_pilot_frequency: "希望的频率",
      alert_pilot_frequency_daily: "每日摘要",
      alert_pilot_frequency_weekly: "每周摘要",
      alert_pilot_focus: "监测重点 / 业务背景",
      alert_pilot_focus_placeholder: "例如：影响日本云服务的人工智能治理规则",
      alert_pilot_focus_help: "请说明用于界定该监测条件的主题、监管机构、关键词或业务活动。",
      alert_pilot_consent: "我同意为本次试点申请发送联系方式和当前搜索条件。",
      alert_pilot_privacy: "隐私政策",
      alert_pilot_submit: "发送试点申请",
      alert_pilot_submitting: "正在发送申请……",
      alert_pilot_success_checkout: "申请已收到。请继续安全结账以开始所选订阅；我们会在审核监测范围后启用提醒。",
      alert_pilot_success_manual: "申请已收到。我们会联系您确认付款详情和启用安排。",
      alert_pilot_reference: "申请编号",
      alert_pilot_validation: "请填写必填项目。",
      alert_pilot_focus_validation: "请用至少10个字符说明监测重点。",
      alert_pilot_failed: "无法发送申请。请改用联系页面。",
      alert_pilot_fallback: "改用联系页面",
      alert_pilot_checkout: "继续安全结账",
      alert_pilot_checkout_plan: "继续安全结账 — {plan}",
      alert_pilot_faq_title: "订阅前须知",
      alert_pilot_faq_criterion_q: "什么是监测条件？",
      alert_pilot_faq_criterion_a: "一项监测条件是一个保存的搜索配置，包括关键词、筛选条件和所选期间。我们会在启用前确认实际可支持的范围。",
      alert_pilot_faq_start_q: "提醒何时开始？",
      alert_pilot_faq_start_a: "付款与申请匹配、监测范围审核完成且 LegalOS 发送启用邮件后，提醒才会开始。仅完成结账不会自动启用监测。",
      alert_pilot_faq_contents_q: "提醒包含哪些内容？",
      alert_pilot_faq_contents_a: "每封提醒会标明匹配的仪表板更新，并链接至日文官方原始来源。摘要仅供筛选参考，不构成法律建议。",
      alert_pilot_faq_cancel_q: "如何变更或取消方案？",
      alert_pilot_faq_cancel_a: "请通过 LegalOS 联系页面申请变更或取消方案。我们会确认生效时间以及对账单的影响。",
      alert_pilot_contact: "联系 LegalOS",

      checkout_thanks_page_title: "结账后续 — Japan Legal Reform Watch",
      checkout_thanks_language: "语言",
      checkout_thanks_eyebrow: "结账后续",
      checkout_thanks_title: "结账后续",
      checkout_thanks_intro: "如果 Stripe 已确认付款，您的订阅申请将进入监测范围审核。",
      checkout_thanks_plan_label: "所选方案",
      checkout_thanks_plan_pro: "Pro — 每月29美元",
      checkout_thanks_plan_team: "Team — 每月149美元",
      checkout_thanks_next_title: "接下来的流程",
      checkout_thanks_step1_title: "我们会将付款与您的试点申请匹配",
      checkout_thanks_step1_body: "我们会使用结账时附带的非敏感申请编号，以及付款前提交的试点申请进行匹配。",
      checkout_thanks_step2_title: "我们会审核所申请的监测范围",
      checkout_thanks_step2_body: "我们会确认试点是否能够支持所选信息源、主题和发送频率。",
      checkout_thanks_step3_title: "您会收到启用邮件",
      checkout_thanks_step3_body: "只有在 LegalOS 向试点申请中的工作邮箱发送启用通知后，提醒才会开始。",
      checkout_thanks_note_title: "提醒不会自动启用",
      checkout_thanks_note_body: "付款表示订阅申请已确认，但不代表监测已经开始。",
      checkout_thanks_dashboard: "返回仪表板",
      checkout_thanks_contact: "联系 LegalOS",
      checkout_thanks_trust: "提醒邮件仅供监测参考，不构成法律建议。日文官方原始来源仍为权威依据。",
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
