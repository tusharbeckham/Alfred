---
name: debugging
description: Systematic diagnosis of failures — reproducing, isolating, and root-causing bugs and test failures. Use when something is broken, failing, or behaving unexpectedly.
---

# Debugging

## Method (scientific, not guess-and-check)
1. REPRODUCE reliably. Capture the exact error, stack trace, and inputs.
2. OBSERVE. Read the failing code and the failing test. Gather facts before theories.
3. HYPOTHESIZE a single root cause supported by the evidence.
4. TEST the hypothesis with the smallest probe (a log, a breakpoint, a unit test).
5. FIX the root cause, not the symptom. Add a regression test that fails before the fix.
6. VERIFY the fix and confirm nothing else regressed.

## Techniques
- Bisect: `git bisect` for regressions; comment-out/binary-search for logic bugs.
- Read the error literally — most bugs are exactly what the message says.
- Reproduce at the lowest level (unit) before debugging the whole system.
- Diff a working vs broken state (env, deps, config, data).
- For flaky tests: look for ordering, time, randomness, shared state, network.

## Common root causes
- Off-by-one, null/undefined, type coercion, async race, mutation of shared state,
  incorrect assumptions about a library, environment/config drift, stale cache.

## Discipline
- Never weaken a test to make it pass. If a test is wrong, prove it and say so.
- If stuck after 2 genuine attempts, write down what you know and what you ruled out,
  then escalate — do not thrash.
- Record the root cause and the lesson in memory/learnings.md.
