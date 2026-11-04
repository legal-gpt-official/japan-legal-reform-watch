#!/usr/bin/env python3
"""Group raw records about the same legal instrument into a factual timeline.

A visitor reading one update usually wants to know how it got there and what
comes next. The answer is often already in the raw history as separate
records: an e-Gov public comment on a draft order, the e-Gov Law entries for
the order once promulgated, a House of Representatives bill and the law it
became. This module links them with three deliberately conservative rules and
renders nothing itself; ``build_public_pages.py`` shows the result.

* **R1 same instrument** -- e-Gov Law records are grouped by the exact
  instrument that made the change (``改正法令番号``, or ``法令番号`` for a new
  law). One instrument often amends several laws, so it has several records.
* **R2 bill -> law** -- a House of Representatives bill whose title minus the
  trailing ``案`` equals an e-Gov ``法律`` name exactly, promulgated within
  ``BILL_WINDOW_DAYS`` of the bill's first detection and in an adjacent
  calendar year, when exactly one bill and exactly one law qualify.
* **R3 public comment -> instrument** -- an e-Gov public comment whose title
  names a draft instrument (``…省令案``, ``…政令（案）``) equal to an e-Gov
  instrument name exactly, promulgated after the draft's publication date and
  within ``PC_WINDOW_DAYS``; the candidate must be unique, no other public
  comment may name the same draft, the name must belong to one instrument in
  the whole history (recurring names such as 予防接種実施規則の一部を改正する
  省令 are skipped), and when the instrument number names a ministry the
  consultation's contact must name that ministry too.

Anything ambiguous stays unlinked: a missing link only leaves a timeline
shorter, a wrong one would tell a reader that two unrelated measures are one.
Titles are compared after Unicode NFKC and the removal of whitespace and
brackets only -- never by similarity.

The output states dates and statuses the official sources give. It never
predicts whether a bill passes or a draft is adopted; the "next step" lines
describe the general procedure and say what this dashboard has not seen.
Every value derived from raw records is untrusted and must be escaped by the
renderer.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from alert_common import comment_deadline_display
from public_comment_deadlines import extract_egov_comment_deadline, normalize_comment_deadline

EGOV_LAW_PREFIX = "e-Gov Law"
EGOV_PUBLIC_COMMENT_PREFIX = "e-Gov Public"
DIET_PREFIX = "House of Representatives"

BILL_WINDOW_DAYS = 400
PC_WINDOW_DAYS = 365

# Ministries that can appear as the issuer inside an instrument number such as
# 令和八年国土交通省令第八十号. Agencies without their own ordinance power (金融庁,
# 消費者庁, 資源エネルギー庁 ...) are deliberately absent: matching an external
# agency to its ministry is left out of this version, so those links are skipped.
ISSUING_MINISTRIES = (
    "国土交通省",
    "厚生労働省",
    "経済産業省",
    "農林水産省",
    "環境省",
    "総務省",
    "財務省",
    "法務省",
    "文部科学省",
    "外務省",
    "防衛省",
    "内閣府",
    "デジタル庁",
    "復興庁",
)

_IGNORED_RE = re.compile(r"[\s　「」『』（）()]+")
_INSTRUMENT_WORD = r"(?:法律|政令|省令|府令|内閣府令|規則|告示|命令|訓令)"
_PC_DRAFT_RE = re.compile(
    r"^(.+?" + _INSTRUMENT_WORD + r")(?:（案）|案)(?:等)?(?:等)?"
    r"(?:に関する|に対する|について|についての|への)"
)
_PC_PUBLICATION_RE = re.compile(r"案の公示日：(\d{4})/(\d{2})/(\d{2})")
_ERA_RE = re.compile(r"^(令和|平成|昭和)(元|[〇一二三四五六七八九十百]+)年")
_ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925}
_KANJI_DIGITS = {c: i for i, c in enumerate("〇一二三四五六七八九")}


# ------------------------------------------------------------------ parsing


def normalize_title(value: Any) -> str:
    """Exact-match key: NFKC, whitespace and brackets removed, nothing else."""
    if not isinstance(value, str):
        return ""
    return _IGNORED_RE.sub("", unicodedata.normalize("NFKC", value))


def egov_law_fields(raw_summary: Any) -> dict[str, str]:
    """The ``key: value; …`` metadata an e-Gov Law record carries."""
    out: dict[str, str] = {}
    if not isinstance(raw_summary, str):
        return out
    for part in raw_summary.split(";"):
        key, sep, value = part.partition(":")
        if sep and key.strip():
            out[key.strip()] = value.strip()
    return out


def parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value.strip()) < 10:
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def kanji_number(text: str) -> int | None:
    if text == "元":
        return 1
    total = current = 0
    for ch in text:
        if ch in _KANJI_DIGITS:
            current = _KANJI_DIGITS[ch]
        elif ch == "十":
            total += (current or 1) * 10
            current = 0
        elif ch == "百":
            total += (current or 1) * 100
            current = 0
        else:
            return None
    return total + current


def instrument_year(number: str) -> int | None:
    """Western year of an instrument number such as 令和八年法律第六十七号."""
    match = _ERA_RE.match(number or "")
    if not match:
        return None
    value = kanji_number(match.group(2))
    return _ERA_BASE[match.group(1)] + value if value else None


def is_statute(number: str) -> bool:
    return "法律第" in (number or "")


def instrument_issuers(number: str) -> list[str]:
    return [name for name in ISSUING_MINISTRIES if name in (number or "")]


def public_comment_draft_name(title: Any) -> str:
    """The normalized name of the draft instrument a consultation title names, or ''."""
    if not isinstance(title, str):
        return ""
    text = unicodedata.normalize("NFKC", title).replace("(案)", "（案）")
    text = text.replace("「", "").replace("」", "")
    match = _PC_DRAFT_RE.match(text)
    return normalize_title(match.group(1)) if match else ""


def public_comment_publication_date(raw_summary: Any) -> date | None:
    match = _PC_PUBLICATION_RE.search(raw_summary if isinstance(raw_summary, str) else "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def diet_fields(raw_summary: Any) -> dict[str, str]:
    """``閣法; 審議状況: 成立; 提出回次: 221; 番号: 4`` -> fields (kind under ``種別``)."""
    out: dict[str, str] = {}
    if not isinstance(raw_summary, str):
        return out
    for index, part in enumerate(raw_summary.split(";")):
        key, sep, value = part.partition(":")
        if sep:
            out[key.strip()] = value.strip()
        elif index == 0 and part.strip():
            out["種別"] = part.strip()
    return out


def _source(raw: Mapping[str, Any]) -> str:
    value = raw.get("source_name")
    return value if isinstance(value, str) else ""


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


# ------------------------------------------------------------------ model


@dataclass(frozen=True)
class Event:
    """One dated (or, for a Diet bill, undated) fact in a dossier."""

    when: date | None
    rank: int  # tie-break within a day: consultation < bill < promulgation < effect
    label: str  # English, built from fixed phrases only
    detail_ja: str  # Japanese official name(s); untrusted, escape on render
    item_ids: tuple[str, ...]  # the raw records behind the fact, sorted
    source_url: str  # untrusted; pass through safe_http_url on render
    # The label in Japanese and Simplified Chinese, from the same fixed phrases.
    label_ja: str = ""
    label_zh: str = ""

    def localized_label(self, locale: str) -> str:
        if locale == "ja":
            return self.label_ja or self.label
        if locale == "zh-Hans":
            return self.label_zh or self.label
        return self.label


@dataclass
class Dossier:
    key: str
    events: list[Event] = field(default_factory=list)
    member_ids: list[str] = field(default_factory=list)
    next_step: str = ""
    cross_source: bool = False
    # Latest first_seen_at among the members: the day the timeline last
    # gained a record, used as the item page's sitemap lastmod.
    latest_detected: date | None = None

    def sort_events(self) -> None:
        dated = [e.when for e in self.events if e.when]
        anchor = min(dated) if dated else date.max
        promulgated = [e.when for e in self.events if e.rank == 2 and e.when]
        bill_anchor = min(promulgated) if promulgated else anchor
        self.events.sort(
            key=lambda e: (e.when or bill_anchor, e.rank, e.label, e.detail_ja, e.item_ids)
        )


@dataclass
class _Instrument:
    number: str
    names: set[str] = field(default_factory=set)
    records: list[Mapping[str, Any]] = field(default_factory=list)

    @property
    def promulgated(self) -> date | None:
        dates = [parse_date(egov_law_fields(r.get("raw_summary")).get("公布日")) for r in self.records]
        dates = [d for d in dates if d]
        return min(dates) if dates else None


# ------------------------------------------------------------------ linking


def _instruments(raw_items: Iterable[Mapping[str, Any]]) -> dict[str, _Instrument]:
    out: dict[str, _Instrument] = {}
    for raw in raw_items:
        if not _source(raw).startswith(EGOV_LAW_PREFIX):
            continue
        fields = egov_law_fields(raw.get("raw_summary"))
        number = fields.get("改正法令番号") or fields.get("法令番号")
        name = fields.get("改正法令") or fields.get("対象法令")
        if not number or not name:
            continue
        instrument = out.setdefault(number, _Instrument(number))
        instrument.names.add(name)
        instrument.records.append(raw)
    return out


def _by_name(instruments: Mapping[str, _Instrument]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = defaultdict(set)
    for number, instrument in instruments.items():
        for name in instrument.names:
            index[normalize_title(name)].add(number)
    return index


def link_bills(
    bills: Sequence[Mapping[str, Any]],
    instruments: Mapping[str, _Instrument],
    by_name: Mapping[str, set[str]],
) -> dict[str, str]:
    """R2: bill raw id -> statute instrument number."""
    keys = {}
    for bill in bills:
        title = bill.get("title_ja")
        if isinstance(title, str) and title.endswith("案"):
            keys[bill["id"]] = normalize_title(title[:-1])
    key_count = Counter(keys.values())

    links: dict[str, str] = {}
    claimed: Counter[str] = Counter()
    for bill in bills:
        key = keys.get(bill.get("id"))
        first_seen = parse_date(bill.get("first_seen_at"))
        if not key or key_count[key] != 1 or not first_seen:
            continue
        candidates = []
        for number in by_name.get(key, ()):
            promulgated = instruments[number].promulgated
            year = instrument_year(number)
            if (
                is_statute(number)
                and promulgated
                and year in (first_seen.year - 1, first_seen.year, first_seen.year + 1)
                and abs((promulgated - first_seen).days) <= BILL_WINDOW_DAYS
            ):
                candidates.append(number)
        if len(candidates) == 1:
            links[bill["id"]] = candidates[0]
            claimed[candidates[0]] += 1
    return {bill_id: number for bill_id, number in links.items() if claimed[number] == 1}


def link_public_comments(
    comments: Sequence[Mapping[str, Any]],
    instruments: Mapping[str, _Instrument],
    by_name: Mapping[str, set[str]],
) -> dict[str, str]:
    """R3: public comment raw id -> instrument number."""
    drafts = {c["id"]: public_comment_draft_name(c.get("title_ja")) for c in comments}
    draft_count = Counter(name for name in drafts.values() if name)

    links: dict[str, str] = {}
    claimed: Counter[str] = Counter()
    for comment in comments:
        draft = drafts.get(comment.get("id"))
        published = public_comment_publication_date(comment.get("raw_summary"))
        if not draft or draft_count[draft] != 1 or not published:
            continue
        numbers = by_name.get(draft, set())
        if len(numbers) != 1:
            continue
        number = next(iter(numbers))
        promulgated = instruments[number].promulgated
        if not promulgated or not published < promulgated <= published + timedelta(days=PC_WINDOW_DAYS):
            continue
        issuers = instrument_issuers(number)
        summary = comment.get("raw_summary") if isinstance(comment.get("raw_summary"), str) else ""
        if issuers and not any(name in summary for name in issuers):
            continue
        links[comment["id"]] = number
        claimed[number] += 1
    return {comment_id: number for comment_id, number in links.items() if claimed[number] == 1}


# ------------------------------------------------------------------ events


def _instrument_events(instrument: _Instrument) -> list[Event]:
    events: list[Event] = []
    names = sorted(instrument.names)
    records = sorted(instrument.records, key=lambda r: (r.get("id") or ""))
    first = records[0]
    statute = is_statute(instrument.number)
    events.append(
        Event(
            when=instrument.promulgated,
            rank=2,
            label=f"Promulgated ({'statute' if statute else 'instrument'} recorded by e-Gov Law Search)",
            label_ja=f"公布（e-Gov法令検索に記録された{'法律' if statute else '法令'}）",
            label_zh=f"公布（e-Gov法令检索记录的{'法律' if statute else '法令'}）",
            detail_ja=f"{names[0]}（{instrument.number}）",
            item_ids=tuple(r["id"] for r in records),
            source_url=first.get("source_url") or "",
        )
    )
    by_effect: dict[date | None, list[Mapping[str, Any]]] = defaultdict(list)
    for record in instrument.records:
        fields = egov_law_fields(record.get("raw_summary"))
        if fields.get("施行日"):
            by_effect[parse_date(fields.get("施行日"))].append(record)
    for when, records in by_effect.items():
        if when is None:
            continue
        targets = sorted(
            {egov_law_fields(r.get("raw_summary")).get("対象法令", "") for r in records} - {""}
        )
        shown = "、".join(targets[:5]) + (f" ほか{len(targets) - 5}件" if len(targets) > 5 else "")
        record = sorted(records, key=lambda r: (r.get("id") or ""))[0]
        events.append(
            Event(
                when=when,
                rank=3,
                label="Effective date stated by e-Gov Law Search",
                label_ja="施行日（e-Gov法令検索の記載）",
                label_zh="施行日期（e-Gov法令检索所载）",
                detail_ja=shown,
                item_ids=tuple(sorted(r["id"] for r in records)),
                source_url=record.get("source_url") or "",
            )
        )
    return events


def _comment_events(comment: Mapping[str, Any]) -> list[Event]:
    events = [
        Event(
            when=public_comment_publication_date(comment.get("raw_summary")),
            rank=0,
            label="Draft published for public comment (e-Gov)",
            label_ja="意見募集のため案を公示（e-Gov）",
            label_zh="草案公开征求意见（e-Gov）",
            detail_ja=_text(comment.get("title_ja")),
            item_ids=(comment["id"],),
            source_url=comment.get("source_url") or "",
        )
    ]
    deadline = normalize_comment_deadline(comment.get("comment_deadline")) or extract_egov_comment_deadline(
        comment.get("raw_summary"), comment.get("source_type")
    )
    if deadline:
        shown = comment_deadline_display(deadline)
        shown_ja = shown.replace(" (end of day, JST)", "・日本時間当日中")
        shown_zh = shown.replace(" (end of day, JST)", "，日本时间当日截止")
        events.append(
            Event(
                when=parse_date(deadline),
                rank=1,
                label=f"Public comment deadline ({shown})",
                label_ja=f"意見募集の締切（{shown_ja}）",
                label_zh=f"公开征求意见截止（{shown_zh}）",
                detail_ja="",
                item_ids=(comment["id"],),
                source_url=comment.get("source_url") or "",
            )
        )
    return events


def _bill_event(bill: Mapping[str, Any]) -> Event:
    fields = diet_fields(bill.get("raw_summary"))
    session = fields.get("提出回次", "")
    status = fields.get("審議状況", "")
    label = "Bill listed by the House of Representatives"
    label_ja = "衆議院の議案一覧に掲載"
    label_zh = "众议院议案列表所载"
    if session.isdigit():
        label += f" (Diet session {session})"
        label_ja += f"（第{session}回国会）"
        label_zh += f"（第{session}次国会）"
    return Event(
        when=None,
        rank=1,
        label=label,
        label_ja=label_ja,
        label_zh=label_zh,
        detail_ja=_text(bill.get("title_ja")) + (f"（審議状況：{status}）" if status else ""),
        item_ids=(bill["id"],),
        source_url=bill.get("source_url") or "",
    )


NEXT_STEP_BILL_PENDING = (
    "General procedure: a bill becomes law only if both Houses of the Diet pass it; an enacted law is "
    "then promulgated, and its effective date is set by the law itself or by a later Cabinet Order. "
    "This is not a forecast of whether this bill will pass. Check the House of Representatives page for "
    "its current status."
)
NEXT_STEP_BILL_ENACTED = (
    "The House of Representatives lists this bill as enacted (成立). Promulgation of the resulting law "
    "has not yet been matched by this dashboard; the official gazette and e-Gov Law Search are "
    "authoritative."
)
NEXT_STEP_BILL_ENDED = (
    "The House of Representatives lists this bill as not concluded (未了) or withdrawn (撤回). A bill in "
    "that status does not become law in this form; a later bill may be introduced. Check the House of "
    "Representatives page for details."
)
BILL_ENDED_STATUSES = frozenset({"未了", "撤回"})
NEXT_STEP_DRAFT = (
    "General procedure: after a consultation closes, the ministry usually publishes the comments received "
    "and its responses, and may then adopt the instrument, possibly revised, or not adopt it. This "
    "dashboard has not yet matched a promulgated instrument with exactly this name. This is not a "
    "forecast of the outcome."
)


# The four next-step notes in Japanese and Simplified Chinese, keyed by the
# English text a dossier carries. Same content: procedure only, no forecast.
NEXT_STEP_TRANSLATIONS: dict[str, dict[str, str]] = {
    NEXT_STEP_BILL_PENDING: {
        "ja": (
            "一般的な手続：法律案は衆議院と参議院の両院で可決されて初めて法律として成立します。成立した法律は"
            "その後公布され、施行日は法律自体または後の政令で定められます。これはこの法案が成立するかどうかの"
            "予測ではありません。現在の審議状況は衆議院のページでご確認ください。"
        ),
        "zh-Hans": (
            "一般程序：法案须经国会两院通过方能成为法律；制定的法律随后公布，其施行日期由法律本身或此后的政令"
            "确定。这并非对该法案能否通过的预测。当前审议状况请查阅众议院页面。"
        ),
    },
    NEXT_STEP_BILL_ENACTED: {
        "ja": (
            "衆議院はこの法案を「成立」と掲載しています。成立した法律の公布は、本ダッシュボードではまだ照合"
            "できていません。官報およびe-Gov法令検索が正式な情報源です。"
        ),
        "zh-Hans": (
            "众议院将该法案列为已通过（成立）。本仪表板尚未匹配到由此产生的法律的公布记录；"
            "应以官报和e-Gov法令检索为准。"
        ),
    },
    NEXT_STEP_BILL_ENDED: {
        "ja": (
            "衆議院はこの法案を「未了」または「撤回」と掲載しています。この状態の法案はこのままの形では法律に"
            "なりませんが、後に別の法案が提出される場合があります。詳細は衆議院のページでご確認ください。"
        ),
        "zh-Hans": (
            "众议院将该法案列为审议未完成（未了）或已撤回（撤回）。处于该状态的法案不会以现有形式成为法律；"
            "之后可能会提出新的法案。详情请查阅众议院页面。"
        ),
    },
    NEXT_STEP_DRAFT: {
        "ja": (
            "一般的な手続：意見募集の終了後、所管省庁は通常、寄せられた意見とそれに対する考え方を公表し、"
            "その後、案を（修正のうえ）制定する場合も、制定しない場合もあります。本ダッシュボードでは、"
            "同じ名称の公布済み法令をまだ照合できていません。これは結果の予測ではありません。"
        ),
        "zh-Hans": (
            "一般程序：征求意见结束后，主管省厅通常会公布收到的意见及其回应，之后可能（经修改后）制定该法令，"
            "也可能不予制定。本仪表板尚未匹配到名称完全相同的已公布法令。这并非对结果的预测。"
        ),
    },
}


def localized_next_step(next_step: str, locale: str) -> str:
    """A dossier's next-step note in ``locale``, falling back to English."""
    return NEXT_STEP_TRANSLATIONS.get(next_step, {}).get(locale, next_step)


