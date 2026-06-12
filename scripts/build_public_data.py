#!/usr/bin/env python3
"""
build_public_data.py — Japan Legal Reform Watch by LegalOS
Stage 2 (provisional): map raw fetched items -> the published dashboard schema,
prioritising items by a rule-based RELEVANCE SCORE.

What this script does
---------------------
- Reads data/raw_items.json (output of fetch_updates.py).
- Drops administrative noise (recruitment, procurement, events, web magazines...)
  unless a strong legal/regulatory keyword is also present.
- Scores every remaining item with a keyword-based `relevance_score` so that law
  reform / regulation / public comments / guidelines rank above minutes,
  statistics, and bare page updates.
- Classifies area / stage / impact_level with simple, conservative RULES.
- Emits controlled rule-based English titles and MODEST English PLACEHOLDER copy
  (no official translation, no interpretation).
- Preserves existing Claude summary fields for matching id/source_url records.
- Orders by an internal ordering score, then impact weight (Medium > Low), then recency;
  caps the output, backs up the previous file, and writes
  docs/data/legal_updates.json.

What this script deliberately does NOT do
------------------------------------------
- No Claude / LLM calls and NO AI translation or summarization.
- No legal judgement. The relevance score is a TECHNICAL HEURISTIC for narrowing
  which items to surface — not an assessment of legal importance. title_en is a
  conservative rule-based label, not an official translation; summary_en /
  business_impact_en / recommended_action_en are fixed templates unless Stage 3
  summaries are preserved.
- Does not add sources, change the UI, or paginate.

Security posture
----------------
Input is treated as UNTRUSTED (it originates from third-party feeds). This script
only copies/derives string fields into JSON; it never renders or executes them,
and `source_url` is preserved verbatim. The browser dashboard escapes every field
on render (see docs/app.js: escapeHtml / safeUrl).

Python 3.11+, standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths / constants
# --------------------------------------------------------------------------- #

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
RAW_PATH = REPO_ROOT / "data" / "raw_items.json"
OUTPUT_PATH = REPO_ROOT / "docs" / "data" / "legal_updates.json"
BACKUP_PATH = REPO_ROOT / "docs" / "data" / "legal_updates.backup.json"

MAX_OUTPUT_ITEMS = 1000  # public dataset cap; the UI renders 50 at a time (Load more)
JST = timezone(timedelta(hours=9))  # display Japanese-source dates on the JST calendar

# The 13 fields the existing dashboard UI expects. (relevance_score is an extra,
# optional field appended after these; the UI ignores unknown fields.)
REQUIRED_FIELDS = (
    "id", "title_en", "title_ja", "area", "stage", "impact_level",
    "summary_en", "business_impact_en", "recommended_action_en",
    "source_name", "source_url", "published_at", "last_checked",
)

# Stage 3 fields that may be carried forward when Stage 2 rebuilds the same item.
# Core metadata (title/source/stage/area/score/date) always comes from the fresh build.
AI_PRESERVE_FIELDS = (
    "summary_en", "business_impact_en", "recommended_action_en",
    "summary_source", "confidence", "ai_notes", "summarized_at", "summary_model",
)

# Modest, NON-interpretive placeholder copy (no AI, no legal conclusion).
TITLE_EN_PREFIX = "Japanese Regulatory Update: "
TITLE_MAX_CHARS = 120
SUMMARY_EN = (
    "This is a rule-based preview and has not yet been reviewed or summarized by AI. "
    "A Japanese government source has published an item related to the original "
    "Japanese title below; it should be reviewed against the official Japanese source."
)
BUSINESS_IMPACT_EN = (
    "The business impact has not yet been assessed. Companies operating in Japan "
    "should review the official source if the item is relevant to their sector or "
    "operations."
)
RECOMMENDED_ACTION_EN = (
    "Review the original Japanese source and determine whether the item may affect "
    "your legal, regulatory, or compliance obligations in Japan."
)

# --------------------------------------------------------------------------- #
# Relevance scoring (keyword heuristic — NOT a legal judgement)
# --------------------------------------------------------------------------- #

# Strong legal/regulatory signals. Their presence also RESCUES an item from the
# hard-exclusion list below (req: do not blanket-exclude when these appear).
BOOST_STRONG = (
    "改正", "施行", "公布", "法律", "政令", "省令", "告示", "通達",
    "意見募集", "パブリックコメント", "パブリック・コメント",
)
BOOST_MODERATE = (
    "指針", "ガイドライン", "Q&A", "Ｑ＆Ａ", "FAQ", "案", "概要", "義務",
    "規制", "届出", "許可", "認可", "監督", "考え方",
)
BOOST_TOPICAL = (
    "個人情報", "ＡＩ", "AI", "金融", "労働", "フリーランス", "下請", "表示",
    "広告", "景品", "サイバー", "エネルギー", "環境", "外国為替", "外為",
    "経済安全保障",
)
BOOST_WEAK = ("報告",)

WEIGHT_STRONG, WEIGHT_MODERATE, WEIGHT_TOPICAL, WEIGHT_WEAK = 5, 3, 2, 1

# Additional clean UTF-8 keywords for newer source expansion. These supplement
# the original keyword sets without changing the earlier scoring design.
ADDITIONAL_BOOST_STRONG = (
    "法改正", "命令", "勧告", "行政処分", "注意喚起", "ガイドライン", "指針",
    "規制", "省令", "告示", "通達", "パブリックコメント", "意見募集",
    "個人情報保護法", "漏えい", "漏洩", "マイナンバー", "特定個人情報",
    "国際移転", "越境移転", "データ利活用", "独占禁止法", "下請法", "取適法",
    "フリーランス法", "スマホソフトウェア競争促進法", "優越的地位",
    "カルテル", "入札談合", "企業結合", "確約手続", "排除措置命令",
    "課徴金", "報告書", "実態調査",
)
ADDITIONAL_BOOST_TOPICAL = (
    "経済安全保障", "安全保障貿易", "外為", "輸出管理", "重要物資",
    "エネルギー", "電力", "ガス", "再エネ", "GX", "脱炭素", "カーボン",
    "サイバー", "セキュリティ", "AI", "デジタル", "データ",
    "中小企業", "下請", "取引適正化",
    "景品表示法", "表示", "広告", "ステルスマーケティング",
    "消費者契約法", "消費者", "取引", "勧誘", "公益通報", "食品表示",
    "個人情報", "個人データ", "保有個人データ", "仮名加工情報", "匿名加工情報",
    "Q&A", "Ｑ＆Ａ", "Q＆A", "フリーランス", "公正取引委員会",
)

# Low-value signals (deliberative / statistical / pure page updates). These are
# NOT auto-excluded — they get a strong penalty so they fall below the cut unless
# rescued by a strong/moderate boost above.
DEBOOST = {
    "議事録": -6, "会議資料": -6, "検討会": -3, "審議会": -3,
    "統計": -5, "概数": -5, "月報": -4, "年報": -4,
    "更新されました": -4, "更新しました": -4, "ページを更新": -4,
    "委員会開催情報": -8, "議事概要": -6, "懇話会": -5, "委員会を開催": -6,
    "主な意見": -3, "ポスターコンクール": -7, "シンボルマーク": -7,
    "採用": -8, "調達": -8, "キッズ": -8,
    # Ministry-source noise (MOJ/MOE/MOF/MIC expansion): market data, speeches,
    # maintenance notices, exams, subsidy adoptions/calls, PR-style items.
    "国債金利情報": -8, "入札情報": -8, "スピーチ": -5, "講演": -4, "挨拶": -4,
    "メンテナンス": -6, "試験": -4, "出願状況": -5, "白書": -3, "見学": -7,
    "採択": -6, "公募": -4, "表彰式": -7, "コンクール": -7, "フォトコンテスト": -8,
}

# Clear administrative noise. Excluded outright UNLESS a BOOST_STRONG keyword is
# present (req 4). Public-comment items are never hard-excluded.
HARD_EXCLUDE = (
    "採用", "求人", "入札", "落札", "調達", "競争入札", "人事異動", "幹部名簿",
    "表彰", "セミナー", "シンポジウム", "イベント", "記者会見", "メールマガジン",
    "広報誌", "ＷＥＢマガジン", "WEBマガジン", "Webマガジン", "ウェブマガジン",
)
ADDITIONAL_HARD_EXCLUDE = (
    "会談", "表敬", "出張", "意見交換を行いました", "開催します", "開催しました",
    "採用", "調達", "キッズ", "ポスターコンクール", "シンボルマーク",
)

SOURCE_BONUS = {"public_comment_rss": 4}  # req 5: prioritise e-Gov public comments

# Recency: a LIGHT penalty so stale items don't linger at the top.
RECENCY_PENALTY_PER_DAY = 0.15
RECENCY_PENALTY_CAP = 6.0
UNKNOWN_DATE_PENALTY = 8.0  # req 8: unknown/invalid dates rank low
PUBLIC_COMMENT_OPEN_ORDERING_BONUS = 4.0
DRAFT_GUIDELINE_ORDERING_BONUS = 2.0
PUBLIC_COMMENT_RESULTS_ORDERING_BONUS = 1.0
PUBLIC_COMMENT_CLOSED_ORDERING_PENALTY = 14.0
PUBLIC_COMMENT_CLOSED_IMPORTANT_SIGNAL_RELIEF = 2.0

# Closed public comments stay visible, but are usually less urgent than open
# consultations. These strong signals soften, but do not remove, the demotion.
IMPORTANT_CLOSED_KEYWORDS = (
    "法律", "改正", "施行", "政令", "省令", "告示", "個人情報", "AI", "金融",
    "労働", "経済安全保障", "エネルギー",
)

# Minimum score to be a candidate (administrative-only items net out below this).
RELEVANCE_FLOOR = 1.0

IMPACT_WEIGHT = {"High": 3, "Medium": 2, "Low": 1}


def is_public_comment(source_type: str) -> bool:
    return source_type == "public_comment_rss"


def is_hard_excluded(title_ja: str) -> bool:
    if any(kw in title_ja for kw in BOOST_STRONG) or any(kw in title_ja for kw in ADDITIONAL_BOOST_STRONG):
        return False  # rescued by a strong legal/regulatory signal
    return any(kw in title_ja for kw in HARD_EXCLUDE) or any(kw in title_ja for kw in ADDITIONAL_HARD_EXCLUDE)


def keyword_score(title_ja: str) -> int:
    score = 0
    score += sum(WEIGHT_STRONG for kw in BOOST_STRONG if kw in title_ja)
    score += sum(WEIGHT_STRONG for kw in ADDITIONAL_BOOST_STRONG if kw in title_ja)
    score += sum(WEIGHT_MODERATE for kw in BOOST_MODERATE if kw in title_ja)
    score += sum(WEIGHT_TOPICAL for kw in BOOST_TOPICAL if kw in title_ja)
    score += sum(WEIGHT_TOPICAL for kw in ADDITIONAL_BOOST_TOPICAL if kw in title_ja)
    score += sum(WEIGHT_WEAK for kw in BOOST_WEAK if kw in title_ja)
    score += sum(weight for kw, weight in DEBOOST.items() if kw in title_ja)
    return score


def recency_penalty(sort_ts: float | None, build_ts: float) -> float:
    if sort_ts is None:
        return UNKNOWN_DATE_PENALTY
    age_days = max(0.0, (build_ts - sort_ts) / 86400.0)
    return min(age_days * RECENCY_PENALTY_PER_DAY, RECENCY_PENALTY_CAP)


def relevance_score(title_ja: str, source_type: str) -> float:
    """Content relevance from keywords + source bonus (NO recency).

    Recency is applied only to ORDERING (see recency_penalty), so a highly
    relevant but older item is ranked lower yet never filtered out by the floor.
    """
    return float(keyword_score(title_ja) + SOURCE_BONUS.get(source_type, 0))


# --------------------------------------------------------------------------- #
# Classification rules (provisional, keyword-based)
# --------------------------------------------------------------------------- #

# First matching area wins; narrower topics are listed before broader ones.
AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Data / Privacy / AI", (
        "個人情報", "個人データ", "プライバシー", "マイナンバー", "ＡＩ", "AI",
        "人工知能", "サイバー", "情報セキュリティ", "デジタル", "電気通信", "クッキー", "Cookie",
    )),
    ("Economic Security / FDI", (
        "経済安全保障", "対内直接投資", "外為", "外国為替", "輸出管理",
        "安全保障貿易", "コア業種", "セキュリティ・クリアランス",
    )),
    ("Antitrust / Fair Trade", (
        "独占禁止", "独禁", "公正取引", "カルテル", "下請", "優越的地位",
        "談合", "企業結合", "不当廉売",
    )),
    ("Finance / AML", (
        "金融", "銀行", "保険", "証券", "資金決済", "資金洗浄", "マネー", "マネロン",
        "暗号資産", "ステーブルコイン", "決済", "信託", "預金", "投資", "金融商品", "犯罪収益", "FATF",
    )),
    ("Tax / Stamp Duty", (
        "印紙", "課税", "消費税", "法人税", "源泉", "インボイス", "関税", "租税", "税制",
    )),
    ("Labor / Employment", (
        "労働", "雇用", "賃金", "ハラスメント", "労災", "フリーランス", "派遣", "解雇",
        "育児", "介護休業", "安全衛生", "年金", "社会保険", "働き方", "技能実習", "外国人材",
    )),
    ("Energy / Environment", (
        "環境", "エネルギー", "電力", "脱炭素", "カーボン", "温室効果ガス", "排出", "気候",
        "再生可能", "原子力", "廃棄物", "リサイクル", "鳥獣", "水質", "大気", "化学物質",
    )),
    ("Consumer / Advertising", (
        "消費者", "景品表示", "景品", "広告", "特定商取引", "食品表示", "製品安全", "リコール", "消費生活",
    )),
    ("Corporate / Governance", (
        "会社法", "コーポレートガバナンス", "ガバナンス", "開示", "有価証券報告書", "株主",
        "取締役", "内部統制", "監査", "サステナビリティ", "上場", "商業登記", "企業統治",
    )),
]

# Source-name fallback when no keyword matched (only the unambiguous ones).
# MIC is deliberately absent: its scope is broad, so unmatched items stay "Other".
AREA_SOURCE_FALLBACK = (
    ("Finance / AML", ("金融庁", "FSA")),
    ("Data / Privacy / AI", ("デジタル庁", "Digital Agency")),
    ("Consumer / Advertising", ("消費者庁", "CAA")),
    ("Data / Privacy / AI", ("個人情報保護委員会", "PPC")),
    ("Antitrust / Fair Trade", ("公正取引委員会", "JFTC")),
    ("Labor / Employment", ("厚生労働省", "MHLW", "Ministry of Health, Labour and Welfare")),
    ("Corporate / Governance", ("法務省", "MOJ")),
    ("Energy / Environment", ("環境省", "MOE")),
    ("Finance / AML", ("財務省", "MOF")),
)

# Source-expansion area rules. These run before the broader legacy table so
# METI/CAA items with clear keywords land in more useful business categories.
METI_AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Economic Security / FDI", (
        "経済安全保障", "安全保障貿易", "外為", "輸出管理", "重要物資",
    )),
    ("Energy / Environment", (
        "エネルギー", "電力", "ガス", "再エネ", "GX", "脱炭素", "カーボン",
    )),
    ("Data / Privacy / AI", (
        "サイバー", "セキュリティ", "AI", "デジタル", "データ",
    )),
    ("Antitrust / Fair Trade", (
        "中小企業", "下請", "取引適正化",
    )),
]
CAA_AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Corporate / Governance", (
        "公益通報",
    )),
    ("Consumer / Advertising", (
        "景品表示法", "表示", "広告", "ステルスマーケティング",
        "消費者契約法", "消費者", "取引", "勧誘", "食品表示",
    )),
]
PPC_AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Data / Privacy / AI", (
        "個人情報", "個人情報保護法", "個人データ", "保有個人データ",
        "仮名加工情報", "匿名加工情報", "漏えい", "漏洩", "マイナンバー",
        "特定個人情報", "国際移転", "越境移転", "AI", "データ利活用",
        "ガイドライン", "Q&A", "Ｑ＆Ａ", "Q＆A",
    )),
]
JFTC_AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Antitrust / Fair Trade", (
        "独占禁止法", "下請法", "取適法", "フリーランス",
        "スマホソフトウェア競争促進法", "優越的地位", "カルテル", "入札談合",
        "企業結合", "確約手続", "排除措置命令", "課徴金", "勧告",
        "不公正な取引方法", "公正な取引", "公正取引委員会",
    )),
]
# Ministry expansion (MOJ / MOE / MOF / MIC). Source-gated like METI/CAA/PPC/JFTC;
# narrower topics come before broader ones.
MOJ_AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Finance / AML", (
        "犯罪収益", "マネー・ローンダリング", "マネロン", "テロ資金", "FATF",
    )),
    ("Data / Privacy / AI", (
        "個人情報", "個人データ",
    )),
    ("Labor / Employment", (
        "入管", "在留資格", "外国人", "技能実習", "特定技能", "出入国", "育成就労",
    )),
    ("Real Estate / Land Use", (
        "不動産登記", "所有者不明土地", "相続登記",
    )),
    ("Corporate / Governance", (
        "会社法", "商業登記", "法人登記", "登記", "民法", "契約", "債権", "担保",
        "民事", "法制審議会", "司法制度",
    )),
]
MOE_AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Public Safety / Disaster Management", (
        "災害", "防災",
    )),
    ("Energy / Environment", (
        "環境", "脱炭素", "気候変動", "GX", "温室効果ガス", "カーボン", "排出",
        "廃棄物", "リサイクル", "資源循環", "化学物質", "PRTR", "フロン", "PFOS",
        "水質", "大気", "自然公園", "国立公園", "鳥獣", "生物多様性", "エネルギー", "再エネ",
    )),
]
MOF_AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Economic Security / FDI", (
        "外為", "外国為替", "対内直接投資", "輸出入", "輸出管理", "経済安全保障", "安全保障",
    )),
    ("Finance / AML", (
        "関税", "税制", "税法", "マネロン", "マネー・ローンダリング", "テロ資金",
        "犯罪収益", "FATF", "金融",
    )),
]
MIC_AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Data / Privacy / AI", (
        "通信", "電気通信", "電波", "放送", "情報通信", "ネットワーク", "サイバー",
        "マイナンバー", "デジタル", "行政手続",
    )),
    ("Corporate / Governance", (
        "地方制度", "地方自治", "地方公共団体",
    )),
]

# Additional UTF-8 area rules for business-friendly dashboard filters. These run
# before the legacy broad table so public-comment items do not fall back to Other.
UTF8_AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Energy / Environment", (
        "電源", "重要電源", "原子力", "エネルギー", "電力", "ガス", "再エネ",
        "GX", "脱炭素", "環境", "鳥獣保護", "鳥獣の保護", "wildlife protection",
        "nuclear", "power source", "一般海域", "占用公募制度",
        "グリーンボンド", "グリーンローン",
    )),
    ("Food / Agriculture", (
        "食品", "添加物", "食品表示", "農薬", "動物用医薬品", "鳥獣", "農林",
        "畜産", "veterinary", "food additive", "pesticide", "agriculture",
    )),
    ("Healthcare / Pharmaceuticals", (
        "薬局", "薬局製剤", "医薬品", "医療機器", "薬機法", "薬事", "医療",
        "pharmaceutical", "pharmacy", "medical device",
    )),
    ("Transport / Infrastructure", (
        "鉄道", "車両", "容器", "高圧ガス", "運輸", "交通", "港湾", "道路",
        "railway", "transport", "container", "infrastructure",
    )),
    ("Real Estate / Land Use", (
        "空家", "空き家", "建築", "都市計画", "不動産", "土地", "住宅",
        "国立公園", "利用調整地区", "vacant house", "real estate", "land use",
        "urban planning",
    )),
    ("Public Safety / Disaster Management", (
        "災害", "防災", "感染症", "ペットの災害対策", "安全対策",
        "disaster", "public safety", "emergency",
    )),
    ("Consumer / Advertising", (
        "景品表示法", "表示", "広告", "ステルスマーケティング", "消費者",
        "消費者契約", "機能性表示食品", "food labeling", "advertising", "consumer",
    )),
    ("Labor / Employment", (
        "労働", "雇用", "派遣", "受入事業主", "送出事業主", "事業主が講ずべき措置",
        "labor", "employment", "worker dispatch",
    )),
]
ADDITIONAL_AREA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Economic Security / FDI", (
        "経済安全保障", "安全保障貿易", "外為", "輸出管理", "重要物資",
    )),
    ("Energy / Environment", (
        "エネルギー", "電力", "ガス", "再エネ", "GX", "脱炭素", "カーボン",
    )),
    ("Data / Privacy / AI", (
        "サイバー", "セキュリティ", "AI", "デジタル", "データ",
        "個人情報", "個人情報保護法", "個人データ", "保有個人データ",
        "仮名加工情報", "匿名加工情報", "漏えい", "漏洩", "マイナンバー",
        "特定個人情報", "国際移転", "越境移転", "データ利活用",
    )),
    ("Antitrust / Fair Trade", (
        "中小企業", "下請", "取引適正化", "独占禁止法", "下請法", "取適法",
        "フリーランス", "スマホソフトウェア競争促進法", "優越的地位",
        "カルテル", "入札談合", "企業結合", "確約手続", "排除措置命令",
        "課徴金", "不公正な取引方法", "公正な取引", "公正取引委員会",
    )),
    ("Corporate / Governance", (
        "公益通報",
    )),
    ("Consumer / Advertising", (
        "景品表示法", "表示", "広告", "ステルスマーケティング",
        "消費者契約法", "消費者", "取引", "勧誘", "食品表示",
    )),
]


def classify_area(title_ja: str, source_name: str) -> str:
    if any(hint in source_name for hint in ("経済産業省", "METI")):
        for area, keywords in METI_AREA_RULES:
            if any(kw in title_ja for kw in keywords):
                return area
    if any(hint in source_name for hint in ("消費者庁", "CAA")):
        for area, keywords in CAA_AREA_RULES:
            if any(kw in title_ja for kw in keywords):
                return area
    if any(hint in source_name for hint in ("個人情報保護委員会", "PPC")):
        for area, keywords in PPC_AREA_RULES:
            if any(kw in title_ja for kw in keywords):
                return area
    if any(hint in source_name for hint in ("公正取引委員会", "JFTC")):
        for area, keywords in JFTC_AREA_RULES:
            if any(kw in title_ja for kw in keywords):
                return area
    if any(hint in source_name for hint in ("法務省", "MOJ")):
        for area, keywords in MOJ_AREA_RULES:
            if any(kw in title_ja for kw in keywords):
                return area
    if any(hint in source_name for hint in ("環境省", "MOE")):
        for area, keywords in MOE_AREA_RULES:
            if any(kw in title_ja for kw in keywords):
                return area
    if any(hint in source_name for hint in ("財務省", "MOF")):
        for area, keywords in MOF_AREA_RULES:
            if any(kw in title_ja for kw in keywords):
                return area
    if any(hint in source_name for hint in ("総務省", "MIC")):
        for area, keywords in MIC_AREA_RULES:
            if any(kw in title_ja for kw in keywords):
                return area
    for area, keywords in UTF8_AREA_RULES:
        if any(kw in title_ja for kw in keywords):
            return area
    for area, keywords in ADDITIONAL_AREA_RULES:
        if any(kw in title_ja for kw in keywords):
            return area
    for area, keywords in AREA_RULES:
        if any(kw in title_ja for kw in keywords):
            return area
    for area, hints in AREA_SOURCE_FALLBACK:
        if any(h in source_name for h in hints):
            return area
    return "Other"


_PC_KEYWORDS = (
    "意見募集", "パブリックコメント", "パブリック・コメント", "パブコメ",
    "意見の募集", "御意見の募集", "ご意見の募集",
)
# Explicit markers always mean public-comment results. Generic result wording
# ("結果について" etc.) appears in plenty of non-PC ministry announcements
# (selection results, survey results), so it only counts inside a PC context.
_PC_RESULT_MARKERS_EXPLICIT = (
    "意見募集結果", "意見募集の結果", "意見の募集の結果",
    "パブリックコメントの結果", "結果の公示",
)
_PC_RESULT_MARKERS_GENERIC = ("結果について", "募集の結果", "結果")
_PC_RESULT_MARKERS_EN = ("results published",)
_PC_CLOSED_MARKERS = ("受付終了", "終了しました", "意見募集を終了", "募集を終了")
_PC_CLOSED_MARKERS_EN = ("closed",)


def contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def is_public_comment_title(title_ja: str) -> bool:
    title_lower = title_ja.lower()
    return contains_any(title_ja, _PC_KEYWORDS) or "public comment" in title_lower


def classify_stage(title_ja: str, source_type: str) -> str:
    title_lower = title_ja.lower()
    pc_context = is_public_comment(source_type) or is_public_comment_title(title_ja)
    if contains_any(title_ja, _PC_RESULT_MARKERS_EXPLICIT) or contains_any(title_lower, _PC_RESULT_MARKERS_EN):
        return "Public Comment Results Published"
    if pc_context and contains_any(title_ja, _PC_RESULT_MARKERS_GENERIC):
        return "Public Comment Results Published"
    if contains_any(title_ja, _PC_CLOSED_MARKERS) or contains_any(title_lower, _PC_CLOSED_MARKERS_EN):
        return "Public Comment Closed"
    if pc_context:
        return "Public Comment Open"
    if any(m in title_ja for m in ("施行されました", "施行しました")):
        return "In Force"
    if any(m in title_ja for m in ("施行期日", "施行日", "施行予定")):
        return "Scheduled to Take Effect"
    if "公布" in title_ja:
        return "Promulgated"
    if "成立" in title_ja and "法" in title_ja:
        return "Enacted"
    if "法案" in title_ja or "法律案" in title_ja or ("提出" in title_ja and ("法律" in title_ja or "法案" in title_ja or "国会" in title_ja)):
        return "Bill Submitted"
    if "案" in title_ja and any(kw in title_ja for kw in ("指針", "ガイドライン", "Q&A", "Ｑ＆Ａ", "Q＆A", "FAQ", "考え方")):
        return "Draft Guideline"
    return "Government Announcement"


def has_important_closed_signal(title_ja: str) -> bool:
    return any(keyword in title_ja for keyword in IMPORTANT_CLOSED_KEYWORDS)


def stage_ordering_adjustment(stage: str, title_ja: str) -> float:
    """Display-order adjustment only; relevance_score remains content-based."""
    if stage == "Public Comment Open":
        return PUBLIC_COMMENT_OPEN_ORDERING_BONUS
    if stage == "Draft Guideline":
        return DRAFT_GUIDELINE_ORDERING_BONUS
    if stage == "Public Comment Results Published":
        return PUBLIC_COMMENT_RESULTS_ORDERING_BONUS
    if stage == "Public Comment Closed":
        penalty = PUBLIC_COMMENT_CLOSED_ORDERING_PENALTY
        if has_important_closed_signal(title_ja):
            penalty -= PUBLIC_COMMENT_CLOSED_IMPORTANT_SIGNAL_RELIEF
        return -penalty
    return 0.0


# Keywords that lift a non-public-comment item from Low to Medium.
_IMPACT_MEDIUM_KEYWORDS = (
    "施行", "改正", "義務", "ガイドライン", "指針", "個人情報", "個人データ", "金融",
    "労働", "フリーランス", "下請", "AI", "ＡＩ", "人工知能", "規制", "プライバシー",
    "マネー", "資金洗浄", "経済安全保障", "外為", "外国為替", "公布", "告示", "通達",
)

# Narrow / industry-specific signals: keep a public comment at Low impact unless a
# broad-economy keyword is also present (req 5 — judge conservatively, by keyword).
_NARROW_SECTOR = (
    "動物用", "獣医", "薬局製剤", "飼料", "農薬", "漁業", "養殖", "鳥獣", "船舶",
    "港湾", "航空機", "鉄道事業", "食品添加物", "水道", "と畜", "家畜", "植物防疫",
)
_BROAD_SCOPE = (
    "個人情報", "金融", "労働", "AI", "ＡＩ", "消費者", "環境", "税", "サイバー",
    "会社法", "下請", "フリーランス", "景品表示", "広告", "経済安全保障",
)


def classify_impact(title_ja: str, source_type: str) -> str:
    if is_public_comment(source_type):
        if any(k in title_ja for k in _NARROW_SECTOR) and not any(k in title_ja for k in _BROAD_SCOPE):
            return "Low"
        return "Medium"

    level = "Medium" if any(k in title_ja for k in _IMPACT_MEDIUM_KEYWORDS) else "Low"

    # === "High" is intentionally NOT emitted by these rule-based heuristics. ===
    # It is reserved for a later, evidence-based (AI- or human-reviewed) stage so
    # the dashboard never over-states impact from keywords alone. When that stage
    # exists, a High signal might look like the following — DO NOT enable here:
    #
    #   if "義務化" in title_ja and any(k in title_ja for k in ("施行", "公布")):
    #       return "High"
    #
    return level


# --------------------------------------------------------------------------- #
# Rule-based English title labels (triage aid; NOT official translation)
# --------------------------------------------------------------------------- #

TITLE_SUBJECT_MARKERS = (
    "に対する意見募集及び公聴会",
    "に対する意見募集",
    "に関する御意見の募集",
    "に関する意見・情報の募集",
    "についての意見・情報の募集",
    "に係る意見の募集",
    "に関する意見の募集",
    "に関する意見募集",
    "への意見募集",
    "の公表について",
    "について公表しました",
)

TITLE_TRAILING_PHRASES = (
    "について",
    "を掲載しました。",
    "を掲載しました",
    "を公表しました。",
    "を公表しました",
    "公表しました。",
    "公表しました",
)

TITLE_TOPIC_RULES: list[tuple[tuple[str, ...], str]] = [
    (("薬局製剤指針",), "Pharmacy Preparation Guidelines"),
    (("鉄道車両", "容器", "検査", "再検査"), "Inspection Standards for Containers Fixed to Railway Vehicles"),
    (("重要電源開発地点",), "Rules on Designation of Important Power Source Development Sites"),
    (("中小・地域金融機関", "監督指針"), "Supervisory Guidelines for Small and Regional Financial Institutions"),
    (("流通・取引慣行", "独占禁止法"), "Antimonopoly Act Guidelines on Distribution and Trade Practices"),
    (("優越的地位", "知的財産権"), "Guidelines on Abuse of Superior Bargaining Position in IP, Know-How and Data Transactions"),
    (("優越的地位",), "Guidelines on Abuse of Superior Bargaining Position"),
    (("不公正な取引方法", "特定荷主"), "Unfair Trade Practices Rules for Specified Shippers"),
    (("不公正な取引方法",), "Unfair Trade Practices Rules"),
    (("国民年金法施行規則",), "Ordinance for Enforcement of the National Pension Act"),
    (("原子力政策",), "Nuclear Energy Policy Direction and Action Guidelines"),
    (("空家等", "基本的な指針"), "Basic Guidelines on Measures for Vacant Houses"),
    (("鳥獣", "基本的な指針"), "Basic Wildlife Protection and Management Guidelines"),
    (("食品、添加物等の規格基準",), "Food and Food Additive Standards"),
    (("受入事業主", "送出事業主"), "Guidelines for Receiving and Sending-Out Business Operators"),
    (("人とペットの災害対策ガイドライン",), "Disaster Preparedness Guidelines for People and Pets"),
    (("動物用医薬品等取締規則",), "Veterinary Pharmaceuticals Control Rules"),
    (("知床国立公園", "知床五湖"), "Visitor Limits for Shiretoko Five Lakes Use Adjustment Area"),
    (("個人情報の保護に関する法律", "閣議決定"), "Cabinet Decision on Bill to Amend the Act on the Protection of Personal Information"),
    (("特定個人情報", "漏えい"), "Response to Leakage Incidents Involving Specific Personal Information"),
    (("機能性表示食品制度届出データベース",), "Functional Claims Food Notification Database"),
    (("景品表示法",), "Act against Unjustifiable Premiums and Misleading Representations"),
    (("ステルスマーケティング",), "Stealth Marketing Advertising Rules"),
    (("食品表示",), "Food Labeling Rules"),
    (("生成AI", "実態調査報告書"), "Market Survey Report on Generative AI"),
    (("排除措置命令", "課徴金"), "Cease-and-Desist and Surcharge Payment Orders"),
    (("取適法", "運用状況"), "Enforcement Status of the Transaction Optimization Act"),
    (("労働者派遣事業", "許可", "取り消し"), "Revocation of Worker Dispatch Business Permit"),
    (("IOSCO", "集団投資スキーム"), "IOSCO Final Report on Valuation of Collective Investment Schemes"),
    (("マレーシア医療機器庁",), "MOU with Malaysia Medical Device Authority to Strengthen Regulatory Cooperation"),
    (("第221回国会", "提出法律案"), "Bills Submitted to the 221st Diet Session"),
    (("RegTech", "ミート"), "RegTech Meet Materials on Analog Regulation Review"),
    (("一般海域", "占用公募制度"), "Revision to Operational Guidelines for the General Sea Area Occupancy Tender System"),
    (("愛媛県", "ドローン"), "Local Government Analog Regulation Review: Drone Use Case in Ehime Prefecture"),
    (("特定保健用食品", "表示許可"), "Permission for Labeling of Foods for Specified Health Uses"),
    (("スマホソフトウェア競争促進法", "遵守報告書"), "Compliance Reports under the Smartphone Software Competition Promotion Act"),
    (("COCoLiS",), "Consumer Organization Litigation System COCoLiS Updated"),
    (("白書", "年次報告書"), "White Papers and Annual Reports"),
    (("知的財産取引適正化", "ワーキンググループ報告書"), "Working Group Report on Fairness in Intellectual Property Transactions"),
    (("電通グループ", "課徴金額"), "Decision Changing Surcharge Amount for Dentsu Group"),
    (("特定石綿被害建設業務労働者", "認定審査会"), "Special Review Committee Meeting on Asbestos-Related Construction Worker Certification"),
    # Ministry expansion (MOJ / MOE / MOF / MIC) recurring topics.
    (("環境影響評価",), "Environmental Impact Assessment Rules"),
    (("グリーンボンド",), "Green Bond and Green Loan Guidelines"),
    (("道路交通法",), "Road Traffic Act Rules"),
    (("商業登記",), "Commercial Registration Rules"),
    (("不動産登記",), "Real Estate Registration Rules"),
    (("法制審議会",), "Legislative Council Deliberations"),
    (("会社法",), "Companies Act Rules"),
    (("民法",), "Civil Code Rules"),
    (("在留資格",), "Residence Status Rules"),
    (("育成就労",), "Employment for Skill Development Program Rules"),
    (("入管",), "Immigration Control Rules"),
    (("関税",), "Customs and Tariff Rules"),
    (("外国為替",), "Foreign Exchange and Foreign Trade Act Rules"),
    (("税制",), "Tax System Measures"),
    (("電気通信事業",), "Telecommunications Business Rules"),
    (("電波法",), "Radio Act Rules"),
    (("電波",), "Radio Spectrum Policy"),
    (("放送",), "Broadcasting Policy"),
    (("廃棄物",), "Waste Management Rules"),
    (("資源循環",), "Resource Circulation Policy"),
    (("化学物質",), "Chemical Substances Regulation"),
    (("PFOS",), "PFOS-Related Measures"),
    (("気候変動",), "Climate Change Policy"),
    (("生物多様性",), "Biodiversity Policy"),
    (("国立公園",), "National Park Rules"),
    (("経済安全保障",), "Economic Security Policy"),
    (("安全保障貿易",), "Security Trade Control"),
    (("輸出管理",), "Export Control Rules"),
    (("外為",), "Foreign Exchange and Foreign Trade Act Rules"),
    (("重要物資",), "Critical Goods Policy"),
    (("エネルギー",), "Energy Policy"),
    (("電力",), "Electricity Policy"),
    (("ガス",), "Gas Policy"),
    (("GX",), "GX Policy"),
    (("脱炭素",), "Decarbonization Policy"),
    (("個人情報",), "Personal Information Protection Rules"),
    (("マイナンバー",), "My Number Rules"),
    (("独占禁止法",), "Antimonopoly Act Rules"),
    (("下請法",), "Subcontract Act Rules"),
    (("フリーランス",), "Freelance Act Rules"),
]


def strip_outer_quotes(value: str) -> str:
    text = value.strip()
    quote_pairs = (("「", "」"), ("『", "』"), ('"', '"'), ("'", "'"))
    changed = True
    while changed and len(text) >= 2:
        changed = False
        for left, right in quote_pairs:
            if text.startswith(left) and text.endswith(right):
                text = text[len(left): -len(right)].strip()
                changed = True
                break
    return text


def clean_japanese_title_subject(title_ja: str) -> str:
    text = (title_ja or "").strip()
    text = re.sub(r"^[（(]\s*受付終了\s*[）)]", "", text).strip()
    quoted = re.match(r"^「([^」]+)」", text)
    if quoted and any(marker in text for marker in ("掲載", "ページ")):
        text = quoted.group(1)
    else:
        for marker in TITLE_SUBJECT_MARKERS:
            if marker in text:
                text = text.split(marker, 1)[0]
                break
    text = strip_outer_quotes(text)
    for phrase in TITLE_TRAILING_PHRASES:
        if text.endswith(phrase):
            text = text[: -len(phrase)].strip()
    text = strip_outer_quotes(text)
    return re.sub(r"\s+", " ", text).strip(" 。")


def topic_english(subject_ja: str, title_ja: str) -> str:
    haystack = subject_ja + " " + title_ja
    for keywords, english in TITLE_TOPIC_RULES:
        if all(keyword in haystack for keyword in keywords):
            return english
    return ""


def has_draft_marker(subject_ja: str) -> bool:
    return any(
        marker in subject_ja
        for marker in ("（案）", "(案)", "改正案", "案等", "案）", "案に", "案及び", "案又は")
    )


def infer_subject_title_en(subject_ja: str, title_ja: str) -> str:
    topic = topic_english(subject_ja, title_ja)
    if topic:
        if topic.endswith("Updated"):
            return topic
        if topic.startswith((
            "Cabinet Decision",
            "Response to",
            "Market Survey",
            "Cease-and-Desist",
            "Enforcement Status",
            "Revocation",
            "IOSCO",
            "MOU",
            "Bills Submitted",
            "RegTech",
            "Revision to",
            "Local Government",
            "Permission for",
            "Compliance Reports",
            "White Papers",
            "Working Group",
            "Decision Changing",
            "Special Review",
        )):
            return topic
        if "閣議決定" in title_ja and not topic.startswith("Cabinet Decision"):
            return "Cabinet Decision on " + topic
        if any(marker in title_ja for marker in ("漏えい", "漏洩")) and not topic.startswith("Response to"):
            return topic
        if "更新" in title_ja:
            return topic + " Updated"
        if any(marker in subject_ja for marker in ("改訂案", "変更案")):
            return "Draft Revision to " + topic
        if any(marker in subject_ja for marker in ("一部改正", "一部を改正", "改正案")):
            return "Draft Amendment to " + topic
        if has_draft_marker(subject_ja) and any(marker in subject_ja for marker in ("指針", "ガイドライン", "考え方")):
            return "Draft " + topic
        if has_draft_marker(subject_ja):
            return "Draft " + topic
        return topic

    if any(marker in subject_ja for marker in ("一部改正", "一部を改正", "改正案")):
        return "Draft Amendment to " + subject_ja
    if any(marker in subject_ja for marker in ("改訂案", "変更案")):
        return "Draft Revision to " + subject_ja
    if has_draft_marker(subject_ja) and any(marker in subject_ja for marker in ("指針", "ガイドライン", "規程")):
        return "Draft Guidelines or Rules: " + subject_ja
    return subject_ja


def title_prefix(source_name: str, stage: str, title_ja: str) -> str:
    if "公正取引委員会" in source_name or "JFTC" in source_name:
        return "JFTC Public Comment" if stage.startswith("Public Comment") else "JFTC Update"
    if "個人情報保護委員会" in source_name or "PPC" in source_name:
        return "PPC Update"
    if "消費者庁" in source_name or "CAA" in source_name:
        return "CAA Update"
    if "経済産業省" in source_name or "METI" in source_name:
        if any(keyword in title_ja for keyword in ("エネルギー", "電力", "ガス", "GX", "脱炭素", "カーボン")):
            return "METI Energy Update"
        return "METI Update"
    if "Financial Services Agency" in source_name or "金融庁" in source_name or "FSA" in source_name:
        return "FSA Update"
    if "Ministry of Health" in source_name or "厚生労働省" in source_name or "MHLW" in source_name:
        return "MHLW Update"
    if "Digital Agency" in source_name or "デジタル庁" in source_name:
        return "Digital Agency Update"
    if "法務省" in source_name or "MOJ" in source_name:
        return "MOJ Update"
    if "環境省" in source_name or "MOE" in source_name:
        return "MOE Update"
    if "財務省" in source_name or "MOF" in source_name:
        return "MOF Update"
    if "総務省" in source_name or "MIC" in source_name:
        return "MIC Update"
    if stage == "Public Comment Results Published":
        return "Public Comment Results"
    if stage == "Public Comment Closed":
        return "Closed Public Comment"
    if stage == "Public Comment Open":
        return "Public Comment"
    if stage == "Draft Guideline":
        return "Draft Guideline"
    return "Japanese Regulatory Update"


def shorten_title(value: str, max_chars: int = TITLE_MAX_CHARS) -> str:
    if len(value) <= max_chars:
        return value
    cut = value[: max_chars - 3].rstrip()
    space = cut.rfind(" ")
    if space >= 72:
        cut = cut[:space].rstrip()
    return cut + "..."


def generate_title_en(title_ja: str, source_name: str, stage: str) -> str:
    subject_ja = clean_japanese_title_subject(title_ja)
    subject_en = infer_subject_title_en(subject_ja, title_ja)
    prefix = title_prefix(source_name, stage, title_ja)

    if stage == "Public Comment Closed" and "Public Comment" not in prefix:
        title = f"{prefix}: Closed public comment on {subject_en}"
    elif stage == "Public Comment Results Published" and "Public Comment" not in prefix:
        title = f"{prefix}: Public comment results on {subject_en}"
    else:
        title = f"{prefix}: {subject_en}"

    return shorten_title(title)


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def parse_published(raw_value: str) -> tuple[float | None, str]:
    """Return (sort_key timestamp, display_date 'YYYY-MM-DD' on JST).

    Unknown/invalid -> (None, "") so the item sorts last and shows no date.
    """
    if not raw_value or not isinstance(raw_value, str):
        return (None, "")
    value = raw_value.strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        if _DATE_RE.match(value):
            try:
                dt = datetime.strptime(value[:10], "%Y-%m-%d")
            except ValueError:
                return (None, "")
        else:
            return (None, "")
    if dt.tzinfo is not None:
        return (dt.timestamp(), dt.astimezone(JST).strftime("%Y-%m-%d"))
    return (dt.timestamp(), dt.strftime("%Y-%m-%d"))


def date_only(value: str) -> str:
    if isinstance(value, str) and _DATE_RE.match(value.strip()):
        return value.strip()[:10]
    return ""


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def build_public_item(raw: dict, build_date: str, score: float) -> dict:
    title_ja = raw.get("title_ja") or ""
    source_name = raw.get("source_name") or ""
    source_type = raw.get("source_type") or ""
    _, display_date = parse_published(raw.get("published_at", ""))
    stage = classify_stage(title_ja, source_type)
    title_en = generate_title_en(title_ja, source_name, stage)

    return {
        "id": raw.get("id") or "",                 # reuse the stable raw id (traceable)
        "title_en": title_en,                      # rule-based label — NOT an official translation
        "title_ja": title_ja,
        "area": classify_area(title_ja, source_name),
        "stage": stage,
        "impact_level": classify_impact(title_ja, source_type),
        "summary_en": SUMMARY_EN,
        "business_impact_en": BUSINESS_IMPACT_EN,
        "recommended_action_en": RECOMMENDED_ACTION_EN,
        "source_name": source_name,
        "source_url": raw.get("source_url") or "",  # verbatim — never modified
        "published_at": display_date,
        "last_checked": date_only(raw.get("fetched_at", "")) or build_date,
        "relevance_score": score,                   # internal heuristic (optional; UI ignores it)
    }


def load_raw(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("raw_items.json must contain a JSON array.")
    return [x for x in data if isinstance(x, dict)]


def load_existing_public_items(path: Path) -> list[dict]:
    """Best-effort load of the current published file for AI summary preservation."""
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: could not read existing {path.name} for AI preservation: {exc}", file=sys.stderr)
        return []
    if not isinstance(data, list):
        print(f"WARNING: existing {path.name} is not a JSON array; skipping AI preservation.", file=sys.stderr)
        return []
    return [x for x in data if isinstance(x, dict)]


def existing_items_by_id(items: list[dict]) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for item in items:
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            by_id[item_id] = item
    return by_id


def preserve_ai_summary_fields(item: dict, existing_by_id: dict[str, dict]) -> bool:
    """Carry forward Claude summary fields only when id and source_url still match."""
    existing = existing_by_id.get(item.get("id") or "")
    if not existing or existing.get("summary_source") != "claude":
        return False
    if (existing.get("source_url") or "") != (item.get("source_url") or ""):
        return False
    for field in AI_PRESERVE_FIELDS:
        if field in existing:
            item[field] = existing[field]
    return item.get("summary_source") == "claude"


def save_json(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build provisional, relevance-ranked public data from raw items.")
    parser.add_argument("--limit", type=int, default=MAX_OUTPUT_ITEMS, help="Max output items (default 50).")
    parser.add_argument("--floor", type=float, default=RELEVANCE_FLOOR, help="Minimum relevance_score to be a candidate.")
    parser.add_argument("--dry-run", action="store_true", help="Build and report, but do not back up or write.")
    args = parser.parse_args(argv)

    if not RAW_PATH.exists():
        print(f"ERROR: input not found: {RAW_PATH}", file=sys.stderr)
        print("Run scripts/fetch_updates.py first.", file=sys.stderr)
        return 1

    build_dt = datetime.now(timezone.utc)
    build_ts = build_dt.timestamp()
    build_date = build_dt.strftime("%Y-%m-%d")

    raw_items = load_raw(RAW_PATH)
    input_items = len(raw_items)
    existing_public_items = load_existing_public_items(OUTPUT_PATH)
    existing_public_by_id = existing_items_by_id(existing_public_items)
    existing_ai_ids = {
        item.get("id")
        for item in existing_public_items
        if item.get("summary_source") == "claude" and item.get("id")
    }

    # ranked[i] = (ordering_score, impact_weight, sort_ts_for_tiebreak, item)
    ranked: list[tuple[float, int, float, dict]] = []
    excluded_items = 0

    for raw in raw_items:
        title_ja = raw.get("title_ja") or ""
        source_type = raw.get("source_type") or ""
        pc = is_public_comment(source_type)

        # 1) Hard-exclude obvious noise (public comments are never hard-excluded).
        if not pc and is_hard_excluded(title_ja):
            excluded_items += 1
            continue

        sort_ts, _ = parse_published(raw.get("published_at", ""))
        score = relevance_score(title_ja, source_type)  # content relevance (no recency)

        # 2) Floor: drop items with no net legal/regulatory signal (public comments exempt).
        if not pc and score < args.floor:
            excluded_items += 1
            continue

        item = build_public_item(raw, build_date, score)
        weight = IMPACT_WEIGHT.get(item["impact_level"], 1)
        # Ordering applies recency and stage adjustments without changing the
        # content-based relevance_score written to the public JSON.
        ordering_score = (
            score
            + stage_ordering_adjustment(item["stage"], title_ja)
            - recency_penalty(sort_ts, build_ts)
        )
        tiebreak_ts = sort_ts if sort_ts is not None else float("-inf")
        ranked.append((ordering_score, weight, tiebreak_ts, item))

    candidate_items = len(ranked)

    # Order: internal ordering score desc, then impact weight (Medium > Low), then recency.
    ranked.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    output = [item for _, _, _, item in ranked[: args.limit]]

    preserved_ai_ids: set[str] = set()
    for item in output:
        if preserve_ai_summary_fields(item, existing_public_by_id):
            preserved_ai_ids.add(item["id"])
    preserved_ai_count = len(preserved_ai_ids)
    rule_based_or_unsummarized_count = len(output) - preserved_ai_count
    dropped_old_ai_count = len(existing_ai_ids - preserved_ai_ids)

    # Self-check: guarantee the UI schema before writing.
    for it in output:
        missing = [k for k in REQUIRED_FIELDS if k not in it]
        if missing:
            print(f"ERROR: built item missing fields {missing}: {it.get('id')}", file=sys.stderr)
            return 2

    backup_created = False
    if not args.dry_run:
        if OUTPUT_PATH.exists():
            shutil.copyfile(OUTPUT_PATH, BACKUP_PATH)
            backup_created = True
        save_json(OUTPUT_PATH, output)

    scores = [it["relevance_score"] for it in output]
    top_score = max(scores) if scores else None
    lowest_score = min(scores) if scores else None

    print("\n==== build_public_data summary ====")
    print(f"input_items                   : {input_items}")
    print(f"excluded_items                : {excluded_items}")
    print(f"candidate_items               : {candidate_items}")
    print(f"output_items                  : {len(output)}")
    print(f"preserved_ai_summaries        : {preserved_ai_count}")
    print(f"rule_based_or_unsummarized    : {rule_based_or_unsummarized_count}")
    print(f"dropped_old_ai_summaries      : {dropped_old_ai_count}")
    print(f"backup_created                : {backup_created}")
    print(f"top_relevance_score           : {top_score}")
    print(f"lowest_output_relevance_score : {lowest_score}")
    print(f"output_path                   : {OUTPUT_PATH}")
    if args.dry_run:
        print("(dry-run: no backup written, output file not modified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
