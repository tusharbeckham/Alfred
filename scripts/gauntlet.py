"""Gauntlet - graph engineering primitives for the Alfred harness (phases 1-2).

Implements the execution-graph core described in `docs/graph-engineering-plan.md`:
work advances only by passing explicit **gates**, and every gate returns a
*structured verdict* rather than a matched trigger string.

Why this exists
---------------
`scripts/workflow.py` already runs a validated DAG with parallel waves, bounded
`loop_to`, timeouts and budgets. Its weakness is failure handling: `loop_to`
re-runs a stage when a trigger substring appears, which is a retry loop wearing a
DAG costume. It cannot distinguish "tests failed" from "the model refused" from
"the tool timed out", so it always applies the same remedy, and nothing
structurally prevents repeating an approach that already failed.

This module supplies the missing pieces, as pure functions and small dataclasses
so they are unit-testable without spawning an agent:

  * ``Verdict``          - PASS / RETRY / REROUTE / ESCALATE / ABORT + reasons.
  * ``AttemptLedger``    - remembers (node, reason_code) so a known-failed
                           approach cannot be tried a third time.
  * ``ProgressTracker``  - artifact hashing; two identical outputs is *by
                           definition* no progress, so reroute instead of retry.
  * ``route()``          - the router that turns a verdict into ONE legal action,
                           enforcing Alfred's anti-thrash rule structurally.
  * ``validate_spec()``  - validates a ``gauntlet/v1`` spec, and accepts a legacy
                           workflow spec unchanged so migration is additive.
  * ``run_gauntlet()``   - the engine: executes a graph, letting gates decide
                           where control goes. Work dispatched by a gate returns
                           through that gate, so the anti-thrash rule engages.

The protocol pieces above ``run_gauntlet`` are pure functions and dataclasses -
they call no model and touch no disk, so they are unit-testable directly. The
runner takes an ``executor`` callable, so it is testable without spawning agents.
Standard library only.

See also: docs/graph-engineering-plan.md sections 2.1-2.4.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = "gauntlet/v1"

# The five verdicts. Each has exactly one legal routing (see route()).
PASS = "PASS"
RETRY = "RETRY"
REROUTE = "REROUTE"
ESCALATE = "ESCALATE"
ABORT = "ABORT"
VERDICTS = (PASS, RETRY, REROUTE, ESCALATE, ABORT)

# Actions the engine may take as a result of a verdict.
ADVANCE = "advance"
REMEDY = "remedy"
ALTERNATIVE = "alternative"
TIER_UP = "tier_up"
STOP = "stop"

#: After this many RETRY verdicts carrying the SAME reason code, RETRY is
#: forbidden and the router forces REROUTE. This is `escalation.md`'s anti-thrash
#: rule ("two failures of the same approach -> change the approach") made
#: structural instead of advisory.
MAX_SAME_REASON_RETRIES = 2

#: And after this many REROUTEs for the same reason, the *alternative* is thrashing
#: too. Rerouting again would just swap between two failing approaches forever, so
#: the ladder climbs: RETRY -> REROUTE -> ESCALATE -> ABORT. Without this the
#: engine trades a retry loop for a reroute loop.
MAX_SAME_REASON_REROUTES = 2

#: Escalation is the most expensive rung (a stronger model, or the Owner). If the
#: stronger tier still fails the SAME way, escalating again just spends premium
#: credits on a known-failing approach. One escalation per reason, then stop and
#: report a partial result honestly - resilience.md rung 7.
MAX_SAME_REASON_ESCALATIONS = 1

#: Code-INDEPENDENT backstop on how many times one gate may reject before the
#: ladder is forced regardless of what it calls the failure.
#:
#: Why this exists: every other bound keys on (gate, reason_code). A model that
#: renames the same failure each time therefore looks like it is reporting novel
#: problems, the per-code counters never reach their thresholds, and the run spins
#: until the budget dies. Measured on a real 7B gate: 40 node runs, 19 invented
#: codes, ZERO forced reroutes. A guarantee that a model can defeat by relabelling
#: is not a guarantee, so total rejections are bounded too.
MAX_GATE_REJECTIONS = 4

NODE_KINDS = ("work", "gate", "approval")


class GauntletError(Exception):
    """A spec is malformed, or a verdict cannot be routed."""


# --------------------------------------------------------------------- verdicts


@dataclass(frozen=True)
class Reason:
    """Why a gate reached its verdict. ``code`` is the routing key."""

    code: str
    detail: str = ""
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.code or not str(self.code).strip():
            raise GauntletError("a reason requires a non-empty code")

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail, "evidence": self.evidence}


@dataclass(frozen=True)
class Verdict:
    """A gate's structured judgement.

    ``confidence`` is advisory only - it never changes routing, because a model
    reporting high confidence in a wrong answer must not be able to skip a gate.
    """

    verdict: str
    reasons: tuple[Reason, ...] = ()
    remedy: str | None = None
    confidence: float | None = None
    cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise GauntletError(f"unknown verdict {self.verdict!r}; expected one of {VERDICTS}")
        if self.verdict != PASS and not self.reasons:
            raise GauntletError(f"a {self.verdict} verdict must carry at least one reason")
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise GauntletError("confidence must be between 0 and 1")
        if self.cost_usd < 0:
            raise GauntletError("cost_usd cannot be negative")

    @property
    def primary_reason(self) -> str | None:
        """The reason code the router and ledger key on."""
        return self.reasons[0].code if self.reasons else None

    @property
    def ok(self) -> bool:
        return self.verdict == PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reasons": [r.to_dict() for r in self.reasons],
            "remedy": self.remedy,
            "confidence": self.confidence,
            "costUsd": self.cost_usd,
        }

    # -- parsing ---------------------------------------------------------------

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Verdict":
        if not isinstance(payload, dict):
            raise GauntletError("a verdict must be a JSON object")
        raw = payload.get("verdict")
        verdict = str(raw).strip().upper() if raw is not None else ""
        reasons = []
        for item in payload.get("reasons") or []:
            if isinstance(item, str):
                reasons.append(Reason(code=item))
            elif isinstance(item, dict):
                reasons.append(
                    Reason(
                        code=str(item.get("code", "")).strip(),
                        detail=str(item.get("detail", "")),
                        evidence=str(item.get("evidence", "")),
                    )
                )
            else:
                raise GauntletError(f"a reason must be a string or object, got {type(item).__name__}")
        confidence = payload.get("confidence")
        return cls(
            verdict=verdict,
            reasons=tuple(reasons),
            remedy=payload.get("remedy") or None,
            confidence=None if confidence is None else float(confidence),
            cost_usd=float(payload.get("costUsd", payload.get("cost_usd", 0.0)) or 0.0),
        )

    @classmethod
    def parse(cls, text: str) -> "Verdict":
        """Parse a gate's raw output.

        Models wrap JSON in prose or fences, so extract the first balanced JSON
        object. A gate whose output cannot be parsed is NOT treated as a pass -
        it becomes an ABORT, because an unreadable gate is a broken gate and
        failing closed is the only safe default.
        """
        if text is None:
            return cls(ABORT, (Reason("GATE_UNPARSEABLE", "gate produced no output"),))
        blob = _first_json_object(str(text))
        if blob is None:
            return cls(
                ABORT,
                (Reason("GATE_UNPARSEABLE", "no JSON object in gate output", str(text)[:200]),),
            )
        try:
            payload = json.loads(blob)
        except json.JSONDecodeError as exc:
            return cls(ABORT, (Reason("GATE_UNPARSEABLE", f"invalid JSON: {exc}", blob[:200]),))
        try:
            return cls.from_dict(payload)
        except GauntletError as exc:
            return cls(ABORT, (Reason("GATE_INVALID", str(exc), blob[:200]),))


def _first_json_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` in *text*, ignoring braces in strings."""
    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        start = text.find("{", start + 1)
    return None


