---
name: prompt-engineering
description: Designing and optimizing agent system prompts and task prompts (the Prompt Service). Use when creating an agent persona or improving a prompt against eval evidence.
---

# Prompt Engineering (the Prompt Service)

`alfred-prompt-engineer` owns every agent's `identity.txt` and the `prompts/` catalog.

## Anatomy of a strong agent prompt
1. **Identity** — who the agent is and its single clear role.
2. **Operating procedure** — numbered steps for how it works.
3. **Constraints & safety** — what it must never do; escalation triggers.
4. **Output contract** — exact format the caller expects.
5. **Voice** — tone and brevity.

## Principles
- Be specific over verbose. Every sentence must change behavior.
- State the output format explicitly; models comply with concrete contracts.
- Prefer positive instructions ("do X") plus a short list of hard "never" rules.
- Encode decision procedures and escalation, not just knowledge.
- Don't duplicate steering — reference it; keep prompts lean.

## Optimizing against evals
1. Read the failing cases and the rubric scores. Find the true gap.
2. Make the smallest change that closes it (add a constraint, tighten the format).
3. Version under `training/prompt-versions/`; log rationale in `training/history.md`.
4. Re-score via alfred-evaluator; keep the winner, else revert.

## Never
- Bloat a prompt to chase one case. Regress a passing capability to fix a failing one.
- Bury the output contract. Contradict the steering files.

## Deliverable
The improved prompt + a diff-style rationale + the version id.
