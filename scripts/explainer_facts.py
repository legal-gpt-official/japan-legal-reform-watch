"""Deterministic date and number cross-check between an English explainer and its translation.

A translation may restate a date or figure in its own notation -- 令和8年 for
2026, 50万 for 500,000, 4割 for 40%, 2027年1月4日 for 4 January 2027 -- but it
must never state a date or number the canonical English explainer does not.
The check is deliberately one-sided and document-wide: an omission, or a
year given in one paragraph and left implicit in the next, is not flagged,
because both are ordinary in faithful Japanese and Chinese prose; a changed
or invented date, threshold, act number or period is. A translated figure
that happens to occur elsewhere in the same English explainer cannot be
caught this way, so this narrows, and does not replace, the writer's check.
"""
from __future__ import annotations

import re
from typing import Iterable

_MONTHS = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(), 1)}
_MON = "|".join(_MONTHS)
_EN_RANGE_D = re.compile(rf"\b(\d{{1,2}}) (?:and|to|or|-|–) (\d{{1,2}}) ({_MON})", re.I)
_EN_RANGE_M = re.compile(rf"\b({_MON}) (?:and|to|or|-|–) ({_MON}) (\d{{4}})", re.I)
_EN_DMY = re.compile(rf"\b(\d{{1,2}}) ({_MON})(?:,? (\d{{4}}))?", re.I)
_EN_MDY = re.compile(rf"\b({_MON}) (\d{{1,2}}),? (\d{{4}})", re.I)
_EN_MY = re.compile(rf"\b({_MON})(?: (\d{{4}}))?\b", re.I)
_EN_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
_EN_WORDS.update({"twice": 2, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "dozen": 12,
                  "hundred": 100})
_ROMAN = re.compile(r"\((i{1,3}|iv|v|vi{1,3}|ix|x)\)")
_ROMAN_VALUES = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NUM = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")

_CJK_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9}
_CJK_NUM = "〇零一二两兩三四五六七八九十百千"
_ERA = re.compile(rf"令和([\d{_CJK_NUM}]+|元)年")
_CJK_RANGE_D = re.compile(r"(\d{1,2})月(\d{1,2})日(?:から|より|至|到|～|〜|~|-|－|—|・|、|和|及|与)(\d{1,2})日")
_CJK_RANGE_M = re.compile(r"(\d{4})年(\d{1,2})月?(?:から|至|到|～|〜|~|-|－)(\d{1,2})月")
_CJK_YMD = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日")
_CJK_YM = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(?!\d)")
# A CJK numeral is read as a quantity only before a counter; "两院" (both
# Houses) or "第一，第二" (enumeration) is not a number the English states.
_CJK_COUNTED = re.compile(
    rf"([{_CJK_NUM}]+)(?=年|か月|ヶ月|カ月|个月|個月|月|日|名|人|户|戸|项|項|件|批|类|類|次|倍|%|％|割|成|天|周|週|回|号|条|款|项)")


def _cjk_int(s: str) -> int:
    if s.isdigit():
        return int(s)
    if s == "元":
        return 1
    total, cur = 0, 0
    for ch in s:
        if ch in _CJK_DIGITS:
            cur = cur * 10 + _CJK_DIGITS[ch] if cur else _CJK_DIGITS[ch]
        elif ch in "十百千":
            total += (cur or 1) * {"十": 10, "百": 100, "千": 1000}[ch]
            cur = 0
    return total + cur


def _int(s: str) -> int:
    return int(s.replace(",", ""))


