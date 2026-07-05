# Alfred — Recursive Self-Improvement (RSI) Design

Status: **approved** — produced by the alfred-architect + alfred-researcher + alfred-reviewer
pipeline on 2026-07-05; reviewer verdict **SHIP IT**. Owner-directed pivot: NO local model.

## 1. What RSI is here — and its hard limits (read this first)
- Alfred is an **orchestration layer** over Kiro's Claude models. RSI = **eval-driven
  optimization of prompts, skills, routing, and memory**. It is **NOT** model-weight training.
- Honest ceiling: we **cannot make a model smarter than the underlying Opus 4.8 / Sonnet 4.6**.
  What RSI improves is how *reliably and closely* the system reaches that ceiling — via sharper
  prompts, better skills, smarter routing, and richer memory. Anyone promising more is selling hype.
- Strongest capability available = `claude-opus-4.8` at `effort: max` (ultrathink), already on
  `alfred-manager` and `alfred-leader`. Hard work routes there.

## 2. The loop
Evaluate → Score (`evals/rubric.json`) → Diagnose weakest category → Optimize (alfred-prompt-engineer,
one **minimal** change) → Regression-test the **full** suite → Accept only if the gate passes →
Version under `training/prompt-versions/` + log delta to `training/history.md` → repeat.
Bounded to **≤3 iterations per `train.ps1` run** for credit control.

## 3. Acceptance gate (anti-regression + anti-gaming)
Accept a change ONLY if **all** hold (encoded in `evals/rubric.json` `acceptance_rule`):
1. the targeted category score **improves**, AND
2. **no** other category regresses below `prior − 0.02`, AND
3. aggregate is non-negative vs the accepted baseline.
Otherwise **REVERT**. Anti-gaming measures (Phase 2+): rotate a held-out case subset once the set
is ≥25 cases; experiment with **cross-model judging** (Sonnet scores Opus) to cut self-preference
bias; enforce a prompt/skill length lint (≤120 lines); log token/credit cost per iteration.

## 4. Metrics
Per-case = weighted mean of {correctness .35, completeness .20, safety .20, format .15,
conciseness .10} × case weight. Track category scores, aggregate, and `pass_threshold = 0.75`.
All deltas recorded in `training/history.md`.

## 5. Phased plan
- **Phase 0 (now):** baseline eval — read-only measurement. `scripts/run-eval-loop.ps1 -Suite all`.
  Produces real numbers + the weakest category. Validates the loop end-to-end.
- **Phase 1:** target the weakest category with ONE minimal prompt/skill fix; regression-test;
  accept/revert; log. `scripts/train.ps1 -Suite <weakest>`.
- **Phase 2:** grow eval set to 25+ cases — **DONE: 28 cases (18 coding + 10 qa), 2026-07-05**; add
  held-out rotation; cross-model judging experiment; length lint (prompts + skills); per-iteration cost logging.
- **Phase 3:** nightly `train.ps1` + morning report; extend rubric to routing/orchestration quality.

## 6. First improvement to ship
**Run the baseline eval.** Zero-risk (read-only), unblocks the acceptance gate (it needs a baseline
to compare against), validates infra (evaluator → rubric → results file), and pinpoints the weakest
category for Phase 1. No other starting point makes sense — you cannot optimize what you haven't measured.

## Reviewer notes folded in
- 30% holdout of 15 cases ≈ 4 cases (too few) → grow to 25+ before splitting; until then rely on the
  regression gate + Owner spot-check.
- Add cross-model judging; extend length lint to skills; log credit cost per iteration for spend visibility.

## Current baseline & first cycle (measured 2026-07-05)
- **Baseline:** qa **0.968**, coding **0.969** (all 15 cases pass ≥0.75). Weakest: qa/research 0.900,
  coding/refactor 0.928, coding/implementation 0.939. Canonical file: `evals/results/baseline.json`.
- **Cycle #1** (`train.ps1 -Suite qa`): alfred-researcher gained an output contract
  (Answer/Confidence/Sources/Caveats) + citation format. research 0.900→0.960 (+0.060), no regression,
  **ACCEPTED**. qa aggregate **0.968 → 0.978**. Rollback: `training/prompt-versions/alfred-researcher-identity-v1.txt`.
  Full log: `training/history.md`. Regression evidence: `evals/results/2026-07-05T152900Z-regression.json`.
- **Cycle #2** (`train.ps1 -Suite coding`, on the expanded 18-case suite): weakest = refactor 0.928.
  alfred-coder gained one conditional line ("For refactors: write characterization tests first…").
  refactor 0.928→0.963 (+0.035), coding aggregate 0.963→0.967, no regression, **ACCEPTED**.
  Rollback: `training/prompt-versions/alfred-coder-identity-v1.txt`. Evidence: `evals/results/2026-07-05T154130Z-regression.json`.
- **Eval suite hardened 2026-07-05:** expanded 15 → 28 cases. The bigger coding suite surfaced real weak
  spots the 10-case set hid: **concurrency 0.933, implementation 0.943, api-design 0.943**.
- **Next candidates:** coding/concurrency (0.933), coding/implementation (0.943). A QA re-baseline on the
  expanded 10-case set is still pending (the qa cycle predates the qa expansion).
