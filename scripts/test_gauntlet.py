#!/usr/bin/env python3
"""Tests for the Gauntlet verdict protocol and router.

This module encodes Alfred's safety rules as *structure* rather than prose, so
the tests are mostly about proving the structure cannot be talked out of:

  * an unreadable gate fails CLOSED (ABORT), never open (PASS)
  * a third RETRY on the same reason code is impossible - it becomes REROUTE
  * a node that repeats an artifact is rerouted, never retried
  * a gate cannot PASS without a reason trail when it isn't a pass
  * confidence never changes routing
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gauntlet as g  # noqa: E402


# ------------------------------------------------------------------- verdicts


class VerdictContract(unittest.TestCase):
    def test_pass_needs_no_reason(self):
        self.assertTrue(g.Verdict(g.PASS).ok)

    def test_every_non_pass_verdict_must_justify_itself(self):
        for verdict in (g.RETRY, g.REROUTE, g.ESCALATE, g.ABORT):
            with self.assertRaises(g.GauntletError, msg=f"{verdict} without a reason"):
                g.Verdict(verdict)

    def test_unknown_verdict_is_rejected(self):
        with self.assertRaises(g.GauntletError):
            g.Verdict("LOOKS_FINE_TO_ME")

    def test_reason_requires_a_code(self):
        for bad in ("", "   "):
            with self.assertRaises(g.GauntletError):
                g.Reason(code=bad)

    def test_confidence_must_be_a_probability(self):
        for bad in (-0.1, 1.1):
            with self.assertRaises(g.GauntletError):
                g.Verdict(g.PASS, confidence=bad)
        g.Verdict(g.PASS, confidence=0.0)
        g.Verdict(g.PASS, confidence=1.0)

    def test_cost_cannot_be_negative(self):
        with self.assertRaises(g.GauntletError):
            g.Verdict(g.PASS, cost_usd=-1)

    def test_primary_reason_is_the_first_code(self):
        v = g.Verdict(g.RETRY, (g.Reason("TESTS_FAILED"), g.Reason("LINT")))
        self.assertEqual(v.primary_reason, "TESTS_FAILED")

    def test_round_trips_through_dict(self):
        v = g.Verdict(g.RETRY, (g.Reason("X", "detail", "evidence"),), remedy="fix", confidence=0.5)
        again = g.Verdict.from_dict(v.to_dict())
        self.assertEqual(again.verdict, v.verdict)
        self.assertEqual(again.primary_reason, "X")
        self.assertEqual(again.remedy, "fix")
        self.assertEqual(again.confidence, 0.5)


class VerdictParsing(unittest.TestCase):
    """Gates are models; their output is messy. Parsing must be forgiving of
    formatting and unforgiving of ambiguity."""

    def test_parses_a_bare_object(self):
        v = g.Verdict.parse('{"verdict":"PASS"}')
        self.assertTrue(v.ok)

    def test_parses_json_wrapped_in_prose_and_fences(self):
        raw = 'Sure! Here is my judgement:\n```json\n{"verdict":"PASS"}\n```\nHope that helps.'
        self.assertTrue(g.Verdict.parse(raw).ok)

    def test_lowercase_verdict_is_normalized(self):
        self.assertTrue(g.Verdict.parse('{"verdict":"pass"}').ok)

    def test_reasons_may_be_plain_strings(self):
        v = g.Verdict.parse('{"verdict":"RETRY","reasons":["TESTS_FAILED"]}')
        self.assertEqual(v.primary_reason, "TESTS_FAILED")

    def test_unparseable_output_aborts_rather_than_passes(self):
        """The critical fail-closed property: a broken gate is not an open gate."""
        for junk in ("", "I think it's fine", "not json at all", "{{{"):
            v = g.Verdict.parse(junk)
            self.assertEqual(v.verdict, g.ABORT, f"{junk!r} must not pass")
            self.assertFalse(v.ok)

    def test_a_retry_with_no_reasons_aborts_instead_of_crashing(self):
        v = g.Verdict.parse('{"verdict":"RETRY"}')
        self.assertEqual(v.verdict, g.ABORT)
        self.assertEqual(v.primary_reason, "GATE_INVALID")

    def test_an_unknown_verdict_string_aborts(self):
        self.assertEqual(g.Verdict.parse('{"verdict":"MAYBE"}').verdict, g.ABORT)

    def test_braces_inside_strings_do_not_confuse_the_extractor(self):
        raw = '{"verdict":"RETRY","reasons":[{"code":"X","detail":"a } brace"}]}'
        self.assertEqual(g.Verdict.parse(raw).primary_reason, "X")


# --------------------------------------------------------------------- ledger


class Ledger(unittest.TestCase):
    def test_passes_are_not_recorded(self):
        ledger = g.AttemptLedger()
        ledger.record("build", g.Verdict(g.PASS))
        self.assertEqual(ledger.entries, [])

    def test_counts_per_node_and_code(self):
        ledger = g.AttemptLedger()
        v = g.Verdict(g.RETRY, (g.Reason("TESTS_FAILED"),))
        ledger.record("build", v)
        ledger.record("build", v)
        ledger.record("other", v)
        self.assertEqual(ledger.count("build", "TESTS_FAILED"), 2)
        self.assertEqual(ledger.count("other", "TESTS_FAILED"), 1)
        self.assertEqual(ledger.count("build", "SOMETHING_ELSE"), 0)

    def test_forbids_retry_only_at_the_threshold(self):
        ledger = g.AttemptLedger()
        v = g.Verdict(g.RETRY, (g.Reason("SAME"),))
        self.assertFalse(ledger.forbids_retry("n", "SAME"))
        ledger.record("n", v)
        self.assertFalse(ledger.forbids_retry("n", "SAME"))
        ledger.record("n", v)
        self.assertTrue(ledger.forbids_retry("n", "SAME"))

    def test_a_missing_code_never_forbids(self):
        self.assertFalse(g.AttemptLedger().forbids_retry("n", None))

    def test_prompt_block_is_empty_when_nothing_failed(self):
        self.assertEqual(g.AttemptLedger().as_prompt_block("n"), "")

    def test_prompt_block_names_what_not_to_repeat(self):
        ledger = g.AttemptLedger()
        ledger.record("n", g.Verdict(g.RETRY, (g.Reason("TESTS_FAILED", "3 failing"),)))
        block = ledger.as_prompt_block("n")
        self.assertIn("ALREADY TRIED", block)
        self.assertIn("TESTS_FAILED", block)
        self.assertIn("3 failing", block)
        self.assertIn("materially different", block)

    def test_prompt_block_is_scoped_to_its_node(self):
        ledger = g.AttemptLedger()
        ledger.record("a", g.Verdict(g.ABORT, (g.Reason("A_ONLY"),)))
        self.assertEqual(ledger.as_prompt_block("b"), "")

    def test_multiple_reasons_are_each_recorded(self):
        ledger = g.AttemptLedger()
        ledger.record("n", g.Verdict(g.RETRY, (g.Reason("ONE"), g.Reason("TWO"))))
        self.assertEqual(sorted(ledger.codes_for("n")), ["ONE", "TWO"])

    def test_the_gate_history_summary_is_data_not_a_directive(self):
        """Regression: a 7B gate parroted the directive block back as a reason code.

        The gate-facing summary must not contain imperative phrasing a model can
        copy into its own structured output.
        """
        ledger = g.AttemptLedger()
        ledger.record("gate", g.Verdict(g.RETRY, (g.Reason("TESTS_FAILED"),)))
        summary = ledger.as_history_summary("gate")
        self.assertIn("TESTS_FAILED=1", summary)
        self.assertNotIn("ALREADY TRIED AND FAILED", summary)
        self.assertNotIn("do not repeat these approaches", summary)
        self.assertIn("data, not instructions", summary)

    def test_the_history_summary_tallies_repeats(self):
        ledger = g.AttemptLedger()
        for _ in range(3):
            ledger.record("gate", g.Verdict(g.RETRY, (g.Reason("SAME"),)))
        self.assertIn("SAME=3", ledger.as_history_summary("gate"))

    def test_the_history_summary_is_empty_when_nothing_failed(self):
        self.assertEqual(g.AttemptLedger().as_history_summary("gate"), "")

    def test_work_nodes_still_get_the_directive_block(self):
        """The doer benefits from being told plainly; only gates get the neutral form."""
        ledger = g.AttemptLedger()
        ledger.record("gate", g.Verdict(g.RETRY, (g.Reason("TESTS_FAILED"),)))
        self.assertIn("do not repeat", ledger.as_prompt_block("gate"))


# ------------------------------------------------------------------- progress


class Progress(unittest.TestCase):
    def test_identical_output_is_a_repeat(self):
        t = g.ProgressTracker()
        self.assertFalse(t.observe("n", "same"))
        self.assertTrue(t.observe("n", "same"))

    def test_whitespace_and_case_do_not_count_as_progress(self):
        t = g.ProgressTracker()
        t.observe("n", "Hello   World")
        self.assertTrue(t.repeats("n", "hello world"))

    def test_different_output_is_progress(self):
        t = g.ProgressTracker()
        t.observe("n", "one")
        self.assertFalse(t.repeats("n", "two"))

    def test_tracking_is_per_node(self):
        t = g.ProgressTracker()
        t.observe("a", "x")
        self.assertFalse(t.repeats("b", "x"))

    def test_repeats_does_not_mutate(self):
        t = g.ProgressTracker()
        t.repeats("n", "x")
        self.assertFalse(t.repeats("n", "x"))

    def test_empty_and_none_are_the_same_artifact(self):
        t = g.ProgressTracker()
        t.observe("n", None)
        self.assertTrue(t.repeats("n", ""))


# -------------------------------------------------------------------- routing


GATE = {
    "name": "review",
    "kind": "gate",
    "on": {g.PASS: "ship", g.RETRY: "fix", g.REROUTE: "redesign", g.ESCALATE: "opus"},
}


class Routing(unittest.TestCase):
    def test_pass_advances(self):
        r = g.route(g.Verdict(g.PASS), GATE)
        self.assertEqual((r.action, r.target), (g.ADVANCE, "ship"))
        self.assertFalse(r.forced)

    def test_retry_takes_the_remedy_edge(self):
        r = g.route(g.Verdict(g.RETRY, (g.Reason("TESTS_FAILED"),)), GATE)
        self.assertEqual((r.action, r.target), (g.REMEDY, "fix"))

    def test_an_explicit_remedy_overrides_the_default_edge(self):
        v = g.Verdict(g.RETRY, (g.Reason("X"),), remedy="hotfix")
        self.assertEqual(g.route(v, GATE).target, "hotfix")

    def test_reroute_takes_the_alternative_edge(self):
        r = g.route(g.Verdict(g.REROUTE, (g.Reason("WRONG_APPROACH"),)), GATE)
        self.assertEqual((r.action, r.target), (g.ALTERNATIVE, "redesign"))

    def test_escalate_tiers_up(self):
        r = g.route(g.Verdict(g.ESCALATE, (g.Reason("TOO_HARD"),)), GATE)
        self.assertEqual((r.action, r.target), (g.TIER_UP, "opus"))

    def test_abort_stops(self):
        r = g.route(g.Verdict(g.ABORT, (g.Reason("UNSAFE"),)), GATE)
        self.assertEqual(r.action, g.STOP)

    # -- the structural guarantees ----------------------------------------

    def test_a_third_retry_on_the_same_reason_is_forced_to_reroute(self):
        """escalation.md's anti-thrash rule, made impossible to violate."""
        ledger = g.AttemptLedger()
        v = g.Verdict(g.RETRY, (g.Reason("TESTS_FAILED"),))

        first = g.route(v, GATE, ledger)
        self.assertEqual(first.action, g.REMEDY)
        ledger.record("review", v)

        second = g.route(v, GATE, ledger)
        self.assertEqual(second.action, g.REMEDY)
        ledger.record("review", v)

        third = g.route(v, GATE, ledger)
        self.assertEqual(third.action, g.ALTERNATIVE, "a third identical retry must reroute")
        self.assertEqual(third.verdict, g.REROUTE)
        self.assertTrue(third.forced)
        self.assertIn("anti-thrash", third.reason)

    def test_a_different_reason_code_may_still_retry(self):
        """The rule targets repeating the same failure, not retrying at all."""
        ledger = g.AttemptLedger()
        old = g.Verdict(g.RETRY, (g.Reason("TESTS_FAILED"),))
        ledger.record("review", old)
        ledger.record("review", old)
        fresh = g.Verdict(g.RETRY, (g.Reason("LINT_FAILED"),))
        self.assertEqual(g.route(fresh, GATE, ledger).action, g.REMEDY)

    def test_no_progress_forces_reroute_even_on_the_first_attempt(self):
        v = g.Verdict(g.RETRY, (g.Reason("STILL_BROKEN"),))
        r = g.route(v, GATE, g.AttemptLedger(), no_progress=True)
        self.assertEqual(r.action, g.ALTERNATIVE)
        self.assertTrue(r.forced)
        self.assertIn("no progress", r.reason)

    def test_high_confidence_cannot_buy_a_pass(self):
        """A model must not be able to skip a gate by asserting confidence."""
        ledger = g.AttemptLedger()
        v = g.Verdict(g.RETRY, (g.Reason("SAME"),), confidence=1.0)
        ledger.record("review", v)
        ledger.record("review", v)
        self.assertTrue(g.route(v, GATE, ledger).forced)

    # -- missing edges fail closed ----------------------------------------

    def test_retry_without_any_edge_aborts(self):
        gate = {"name": "g", "kind": "gate", "on": {g.PASS: "next"}}
        r = g.route(g.Verdict(g.RETRY, (g.Reason("X"),)), gate)
        self.assertEqual((r.action, r.verdict), (g.STOP, g.ABORT))

    def test_anti_thrash_without_a_reroute_edge_aborts_rather_than_retrying(self):
        gate = {"name": "g", "kind": "gate", "on": {g.RETRY: "fix"}}
        ledger = g.AttemptLedger()
        v = g.Verdict(g.RETRY, (g.Reason("SAME"),))
        ledger.record("g", v)
        ledger.record("g", v)
        r = g.route(v, gate, ledger)
        self.assertEqual(r.action, g.STOP)
        self.assertTrue(r.forced)

    def test_escalate_without_an_edge_aborts(self):
        gate = {"name": "g", "kind": "gate", "on": {g.PASS: "n"}}
        r = g.route(g.Verdict(g.ESCALATE, (g.Reason("X"),)), gate)
        self.assertEqual(r.verdict, g.ABORT)

    def test_routing_is_serializable_for_the_audit_trail(self):
        payload = g.route(g.Verdict(g.PASS), GATE).to_dict()
        self.assertEqual(
            set(payload), {"action", "target", "verdict", "reason", "forced"}
        )