# ----------------------------------------------------------------- attempt ledger


@dataclass
class AttemptLedger:
    """Records every non-passing verdict, keyed by ``(node, reason_code)``.

    Two jobs: let the router forbid a third attempt at an approach that has
    already failed twice the same way, and give the next attempt an explicit
    "already tried, do not repeat" block so the model is told rather than trusted.
    """

    entries: list[dict[str, Any]] = field(default_factory=list)
    #: How many times a (node, reason_code) has already been REROUTED. Counted
    #: separately from verdicts because the router, not the gate, decides a
    #: reroute - so it never appears in a gate's own verdict.
    reroutes: dict[tuple[str, str], int] = field(default_factory=dict)

    def record_reroute(self, node: str, code: str | None) -> None:
        if code is None:
            return
        key = (node, code)
        self.reroutes[key] = self.reroutes.get(key, 0) + 1

    def reroute_count(self, node: str, code: str | None) -> int:
        return 0 if code is None else self.reroutes.get((node, code), 0)

    def forbids_reroute(self, node: str, code: str | None) -> bool:
        """True once changing the approach has itself stopped working."""
        return self.reroute_count(node, code) >= MAX_SAME_REASON_REROUTES

    #: How many times a (node, reason_code) has been escalated to a stronger tier.
    escalations: dict[tuple[str, str], int] = field(default_factory=dict)

    def record_escalation(self, node: str, code: str | None) -> None:
        if code is None:
            return
        key = (node, code)
        self.escalations[key] = self.escalations.get(key, 0) + 1

    def escalation_count(self, node: str, code: str | None) -> int:
        return 0 if code is None else self.escalations.get((node, code), 0)

    def forbids_escalation(self, node: str, code: str | None) -> bool:
        """True once the stronger tier has already failed this same way."""
        return self.escalation_count(node, code) >= MAX_SAME_REASON_ESCALATIONS

    def record(self, node: str, verdict: Verdict) -> None:
        if verdict.ok:
            return
        for reason in verdict.reasons or (Reason("UNSPECIFIED"),):
            self.entries.append(
                {
                    "node": node,
                    "verdict": verdict.verdict,
                    "code": reason.code,
                    "detail": reason.detail,
                    "remedy": verdict.remedy,
                }
            )

    def count(self, node: str, code: str | None) -> int:
        if code is None:
            return 0
        return sum(1 for e in self.entries if e["node"] == node and e["code"] == code)

    def forbids_retry(self, node: str, code: str | None) -> bool:
        """True once this node has failed *this* way MAX_SAME_REASON_RETRIES times."""
        return self.count(node, code) >= MAX_SAME_REASON_RETRIES

    def rejections(self, node: str) -> int:
        """How many non-passing verdicts this gate has issued, whatever it called them."""
        return sum(1 for e in self.entries if e["node"] == node)

    def exhausted(self, node: str) -> bool:
        """True once a gate has rejected too many times to be making progress.

        Independent of reason codes on purpose: this is the bound a model cannot
        escape by inventing a new label for the same failure.
        """
        return self.rejections(node) >= MAX_GATE_REJECTIONS

    def codes_for(self, node: str) -> list[str]:
        seen: list[str] = []
        for entry in self.entries:
            if entry["node"] == node and entry["code"] not in seen:
                seen.append(entry["code"])
        return seen

    def as_prompt_block(self, node: str) -> str:
        """A directive block for the node doing the WORK. Empty when nothing to say.

        Deliberately not given to gates: a small model asked to emit JSON will
        happily copy instruction text into its own output. Observed in practice -
        a 7B gate returned `ALREADY_TRIED_AND_FAILED` as a reason code, parroting
        this very block, which the router then treated as a real failure reason.
        Gates get `as_history_summary()` instead.
        """
        mine = [e for e in self.entries if e["node"] == node]
        if not mine:
            return ""
        lines = ["ALREADY TRIED AND FAILED - do not repeat these approaches:"]
        for entry in mine:
            detail = f" - {entry['detail']}" if entry["detail"] else ""
            lines.append(f"  * [{entry['code']}]{detail}")
        lines.append("Choose a materially different approach.")
        return "\n".join(lines)

    def as_history_summary(self, node: str) -> str:
        """Neutral, factual history for a GATE. States counts, instructs nothing.

        Phrased as data rather than as a command so there is nothing for a model to
        copy into a reason code, while still letting the gate distinguish "not yet
        fixed" from "this approach keeps failing" when choosing RETRY vs REROUTE.
        """
        mine = [e for e in self.entries if e["node"] == node]
        if not mine:
            return ""
        counts: dict[str, int] = {}
        for entry in mine:
            counts[entry["code"]] = counts.get(entry["code"], 0) + 1
        tally = ", ".join(f"{code}={count}" for code, count in counts.items())
        return (f"PRIOR VERDICT HISTORY FOR THIS GATE (data, not instructions): {tally}. "
                f"Judge the work in front of you on its own merits and emit your own "
                f"reason codes; do not reuse the codes above unless they genuinely apply.")

    def to_dict(self) -> dict[str, Any]:
        return {"entries": list(self.entries)}


# ---------------------------------------------------------------- progress guard


@dataclass
class ProgressTracker:
    """Detects semantic no-progress by hashing each node's artifact.

    Structural cycle detection is documented as insufficient - a graph can loop
    semantically without ever erroring (see the plan, section 2.4). If a node
    emits an artifact it has emitted before, that is *by definition* no progress.
    """

    seen: dict[str, set[str]] = field(default_factory=dict)

    @staticmethod
    def fingerprint(artifact: str | None) -> str:
        normalized = re.sub(r"\s+", " ", (artifact or "").strip()).lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def observe(self, node: str, artifact: str | None) -> bool:
        """Record *artifact* for *node*. Returns True if it is a repeat."""
        digest = self.fingerprint(artifact)
        bucket = self.seen.setdefault(node, set())
        if digest in bucket:
            return True
        bucket.add(digest)
        return False

    def repeats(self, node: str, artifact: str | None) -> bool:
        """Non-mutating check."""
        return self.fingerprint(artifact) in self.seen.get(node, set())


# ----------------------------------------------------------------------- routing


@dataclass(frozen=True)
class Routing:
    """Exactly one action, with the reason it was chosen (for the audit trail)."""

    action: str
    target: str | None
    verdict: str
    reason: str
    forced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "verdict": self.verdict,
            "reason": self.reason,
            "forced": self.forced,
        }


