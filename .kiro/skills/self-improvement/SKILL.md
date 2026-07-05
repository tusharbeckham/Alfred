---
name: self-improvement
description: Eval-driven improvement of Alfred's prompts and skills (NOT model training). Use when running the training loop, scoring outputs, or diagnosing weak capabilities.
---

# Self-Improvement (eval-driven)

Alfred improves by optimizing prompts and skills against evals — never by training model
weights. No Kaggle, no gradients.

## The loop
1. **Evaluate** — run eval datasets (`evals/*.json`) through the target agent/prompt.
2. **Score** — grade each case with its rubric; aggregate per-category pass rate.
3. **Diagnose** — find the lowest-scoring capability and the specific failing cases.
4. **Optimize** — prompt-engineer rewrites the target prompt (minimal, surgical).
5. **Regression-test** — re-run the FULL eval set. Accept only if target improves AND
   nothing else regresses. Else revert.
6. **Accumulate** — promote durable lessons into a skill or memory/learnings.md; append
   the delta to `training/history.md`.

## Eval design
- Each case: `id`, `input`, `expected` (or rubric criteria), `category`, `weight`.
- Cover happy path, edge cases, and known past failures (regression cases).
- Deterministic scoring where possible; rubric-based where judgment is needed.

## Rubric scoring (0–1 per criterion)
- Correctness, completeness, safety-adherence, format-compliance, conciseness.
- Weighted sum → case score. Category score = mean of its cases.

## Guardrails
- Never overfit a prompt to the eval set — keep cases representative.
- A change that fixes one category but regresses another is REJECTED.
- Version every prompt change; keep the ability to roll back instantly.
