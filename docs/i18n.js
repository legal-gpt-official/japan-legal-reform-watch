/* =============================================================
   Japan Legal Reform Watch by LegalOS — i18n layer
   Single namespace (window.JLRW_I18N). English is canonical; Simplified
   Chinese (zh-Hans) is an optional overlay. Every English string here mirrors
   the existing UI exactly, so switching back to English is loss-less. All
   localized strings (including the Chinese CSV layout and copy-summary labels)
   live here so app.js stays ASCII and free of scattered language branches.
   Vanilla JS, no dependencies.
   ============================================================= */

(function () {
  "use strict";

  var DEFAULT_LANG = "en";
  var SUPPORTED = ["en", "zh-Hans"];
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
      ds_ai_summaries: "AI summaries",
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

      // Copy feedback
      copy_summary_success: "Summary copied",
      copy_source_success: "Source link copied",
      copy_failed: "Copy failed",

      // Results meta
      meta_showing: "Showing {shown} of {total} matching updates",
      meta_total: "{count} total updates",
      meta_ai: "AI summaries: {count}",
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
      footer_bottom:
        "This site provides English summaries for informational purposes only. It is not an official " +
        "translation and not legal advice. See the full Legal Notice for details.",

      // Language selector
      lang_selector_label: "Language",
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
      ds_ai_summaries: "AI摘要数",
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

      copy_summary_success: "摘要已复制",
      copy_source_success: "来源链接已复制",
      copy_failed: "复制失败",

      meta_showing: "显示 {total} 条匹配更新中的 {shown} 条",
      meta_total: "共 {count} 条更新",
      meta_ai: "AI摘要：{count}",
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
      footer_bottom:
        "本网站仅出于信息目的提供英文摘要，并非官方译文，也不构成法律意见。详情请参见完整法律声明。",

      lang_selector_label: "语言",
    },
  };

  // -------- Controlled-vocabulary label maps (internal value -> localized) --------
  // English keeps the internal value (already English) or a caller-supplied
  // display name; only zh-Hans needs an explicit map. Unmapped values fall back.
  var AREA_LABELS = {
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
    "zh-Hans": { "High": "高影响", "Medium": "中等影响", "Low": "低影响" },
  };
  var SOURCE_LABELS = {
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
    applyStatic: applyStatic,
  };
})();
