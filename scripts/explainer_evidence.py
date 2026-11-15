#!/usr/bin/env python3
"""Deterministic official-source evidence packs for reform explainers (no AI, no paid API).

    python scripts/explainer_evidence.py build --from-queue 25 --include-ledger [--max-api-calls 160]
    python scripts/explainer_evidence.py build --seeds raw-...,raw-...
    python scripts/explainer_evidence.py show raw-...

Why this exists (measured 2026-11-15): the two dossiers held BLOCKED after case
files were introduced (PRs #156, #157) had never fetched a single source
(attempts 0 / an empty ledger): the writing environment cannot run network code,
so "the promulgated operative text could not be inspected" even though e-Gov's
official Law API v2 serves it in machine-readable form. Fetching is not a writing
task. This script runs on a GitHub-hosted runner and writes one bounded pack per
candidate to ``data/explainer_evidence/<seed>.json``:

* **e-Gov amendments** (from the record's ``lawid=<law>_<date>_<amending law>``,
  which IS the e-Gov revision id): the revision list (promulgation date, every
  enforcement date of the same amending instrument = partial commencement, an
  enforcement left to a Cabinet Order = ``amendment_enforcement_comment``, never a
  fixed date), the full text of the new revision and of the revision before it,
  an article-level old/new diff (citation-only edits labelled), the amending
  instrument's own supplementary provisions (施行期日 / 経過措置 sentences), and
  sentences in changed provisions that delegate (政令で定める …) or carry a legal
  modality (しなければならない / してはならない / 努めなければならない / この限りでない).
* **e-Gov public-comment drafts**: whether e-Gov now lists a promulgated
  instrument with the draft's exact title after the draft was published. A unique
  match is resolved; several same-titled amendments are narrowed by the deadline
  and by article numbers the draft's own documents cite; anything else stays
  ``ambiguous`` with every candidate's diff listed, or ``not_found_in_egov``
  (absence is never evidence that a draft was abandoned; notices are mostly not in
  e-Gov).

Every extracted item carries its official URL, the revision id, the SHA-256 of
the exact API response and the fetch time. Facts that are pure record data
(dates, numbers, which articles changed) are marked ``machine_confirmed``; what
they MEAN (scope, obligation type, policy, impact) stays ``requires_ai``. A pack
also carries a ``case_file_template`` a writer can copy into
``data/explainer_state/<id>.json`` without running anything.

Access controls are respected: only official hosts, every redirect re-checked,
bounded bodies, a bounded number of API calls per run, no retries of 4xx.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote, urlsplit, urljoin, parse_qs

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import explainer_state as es  # noqa: E402
import explainers as ex  # noqa: E402
from alert_common import JST  # noqa: E402

EVIDENCE_DIR = ex.REPO_ROOT / "data" / "explainer_evidence"
API = "https://laws.e-gov.go.jp/api/2"
SCHEMA_VERSION = 1
MAX_TEXT = 1500           # characters kept per provision text
MAX_DIFF_ITEMS = 40       # changed/added/removed provisions kept per instrument
MAX_SUPPL = 6000          # characters of the amending instrument's supplementary provisions
MAX_CANDIDATES = 4        # same-titled promulgated amendments examined for one draft
MAX_SENTENCES = 30
DEFAULT_MAX_API_CALLS = 160
LAW_DATA_LIMIT = 8_000_000
SMALL_LIMIT = 1_500_000
USER_AGENT = "JapanLegalReformWatch/1.0 (explainer evidence; +https://updates.legal-gpt.com/)"

# ------------------------------------------------------------------ numbers and law numbers

_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000}


def kanji_int(text: str) -> int | None:
    """'百二十一' -> 121, '一二一' -> 121, '１２１' / '121' -> 121, '元' -> 1."""
    s = unicodedata.normalize("NFKC", text or "").strip()
    if not s:
        return None
    if s == "元":
        return 1
    if s.isdigit():
        return int(s)
    if any(ch in _UNITS for ch in s):
        total, current = 0, 0
        for ch in s:
            if ch in _DIGITS:
                current = _DIGITS[ch]
            elif ch in _UNITS:
                total += (current or 1) * _UNITS[ch]
                current = 0
            else:
                return None
        return total + current
    if all(ch in _DIGITS for ch in s):
        return int("".join(str(_DIGITS[ch]) for ch in s))
    return None


_LAWNUM = re.compile(
    r"^(明治|大正|昭和|平成|令和)([^年]+)年(?:[^月]+月[^日]+日)?(.+?)第([^号]+)号$")


def law_number_key(text: str | None) -> tuple | str | None:
    """Comparable key for '令和八年厚生労働省令第百二十一号' and '令和八年七月三一日厚生労働省令第一二一号'."""
    if not text:
        return None
    s = unicodedata.normalize("NFKC", text).replace(" ", "").replace("　", "")
    match = _LAWNUM.match(s)
    if not match:
        return s
    era, year, issuer, num = match.groups()
    y, n = kanji_int(year), kanji_int(num)
    if y is None or n is None:
        return s
    return (era, y, issuer, n)


# ------------------------------------------------------------------ e-Gov law text


def node_text(node: Any) -> str:
    """Plain text of an e-Gov JSON law node; titles get a space, structural blocks a newline."""
    if isinstance(node, str):
        return node
    if not isinstance(node, Mapping):
        return ""
    tag = node.get("tag", "")
    parts = [node_text(c) for c in node.get("children") or []]
    text = "".join(parts)
    if tag.endswith("Title") or tag.endswith("Num") or tag in ("ArticleCaption", "ParagraphCaption"):
        return text + (" " if text else "")
    if tag in ("Paragraph", "Item", "Subitem1", "Subitem2", "Subitem3", "TableRow"):
        return text.rstrip() + "\n"
    return text


def _squeeze(text: str) -> str:
    return re.sub(r"[ \t　]+", " ", re.sub(r"\n+", "\n", text)).strip()


def provisions(tree: Mapping[str, Any]) -> dict[str, str]:
    """Diffable units: main-provision articles (and paragraphs of article-less laws),
    appended tables/forms by title, and supplementary provisions keyed by amending law."""
    out: dict[str, str] = {}

    def walk(node: Any, prefix: str) -> None:
        if not isinstance(node, Mapping):
            return
        tag = node.get("tag")
        attr = node.get("attr") or {}
        if tag == "TOC":
            return
        if tag == "SupplProvision":
            label = attr.get("AmendLawNum") or "原始"
            out[f"附則[{label}]"] = _squeeze(node_text(node))[:MAX_SUPPL]
            return
        if tag == "Article":
            out[f"{prefix}第{attr.get('Num', '?')}条"] = _squeeze(node_text(node))
            return
        if tag == "Paragraph" and prefix.endswith("本則/"):
            out[f"{prefix}項{attr.get('Num', '?')}"] = _squeeze(node_text(node))
            return
        if tag in ("AppdxTable", "AppdxStyle", "AppdxFormat", "AppdxNote", "AppdxFig", "Appdx"):
            title = ""
            for child in node.get("children") or []:
                if isinstance(child, Mapping) and str(child.get("tag", "")).endswith("Title"):
                    title = _squeeze(node_text(child))
                    break
            out[f"別表等[{title or tag}]"] = _squeeze(node_text(node))
            return
        child_prefix = "本則/" if tag == "MainProvision" else prefix
        for child in node.get("children") or []:
            walk(child, child_prefix)

    walk(tree, "")
    return out


_CITATION = re.compile(r"（(?:明治|大正|昭和|平成|令和)[^（）]{1,20}年[^（）]{0,30}第[^（）]{1,12}号）")


def citation_only(old: str, new: str) -> bool:
    """True when two provision texts differ only by parenthesised law-number citations."""
    strip = lambda s: re.sub(r"\s+", "", _CITATION.sub("", s))  # noqa: E731
    return strip(old) == strip(new)


def article_label(path: str) -> str:
    """'本則/第73_7条' -> '第七十三条の七' style readable label ('第73条の7')."""
    match = re.search(r"第([0-9_]+)条", path)
    if not match:
        return path.replace("本則/", "")
    parts = match.group(1).split("_")
    return "第" + parts[0] + "条" + "".join("の" + p for p in parts[1:])


MAX_SPANS = 12
SPAN_CONTEXT = 120
SPAN_TEXT = 700


def change_spans(old: str, new: str) -> list[dict[str, str]]:
    """The exact changed passages of a provision with a little context, so a change deep inside a
    long article is never hidden by truncating the full text."""
    import difflib

    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    spans = []
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op == "equal":
            continue
        spans.append({
            "op": op,
            "before": old[max(0, a1 - SPAN_CONTEXT):a1],
            "old": old[a1:a2][:SPAN_TEXT],
            "new": new[b1:b2][:SPAN_TEXT],
            "after": new[b2:b2 + SPAN_CONTEXT],
        })
        if len(spans) >= MAX_SPANS:
            break
    return spans


def diff_provisions(old: Mapping[str, str], new: Mapping[str, str]) -> dict[str, list[dict[str, Any]]]:
    added, changed, removed = [], [], []
    for key, text in new.items():
        if key.startswith("附則["):
            continue
        if key not in old:
            added.append({"path": key, "label": article_label(key), "new": text[:MAX_TEXT]})
        elif old[key] != text:
            changed.append({"path": key, "label": article_label(key), "old": old[key][:MAX_TEXT],
                            "new": text[:MAX_TEXT], "citation_only": citation_only(old[key], text),
                            "spans": change_spans(old[key], text),
                            "full_length": {"old": len(old[key]), "new": len(text)}})
    for key, text in old.items():
        if key not in new and not key.startswith("附則["):
            removed.append({"path": key, "label": article_label(key), "old": text[:MAX_TEXT]})
    substantive = [c for c in changed if not c["citation_only"]]
    return {
        "added": added[:MAX_DIFF_ITEMS],
        "changed": (substantive + [c for c in changed if c["citation_only"]])[:MAX_DIFF_ITEMS],
        "removed": removed[:MAX_DIFF_ITEMS],
        "counts": {"added": len(added), "changed": len(substantive),
                   "citation_only": len(changed) - len(substantive), "removed": len(removed)},
    }


_SENT_SPLIT = re.compile(r"(?<=。)")
EFFECTIVE_CUES = ("から施行する", "施行期日", "政令で定める日から施行", "公布の日から施行")
TRANSITIONAL_CUES = ("経過措置", "なお従前の例", "従前の例による", "この省令の施行の際", "この政令の施行の際",
                     "この法律の施行の際", "施行日前", "施行の日前", "なお効力を有する", "適用しない", "適用する")
DELEGATION = re.compile(r"(政令|内閣府令|省令|厚生労働省令|[^\s、。]{1,12}省令|規則|告示|大臣|長官)で定め")
MODALITY = (
    ("effort_duty", re.compile(r"努めなければならない|努めるものとする|努力しなければならない")),
    ("prohibition", re.compile(r"してはならない|しては、ならない|禁止する|禁止される")),
    ("obligation", re.compile(r"しなければならない|なければならない|義務を負う|ものとする")),
    ("exception", re.compile(r"この限りでない|適用しない|除く。")),
    ("permission", re.compile(r"ことができる")),
)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text.replace("\n", "")) if s.strip()]


IMPLEMENTS = re.compile(r"に規定する[^、。]{0,16}(?:政令|内閣府令|省令|規則|告示)で定め|"
                        r"の(?:政令|内閣府令|省令|[^\s、。]{1,12}省令)で定めるところにより")


def delegation_kind(sentence: str) -> str | None:
    """'implements': the provision IS the rule a higher instrument delegated
    ('法第八十八条の三第一項に規定する厚生労働省令で定める事由は…');
    'further': it leaves something to yet another instrument ('…は、政令で定める。')."""
    matches = list(DELEGATION.finditer(sentence))
    if not matches:
        return None
    implemented = [(m.start(), m.end()) for m in IMPLEMENTS.finditer(sentence)]
    further = [m for m in matches if not any(m.start() < end and start < m.end() for start, end in implemented)]
    return "further" if further else "implements"


def tag_sentences(items: Iterable[Mapping[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Delegation and modality sentences in added / substantively changed provisions."""
    delegations, modalities = [], []
    for item in items:
        if item.get("citation_only"):
            continue
        span_text = "".join(sp.get("before", "") + sp.get("new", "") + sp.get("after", "")
                            for sp in item.get("spans") or [])
        for sentence in sentences((item.get("new") or "") + "。" + span_text):
            kind = delegation_kind(sentence)
            if kind and len(delegations) < MAX_SENTENCES:
                delegations.append({"label": item["label"], "kind": kind, "sentence": sentence[:400]})
            for kind, pattern in MODALITY:
                if pattern.search(sentence):
                    if len(modalities) < MAX_SENTENCES:
                        modalities.append({"label": item["label"], "kind": kind, "sentence": sentence[:400]})
                    break
    return delegations, modalities


