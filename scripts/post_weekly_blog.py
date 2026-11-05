#!/usr/bin/env python3
"""Post the weekly roundup to the Legal GPT WordPress blog (English + Japanese).

Run every Monday by ``.github/workflows/weekly-blog-post.yml``. It takes the
most recent completed JST week from ``weekly_roundups.build_weeks()`` (the same
week the site's ``docs/weekly/<slug>/`` page shows) and creates two posts
through the WordPress REST API (``/wp-json/wp/v2/posts``):

* English: an excerpt of the roundup -- a few items per area with a one-line
  AI summary, the consultations open at week end, and a link to the full
  roundup page on updates.legal-gpt.com (copying every item would duplicate the
  site's page and send nobody to it);
* Japanese: the same week from the Japanese side -- the original Japanese title
  (``title_ja``) and the first sentence of the Japanese AI summary generated
  directly from the Japanese source metadata (``summary_ja``; never a
  translation of the English), only for items that have one.

No AI calls and no repository writes. Posts are created as ``draft`` unless
``WP_POST_STATUS`` says otherwise, so a person reads them before they go live.
Each post has a fixed slug per week and language; an existing post with that
slug (in any status) is never touched, so a rerun never duplicates or
overwrites an edited post.

Settings (GitHub): secrets ``WP_USERNAME`` and ``WP_APP_PASSWORD`` (a
WordPress application password), variables ``WP_BASE_URL`` (https only),
optional ``WP_POST_STATUS`` (draft | pending | publish), ``WP_CATEGORY_IDS_EN``
/ ``WP_CATEGORY_IDS_JA`` and ``WP_TAG_IDS_EN`` / ``WP_TAG_IDS_JA`` (comma-
separated numeric ids). Until the credentials are set the run warns and exits
0; a WordPress error fails the run. The Actions log is public, so nothing
secret, no response body and no account detail is printed.

Every record field is untrusted: text is HTML-escaped and only http(s) source
URLs are emitted as links.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import page_l10n as pl  # noqa: E402
import weekly_roundups as wr  # noqa: E402
from alert_common import (  # noqa: E402
    FEEDS_URL,
    JST,
    SITE_URL,
    clean_text,
    comment_deadline_display,
    safe_http_url,
    source_display_name,
    strict_iso_date,
)
import build_public_pages as bpp  # noqa: E402

DATA_PATH = SCRIPTS_DIR.parent / "docs" / "data" / "legal_updates.json"
LANGUAGES = ("en", "ja")
BLOG_PER_AREA = 2
BLOG_OTHER_PER_AREA = 1
BLOG_MAX_ITEMS = 10
BLOG_CONSULTATIONS = 5
JA_LINE_MAX_CHARS = 120
ALLOWED_STATUSES = ("draft", "pending", "publish")
REQUEST_TIMEOUT = 30
DISCLAIMER_EN_URL = SITE_URL + "legal/disclaimer_en.html"
DISCLAIMER_JA_URL = SITE_URL + "legal/disclaimer_ja.html"
JA_SENTENCE_END = re.compile(r"(?<=。)")
# The point of the posts is to bring readers to the dashboard: every post opens
# and closes with a prominent link to it (tested).
CTA_EN = (
    "<p><strong>Japan Legal Reform Watch</strong> is a free English dashboard of Japanese legal and regulatory "
    "updates, checked daily against official sources: {link}</p>"
)
CTA_JA = (
    "<p><strong>Japan Legal Reform Watch</strong>は、日本の法改正・規制・パブリックコメントの動向を公式情報から"
    "毎日収集する無料のダッシュボードです（英語・日本語・中国語）：{link}</p>"
)


class ConfigError(ValueError):
    """A setting is present but unusable (fails the run)."""


class WordPressError(RuntimeError):
    """The WordPress API refused or failed a request (message carries no body)."""


@dataclass(frozen=True)
class Config:
    base_url: str
    username: str
    password: str
    status: str
    categories: Mapping[str, tuple[int, ...]]
    tags: Mapping[str, tuple[int, ...]]


@dataclass(frozen=True)
class Post:
    language: str
    slug: str
    title: str
    excerpt: str
    content: str
    item_count: int


# ----------------------------------------------------------------- settings


def parse_ids(value: str | None, name: str) -> tuple[int, ...]:
    text = (value or "").strip()
    if not text:
        return ()
    ids = []
    for part in text.split(","):
        part = part.strip()
        if not part.isdigit() or int(part) <= 0:
            raise ConfigError(f"{name} must be comma-separated positive numeric ids")
        ids.append(int(part))
    return tuple(ids)


def load_config(env: Mapping[str, str]) -> Config | None:
    """The WordPress settings, or None while the credentials are not configured."""
    base_url = (env.get("WP_BASE_URL") or "").strip().rstrip("/")
    username = (env.get("WP_USERNAME") or "").strip()
    password = (env.get("WP_APP_PASSWORD") or "").strip()
    if not (base_url and username and password):
        return None
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~/-]*)?", base_url):
        raise ConfigError("WP_BASE_URL must be an https:// site URL")
    status = (env.get("WP_POST_STATUS") or "draft").strip()
    if status not in ALLOWED_STATUSES:
        raise ConfigError(f"WP_POST_STATUS must be one of {', '.join(ALLOWED_STATUSES)}")
    categories = {lang: parse_ids(env.get(f"WP_CATEGORY_IDS_{lang.upper()}"), f"WP_CATEGORY_IDS_{lang.upper()}")
                  for lang in LANGUAGES}
    tags = {lang: parse_ids(env.get(f"WP_TAG_IDS_{lang.upper()}"), f"WP_TAG_IDS_{lang.upper()}")
            for lang in LANGUAGES}
    return Config(base_url, username, password, status, categories, tags)


# ----------------------------------------------------------------- content


def post_slug(week: wr.Week, language: str) -> str:
    return f"japan-legal-roundup-{week.slug}-{language}"


def has_japanese_summary(item: Mapping[str, Any]) -> bool:
    return (
        item.get("summary_ja_source") == "claude"
        and bool(clean_text(item.get("summary_ja")))
        and bool(clean_text(item.get("title_ja")))
    )


def japanese_ranked(items: Sequence[Mapping[str, Any]], week: wr.Week) -> list[Mapping[str, Any]]:
    """The week's items with a Japanese AI summary, in the roundup's ranking order."""
    rows = []
    for index, item in enumerate(items):
        published = strict_iso_date(item.get("published_at"))
        if published and week.monday <= published <= week.sunday and has_japanese_summary(item):
            rows.append((index, item, published))
    rows.sort(key=lambda row: (-wr._relevance(row[1]), -row[2].toordinal(), row[0]))
    return [item for _, item, _ in rows]


def japanese_one_line(item: Mapping[str, Any]) -> str:
    text = clean_text(item.get("summary_ja"))
    first = next((part for part in JA_SENTENCE_END.split(text) if part.strip()), "")
    if len(first) <= JA_LINE_MAX_CHARS:
        return first
    return first[: JA_LINE_MAX_CHARS - 1] + "…"


def blog_selection(ranked: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return wr.select(ranked, per_area=BLOG_PER_AREA, other_per_area=BLOG_OTHER_PER_AREA, page_max=BLOG_MAX_ITEMS)


def grouped(items: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        groups.setdefault(wr.area_of(item), []).append(item)
    return groups


def item_url(item: Mapping[str, Any], language: str) -> str:
    path = bpp.item_page_path(item)
    if not path:
        return ""
    return SITE_URL + path + ("?lang=ja" if language == "ja" else "")


def link(url: str, text: str, *, external: bool = False) -> str:
    rel = ' target="_blank" rel="noopener noreferrer"' if external else ""
    return f'<a href="{escape(url)}"{rel}>{escape(text)}</a>'


def ja_date(day: date) -> str:
    return f"{day.year}年{day.month}月{day.day}日"


def ja_range(week: wr.Week) -> str:
    end = week.sunday
    tail = f"{end.month}月{end.day}日" if end.year == week.monday.year else ja_date(end)
    return f"{ja_date(week.monday)}〜{tail}"


def render_item_en(item: Mapping[str, Any]) -> str:
    title = clean_text(item.get("title_en"))
    url = item_url(item, "en")
    meta = " · ".join(escape(part) for part in (
        clean_text(item.get("published_at")),
        source_display_name(item.get("source_name")),
        clean_text(item.get("stage")),
    ) if part)
    source = safe_http_url(item.get("source_url"))
    source_link = f" · {link(source, 'Original Japanese source', external=True)}" if source else ""
    heading = link(url, title) if url else escape(title)
    return (
        f"<li><strong>{heading}</strong><br />{escape(wr.one_line(item))} <em>(AI summary)</em>"
        f"<br /><small>{meta}{source_link}</small></li>"
    )


def render_item_ja(item: Mapping[str, Any]) -> str:
    title = clean_text(item.get("title_ja"))
    url = item_url(item, "ja")
    source_name = clean_text(item.get("source_name"))
    meta = " · ".join(escape(part) for part in (
        clean_text(item.get("published_at")),
        pl.source_label(source_name, source_display_name(source_name), "ja"),
        pl.stage_label(clean_text(item.get("stage")), "ja"),
    ) if part)
    source = safe_http_url(item.get("source_url"))
    source_link = f" · {link(source, '公式原典（日本語）', external=True)}" if source else ""
    heading = link(url, title) if url else escape(title)
    return (
        f"<li><strong>{heading}</strong><br />{escape(japanese_one_line(item))}<em>（AI要約）</em>"
        f"<br /><small>{meta}{source_link}</small></li>"
    )


def render_consultation(item: Mapping[str, Any], language: str) -> str:
    if language == "ja":
        title = clean_text(item.get("title_ja")) or clean_text(item.get("title_en"))
        due = pl.deadline_display(comment_deadline_display(item.get("comment_deadline")), "ja")
        due_text = f"意見提出期限：{due}"
        source_text = "公式原典（日本語）"
    else:
        title = clean_text(item.get("title_en"))
        due_text = f"Comments due: {comment_deadline_display(item.get('comment_deadline'))}"
        source_text = "Original Japanese source"
    url = item_url(item, language)
    source = safe_http_url(item.get("source_url"))
    heading = link(url, title) if url else escape(title)
    source_link = f" · {link(source, source_text, external=True)}" if source else ""
    return f"<li>{heading}<br /><small>{escape(due_text)}{source_link}</small></li>"


def render_english(week: wr.Week) -> Post:
    selected = blog_selection(week.items)
    page_url = SITE_URL + week.path
    parts = [
        f"<p>Selected Japanese legal and regulatory developments officially published "
        f"{escape(wr.date_range_label(week.monday))} (JST), drawn from Japan Legal Reform Watch, which monitors "
        "official government, regulator, Diet, court and self-regulatory sources daily and summarizes them in "
        "English for triage. Items are grouped by area and ranked by a keyword relevance heuristic, not by "
        "legal importance.</p>",
        CTA_EN.format(link=link(SITE_URL, "updates.legal-gpt.com")),
        f"<p><strong>{link(page_url, f'Read the full roundup ({len(week.items):,} updates with AI summaries)')}"
        "</strong></p>",
    ]
    for area, rows in grouped(selected).items():
        parts.append(f"<h2>{escape(area)}</h2>")
        parts.append("<ul>" + "".join(render_item_en(item) for item in rows) + "</ul>")
    consultations = week.consultations[:BLOG_CONSULTATIONS]
    if consultations:
        parts.append("<h2>Consultations open at week end</h2>")
        parts.append(
            f"<p>Public comment procedures whose structured deadline fell after {week.sunday.isoformat()}, "
            "soonest first. Confirm the deadline and submission method in the official source.</p>"
        )
        parts.append("<ul>" + "".join(render_consultation(item, "en") for item in consultations) + "</ul>")
    parts += [
        "<h2>Follow Japanese legal updates in English</h2>",
        "<ul>"
        f"<li>{link(SITE_URL, 'Japan Legal Reform Watch dashboard (search and filter every update)')}</li>"
        f"<li>{link(FEEDS_URL + wr.FEED_FILE_NAME, 'Weekly roundup RSS feed')}</li>"
        f"<li>{link(FEEDS_URL + 'all.ics', 'Public comment deadline calendar (.ics)')}</li>"
        "</ul>",
        CTA_EN.format(link=link(SITE_URL, "Open Japan Legal Reform Watch")),
        "<p><small>English titles and AI summaries are unofficial monitoring aids, not legal advice or "
        "official translations, and may contain errors. The original Japanese official sources remain "
        f"authoritative. {link(DISCLAIMER_EN_URL, 'Disclaimer')}</small></p>",
    ]
    return Post(
        language="en",
        slug=post_slug(week, "en"),
        title=wr.week_heading(week.monday),
        excerpt=wr.week_description(week),
        content="\n".join(parts),
        item_count=len(selected),
    )


def render_japanese(items: Sequence[Mapping[str, Any]], week: wr.Week) -> Post:
    ranked = japanese_ranked(items, week)
    selected = blog_selection(ranked)
    year, number, _ = week.monday.isocalendar()
    parts = [
        f"<p>{escape(ja_range(week))}に公表された日本の法令・規制関連情報のうち、注目案件を分野別にまとめました。"
        "官公庁・規制当局・国会・裁判所・自主規制機関の公式情報を Japan Legal Reform Watch が毎日収集し、"
        "日本語の原資料から AI が要約したものです。並び順はキーワードに基づく機械的な指標によるもので、"
        "法的な重要度を示すものではありません。</p>",
        CTA_JA.format(link=link(SITE_URL + "?lang=ja", "updates.legal-gpt.com")),
    ]
    if selected:
        for area, rows in grouped(selected).items():
            parts.append(f"<h2>{escape(pl.area_label(area, 'ja'))}</h2>")
            parts.append("<ul>" + "".join(render_item_ja(item) for item in rows) + "</ul>")
    else:
        parts.append("<p>今週は日本語の AI 要約がある案件がありませんでした。</p>")
    consultations = week.consultations[:BLOG_CONSULTATIONS]
    if consultations:
        parts.append("<h2>週末時点で意見募集中のパブリックコメント</h2>")
        parts.append(
            f"<p>{escape(ja_date(week.sunday))}より後に提出期限を迎える意見募集（期限の近い順）。"
            "期限と提出方法は必ず公式原典でご確認ください。</p>"
        )
        parts.append("<ul>" + "".join(render_consultation(item, "ja") for item in consultations) + "</ul>")
    parts += [
        "<h2>関連リンク</h2>",
        "<ul>"
        f"<li>{link(SITE_URL + '?lang=ja', 'Japan Legal Reform Watch（日本の法改正・規制動向ダッシュボード）')}</li>"
        f"<li>{link(SITE_URL + week.path, '英語版の週次まとめ（全件）')}</li>"
        f"<li>{link(FEEDS_URL + 'all.ics', 'パブリックコメント締切カレンダー（.ics）')}</li>"
        "</ul>",
        CTA_JA.format(link=link(SITE_URL + "?lang=ja", "Japan Legal Reform Watch を開く")),
        "<p><small>本記事の要約は AI が生成した情報監視のための参考情報であり、法的助言ではなく、誤りを含む"
        f"可能性があります。正確な内容は必ず日本語の公式原典をご確認ください。{link(DISCLAIMER_JA_URL, '免責事項')}"
        "</small></p>",
    ]
    return Post(
        language="ja",
        slug=post_slug(week, "ja"),
        title=f"日本の法令・規制動向 週次まとめ（{year}年第{number}週：{ja_range(week)}）",
        excerpt=(
            f"{ja_range(week)}に公表された日本の法令・規制動向から注目案件を分野別にまとめました。"
            "AI による要約で、法的助言ではありません。"
        ),
        content="\n".join(parts),
        item_count=len(selected),
    )


def render_posts(items: Sequence[Mapping[str, Any]], week: wr.Week, languages: Sequence[str]) -> list[Post]:
    posts = []
    if "en" in languages:
        posts.append(render_english(week))
    if "ja" in languages:
        posts.append(render_japanese(items, week))
    return posts


# ----------------------------------------------------------------- WordPress


class WordPress:
    def __init__(self, config: Config, session: Any = None) -> None:
        if session is None:
            import requests  # noqa: PLC0415 -- only needed for a real run

            session = requests.Session()
        self.session = session
        self.session.auth = (config.username, config.password)
        self.endpoint = config.base_url + "/wp-json/wp/v2/posts"
        self.config = config

    def _check(self, response: Any, action: str) -> Any:
        if response.status_code >= 400:
            code = ""
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("code"), str):
                    code = re.sub(r"[^A-Za-z0-9_]", "", body["code"])[:60]
            except ValueError:
                pass
            raise WordPressError(f"{action} failed: HTTP {response.status_code}{' ' + code if code else ''}")
        try:
            return response.json()
        except ValueError as error:
            raise WordPressError(f"{action} failed: response was not JSON") from error

    def find_post(self, slug: str) -> int | None:
        response = self.session.get(
            self.endpoint,
            params={"slug": slug, "status": "draft,pending,publish,future,private", "context": "edit",
                    "_fields": "id"},
            timeout=REQUEST_TIMEOUT,
        )
        body = self._check(response, "lookup")
        if not isinstance(body, list):
            raise WordPressError("lookup failed: unexpected response")
        for row in body:
            if isinstance(row, dict) and isinstance(row.get("id"), int):
                return row["id"]
        return None

    def create_post(self, post: Post) -> int:
        payload: dict[str, Any] = {
            "title": post.title,
            "slug": post.slug,
            "status": self.config.status,
            "excerpt": post.excerpt,
            "content": post.content,
        }
        if self.config.categories.get(post.language):
            payload["categories"] = list(self.config.categories[post.language])
        if self.config.tags.get(post.language):
            payload["tags"] = list(self.config.tags[post.language])
        response = self.session.post(self.endpoint, json=payload, timeout=REQUEST_TIMEOUT)
        body = self._check(response, "create")
        if not isinstance(body, dict) or not isinstance(body.get("id"), int):
            raise WordPressError("create failed: unexpected response")
        return body["id"]


def publish(posts: Sequence[Post], client: WordPress) -> list[dict[str, Any]]:
    results = []
    for post in posts:
        existing = client.find_post(post.slug)
        if existing is not None:
            results.append({"language": post.language, "slug": post.slug, "action": "exists", "post_id": existing})
            continue
        post_id = client.create_post(post)
        results.append({"language": post.language, "slug": post.slug, "action": "created",
                        "status": client.config.status, "post_id": post_id, "items": post.item_count})
    return results


# ----------------------------------------------------------------- main


def append_step_summary(lines: Sequence[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def choose_week(weeks: Sequence[wr.Week], slug: str | None) -> wr.Week | None:
    if slug:
        return next((week for week in weeks if week.slug == slug), None)
    return weeks[0] if weeks else None


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None, session: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DATA_PATH)
    parser.add_argument("--today", type=date.fromisoformat, default=None, help="JST date (YYYY-MM-DD)")
    parser.add_argument("--week", default="", help="week slug such as 2026-w45 (default: latest completed week)")
    parser.add_argument("--languages", default="en,ja", help="comma-separated: en, ja")
    parser.add_argument("--dry-run", action="store_true", help="render only; never contact WordPress")
    parser.add_argument("--preview-dir", type=Path, default=None, help="write each rendered post as HTML here")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]
    if not languages or any(lang not in LANGUAGES for lang in languages):
        parser.error("--languages accepts en and/or ja")
    week_slug = args.week.strip()
    if week_slug and not wr.SLUG_PATTERN.match(week_slug):
        parser.error("--week must look like 2026-w45")

    today = args.today or datetime.now(JST).date()
    items = bpp.load_items(args.data)
    week = choose_week(wr.build_weeks(items, today), week_slug or None)
    if week is None:
        print("::warning::No completed weekly roundup is available to post.")
        return 0
    posts = render_posts(items, week, languages)
    if args.preview_dir:
        args.preview_dir.mkdir(parents=True, exist_ok=True)
        for post in posts:
            (args.preview_dir / f"{post.slug}.html").write_text(
                f"<h1>{escape(post.title)}</h1>\n<p><em>{escape(post.excerpt)}</em></p>\n{post.content}\n",
                encoding="utf-8",
            )

    summary = ["## Weekly blog post", "", f"Week: `{week.slug}`", ""]
    if args.dry_run:
        results = [{"language": p.language, "slug": p.slug, "action": "dry-run", "items": p.item_count} for p in posts]
    else:
        try:
            config = load_config(env)
        except ConfigError as error:
            print(f"::error::{error}")
            return 1
        if config is None:
            print("::warning::WordPress credentials are not configured (WP_BASE_URL, WP_USERNAME, WP_APP_PASSWORD); "
                  "nothing was posted.")
            append_step_summary(summary + ["WordPress credentials are not configured; nothing was posted."])
            return 0
        try:
            results = publish(posts, WordPress(config, session))
        except WordPressError as error:
            print(f"::error::WordPress {error}")
            append_step_summary(summary + [f"WordPress {error}"])
            return 1
        except Exception as error:  # network failure: report the type only
            print(f"::error::WordPress request failed: {type(error).__name__}")
            append_step_summary(summary + [f"WordPress request failed: {type(error).__name__}"])
            return 1
    summary += ["| Language | Slug | Result | Items |", "| --- | --- | --- | --- |"]
    for row in results:
        result = row["action"] + (f" ({row['status']}, id {row['post_id']})" if row["action"] == "created" else "")
        if row["action"] == "exists":
            result += f" (id {row['post_id']}, left unchanged)"
        summary.append(f"| {row['language']} | `{row['slug']}` | {result} | {row.get('items', '')} |")
    append_step_summary(summary)
    print(json.dumps({"week": week.slug, "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