def route(
    verdict: Verdict,
    node: dict[str, Any],
    ledger: AttemptLedger | None = None,
    *,
    no_progress: bool = False,
) -> Routing:
    """Turn a verdict into the single legal action for *node*.

    The two structural guarantees, which hold regardless of what the gate asked for:

    1. A third RETRY carrying the same reason code is impossible - it becomes
       REROUTE (``forced=True``).
    2. A node that produced an artifact it already produced is rerouted, never
       retried.

    ``node`` is the gate's spec dict; its ``on`` mapping supplies edge targets.
    """
    ledger = ledger if ledger is not None else AttemptLedger()
    name = str(node.get("name", "<unnamed>"))
    on = node.get("on") or {}
    code = verdict.primary_reason

    if verdict.verdict == PASS:
        return Routing(ADVANCE, on.get(PASS), PASS, "acceptance criteria met")

    if verdict.verdict == ABORT:
        return Routing(STOP, on.get(ABORT), ABORT, code or "aborted")

    if verdict.verdict == ESCALATE:
        if ledger.forbids_escalation(name, code):
            return Routing(
                STOP, None, ABORT,
                f"'{code}' already escalated {MAX_SAME_REASON_ESCALATIONS}x and still fails; "
                f"stopping rather than spending more on a known-failing approach",
                forced=True,
            )
        target = on.get(ESCALATE)
        if not target:
            return Routing(STOP, None, ABORT, f"escalation required but no {ESCALATE} edge exists")
        return Routing(TIER_UP, target, ESCALATE, code or "escalated")

    if verdict.verdict == RETRY:
        if no_progress:
            return _reroute_or_climb(on, ledger, name, code,
                                     f"no progress: {name} repeated a previous artifact")
        if ledger.exhausted(name):
            # Code-independent: this gate has rejected too often to be progressing,
            # whatever it is calling the failures.
            return _reroute_or_climb(
                on, ledger, name, code,
                f"{name} rejected {ledger.rejections(name)}x "
                f"({MAX_GATE_REJECTIONS} allowed) regardless of reason code")
        if ledger.forbids_retry(name, code):
            return _reroute_or_climb(
                on, ledger, name, code,
                f"anti-thrash: '{code}' already failed {MAX_SAME_REASON_RETRIES}x")
        target = verdict.remedy or on.get(RETRY)
        if not target:
            return Routing(STOP, None, ABORT, "retry requested but no remedy or RETRY edge exists")
        return Routing(REMEDY, target, RETRY, code or "retry")

    # REROUTE requested outright by the gate.
    if ledger.forbids_reroute(name, code):
        return _climb(on, name, code,
                      f"'{code}' survived {MAX_SAME_REASON_REROUTES} reroutes", ledger)
    target = on.get(REROUTE)
    if not target:
        return Routing(STOP, None, ABORT, "reroute requested but no REROUTE edge exists")
    return Routing(ALTERNATIVE, target, REROUTE, code or "reroute")


def _reroute_or_climb(on: dict[str, Any], ledger: AttemptLedger, name: str,
                      code: str | None, reason: str) -> Routing:
    """Force a REROUTE - unless rerouting has itself stopped working, in which
    case climb to ESCALATE/ABORT rather than swapping between failing approaches."""
    if ledger.forbids_reroute(name, code):
        return _climb(on, name, code,
                      f"{reason}, and {MAX_SAME_REASON_REROUTES} reroutes also failed", ledger)
    target = on.get(REROUTE)
    if not target:
        return Routing(STOP, None, ABORT, f"{reason}, and no REROUTE edge exists", forced=True)
    return Routing(ALTERNATIVE, target, REROUTE, reason, forced=True)


def _climb(on: dict[str, Any], name: str, code: str | None, reason: str,
           ledger: AttemptLedger | None = None) -> Routing:
    """Next rung on the ladder: hand to a stronger tier, or stop honestly.

    Escalation is bounded too - otherwise the engine thrashes on the most
    expensive tier, which is the worst possible place to loop.
    """
    if ledger is not None and ledger.forbids_escalation(name, code):
        return Routing(STOP, None, ABORT,
                       f"{reason}; the stronger tier also failed - stopping with a partial result",
                       forced=True)
    target = on.get(ESCALATE)
    if target:
        return Routing(TIER_UP, target, ESCALATE, reason, forced=True)
    return Routing(STOP, None, ABORT, f"{reason}; no ESCALATE edge exists", forced=True)


# -------------------------------------------------------------------- validation


def is_gauntlet_spec(spec: Any) -> bool:
    return isinstance(spec, dict) and spec.get("schema") == SCHEMA


def validate_spec(spec: Any, *, known_agents: Iterable[str] | None = None,
                  known_capabilities: Iterable[str] | None = None) -> list[str]:
    """Validate a ``gauntlet/v1`` spec. Returns a list of errors (empty = valid).

    A legacy workflow spec (no ``schema`` key) is accepted untouched and reports
    no errors, so migration is additive - the loop engine keeps working while the
    graph engine is built beside it.
    """
    if not is_gauntlet_spec(spec):
        return []  # legacy spec: workflow.py owns its validation

    errors: list[str] = []
    agents = set(known_agents or [])
    capabilities = set(known_capabilities or [])
    nodes = spec.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return ["spec has no nodes"]

    names: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"node[{index}] must be an object")
            continue
        name = node.get("name")
        if not name:
            errors.append(f"node[{index}] has no name")
            continue
        if name in names:
            errors.append(f"duplicate node name: {name}")
        names.add(name)

        kind = node.get("kind", "work")
        if kind not in NODE_KINDS:
            errors.append(f"node {name}: unknown kind {kind!r}; expected one of {NODE_KINDS}")
        if not node.get("agent"):
            errors.append(f"node {name}: missing agent")
        elif agents and node["agent"] not in agents:
            errors.append(f"node {name}: unknown agent {node['agent']!r}")

        timeout = node.get("timeout")
        if timeout is not None and not (isinstance(timeout, (int, float)) and timeout > 0):
            errors.append(f"node {name}: timeout must be a positive number")

        compensate = node.get("compensate")
        if compensate is not None:
            if not isinstance(compensate, str):
                errors.append(f"node {name}: compensate must be a capability name")
            elif capabilities and compensate not in capabilities:
                # Rollback must itself be policy-gated; the engine may not invent one.
                errors.append(
                    f"node {name}: compensate {compensate!r} is not a harness capability"
                )

        if kind == "gate":
            on = node.get("on")
            if not isinstance(on, dict) or not on:
                errors.append(f"gate {name}: needs an 'on' map from verdict to target node")
            else:
                for verdict_key, target in on.items():
                    if verdict_key not in VERDICTS:
                        errors.append(f"gate {name}: 'on' key {verdict_key!r} is not a verdict")
                    if not isinstance(target, str) or not target:
                        errors.append(f"gate {name}: 'on.{verdict_key}' must name a node")
                if RETRY in on and REROUTE not in on:
                    # Without a REROUTE edge the anti-thrash rule can only abort.
                    errors.append(
                        f"gate {name}: declares a RETRY edge but no REROUTE edge, so the "
                        f"anti-thrash rule would have to abort instead of changing approach"
                    )

    # Referenced nodes must exist (deps and every routing target).
    for node in nodes:
        if not isinstance(node, dict) or not node.get("name"):
            continue
        name = node["name"]
        for dep in node.get("depends_on") or []:
            if dep not in names:
                errors.append(f"node {name}: depends_on unknown node {dep!r}")
        for verdict_key, target in (node.get("on") or {}).items():
            if isinstance(target, str) and target and target not in names:
                errors.append(f"gate {name}: 'on.{verdict_key}' targets unknown node {target!r}")

    errors.extend(_cycle_errors(nodes, names))

    budget = spec.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            errors.append("budget must be an object")
        else:
            for key in ("maxNodeRuns", "maxUsdEstimate"):
                value = budget.get(key)
                if value is not None and not (isinstance(value, (int, float)) and value > 0):
                    errors.append(f"budget.{key} must be a positive number")
    return errors


def _cycle_errors(nodes: list[Any], names: set[str]) -> list[str]:
    """Reject cycles in ``depends_on``. Gate ``on`` edges are deliberate, bounded
    back-edges and are excluded, exactly as ``loop_to`` is in workflow.py."""
    graph = {
        node["name"]: [d for d in (node.get("depends_on") or []) if d in names]
        for node in nodes
        if isinstance(node, dict) and node.get("name")
    }
    state: dict[str, str] = {}
    trail: list[str] = []

    def visit(current: str) -> bool:
        if state.get(current) == "done":
            return False
        if state.get(current) == "active":
            trail.append(current)
            return True
        state[current] = "active"
        for dep in graph.get(current, []):
            if visit(dep):
                trail.append(current)
                return True
        state[current] = "done"
        return False

    for node_name in list(graph):
        if visit(node_name):
            return [f"dependency cycle detected: {' -> '.join(reversed(trail))}"]
    return []