class LadderClimbs(unittest.TestCase):
    """RETRY -> REROUTE -> ESCALATE -> ABORT. Every rung is bounded, so the engine
    cannot trade a retry loop for a reroute loop or thrash on the priciest tier."""

    def _exhaust_retries(self, ledger, node="review", code="SAME"):
        v = g.Verdict(g.RETRY, (g.Reason(code),))
        for _ in range(g.MAX_SAME_REASON_RETRIES):
            ledger.record(node, v)
        return v

    def test_reroute_is_bounded_and_then_escalates(self):
        ledger = g.AttemptLedger()
        v = self._exhaust_retries(ledger)
        for _ in range(g.MAX_SAME_REASON_REROUTES):
            ledger.record_reroute("review", "SAME")
        r = g.route(v, GATE, ledger)
        self.assertEqual(r.action, g.TIER_UP)
        self.assertEqual(r.verdict, g.ESCALATE)
        self.assertTrue(r.forced)

    def test_escalation_is_bounded_and_then_aborts(self):
        """The top tier is the worst place to loop - it is the most expensive."""
        ledger = g.AttemptLedger()
        v = self._exhaust_retries(ledger)
        for _ in range(g.MAX_SAME_REASON_REROUTES):
            ledger.record_reroute("review", "SAME")
        for _ in range(g.MAX_SAME_REASON_ESCALATIONS):
            ledger.record_escalation("review", "SAME")
        r = g.route(v, GATE, ledger)
        self.assertEqual(r.action, g.STOP)
        self.assertEqual(r.verdict, g.ABORT)
        self.assertIn("partial result", r.reason)

    def test_a_gate_requested_escalation_is_also_bounded(self):
        ledger = g.AttemptLedger()
        ledger.record_escalation("review", "TOO_HARD")
        v = g.Verdict(g.ESCALATE, (g.Reason("TOO_HARD"),))
        r = g.route(v, GATE, ledger)
        self.assertEqual(r.action, g.STOP)
        self.assertIn("known-failing", r.reason)

    def test_a_first_escalation_is_still_allowed(self):
        v = g.Verdict(g.ESCALATE, (g.Reason("TOO_HARD"),))
        self.assertEqual(g.route(v, GATE, g.AttemptLedger()).action, g.TIER_UP)

    def test_reroute_counters_are_scoped_per_reason(self):
        ledger = g.AttemptLedger()
        for _ in range(g.MAX_SAME_REASON_REROUTES):
            ledger.record_reroute("review", "ONE")
        self.assertTrue(ledger.forbids_reroute("review", "ONE"))
        self.assertFalse(ledger.forbids_reroute("review", "TWO"))

    def test_a_none_code_never_blocks_a_rung(self):
        ledger = g.AttemptLedger()
        ledger.record_reroute("review", None)
        ledger.record_escalation("review", None)
        self.assertFalse(ledger.forbids_reroute("review", None))
        self.assertFalse(ledger.forbids_escalation("review", None))

    def test_the_whole_ladder_terminates_on_a_permanently_failing_gate(self):
        """End to end: a gate that always fails the same way must stop, not spin."""
        retry = '{"verdict":"RETRY","reasons":[{"code":"TESTS_FAILED"}]}'
        counter = {"n": 0}

        def executor(agent, task, timeout=None):
            if agent == "alfred-reviewer":
                return retry
            counter["n"] += 1
            return f"distinct {counter['n']}"

        spec = linear_spec()
        spec["nodes"][1]["on"][g.ESCALATE] = "deep"
        spec["nodes"].append({"name": "deep", "agent": "alfred-security", "task": "review hard"})

        result = g.run_gauntlet(spec, "t", executor, max_node_runs=200)
        self.assertEqual(result.status, g.ABORTED,
                         "the ladder must abort, not exhaust the budget")
        self.assertLess(len(result.runs), 200, "it must stop well before the cap")
        verdicts = [r.routing["verdict"] for r in result.runs if r.routing]
        self.assertIn(g.REROUTE, verdicts)
        self.assertIn(g.ESCALATE, verdicts)
        self.assertEqual(verdicts[-1], g.ABORT)


