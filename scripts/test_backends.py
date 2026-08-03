#!/usr/bin/env python3
"""Tests for Alfred's shared backend brain (scripts/backends.py).

Pure-logic coverage only - no network, no subprocess, no model is ever spawned.
The Anthropic client is exercised through an injected `opener`/`sleeper`, and
backend resolution through injected `probes`, so this whole suite runs offline.

    python scripts/test_backends.py
or under pytest:
    python -m pytest scripts/test_backends.py
"""
import io
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backends as be  # noqa: E402


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
def _http_error(status, message="boom"):
    body = ('{"error":{"message":"%s"}}' % message).encode("utf-8")
    return urllib.error.HTTPError(
        "https://api.anthropic.com/v1/messages", status, message, {},
        io.BytesIO(body))


class _Opener:
    """A fake `send(url, headers, body, timeout)` for call_anthropic.

    `script` is a list of either dict responses (returned) or Exceptions (raised),
    consumed one per attempt. Records every (url, headers, body) it was called with.
    """
    def __init__(self, script):
        self._script = list(script)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body})
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class _Sleeper:
    """Records backoff delays instead of actually sleeping."""
    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


# --------------------------------------------------------------------------- #
# Frontmatter / URI / skill parsing (pure text helpers)
# --------------------------------------------------------------------------- #
class FrontmatterTests(unittest.TestCase):
    def test_splits_meta_and_body(self):
        body, meta = be.strip_frontmatter("---\nname: x\nmodel: opus\n---\nHello there")
        self.assertEqual(body, "Hello there")
        self.assertEqual(meta["name"], "x")
        self.assertEqual(meta["model"], "opus")

    def test_no_frontmatter_returns_text_unchanged(self):
        body, meta = be.strip_frontmatter("# Just a body\ncontent")
        self.assertEqual(body, "# Just a body\ncontent")
        self.assertEqual(meta, {})

    def test_unterminated_frontmatter_left_intact(self):
        text = "---\nname: x\nno closing fence"
        body, meta = be.strip_frontmatter(text)
        self.assertEqual(body, text)
        self.assertEqual(meta, {})


class ResolveUriTests(unittest.TestCase):
    def test_relative_path_anchored_to_root(self):
        p = be.resolve_uri("scripts/backends.py")
        self.assertTrue(p.is_absolute())
        self.assertEqual(p.name, "backends.py")
        self.assertTrue(str(p).startswith(str(be.ROOT)))

    def test_file_uri_prefix_stripped(self):
        # A non-existent absolute file:/// path with no anchor comes back as-is.
        p = be.resolve_uri("file:///nonexistent-xyz/somefile.md")
        self.assertEqual(p.name, "somefile.md")

    def test_relocate_maps_foreign_checkout_onto_root(self):
        # A genuinely-absolute path from another checkout, anchored on `.kiro`, is
        # rebuilt under this ROOT when the tail actually exists here. Built off
        # ROOT.anchor so it is absolute on both Windows (drive) and POSIX (`/`).
        if not (be.ROOT / ".kiro" / "agents").exists():
            self.skipTest("no .kiro/agents in this checkout")
        foreign = be.Path(be.ROOT.anchor) / "some" / "other" / ".kiro" / "agents"
        self.assertTrue(foreign.is_absolute())
        self.assertEqual(be._relocate(foreign), (be.ROOT / ".kiro" / "agents").resolve())

    def test_relocate_returns_none_when_tail_absent(self):
        foreign = be.Path(be.ROOT.anchor) / "x" / ".kiro" / "no-such-dir-xyz"
        self.assertIsNone(be._relocate(foreign))