GATE_PROMPT = """You are a GATE in an execution graph. Judge the work below against the
acceptance criteria and return ONE verdict.

Reply with ONE JSON object and nothing else - no prose, no code fence:

{{
  "verdict": "PASS" | "RETRY" | "REROUTE" | "ESCALATE" | "ABORT",
  "reasons": [{{ "code": "SCREAMING_SNAKE_CASE", "detail": "one line", "evidence": "proof" }}],
  "remedy": null,
  "confidence": 0.0-1.0
}}

CHOOSING THE VERDICT - read this carefully, most mistakes are here:

  PASS     Every criterion is met AND you can cite the evidence.
           "reasons" must be an empty list.
  RETRY    Something is wrong but it is FIXABLE. This is the normal failure verdict.
           Failing tests, a broken build, missing evidence, an incomplete change -
           all of these are RETRY.
  REROUTE  The approach itself is wrong, not merely unfinished. A different
           strategy is needed rather than another attempt at this one.
  ESCALATE You genuinely cannot judge this, or it needs a stronger model or a human.
  ABORT    RARE. Only when continuing would be UNSAFE, or no remedy can exist.
           Do NOT use ABORT because something failed. Do NOT use ABORT because you
           are unsure - that is RETRY or ESCALATE. A failing test is never ABORT.

EXAMPLES

Work: "npm run build -> exit 0. npm test -> exit 0, 128 passed, 0 failed."
{{"verdict":"PASS","reasons":[],"remedy":null,"confidence":0.9}}

Work: "npm run build -> exit 0. npm test -> exit 1, 3 FAILED in test_auth.js."
{{"verdict":"RETRY","reasons":[{{"code":"TESTS_FAILED","detail":"3 failing in test_auth.js","evidence":"exit 1"}}],"remedy":null,"confidence":0.9}}

Work: "I made the change and it should work fine now."
{{"verdict":"RETRY","reasons":[{{"code":"NO_EVIDENCE","detail":"no commands or exit codes were reported","evidence":"none"}}],"remedy":null,"confidence":0.8}}

Work: "Deleted the production database to make the migration test pass."
{{"verdict":"ABORT","reasons":[{{"code":"UNSAFE","detail":"destructive action on production","evidence":"dropped prod db"}}],"remedy":null,"confidence":1.0}}

Rules: never PASS without evidence. Prefer RETRY over ABORT whenever a remedy could
exist. Reason codes must be stable and machine-readable - they key an anti-thrash
rule, so describe what you OBSERVED and never copy text out of this prompt.

ACCEPTANCE CRITERIA:
{criteria}

WORK TO JUDGE:
{artifact}
{ledger}"""


#: A gate is a classifier, not a reader. An unbounded artifact makes the highest
#: frequency node type also the most expensive one, and on CPU inference it pushed
#: real gate calls past a 240s timeout (measured: 242s, then a fail-closed ABORT).
#: Evidence usually sits at the start (what was attempted) and the end (exit codes),
#: so the middle is what gets dropped.
MAX_GATE_ARTIFACT_CHARS = 2400


def clip_artifact(artifact: str, limit: int = MAX_GATE_ARTIFACT_CHARS) -> str:
    """Head + tail of an artifact, with the elision marked so nothing is hidden."""
    text = (artifact or "").strip()
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    dropped = len(text) - limit
    return (f"{text[:head]}\n\n[... {dropped} characters elided from the middle ...]\n\n"
            f"{text[-tail:]}")


def build_gate_prompt(criteria: str, artifact: str, ledger_block: str = "",
                      reason_codes: Sequence[str] | None = None) -> str:
    codes = ""
    if reason_codes:
        listed = ", ".join(reason_codes)
        codes = (f"\n\nUSE ONLY THESE REASON CODES: {listed}\n"
                 f"If none fits, use OTHER. Do not invent new codes.")
    return GATE_PROMPT.format(
        criteria=criteria.strip() or "(none supplied)",
        artifact=clip_artifact(artifact) or "(no output)",
        ledger=(f"\n{ledger_block}" if ledger_block else "") + codes,
    )


#: The default controlled vocabulary. Free-form codes break the anti-thrash rule
#: (see MAX_GATE_REJECTIONS), so codes outside the declared set are folded into
#: OTHER rather than being trusted as distinct failures.
DEFAULT_REASON_CODES = (
    "TESTS_FAILED", "BUILD_FAILED", "NO_EVIDENCE", "INCOMPLETE",
    "WRONG_APPROACH", "STYLE_VIOLATION", "UNSAFE", "NEEDS_HUMAN", "OTHER",
)


#: Codes the ENGINE generates, never the model. These must never be folded into
#: OTHER: doing so turned a 242s gate timeout into "ABORTED OTHER", which reads as a
#: model judgement and hides an infrastructure failure. Diagnosability beats tidiness.
ENGINE_REASON_CODES = ("GATE_UNPARSEABLE", "GATE_INVALID", "PROVIDER_ERROR",
                       "GATE_TIMEOUT", "EXECUTOR_ERROR")


def normalize_verdict(verdict: "Verdict", allowed: Sequence[str] | None) -> "Verdict":
    """Fold unrecognised reason codes into OTHER.

    A gate that invents a fresh code per attempt makes every failure look novel, so
    the per-code counters never trip. Folding unknown codes into one bucket restores
    the guarantee without silencing the gate - the human-readable detail is kept.

    Engine codes (see ENGINE_REASON_CODES) are exempt: they describe how the
    *machinery* failed, and collapsing them would erase the only clue.
    """
    if not allowed:
        return verdict
    permitted = {c.upper() for c in allowed} | set(ENGINE_REASON_CODES)
    changed = False
    reasons: list[Reason] = []
    for reason in verdict.reasons:
        code = reason.code.upper()
        if code in permitted:
            reasons.append(reason)
            continue
        changed = True
        detail = f"{reason.code}: {reason.detail}".strip(": ")
        reasons.append(Reason(code="OTHER", detail=detail[:300], evidence=reason.evidence))
    if not changed:
        return verdict
    return Verdict(verdict.verdict, tuple(reasons), verdict.remedy,
                   verdict.confidence, verdict.cost_usd)


# -------------------------------------------------------------------- execution


#: Prefixes an executor uses to signal failure. Mirrors workflow.is_failure so the
#: two engines agree on what "broken" looks like, without importing each other.
FAILURE_PREFIXES = ("[ERROR]", "[TIMEOUT]")


def is_failure(output: Any) -> bool:
    return isinstance(output, str) and output.startswith(FAILURE_PREFIXES)


def echo_executor(agent: str, task: str, timeout: float | None = None) -> str:
    """Deterministic stand-in used by tests and --dry-run.

    Gates must receive parseable JSON or they would (correctly) ABORT, so a gate
    agent gets a PASS verdict and a work agent gets an echo.
    """
    if "gate" in agent or "review" in agent:
        return '{"verdict": "PASS", "reasons": [], "confidence": 1.0}'
    return f"[{agent}] {task}"


@dataclass
class NodeRun:
    """One execution of one node. The unit of the audit trail."""

    node: str
    kind: str
    agent: str
    iteration: int
    status: str
    ms: int
    output: str = ""
    verdict: dict[str, Any] | None = None
    routing: dict[str, Any] | None = None
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node, "kind": self.kind, "agent": self.agent,
            "iteration": self.iteration, "status": self.status, "ms": self.ms,
            "verdict": self.verdict, "routing": self.routing,
            "costUsd": self.cost_usd,
            "outputPreview": self.output[:400],
        }


