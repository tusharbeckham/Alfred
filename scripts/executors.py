#!/usr/bin/env python3
"""Executors - bridge the gauntlet engine to real models.

WHY THIS IS A SEPARATE MODULE
-----------------------------
`gauntlet.py` deliberately knows nothing about models: it takes an
`executor(agent, task, timeout)` callable so the engine stays pure and unit-testable
without a network. `providers.py` deliberately knows nothing about graphs. This is
the thin seam that joins them, and it is where routing policy lives.

THE ROUTING BET
---------------
From `.kiro/steering/token-budget.md`: route to the cheapest tier that can do the
job correctly. Gates are the most numerous node type and do the easiest task
(classification against a fixed schema), so they run on the cheapest reachable
tier. Real engineering escalates. If gates are cheap and plentiful, adding more
gates should make the system *more* reliable and *cheaper* at once - that is the
falsifiable claim in the graph plan, and this module is what makes it testable.

Measured caveat on this machine: local 7B gates take 25-35s on CPU. So when a
hosted cheap tier is configured it is preferred FOR GATES specifically, while bulk
generation can stay local. Correctness first, then speed, then cost.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import providers as P  # noqa: E402

#: Which tier an agent belongs to. Matched as a substring, longest first, so
#: `alfred-security` beats a generic `alfred-` rule. Mirrors the plan's section 4.1.
AGENT_TIERS: dict[str, str] = {
    # Judgement and classification: short structured output, highest frequency.
    "reviewer": "gate",
    "evaluator": "gate",
    # Bulk generation and mechanical work.
    "coder": "bulk",
    "tester": "bulk",
    "docs": "bulk",
    "planner": "bulk",
    "debugger": "bulk",
    # Work where being wrong is expensive.
    "architect": "hard",
    "security": "hard",
    "leader": "hard",
    "manager": "hard",
}

#: Tier -> ordered provider preference. First reachable one wins.
TIER_PROVIDERS: dict[str, list[str]] = {
    # Gates: cheapest and fastest. A hosted cheap tier beats a slow local 7B here
    # because gate latency multiplies across every node.
    "gate": ["deepseek", "nvidia", "freebuff", "lmstudio", "ollama"],
    # Bulk: local first - it is free and privacy-preserving, and length is fine.
    "bulk": ["lmstudio", "ollama", "deepseek", "nvidia", "freebuff"],
    # Hard: prefer a stronger hosted model; fall back to local rather than fail.
    "hard": ["nvidia", "deepseek", "openrouter", "lmstudio", "ollama"],
}

GATE_SYSTEM = (
    "You are a GATE in an execution graph. You judge work and emit a verdict.\n"
    "Reply with EXACTLY ONE JSON object and no other text, no prose, no code fence.\n"
    "Use your OWN reason codes describing what you actually observed; never copy "
    "codes or phrases out of the prompt."
)

WORK_SYSTEM = (
    "You are a node in an execution graph. Do the task and report the result "
    "concisely - aim for under 200 words. State the commands you ran and their exit "
    "codes, what you verified, and what you did not. Never invent output, file "
    "contents, or test results.\n"
    "Your output is judged by an automated gate, so lead with the evidence."
)

#: Gates are small classification calls. A generous timeout here does not help - it
#: just means a stuck gate blocks the graph for longer - and a gate that cannot answer
#: quickly is better treated as a failure the ladder can route around.
GATE_TIMEOUT = 120.0
WORK_TIMEOUT = 300.0


def tier_for(agent: str) -> str:
    """Resolve an agent name to a routing tier."""
    lowered = (agent or "").lower()
    for needle in sorted(AGENT_TIERS, key=len, reverse=True):
        if needle in lowered:
            return AGENT_TIERS[needle]
    return "bulk"


def _looks_like_gate(task: str) -> bool:
    """Gate prompts are built by build_gate_prompt and carry these markers."""
    return "ACCEPTANCE CRITERIA" in task or '"verdict"' in task


@dataclass
class Route:
    """One resolved routing decision, kept for the report."""

    agent: str
    tier: str
    provider: str
    model: str
    ms: int = 0
    usd: float | None = 0.0
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"agent": self.agent, "tier": self.tier, "provider": self.provider,
                "model": self.model, "ms": self.ms, "usd": self.usd, "ok": self.ok}


@dataclass
class ExecutorReport:
    """What the run actually cost and where it went. Printed after a graph run."""

    routes: list[Route] = field(default_factory=list)
    total_usd: float = 0.0
    unknown_cost_calls: int = 0

    def add(self, route: Route) -> None:
        self.routes.append(route)
        if route.usd is None:
            # A hosted provider with no configured rate. Counting it as 0 would
            # under-report spend, so it is counted separately and reported.
            self.unknown_cost_calls += 1
        else:
            self.total_usd += route.usd

    def by_provider(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for route in self.routes:
            counts[route.provider] = counts.get(route.provider, 0) + 1
        return counts

    def summary(self) -> str:
        mix = ", ".join(f"{name}x{count}" for name, count in self.by_provider().items())
        cost = f"${self.total_usd:.4f}"
        if self.unknown_cost_calls:
            cost += f" + {self.unknown_cost_calls} call(s) at unknown rates"
        return f"{len(self.routes)} calls [{mix or 'none'}] {cost}"


def preflight_latency(provider: str = "lmstudio", *, budget_s: float = 25.0) -> dict:
    """Time one tiny call so a run can warn BEFORE spending minutes.

    A 7B model on CPU is fine when the machine is idle and unusable when it is not.
    Memory pressure was measured turning an 8s call into a 120s timeout, so latency
    is checked rather than assumed.
    """
    started = time.time()
    result = P.chat("Reply with the single word: ready", provider=provider,
                    temperature=0.0, max_tokens=5, timeout=budget_s + 10)
    elapsed = time.time() - started
    return {
        "provider": provider,
        "ok": bool(result.get("ok")),
        "seconds": round(elapsed, 1),
        "slow": elapsed > budget_s,
        "error": None if result.get("ok") else str(result.get("error"))[:160],
    }


def advise(check: dict, node_count: int) -> str | None:
    """A one-line, actionable warning - or None when things look fine."""
    if not check["ok"]:
        return (f"{check['provider']} did not answer a 5-token call "
                f"({check['error']}). Start it, or configure an API key.")
    if check["slow"]:
        estimate = int(check["seconds"] * node_count)
        return (f"{check['provider']} took {check['seconds']}s for a 5-token call. "
                f"A {node_count}-node graph could take ~{estimate // 60}m{estimate % 60}s. "
                f"Free memory, use a smaller model for gates, or add an API key.")
    return None
    for name in TIER_PROVIDERS.get(tier, TIER_PROVIDERS["bulk"]):
        if name in reachable:
            return name
    return None


def resolve_provider(tier: str, reachable: set[str]) -> str | None:
    for name in TIER_PROVIDERS.get(tier, TIER_PROVIDERS["bulk"]):
        if name in reachable:
            return name
    return None


def reachable_providers(*, timeout: float = 5.0) -> set[str]:
    """Which providers are usable right now. Probed once per executor, not per call."""
    return {s.name for s in P.probe_all(timeout=timeout)
            if s.configured and s.reachable}


def make_executor(
    *,
    prefer: str | None = None,
    report: ExecutorReport | None = None,
    max_usd: float | None = None,
    logger: Callable[[str], None] = lambda _msg: None,
    probe_timeout: float = 5.0,
) -> Callable[..., str]:
    """Build a gauntlet-compatible executor backed by the provider registry.

    ``prefer`` pins every node to one provider (useful for testing and for "run
    this entirely locally"). Otherwise each node is routed by its agent's tier.

    The returned callable sets ``last_meta`` after every call, which is how
    `gauntlet.run_gauntlet` accumulates cost and enforces its USD budget.
    """
    report = report if report is not None else ExecutorReport()
    available = {prefer} if prefer else reachable_providers(timeout=probe_timeout)
    if prefer and prefer not in P.PROVIDERS:
        raise ValueError(f"unknown provider {prefer!r}")
    logger(f"[exec] reachable providers: {', '.join(sorted(available)) or 'none'}")

    def executor(agent: str, task: str, timeout: float | None = None) -> str:
        tier = "gate" if _looks_like_gate(task) else tier_for(agent)
        provider = prefer or resolve_provider(tier, available)
        if provider is None:
            executor.last_meta = {}
            return ("[ERROR] no provider is reachable for tier "
                    f"{tier!r}; configure a key or start LM Studio")

        # Budget is enforced by the engine too, but stopping here avoids making a
        # call we already know we cannot afford.
        if max_usd is not None and report.total_usd >= max_usd:
            executor.last_meta = {}
            return f"[ERROR] executor budget of ${max_usd:.4f} is exhausted"

        is_gate = tier == "gate"
        started = time.time()
        result = P.chat(
            task,
            provider=provider,
            system=GATE_SYSTEM if is_gate else WORK_SYSTEM,
            temperature=0.0 if is_gate else 0.4,
            # Bounded output: a work node that writes an essay makes the gate that
            # reads it slow, which is the highest-frequency node type.
            max_tokens=200 if is_gate else 420,
            timeout=timeout or (GATE_TIMEOUT if is_gate else WORK_TIMEOUT),
        )
        ms = int((time.time() - started) * 1000)

        if not result["ok"]:
            route = Route(agent=agent, tier=tier, provider=provider, model="?",
                          ms=ms, usd=0.0, ok=False)
            report.add(route)
            executor.last_meta = {"backend": provider, "cost_usd": 0.0}
            logger(f"[exec] {agent} via {provider} FAILED: {result['error'][:120]}")
            return f"[ERROR] {provider}: {result['error']}"

        usd = result.get("estimatedUsd")
        route = Route(agent=agent, tier=tier, provider=provider,
                      model=result.get("model", "?"), ms=ms, usd=usd)
        report.add(route)
        executor.last_meta = {
            "backend": provider,
            "model": result.get("model"),
            "cost_usd": usd or 0.0,
        }
        logger(f"[exec] {agent} [{tier}] via {provider} {ms}ms")
        return result["text"]

    executor.last_meta = {}          # type: ignore[attr-defined]
    executor.report = report         # type: ignore[attr-defined]
    executor.available = available   # type: ignore[attr-defined]
    return executor


def make_stub_executor(delay: float = 0.0) -> Callable[..., str]:
    """Deterministic executor for demos and tests. No network, no cost."""
    def executor(agent: str, task: str, timeout: float | None = None) -> str:
        if delay:
            time.sleep(delay)
        executor.last_meta = {"backend": "stub", "cost_usd": 0.0}
        if _looks_like_gate(task):
            return '{"verdict":"PASS","reasons":[],"confidence":1.0}'
        return f"[{agent}] completed"
    executor.last_meta = {}          # type: ignore[attr-defined]
    executor.report = ExecutorReport()  # type: ignore[attr-defined]
    return executor


if __name__ == "__main__":
    import json

    print("agent tier routing:")
    for agent in ("alfred-reviewer", "alfred-coder", "alfred-architect",
                  "alfred-security", "alfred-docs", "something-else"):
        print(f"  {agent:<20} -> {tier_for(agent)}")
    print("\ntier preferences:")
    for tier, order in TIER_PROVIDERS.items():
        print(f"  {tier:<6} {' > '.join(order)}")
    print("\nreachable now:", ", ".join(sorted(reachable_providers())) or "none")
    for tier in TIER_PROVIDERS:
        print(f"  {tier:<6} resolves to "
              f"{resolve_provider(tier, reachable_providers()) or '(nothing)'}")
