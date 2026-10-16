"""Offline tests for the OpenAI (gpt-*) direct-call adapter used by Stages 3-4.

The adapter turns a Responses API result into the Messages shape both stages
already parse, so validation, the title gate, caching and the cost guard run
unchanged. These tests pin the request shape (no sampling parameters, lowest
reasoning effort, room for reasoning on top of the visible ceiling, strict
schema), the usage mapping the cost estimate depends on, and the failure modes
that must not be mistaken for success: a refusal, a response cut off by
max_output_tokens, and exhausted credit reported as HTTP 429.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import anthropic_batch as ab  # noqa: E402
import openai_provider as op  # noqa: E402
import summarize_updates as su  # noqa: E402
import translate_updates as tu  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL = "gpt-6.1-sol"

ITEM = {
    "id": "jlrw-test-1",
    "title_en": "Draft amendment to the Enforcement Ordinance",
    "title_ja": "施行令の一部を改正する政令案",
    "summary_en": "s", "business_impact_en": "b", "recommended_action_en": "r",
    "stage": "Public Comment Open", "source_name": "e-Gov",
    "source_url": "https://example.go.jp/x", "area": "Other", "impact_level": "Low",
}


def response(text="{}", *, status="completed", reason=None, refusal=False,
             input_tokens=1200, cached=1000, written=0, output=300, reasoning=120):
    parts = [types.SimpleNamespace(type="output_text", text=text)]
    if refusal:
        parts = [types.SimpleNamespace(type="refusal", refusal="declined")]
    return types.SimpleNamespace(
        model=MODEL,
        status=status,
        incomplete_details=types.SimpleNamespace(reason=reason) if reason else None,
        output=[
            types.SimpleNamespace(type="reasoning", content=None),
            types.SimpleNamespace(type="message", content=parts),
        ],
        usage=types.SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output,
            input_tokens_details=types.SimpleNamespace(cached_tokens=cached, cache_write_tokens=written),
            output_tokens_details=types.SimpleNamespace(reasoning_tokens=reasoning),
        ),
    )


class FakeClient:
    def __init__(self, resp):
        self.calls = []
        self.responses = types.SimpleNamespace(create=self._create)
        self._resp = resp

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


class TestProviderSelection(unittest.TestCase):
    def test_only_gpt_models_route_to_openai(self):
        self.assertTrue(op.is_openai_model("gpt-6.1-sol"))
        for model in ("claude-sonnet-5-5", "claude-sonnet-5", "", None):
            self.assertFalse(op.is_openai_model(model))

    def test_production_defaults_stay_on_claude(self):
        """Rollback is the absence of a switch: nothing routes to OpenAI by default."""
        self.assertFalse(op.is_openai_model(su.DEFAULT_MODEL))
        self.assertFalse(op.is_openai_model(tu.DEFAULT_TRANSLATION_MODEL))
        for name in ("daily-update.yml", "translation-backfill.yml",
                     "japanese-summary-backfill.yml", "english-summary-backfill.yml"):
            text = (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            with self.subTest(name):
                self.assertNotIn("gpt-", text)
                self.assertNotIn("OPENAI_API_KEY", text)

    def test_key_check_follows_the_provider(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}, clear=True):
            self.assertFalse(su.provider_api_key_available(MODEL))
            self.assertTrue(su.provider_api_key_available("claude-sonnet-5-5"))
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}, clear=True):
            self.assertTrue(tu.provider_api_key_available(MODEL))
            self.assertFalse(tu.provider_api_key_available("claude-sonnet-5"))


class TestRequestShape(unittest.TestCase):
    def params(self, **env):
        with mock.patch.dict(os.environ, env, clear=False):
            return op.request_params(
                model=MODEL, system=[{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}],
                user="USER", schema=su.RESULT_SCHEMA, schema_name="english_summary",
                visible_max_tokens=1500, cache_key="k",
            )

    def test_lowest_effort_no_sampling_params_strict_schema(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            params = op.request_params(
                model=MODEL, system="SYS", user="USER", schema=su.RESULT_SCHEMA,
                schema_name="english_summary", visible_max_tokens=1500, cache_key="k",
            )
        self.assertEqual(params["reasoning"], {"effort": "low"})
        for forbidden in ("temperature", "top_p", "top_k", "thinking", "max_tokens"):
            self.assertNotIn(forbidden, params)
        fmt = params["text"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertTrue(fmt["strict"])
        self.assertIs(fmt["schema"], su.RESULT_SCHEMA)
        self.assertFalse(params["store"])

    def test_reasoning_gets_room_above_the_visible_ceiling(self):
        """max_output_tokens includes reasoning; the visible ceiling alone could
        leave a response with reasoning and no JSON."""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(op.max_output_tokens(1500), 1500 + op.DEFAULT_REASONING_HEADROOM_TOKENS)
        self.assertEqual(self.params(OPENAI_REASONING_HEADROOM_TOKENS="0")["max_output_tokens"], 1500)

    def test_anthropic_system_blocks_are_flattened(self):
        self.assertEqual(self.params()["instructions"], "SYS")

    def test_unsupported_effort_is_rejected(self):
        """`none` / `minimal` are 400s on gpt-6.1-sol; fail before any call."""
        for effort in ("none", "minimal", "bogus"):
            with self.subTest(effort), mock.patch.dict(os.environ, {"OPENAI_REASONING_EFFORT": effort}):
                with self.assertRaises(ValueError):
                    op.reasoning_effort()

    def test_every_schema_is_strict_mode_compatible(self):
        """Strict structured outputs need every property required and no extras."""
        schemas = [su.RESULT_SCHEMA, su.JA_RESULT_SCHEMA, tu.RESULT_SCHEMA,
                   tu.result_schema(("title",)), tu.result_schema(("summary", "recommended_action"))]
        for schema in schemas:
            with self.subTest(sorted(schema["properties"])):
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(sorted(schema["required"]), sorted(schema["properties"]))


class TestResponseMapping(unittest.TestCase):
    def test_usage_uses_anthropic_semantics(self):
        """input_tokens INCLUDES cache reads/writes on OpenAI; the estimators
        price them separately, so the uncached remainder must be split out."""
        msg = op.to_message(response(input_tokens=1200, cached=1000, written=50), MODEL)
        usage = su.message_usage(msg)
        self.assertEqual(usage["input_tokens"], 150)
        self.assertEqual(usage["cache_read_input_tokens"], 1000)
        self.assertEqual(usage["cache_creation_input_tokens"], 50)
        self.assertEqual(usage["output_tokens"], 300)
        self.assertEqual(usage["reasoning_tokens"], 120)

    def test_cost_counts_reasoning_once_as_output(self):
        usage = su.message_usage(op.to_message(response(input_tokens=1200, cached=1000, written=0,
                                                        output=300, reasoning=120), MODEL))
        expected = (200 * 2.0 + 1000 * 0.10 + 300 * 10.0) / 1_000_000
        self.assertAlmostEqual(su.estimate_usage_cost_usd(usage, MODEL), expected)
        self.assertAlmostEqual(tu.estimate_usage_cost_usd(usage, MODEL), expected)

    def test_refusal_raises_model_refusal(self):
        msg = op.to_message(response(refusal=True), MODEL)
        with self.assertRaises(ab.ModelRefusal):
            ab.raise_if_refused(msg)
        filtered = op.to_message(response(status="incomplete", reason="content_filter"), MODEL)
        self.assertEqual(filtered.stop_reason, "refusal")

    def test_truncated_response_is_an_item_failure_not_a_result(self):
        with self.assertRaises(op.IncompleteResponse) as raised:
            op.to_message(response(status="incomplete", reason="max_output_tokens"), MODEL)
        self.assertIsInstance(raised.exception, ValueError)
        self.assertEqual(tu.classify_error(raised.exception), "item_validation_error")


class TestErrorClassification(unittest.TestCase):
    def exc(self, name, status, **attrs):
        cls = type(name, (Exception,), {})
        err = cls("provider text never logged")
        err.status_code = status
        for key, value in attrs.items():
            setattr(err, key, value)
        return err

    def test_insufficient_quota_429_is_fatal_credit_not_rate_limit(self):
        shapes = [
            self.exc("RateLimitError", 429, code="insufficient_quota", body={"code": "insufficient_quota"}),
            self.exc("RateLimitError", 429, body={"error": {"code": "insufficient_quota"}}),
        ]
        for err in shapes:
            self.assertEqual(su.classify_provider_error(err), "insufficient_credit")
            self.assertEqual(tu.classify_provider_error(err), "insufficient_credit")
            self.assertIn("insufficient_credit", su.FATAL_PROVIDER_ERRORS)

    def test_plain_rate_limit_stays_retryable(self):
        err = self.exc("RateLimitError", 429, code="rate_limit_exceeded", body={"code": "rate_limit_exceeded"})
        self.assertEqual(su.classify_provider_error(err), "rate_limit")
        self.assertEqual(tu.classify_provider_error(err), "rate_limit")

    def test_auth_errors_share_the_sdk_names(self):
        self.assertEqual(su.classify_provider_error(self.exc("AuthenticationError", 401)), "authentication_error")
        self.assertEqual(tu.classify_provider_error(self.exc("PermissionDeniedError", 403)), "permission_error")


class TestStageRouting(unittest.TestCase):
    def test_english_summary_uses_production_prompt_and_schema(self):
        payload = {field: "x" for field in su.AI_FIELDS}
        payload["confidence"] = "medium"
        client = FakeClient(response(json.dumps(payload)))
        result, model, usage = su.request_summary(client, MODEL, dict(ITEM), {"raw_summary": "r"})
        self.assertEqual(result, payload)
        self.assertEqual(model, MODEL)
        call = client.calls[0]
        self.assertEqual(call["instructions"], su.SYSTEM_PROMPT)
        self.assertEqual(call["input"], su.build_user_content(dict(ITEM), {"raw_summary": "r"}))
        self.assertIs(call["text"]["format"]["schema"], su.RESULT_SCHEMA)
        self.assertEqual(usage["reasoning_tokens"], 120)

    def test_japanese_summary_uses_japanese_prompt(self):
        payload = {field: "要約" for field in su.JA_AI_FIELDS}
        client = FakeClient(response(json.dumps(payload, ensure_ascii=False)))
        result, _model, _usage = su.request_japanese_summary(client, MODEL, dict(ITEM), {})
        self.assertEqual(result, payload)
        call = client.calls[0]
        self.assertEqual(call["instructions"], su.SYSTEM_PROMPT_JA)
        self.assertIs(call["text"]["format"]["schema"], su.JA_RESULT_SCHEMA)

    def test_translation_requests_only_the_missing_fields(self):
        client = FakeClient(response(json.dumps({"title": "公开征求意见：施行令修订草案"}, ensure_ascii=False)))
        result, _model, _usage = tu.request_translation(client, MODEL, dict(ITEM), "zh-Hans", ("title",))
        self.assertEqual(result, {"title": "公开征求意见：施行令修订草案"})
        call = client.calls[0]
        self.assertEqual(call["instructions"], tu.SYSTEM_PROMPT)
        self.assertEqual(call["input"], tu.build_user_content(dict(ITEM), "zh-Hans", ("title",)))
        self.assertEqual(call["text"]["format"]["schema"]["required"], ["title"])
        self.assertEqual(call["prompt_cache_key"], "jlrw-translate-zh-Hans")

    def test_claude_models_never_touch_the_openai_client(self):
        client = FakeClient(response())
        client.messages = types.SimpleNamespace(create=mock.Mock(side_effect=RuntimeError("anthropic path")))
        with self.assertRaises(RuntimeError):
            su.request_summary(client, "claude-sonnet-5-5", dict(ITEM), {})
        self.assertEqual(client.calls, [])


class TestBatchIsRefused(unittest.TestCase):
    """The Batch path is Anthropic-only; a gpt model with --batch must fail loudly."""

    def test_summarize(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            su.main(["--batch", "--model", MODEL])
        self.assertEqual(raised.exception.code, 2)

    def tearDown(self):
        for handler in list(tu.logger.handlers):
            handler.close()
        tu.logger.handlers.clear()

    def test_translate(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            (tmp / "in.json").write_text(json.dumps([dict(ITEM)]), encoding="utf-8")
            with mock.patch.multiple(tu, INPUT_PATH=tmp / "in.json", OUTPUT_PATH=tmp / "in.json",
                                     CACHE_PATH=tmp / "cache.json", LOG_PATH=tmp / "t.log"), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                tu.main(["--batch", "--model", MODEL])
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