class SkillParseTests(unittest.TestCase):
    def test_parses_comma_and_and_list(self):
        names = be.parse_skill_names("Load the coding, review and testing skills.")
        self.assertEqual(names, ["coding", "review", "testing"])

    def test_no_skill_line_returns_empty(self):
        self.assertEqual(be.parse_skill_names("I am an agent with no skills line."), [])

    def test_backticks_stripped(self):
        names = be.parse_skill_names("Load the `git-flow` and `ci-gate` skills.")
        self.assertEqual(names, ["git-flow", "ci-gate"])


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #
class AssembleSystemPromptTests(unittest.TestCase):
    def test_identity_and_memory_included(self):
        agent = {"identity": "I am alfred-coder."}
        out = be.assemble_system_prompt(
            agent, steering=False, skills=False, memory_text="remember X")
        self.assertIn(be.DEFAULT_PREAMBLE, out)
        self.assertIn("# Your identity", out)
        self.assertIn("I am alfred-coder.", out)
        self.assertIn("Alfred memory", out)
        self.assertIn("remember X", out)

    def test_empty_agent_and_no_memory_is_just_preamble(self):
        out = be.assemble_system_prompt(
            {}, steering=False, skills=False, memory_text="")
        self.assertEqual(out, be.DEFAULT_PREAMBLE)

    def test_custom_preamble_replaces_default(self):
        out = be.assemble_system_prompt(
            {"identity": "x"}, steering=False, skills=False,
            memory_text="", preamble="CUSTOM")
        self.assertTrue(out.startswith("CUSTOM"))
        self.assertNotIn(be.DEFAULT_PREAMBLE, out)


# --------------------------------------------------------------------------- #
# Model mapping + cost
# --------------------------------------------------------------------------- #
class ResolveAgentModelTests(unittest.TestCase):
    def test_opus_48_maps_to_cli_alias_and_effort(self):
        model, effort = be.resolve_agent_model("claude-opus-4.8", "claude")
        self.assertEqual(model, "opus")
        self.assertEqual(effort, "max")

    def test_opus_48_maps_to_api_model_id(self):
        model, effort = be.resolve_agent_model("claude-opus-4.8", "api")
        self.assertEqual(model, "claude-opus-5")
        self.assertEqual(effort, "max")

    def test_sonnet_maps(self):
        self.assertEqual(be.resolve_agent_model("claude-sonnet-4.6", "claude"),
                         ("sonnet", "high"))

    def test_unknown_model_falls_back_to_opus_tier(self):
        model, effort = be.resolve_agent_model("some-future-model", "api")
        self.assertEqual(model, be.FALLBACK_MODEL["api"])
        self.assertEqual(effort, "high")

    def test_overrides_win_over_builtin_map(self):
        overrides = {"claude-opus-4.8": {"effort": "low"}}
        model, effort = be.resolve_agent_model("claude-opus-4.8", "claude", overrides)
        self.assertEqual(effort, "low")       # overridden
        self.assertEqual(model, "opus")       # untouched key falls through

    def test_override_can_add_a_new_model_id(self):
        overrides = {"brand-new": {"api": "claude-x", "cli": "x", "effort": "high"}}
        self.assertEqual(be.resolve_agent_model("brand-new", "api", overrides),
                         ("claude-x", "high"))

    def test_injected_model_map_is_used(self):
        table = {"m": {"api": "A", "cli": "C", "effort": "mid"}}
        self.assertEqual(be.resolve_agent_model("m", "claude", None, table),
                         ("C", "mid"))


