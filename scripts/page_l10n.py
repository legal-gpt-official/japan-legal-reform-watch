"""Japanese and Simplified-Chinese wording for the fixed text of static item pages.

The item pages are English (canonical). A page opened from the Japanese or
Chinese dashboard (``?lang=ja`` / ``?lang=zh-Hans``) swaps its fixed text for
the wording below, and a translated reform-explainer page (``/u/<id>/ja/``)
renders it directly. Only fixed phrases and controlled vocabularies live here;
record text (titles, summaries) comes from the published data.

The controlled-vocabulary maps mirror ``docs/i18n.js`` so a stage, area or
source has the same name on the dashboard and on its detail page; a test
enforces it.
"""

from __future__ import annotations

from typing import Any

import alert_common as ac

LOCALES: tuple[str, ...] = ("ja", "zh-Hans")

# Keep equal to STAGE_LABELS in docs/i18n.js (tested).
STAGE_LABELS: dict[str, dict[str, str]] = {
    "ja": {
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
}

# Keep equal to IMPACT_LABELS in docs/i18n.js (tested).
IMPACT_LABELS: dict[str, dict[str, str]] = {
    "ja": {"High": "高", "Medium": "中", "Low": "低"},
    "zh-Hans": {"High": "高影响", "Medium": "中等影响", "Low": "低影响"},
}

# Keep equal to SOURCE_LABELS in docs/i18n.js (tested). Keyed by the published
# ``source_name``; an unmapped source keeps its English display name.
SOURCE_LABELS: dict[str, dict[str, str]] = {
    "ja": {
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
}

OTHER_AREA = {"ja": "その他", "zh-Hans": "其他"}

# Fixed page text. ``{name}`` placeholders are filled by ``text``.
STRINGS: dict[str, dict[str, str]] = {
    # Page frame (header, reliability note, footer). Wording follows docs/i18n.js.
    "tagline": {
        "en": "Free Japan Legal & Regulatory Update Monitor",
        "ja": "日本の法令・規制動向を確認できる無料モニター",
        "zh-Hans": "免费的日本法律与监管动态监测",
    },
    "open_dashboard": {"en": "Open the dashboard", "ja": "ダッシュボードを開く", "zh-Hans": "打开仪表板"},
    "trust_title": {"en": "Reliability note", "ja": "情報の信頼性について", "zh-Hans": "可靠性说明"},
    "trust_body": {
        "en": (
            "Titles and AI summaries are unofficial English monitoring aids, not legal advice or official "
            "translations. Each link opens the original Japanese official source, which remains authoritative."
        ),
        "ja": (
            "タイトルとAI要約は非公式のモニタリング補助であり、法的助言や公式の翻訳ではありません。"
            "各リンクは日本語の公式情報源を開きます。公式情報源が正式なものです。"
        ),
        "zh-Hans": (
            "标题和AI摘要为非官方的监测辅助，并非法律意见或官方译文。"
            "各链接指向日文官方来源，应以日文官方来源为准。"
        ),
    },
    "footer_brand_p": {
        "en": "Created by Legal GPT. Explore more Japan legal updates and legal-tech tools.",
        "ja": "Legal GPTが提供しています。日本の法令動向やリーガルテック関連情報もご覧ください。",
        "zh-Hans": "由 Legal GPT 创建。探索更多日本法律动态与法律科技工具。",
    },
    "footer_learn_more": {
        "en": "Learn more about Japan Legal Reform Watch",
        "ja": "Japan Legal Reform Watchについて",
        "zh-Hans": "了解更多关于 Japan Legal Reform Watch",
    },
    "footer_legal_en": {
        "en": "Legal Notice / Disclaimer (EN)",
        "ja": "法律上の注意事項・免責事項（英語）",
        "zh-Hans": "法律声明 / 免责声明（英文）",
    },
    "footer_bottom": {
        "en": (
            "This site provides English summaries for informational purposes only. It is not an official "
            "translation and not legal advice. See the full Legal Notice for details."
        ),
        "ja": "本サイトの要約は情報提供のみを目的とし、公式文書でも法的助言でもありません。詳細は法律上の注意事項をご確認ください。",
        "zh-Hans": "本网站提供的摘要仅供参考，并非官方译文，也不构成法律意见。详情请参见完整法律声明。",
    },
    # Record details line and actions.
    "fact_published": {"en": "Published", "ja": "公表日", "zh-Hans": "发布日期"},
    "fact_not_stated": {"en": "not stated by the source", "ja": "記載なし", "zh-Hans": "来源未注明"},
    "fact_source": {"en": "Source", "ja": "情報源", "zh-Hans": "来源"},
    "fact_stage": {"en": "Stage", "ja": "段階", "zh-Hans": "阶段"},
    "fact_area": {"en": "Area", "ja": "分野", "zh-Hans": "领域"},
    "fact_impact": {"en": "Impact (preliminary)", "ja": "影響度（暫定）", "zh-Hans": "影响程度（初步）"},
    "fact_deadline": {"en": "Comment deadline", "ja": "意見募集の締切", "zh-Hans": "征求意见截止"},
    "fact_first_seen": {
        "en": "First detected by this dashboard",
        "ja": "本ダッシュボードでの初回検出日",
        "zh-Hans": "本仪表板首次收录",
    },
    "action_source": {
        "en": "View the original Japanese source",
        "ja": "日本語の公式情報源を見る",
        "zh-Hans": "查看日文官方来源",
    },
    "action_area": {"en": "More {area} updates", "ja": "「{area}」のその他の更新", "zh-Hans": "更多“{area}”动态"},
    "action_dashboard": {
        "en": "Search all updates in the dashboard",
        "ja": "ダッシュボードですべての更新を検索",
        "zh-Hans": "在仪表板中搜索全部动态",
    },
    "summary_missing": {
        "en": "",
        "ja": "この更新の日本語要約はまだありません。以下は英語の要約です。",
        "zh-Hans": "该更新暂无中文摘要，以下显示英文摘要。",
    },
    # Timeline of related official records.
    "timeline_title": {
        "en": "Timeline of related official records",
        "ja": "関連する公式記録の経過",
        "zh-Hans": "相关官方记录时间线",
    },
    "timeline_notice": {
        "en": (
            "Related official records are linked only when names and instrument numbers match exactly, so this "
            "timeline may be incomplete. Dates are as stated by the official sources. It is a monitoring aid, not "
            "legal advice; the original Japanese sources are authoritative."
        ),
        "ja": (
            "関連する公式記録は、名称と法令番号が完全に一致する場合にのみ結び付けているため、この経過は網羅的でない"
            "可能性があります。日付は公式情報源の記載によります。モニタリング補助であり法的助言ではありません。"
            "日本語の公式情報源が正式なものです。"
        ),
        "zh-Hans": (
            "仅在名称和法令编号完全一致时才关联相关官方记录，因此该时间线可能并不完整。日期以官方来源所载为准。"
            "本内容仅为监测辅助，不构成法律意见；应以日文官方来源为准。"
        ),
    },
    "timeline_no_date": {"en": "Date not stated by the source", "ja": "日付の記載なし", "zh-Hans": "来源未注明日期"},
    "timeline_this_update": {"en": "(this update)", "ja": "（このページの記録）", "zh-Hans": "（本条记录）"},
    "timeline_dashboard_page": {"en": "Dashboard page", "ja": "詳細ページ", "zh-Hans": "详情页面"},
    "timeline_source": {
        "en": "Original Japanese source",
        "ja": "日本語の公式情報源",
        "zh-Hans": "日文官方来源",
    },
    "timeline_next": {"en": "What happens next", "ja": "今後の一般的な流れ", "zh-Hans": "后续一般流程"},
}


def text(key: str, locale: str = "en", **params: Any) -> str:
    """The fixed phrase ``key`` in ``locale``, falling back to English."""
    entry = STRINGS[key]
    value = entry.get(locale) or entry["en"]
    return value.format(**params) if params else value


def stage_label(value: str, locale: str) -> str:
    return STAGE_LABELS.get(locale, {}).get(value, value)


def impact_label(value: str, locale: str) -> str:
    return IMPACT_LABELS.get(locale, {}).get(value, value)


def area_label(value: str, locale: str) -> str:
    if locale == "en":
        return value
    if value == "Other":
        return OTHER_AREA.get(locale, value)
    return ac.AREA_LABELS.get(locale, {}).get(value, value)


def source_label(source_name: str, english_display: str, locale: str) -> str:
    return SOURCE_LABELS.get(locale, {}).get(source_name, english_display)


def deadline_display(english: str, locale: str) -> str:
    """``comment_deadline_display`` output in ``locale``; the date and time stay as given."""
    if locale == "ja":
        return english.replace(" (end of day, JST)", "（日本時間・当日中）")
    if locale == "zh-Hans":
        return english.replace(" (end of day, JST)", "（日本时间当日截止）")
    return english