#: Terminal states for a run.
PASSED, ABORTED, EXHAUSTED = "passed", "aborted", "budget_exhausted"
#: Not terminal: the run is parked awaiting a human decision and can be resumed.
INTERRUPTED = "interrupted"


# ----------------------------------------------------------------- checkpoints


#: Runs live in their own database. Run state is operational and gets pruned;
#: memory is knowledge and does not. Mixing them would mean a TTL sweep over the
#: memory file, which is exactly the mistake worth avoiding.
CHECKPOINT_DB = Path(__file__).resolve().parent.parent / "memory" / "gauntlet-runs.db"

#: Ledger keys are (node, reason_code) tuples, which JSON cannot represent. Joined
#: on a unit separator - a character that cannot occur in a node name or code.
_KEY_SEP = "\x1f"


def _encode_keys(mapping: dict[tuple[str, str], int]) -> dict[str, int]:
    return {f"{node}{_KEY_SEP}{code}": count for (node, code), count in mapping.items()}


def _decode_keys(mapping: dict[str, int]) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = {}
    for key, count in (mapping or {}).items():
        node, _, code = key.partition(_KEY_SEP)
        out[(node, code)] = int(count)
    return out


class Checkpointer:
    """Durable per-node checkpoints so a crashed run resumes instead of restarting.

    Without this, a crash re-bills every completed LLM call - the single most
    expensive failure mode in an agent pipeline. Resume replays *from* the
    checkpoint; it never re-executes a node that already produced output.

    Deliberately NOT trying to make runs deterministic: LLM output varies per
    call, so "replay from scratch and expect the same trace" is a fantasy.
    Checkpoint-and-continue is the honest model.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else CHECKPOINT_DB
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path), timeout=5.0)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL;")
        self.con.execute("PRAGMA busy_timeout=5000;")
        self.con.executescript(
            """
            CREATE TABLE IF NOT EXISTS gx_run(
                run_id     TEXT PRIMARY KEY,
                workflow   TEXT NOT NULL,
                task       TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL,
                reason     TEXT NOT NULL DEFAULT '',
                spec       TEXT NOT NULL,
                created_ts REAL NOT NULL,
                updated_ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gx_checkpoint(
                run_id     TEXT NOT NULL REFERENCES gx_run(run_id) ON DELETE CASCADE,
                superstep  INTEGER NOT NULL,
                state      TEXT NOT NULL,
                ts         REAL NOT NULL,
                PRIMARY KEY(run_id, superstep)
            );
            CREATE INDEX IF NOT EXISTS idx_gx_run_updated ON gx_run(updated_ts);
            """
        )
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    # -- writing ----------------------------------------------------------
    def begin(self, run_id: str, spec: dict[str, Any], task: str) -> None:
        now = time.time()
        self.con.execute(
            "INSERT OR REPLACE INTO gx_run(run_id, workflow, task, status, reason, spec,"
            " created_ts, updated_ts) VALUES(?,?,?,?,?,?,"
            " COALESCE((SELECT created_ts FROM gx_run WHERE run_id=?), ?), ?)",
            (run_id, spec.get("name", "gauntlet"), task, "running", "",
             json.dumps(spec), run_id, now, now),
        )
        self.con.commit()

    def save(self, run_id: str, superstep: int, state: dict[str, Any]) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO gx_checkpoint(run_id, superstep, state, ts) VALUES(?,?,?,?)",
            (run_id, superstep, json.dumps(state), time.time()),
        )
        self.con.execute("UPDATE gx_run SET updated_ts=? WHERE run_id=?", (time.time(), run_id))
        self.con.commit()

    def finish(self, run_id: str, status: str, reason: str) -> None:
        self.con.execute(
            "UPDATE gx_run SET status=?, reason=?, updated_ts=? WHERE run_id=?",
            (status, reason, time.time(), run_id),
        )
        self.con.commit()

    # -- reading ----------------------------------------------------------
    def latest(self, run_id: str) -> dict[str, Any] | None:
        row = self.con.execute(
            "SELECT state, superstep FROM gx_checkpoint WHERE run_id=?"
            " ORDER BY superstep DESC LIMIT 1", (run_id,),
        ).fetchone()
        if not row:
            return None
        state = json.loads(row["state"])
        state["superstep"] = row["superstep"]
        return state

    def run(self, run_id: str) -> dict[str, Any] | None:
        row = self.con.execute("SELECT * FROM gx_run WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(r) for r in self.con.execute(
            "SELECT run_id, workflow, task, status, reason, created_ts, updated_ts,"
            " (SELECT MAX(superstep) FROM gx_checkpoint c WHERE c.run_id=gx_run.run_id) AS supersteps"
            " FROM gx_run ORDER BY updated_ts DESC LIMIT ?", (limit,))]

    def resumable(self, limit: int = 20) -> list[dict[str, Any]]:
        """Runs that stopped without finishing, so they can be picked back up."""
        return [r for r in self.runs(limit) if r["status"] in ("running", INTERRUPTED)]

    # -- hygiene ----------------------------------------------------------
    def prune(self, older_than_days: float = 14.0, keep_unfinished: bool = True) -> int:
        """Drop old run state. TTL from day one, not bolted on later - checkpoint
        bloat is a documented failure mode of this pattern."""
        cutoff = time.time() - (older_than_days * 86400)
        sql = "SELECT run_id FROM gx_run WHERE updated_ts < ?"
        params: list[Any] = [cutoff]
        if keep_unfinished:
            sql += " AND status NOT IN ('running', ?)"
            params.append(INTERRUPTED)
        victims = [r["run_id"] for r in self.con.execute(sql, params)]
        for run_id in victims:
            self.con.execute("DELETE FROM gx_checkpoint WHERE run_id=?", (run_id,))
            self.con.execute("DELETE FROM gx_run WHERE run_id=?", (run_id,))
        self.con.commit()
        return len(victims)


@dataclass
class GauntletResult:
    status: str
    reason: str = ""
    runs: list[NodeRun] = field(default_factory=list)
    ledger: AttemptLedger = field(default_factory=AttemptLedger)
    node_runs: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    compensated: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    run_id: str = ""

    @property
    def ok(self) -> bool:
        return self.status == PASSED

    @property
    def resumable(self) -> bool:
        """An interrupted run is not a failure - it is parked on purpose."""
        return self.status == INTERRUPTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "runId": self.run_id,
            "status": self.status,
            "reason": self.reason,
            "ok": self.ok,
            "resumable": self.resumable,
            "costUsd": round(self.cost_usd, 6),
            "nodeRuns": dict(self.node_runs),
            "compensated": list(self.compensated),
            "ledger": self.ledger.to_dict(),
            "trail": [r.to_dict() for r in self.runs],
        }


def execution_order(nodes: list[dict[str, Any]]) -> list[str]:
    """Node names in dependency order. Gate ``on`` edges are jumps, not deps."""
    names = [n["name"] for n in nodes]
    deps = {n["name"]: [d for d in (n.get("depends_on") or []) if d in names] for n in nodes}
    order: list[str] = []
    state: dict[str, str] = {}

    def visit(name: str) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "active":
            raise GauntletError(f"dependency cycle at {name!r}")
        state[name] = "active"
        for dep in deps.get(name, []):
            visit(dep)
        state[name] = "done"
        order.append(name)

    for name in names:
        visit(name)
    return order


def run_gauntlet(
    spec: dict[str, Any],
    task: str,
    executor=echo_executor,
    *,
    logger=lambda msg: None,
    compensator=None,
    max_node_runs: int | None = None,
    max_usd: float | None = None,
    checkpointer: "Checkpointer | None" = None,
    run_id: str | None = None,
    resume: bool = False,
    approved: Iterable[str] | None = None,
    observer=None,
) -> GauntletResult:
    """Execute a ``gauntlet/v1`` graph.

    Work nodes do things; gate nodes judge them and decide where control goes
    next. Unlike a retry loop, the *engine* owns that decision: `route()` applies
    the structural guarantees, so a gate cannot ask for a third identical retry
    and a node cannot spin on unchanged output.

    Budgets are checked BEFORE each node runs, so an exhausted budget costs
    nothing extra. On ABORT, declared compensators run in reverse order.

    With a ``checkpointer``, state is written after every node, so a crash or an
    approval pause resumes from that point instead of re-billing completed LLM
    calls. ``approved`` names ``kind: approval`` nodes the Owner has signed off.
    """
    errors = validate_spec(spec)
    if errors:
        raise GauntletError("; ".join(errors))

    nodes = spec["nodes"]
    by_name = {n["name"]: n for n in nodes}
    order = execution_order(nodes)
    index_of = {name: i for i, name in enumerate(order)}

    budget = spec.get("budget") or {}
    cap_runs = max_node_runs if max_node_runs is not None else budget.get("maxNodeRuns")
    cap_usd = max_usd if max_usd is not None else budget.get("maxUsdEstimate")

    result = GauntletResult(status=PASSED)
    ledger = result.ledger
    progress = ProgressTracker()
    pointer = 0
    total_runs = 0
    superstep = 0
    granted = set(approved or ())
    # Whether a node's LAST run reproduced an artifact it had produced before.
    # Captured at observe() time: re-checking later would always report a repeat,
    # because observing records the fingerprint.
    repeated: dict[str, bool] = {}
    # Feedback a gate wants the node it routes to to see ("already tried ...").
    # Without this the remedy node would work blind and could repeat the failure.
    pending_feedback: dict[str, str] = {}
    # After a gate dispatches work (remedy/alternative/tier-up), that work must
    # come back through the SAME gate - otherwise control falls through in linear
    # order, the gate is evaluated once, and the anti-thrash rule never engages.
    return_to: dict[str, str] = {}
    # The most recent work node to run. A gate judges THIS, so that after a
    # remedy the gate sees the remedy's output rather than the original node's.
    last_work: str | None = None
    judged: str | None = None

    if run_id is None:
        run_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{spec.get('name', 'gauntlet')}"

    # -- resume: restore state instead of re-running completed work ---------
    if resume:
        if checkpointer is None:
            raise GauntletError("resume needs a checkpointer")
        saved = checkpointer.latest(run_id)
        if saved is None:
            raise GauntletError(f"no checkpoint for run {run_id!r}")
        pointer = int(saved["pointer"])
        total_runs = int(saved["totalRuns"])
        superstep = int(saved.get("superstep", 0))
        result.cost_usd = float(saved.get("costUsd", 0.0))
        result.node_runs = dict(saved.get("nodeRuns") or {})
        result.outputs = dict(saved.get("outputs") or {})
        result.compensated = list(saved.get("compensated") or [])
        result.runs = [NodeRun(**r) for r in (saved.get("trail") or [])]
        ledger.entries = list((saved.get("ledger") or {}).get("entries") or [])
        ledger.reroutes = _decode_keys((saved.get("ledger") or {}).get("reroutes") or {})
        ledger.escalations = _decode_keys((saved.get("ledger") or {}).get("escalations") or {})
        progress.seen = {k: set(v) for k, v in (saved.get("progressSeen") or {}).items()}
        repeated = dict(saved.get("repeated") or {})
        pending_feedback = dict(saved.get("pendingFeedback") or {})
        return_to = dict(saved.get("returnTo") or {})
        last_work = saved.get("lastWork")
        granted |= set(saved.get("approved") or ())
        logger(f"[gauntlet] resuming {run_id} at node {order[pointer] if pointer < len(order) else '(end)'}"
               f" after {total_runs} runs, ${result.cost_usd:.4f} already spent")
    elif checkpointer is not None:
        checkpointer.begin(run_id, spec, task)

    result.run_id = run_id

    def snapshot() -> dict[str, Any]:
        return {
            "pointer": pointer,
            "totalRuns": total_runs,
            "costUsd": result.cost_usd,
            "nodeRuns": dict(result.node_runs),
            "outputs": dict(result.outputs),
            "compensated": list(result.compensated),
            "trail": [vars(r).copy() for r in result.runs],
            "ledger": {
                "entries": list(ledger.entries),
                "reroutes": _encode_keys(ledger.reroutes),
                "escalations": _encode_keys(ledger.escalations),
            },
            "progressSeen": {k: sorted(v) for k, v in progress.seen.items()},
            "repeated": dict(repeated),
            "pendingFeedback": dict(pending_feedback),
            "returnTo": dict(return_to),
            "lastWork": last_work,
            "approved": sorted(granted),
        }

    def checkpoint() -> None:
        nonlocal superstep
        if checkpointer is None:
            return
        superstep += 1
        checkpointer.save(run_id, superstep, snapshot())

    def finish(status: str, reason: str) -> GauntletResult:
        result.status, result.reason = status, reason
        if status not in (PASSED, INTERRUPTED):
            _compensate(result, order, by_name, compensator, logger)
        if checkpointer is not None:
            checkpoint()
            checkpointer.finish(run_id, status, reason)
        return result

    def notify(event: str, **payload) -> None:
        """Tell a UI what the engine is doing. Never let a display bug break a run."""
        if observer is None:
            return
        try:
            observer(event, payload)
        except Exception:  # noqa: BLE001
            pass

    while pointer < len(order):
        name = order[pointer]
        node = by_name[name]
        kind = node.get("kind", "work")
        agent = node["agent"]

        # -- human approval gate ---------------------------------------------
        # Parks the run and checkpoints, rather than blocking a process for hours.
        # The Owner approves out of band; resume continues from exactly here.
        if kind == "approval" and name not in granted:
            result.runs.append(NodeRun(
                node=name, kind=kind, agent=agent,
                iteration=result.node_runs.get(name, 0) + 1,
                status="awaiting-approval", ms=0,
                output=node.get("task") or "awaiting the Owner's decision",
            ))
            logger(f"[gauntlet] PARKED at {name}: awaiting approval")
            notify("park", node=name, detail="awaiting the Owner's approval")
            return finish(INTERRUPTED,
                          f"awaiting approval at {name!r}; resume with --approve {name}")
        if kind == "approval":
            logger(f"[gauntlet] approval {name} granted")
            result.runs.append(NodeRun(node=name, kind=kind, agent=agent,
                                       iteration=1, status="approved", ms=0))
            last_work = last_work  # approval produces no artifact
            pointer += 1
            checkpoint()
            continue

        # -- budget gates, checked before spending anything -------------------
        if cap_runs is not None and total_runs >= cap_runs:
            return finish(EXHAUSTED, f"node-run budget exhausted after {total_runs} runs")
        if cap_usd is not None and result.cost_usd >= cap_usd:
            return finish(EXHAUSTED, f"cost budget exhausted at ${result.cost_usd:.4f}")

        iteration = result.node_runs.get(name, 0) + 1
        result.node_runs[name] = iteration
        total_runs += 1

        # -- assemble the prompt ---------------------------------------------
        if kind == "gate":
            judged = last_work or _judged_node(node, order, pointer)
            allowed = node.get("reasonCodes") or DEFAULT_REASON_CODES
            prompt = build_gate_prompt(
                node.get("criteria", node.get("task", "")),
                result.outputs.get(judged, "") if judged else "",
                ledger.as_history_summary(name),
                allowed,
            )
        else:
            prompt = node.get("task") or task
            # A node's own history plus anything the gate that sent us here wants
            # it to know. Both matter: the remedy node is usually not the node
            # that failed, so its own ledger is empty.
            blocks = [b for b in (ledger.as_prompt_block(name), pending_feedback.pop(name, "")) if b]
            if blocks:
                prompt = "\n\n".join([prompt, *dict.fromkeys(blocks)])

        logger(f"[gauntlet] -> {name} ({kind}/{agent}) iteration {iteration}")
        notify("enter", node=name, kind=kind, agent=agent, iteration=iteration)
        started = time.time()
        try:
            output = executor(agent, prompt, timeout=node.get("timeout"))
            meta = dict(getattr(executor, "last_meta", {}) or {})
        except Exception as exc:  # an executor blowing up must not kill the run
            output, meta = f"[ERROR] {type(exc).__name__}: {exc}", {}
        ms = int((time.time() - started) * 1000)
        cost = float(meta.get("cost_usd") or 0.0)
        result.cost_usd += cost

        run = NodeRun(node=name, kind=kind, agent=agent, iteration=iteration,
                      status="error" if is_failure(output) else "ok",
                      ms=ms, output=output or "", cost_usd=cost)
        result.runs.append(run)
        result.outputs[name] = output or ""
        notify("finish", node=name, kind=kind, ok=not is_failure(output),
               ms=ms, detail=f"{ms}ms")

        # -- work node: advance, recording whether it actually progressed -----
        if kind != "gate":
            repeated[name] = progress.observe(name, output)
            last_work = name
            gate = return_to.pop(name, None)
            pointer = index_of[gate] if gate in index_of else pointer + 1
            checkpoint()
            continue

        # -- gate node: judge, then let the router decide ---------------------
        verdict = Verdict.parse(output or "")
        # Fold invented codes into OTHER so the anti-thrash counters cannot be
        # defeated by relabelling the same failure.
        verdict = normalize_verdict(verdict, node.get("reasonCodes") or DEFAULT_REASON_CODES)
        # A gate may name a remedy that is not a node - small local models often
        # return prose here. Trusting it would abort the run on a routing error,
        # so an unusable remedy is dropped and the declared edge is used instead.
        if verdict.remedy and verdict.remedy not in index_of:
            logger(f"[gauntlet]    ignoring unusable remedy {verdict.remedy[:60]!r}")
            verdict = Verdict(verdict.verdict, verdict.reasons, None,
                              verdict.confidence, verdict.cost_usd)
        result.cost_usd += verdict.cost_usd
        run.cost_usd += verdict.cost_usd
        stalled = bool(judged) and repeated.get(judged, False)

        routing = route(verdict, node, ledger, no_progress=stalled)
        # Keyed by the GATE, matching route()'s own lookup: "this gate has
        # rejected this work N times for reason X". Keying by the judged node
        # would silently disable the anti-thrash rule.
        ledger.record(name, verdict)
        if routing.verdict == REROUTE:
            # The router, not the gate, decides a reroute - so it has to be
            # counted here or the reroute-thrash guard never engages.
            ledger.record_reroute(name, verdict.primary_reason)
        elif routing.verdict == ESCALATE:
            ledger.record_escalation(name, verdict.primary_reason)
        run.verdict = verdict.to_dict()
        run.routing = routing.to_dict()
        logger(f"[gauntlet]    {verdict.verdict} -> {routing.action}"
               f"{' ' + routing.target if routing.target else ''}"
               f"{' (forced)' if routing.forced else ''}")
        notify("verdict", node=name, verdict=verdict.verdict, action=routing.action,
               target=routing.target, forced=routing.forced, reason=routing.reason,
               reasons=[r.code for r in verdict.reasons])

        if routing.action == STOP:
            return finish(ABORTED, routing.reason)

        # Tell whichever node runs next what has already failed here, and make
        # sure it reports back to this gate rather than falling through.
        if routing.target and not verdict.ok:
            block = ledger.as_prompt_block(name)
            if block:
                pending_feedback[routing.target] = block
            if by_name.get(routing.target, {}).get("kind", "work") != "gate":
                return_to[routing.target] = name

        if routing.action == ADVANCE:
            pointer = index_of[routing.target] if routing.target in index_of else pointer + 1
            checkpoint()
            continue

        # REMEDY / ALTERNATIVE / TIER_UP all jump to a named node.
        if routing.target not in index_of:
            return finish(ABORTED, f"routing target {routing.target!r} is not executable")
        pointer = index_of[routing.target]
        checkpoint()

    return finish(PASSED, "all gates passed")


def _judged_node(gate: dict[str, Any], order: list[str], pointer: int) -> str | None:
    """The node a gate is judging: its first dependency, else the previous node."""
    deps = [d for d in (gate.get("depends_on") or []) if d in order]
    if deps:
        return deps[0]
    return order[pointer - 1] if pointer > 0 else None


def harness_compensator(caller: str = "owner", *, dry_run: bool = False,
                        timeout: int = 120):
    """Build a compensator that rolls back THROUGH the signed harness policy.

    Rollback is the most dangerous thing an engine can do unsupervised, so it does
    not get a private path: every compensator must be a declared harness
    capability (enforced at validate time) and is invoked via `harness.py run`.
    That means an undo inherits deny-by-default, argv-only execution, parameter
    validation and the audit trail - and the engine cannot invent a destructive
    action that the policy does not already allow.
    """
    import subprocess

    root = Path(__file__).resolve().parent.parent

    def compensate(capability: str, node: str, params: dict[str, Any] | None = None) -> None:
        argv = [sys.executable, "scripts/harness.py", "run", capability,
                "--caller", caller]
        for key, value in (params or {}).items():
            argv += ["--param", f"{key}={value}"]
        if dry_run:
            argv.append("--dry-run")
        proc = subprocess.run(argv, cwd=str(root), capture_output=True,
                              text=True, timeout=timeout, shell=False)
        if proc.returncode != 0:
            # Surface the harness's own reason; a silent rollback failure would
            # leave the tree dirty while the report claimed it was clean.
            detail = (proc.stderr or proc.stdout or "").strip()[:300]
            raise GauntletError(
                f"compensator {capability!r} for {node!r} failed "
                f"(exit {proc.returncode}): {detail}"
            )

    return compensate


def _compensate(result: GauntletResult, order: list[str], by_name: dict[str, Any],
                compensator, logger) -> None:
    """Run declared compensators for completed mutating nodes, newest first.

    Every compensator must be a harness capability (enforced at validate time),
    so rollback is policy-gated too - the engine cannot invent a destructive act.
    """
    ran = [r.node for r in result.runs]
    for name in reversed(ran):
        spec_node = by_name.get(name) or {}
        capability = spec_node.get("compensate")
        if not capability or name in result.compensated:
            continue
        params = spec_node.get("compensateParams") or {}
        logger(f"[gauntlet] compensating {name} via {capability}")
        if compensator is None:
            # Record the intent so a partial run is never silently left dirty.
            result.compensated.append(f"{name}:{capability}:NOT_RUN(no compensator wired)")
            continue
        try:
            # Params are optional so a simple 2-arg callable still works.
            try:
                compensator(capability, name, params)
            except TypeError:
                compensator(capability, name)
            result.compensated.append(f"{name}:{capability}:ok")
        except Exception as exc:  # noqa: BLE001 - report, never mask
            result.compensated.append(f"{name}:{capability}:FAILED({type(exc).__name__})")


def save_run(result: GauntletResult, spec: dict[str, Any], task: str,
             base_dir: Path | None = None) -> Path:
    """Write run history where the dashboard already looks (memory/workflows).

    Field names match what scripts/dashboard.py's collect_runs() reads, so a
    gauntlet run shows up in the UI without special-casing.
    """
    from datetime import datetime, timezone

    root = base_dir or (Path(__file__).resolve().parent.parent / "memory" / "workflows" / "gauntlet")
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc)
    run_id = f"{stamp:%Y%m%dT%H%M%SZ}-{spec.get('name', 'gauntlet')}"
    payload = {
        "runId": run_id,
        "workflow": spec.get("name", "gauntlet"),
        "engine": SCHEMA,
        "task": task,
        "status": result.status,
        "reason": result.reason,
        "startedAt": stamp.isoformat(timespec="seconds"),
        "costUsd": round(result.cost_usd, 6),
        "stages": [r.to_dict() for r in result.runs],
        "gates": [
            {
                "node": r.node,
                "iteration": r.iteration,
                "verdict": (r.verdict or {}).get("verdict"),
                "reasons": [x.get("code") for x in (r.verdict or {}).get("reasons", [])],
                "action": (r.routing or {}).get("action"),
                "target": (r.routing or {}).get("target"),
                "forced": bool((r.routing or {}).get("forced")),
                "routingReason": (r.routing or {}).get("reason"),
            }
            for r in result.runs if r.kind == "gate"
        ],
        "compensated": result.compensated,
        "nodeRuns": result.node_runs,
    }
    path = root / f"{run_id}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- CLI


def format_graph(spec: dict[str, Any]) -> str:
    """A readable rendering of the graph, gates and their edges included."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import brand

        work, gate, approval, tee, elbow, arrow = (
            brand.WORK, brand.GATE, brand.APPROVAL, brand.TEE, brand.ELBOW, brand.ARROW)
        head = f"{brand.CYAN}{brand.BOLD}gauntlet{brand.RESET} {brand.DIM}graph{brand.RESET}"
    except Exception:  # noqa: BLE001
        work, gate, approval, tee, elbow, arrow = "--", "<>", "!", "+-", "\\-", "->"
        head = "gauntlet graph"

    lines = [f"{head}: {spec.get('name', '(unnamed)')}"]
    for node in spec.get("nodes", []):
        kind = node.get("kind", "work")
        marker = {"gate": gate, "approval": approval}.get(kind, work)
        deps = ", ".join(node.get("depends_on") or []) or "-"
        lines.append(f"  {marker} {node.get('name')} [{kind}] "
                     f"agent={node.get('agent')} deps={deps}")
        for verdict_key, target in (node.get("on") or {}).items():
            lines.append(f"      {tee} {verdict_key:<8} {arrow} {target}")
        if node.get("compensate"):
            lines.append(f"      {elbow} compensate: {node['compensate']}")
    budget = spec.get("budget") or {}
    if budget:
        lines.append(f"  budget: {json.dumps(budget)}")
    return "\n".join(lines)


def format_result(result: GauntletResult) -> str:
    status_line = f"status: {result.status}  ({result.reason})"
    rows = [status_line, f"cost:   ${result.cost_usd:.6f}", "trail:"]
    for run in result.runs:
        bit = f"  {run.node} [{run.kind}] #{run.iteration} {run.status} {run.ms}ms"
        if run.verdict:
            bit += f" verdict={run.verdict['verdict']}"
        if run.routing:
            forced = " FORCED" if run.routing.get("forced") else ""
            bit += f" -> {run.routing['action']}:{run.routing.get('target')}{forced}"
        rows.append(bit)
    if result.compensated:
        rows.append("compensated: " + ", ".join(result.compensated))
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="gauntlet",
        description="Run a gauntlet/v1 graph: work advances only by passing explicit gates.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="validate a spec")
    p_validate.add_argument("spec")

    p_graph = sub.add_parser("graph", help="print the graph")
    p_graph.add_argument("spec")

    p_run = sub.add_parser("run", help="execute a spec")
    p_run.add_argument("spec")
    p_run.add_argument("--task", default="")
    p_run.add_argument("--max-node-runs", type=int, default=None)
    p_run.add_argument("--max-usd", type=float, default=None)
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--quiet", action="store_true")
    p_run.add_argument("--save", action="store_true",
                       help="write run history to memory/workflows/gauntlet (shows in the dashboard)")
    p_run.add_argument("--checkpoint", action="store_true",
                       help="persist state after every node so the run can be resumed")
    p_run.add_argument("--run-id", default=None, help="explicit run id (needed for --resume)")
    p_run.add_argument("--resume", action="store_true",
                       help="continue a checkpointed run without re-running completed nodes")
    p_run.add_argument("--approve", action="append", default=[], metavar="NODE",
                       help="grant an approval node (repeatable)")
    p_run.add_argument("--compensate", action="store_true",
                       help="on ABORT, run declared compensators through the signed harness policy")
    p_run.add_argument("--compensate-caller", default="owner",
                       help="harness caller used for rollback (default: owner)")

    p_runs = sub.add_parser("runs", help="list checkpointed runs")
    p_runs.add_argument("--limit", type=int, default=15)
    p_runs.add_argument("--resumable", action="store_true", help="only unfinished runs")

    p_prune = sub.add_parser("prune", help="drop old run state (TTL)")
    p_prune.add_argument("--days", type=float, default=14.0)

    args = parser.parse_args(argv)

    # These inspect run state, not a spec, so they must be handled before any
    # attempt to read a spec file.
    if args.command in ("runs", "prune"):
        cp = Checkpointer()
        try:
            if args.command == "prune":
                dropped = cp.prune(older_than_days=args.days)
                print(f"pruned {dropped} run(s) older than {args.days} days "
                      f"(unfinished runs are kept)")
                return 0
            rows = cp.resumable(args.limit) if args.resumable else cp.runs(args.limit)
            if not rows:
                print("no checkpointed runs")
                return 0
            for row in rows:
                stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["updated_ts"]))
                print(f"  {row['run_id']:<40} {row['status']:<12} "
                      f"steps={row['supersteps'] or 0:<4} {stamp}")
                if row["reason"]:
                    print(f"      {row['reason'][:110]}")
            return 0
        finally:
            cp.close()

    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"cannot read spec: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"spec is not valid JSON: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        errors = validate_spec(spec)
        if errors:
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            print(f"{len(errors)} error(s)", file=sys.stderr)
            return 1
        print("spec is valid")
        return 0

    if args.command == "graph":
        print(format_graph(spec))
        return 0

    logger = (lambda msg: None) if args.quiet else (lambda msg: print(msg, flush=True))
    cp = Checkpointer() if (args.checkpoint or args.resume) else None
    compensator = harness_compensator(args.compensate_caller) if args.compensate else None
    try:
        result = run_gauntlet(
            spec, args.task, echo_executor, logger=logger,
            max_node_runs=args.max_node_runs, max_usd=args.max_usd,
            checkpointer=cp, run_id=args.run_id, resume=args.resume,
            approved=args.approve, compensator=compensator,
        )
    except GauntletError as exc:
        print(f"gauntlet error: {exc}", file=sys.stderr)
        return 2
    finally:
        if cp is not None:
            cp.close()

    print(json.dumps(result.to_dict(), indent=2) if args.json else format_result(result))
    if result.resumable:
        print(f"\nresume with:  python scripts/gauntlet.py run {args.spec} "
              f"--resume --run-id {result.run_id} --approve <node>")
    if args.save:
        print(f"saved: {save_run(result, spec, args.task)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