def suppl_for(prov: Mapping[str, str], amend_num: str | None) -> str | None:
    """The amending instrument's own supplementary provisions inside the consolidated text."""
    want = law_number_key(amend_num)
    for key, text in prov.items():
        if key.startswith("附則[") and want is not None and law_number_key(key[3:-1]) == want:
            return text
    return None


# ------------------------------------------------------------------ fetching


class FetchBudgetExceeded(Exception):
    pass


@dataclass
class Fetched:
    url: str
    data: Any
    sha256: str
    fetched_at: str


@dataclass
class EgovClient:
    """Bounded, cached, official-host-only fetcher. ``fetch`` returns (bytes, final_url)."""
    fetch: Callable[[str, int], tuple[bytes, str]]
    max_calls: int = DEFAULT_MAX_API_CALLS
    calls: int = 0
    cache: dict[str, Fetched] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def get_json(self, url: str, limit: int = SMALL_LIMIT) -> Fetched | None:
        if url in self.cache:
            return self.cache[url]
        if self.calls >= self.max_calls:
            raise FetchBudgetExceeded(url)
        if not es.official_url(url):
            self.errors.append(f"non_official_url {url}")
            return None
        self.calls += 1
        try:
            body, final = self.fetch(url, limit)
        except Exception as exc:  # noqa: BLE001 - classified, recorded, never re-raised
            import explainer_sources as src
            err = src.classify_exception(exc)
            self.errors.append(f"{err.error_class} {url}")
            return None
        if not es.official_url(final):
            self.errors.append(f"non_official_url {final}")
            return None
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self.errors.append(f"parse_error {url}")
            return None
        fetched = Fetched(url, data, hashlib.sha256(body).hexdigest(),
                          datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
        self.cache[url] = fetched
        return fetched

    def revisions(self, law_id: str) -> Fetched | None:
        return self.get_json(f"{API}/law_revisions/{quote(law_id)}")

    def law_data(self, revision_id: str) -> Fetched | None:
        return self.get_json(f"{API}/law_data/{quote(revision_id)}?response_format=json&law_full_text_format=json",
                             LAW_DATA_LIMIT)

    def search_title(self, title: str) -> Fetched | None:
        return self.get_json(f"{API}/laws?law_title={quote(title)}")


def http_fetch(url: str, limit: int) -> tuple[bytes, str]:
    """Live fetch on a GitHub-hosted runner (official hosts only, every redirect re-checked)."""
    import requests

    current = url
    for _ in range(4):
        if not es.official_url(current):
            raise ValueError("redirect to non-official URL")
        for attempt in range(3):
            try:
                with requests.get(current, timeout=(10, 60), allow_redirects=False, stream=True,
                                  headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        current = requests.compat.urljoin(current, resp.headers.get("Location", ""))
                        break
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        time.sleep(3 * (attempt + 1))
                        continue
                    resp.raise_for_status()
                    body = bytearray()
                    for chunk in resp.iter_content(65536):
                        body.extend(chunk)
                        if len(body) > limit:
                            raise ValueError("source document over size limit")
                    return bytes(body), current
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt + 1))
        else:
            continue
    raise ValueError("too many redirects")


