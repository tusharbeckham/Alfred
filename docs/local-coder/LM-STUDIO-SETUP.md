# Alfred-Coder — LM Studio setup (IBM Granite 4.1 8B)

Base model: **IBM Granite 4.1 8B Instruct** (Apache-2.0). Chosen because it fits this machine
comfortably, beats qwen2.5-coder-7B on coding, and fine-tunes on a FREE Colab/Kaggle GPU.

## Why this model (for a 24 GB, CPU-only machine)
- Q4_K_M GGUF ≈ **5.5 GB** → runs with your browser open (you have ~9–10 GB free normally).
- 87.2% HumanEval — strongest sub-10B coder; released Apr 2026 (newer than qwen2.5-coder).
- Apache-2.0 → your fine-tuned derivative is fully yours.
- CPU speed: expect ~3–8 tok/s on the Core Ultra 5 125H (fine for a coding assistant).
- Bigger model (qwen3-coder-30B) is the future upgrade once on Arch Linux / more free RAM.

## Status
- LM Studio: **installed** (winget `ElementLabs.LMStudio`).

## Step 1 — Download the model (in LM Studio)
1. Search: `granite-4.1-8b`.
2. Download **`granite-4.1-8b-instruct`**, quant **Q4_K_M** (~5.5 GB).
   - Use *instruct* for Alfred (task in → code out). The *base* variant is only for raw
     editor autocomplete/FIM.
   - Repo: `lmstudio-community/granite-4.1-8b-GGUF`.

## Step 2 — Load + context
- Load the model. Set **context length = 8192** to start.
- Context vs RAM (important): Granite supports up to 131K tokens, but each token of context
  uses extra "KV cache" RAM. On 24 GB, practical context is ~8K comfortable, ~16–32K if you
  free RAM; full 131K needs far more RAM (Arch Linux / GPU later).
- Optional: enable **KV-cache quantization** (Q8/Q4) to fit more context in the same RAM.
- GPU offload: try a few layers on the integrated Arc GPU (Vulkan) for a small speed bump; set
  back to 0 if unstable.

## Step 3 — Test + speed
- Chat test: "Write a PowerShell function that returns the largest of three numbers."
- Note the tok/s (expect ~3–8). Tell Alfred the number.

## Step 4 — Start the local server (how Alfred uses it)
- Developer / Local Server tab → **Start Server** → `http://localhost:1234/v1` (OpenAI-compatible).
- Check: `(Invoke-RestMethod http://localhost:1234/v1/models).data.id`

## Step 5 — Wire into Alfred (I do this once the server is up)
- Retarget `scripts/local-coder.ps1` from Ollama (:11434) to LM Studio (`:1234/v1/chat/completions`).
- Flip routing to hybrid: local Granite for routine/low-stakes; Kiro/Opus for hard/architectural.

## Fine-tuning (later, FREE)
- Use `notebooks/alfred-coder-finetune-colab.ipynb` on **Kaggle** (free ~30h/week T4) or Colab.
- 8B QLoRA fits a free T4 — no Colab Pro needed. Export GGUF → load back here as your Alfred-Coder.

## Upgrade path ("better in future")
- On Arch Linux (much lower RAM overhead) or with a GPU, re-seed onto qwen3-coder-30B using your
  accumulated fine-tune data. Same pipeline, bigger brain.
