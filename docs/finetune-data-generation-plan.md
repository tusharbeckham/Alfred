# Fine-Tuning Data Generation Plan — Math/Physics/Coding for Granite 4.1 8B

> Uses the Kiro/Opus agents (alfred-math, alfred-physics) to generate VERIFIED training
> data that makes the offline local model stronger at the Owner's scientific ML domain.

## Honest limits (non-negotiable)

- An 8B model fine-tuned on ~200–1000 curated pairs will improve at the Owner's domain
  and style. It will NOT match a frontier model (Opus 4.6/4.8) on novel reasoning.
- Purpose: offline convenience — fast, free, and personalized, not a replacement.
- Any agent that claims otherwise is wrong. This constraint is baked into agent identities.

## Data generation pipeline

### 1. Problem sourcing (alfred-math, alfred-physics)

Each agent generates problems at graduate scientific-ML level across:
- **Math**: linear algebra for ML, calculus/optimization, ODEs/PDEs, probability,
  numerical methods, proofs relevant to ML theory.
- **Physics**: classical mechanics (Lagrangian/Hamiltonian), computational physics,
  stat mech, quantum basics, PINNs, scientific ML applications.
- **Coding**: numerical implementations, simulations, data pipelines — the intersection.

Source strategies:
- Parametric variants of textbook problems (change constants, domain, constraints).
- Problems arising from the Owner's actual project work (anonymized if needed).
- "Explain and implement" pairs: derive a result, then code it.
- Debugging: broken code with physics/math errors → corrected code with explanation.

**Anti-leakage rule**: never copy from eval sets (`evals/`). Never use verbatim textbook
problems that appear in public benchmarks. Parametric modification is required.

### 2. Solution generation (alfred-math, alfred-physics)

Each agent produces a FULL worked solution:
- Step-by-step derivation (no skipped steps — the 8B needs the chain).
- Units/dimensions checked throughout (physics).
- Final answer boxed/highlighted.
- Code implementation where relevant (Python, tested).

### 3. Verification (MANDATORY — no unverified data enters the dataset)

Every pair is checked before acceptance. Methods (use ≥1, prefer 2):

| Method | When |
|--------|------|
| **Symbolic cross-check** | Derive the same result a different way |
| **Numerical spot-check** | Plug in concrete values; confirm answer matches |
| **Code execution** | Run the implementation; assert correctness |
| **Dimensional analysis** | Confirm units are consistent (physics) |
| **Limiting cases** | Check behavior at extremes (0, ∞, known special cases) |
| **Peer review** | alfred-math verifies physics derivations and vice versa |

If verification FAILS: discard the pair. Do not patch it without re-verifying from scratch.
Label each accepted pair with the verification method used.

### 4. Formatting → `data/finetune/examples.md`

Use the existing format exactly:

```
### PROMPT
<problem, as the user would ask the local model>
### OUTPUT
<full worked solution — derivation + code if applicable>
### END
```

Guidelines for the PROMPT field:
- Write it as the Owner would ask: concise, technical, expecting a complete answer.
- Include enough context that the answer is self-contained.

Guidelines for the OUTPUT field:
- Full chain of reasoning. Explicit steps. No "it can be shown that…"
- Code in fenced blocks with language tag.
- Short — aim for ≤800 tokens. The 8B has limited context (2048 tokens in training).
  Split large problems into sub-problems if needed.

### 5. Dedup and quality filter

Before running `build-finetune-jsonl.ps1`:
- Dedup by problem semantics (not just string match) — two problems that differ only by
  variable names are the same problem.
- Remove any pair where the solution uses knowledge beyond what an 8B can learn to
  reproduce (e.g., citing an obscure theorem without proving it).
- Balance: aim for ~40% math, ~40% physics, ~20% pure coding (numerical/scientific).

### 6. Build JSONL

```powershell
powershell -File scripts/build-finetune-jsonl.ps1
```

This reads `data/finetune/examples.md`, deduplicates, shuffles, splits train/val (85/15),
and writes `data/finetune/train.jsonl` + `val.jsonl`.

### 7. Fine-tune on Kaggle (free T4 GPU)

Cadence: **monthly**, or when examples.md accumulates ≥50 new verified pairs.

1. Upload `train.jsonl` as a Kaggle Dataset.
2. Run `notebooks/alfred-coder-finetune-colab.ipynb` (works on both Colab and Kaggle).
3. Download the GGUF (Q4_K_M).
4. Load in LM Studio → `alfred-coder/` model directory → restart server.

The notebook already handles: Unsloth QLoRA (r=16), 2 epochs, 2e-4 LR, GGUF export.
No changes needed to the training notebook for math/physics data — the format is identical.

## Skill assignments

| Agent | Skills to load |
|-------|---------------|
| `alfred-math` | `mathematics`, `coding` |
| `alfred-physics` | `physics`, `coding` |

Both skills already exist in `.kiro/skills/`. The agents reference them in their identity
files via "Load the `X` and `Y` skills."

## Model tier

| Agent | Model | Justification |
|-------|-------|---------------|
| `alfred-math` | `claude-opus-4.6` | Needs strong reasoning for proofs and derivations |
| `alfred-physics` | `claude-opus-4.6` | Needs strong reasoning for physics + code |

Opus 4.6 (not 4.8) balances capability vs. cost — these agents generate training data in
batch, so cost matters. Escalate to 4.8 only if a specific problem defeats 4.6.

## Safety guardrails

- **No eval leakage**: never source problems from `evals/` or known benchmark datasets.
- **No fabrication**: every solution must be verified. The goal is to teach the 8B
  CORRECT reasoning, not quantity. 10 perfect pairs > 100 unverified ones.
- **No overpromising**: the fine-tuned 8B is better at *this domain in this style*.
  It is not generally smarter. Agents must not claim otherwise in any output.
- **No sensitive data**: no real credentials, personal data, or proprietary research
  results in training pairs (unless the Owner explicitly approves).

## Cadence summary

| Activity | Frequency |
|----------|-----------|
| Generate + verify pairs (batch of 20–30) | Weekly (overnight run) |
| Accumulate in `data/finetune/examples.md` | Continuous |
| Build JSONL + fine-tune on Kaggle | Monthly (or at 50+ new pairs) |
| Eval fine-tuned model vs base | After each fine-tune (manual spot-check) |
| Retire stale/superseded pairs | Quarterly |