# ------------------------------------------------------------------ instruments


def lawid_parts(url: Any) -> tuple[str, str] | None:
    """('335M50000100012', '335M50000100012_20261001_508M60000100121') from an e-Gov record URL."""
    if not isinstance(url, str):
        return None
    query = parse_qs(urlsplit(url).query)
    rid = (query.get("lawid") or [""])[0]
    match = re.fullmatch(r"([0-9A-Za-z]+)_(\d{8})_([0-9A-Za-z]+)", rid)
    if match:
        return match.group(1), rid
    return None


def _source(fetched: Fetched, sid: str, kind: str = "egov_law") -> dict[str, Any]:
    return {"id": sid, "url": fetched.url, "kind": kind, "status": "fetched", "fetched_at": fetched.fetched_at,
            "sha256": fetched.sha256, "error_class": None, "attempts": 1}


def amendment_evidence(client: EgovClient, law_id: str, revision_id: str, today: date) -> dict[str, Any]:
    """Dates, status, old/new diff and supplementary provisions of one amendment to one law."""
    revs = client.revisions(law_id)
    if revs is None:
        return {"status": "error", "error": "law_revisions unavailable", "law_id": law_id, "revision_id": revision_id}
    rows = [r for r in (revs.data.get("revisions") or []) if isinstance(r, Mapping)]
    index = next((i for i, r in enumerate(rows) if r.get("law_revision_id") == revision_id), None)
    if index is None:
        return {"status": "error", "error": "revision not listed by e-Gov", "law_id": law_id, "revision_id": revision_id}
    row = rows[index]
    amend_id = row.get("amendment_law_id")
    previous = next((r.get("law_revision_id") for r in rows[index + 1:]), None)
    stages = []
    for r in rows:
        if r.get("amendment_law_id") != amend_id:
            continue
        comment = r.get("amendment_enforcement_comment")
        fixed = r.get("amendment_enforcement_date") if not comment else None
        rid = str(r.get("law_revision_id") or "")
        outer = re.search(r"_(\d{4})(\d{2})(\d{2})_", rid)
        stages.append({
            "revision_id": rid,
            "effective_date": fixed,
            "delegated": bool(comment),
            "enforcement_comment": comment,
            # e-Gov dates a revision whose date is left to a Cabinet Order with the latest
            # date the Act allows: an outer limit, never a fixed date.
            "outer_limit": f"{outer.group(1)}-{outer.group(2)}-{outer.group(3)}" if comment and outer else None,
            "status": r.get("current_revision_status"),
        })
    in_force = [s for s in stages if s["effective_date"] and s["effective_date"] <= today.isoformat()]
    pending = [s for s in stages if s not in in_force]
    form = "in_force_instrument" if in_force else "promulgated_instrument"
    stage = ("in_force" if in_force and not pending else "partially_in_force" if in_force else "promulgated")
    out: dict[str, Any] = {
        "status": "ok",
        "law_id": law_id,
        "law_title": row.get("law_title"),
        "law_num": (revs.data.get("law_info") or {}).get("law_num"),
        "revision_id": revision_id,
        "previous_revision_id": previous,
        "amendment_law_id": amend_id,
        "amendment_law_num": row.get("amendment_law_num"),
        "amendment_law_title": row.get("amendment_law_title"),
        "promulgation_date": row.get("amendment_promulgate_date"),
        "effective_stages": stages,
        "instrument_form": form,
        "stage": stage,
        "revisions_source": {"url": revs.url, "sha256": revs.sha256, "fetched_at": revs.fetched_at},
    }
    new = client.law_data(revision_id)
    old = client.law_data(previous) if previous else None
    if new is None:
        out["text_status"] = "unavailable"
        return out
    new_prov = provisions(new.data.get("law_full_text") or {})
    out["law_data_source"] = {"url": new.url, "sha256": new.sha256, "fetched_at": new.fetched_at}
    suppl = suppl_for(new_prov, row.get("amendment_law_num"))
    out["supplementary_provisions"] = suppl
    if suppl:
        sents = sentences(suppl)
        out["effective_sentences"] = [s for s in sents if any(c in s for c in EFFECTIVE_CUES)][:MAX_SENTENCES]
        out["transitional_sentences"] = [s for s in sents if any(c in s for c in TRANSITIONAL_CUES)
                                         and s not in out["effective_sentences"]][:MAX_SENTENCES]
    if old is None:
        out["text_status"] = "new_law_or_previous_unavailable"
        out["diff"] = diff_provisions({}, new_prov) if not previous else None
    else:
        out["previous_law_data_source"] = {"url": old.url, "sha256": old.sha256, "fetched_at": old.fetched_at}
        out["diff"] = diff_provisions(provisions(old.data.get("law_full_text") or {}), new_prov)
        out["text_status"] = "ok"
    if out.get("diff"):
        delegations, modalities = tag_sentences(out["diff"]["added"] + out["diff"]["changed"])
        out["delegation_sentences"], out["modality_sentences"] = delegations, modalities
    return out


