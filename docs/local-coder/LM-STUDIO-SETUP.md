# Alfred-Coder — LM Studio setup (Qwen2.5-Coder-7B)

Base model: **Qwen2.5-Coder-7B-Instruct** (Apache-2.0). Chosen because it is a top-tier open
coding model AND it fine-tunes cleanly on a FREE Kaggle/Colab T4 — the whole point of the
hybrid tier (run it locally, improve it for free).

## Why this model (for a 24 GB, CPU-only machine)
- Q4_K_M GGUF ≈ **4.7 GB** → runs with your browser open (you have ~9–10 GB free normally).
- Strong, well-supported coder; huge ecosystem support for QLoRA fine-tuning (Kaggle/Colab).
- Apache-2.0 → your fine-tuned derivative is fully yours.
- CPU speed: expect a few tok/s on the Core Ultra 5 125H (fine for a coding assistant).
- Upgrade path: Qwen2.5-Coder-14B/32B once on more free RAM or a GPU — same pipeline, bigger brain.

> Note: Granite 4.1 8B was the earlier pick. We moved to Qwen2.5-Coder-7B because Granite's
> architecture does not fine-tune on a free Kaggle T4, and free fine-tuning is a core goal.

## Download (scripted)
```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" get qwen2.5-coder-7b-instruct --gguf -y
```
Or in the LM Studio GUI: search `qwen2.5-coder-7b-instruct`, download the **Q4_K_M** GGUF.

## Load + context
```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" load qwen2.5-coder-7b-instruct -y
```
- Set **context length = 8192** to start. Larger context uses more KV-cache RAM; on 24 GB,
  ~8K is comfortable, ~16–32K if you free RAM.
- Optional: enable **KV-cache quantization** (Q8/Q4) to fit more context in the same RAM.
- GPU offload: try a few layers on the integrated Arc GPU (Vulkan) for a small speed bump.

## Start the local server (how Alfred uses it)
```powershell
& "$env:USERPROFILE\.lmstudio\bin\lms.exe" server start
(Invoke-RestMethod http://localhost:1234/v1/models).data.id
```
Serves an OpenAI-compatible API at `http://localhost:1234/v1`.

## Wire into Alfred
- `scripts/local-coder.ps1` targets LM Studio (`:1234/v1/chat/completions`), default model
  `qwen2.5-coder-7b-instruct`.
- Routing is hybrid (`.kiro/steering/routing.md`): local Qwen for routine/low-stakes; Kiro/Opus
  for hard/architectural.

## Fine-tuning (FREE)
- `notebooks/alfred-coder-finetune-colab.ipynb` (or the Kaggle kernel in `kaggle/kernel/`) runs
  QLoRA on a free T4 using Kaggle's stock, GPU-matched stack (no Unsloth/torch reinstall — that
  broke CUDA on Kaggle). Export GGUF → load back here as your bespoke Alfred-Coder.
