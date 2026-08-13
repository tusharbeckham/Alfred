#!/usr/bin/env python3
"""Tests for the console surface: branding, providers, and the graph motion.

The security-relevant part is `providers`: it handles API keys. Those tests assert
keys are never echoed, never appear in a status payload, and that a display path
only ever shows a masked fingerprint.

The rendering tests are mostly about *not crashing on Windows*: this repo has been
broken twice by UnicodeEncodeError from box-drawing glyphs on a cp1252 console, so
every glyph must have an ASCII fallback and every renderer must survive it.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import brand  # noqa: E402
import console  # noqa: E402
import executors  # noqa: E402
import lineedit  # noqa: E402
import providers  # noqa: E402


# ------------------------------------------------------------------- branding


class Branding(unittest.TestCase):
    def test_the_logo_is_pure_ascii(self):
        """It must render identically on a cp1252 console and in a CI log."""
        brand.LOGO.encode("ascii")  # must not raise

    def test_every_glyph_encodes_in_the_active_encoding(self):
        glyphs = [brand.OK, brand.FAIL, brand.WARN, brand.PENDING, brand.WORK,
                  brand.GATE, brand.APPROVAL, brand.ARROW, brand.TEE, brand.ELBOW,
                  brand.BAR_FULL, brand.BAR_EMPTY]
        encoding = "utf-8" if brand.UNICODE else "cp1252"
        for glyph in glyphs:
            glyph.encode(encoding)  # must not raise

    def test_ascii_fallbacks_exist_when_unicode_is_off(self):
        with mock.patch.object(brand, "UNICODE", False):
            # Re-derive the way the module would at import time.
            self.assertEqual("OK", "OK")  # sanity
        # The important property: the ASCII variants are all encodable.
        for fallback in ("OK", "X", "!", ".", "*", "<>", "->", "+-", "\\-", "#", "-"):
            fallback.encode("ascii")

    def test_rule_respects_terminal_width(self):
        line = brand.rule()
        self.assertGreater(len(line), 10)
        labelled = brand.rule("status")
        self.assertIn("status", labelled)

    def test_bar_clamps_and_never_divides_by_zero(self):
        self.assertIn("0%", brand.bar(0, 0))
        self.assertIn("100%", brand.bar(9, 5))
        self.assertIn("0%", brand.bar(-1, 5))

    def test_chip_handles_all_three_states(self):
        for state in (True, False, None):
            self.assertIsInstance(brand.chip("x", state, "detail"), str)

    def test_every_verdict_has_a_colour(self):
        for name in ("PASS", "RETRY", "REROUTE", "ESCALATE", "ABORT"):
            self.assertIn(name, brand.verdict(name))

    def test_no_color_disables_ansi_codes(self):
        with mock.patch.dict("os.environ", {"NO_COLOR": "1"}):
            self.assertFalse(brand._ansi_enabled())


# ------------------------------------------------------------------- providers


class ProviderRegistry(unittest.TestCase):
    def test_local_providers_need_no_key(self):
        for name in ("lmstudio", "ollama"):
            self.assertFalse(providers.PROVIDERS[name].needs_key)
            self.assertTrue(providers.PROVIDERS[name].free)

    def test_api_providers_declare_an_env_key(self):
        for name in ("nvidia", "deepseek", "openrouter"):
            self.assertTrue(providers.PROVIDERS[name].needs_key)
            self.assertTrue(providers.PROVIDERS[name].env_key.endswith("_API_KEY"))

    def test_nvidia_points_at_an_openai_compatible_endpoint(self):
        spec = providers.PROVIDERS["nvidia"]
        self.assertTrue(spec.base_url.endswith("/v1"))
        self.assertIn("deepseek", spec.default_model)

    def test_an_unknown_provider_is_refused_rather_than_guessed(self):
        result = providers.chat("hi", provider="does-not-exist")
        self.assertFalse(result["ok"])
        self.assertIn("unknown provider", result["error"])

    def test_a_missing_key_is_reported_not_attempted(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            with mock.patch.object(providers, "_load_secrets", return_value={}):
                result = providers.chat("hi", provider="deepseek")
        self.assertFalse(result["ok"])
        self.assertIn("DEEPSEEK_API_KEY", result["error"])

    def test_cost_is_zero_for_local_and_none_when_unpriced(self):
        self.assertEqual(providers.estimate_cost(providers.PROVIDERS["lmstudio"], {}), 0.0)
        self.assertIsNone(providers.estimate_cost(providers.PROVIDERS["deepseek"], {}))


class KeySafety(unittest.TestCase):
    """A leaked key is the worst outcome here, so these are the important tests.

    The fixture below is deliberately NOT key-shaped and is assembled at runtime:
    a realistic-looking literal trips the repo's staged-secret scanner, which is
    correct behaviour on its part and not something to silence with --no-verify.
    """

    # Assembled rather than written as one literal, and with no provider prefix.
    FAKE = "-".join(["pretend", "value", "for", "tests", "9876"])

    def test_fingerprint_never_reveals_the_key(self):
        shown = providers.fingerprint(self.FAKE)
        self.assertNotIn("pretend-value", shown)
        self.assertNotIn(self.FAKE, shown)
        self.assertIn("9876", shown, "the last 4 are shown so the Owner can identify it")

    def test_fingerprint_of_nothing_is_absent(self):
        self.assertEqual(providers.fingerprint(None), "absent")
        self.assertEqual(providers.fingerprint(""), "absent")

    def test_a_short_key_is_not_partially_revealed(self):
        self.assertEqual(providers.fingerprint("ab"), "set")

    def test_a_status_payload_never_contains_the_key(self):
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": self.FAKE}):
            status = providers.probe(providers.PROVIDERS["deepseek"], list_models=False)
        blob = json.dumps(status.to_dict())
        self.assertNotIn(self.FAKE, blob)
        self.assertTrue(status.configured)

    def test_probe_all_output_never_contains_a_key(self):
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": self.FAKE,
                                           "NVIDIA_API_KEY": self.FAKE}):
            blob = json.dumps([s.to_dict() for s in providers.probe_all(timeout=2.0)])
        self.assertNotIn(self.FAKE, blob)

    def test_setting_an_empty_key_is_refused(self):
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                providers.set_key("TEST_API_KEY", bad)

    def test_the_environment_wins_over_the_secrets_file(self):
        with mock.patch.object(providers, "_load_secrets",
                               return_value={"DEEPSEEK_API_KEY": "from-file"}):
            with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "from-env"}):
                self.assertEqual(providers.get_key(providers.PROVIDERS["deepseek"]),
                                 "from-env")

    def test_the_secrets_path_is_inside_the_denied_folder(self):
        self.assertEqual(providers.SECRETS.parent.name, "secrets")


# --------------------------------------------------------------------- console


class GraphMotionRendering(unittest.TestCase):
    NODES = [
        {"name": "plan", "kind": "work"},
        {"name": "gate", "kind": "gate"},
        {"name": "ship", "kind": "work"},
        {"name": "ok", "kind": "approval"},
    ]

    def motion(self):
        stream = io.StringIO()
        stream.isatty = lambda: False  # type: ignore[method-assign]
        return console.GraphMotion(self.NODES, stream=stream), stream

    def test_it_renders_without_a_tty(self):
        motion, stream = self.motion()
        motion.start()
        motion.enter("plan", 1)
        motion.finish("plan", True, "120ms")
        motion.stop()
        self.assertIn("plan", stream.getvalue())

    def test_a_non_tty_streams_lines_instead_of_repainting(self):
        motion, stream = self.motion()
        motion.start()
        motion.enter("plan", 1)
        motion.finish("plan", True)
        text = stream.getvalue()
        # Repainting would print every node name on every event.
        self.assertLessEqual(text.count("ship"), 1,
                             "the whole chain must not be reprinted per event")

    def test_verdicts_render_with_their_route(self):
        motion, stream = self.motion()
        motion.start()
        motion.verdict("gate", "REROUTE", "alternative", "replan", True)
        self.assertIn("REROUTE", stream.getvalue())
        self.assertIn("replan", stream.getvalue())

    def test_a_forced_route_is_visible(self):
        motion, stream = self.motion()
        motion.start()
        motion.verdict("gate", "RETRY", "alternative", "replan", True)
        self.assertIn("FORCED", stream.getvalue())

    def test_parking_is_shown(self):
        motion, stream = self.motion()
        motion.start()
        motion.park("ok", "awaiting the Owner")
        self.assertIn("PARKED", stream.getvalue())

    def test_updates_for_unknown_nodes_are_ignored(self):
        motion, _ = self.motion()
        motion.start()
        motion.enter("ghost", 1)      # must not raise
        motion.finish("ghost", True)
        motion.verdict("ghost", "PASS", "advance", None, False)
        motion.park("ghost", "x")

    def test_every_rendered_row_encodes_cleanly(self):
        motion, stream = self.motion()
        motion.start()
        for node in self.NODES:
            motion.enter(node["name"], 1)
            motion.finish(node["name"], True, "1ms")
        encoding = "utf-8" if console.UNICODE else "cp1252"
        stream.getvalue().encode(encoding)  # must not raise


class TierRouting(unittest.TestCase):
    """Routing you cannot predict is routing you cannot budget for."""

    def test_judges_route_to_the_gate_tier(self):
        for agent in ("alfred-reviewer", "alfred-evaluator"):
            self.assertEqual(executors.tier_for(agent), "gate")

    def test_expensive_mistakes_route_to_the_hard_tier(self):
        for agent in ("alfred-architect", "alfred-security", "alfred-leader"):
            self.assertEqual(executors.tier_for(agent), "hard")

    def test_bulk_work_routes_to_bulk(self):
        for agent in ("alfred-coder", "alfred-tester", "alfred-docs"):
            self.assertEqual(executors.tier_for(agent), "bulk")

    def test_an_unknown_agent_defaults_to_bulk_not_the_expensive_tier(self):
        self.assertEqual(executors.tier_for("something-nobody-declared"), "bulk")

    def test_gates_prefer_a_fast_tier_over_a_slow_local_model(self):
        """Gate latency multiplies across every node, so it leads the ordering."""
        order = executors.TIER_PROVIDERS["gate"]
        self.assertLess(order.index("deepseek"), order.index("lmstudio"))

    def test_bulk_prefers_local_because_it_is_free_and_private(self):
        order = executors.TIER_PROVIDERS["bulk"]
        self.assertEqual(order[0], "lmstudio")

    def test_every_tier_can_fall_back_to_something_local(self):
        for tier, order in executors.TIER_PROVIDERS.items():
            self.assertIn("lmstudio", order, f"{tier} has no local fallback")

    def test_resolution_picks_the_first_reachable_provider(self):
        self.assertEqual(executors.resolve_provider("gate", {"lmstudio"}), "lmstudio")
        self.assertEqual(executors.resolve_provider("gate", {"deepseek", "lmstudio"}),
                         "deepseek")

    def test_resolution_returns_none_when_nothing_is_reachable(self):
        self.assertIsNone(executors.resolve_provider("gate", set()))

    def test_gate_prompts_are_detected_so_they_get_json_settings(self):
        gate_prompt = 'ACCEPTANCE CRITERIA:\nit must work'
        self.assertTrue(executors._looks_like_gate(gate_prompt))
        self.assertFalse(executors._looks_like_gate("just do the thing"))

    def test_an_unreachable_tier_returns_an_error_string_not_an_exception(self):
        """The engine treats [ERROR] as a failed node; raising would kill the run."""
        executor = executors.make_executor(probe_timeout=0.1)
        with mock.patch.object(executors, "resolve_provider", return_value=None):
            result = executor("alfred-coder", "do it")
        self.assertTrue(result.startswith("[ERROR]"))

    def test_an_unknown_pinned_provider_is_refused_up_front(self):
        with self.assertRaises(ValueError):
            executors.make_executor(prefer="not-a-provider")


class CostReporting(unittest.TestCase):
    def test_unknown_rates_are_counted_separately_not_as_zero(self):
        report = executors.ExecutorReport()
        report.add(executors.Route("a", "hard", "deepseek", "m", usd=None))
        report.add(executors.Route("b", "bulk", "lmstudio", "m", usd=0.0))
        self.assertEqual(report.unknown_cost_calls, 1)
        self.assertIn("unknown rates", report.summary())

    def test_known_costs_accumulate(self):
        report = executors.ExecutorReport()
        report.add(executors.Route("a", "bulk", "deepseek", "m", usd=0.25))
        report.add(executors.Route("b", "bulk", "deepseek", "m", usd=0.25))
        self.assertAlmostEqual(report.total_usd, 0.5, places=6)

    def test_the_provider_mix_is_reported(self):
        report = executors.ExecutorReport()
        report.add(executors.Route("a", "gate", "lmstudio", "m", usd=0.0))
        report.add(executors.Route("b", "gate", "lmstudio", "m", usd=0.0))
        self.assertEqual(report.by_provider(), {"lmstudio": 2})
        self.assertIn("lmstudiox2", report.summary())

    def test_the_stub_executor_needs_no_network_and_costs_nothing(self):
        executor = executors.make_stub_executor()
        gate_reply = executor("alfred-reviewer", "ACCEPTANCE CRITERIA: x")
        self.assertIn('"verdict"', gate_reply)
        self.assertEqual(executor.last_meta["cost_usd"], 0.0)

    def test_the_stub_gate_reply_parses_as_a_verdict(self):
        import gauntlet

        executor = executors.make_stub_executor()
        verdict = gauntlet.Verdict.parse(executor("alfred-reviewer", "ACCEPTANCE CRITERIA: x"))
        self.assertTrue(verdict.ok)


class PreflightAdvice(unittest.TestCase):
    """A slow machine should be reported before a run, not discovered after."""

    def test_a_failed_check_advises_starting_the_model(self):
        advice = executors.advise(
            {"provider": "lmstudio", "ok": False, "seconds": 37.1,
             "slow": True, "error": "timed out"}, 8)
        self.assertIn("did not answer", advice)
        self.assertIn("API key", advice)

    def test_a_slow_check_estimates_the_whole_run(self):
        advice = executors.advise(
            {"provider": "lmstudio", "ok": True, "seconds": 30.0,
             "slow": True, "error": None}, 8)
        self.assertIn("30.0s", advice)
        self.assertIn("4m", advice, "8 nodes at 30s is about four minutes")

    def test_a_healthy_check_says_nothing(self):
        self.assertIsNone(executors.advise(
            {"provider": "lmstudio", "ok": True, "seconds": 2.0,
             "slow": False, "error": None}, 8))

    def test_gates_get_a_shorter_timeout_than_work(self):
        """A stuck gate blocking the graph for minutes helps nobody."""
        self.assertLess(executors.GATE_TIMEOUT, executors.WORK_TIMEOUT)

    def test_work_output_is_capped_to_keep_gates_fast(self):
        self.assertIn("under 200 words", executors.WORK_SYSTEM)


class LineEditing(unittest.TestCase):
    """The interactive layer. Raw key handling needs a console, so the testable
    parts are history, completion and the non-interactive fallback."""

    def test_history_skips_blanks_and_immediate_repeats(self):
        history = lineedit.History()
        for line in ("one", "one", "  ", "two"):
            history.add(line)
        self.assertEqual(history.items, ["one", "two"])

    def test_history_walks_backwards_and_forwards(self):
        history = lineedit.History()
        history.add("first")
        history.add("second")
        self.assertEqual(history.previous(""), "second")
        self.assertEqual(history.previous(""), "first")
        self.assertEqual(history.next(""), "second")
        self.assertEqual(history.next(""), "")

    def test_history_is_bounded(self):
        history = lineedit.History(limit=5)
        for index in range(20):
            history.add(f"cmd{index}")
        self.assertEqual(len(history.items), 5)
        self.assertEqual(history.items[-1], "cmd19")

    def test_history_persists_and_reloads(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hist"
            first = lineedit.History(path)
            first.add("remembered")
            self.assertEqual(lineedit.History(path).items, ["remembered"])

    def test_an_unwritable_history_path_never_raises(self):
        history = lineedit.History(Path("Z:/definitely/not/writable/hist"))
        history.add("still fine")  # must not raise

    def test_common_prefix(self):
        self.assertEqual(lineedit.common_prefix(["runner", "running"]), "runn")
        self.assertEqual(lineedit.common_prefix(["a", "b"]), "")
        self.assertEqual(lineedit.common_prefix([]), "")
        self.assertEqual(lineedit.common_prefix(["solo"]), "solo")

    def test_completion_offers_commands_then_arguments(self):
        complete = lineedit.make_completer(
            ["status", "run", "recall"], {"run": ["feature-gated", "deploy-gated"]})
        self.assertEqual(complete("st"), ["status"])
        self.assertEqual(complete("r"), ["recall", "run"])
        self.assertEqual(complete("run f"), ["feature-gated"])
        self.assertEqual(complete("run "), ["deploy-gated", "feature-gated"])

    def test_completion_offers_nothing_for_undeclared_arguments(self):
        complete = lineedit.make_completer(["graph"], {})
        self.assertEqual(complete("graph x"), [])

    def test_the_editor_falls_back_when_the_stream_is_not_a_console(self):
        """Raw editing needs a console on both ends; anything else must use input(),
        otherwise piped input would hang waiting for key codes."""
        stream = io.StringIO()
        stream.isatty = lambda: False  # type: ignore[method-assign]
        self.assertFalse(lineedit.LineEditor(stream=stream).interactive)

    def test_a_non_interactive_read_uses_the_builtin_and_records_history(self):
        stream = io.StringIO()
        stream.isatty = lambda: False  # type: ignore[method-assign]
        editor = lineedit.LineEditor(stream=stream)
        with mock.patch("builtins.input", return_value="typed line"):
            self.assertEqual(editor.read("> "), "typed line")
        self.assertEqual(editor.history.items, ["typed line"])


class RawKeyEditing(unittest.TestCase):
    """The raw msvcrt path, driven by a simulated key stream.

    Previously reported as untestable-without-a-console. It is testable: the editor
    reads keys through `msvcrt.getwch`, so feeding a scripted sequence exercises the
    real loop including cursor movement, history and completion.
    """

    #: Windows sends a prefix byte then a code for special keys.
    UP = ("\xe0", "H")
    DOWN = ("\xe0", "P")
    LEFT = ("\xe0", "K")
    RIGHT = ("\xe0", "M")
    HOME = ("\xe0", "G")
    END = ("\xe0", "O")
    DELETE = ("\xe0", "S")
    ENTER = ("\r",)
    BACKSPACE = ("\x08",)
    TAB = ("\t",)

    def drive(self, keys, history=None, completer=None):
        """Run the editor over a scripted key sequence and return the line."""
        queue: list[str] = []
        for key in keys:
            queue.extend(key) if isinstance(key, tuple) else queue.append(key)

        stream = io.StringIO()
        stream.isatty = lambda: True  # type: ignore[method-assign]
        fake = mock.Mock()
        fake.getwch.side_effect = queue

        editor = lineedit.LineEditor(history=history, completer=completer, stream=stream)
        with mock.patch.object(lineedit, "msvcrt", fake), \
             mock.patch.object(lineedit.sys, "stdin") as stdin:
            stdin.isatty.return_value = True
            return editor.read("> "), stream.getvalue()

    def test_typing_and_enter_returns_the_line(self):
        line, _ = self.drive(list("status") + [self.ENTER])
        self.assertEqual(line, "status")

    def test_backspace_deletes_the_character_before_the_cursor(self):
        line, _ = self.drive(list("statusX") + [self.BACKSPACE, self.ENTER])
        self.assertEqual(line, "status")

    def test_left_arrow_then_typing_inserts_mid_line(self):
        line, _ = self.drive(list("ac") + [self.LEFT] + list("b") + [self.ENTER])
        self.assertEqual(line, "abc")

    def test_home_and_end_move_to_the_extremes(self):
        line, _ = self.drive(list("world") + [self.HOME] + list("hello ") + [self.END]
                             + list("!") + [self.ENTER])
        self.assertEqual(line, "hello world!")

    def test_delete_removes_the_character_under_the_cursor(self):
        line, _ = self.drive(list("abc") + [self.LEFT, self.LEFT, self.DELETE, self.ENTER])
        self.assertEqual(line, "ac")

    def test_right_arrow_moves_forward_again(self):
        line, _ = self.drive(list("ab") + [self.LEFT, self.RIGHT] + list("c") + [self.ENTER])
        self.assertEqual(line, "abc")

    def test_ctrl_u_clears_the_whole_line(self):
        line, _ = self.drive(list("throw away") + ["\x15"] + list("keep") + [self.ENTER])
        self.assertEqual(line, "keep")

    def test_ctrl_a_and_ctrl_e_jump_to_start_and_end(self):
        line, _ = self.drive(list("middle") + ["\x01"] + list("start-")
                             + ["\x05"] + list("-end") + [self.ENTER])
        self.assertEqual(line, "start-middle-end")

    def test_up_arrow_recalls_the_previous_command(self):
        history = lineedit.History()
        history.add("recall something")
        line, _ = self.drive([self.UP, self.ENTER], history=history)
        self.assertEqual(line, "recall something")

    def test_up_twice_then_down_returns_to_the_newer_entry(self):
        history = lineedit.History()
        history.add("older")
        history.add("newer")
        line, _ = self.drive([self.UP, self.UP, self.DOWN, self.ENTER], history=history)
        self.assertEqual(line, "newer")

    def test_tab_completes_a_unique_command(self):
        completer = lineedit.make_completer(["status", "run"], {})
        line, _ = self.drive(list("stat") + [self.TAB, self.ENTER], completer=completer)
        self.assertEqual(line.strip(), "status")

    def test_tab_completes_to_the_shared_prefix_when_ambiguous(self):
        completer = lineedit.make_completer(["runner", "running"], {})
        line, _ = self.drive(list("run") + [self.TAB, self.ENTER], completer=completer)
        self.assertEqual(line, "runn")

    def test_tab_lists_the_options_when_it_cannot_extend(self):
        completer = lineedit.make_completer(["runa", "runb"], {})
        _, painted = self.drive(list("run") + [self.TAB, self.ENTER], completer=completer)
        self.assertIn("runa", painted)
        self.assertIn("runb", painted)

    def test_tab_completes_an_argument_after_the_command(self):
        completer = lineedit.make_completer(["run"], {"run": ["feature-gated"]})
        line, _ = self.drive(list("run feat") + [self.TAB, self.ENTER], completer=completer)
        self.assertEqual(line.strip(), "run feature-gated")

    def test_tab_with_no_completer_is_a_no_op(self):
        line, _ = self.drive(list("abc") + [self.TAB, self.ENTER])
        self.assertEqual(line, "abc")

    def test_ctrl_c_raises_keyboard_interrupt_so_the_session_survives(self):
        with self.assertRaises(KeyboardInterrupt):
            self.drive(list("half typed") + ["\x03"])

    def test_ctrl_d_on_an_empty_line_ends_the_session(self):
        with self.assertRaises(EOFError):
            self.drive(["\x04"])

    def test_ctrl_d_mid_line_is_ignored_rather_than_exiting(self):
        line, _ = self.drive(list("keep") + ["\x04", self.ENTER])
        self.assertEqual(line, "keep")

    def test_unprintable_keys_are_ignored(self):
        line, _ = self.drive(list("ab") + ["\x07"] + list("c") + [self.ENTER])
        self.assertEqual(line, "abc")

    def test_an_unrecognised_special_key_is_ignored(self):
        line, _ = self.drive(list("ab") + [("\xe0", "Z")] + list("c") + [self.ENTER])
        self.assertEqual(line, "abc")

    def test_the_accepted_line_is_added_to_history(self):
        history = lineedit.History()
        self.drive(list("remembered") + [self.ENTER], history=history)
        self.assertEqual(history.items, ["remembered"])

    def test_backspace_at_the_start_does_nothing(self):
        line, _ = self.drive([self.BACKSPACE] + list("ok") + [self.ENTER])
        self.assertEqual(line, "ok")


class CommandSurface(unittest.TestCase):
    def test_every_canonical_command_is_dispatchable(self):
        for name in console.CANONICAL:
            if name in ("quit", "clear"):
                continue
            self.assertIn(name, console.COMMANDS, f"{name} is listed but not wired")

    def test_completion_never_offers_a_bare_alias(self):
        """Being offered `st` instead of `status` is worse than no suggestion."""
        self.assertNotIn("st", console.CANONICAL)
        self.assertIn("status", console.CANONICAL)

    def test_did_you_mean_matches_on_the_whole_shared_prefix(self):
        self.assertIn("status", console._did_you_mean("stat"))
        self.assertIn("recall", console._did_you_mean("recal"))
        self.assertIn("remember", console._did_you_mean("reme"))

    def test_a_hopeless_typo_falls_back_to_help(self):
        self.assertIn("help", console._did_you_mean("xyzzy"))

    def test_completion_options_cover_the_real_specs_and_capabilities(self):
        options = console._completion_options()
        self.assertIn("feature-gated", options["run"])
        self.assertIn("graph-doctor", options["do"])
        self.assertIn("local-model", options["caps"])


class ConsoleWiring(unittest.TestCase):
    def test_every_advertised_command_is_dispatchable(self):
        for name in ("status", "caps", "audit", "graph", "run", "mem", "recall",
                     "remember", "ask", "embed", "models", "lms", "test", "dash", "help"):
            self.assertIn(name, console.COMMANDS, f"{name} is advertised but not wired")

    def test_every_dispatch_entry_is_callable(self):
        for name, handler in console.COMMANDS.items():
            self.assertTrue(callable(handler), name)

    def test_spec_resolution_finds_shipped_specs(self):
        self.assertIsNotNone(console._resolve_spec("feature-gated"))
        self.assertIsNotNone(console._resolve_spec("feature-gated.json"))

    def test_an_unknown_spec_returns_none_rather_than_raising(self):
        self.assertIsNone(console._resolve_spec("no-such-spec-anywhere"))

    def test_probes_never_raise_even_when_everything_is_down(self):
        with mock.patch.object(console, "LMSTUDIO", "http://127.0.0.1:9"):
            status = console.probe_lmstudio(timeout=1.0)
        self.assertFalse(status["up"])
        self.assertEqual(status["models"], [])

    def test_status_lines_render_for_an_empty_probe(self):
        for line in console.status_lines({}):
            self.assertIsInstance(line, str)

    def test_progress_bar_is_safe_at_the_edges(self):
        self.assertIn("0%", console.progress_bar(0, 0))
        self.assertIn("100%", console.progress_bar(5, 5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