# ----------------------------------------------------------------- validation


def spec(*nodes, **extra):
    return {"schema": g.SCHEMA, "nodes": list(nodes), **extra}


WORK = {"name": "build", "kind": "work", "agent": "alfred-coder"}


class SpecValidation(unittest.TestCase):
    def test_a_legacy_workflow_spec_is_left_alone(self):
        """Migration must be additive: the loop engine keeps working."""
        self.assertEqual(g.validate_spec({"stages": [{"name": "a"}]}), [])

    def test_a_minimal_spec_is_valid(self):
        self.assertEqual(g.validate_spec(spec(WORK)), [])

    def test_nodes_are_required(self):
        self.assertIn("spec has no nodes", g.validate_spec(spec()))

    def test_duplicate_names_are_rejected(self):
        errors = g.validate_spec(spec(WORK, dict(WORK)))
        self.assertTrue(any("duplicate" in e for e in errors))

    def test_a_node_needs_an_agent(self):
        errors = g.validate_spec(spec({"name": "x", "kind": "work"}))
        self.assertTrue(any("missing agent" in e for e in errors))

    def test_an_unknown_agent_is_rejected_when_the_roster_is_known(self):
        errors = g.validate_spec(spec(WORK), known_agents=["someone-else"])
        self.assertTrue(any("unknown agent" in e for e in errors))

    def test_a_gate_needs_routing(self):
        errors = g.validate_spec(spec({"name": "gate", "kind": "gate", "agent": "a"}))
        self.assertTrue(any("needs an 'on' map" in e for e in errors))

    def test_a_retry_edge_without_a_reroute_edge_is_rejected(self):
        """Otherwise the anti-thrash rule could only abort - a design trap."""
        errors = g.validate_spec(spec(
            WORK,
            {"name": "gate", "kind": "gate", "agent": "a",
             "on": {g.RETRY: "build"}},
        ))
        self.assertTrue(any("no REROUTE edge" in e for e in errors), errors)

    def test_routing_to_an_unknown_node_is_rejected(self):
        errors = g.validate_spec(spec(
            {"name": "gate", "kind": "gate", "agent": "a",
             "on": {g.PASS: "nowhere", g.REROUTE: "nowhere"}},
        ))
        self.assertTrue(any("unknown node" in e for e in errors))

    def test_an_invalid_verdict_key_is_rejected(self):
        errors = g.validate_spec(spec(
            WORK,
            {"name": "gate", "kind": "gate", "agent": "a", "on": {"MAYBE": "build"}},
        ))
        self.assertTrue(any("is not a verdict" in e for e in errors))

    def test_unknown_kind_is_rejected(self):
        errors = g.validate_spec(spec({"name": "x", "kind": "wat", "agent": "a"}))
        self.assertTrue(any("unknown kind" in e for e in errors))

    def test_depends_on_must_reference_a_real_node(self):
        errors = g.validate_spec(spec({**WORK, "depends_on": ["ghost"]}))
        self.assertTrue(any("unknown node" in e for e in errors))

    def test_dependency_cycles_are_rejected(self):
        errors = g.validate_spec(spec(
            {"name": "a", "agent": "x", "depends_on": ["b"]},
            {"name": "b", "agent": "x", "depends_on": ["a"]},
        ))
        self.assertTrue(any("cycle" in e for e in errors), errors)

    def test_gate_back_edges_are_not_cycles(self):
        """A gate routing back to an earlier node is the whole point."""
        errors = g.validate_spec(spec(
            WORK,
            {"name": "gate", "kind": "gate", "agent": "a", "depends_on": ["build"],
             "on": {g.PASS: "build", g.RETRY: "build", g.REROUTE: "build"}},
        ))
        self.assertEqual(errors, [])

    def test_compensation_must_be_a_real_harness_capability(self):
        errors = g.validate_spec(
            spec({**WORK, "compensate": "rm-rf-everything"}),
            known_capabilities=["git-commit"],
        )
        self.assertTrue(any("not a harness capability" in e for e in errors))

    def test_a_valid_compensator_is_accepted(self):
        errors = g.validate_spec(
            spec({**WORK, "compensate": "git-commit"}),
            known_capabilities=["git-commit"],
        )
        self.assertEqual(errors, [])

    def test_timeout_must_be_positive(self):
        for bad in (0, -5, "soon"):
            errors = g.validate_spec(spec({**WORK, "timeout": bad}))
            self.assertTrue(any("timeout" in e for e in errors), bad)

    def test_budget_values_must_be_positive_numbers(self):
        errors = g.validate_spec(spec(WORK, budget={"maxNodeRuns": 0}))
        self.assertTrue(any("maxNodeRuns" in e for e in errors))

    def test_a_valid_budget_is_accepted(self):
        self.assertEqual(
            g.validate_spec(spec(WORK, budget={"maxNodeRuns": 10, "maxUsdEstimate": 1.5})), []
        )


