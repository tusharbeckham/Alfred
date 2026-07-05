# Alfred — Training History

Append-only log of the eval-driven self-improvement loop. Each entry records a prompt/skill
change, the eval delta that justified it, and the accept/revert decision. (This is prompt &
skill optimization — NOT model-weight training.)

## Format
```
## <date> — <target prompt/skill> — vN
- Motivation: <failing category + case ids>
- Change: <what changed, 1–2 lines>
- Baseline aggregate: <x.xx> → Candidate: <y.yy>  (category <cat>: a.aa → b.bb)
- Regression check: <pass/fail — any category that dropped>
- Decision: ACCEPT | REVERT
- Version: training/prompt-versions/<file>
```

## Entries

### 2026-07-05 — alfred-researcher identity.txt — v1
- Motivation: qa/research (qa-05) scored 0.900 — lowest in QA suite. Rubric deductions:
  format_compliance −0.15 (no citation format), completeness −0.15 (confidence not required),
  conciseness −0.15 (no 'lead with answer' instruction).
- Change: Added explicit output contract (Answer/Confidence/Sources/Caveats sections),
  mandatory HIGH/MEDIUM/LOW confidence with reason, citation format `[n] title — URL/path`,
  and 'lead with the answer, then supporting detail' to SYNTHESIZE step.
- Baseline aggregate: 0.968 → Candidate: 0.978  (category research: 0.900 → 0.960)
- Regression check: PASS — no other category moved (all δ = 0.000, tolerance = 0.02)
- Decision: **ACCEPT**
- Version: `training/prompt-versions/alfred-researcher-identity-v1.txt`
- Evidence: `evals/results/2026-07-05T152900Z-regression.json`

### 2026-07-05 — alfred-coder identity.txt — v1
- Motivation: coding/refactor (code-08, code-11) scored 0.928 — lowest in coding suite.
  Rubric deductions: completeness −0.20 (characterization tests not named as strategy),
  conciseness −0.15 (refactoring approach ad-hoc rather than purposeful).
- Change: Added single sentence to step 2: `For refactors: write characterization tests
  first to lock current behavior, then restructure against them.` Conditional clause —
  activates only for refactoring tasks.
- Baseline aggregate: 0.963 → Candidate: 0.967  (category refactor: 0.928 → 0.963)
- Regression check: PASS — no other category moved (all δ = 0.000, tolerance = 0.02)
- Decision: **ACCEPT**
- Version: `training/prompt-versions/alfred-coder-identity-v1.txt`
- Evidence: `evals/results/2026-07-05T154130Z-regression.json`

## Current baselines
- coding suite: aggregate **0.967** (updated 2026-07-05, post-coder-v1 acceptance)
- qa suite: aggregate **0.978** (updated 2026-07-05, post-researcher-v1 acceptance)
