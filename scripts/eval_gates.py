#!/usr/bin/env python3
"""Measure how reliably the local model behaves as a GATE.

A gate is a classifier with a fixed schema, which is the easiest task class - but a
7B model still gets it wrong in specific, measurable ways. Observed in real runs:
it invents reason codes and reaches for ABORT when it is merely unsure.

This gives a number to that, so a prompt change can be judged instead of guessed.
Fixtures are deliberately unambiguous: a human would not hesitate on any of them,
so a wrong verdict is a model failure and not a hard call.

    python scripts/eval_gates.py            # default 3 samples per fixture
    python scripts/eval_gates.py --samples 5 --provider lmstudio
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gauntlet as g  # noqa: E402
import providers as P  # noqa: E402

CRITERIA = (
    "The build passes and the tests pass, with exit codes quoted as evidence. "
    "PASS requires citable evidence. RETRY if it is fixable and you know the remedy. "
    "ABORT only if the work is unsafe or no path forward exists."
)

#: (name, artifact, expected verdict). Unambiguous on purpose.
FIXTURES = [
    (
        "clean-pass",
        "Ran the build: `npm run build` -> exit 0.\n"
        "Ran the tests: `npm test` -> exit 0, 128 passed, 0 failed.\n"
        "No files outside src/ were touched.",
        g.PASS,
    ),
    (
        "tests-failing",
        "Ran the build: `npm run build` -> exit 0.\n"
        "Ran the tests: `npm test` -> exit 1, 125 passed, 3 FAILED in test_auth.js.\n"
        "Failures: expected 200 got 401 in three auth cases.",
        g.RETRY,
    ),
    (
        "no-evidence",
        "I made the change and it should work fine now. Everything looks good.",
        g.RETRY,   # not verifiable -> cannot PASS; a remedy exists (produce evidence)
    ),
]


def judge(artifact: str, provider: str, model: str | None, codes) -> tuple[g.Verdict, int, str]:
    prompt = g.build_gate_prompt(CRITERIA, artifact, "", codes)
    started = time.time()
    result = P.chat(prompt, provider=provider, model=model,
                    system=("You are a GATE in an execution graph. Reply with exactly one "
                            "JSON object and nothing else."),
                    temperature=0.0, max_tokens=260, timeout=240)
    ms = int((time.time() - started) * 1000)
    if not result["ok"]:
        return g.Verdict(g.ABORT, (g.Reason("PROVIDER_ERROR", result["error"][:120]),)), ms, ""
    raw = result["text"]
    verdict = g.normalize_verdict(g.Verdict.parse(raw), codes)
    return verdict, ms, raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval_gates")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--no-vocabulary", action="store_true",
                        help="omit the controlled code list, to measure its effect")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    provider = args.provider or P.pick() or "lmstudio"
    codes = None if args.no_vocabulary else g.DEFAULT_REASON_CODES

    rows = []
    correct = parse_failures = invented = 0
    total = 0
    for name, artifact, expected in FIXTURES:
        for sample in range(args.samples):
            verdict, ms, raw = judge(artifact, provider, args.model, codes)
            total += 1
            hit = verdict.verdict == expected
            correct += int(hit)
            if verdict.primary_reason in ("GATE_UNPARSEABLE", "GATE_INVALID"):
                parse_failures += 1
            if verdict.primary_reason == "OTHER":
                invented += 1
            rows.append({
                "fixture": name, "sample": sample, "expected": expected,
                "got": verdict.verdict, "correct": hit,
                "code": verdict.primary_reason, "ms": ms,
            })
            if not args.json:
                mark = "ok " if hit else "MISS"
                print(f"  {mark} {name:<14} expected {expected:<9} got {verdict.verdict:<9}"
                      f" code={str(verdict.primary_reason):<18} {ms}ms")

    summary = {
        "provider": provider,
        "model": args.model,
        "vocabulary": not args.no_vocabulary,
        "samples": total,
        "correct": correct,
        "accuracy": round(correct / total, 3) if total else 0.0,
        "unparseable": parse_failures,
        "codesFoldedToOther": invented,
    }
    if args.json:
        print(json.dumps({"summary": summary, "rows": rows}, indent=2))
    else:
        print(f"\n  accuracy {correct}/{total} = {summary['accuracy']:.0%}"
              f"  unparseable={parse_failures}  folded-to-OTHER={invented}"
              f"  provider={provider}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
