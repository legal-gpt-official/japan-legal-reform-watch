"""Offline tests for the weekly WordPress blog post (no network)."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import post_weekly_blog as pwb  # noqa: E402
import weekly_roundups as wr  # noqa: E402

TODAY = date(2026, 11, 4)  # last completed week: 2026-W44 (26 Oct - 1 Nov)
ENV = {"WP_BASE_URL": "https://legal-gpt.com/", "WP_USERNAME": "bot", "WP_APP_PASSWORD": "abcd efgh"}


def item(item_id, **overrides):
    base = {
        "id": item_id,
        "title_en": f"Title {item_id}",
        "title_ja": f"日本語タイトル{item_id}",
        "area": "Finance / AML",
        "stage": "Government Announcement",
        "source_name": "Financial Services Agency (金融庁) 新着情報",
        "source_url": f"https://www.fsa.go.jp/{item_id}",
        "published_at": "2026-10-28",
        "summary_en": f"The FSA published {item_id}. A second sentence.",
        "summary_source": "claude",
        "summary_ja": f"金融庁は{item_id}を公表しました。二文目です。",
        "summary_ja_source": "claude",
        "relevance_score": 10,
    }
    base.update(overrides)
    return base


def corpus():
    return [item(f"i{n}", relevance_score=20 - n) for n in range(6)]


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, existing=(), fail_create=None):
        self.existing = set(existing)
        self.fail_create = fail_create
        self.created = []
        self.lookups = []
        self.auth = None

    def get(self, url, params=None, timeout=None):
        self.lookups.append((url, params))
        return FakeResponse(200, [{"id": 7}] if params["slug"] in self.existing else [])

    def post(self, url, json=None, timeout=None):
        if self.fail_create:
            return FakeResponse(self.fail_create, {"code": "rest_cannot_create", "message": "secret detail"})
        self.created.append((url, json))
        return FakeResponse(201, {"id": 100 + len(self.created)})


def run(argv, env=ENV, session=None, data=None):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "data.json"
        path.write_text(json.dumps(data if data is not None else corpus()), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            code = pwb.main(["--data", str(path), "--today", TODAY.isoformat(), *argv], env=env, session=session)
    return code, out.getvalue()


class TestConfig(unittest.TestCase):
    def test_missing_credentials_mean_not_configured(self):
        self.assertIsNone(pwb.load_config({}))
        self.assertIsNone(pwb.load_config({"WP_BASE_URL": "https://legal-gpt.com"}))

    def test_defaults_to_draft_and_parses_ids(self):
        config = pwb.load_config({**ENV, "WP_CATEGORY_IDS_JA": "12, 34", "WP_TAG_IDS_EN": "5"})
        self.assertEqual(config.status, "draft")
        self.assertEqual(config.base_url, "https://legal-gpt.com")
        self.assertEqual(config.categories["ja"], (12, 34))
        self.assertEqual(config.tags["en"], (5,))

    def test_rejects_plain_http_bad_status_and_bad_ids(self):
        for env in (
            {**ENV, "WP_BASE_URL": "http://legal-gpt.com"},
            {**ENV, "WP_BASE_URL": "https://legal-gpt.com/?x=<script>"},
            {**ENV, "WP_POST_STATUS": "private"},
            {**ENV, "WP_CATEGORY_IDS_EN": "news"},
        ):
            with self.subTest(env=env), self.assertRaises(pwb.ConfigError):
                pwb.load_config(env)


class TestContent(unittest.TestCase):
    def setUp(self):
        self.items = corpus() + [
            item("hostile", title_en="<script>x</script>", title_ja="<img src=x>", summary_en="<b>b</b>.",
                 summary_ja="<i>i</i>。", source_url="javascript:alert(1)", relevance_score=99),
            item("en-only", summary_ja="", summary_ja_source="", relevance_score=50),
            item("pc", stage="Public Comment Open", comment_deadline="2026-11-20", relevance_score=0),
        ]
        self.week = wr.build_weeks(self.items, TODAY)[0]
        self.en = pwb.render_english(self.week)
        self.ja = pwb.render_japanese(self.items, self.week)

    def test_slugs_are_fixed_per_week_and_language(self):
        self.assertEqual(self.en.slug, "japan-legal-roundup-2026-w44-en")
        self.assertEqual(self.ja.slug, "japan-legal-roundup-2026-w44-ja")
        self.assertEqual(self.ja.title, "日本の法令・規制動向 週次まとめ（2026年第44週：2026年10月26日〜11月1日）")

    def test_untrusted_fields_are_escaped_and_unsafe_urls_dropped(self):
        for post in (self.en, self.ja):
            with self.subTest(language=post.language):
                self.assertNotIn("<script>x", post.content)
                self.assertNotIn("<img src=x", post.content)
                self.assertNotIn("<b>b", post.content)
                self.assertNotIn("<i>i", post.content)
                self.assertNotIn("javascript:", post.content)

    def test_english_links_full_roundup_item_pages_sources_and_disclaimer(self):
        self.assertIn('href="https://updates.legal-gpt.com/weekly/2026-w44/"', self.en.content)
        self.assertIn('href="https://updates.legal-gpt.com/u/en-only/"', self.en.content)
        self.assertIn('href="https://www.fsa.go.jp/en-only"', self.en.content)
        self.assertIn("(AI summary)", self.en.content)
        self.assertIn("not legal advice", self.en.content)
        self.assertIn("disclaimer_en.html", self.en.content)
        self.assertIn("Comments due: 2026-11-20 (end of day, JST)", self.en.content)
        self.assertLessEqual(self.en.item_count, pwb.BLOG_MAX_ITEMS)

    def test_japanese_uses_japanese_title_and_summary_only_where_complete(self):
        self.assertIn("日本語タイトルi0", self.ja.content)
        self.assertIn("金融庁はi0を公表しました。", self.ja.content)
        self.assertNotIn("二文目", self.ja.content)  # first sentence only
        self.assertNotIn("日本語タイトルen-only", self.ja.content.split("週末時点")[0])
        self.assertIn('href="https://updates.legal-gpt.com/u/i0/?lang=ja"', self.ja.content)
        self.assertIn("（AI要約）", self.ja.content)
        self.assertIn("金融・マネーロンダリング対策", self.ja.content)
        self.assertIn("金融庁", self.ja.content)
        self.assertIn("法的助言ではなく", self.ja.content)
        self.assertIn("disclaimer_ja.html", self.ja.content)
        self.assertIn("意見提出期限：2026-11-20（日本時間・当日中）", self.ja.content)
        self.assertNotIn("The FSA published", self.ja.content)

    def test_every_post_opens_and_closes_with_a_dashboard_link(self):
        for post, href in ((self.en, 'href="https://updates.legal-gpt.com/"'),
                           (self.ja, 'href="https://updates.legal-gpt.com/?lang=ja"')):
            with self.subTest(language=post.language):
                paragraphs = post.content.splitlines()
                self.assertIn(href, paragraphs[1])
                self.assertIn("Japan Legal Reform Watch", paragraphs[1])
                self.assertIn(href, paragraphs[-2])
        empty = pwb.render_japanese([], self.week)
        self.assertIn('href="https://updates.legal-gpt.com/?lang=ja"', empty.content.splitlines()[1])

    def test_long_japanese_sentence_is_cut(self):
        line = pwb.japanese_one_line({"summary_ja": "あ" * 300 + "。"})
        self.assertEqual(len(line), pwb.JA_LINE_MAX_CHARS)
        self.assertTrue(line.endswith("…"))


class TestPublishing(unittest.TestCase):
    def test_creates_both_posts_as_drafts_with_categories(self):
        session = FakeSession()
        code, out = run([], env={**ENV, "WP_CATEGORY_IDS_JA": "9"}, session=session)
        self.assertEqual(code, 0)
        self.assertEqual(session.auth, ("bot", "abcd efgh"))
        self.assertEqual([p["slug"] for _, p in session.created],
                         ["japan-legal-roundup-2026-w44-en", "japan-legal-roundup-2026-w44-ja"])
        self.assertTrue(all(p["status"] == "draft" for _, p in session.created))
        self.assertTrue(all(url == "https://legal-gpt.com/wp-json/wp/v2/posts" for url, _ in session.created))
        self.assertNotIn("categories", session.created[0][1])
        self.assertEqual(session.created[1][1]["categories"], [9])
        self.assertNotIn("abcd", out)

    def test_existing_post_is_never_touched_so_reruns_do_not_duplicate(self):
        session = FakeSession(existing={"japan-legal-roundup-2026-w44-en"})
        code, out = run([], session=session)
        self.assertEqual(code, 0)
        self.assertEqual([p["slug"] for _, p in session.created], ["japan-legal-roundup-2026-w44-ja"])
        self.assertIn('"action": "exists"', out)
        self.assertEqual(session.lookups[0][1]["status"], "draft,pending,publish,future,private")

    def test_missing_credentials_warn_and_post_nothing(self):
        session = FakeSession()
        code, out = run([], env={}, session=session)
        self.assertEqual(code, 0)
        self.assertIn("::warning::", out)
        self.assertEqual(session.created, [])

    def test_wordpress_error_fails_without_printing_the_response_body(self):
        code, out = run([], session=FakeSession(fail_create=401))
        self.assertEqual(code, 1)
        self.assertIn("HTTP 401 rest_cannot_create", out)
        self.assertNotIn("secret detail", out)

    def test_dry_run_never_contacts_wordpress_and_writes_previews(self):
        session = FakeSession()
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = run(["--dry-run", "--preview-dir", tmp], session=session)
            files = sorted(p.name for p in Path(tmp).iterdir())
        self.assertEqual(code, 0)
        self.assertEqual(session.lookups, [])
        self.assertEqual(files, ["japan-legal-roundup-2026-w44-en.html", "japan-legal-roundup-2026-w44-ja.html"])

    def test_week_and_language_selection(self):
        session = FakeSession()
        code, _ = run(["--week", "2026-w44", "--languages", "ja"], session=session)
        self.assertEqual(code, 0)
        self.assertEqual(len(session.created), 1)
        code, out = run(["--week", "2026-w30"], session=FakeSession())
        self.assertEqual(code, 0)
        self.assertIn("No completed weekly roundup", out)

    def test_no_completed_week_warns(self):
        code, out = run([], data=[item("x", published_at="2026-11-03")], session=FakeSession())
        self.assertEqual(code, 0)
        self.assertIn("::warning::", out)


class TestWorkflow(unittest.TestCase):
    def test_workflow_is_weekly_read_only_and_keeps_secrets_in_secrets(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "weekly-blog-post.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "0 1 * * 1"', workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertIn("WP_APP_PASSWORD: ${{ secrets.WP_APP_PASSWORD }}", workflow)
        self.assertIn("WP_USERNAME: ${{ secrets.WP_USERNAME }}", workflow)
        self.assertIn("group: japan-legal-reform-data-writer", workflow)
        self.assertNotIn("ANTHROPIC_API_KEY", workflow)
        self.assertNotIn("OPENAI_API_KEY", workflow)


if __name__ == "__main__":
    unittest.main()