class GatePrompt(unittest.TestCase):
    def test_prompt_carries_criteria_and_artifact(self):
        text = g.build_gate_prompt("must compile", "the code")
        self.assertIn("must compile", text)
        self.assertIn("the code", text)
        for verdict in g.VERDICTS:
            self.assertIn(verdict, text)

    def test_missing_inputs_degrade_readably(self):
        text = g.build_gate_prompt("", "")
        self.assertIn("(none supplied)", text)
        self.assertIn("(no output)", text)

    def test_the_ledger_block_is_included_when_present(self):
        text = g.build_gate_prompt("c", "a", "ALREADY TRIED: X")
        self.assertIn("ALREADY TRIED: X", text)


# -------------------------------------------------------------------- execution


def scripted(*responses):
    """An executor that returns queued responses, then repeats the last one."""
    queue = list(responses)
    calls: list[tuple[str, str]] = []

    def executor(agent, task, timeout=None):
        calls.append((agent, task))
        return queue.pop(0) if len(queue) > 1 else (queue[0] if queue else "")

    executor.calls = calls
    return executor


PASS_JSON = '{"verdict":"PASS"}'


def linear_spec(**gate_extra):
    """build -> review(gate) -> ship, with fix/redesign remedies available."""
    gate = {
        "name": "review", "kind": "gate", "agent": "alfred-reviewer",
        "depends_on": ["build"], "criteria": "it must work",
        "on": {g.PASS: "ship", g.RETRY: "fix", g.REROUTE: "redesign"},
        **gate_extra,
    }
    return {
        "schema": g.SCHEMA,
        "nodes": [
            {"name": "build", "agent": "alfred-coder", "task": "build it"},
            gate,
            {"name": "fix", "agent": "alfred-debugger", "task": "fix it"},
            {"name": "redesign", "agent": "alfred-architect", "task": "rethink it"},
            {"name": "ship", "agent": "alfred-devops", "task": "ship it"},
        ],
    }


class ExecutionOrder(unittest.TestCase):
    def test_dependencies_come_first(self):
        order = g.execution_order([
            {"name": "b", "depends_on": ["a"]},
            {"name": "a"},
        ])
        self.assertLess(order.index("a"), order.index("b"))

    def test_a_cycle_is_reported(self):
        with self.assertRaises(g.GauntletError):
            g.execution_order([
                {"name": "a", "depends_on": ["b"]},
                {"name": "b", "depends_on": ["a"]},
            ])


class RunnerHappyPath(unittest.TestCase):
    def test_a_passing_gate_advances_to_its_pass_target(self):
        result = g.run_gauntlet(linear_spec(), "task", scripted("built", PASS_JSON, "shipped"))
        self.assertTrue(result.ok)
        self.assertEqual(result.status, g.PASSED)
        visited = [r.node for r in result.runs]
        self.assertEqual(visited, ["build", "review", "ship"])
        self.assertNotIn("fix", visited, "a passing gate must not run the remedy")

    def test_the_trail_is_serializable(self):
        result = g.run_gauntlet(linear_spec(), "t", scripted("built", PASS_JSON, "ok"))
        import json as _json
        _json.dumps(result.to_dict())
        self.assertEqual(result.to_dict()["schema"], g.SCHEMA)

    def test_a_malformed_spec_is_refused_before_anything_runs(self):
        executor = scripted("never")
        with self.assertRaises(g.GauntletError):
            g.run_gauntlet({"schema": g.SCHEMA, "nodes": []}, "t", executor)
        self.assertEqual(executor.calls, [])

    def test_work_nodes_receive_the_overall_task_when_they_declare_none(self):
        spec = {"schema": g.SCHEMA, "nodes": [{"name": "solo", "agent": "alfred-coder"}]}
        executor = scripted("done")
        g.run_gauntlet(spec, "the objective", executor)
        self.assertIn("the objective", executor.calls[0][1])