def bill_next_step(status: str) -> str:
    if status == "成立":
        return NEXT_STEP_BILL_ENACTED
    if status in BILL_ENDED_STATUSES:
        return NEXT_STEP_BILL_ENDED
    return NEXT_STEP_BILL_PENDING


# ------------------------------------------------------------------ assembly


def build_dossiers(raw_items: Sequence[Mapping[str, Any]]) -> dict[str, Dossier]:
    """Map each raw item id that belongs to a timeline to its dossier.

    An item is included when its dossier has at least two events, or when it
    is an unmatched bill or draft-instrument consultation, which still gets
    a general next-step note.
    """
    raw_items = [r for r in raw_items if isinstance(r, Mapping) and isinstance(r.get("id"), str)]
    instruments = _instruments(raw_items)
    by_name = _by_name(instruments)
    bills = [r for r in raw_items if _source(r).startswith(DIET_PREFIX)]
    comments = [r for r in raw_items if _source(r).startswith(EGOV_PUBLIC_COMMENT_PREFIX)]
    bill_links = link_bills(bills, instruments, by_name)
    comment_links = link_public_comments(comments, instruments, by_name)

    dossiers: dict[str, Dossier] = {}
    for number, instrument in instruments.items():
        dossier = Dossier(key=f"instrument:{number}")
        dossier.events += _instrument_events(instrument)
        dossier.member_ids += [r["id"] for r in instrument.records]
        dossiers[number] = dossier
    for bill in bills:
        number = bill_links.get(bill["id"])
        if number:
            dossiers[number].events.append(_bill_event(bill))
            dossiers[number].member_ids.append(bill["id"])
            dossiers[number].cross_source = True
    for comment in comments:
        number = comment_links.get(comment["id"])
        if number:
            dossiers[number].events += _comment_events(comment)
            dossiers[number].member_ids.append(comment["id"])
            dossiers[number].cross_source = True

    first_seen = {r["id"]: parse_date(r.get("first_seen_at")) for r in raw_items}

    by_item: dict[str, Dossier] = {}
    for dossier in dossiers.values():
        dossier.member_ids = sorted(set(dossier.member_ids))
        dossier.sort_events()
        detected = [first_seen.get(item_id) for item_id in dossier.member_ids]
        dossier.latest_detected = max((d for d in detected if d), default=None)
        if len(dossier.events) >= 2:
            for item_id in dossier.member_ids:
                by_item[item_id] = dossier

    for bill in bills:
        if bill["id"] in bill_links or not isinstance(bill.get("title_ja"), str) or not bill["title_ja"].endswith("案"):
            continue
        status = diet_fields(bill.get("raw_summary")).get("審議状況", "")
        by_item[bill["id"]] = Dossier(
            key=f"bill:{bill['id']}",
            events=[_bill_event(bill)],
            member_ids=[bill["id"]],
            next_step=bill_next_step(status),
            latest_detected=first_seen.get(bill["id"]),
        )
    for comment in comments:
        if comment["id"] in comment_links or not public_comment_draft_name(comment.get("title_ja")):
            continue
        dossier = Dossier(
            key=f"draft:{comment['id']}",
            events=_comment_events(comment),
            member_ids=[comment["id"]],
            next_step=NEXT_STEP_DRAFT,
            latest_detected=first_seen.get(comment["id"]),
        )
        dossier.sort_events()
        by_item[comment["id"]] = dossier
    return by_item


def summarize(by_item: Mapping[str, Dossier]) -> dict[str, int]:
    unique = {id(d): d for d in by_item.values()}.values()
    return {
        "timeline_items": len(by_item),
        "dossiers_linked": sum(1 for d in unique if not d.next_step),
        "dossiers_cross_source": sum(1 for d in unique if d.cross_source),
        "next_step_only": sum(1 for d in unique if d.next_step),
    }