_DRAFT_QUOTE = re.compile(r"[「『]([^」』]{4,200})[」』]")
_INSTRUMENT_END = re.compile(r"(政令|省令|府令|命令|規則|告示|訓令)(?:[（(]案[）)]|案)$")


def draft_instrument_name(title: str) -> str | None:
    """'「X施行規則の一部を改正する省令（案）」に対する…' -> 'X施行規則の一部を改正する省令'.

    The name ends at the first instrument word immediately followed by 案, so a law
    title that itself contains に関する (南極地域の環境の保護に関する法律…) is kept whole.
    """
    t = unicodedata.normalize("NFKC", title or "")
    for cand in _DRAFT_QUOTE.findall(t):
        cand = cand.strip()
        if _INSTRUMENT_END.search(cand):
            return _INSTRUMENT_END.sub(lambda m: m.group(1), cand)
    match = re.search(r"^[「『\s]*(.*?(?:政令|省令|府令|命令|規則|告示|訓令))(?:[（(]案[）)]|案)", t)
    return match.group(1).strip() if match else None


def target_laws(name: str) -> list[str]:
    """Titles to look up, most specific first: the whole target, then the first of several
    laws joined by 及び / 並びに / 、 (a law title can itself contain 及び, so the whole
    title is always tried first and a split is only a fallback)."""
    match = re.match(r"^(.+?)(?:等)?の一部を改正する(?:政令|省令|内閣府令|府令|命令|規則|告示)$", name)
    if not match:
        return []
    target = match.group(1)
    out = [target]
    for sep in ("並びに", "及び", "、"):
        pieces = target.split(sep)
        for n in range(len(pieces) - 1, 0, -1):
            head = sep.join(pieces[:n])
            if head and head not in out:
                out.append(head)
    return out


def target_law(name: str) -> str | None:
    found = target_laws(name)
    return found[0] if found else None


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


_DEADLINE = re.compile(r"受付締切日時[：:]\s*(\d{4})/(\d{2})/(\d{2})")
_PUBLISHED = re.compile(r"案の公示日[：:]\s*(\d{4})/(\d{2})/(\d{2})")
_ARTICLE_REF = re.compile(r"第([0-9０-９一二三四五六七八九十百千]+)条(?:の([0-9０-９一二三四五六七八九十]+))?")


def article_refs(text: str) -> set[str]:
    out = set()
    for main, sub in _ARTICLE_REF.findall(text or ""):
        m = kanji_int(main)
        if m is None:
            continue
        s = kanji_int(sub) if sub else None
        out.add(f"第{m}条" + (f"の{s}" if s else ""))
    return out


_EGOV_FIELD = re.compile(r"(改正法令|改正法令番号|公布日)[:：]\s*([^;；]+)")


def history_matches(index: ex.OfficialIndex | None, name: str, after: str) -> dict[str, dict[str, Any]]:
    """Amending-instrument number -> [(raw id, e-Gov revision id)] for e-Gov update records whose
    amending instrument has exactly this title and was promulgated on or after ``after``.
    Consequential-amendment ordinances (…の整備に関する省令) name no target law, so only the
    repository's own e-Gov update history can tie a draft to them."""
    out: dict[str, list[tuple[str, str]]] = {}
    if index is None:
        return out
    want = _norm(name)
    for item_id, raw in index.raw_by_id.items():
        parts = lawid_parts(raw.get("source_url"))
        if not parts:
            continue
        fields = dict(_EGOV_FIELD.findall(str(raw.get("raw_summary") or "")))
        if _norm(fields.get("改正法令")) != want:
            continue
        prom = (fields.get("公布日") or "").strip()
        if not prom or prom < after:
            continue
        slot = out.setdefault((fields.get("改正法令番号") or "").strip(), {"promulgated": prom, "records": []})
        if (item_id, parts[1]) not in slot["records"]:
            slot["records"].append((item_id, parts[1]))
    for slot in out.values():
        slot["records"].sort()
    return out


def resolve_draft(client: EgovClient, raw: Mapping[str, Any], today: date,
                  documents_text: str = "", index: ex.OfficialIndex | None = None) -> dict[str, Any]:
    """Does e-Gov list a promulgated instrument with this draft's exact title, after the draft?"""
    title = str(raw.get("title_ja") or "")
    summary = str(raw.get("raw_summary") or "")
    name = draft_instrument_name(title)
    out: dict[str, Any] = {"raw_id": raw.get("id"), "title": title, "draft_name": name,
                           "source_url": raw.get("source_url")}
    pub = _PUBLISHED.search(summary)
    dl = _DEADLINE.search(summary)
    out["published"] = "-".join(pub.groups()) if pub else raw.get("published_at")
    out["deadline"] = "-".join(dl.groups()) if dl else None
    if not name:
        out["resolution"] = {"status": "unresolvable_by_title", "note": "no instrument name in the title"}
        return out
    after = out["published"] or "0000-00-00"
    hist = history_matches(index, name, after)
    if hist:
        if len(hist) > 1 and out["deadline"]:
            # Promulgated after the comment deadline: an instrument cannot be the result of a
            # consultation that had not closed yet.
            hist = {n: v for n, v in hist.items() if v["promulgated"] >= out["deadline"]} or hist
        numbers = sorted(hist)
        if len(numbers) == 1:
            evs = []
            for rid, revision in hist[numbers[0]]["records"][:MAX_CANDIDATES]:
                law_id = revision.split("_")[0]
                ev = amendment_evidence(client, law_id, revision, today)
                ev["raw_id"] = rid
                evs.append(ev)
            out["resolution"] = {"status": "resolved", "method": "repository_egov_history_exact_title",
                                 "candidates": evs, "candidate_count": 1}
            return out
    targets = target_laws(name)
    if not targets:
        out["resolution"] = {"status": "unresolvable_by_title",
                             "note": "not an 'X の一部を改正する…' amendment; e-Gov lookup by title is not attempted"}
        return out
    laws: list = []
    target = targets[0]
    for target in targets:
        found = client.search_title(target)
        if found is None:
            out["resolution"] = {"status": "error", "note": "e-Gov title search unavailable"}
            return out
        laws = [l for l in found.data.get("laws") or []
                if _norm((l.get("revision_info") or {}).get("law_title")) == _norm(target)]
        if laws:
            break
    if len(laws) != 1:
        out["resolution"] = {"status": "not_found_in_egov" if not laws else "ambiguous_target",
                             "note": f"{len(laws)} e-Gov laws titled {target}"}
        return out
    out["target_law_title"] = target
    law_id = (laws[0].get("law_info") or {}).get("law_id")
    revs = client.revisions(law_id)
    if revs is None:
        out["resolution"] = {"status": "error", "note": "law_revisions unavailable"}
        return out
    cands, seen = [], set()
    for r in revs.data.get("revisions") or []:
        if _norm(r.get("amendment_law_title")) != _norm(name):
            continue
        if not r.get("amendment_promulgate_date") or r["amendment_promulgate_date"] < after:
            continue
        if r.get("amendment_law_id") in seen:
            continue
        seen.add(r.get("amendment_law_id"))
        cands.append(r)
    out["target_law_id"] = law_id
    if not cands:
        out["resolution"] = {"status": "not_found_in_egov",
                             "note": f"no amendment titled {name} promulgated on or after {after} in e-Gov as of "
                                     f"{today.isoformat()}; this is not evidence that the draft was abandoned"}
        return out
    if out["deadline"]:
        after_deadline = [c for c in cands if c["amendment_promulgate_date"] >= out["deadline"]]
        if after_deadline:
            cands = after_deadline
    method = "unique_title_match" if len(cands) == 1 else None
    details = []
    for c in cands[:MAX_CANDIDATES]:
        ev = amendment_evidence(client, law_id, c["law_revision_id"], today)
        changed = {e["label"] for part in ("added", "changed", "removed")
                   for e in ((ev.get("diff") or {}).get(part) or [])}
        details.append((c, ev, changed))
    if len(cands) > 1 and documents_text:
        refs = article_refs(documents_text)
        scored = [(len(refs & changed), c, ev) for c, ev, changed in details]
        best = max((s for s, _, _ in scored), default=0)
        winners = [x for x in scored if x[0] == best]
        if best > 0 and len(winners) == 1:
            method = f"article_overlap({best})"
            details = [(winners[0][1], winners[0][2], set())]
    status = "resolved" if method else "ambiguous"
    out["resolution"] = {"status": status, "method": method,
                         "candidates": [ev for _, ev, _ in details],
                         "candidate_count": len(cands)}
    return out