class RunnerRouting(unittest.TestCase):
    def test_a_retry_verdict_runs_the_remedy_node(self):
        retry = '{"verdict":"RETRY","reasons":[{"code":"TESTS_FAILED"}]}'
        # build, gate->RETRY, fix, gate->PASS, ship
        executor = scripted("built", retry, "fixed", PASS_JSON, "shipped")
        result = g.run_gauntlet(linear_spec(), "t", executor)
        visited = [r.node for r in result.runs]
        self.assertIn("fix", visited)
        self.assertTrue(result.ok)

    def test_an_unreadable_gate_aborts_the_run(self):
        """Fail closed: a gate we cannot read is not a gate we passed."""
        result = g.run_gauntlet(linear_spec(), "t", scripted("built", "looks good to me!"))
        self.assertEqual(result.status, g.ABORTED)
        self.assertNotIn("ship", [r.node for r in result.runs])

    def test_an_abort_verdict_stops_immediately(self):
        abort = '{"verdict":"ABORT","reasons":[{"code":"UNSAFE"}]}'
        result = g.run_gauntlet(linear_spec(), "t", scripted("built", abort))
        self.assertEqual(result.status, g.ABORTED)
        self.assertIn("UNSAFE", result.reason)

    def test_escalate_without_an_edge_aborts_rather_than_guessing(self):
        escalate = '{"verdict":"ESCALATE","reasons":[{"code":"TOO_HARD"}]}'
        result = g.run_gauntlet(linear_spec(), "t", scripted("built", escalate))
        self.assertEqual(result.status, g.ABORTED)

    def test_the_anti_thrash_rule_switches_to_the_alternative_branch(self):
        """The headline behaviour: identical failures cannot loop forever."""
        retry = '{"verdict":"RETRY","reasons":[{"code":"TESTS_FAILED"}]}'

        def executor(agent, task, timeout=None):
            # Every gate call returns the SAME failure; work nodes vary output so
            # the no-progress detector is not what stops it.
            if agent == "alfred-reviewer":
                return retry
            executor.n = getattr(executor, "n", 0) + 1
            return f"attempt {executor.n}"

        result = g.run_gauntlet(linear_spec(), "t", executor, max_node_runs=25)
        visited = [r.node for r in result.runs]
        self.assertIn("redesign", visited, "a third identical failure must reroute")
        forced = [r for r in result.runs if (r.routing or {}).get("forced")]
        self.assertTrue(forced, "the reroute must be recorded as forced")
        self.assertEqual(forced[0]["routing"]["verdict"] if isinstance(forced[0], dict)
                         else forced[0].routing["verdict"], g.REROUTE)

    def test_unchanged_output_reroutes_even_before_the_retry_limit(self):
        """The no-progress detector: identical artifact twice is not progress."""
        retry = '{"verdict":"RETRY","reasons":[{"code":"STILL_BROKEN"}]}'

        def executor(agent, task, timeout=None):
            return retry if agent == "alfred-reviewer" else "IDENTICAL OUTPUT"

        result = g.run_gauntlet(linear_spec(), "t", executor, max_node_runs=25)
        self.assertIn("redesign", [r.node for r in result.runs])


class RunnerBudgets(unittest.TestCase):
    def test_the_node_run_budget_stops_a_runaway_graph(self):
        retry = '{"verdict":"RETRY","reasons":[{"code":"X"}]}'
        spec = linear_spec()
        # Route REROUTE back to build so the graph would otherwise never settle.
        spec["nodes"][1]["on"][g.REROUTE] = "build"

        def executor(agent, task, timeout=None):
            executor.n = getattr(executor, "n", 0) + 1
            return retry if agent == "alfred-reviewer" else f"out {executor.n}"

        result = g.run_gauntlet(spec, "t", executor, max_node_runs=6)
        self.assertEqual(result.status, g.EXHAUSTED)
        self.assertLessEqual(len(result.runs), 6)

    def test_budget_from_the_spec_is_honoured(self):
        spec = linear_spec()
        spec["budget"] = {"maxNodeRuns": 1}
        result = g.run_gauntlet(spec, "t", scripted("built", PASS_JSON, "ship"))
        self.assertEqual(result.status, g.EXHAUSTED)

    def test_the_cost_budget_is_checked_before_spending_more(self):
        def executor(agent, task, timeout=None):
            executor.last_meta = {"cost_usd": 0.5}
            return PASS_JSON if agent == "alfred-reviewer" else "out"

        result = g.run_gauntlet(linear_spec(), "t", executor, max_usd=0.6)
        self.assertEqual(result.status, g.EXHAUSTED)
        self.assertIn("cost budget", result.reason)

    def test_cost_is_accumulated_onto_the_result(self):
        def executor(agent, task, timeout=None):
            executor.last_meta = {"cost_usd": 0.25}
            return PASS_JSON if agent == "alfred-reviewer" else "out"

        result = g.run_gauntlet(linear_spec(), "t", executor)
        self.assertGreater(result.cost_usd, 0)


class RunnerResilience(unittest.TestCase):
    def test_an_executor_exception_becomes_output_not_a_crash(self):
        def boom(agent, task, timeout=None):
            if agent == "alfred-coder":
                raise RuntimeError("backend died")
            return PASS_JSON

        result = g.run_gauntlet(linear_spec(), "t", boom)
        first = result.runs[0]
        self.assertEqual(first.status, "error")
        self.assertIn("backend died", first.output)

    def test_the_ledger_block_is_injected_into_the_remedy_prompt(self):
        retry = '{"verdict":"RETRY","reasons":[{"code":"TESTS_FAILED","detail":"3 failing"}]}'
        executor = scripted("built", retry, "fixed", PASS_JSON, "shipped")
        g.run_gauntlet(linear_spec(), "t", executor)
        # The gate judges 'build', so build's ledger carries the failure. Assert
        # some later prompt was told what already failed.
        prompts = [task for _, task in executor.calls]
        self.assertTrue(any("ALREADY TRIED" in p for p in prompts),
                        "a later attempt must be told what already failed")


class RunnerCompensation(unittest.TestCase):
    def test_compensators_run_in_reverse_order_on_abort(self):
        spec = linear_spec()
        spec["nodes"][0]["compensate"] = "git-status"
        abort = '{"verdict":"ABORT","reasons":[{"code":"UNSAFE"}]}'
        undone: list[str] = []
        result = g.run_gauntlet(
            spec, "t", scripted("built", abort),
            compensator=lambda capability, node: undone.append(f"{node}:{capability}"),
        )
        self.assertEqual(result.status, g.ABORTED)
        self.assertEqual(undone, ["build:git-status"])
        self.assertIn("build:git-status:ok", result.compensated)

    def test_a_missing_compensator_is_recorded_not_silently_skipped(self):
        spec = linear_spec()
        spec["nodes"][0]["compensate"] = "git-status"
        abort = '{"verdict":"ABORT","reasons":[{"code":"X"}]}'
        result = g.run_gauntlet(spec, "t", scripted("built", abort))
        self.assertTrue(any("NOT_RUN" in c for c in result.compensated))

    def test_a_failing_compensator_is_reported_rather_than_masked(self):
        spec = linear_spec()
        spec["nodes"][0]["compensate"] = "git-status"
        abort = '{"verdict":"ABORT","reasons":[{"code":"X"}]}'

        def bad(capability, node):
            raise OSError("rollback failed")

        result = g.run_gauntlet(spec, "t", scripted("built", abort), compensator=bad)
        self.assertTrue(any("FAILED(OSError)" in c for c in result.compensated))

    def test_nothing_is_compensated_on_success(self):
        spec = linear_spec()
        spec["nodes"][0]["compensate"] = "git-status"
        undone: list[str] = []
        result = g.run_gauntlet(spec, "t", scripted("built", PASS_JSON, "shipped"),
                                compensator=lambda c, n: undone.append(n))
        self.assertTrue(result.ok)
        self.assertEqual(undone, [])


