# Alfred — Fine-Tuning Data Pipeline for the Local Model

> **Scope:** Use the online Kiro/Opus agents to generate VERIFIED instruction–response
> pairs that are then used to fine-tune Granite 4.1 8B (via LM Studio) for the Owner's
> math/physics/coding domain. This makes the offline model meaningfully better in that
> narrow domain and in the Owner's style.
>
> **Hard honest constraint:** Fine-tuning an 8B model on domain data improves it for the
> Owner's domain and style. It will NOT match a frontier model in general reasoning,
> novel proofs, or broad coding. No agent may claim otherwise — ever.

---

## Why this is worth doing

When Kiro is unavailable, the local Granite 4.1 8B handles coding and simple automation.
Without fine-tuning it has no knowledge of:
- The Owner's math/physics notation and style preferences.
- Alfred's conventions (concise reports, verified results, "sir" register).
- The specific subdomains the Owner works in (PINNs, solar modeling, NumPy/PyTorch idioms).

A domain fine-tune closes that gap cheaply. The output is a bespoke model that handles
the Owner's *routine* domain work offline — not a frontier replacement.

---

## Data quality principle

Every fine-tuning example must be **verified** by an Opus agent before it enters the
dataset. Unverified examples corrupt the model. The pipeline enforces a verification gate;
examples that fail are discarded, not patched.

---

## Dataset domains and target sizes

| Domain           | Target pairs | Notes                                           |
|------------------|--------------|-------------------------------------------------|
| Mathematics      | 300–500      | Algebra, calculus, LinAlg, probability, proofs  |
| Physics / PINNs  | 300–500      | Mechanics, E&M, thermo, PINN loss derivations   |
| Scientific coding| 400–600      | NumPy/SciPy/PyTorch snippets, Alfred conventions|
| Alfred style     | 100–150      | Report format, "sir" register, escalation style |
| **Total**        | **~1100–1750**|                                                |

Start with 500 high-quality verified pairs; fine-tune; eval; add more if gains plateau.

---

## Pipeline (DAG)

```
alfred-leader
    |
    +-- [1] alfred-math / alfred-physics / alfred-coder  (GENERATE)
    |         Produce (instruction, response) pairs per domain.
    |         alfred-coder: run/test all code snippets; show real output.
    |         alfred-math:  show full working; verify by substitution.
    |         alfred-physics: check dimensional consistency and limits.
    |
    +-- [2] alfred-reviewer  (VERIFY — independent pass, read-only)
    |         Reject any pair where:
    |           - The answer contains a mathematical error.
    |           - Code does not run or produces wrong output.
    |           - A claim is unverified or fabricated.
    |           - Style deviates from Alfred conventions.
    |         Returns: PASS / FAIL + reason.
    |
    +-- [3] alfred-evaluator  (SCORE)
    |         Score each PASS pair on: correctness, conciseness, style match.
    |         Threshold: score >= 4/5. Below threshold -> discard.
    |
    +-- [4] alfred-data  (ASSEMBLE)
              Collect scored pairs -> write JSONL (instruction/input/output format).
              Deduplicate. Shuffle. Split 90/10 train/val.
              Output: data/finetune/math-physics-coding-vN.jsonl
```

---

## JSONL record format

```jsonl
{"instruction": "<task description>", "input": "", "output": "<full verified response>"}
```

- `instruction`: the task as the Owner or an agent would phrase it.
- `input`: context or code snippet if applicable; empty string otherwise.
- `output`: the complete, verified response in Alfred style.

---

## Example verified pairs (illustrative)

**Math:**
```json
{
  "instruction": "Derive the gradient of the cross-entropy loss with respect to the pre-activation z for a softmax output layer.",
  "input": "",
  "output": "Let p = softmax(z), y = one-hot label. Cross-entropy L = -sum_i y_i log p_i.\n∂L/∂z_j = p_j - y_j.\nDerivation: ∂L/∂z_j = -sum_i y_i (1/p_i) · ∂p_i/∂z_j.\n∂p_i/∂z_j = p_i(δ_ij - p_j) (standard softmax Jacobian).\nSubstituting and using sum_i y_i = 1: ∂L/∂z_j = p_j - y_j. □\nVerified by substitution with a 3-class example."
}
```

**Physics / PINN:**
```json
{
  "instruction": "Write the PINN loss terms for the 1-D steady-state heat equation d²T/dx² = -q/k on x∈[0,1] with T(0)=T0, T(1)=T1.",
  "input": "",
  "output": "Residual loss (N_r collocation points):\n  L_r = (1/N_r) sum_i (d²T_θ/dx²|x_i + q/k)²\nBoundary loss:\n  L_bc = (T_θ(0) - T0)² + (T_θ(1) - T1)²\nTotal: L = λ_r L_r + λ_bc L_bc  (start λ_r=λ_bc=1).\nNote: d²T_θ/dx² via torch.autograd.grad twice. Verified against analytical solution T(x)=T0+(T1-T0)x - (q/2k)x(1-x)."
}
```

**Coding:**
```json
{
  "instruction": "Implement a numerically stable log-sum-exp in NumPy.",
  "input": "",
  "output": "import numpy as np\n\ndef log_sum_exp(x: np.ndarray) -> float:\n    \"\"\"Numerically stable log(sum(exp(x))). Subtracts max to prevent overflow.\"\"\"\n    c = x.max()\n    return c + np.log(np.sum(np.exp(x - c)))\n\n# Verified:\n# log_sum_exp(np.array([1000., 1001., 1002.])) -> 1002.407606...  (correct)\n# Naive np.log(np.sum(np.exp([1000,1001,1002]))) -> inf  (overflow)"
}
```

---

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/build-finetune-jsonl.ps1` | Orchestrates generation → verify → score → assemble |
| `scripts/run-finetune-eval.ps1` | Evals the fine-tuned model against a held-out set |

---

## Fine-tuning execution (NOT on this machine)

This machine has no dedicated GPU. Fine-tuning runs on **Kaggle (free T4/P100)** or
**Google Colab (free T4)**. The notebook is at `notebooks/local-coder-finetune-colab.ipynb`.

Recommended approach: **QLoRA** (4-bit base, LoRA adapters r=16, α=32) using
`transformers` + `peft` + `trl` (SFTTrainer). Merge adapters after training; reload in
LM Studio.

Hyperparameters (starting point):
- Epochs: 3–5 (watch val loss; stop if it rises).
- Batch size: 4 (gradient accumulation = 4 → effective 16).
- LR: 2e-4 with cosine schedule and 3% warmup.
- Max sequence length: 2048.

---

## Evaluation gate (before deploying the fine-tuned model)

Run `scripts/run-finetune-eval.ps1`. The fine-tuned model must beat the base model on
the held-out val set across all three domains (math, physics, coding) before it replaces
the current local model in LM Studio. If it regresses on any domain, discard the
checkpoint and diagnose (data quality, epoch count, LR) before retrying.

---

## What fine-tuning will and will not improve

| Will improve | Will NOT improve |
|---|---|
| Domain notation and style matching | General reasoning beyond training distribution |
| Alfred report format and "sir" register | Novel proofs or unseen physics regimes |
| Routine NumPy/PyTorch/SciPy idioms | Broad coding outside the training domains |
| PINN loss formulation for trained PDEs | Frontier-level accuracy on hard benchmarks |
| Speed of routine domain responses | Competing with Opus 4.8/4.6 on hard tasks |

**The fine-tuned local model is the offline fallback for routine domain work — nothing more.**