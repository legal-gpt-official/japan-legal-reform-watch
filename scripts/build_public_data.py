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
- Emits MODEST English PLACEHOLDER copy (no translation, no interpretation).
- Orders by an internal ordering score, then impact weight (Medium > Low), then recency;
  caps the output, backs up the previous file, and writes
  docs/data/legal_updates.json.

What this script deliberately does NOT do
------------------------------------------
- No Claude / LLM calls and NO AI translation or summarization.
- No legal judgement. The relevance score is a TECHNICAL HEURISTIC for narrowing
  which items to surface — not an assessment of legal importance. title_en /
  summary_en / business_impact_en / recommended_action_en are fixed templates.
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

MAX_OUTPUT_ITEMS = 50  # initial conservative cap
JST = timezone(timedelta(hours=9))  # display Japanese-source dates on the JST calendar

# The 13 fields the existing dashboard UI expects. (relevance_score is an extra,
# optional field appended after these; the UI ignores unknown fields.)
REQUIRED_FIELDS = (
    "id", "title_en", "title_ja", "area", "stage", "impact_level",
    "summary_en", "business_impact_en", "recommended_action_en",
    "source_name", "source_url", "published_at", "last_checked",
)

# Modest, NON-interpretive placeholder copy (no AI, no legal conclusion).
TITLE_EN_PREFIX = "Japanese Regulatory Update: "
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
AREA_SOURCE_FALLBACK = (
    ("Finance / AML", ("金融庁", "FSA")),
    ("Data / Privacy / AI", ("デジタル庁", "Digital Agency")),
    ("Consumer / Advertising", ("消費者庁", "CAA")),
    ("Data / Privacy / AI", ("個人情報保護委員会", "PPC")),
    ("Antitrust / Fair Trade", ("公正取引委員会", "JFTC")),
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
_PC_RESULT_MARKERS = (
    "意見募集結果", "意見募集の結果", "意見の募集の結果", "募集の結果",
    "結果の公示", "結果について", "パブリックコメントの結果",
)
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
    if contains_any(title_ja, _PC_RESULT_MARKERS) or contains_any(title_lower, _PC_RESULT_MARKERS_EN):
        return "Public Comment Results Published"
    if contains_any(title_ja, _PC_CLOSED_MARKERS) or contains_any(title_lower, _PC_CLOSED_MARKERS_EN):
        return "Public Comment Closed"
    if is_public_comment(source_type) or is_public_comment_title(title_ja):
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
    title_en = TITLE_EN_PREFIX + title_ja
    if stage == "Public Comment Closed":
        title_en = "Closed public comment: " + title_ja

    return {
        "id": raw.get("id") or "",                 # reuse the stable raw id (traceable)
        "title_en": title_en,                      # NOT a translation — a labelled passthrough
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
    print(f"backup_created                : {backup_created}")
    print(f"top_relevance_score           : {top_score}")
    print(f"lowest_output_relevance_score : {lowest_score}")
    print(f"output_path                   : {OUTPUT_PATH}")
    if args.dry_run:
        print("(dry-run: no backup written, output file not modified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