class HarnessBackedCompensation(unittest.TestCase):
    """Rollback must inherit the signed policy, not get a private path."""

    def test_the_factory_returns_a_callable(self):
        self.assertTrue(callable(g.harness_compensator()))

    def test_a_dry_run_rollback_succeeds_against_the_real_harness(self):
        """Uses --dry-run so nothing is actually executed, but the whole path -
        policy load, signature check, caller allowlist, parameter validation -
        is exercised against the real signed policy."""
        compensate = g.harness_compensator("owner", dry_run=True)
        compensate("git-status", "build", {"path": str(ROOT)})  # must not raise

    def test_a_missing_required_parameter_is_reported_not_swallowed(self):
        compensate = g.harness_compensator("owner", dry_run=True)
        with self.assertRaises(g.GauntletError) as ctx:
            compensate("git-status", "build")  # git-status requires `path`
        self.assertIn("git-status", str(ctx.exception))

    def test_node_declared_params_reach_the_capability(self):
        spec = linear_spec()
        spec["nodes"][0]["compensate"] = "git-status"
        spec["nodes"][0]["compensateParams"] = {"path": str(ROOT)}
        abort = '{"verdict":"ABORT","reasons":[{"code":"X"}]}'
        result = g.run_gauntlet(spec, "t", scripted("built", abort),
                                compensator=g.harness_compensator("owner", dry_run=True))
        self.assertEqual(result.status, g.ABORTED)
        self.assertIn("build:git-status:ok", result.compensated)

    def test_a_capability_the_policy_forbids_raises(self):
        compensate = g.harness_compensator("owner", dry_run=True)
        with self.assertRaises(g.GauntletError):
            compensate("rm-rf-everything", "build")

    def test_an_untrusted_caller_cannot_roll_back(self):
        """local-model must not be able to trigger a mutating undo."""
        compensate = g.harness_compensator("local-model", dry_run=True)
        with self.assertRaises(g.GauntletError):
            compensate("git-commit", "build")

    def test_a_failing_rollback_is_reported_through_the_result(self):
        spec = linear_spec()
        spec["nodes"][0]["compensate"] = "git-status"
        abort = '{"verdict":"ABORT","reasons":[{"code":"X"}]}'
        result = g.run_gauntlet(
            spec, "t", scripted("built", abort),
            compensator=g.harness_compensator("local-model", dry_run=True),
        )
        self.assertEqual(result.status, g.ABORTED)
        self.assertTrue(any("FAILED" in c for c in result.compensated),
                        f"a denied rollback must be reported: {result.compensated}")


class RenamedFailuresCannotEscape(unittest.TestCase):
    """Regression for the worst bug found in this engine.

    Every other bound keys on (gate, reason_code). A real 7B gate invented a new
    code per attempt, so the counters never tripped and the run spun to the budget
    cap with ZERO forced reroutes. Two independent defences now exist: a
    code-independent rejection cap, and folding unknown codes into OTHER.
    """

    def test_the_rejection_cap_is_code_independent(self):
        ledger = g.AttemptLedger()
        for index in range(g.MAX_GATE_REJECTIONS):
            ledger.record("review", g.Verdict(g.RETRY, (g.Reason(f"NOVEL_{index}"),)))
        self.assertTrue(ledger.exhausted("review"))
        # Not one code repeated, yet the ladder must still climb.
        routing = g.route(g.Verdict(g.RETRY, (g.Reason("BRAND_NEW"),)), GATE, ledger)
        self.assertEqual(routing.action, g.ALTERNATIVE)
        self.assertTrue(routing.forced)
        self.assertIn("regardless of reason code", routing.reason)

    def test_rejections_are_counted_per_gate(self):
        ledger = g.AttemptLedger()
        for index in range(g.MAX_GATE_REJECTIONS):
            ledger.record("review", g.Verdict(g.RETRY, (g.Reason(f"C{index}"),)))
        self.assertTrue(ledger.exhausted("review"))
        self.assertFalse(ledger.exhausted("other-gate"))

    def test_under_the_cap_a_novel_code_may_still_retry(self):
        ledger = g.AttemptLedger()
        ledger.record("review", g.Verdict(g.RETRY, (g.Reason("ONE"),)))
        routing = g.route(g.Verdict(g.RETRY, (g.Reason("TWO"),)), GATE, ledger)
        self.assertEqual(routing.action, g.REMEDY)

    def test_unknown_codes_are_folded_into_other(self):
        verdict = g.Verdict(g.RETRY, (g.Reason("INVENTED_NONSENSE", "detail here"),))
        folded = g.normalize_verdict(verdict, g.DEFAULT_REASON_CODES)
        self.assertEqual(folded.primary_reason, "OTHER")
        self.assertIn("INVENTED_NONSENSE", folded.reasons[0].detail,
                      "the original label must survive as detail")

    def test_known_codes_pass_through_untouched(self):
        verdict = g.Verdict(g.RETRY, (g.Reason("TESTS_FAILED"),))
        self.assertIs(g.normalize_verdict(verdict, g.DEFAULT_REASON_CODES), verdict)

    def test_normalisation_is_case_insensitive(self):
        verdict = g.Verdict(g.RETRY, (g.Reason("tests_failed"),))
        self.assertEqual(g.normalize_verdict(verdict, g.DEFAULT_REASON_CODES).primary_reason,
                         "tests_failed", "an existing code differing only in case is kept")

    def test_no_vocabulary_means_no_normalisation(self):
        verdict = g.Verdict(g.RETRY, (g.Reason("ANYTHING"),))
        self.assertIs(g.normalize_verdict(verdict, None), verdict)

    def test_folding_makes_repeated_failures_visible_to_the_counter(self):
        """The end-to-end point: distinct labels for one failure now converge."""
        ledger = g.AttemptLedger()
        for label in ("PROBLEM_A", "PROBLEM_B", "PROBLEM_C"):
            folded = g.normalize_verdict(
                g.Verdict(g.RETRY, (g.Reason(label),)), g.DEFAULT_REASON_CODES)
            ledger.record("review", folded)
        self.assertTrue(ledger.forbids_retry("review", "OTHER"),
                        "folded codes must accumulate against one counter")

    def test_a_gate_that_renames_failures_now_terminates(self):
        """End to end against the real engine, with a deliberately shifty gate."""
        counter = {"n": 0}

        def shifty(agent, task, timeout=None):
            if agent == "alfred-reviewer":
                counter["n"] += 1
                return ('{"verdict":"RETRY","reasons":[{"code":"VARIANT_'
                        f'{counter["n"]}"' + '}]}')
            return f"attempt {counter['n']}"

        spec = linear_spec()
        result = g.run_gauntlet(spec, "t", shifty, max_node_runs=40)
        self.assertNotEqual(result.status, g.EXHAUSTED,
                            "renaming failures must not buy unlimited retries")
        forced = [r for r in result.runs if (r.routing or {}).get("forced")]
        self.assertTrue(forced, "the ladder must have been forced at least once")
        self.assertLess(len(result.runs), 40)

    def test_the_gate_prompt_lists_the_allowed_codes(self):
        prompt = g.build_gate_prompt("criteria", "artifact", "",
                                     ["TESTS_FAILED", "OTHER"])
        self.assertIn("USE ONLY THESE REASON CODES", prompt)
        self.assertIn("TESTS_FAILED", prompt)
        self.assertIn("Do not invent new codes", prompt)

    def test_the_prompt_omits_the_vocabulary_when_none_is_given(self):
        self.assertNotIn("USE ONLY THESE",
                         g.build_gate_prompt("criteria", "artifact"))

    def test_engine_codes_are_never_folded_into_other(self):
        """Regression: a 242s gate timeout was reported as "ABORTED OTHER".

        Folding engine diagnostics into the model's bucket made an infrastructure
        failure read as a model judgement, erasing the only clue.
        """
        for code in g.ENGINE_REASON_CODES:
            verdict = g.Verdict(g.ABORT, (g.Reason(code, "the machinery failed"),))
            folded = g.normalize_verdict(verdict, g.DEFAULT_REASON_CODES)
            self.assertEqual(folded.primary_reason, code,
                             f"{code} must survive normalisation")

    def test_an_unparseable_gate_keeps_its_diagnostic_code(self):
        verdict = g.normalize_verdict(g.Verdict.parse("not json at all"),
                                      g.DEFAULT_REASON_CODES)
        self.assertEqual(verdict.verdict, g.ABORT)
        self.assertIn(verdict.primary_reason, g.ENGINE_REASON_CODES)