def english_facts(text: str) -> tuple[set, set]:
    """(dates, numbers) an English text states. Dates are (y|None, m, d|0)."""
    dates, numbers = set(), set()
    for m in _ISO.finditer(text):
        dates.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    text = _ISO.sub(" ", text)

    def months(name: str) -> int:
        return _MONTHS[name.lower()]

    def dmy_range(m):
        for d in (m.group(1), m.group(2)):
            dates.add((None, months(m.group(3)), int(d)))
        return f" {m.group(2)} {m.group(3)}"
    text = _EN_RANGE_D.sub(dmy_range, text)

    def my_range(m):
        for name in (m.group(1), m.group(2)):
            dates.add((int(m.group(3)), months(name), 0))
        return " "
    text = _EN_RANGE_M.sub(my_range, text)
    for pattern, d_i, m_i, y_i in ((_EN_MDY, 2, 1, 3), (_EN_DMY, 1, 2, 3)):
        def take(m):
            year = int(m.group(y_i)) if m.group(y_i) else None
            dates.add((year, months(m.group(m_i)), int(m.group(d_i))))
            if year:
                numbers.add(year)
            return " "
        text = pattern.sub(take, text)

    def month_year(m):
        year = int(m.group(2)) if m.group(2) else None
        dates.add((year, months(m.group(1)), 0))
        if year:
            numbers.add(year)
        return " "
    text = _EN_MY.sub(month_year, text)
    numbers |= {_ROMAN_VALUES[r] for r in _ROMAN.findall(text)}
    text = _ROMAN.sub(" ", text)
    numbers |= {_int(n) for n in _NUM.findall(text)}
    numbers |= {_EN_WORDS[w.lower()] for w in re.findall(r"\b(" + "|".join(_EN_WORDS) + r")\b", text, re.I)}
    return dates, numbers


def local_facts(text: str) -> tuple[set, set]:
    """(dates, numbers) a Japanese or Simplified-Chinese text states, in the same form."""
    dates, numbers = set(), set()
    for m in _ISO.finditer(text):
        dates.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    text = _ISO.sub(" ", text)
    text = _ERA.sub(lambda m: f"{2018 + _cjk_int(m.group(1))}年", text)

    def d_range(m):
        dates.add((None, int(m.group(1)), int(m.group(2))))
        dates.add((None, int(m.group(1)), int(m.group(3))))
        return " "
    text = _CJK_RANGE_D.sub(d_range, text)

    def m_range(m):
        for month in (m.group(2), m.group(3)):
            dates.add((int(m.group(1)), int(month), 0))
        numbers.add(int(m.group(1)))
        return " "
    text = _CJK_RANGE_M.sub(m_range, text)

    def ymd(m):
        year = int(m.group(1)) if m.group(1) else None
        dates.add((year, int(m.group(2)), int(m.group(3))))
        if year:
            numbers.add(year)
        return " "
    text = _CJK_YMD.sub(ymd, text)

    def ym(m):
        year = int(m.group(1)) if m.group(1) else None
        dates.add((year, int(m.group(2)), 0))
        if year:
            numbers.add(year)
        return " "
    text = _CJK_YM.sub(ym, text)
    text = re.sub(r"(\d+(?:\.\d+)?)万", lambda m: str(int(float(m.group(1)) * 10000)), text)
    text = re.sub(r"(\d+)割", lambda m: str(int(m.group(1)) * 10) + "%", text)
    numbers |= {_int(n) for n in _NUM.findall(text)}
    numbers |= {_cjk_int(s) for s in _CJK_COUNTED.findall(text)}
    return dates, numbers


def _date_known(date: tuple, known: set) -> bool:
    year, month, day = date
    for k_year, k_month, k_day in known:
        if k_month != month or (day and k_day and k_day != day) or (day and not k_day):
            continue
        if year is None or k_year is None or k_year == year:
            return True
    return False


def unsupported_facts(english_texts: Iterable[str], local_texts: Iterable[str]) -> list[str]:
    """Dates and numbers a translation states that the canonical English explainer never states."""
    en_dates, en_numbers = set(), set()
    for text in english_texts:
        d, n = english_facts(text)
        en_dates |= d
        en_numbers |= n
    en_numbers |= {d[0] for d in en_dates if d[0]}
    problems = []
    for text in local_texts:
        dates, numbers = local_facts(text)
        for date in sorted(dates, key=lambda d: (d[0] or 0, d[1], d[2])):
            if not _date_known(date, en_dates):
                label = "-".join(f"{p:02d}" for p in date[1:] if p) if date[0] is None else \
                    "-".join([str(date[0])] + [f"{p:02d}" for p in date[1:] if p])
                problems.append(f"date {label}")
        for number in sorted(numbers):
            if number >= 2 and number not in en_numbers:
                problems.append(f"number {number}")
    return sorted(set(problems))