class EstimateCostTests(unittest.TestCase):
    def test_none_usage_is_unpriced(self):
        self.assertIsNone(be.estimate_cost("claude-opus-5", None))

    def test_unknown_model_is_unpriced(self):
        self.assertIsNone(be.estimate_cost("mystery-model", {"input_tokens": 10}))

    def test_basic_input_output_cost(self):
        cost = be.estimate_cost(
            "claude-opus-5", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
        self.assertEqual(cost, 30.0)          # 5.0 in + 25.0 out

    def test_cache_read_is_discounted(self):
        cost = be.estimate_cost(
            "claude-opus-5",
            {"input_tokens": 0, "output_tokens": 0,
             "cache_read_input_tokens": 1_000_000})
        self.assertEqual(cost, round(0.1 * 5.0, 6))     # 10% of input price

    def test_cache_creation_is_surcharged(self):
        cost = be.estimate_cost(
            "claude-opus-5",
            {"input_tokens": 0, "output_tokens": 0,
             "cache_creation_input_tokens": 1_000_000})
        self.assertEqual(cost, round(1.25 * 5.0, 6))    # 125% of input price


class ResolveModelTests(unittest.TestCase):
    def test_none_list_returns_requested(self):
        self.assertEqual(be.resolve_model("alfred-coder-7b", None), "alfred-coder-7b")

    def test_requested_present_is_kept(self):
        self.assertEqual(
            be.resolve_model("a", ["a", "b"], quiet=True), "a")

    def test_requested_absent_falls_to_first_loaded(self):
        self.assertEqual(
            be.resolve_model("z", ["a", "b"], quiet=True), "a")


# --------------------------------------------------------------------------- #
# Backend resolution + reporting (pure with injected probes)
# --------------------------------------------------------------------------- #
class ResolveBackendTests(unittest.TestCase):
    def test_auto_prefers_claude(self):
        probes = {"claude": True, "api": True, "local": True, "kiro": True, "dry": True}
        self.assertEqual(be.resolve_backend("auto", probes), "claude")

    def test_auto_walks_to_first_available(self):
        probes = {"claude": False, "api": False, "local": True, "kiro": True, "dry": True}
        self.assertEqual(be.resolve_backend("auto", probes), "local")

    def test_auto_none_available_raises(self):
        probes = {"claude": False, "api": False, "local": False, "kiro": False, "dry": True}
        with self.assertRaises(be.BackendError):
            be.resolve_backend("auto", probes)

    def test_explicit_available_is_honoured(self):
        probes = {"claude": False, "api": True, "local": False, "kiro": False, "dry": True}
        self.assertEqual(be.resolve_backend("api", probes), "api")

    def test_explicit_unavailable_raises_not_downgrades(self):
        probes = {"claude": False, "api": True, "local": False, "kiro": False, "dry": True}
        with self.assertRaises(be.BackendError):
            be.resolve_backend("claude", probes)     # must NOT silently pick api

    def test_unknown_backend_name_raises(self):
        with self.assertRaises(be.BackendError):
            be.resolve_backend("banana", {"dry": True})

    def test_report_names_the_auto_choice(self):
        probes = {"claude": False, "api": True, "local": False, "kiro": False, "dry": True}
        report = be.backend_report(probes)
        self.assertIn("auto would use: api", report)
        self.assertIn("unavailable", report)


# --------------------------------------------------------------------------- #
# Executor construction (dry path never spawns anything)
# --------------------------------------------------------------------------- #
class MakeExecutorTests(unittest.TestCase):
    def test_dry_executor_previews_without_spawning(self):
        probes = {"claude": False, "api": False, "local": False, "kiro": False, "dry": True}
        ex = be.make_executor("dry", probes=probes)
        out = ex("alfred-coder", "write a function")
        self.assertIn("[DRY-RUN]", out)
        self.assertIn("alfred-coder", out)
        self.assertEqual(ex.last_meta.get("model"), "(dry)")
        self.assertEqual(ex.backend, "dry")

    def test_executor_sets_backend_in_meta(self):
        ex = be.make_executor("dry", probes={"dry": True})
        ex("a", "t")
        self.assertEqual(ex.last_meta.get("backend"), "dry")

    def test_unavailable_backend_raises(self):
        probes = {"claude": True, "api": False, "local": False, "kiro": False, "dry": True}
        with self.assertRaises(be.BackendError):
            be.make_executor("api", probes=probes)


class EchoExecutorTests(unittest.TestCase):
    def test_long_task_is_truncated(self):
        out = be.echo_executor("agent", "x" * 500)
        self.assertIn("...[truncated]", out)

    def test_short_task_is_verbatim(self):
        out = be.echo_executor("agent", "short task")
        self.assertIn("short task", out)
        self.assertNotIn("truncated", out)


# --------------------------------------------------------------------------- #
# Anthropic client - retry / refusal / truncation, all offline
# --------------------------------------------------------------------------- #
class CallAnthropicTests(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._prev

    def test_missing_key_raises(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with self.assertRaises(be.BackendError):
            be.call_anthropic("claude-opus-5", "sys", "task",
                              opener=_Opener([{}]), sleeper=_Sleeper())

    def test_happy_path_returns_text_and_meta(self):
        resp = {"stop_reason": "end_turn", "model": "claude-opus-5",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "content": [{"type": "text", "text": "hello"}]}
        opener = _Opener([resp])
        text, meta = be.call_anthropic("claude-opus-5", "sys", "task",
                                       opener=opener, sleeper=_Sleeper())
        self.assertEqual(text, "hello")
        self.assertEqual(meta["backend"], "api")
        self.assertEqual(meta["model"], "claude-opus-5")
        self.assertEqual(meta["stop_reason"], "end_turn")
        self.assertIsNotNone(meta["cost_usd"])

    def test_effort_and_key_are_sent(self):
        resp = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}
        opener = _Opener([resp])
        be.call_anthropic("claude-opus-5", "the system", "do it",
                          effort="max", opener=opener, sleeper=_Sleeper())
        body = opener.calls[0]["body"]
        self.assertEqual(body["output_config"], {"effort": "max"})
        self.assertEqual(body["system"], "the system")
        self.assertEqual(opener.calls[0]["headers"]["x-api-key"], "test-key-not-real")

    def test_refusal_is_reported_not_raised(self):
        resp = {"stop_reason": "refusal",
                "stop_details": {"category": "safety"}, "content": []}
        text, meta = be.call_anthropic("claude-opus-5", "", "task",
                                       opener=_Opener([resp]), sleeper=_Sleeper())
        self.assertIn("[REFUSAL]", text)
        self.assertEqual(meta["refusal_category"], "safety")

    def test_max_tokens_appends_truncation_notice(self):
        resp = {"stop_reason": "max_tokens", "model": "claude-opus-5",
                "content": [{"type": "text", "text": "partial answer"}]}
        text, _ = be.call_anthropic("claude-opus-5", "", "task",
                                    opener=_Opener([resp]), sleeper=_Sleeper())
        self.assertIn("partial answer", text)
        self.assertIn("[TRUNCATED]", text)

    def test_429_is_retried_then_succeeds(self):
        resp = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}
        opener = _Opener([_http_error(429), resp])
        sleeper = _Sleeper()
        text, _ = be.call_anthropic("claude-opus-5", "", "task",
                                    opener=opener, sleeper=sleeper)
        self.assertEqual(text, "ok")
        self.assertEqual(len(sleeper.delays), 1)      # one backoff before retry
        self.assertEqual(len(opener.calls), 2)

    def test_5xx_is_retried(self):
        resp = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}
        opener = _Opener([_http_error(503), resp])
        sleeper = _Sleeper()
        text, _ = be.call_anthropic("claude-opus-5", "", "task",
                                    opener=opener, sleeper=sleeper)
        self.assertEqual(text, "ok")
        self.assertEqual(len(sleeper.delays), 1)

    def test_4xx_is_fatal_without_retry(self):
        opener = _Opener([_http_error(400, "bad request")])
        sleeper = _Sleeper()
        with self.assertRaises(be.BackendError):
            be.call_anthropic("claude-opus-5", "", "task",
                              opener=opener, sleeper=sleeper)
        self.assertEqual(len(sleeper.delays), 0)      # no retry on a 4xx
        self.assertEqual(len(opener.calls), 1)

    def test_retries_are_bounded(self):
        # Always 429. The retry guard is `attempt <= retries + 1`, so send fires on
        # attempts 1..retries+2 before the final raise: retries=1 -> 3 calls, 2 sleeps.
        opener = _Opener([_http_error(429), _http_error(429), _http_error(429)])
        sleeper = _Sleeper()
        with self.assertRaises(be.BackendError):
            be.call_anthropic("claude-opus-5", "", "task", retries=1,
                              opener=opener, sleeper=sleeper)
        self.assertEqual(len(opener.calls), 3)
        self.assertEqual(len(sleeper.delays), 2)

    def test_url_error_is_retried_then_fatal(self):
        # retries=2 -> send fires on attempts 1..4 (retries+2) before raising.
        err = urllib.error.URLError("connection refused")
        opener = _Opener([err, err, err, err])
        sleeper = _Sleeper()
        with self.assertRaises(be.BackendError):
            be.call_anthropic("claude-opus-5", "", "task", retries=2,
                              opener=opener, sleeper=sleeper)
        self.assertEqual(len(opener.calls), 4)
        self.assertEqual(len(sleeper.delays), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