class GateInputIsBounded(unittest.TestCase):
    """A gate is a classifier, not a reader; unbounded input made it the slowest node."""

    def test_a_short_artifact_is_untouched(self):
        self.assertEqual(g.clip_artifact("short"), "short")

    def test_a_long_artifact_is_clipped_to_the_limit(self):
        clipped = g.clip_artifact("x" * 10_000, limit=1000)
        self.assertLessEqual(len(clipped), 1000 + 80)  # plus the elision marker

    def test_clipping_keeps_the_head_and_the_tail(self):
        text = "HEAD-EVIDENCE" + ("m" * 5000) + "TAIL-EXIT-CODE-0"
        clipped = g.clip_artifact(text, limit=600)
        self.assertIn("HEAD-EVIDENCE", clipped)
        self.assertIn("TAIL-EXIT-CODE-0", clipped, "concluding evidence must survive")

    def test_the_elision_is_declared_not_silent(self):
        clipped = g.clip_artifact("y" * 5000, limit=500)
        self.assertIn("elided", clipped)

    def test_the_gate_prompt_clips_its_artifact(self):
        prompt = g.build_gate_prompt("criteria", "z" * 20_000)
        self.assertLess(len(prompt), 20_000, "the prompt must not carry the whole artifact")
        self.assertIn("elided", prompt)


class EchoExecutor(unittest.TestCase):
    def test_gate_agents_receive_parseable_json(self):
        self.assertTrue(g.Verdict.parse(g.echo_executor("alfred-reviewer", "x")).ok)

    def test_work_agents_get_an_echo(self):
        self.assertIn("hello", g.echo_executor("alfred-coder", "hello"))

    def test_the_default_executor_completes_a_graph(self):
        result = g.run_gauntlet(linear_spec(), "task")
        self.assertTrue(result.ok, result.reason)

    def test_failure_prefixes_are_detected(self):
        self.assertTrue(g.is_failure("[ERROR] boom"))
        self.assertTrue(g.is_failure("[TIMEOUT] slow"))
        self.assertFalse(g.is_failure("fine"))
        self.assertFalse(g.is_failure(None))


# ----------------------------------------------------------------- checkpoints


class CheckpointStore(unittest.TestCase):
    def setUp(self):
        self.cp = g.Checkpointer(":memory:")

    def tearDown(self):
        self.cp.close()

    def test_a_run_can_be_started_and_found(self):
        self.cp.begin("r1", {"name": "demo"}, "task")
        row = self.cp.run("r1")
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["workflow"], "demo")

    def test_the_latest_checkpoint_wins(self):
        self.cp.begin("r1", {"name": "demo"}, "t")
        self.cp.save("r1", 1, {"pointer": 1})
        self.cp.save("r1", 2, {"pointer": 5})
        self.assertEqual(self.cp.latest("r1")["pointer"], 5)

    def test_latest_is_none_for_an_unknown_run(self):
        self.assertIsNone(self.cp.latest("nope"))

    def test_beginning_twice_does_not_lose_the_creation_time(self):
        self.cp.begin("r1", {"name": "demo"}, "t")
        first = self.cp.run("r1")["created_ts"]
        self.cp.begin("r1", {"name": "demo"}, "t")
        self.assertEqual(self.cp.run("r1")["created_ts"], first)

    def test_finishing_records_status_and_reason(self):
        self.cp.begin("r1", {"name": "demo"}, "t")
        self.cp.finish("r1", g.ABORTED, "because")
        row = self.cp.run("r1")
        self.assertEqual((row["status"], row["reason"]), (g.ABORTED, "because"))

    def test_resumable_lists_only_unfinished_runs(self):
        self.cp.begin("done", {"name": "d"}, "t")
        self.cp.finish("done", g.PASSED, "ok")
        self.cp.begin("parked", {"name": "p"}, "t")
        self.cp.finish("parked", g.INTERRUPTED, "awaiting")
        self.cp.begin("live", {"name": "l"}, "t")
        ids = {r["run_id"] for r in self.cp.resumable()}
        self.assertEqual(ids, {"parked", "live"})

    def test_prune_drops_old_finished_runs(self):
        self.cp.begin("old", {"name": "o"}, "t")
        self.cp.save("old", 1, {"pointer": 0})
        self.cp.finish("old", g.PASSED, "ok")
        self.cp.con.execute("UPDATE gx_run SET updated_ts=? WHERE run_id='old'",
                            (time.time() - 40 * 86400,))
        self.cp.con.commit()
        self.assertEqual(self.cp.prune(older_than_days=14), 1)
        self.assertIsNone(self.cp.run("old"))

    def test_prune_keeps_unfinished_runs_however_old(self):
        """An interrupted run is waiting on a human; it must not be swept away."""
        self.cp.begin("parked", {"name": "p"}, "t")
        self.cp.finish("parked", g.INTERRUPTED, "awaiting")
        self.cp.con.execute("UPDATE gx_run SET updated_ts=? WHERE run_id='parked'",
                            (time.time() - 90 * 86400,))
        self.cp.con.commit()
        self.assertEqual(self.cp.prune(older_than_days=14), 0)
        self.assertIsNotNone(self.cp.run("parked"))

    def test_tuple_keys_survive_serialization(self):
        original = {("gate", "CODE"): 2}
        self.assertEqual(g._decode_keys(g._encode_keys(original)), original)