def describe_page(data: bytes, mime: str, final_url: str) -> str:
    """One line saying what an official page actually served when no text was extracted.

    Facts only (type, size, final path, <title>, how many text blocks and attachment
    links the HTML holds), so a gap can be diagnosed from the committed pack without
    re-fetching from an environment the site blocks. Never page text beyond the title.
    """
    from bs4 import BeautifulSoup
    import official_source_evidence as ose

    path = urlsplit(final_url).path or "/"
    head = f"{(mime or '?').split(';')[0]} {len(data)} bytes final={path}"
    if data.startswith(b"%PDF"):
        return head + " pdf"
    soup = BeautifulSoup(data, "html.parser")
    title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").split())[:80]
    blocks = [t for t in soup.select("h1, h2, h3, h4, p, li, dt, dd, th, td")
              if len(" ".join(t.get_text(" ", strip=True).split())) >= 10]
    links = [urljoin(final_url, str(a.get("href") or "")) for a in soup.select("a[href]")]
    attachments = sum(ose.is_pdf_attachment_url(link) for link in links)
    scripts = len(soup.select("script"))
    return (f"{head} title={title!r} text_blocks={len(blocks)} links={len(links)} "
            f"attachment_links={attachments} scripts={scripts}")


ERROR_PAGE_TITLE = "ご迷惑をお掛けします"
# e-Gov serves one consultation under several views of the same record. The feed links
# the call-for-comments view (Mode=0); once results are published that view returns the
# e-Gov error page and the record (draft, its attachments, the results) is served under
# another mode. Same official host, same record id: an alternative official URL, not a
# way around an access restriction.
CONSULTATION_ALT_MODES = ("2", "1")


def consultation_alternatives(url: str) -> list[tuple[str, str]]:
    u = urlsplit(url)
    if (u.hostname or "").lower() != "public-comment.e-gov.go.jp":
        return []
    query = parse_qs(u.query, keep_blank_values=True)
    if "id" not in query or query.get("Mode") != ["0"]:
        return []
    return [(mode, re.sub(r"([?&])Mode=0(?=&|$)", rf"\g<1>Mode={mode}", url)) for mode in CONSULTATION_ALT_MODES]


def consultation_documents(url: str, title: str, *, gather: Callable[[str], list],
                           fetch: Callable[..., tuple[bytes, str, str]]) -> tuple[str, str, str]:
    """(text, status, url used) for a draft's official consultation page.

    The status says why documents are missing, so a gap is diagnosable from the
    committed pack. An alternative view is accepted only when its text can be tied
    to the draft's own title (``official_source_evidence.related_to_title``), so a
    different consultation or a menu never stands in for this one.
    """
    import explainer_sources as src
    import official_source_evidence as ose

    def attempt(target: str) -> tuple[str, str]:
        found = gather(target)
        text = " ".join(d.get("excerpt", "") for d in found)
        formats = ",".join(sorted({d.get("format", "?") for d in found}))
        if text and title and not ose.related_to_title(title, found):
            return "", "unrelated"
        return text, formats

    try:
        text, formats = attempt(url)
    except Exception as exc:  # noqa: BLE001 - optional narrowing only
        err = src.classify_exception(exc)
        print(f"documents unavailable for {url}: {err.error_class}")
        return "", f"error:{err.error_class}", url
    if text:
        return text, f"ok:{formats}", url
    try:
        data, mime, final_url = fetch(url, ose.MAX_HTML_BYTES)
        described = describe_page(data, mime, final_url)
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        return "", f"empty: describe failed ({src.classify_exception(exc).error_class})", url
    if ERROR_PAGE_TITLE not in described:
        return "", "empty: " + described, url
    tried = []
    for mode, alt in consultation_alternatives(url):
        try:
            text, formats = attempt(alt)
        except Exception as exc:  # noqa: BLE001
            tried.append(f"Mode={mode} {src.classify_exception(exc).error_class}")
            continue
        if text:
            return text, f"ok:{formats} via Mode={mode} (Mode=0 served the e-Gov error page)", alt
        tried.append(f"Mode={mode} {formats or 'empty'}")
    return "", "empty: Mode=0 served the e-Gov error page; " + ("; ".join(tried) or "no alternative view") + \
        f" [{described}]", url


# ------------------------------------------------------------------ pack


