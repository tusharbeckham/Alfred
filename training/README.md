# Alfred — Training Store

The versioned home of the self-improvement loop.

- `history.md` — the append-only improvement log (deltas, decisions).
- `prompt-versions/` — every optimized prompt is saved here as `<agent-or-prompt>.vN.txt`
  before it replaces the live version, so any change can be rolled back instantly.
- A/B logs — when comparing two prompt versions, the per-case scores are saved next to the
  version under `prompt-versions/`.

## How a change flows
1. `alfred-evaluator` scores the live prompt against `evals/` (writes `evals/results/`).
2. `alfred-trainer` finds the weakest category and its failing cases.
3. `alfred-prompt-engineer` writes a candidate → `prompt-versions/<name>.vN.txt`.
4. `alfred-evaluator` re-scores the FULL suite on the candidate.
5. `alfred-trainer` applies the acceptance rule in `evals/rubric.json`:
   accept only if the target improves and nothing regresses; else revert.
6. The delta + decision is appended to `history.md`.

Nothing here trains model weights. "Training" = better prompts and skills, measured.