class ResumeSkipsCompletedWork(unittest.TestCase):
    """The economic point: a crash must not re-bill completed LLM calls."""

    def setUp(self):
        self.cp = g.Checkpointer(":memory:")

    def tearDown(self):
        self.cp.close()

    def test_a_crashed_run_resumes_without_repeating_nodes(self):
        calls: list[str] = []
        boom = {"explode": True}

        def executor(agent, task, timeout=None):
            calls.append(agent)
            if agent == "alfred-tester" and boom["explode"]:
                raise KeyboardInterrupt("simulated crash")
            return PASS_JSON if agent == "alfred-reviewer" else f"out-{agent}"

        spec = {
            "schema": g.SCHEMA,
            "nodes": [
                {"name": "plan", "agent": "alfred-planner"},
                {"name": "build", "agent": "alfred-coder", "depends_on": ["plan"]},
                {"name": "test", "agent": "alfred-tester", "depends_on": ["build"]},
            ],
        }
        with self.assertRaises(KeyboardInterrupt):
            g.run_gauntlet(spec, "t", executor, checkpointer=self.cp, run_id="crash1")
        self.assertEqual(calls, ["alfred-planner", "alfred-coder", "alfred-tester"])

        boom["explode"] = False
        calls.clear()
        result = g.run_gauntlet(spec, "t", executor, checkpointer=self.cp,
                                run_id="crash1", resume=True)
        self.assertTrue(result.ok, result.reason)
        self.assertNotIn("alfred-planner", calls, "completed work must not be re-run")
        self.assertNotIn("alfred-coder", calls, "completed work must not be re-run")
        self.assertIn("alfred-tester", calls, "the crashed node must be retried")

    def test_resume_preserves_cost_already_spent(self):
        def executor(agent, task, timeout=None):
            executor.last_meta = {"cost_usd": 0.1}
            return PASS_JSON if agent == "alfred-reviewer" else "out"

        spec = {"schema": g.SCHEMA, "nodes": [
            {"name": "a", "agent": "alfred-coder"},
            {"name": "b", "agent": "alfred-coder", "depends_on": ["a"]},
        ]}
        g.run_gauntlet(spec, "t", executor, checkpointer=self.cp, run_id="cost1")
        saved = self.cp.latest("cost1")
        self.assertGreater(saved["costUsd"], 0)

        resumed = g.run_gauntlet(spec, "t", executor, checkpointer=self.cp,
                                 run_id="cost1", resume=True)
        self.assertGreaterEqual(resumed.cost_usd, saved["costUsd"],
                                "spend already incurred must carry forward")

    def test_resume_restores_the_attempt_ledger(self):
        """Otherwise the anti-thrash counters reset and the loop restarts."""
        retry = '{"verdict":"RETRY","reasons":[{"code":"TESTS_FAILED"}]}'
        n = {"i": 0}

        def executor(agent, task, timeout=None):
            if agent == "alfred-reviewer":
                return retry
            n["i"] += 1
            return f"out {n['i']}"

        g.run_gauntlet(linear_spec(), "t", executor, checkpointer=self.cp,
                       run_id="ledger1", max_node_runs=5)
        saved = self.cp.latest("ledger1")
        self.assertTrue(saved["ledger"]["entries"], "the ledger must be persisted")

        resumed = g.run_gauntlet(linear_spec(), "t", executor, checkpointer=self.cp,
                                 run_id="ledger1", resume=True, max_node_runs=30)
        self.assertTrue(resumed.ledger.entries)

    def test_resuming_an_unknown_run_fails_loudly(self):
        with self.assertRaises(g.GauntletError):
            g.run_gauntlet(linear_spec(), "t", checkpointer=self.cp,
                           run_id="never-existed", resume=True)

    def test_resume_without_a_checkpointer_is_refused(self):
        with self.assertRaises(g.GauntletError):
            g.run_gauntlet(linear_spec(), "t", resume=True)

    def test_a_checkpoint_is_written_for_every_node(self):
        g.run_gauntlet(linear_spec(), "t", checkpointer=self.cp, run_id="steps1")
        count = self.cp.con.execute(
            "SELECT COUNT(*) FROM gx_checkpoint WHERE run_id='steps1'").fetchone()[0]
        self.assertGreaterEqual(count, 3)

    def test_the_run_id_is_reported_on_the_result(self):
        result = g.run_gauntlet(linear_spec(), "t", checkpointer=self.cp, run_id="id1")
        self.assertEqual(result.run_id, "id1")


class ApprovalGates(unittest.TestCase):
    """Human-in-the-loop: park the run, do not block a process for hours."""

    def setUp(self):
        self.cp = g.Checkpointer(":memory:")
        self.spec = {
            "schema": g.SCHEMA,
            "nodes": [
                {"name": "build", "agent": "alfred-coder"},
                {"name": "owner-ok", "kind": "approval", "agent": "alfred-manager",
                 "depends_on": ["build"], "task": "Deploy to production?"},
                {"name": "deploy", "agent": "alfred-devops", "depends_on": ["owner-ok"]},
            ],
        }

    def tearDown(self):
        self.cp.close()

    def test_an_approval_node_parks_the_run(self):
        calls: list[str] = []

        def executor(agent, task, timeout=None):
            calls.append(agent)
            return "done"

        result = g.run_gauntlet(self.spec, "t", executor, checkpointer=self.cp, run_id="ap1")
        self.assertEqual(result.status, g.INTERRUPTED)
        self.assertTrue(result.resumable)
        self.assertNotIn("alfred-devops", calls, "work past the gate must not run")

    def test_an_interrupted_run_is_not_a_failure(self):
        result = g.run_gauntlet(self.spec, "t", checkpointer=self.cp, run_id="ap2")
        self.assertFalse(result.ok)
        self.assertTrue(result.resumable)
        self.assertEqual(self.cp.run("ap2")["status"], g.INTERRUPTED)

    def test_approving_lets_the_run_continue_from_the_gate(self):
        calls: list[str] = []

        def executor(agent, task, timeout=None):
            calls.append(agent)
            return "done"

        g.run_gauntlet(self.spec, "t", executor, checkpointer=self.cp, run_id="ap3")
        calls.clear()
        result = g.run_gauntlet(self.spec, "t", executor, checkpointer=self.cp,
                                run_id="ap3", resume=True, approved=["owner-ok"])
        self.assertTrue(result.ok, result.reason)
        self.assertIn("alfred-devops", calls, "approved work must proceed")
        self.assertNotIn("alfred-coder", calls, "work before the gate must not repeat")

    def test_approval_granted_up_front_never_parks(self):
        result = g.run_gauntlet(self.spec, "t", approved=["owner-ok"])
        self.assertTrue(result.ok, result.reason)

    def test_an_unrelated_approval_does_not_unlock_the_gate(self):
        result = g.run_gauntlet(self.spec, "t", approved=["some-other-node"])
        self.assertEqual(result.status, g.INTERRUPTED)

    def test_approval_is_a_valid_node_kind(self):
        self.assertEqual(g.validate_spec(self.spec), [])

    def test_a_parked_run_does_not_trigger_compensation(self):
        """Parking is deliberate, not a failure - rolling back would be wrong."""
        spec = dict(self.spec)
        spec["nodes"] = [dict(n) for n in self.spec["nodes"]]
        spec["nodes"][0]["compensate"] = "git-status"
        undone: list[str] = []
        result = g.run_gauntlet(spec, "t", checkpointer=self.cp, run_id="ap4",
                                compensator=lambda c, n: undone.append(n))
        self.assertEqual(result.status, g.INTERRUPTED)
        self.assertEqual(undone, [], "a parked run must not be rolled back")


if __name__ == "__main__":
    unittest.main(verbosity=2)