def pack_for(seed: str, index: ex.OfficialIndex, client: EgovClient, today: date, *,
             queue_entry: Mapping[str, Any] | None = None,
             documents: Callable[[str], str] | None = None,
             previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    closure = index.closure([seed]) or [seed]
    instruments, drafts, notes = [], [], []
    for item_id in closure:
        raw = index.raw_by_id.get(item_id) or {}
        url = raw.get("source_url")
        parts = lawid_parts(url)
        if parts:
            ev = amendment_evidence(client, parts[0], parts[1], today)
            ev["raw_id"] = item_id
            instruments.append(ev)
        elif "public-comment.e-gov.go.jp" in str(url):
            docs, doc_status, docs_url = "", "not_requested", url
            if documents:
                got = documents(url)
                if isinstance(got, tuple):
                    docs, doc_status = got[0], got[1]
                    docs_url = got[2] if len(got) > 2 else url
                else:
                    docs, doc_status, docs_url = got, ("ok" if got else "empty"), url
            res = resolve_draft(client, raw, today, docs, index)
            res["documents_status"] = doc_status
            if docs:
                res["document_excerpt"] = docs[:2500]
                if docs_url != url:
                    res["documents_url"] = docs_url
            drafts.append(res)
    resolved_from_drafts = [ev for d in drafts if (d.get("resolution") or {}).get("status") == "resolved"
                            for ev in d["resolution"]["candidates"]]
    known = {i.get("revision_id") for i in instruments}
    effective = instruments + [ev for ev in resolved_from_drafts if ev.get("revision_id") not in known]
    # A resolved draft's candidates now live in ``instruments``: keep only references here.
    for d in drafts:
        res = d.get("resolution") or {}
        if res.get("status") == "resolved":
            res["candidates"] = [{"revision_id": c.get("revision_id"), "amendment_law_num": c.get("amendment_law_num"),
                                  "law_title": c.get("law_title")} for c in res.get("candidates") or []]
    ok = [i for i in effective if i.get("status") == "ok"]
    ambiguous = any((d.get("resolution") or {}).get("status") == "ambiguous" for d in drafts)
    if ok and all(i.get("text_status") == "ok" for i in ok):
        status = "ready"
    elif ok:
        status = "partial"
    elif ambiguous:
        status = "ambiguous"
    elif drafts and all((d.get("resolution") or {}).get("status") in ("not_found_in_egov", "unresolvable_by_title")
                        for d in drafts):
        status = "draft_only"
    else:
        status = "unresolved"
    pack = {
        "schema_version": SCHEMA_VERSION,
        "seed_item_id": seed,
        "queue_id": (queue_entry or {}).get("queue_id"),
        "title_ja": (queue_entry or {}).get("title_ja") or (index.raw_by_id.get(seed) or {}).get("title_ja"),
        "related_item_ids": closure,
        "source_fingerprint": index.fingerprint([seed]),
        "status": status,
        "instruments": effective,
        "drafts": drafts,
        "fetch_errors": sorted(set(client.errors))[:20],
        "notice": ("Machine-extracted from official e-Gov data. Dates, numbers and the list of changed provisions are "
                   "record data; what a provision means for any party is NOT interpreted here. Absence from e-Gov is "
                   "not evidence that a step did not happen."),
    }
    pack["facts"] = machine_facts(pack, today)
    pack["case_file_template"] = case_file_template(pack, today)
    return stabilise(fit(pack), previous)


MAX_PACK_BYTES = 300_000


def fit(pack: dict[str, Any]) -> dict[str, Any]:
    """Keep a pack under MAX_PACK_BYTES by shortening quoted provision texts, never dropping
    record data (dates, numbers, labels of changed provisions)."""
    for limit in (900, 500, 250):
        if len(json.dumps(pack, ensure_ascii=False).encode()) <= MAX_PACK_BYTES:
            break
        for inst in pack.get("instruments") or []:
            for part in ("added", "changed", "removed"):
                for item in ((inst.get("diff") or {}).get(part) or []):
                    for key in ("old", "new"):
                        if isinstance(item.get(key), str) and len(item[key]) > limit:
                            item[key] = item[key][:limit] + "…[truncated; see the e-Gov source]"
        pack["truncated_texts"] = limit
    return pack


def machine_facts(pack: Mapping[str, Any], today: date) -> dict[str, dict[str, Any]]:
    """Deterministic categories from record data; interpretive ones are left to the writer."""
    ok = [i for i in pack.get("instruments") or [] if i.get("status") == "ok"]
    facts: dict[str, dict[str, Any]] = {}
    if not ok:
        draft = next(iter(pack.get("drafts") or []), None)
        if draft and (draft.get("resolution") or {}).get("status") in ("not_found_in_egov",):
            facts["instrument_form"] = {"status": "machine_confirmed", "value": "draft_instrument",
                                        "note": "e-Gov lists no promulgated instrument with this title yet; treat as a draft"}
        return facts
    primary = ok[0]
    forms = {i["instrument_form"] for i in ok}
    facts["instrument_form"] = {"status": "machine_confirmed",
                                "value": "in_force_instrument" if "in_force_instrument" in forms else "promulgated_instrument",
                                "stage": primary["stage"], "evidence": [primary["revision_id"]]}
    proms = sorted({i["promulgation_date"] for i in ok if i.get("promulgation_date")})
    facts["promulgation_date"] = {"status": "machine_confirmed" if len(proms) == 1 else "requires_ai",
                                  "value": proms[0] if len(proms) == 1 else proms,
                                  "evidence": [i["revision_id"] for i in ok]}
    stages = [s for i in ok for s in i.get("effective_stages") or []]
    fixed = sorted({s["effective_date"] for s in stages if s.get("effective_date")})
    delegated = [s for s in stages if s.get("delegated")]
    facts["effective_date"] = {
        "status": "machine_confirmed",
        "value": fixed[0] if len(fixed) == 1 and not delegated else fixed,
        "delegated": [{"enforcement_comment": s["enforcement_comment"], "outer_limit": s["outer_limit"]} for s in delegated],
        "evidence": sorted({s["revision_id"] for s in stages}),
    }
    facts["partial_effective_date"] = {"status": "machine_confirmed",
                                       "value": fixed if len(fixed) + len(delegated) > 1 else None,
                                       "note": "several commencement stages" if len(fixed) + len(delegated) > 1
                                       else "single commencement stage"}
    labels = sorted({e["label"] for i in ok for part in ("added", "changed", "removed")
                     for e in ((i.get("diff") or {}).get(part) or []) if not e.get("citation_only")})
    if all(i.get("text_status") == "ok" for i in ok):
        facts["amended_provisions"] = {"status": "machine_confirmed", "value": labels,
                                       "evidence": [i["revision_id"] for i in ok]}
        facts["old_vs_new"] = {"status": "requires_ai",
                               "note": "diff below is exact; describe its legal effect from the quoted texts"}
    trans = [s for i in ok for s in i.get("transitional_sentences") or []]
    suppl_known = all(i.get("supplementary_provisions") for i in ok)
    facts["transitional_measures"] = {
        "status": "requires_ai" if trans else ("machine_confirmed" if suppl_known else "requires_ai"),
        "value": trans or (None if not suppl_known else []),
        "note": ("transitional sentences found in the amending instrument's supplementary provisions" if trans else
                 "the amending instrument's supplementary provisions contain no transitional sentence" if suppl_known
                 else "supplementary provisions not located"),
    }
    dels = [d for i in ok for d in i.get("delegation_sentences") or []]
    facts["delegation"] = {
        "status": "requires_ai", "value": dels,
        "note": ("'implements' = the changed provision is itself the rule a higher instrument delegated; "
                 "'further' = it leaves a matter to yet another instrument"),
    }
    for category in ("scope", "obligation_type", "policy_background", "business_impact"):
        facts[category] = {"status": "requires_ai"}
    facts["obligation_type"]["hints"] = [m for i in ok for m in i.get("modality_sentences") or []][:MAX_SENTENCES]
    return facts


def case_file_template(pack: Mapping[str, Any], today: date) -> dict[str, Any]:
    """A valid case file a writer can copy: record-data facts confirmed, interpretation left open."""
    sources, by_rev = [], {}
    for inst in pack.get("instruments") or []:
        if inst.get("status") != "ok":
            continue
        for key in ("revisions_source", "law_data_source", "previous_law_data_source"):
            src = inst.get(key)
            if not src or any(s["url"] == src["url"] for s in sources):
                continue
            sid = f"s{len(sources) + 1}"
            sources.append({"id": sid, "url": src["url"], "kind": "egov_law", "status": "fetched",
                            "fetched_at": src["fetched_at"], "sha256": src["sha256"], "error_class": None,
                            "attempts": 1, "evidence_pack": pack["seed_item_id"]})
            by_rev.setdefault(inst["revision_id"], []).append(sid)
    facts = []
    machine = pack.get("facts") or {}
    for n, category in enumerate(es.FACT_CATEGORIES, start=1):
        m = machine.get(category) or {}
        ids = sorted({sid for rev in (m.get("evidence") or []) for sid in by_rev.get(rev, [])}) or \
            [s["id"] for s in sources[:1]]
        fact: dict[str, Any] = {"id": f"f{n}", "category": category, "status": "unconfirmed",
                                "statement": "To be confirmed by the writer from the quoted official text.",
                                "source_ids": []}
        if m.get("status") == "machine_confirmed" and sources and category in (
                "instrument_form", "promulgation_date", "effective_date", "partial_effective_date",
                "amended_provisions", "transitional_measures"):
            value = m.get("value")
            fact.update({"status": "confirmed" if value not in (None, []) or category in
                         ("partial_effective_date", "transitional_measures") else "not_applicable",
                         "source_ids": ids, "evidence_ref": f"{pack['seed_item_id']}#{category}",
                         "statement": machine_statement(category, m)})
            if category == "instrument_form":
                fact["value"] = value
            if category in ("partial_effective_date", "transitional_measures") and value in (None, []):
                fact["status"] = "not_applicable"
        facts.append(fact)
    return {
        "schema_version": es.SCHEMA_VERSION,
        "dossier_id": "REPLACE-WITH-DOSSIER-ID",
        "kind": "new",
        "queue_id": pack.get("queue_id"),
        "seed_item_id": pack["seed_item_id"],
        "hold": None,
        "next_action": "Interpret scope, obligation type, old vs new and delegation from the evidence pack quotes; "
                       "then write the English explainer.",
        "sources": sources,
        "facts": facts,
        "unresolved": [],
    }


def machine_statement(category: str, fact: Mapping[str, Any]) -> str:
    value = fact.get("value")
    if category == "instrument_form":
        return f"e-Gov records the amendment as {fact.get('stage')} ({value}) as of the evidence date."
    if category == "promulgation_date":
        return f"Promulgated {value} (e-Gov revision record)."
    if category == "effective_date":
        dele = fact.get("delegated") or []
        text = f"Effective {value}" if value else "No fixed effective date"
        if dele:
            text += "; a stage is left to a Cabinet Order (outer limit " + ", ".join(
                str(d.get("outer_limit")) for d in dele) + ", not a fixed date)"
        return text + " (e-Gov revision record)."
    if category == "partial_effective_date":
        return "Several commencement stages: " + ", ".join(value) if value else "Single commencement stage."
    if category == "amended_provisions":
        return "Provisions changed (e-Gov old/new text): " + ", ".join(value)[:900]
    if category == "transitional_measures":
        return ("Transitional sentences in the supplementary provisions: " + " / ".join(value)[:900]) if value else \
            "The amending instrument's supplementary provisions contain no transitional provision."
    return str(value)[:900]


_VOLATILE = ("fetched_at",)


def stabilise(pack: dict[str, Any], previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep the earlier fetch time for byte-identical responses, so an unchanged pack is unchanged."""
    if not previous:
        return pack
    old_times: dict[str, str] = {}

    def collect(node: Any) -> None:
        if isinstance(node, Mapping):
            if "sha256" in node and "fetched_at" in node:
                old_times.setdefault(node["sha256"], node["fetched_at"])
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for v in node:
                collect(v)

    def apply(node: Any) -> None:
        if isinstance(node, dict):
            if "sha256" in node and "fetched_at" in node and node["sha256"] in old_times:
                node["fetched_at"] = old_times[node["sha256"]]
            for v in node.values():
                apply(v)
        elif isinstance(node, list):
            for v in node:
                apply(v)

    collect(previous)
    apply(pack)
    return pack


def evidence_digest(pack: Mapping[str, Any]) -> str:
    """What a hold depends on: response hashes and resolution outcomes, not fetch times."""
    keys: list[str] = []

    def collect(node: Any) -> None:
        if isinstance(node, Mapping):
            if "sha256" in node:
                keys.append(str(node["sha256"]))
            if "status" in node and isinstance(node.get("status"), str) and "method" in node:
                keys.append(f"{node['status']}:{node.get('method')}")
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for v in node:
                collect(v)

    collect({"i": pack.get("instruments"), "d": pack.get("drafts"), "s": pack.get("status")})
    keys.append(str(pack.get("status")))
    return "sha256:" + hashlib.sha256("|".join(sorted(keys)).encode()).hexdigest()[:32]


def load_pack(seed: str, directory: Path = EVIDENCE_DIR) -> dict[str, Any] | None:
    path = directory / f"{seed}.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


# ------------------------------------------------------------------ CLI


def wanted_seeds(queue: Mapping[str, Any], ledger: Mapping[str, Any] | None, top: int, include_ledger: bool) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    seen = set()
    if include_ledger and ledger:
        for entry in (ledger.get("dossiers") or {}).values():
            if entry.get("stage") in ("MERGED", "PAGES_DEPLOYED", "LIVE_VERIFIED"):
                continue
            seed = entry.get("seed_item_id")
            if seed and seed not in seen:
                seen.add(seed)
                out.append((seed, {"queue_id": entry.get("queue_id")}))
    for entry in (queue.get("entries") or []):
        if entry.get("kind") != "new" or len([s for s in out if s[1].get("from_queue")]) >= top:
            continue
        seed = entry.get("suggested_seed_item_id") or (entry.get("item_ids") or [None])[0]
        if seed and seed not in seen:
            seen.add(seed)
            out.append((seed, {**entry, "from_queue": True}))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=EVIDENCE_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--seeds", default="")
    b.add_argument("--from-queue", type=int, default=0)
    b.add_argument("--include-ledger", action="store_true")
    b.add_argument("--max-api-calls", type=int, default=DEFAULT_MAX_API_CALLS)
    b.add_argument("--with-documents", action="store_true",
                   help="also read the public-comment page and its PDFs (official_source_evidence) to narrow drafts")
    b.add_argument("--prune", action="store_true", help="delete packs no longer wanted")
    s = sub.add_parser("show")
    s.add_argument("seed")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "show":
        pack = load_pack(args.seed, args.dir)
        if pack is None:
            raise SystemExit(f"no pack for {args.seed}")
        print(json.dumps({k: pack[k] for k in ("seed_item_id", "status", "facts")}, ensure_ascii=False, indent=2))
        return 0
    today = datetime.now(JST).date()
    index = ex.OfficialIndex.build(ex.load_json(ex.RAW_PATH))
    queue = ex.load_json(ex.QUEUE_PATH) if ex.QUEUE_PATH.is_file() else {}
    ledger_path = ex.REPO_ROOT / "data" / "explainer_pipeline.json"
    ledger = ex.load_json(ledger_path) if ledger_path.is_file() else None
    targets = [(s.strip(), {}) for s in args.seeds.split(",") if s.strip()]
    if args.from_queue or args.include_ledger:
        targets += [t for t in wanted_seeds(queue, ledger, args.from_queue, args.include_ledger)
                    if t[0] not in {x[0] for x in targets}]
    documents = None
    if args.with_documents:
        import official_source_evidence as ose

        titles = {str(r.get("source_url")): str(r.get("title_ja") or "") for r in index.raw_by_id.values()}

        def documents(url: str) -> tuple[str, str, str]:
            return consultation_documents(url, titles.get(url, ""), gather=ose.gather, fetch=ose.fetch_bounded)
    client = EgovClient(fetch=http_fetch, max_calls=args.max_api_calls)
    counts: dict[str, int] = {}
    written, kept = 0, set()
    for seed, entry in targets:
        if seed not in index.raw_by_id:
            print(f"{seed}: not in raw records; skipped")
            continue
        try:
            pack = pack_for(seed, index, client, today, queue_entry=entry, documents=documents,
                            previous=load_pack(seed, args.dir))
        except FetchBudgetExceeded:
            print(f"API budget of {args.max_api_calls} calls reached; remaining seeds wait for the next run")
            break
        kept.add(seed)
        counts[pack["status"]] = counts.get(pack["status"], 0) + 1
        written += ex.write_if_changed(args.dir / f"{seed}.json", ex.dump(pack))
        client.errors.clear()
        print(f"{seed}: {pack['status']} instruments={len(pack['instruments'])} drafts={len(pack['drafts'])}")
    if args.prune and args.dir.is_dir():
        for path in args.dir.glob("raw-*.json"):
            if path.stem not in kept and path.stem not in {t[0] for t in targets}:
                path.unlink()
    print("evidence " + json.dumps({"packs": len(kept), "changed": written, "api_calls": client.calls, **counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ------------------------------------------------------------------ checks against a pack


def pack_for_dossier(explainer: Mapping[str, Any] | None, state: Mapping[str, Any] | None,
                     directory: Path = EVIDENCE_DIR) -> dict[str, Any] | None:
    """The pack of the dossier's seed (case file first), else of any related record."""
    seeds = [(state or {}).get("seed_item_id")] + list((explainer or {}).get("related_item_ids") or [])
    for seed in seeds:
        if isinstance(seed, str):
            pack = load_pack(seed, directory)
            if pack is not None:
                return pack
    return None


def _amendments(pack: Mapping[str, Any]) -> dict[Any, list[Mapping[str, Any]]]:
    out: dict[Any, list[Mapping[str, Any]]] = {}
    for inst in pack.get("instruments") or []:
        if inst.get("status") == "ok":
            out.setdefault(law_number_key(inst.get("amendment_law_num")), []).append(inst)
    return out


def explainer_conflicts(explainer: Mapping[str, Any], pack: Mapping[str, Any]) -> list[str]:
    """Record-data contradictions between an explainer and its evidence pack (deterministic).

    Only instruments the pack actually resolved are checked, and only facts e-Gov
    records: promulgation date, fixed effective dates, a Cabinet-Order outer limit
    used as a fixed date, and a stage the revision list contradicts.
    """
    errors: list[str] = []
    by_number = _amendments(pack)
    if not by_number:
        return errors
    listed = [i.get("number") for i in explainer.get("instruments") or [] if isinstance(i, Mapping)]
    default = listed[0] if len(listed) == 1 else None
    for n, row in enumerate(explainer.get("key_dates") or []):
        if not isinstance(row, Mapping):
            continue
        kind, when = row.get("kind"), row.get("date")
        number = row.get("instrument_number") or default
        insts = by_number.get(law_number_key(number)) if number else None
        if not insts or kind not in ("promulgated", "effective", "partially_effective"):
            continue
        proms = {i.get("promulgation_date") for i in insts}
        stages = [s for i in insts for s in i.get("effective_stages") or []]
        fixed = {s.get("effective_date") for s in stages if s.get("effective_date")}
        outer = {s.get("outer_limit") for s in stages if s.get("delegated")}
        if kind == "promulgated" and when not in proms:
            errors.append(f"key_dates[{n}]: promulgation {when} for {number} differs from e-Gov ({', '.join(sorted(proms))})")
        if kind in ("effective", "partially_effective"):
            if when in outer and when not in fixed:
                errors.append(f"key_dates[{n}]: {when} for {number} is the Cabinet-Order outer limit e-Gov records, "
                              "not a fixed effective date")
            elif fixed and when not in fixed:
                errors.append(f"key_dates[{n}]: effective {when} for {number} is not an effective date e-Gov records "
                              f"({', '.join(sorted(fixed))})")
    numbers = {law_number_key(x) for x in listed if x}
    covered = [insts for key, insts in by_number.items() if key in numbers]
    stage = explainer.get("current_stage")
    as_of = str(explainer.get("verified_at") or "")
    if len(covered) == 1 and stage in ("promulgated", "partially_in_force", "in_force") and as_of:
        # Judged at the explainer's own verification date: an effective date passing
        # later is a re-check (stage_overtaken), never a reason to withhold it.
        recorded = stage_on(covered[0], as_of)
        if recorded != stage:
            errors.append(f"current_stage {stage!r} contradicts e-Gov's revision list as of verified_at {as_of} "
                          f"({recorded})")
    return errors


def stage_on(insts: Sequence[Mapping[str, Any]], when: str) -> str:
    """in_force / partially_in_force / promulgated for one amendment's stages on a given date."""
    stages = [s for i in insts for s in i.get("effective_stages") or []]
    done = [s for s in stages if s.get("effective_date") and s["effective_date"] <= when]
    if not done:
        return "promulgated"
    return "in_force" if len(done) == len(stages) else "partially_in_force"


def case_file_conflicts(state: Mapping[str, Any], pack: Mapping[str, Any]) -> list[str]:
    """A case-file fact that cites the pack must agree with the pack's record data."""
    errors: list[str] = []
    facts = pack.get("facts") or {}
    for fact in state.get("facts") or []:
        if not isinstance(fact, Mapping) or not fact.get("evidence_ref") or fact.get("status") != "confirmed":
            continue
        category = fact.get("category")
        machine = facts.get(category) or {}
        if machine.get("status") != "machine_confirmed":
            continue
        value = machine.get("value")
        if category == "instrument_form" and fact.get("value") != value:
            errors.append(f"{fact.get('id')}: instrument_form {fact.get('value')!r} differs from the evidence pack ({value!r})")
        if category in ("promulgation_date", "effective_date", "partial_effective_date"):
            wanted = value if isinstance(value, list) else [value] if value else []
            missing = [v for v in wanted if v and v not in str(fact.get("statement"))]
            if missing:
                errors.append(f"{fact.get('id')}: {category} statement omits the e-Gov date(s) {', '.join(missing)}")
    return errors
