# Alfred-Coder — LM Studio setup (Qwen3-Coder-30B-A3B)

The one gated step: installing LM Studio. Do this, then load the model. Alfred talks to LM
Studio's OpenAI-compatible server over HTTP — no chat window needed for automation.

## Verified target
- Model: **Qwen3-Coder-30B-A3B-Instruct** (Apache-2.0). MoE: 30B total, ~3.3B active/token →
  ~10 tok/s on CPU-only. Q4_K_M GGUF ≈ 18.7 GB (fits your 24 GB; leaves ~4–8K context).
- Your hardware: CPU-only, 24 GB RAM. No GPU needed to *run* it (MoE makes it feasible).

## Step 1 — Install LM Studio (needs your OK; system-wide install)
- Download: https://lmstudio.ai  → run the installer, OR
- I can install it for you: `winget search lmstudio` then `winget install <id>` (tell me to proceed).

## Step 2 — Download the model (inside LM Studio)
1. Open LM Studio → the search/discover tab.
2. Search: `Qwen3-Coder-30B-A3B`.
3. Pick a GGUF quant:
   - `Q4_K_M` (~18.7 GB) — best quality that fits; ~4–8K context headroom.
   - `Q3_K_M` (~16 GB) — a bit lower quality but leaves more room for context.
   - `unsloth` "UD-Q4_K_XL" dynamic quant is a good pick if listed.
4. Download (needs ~19 GB free disk — you have 1 TB, fine).

## Step 3 — Load + tune settings
- Load the model. Set **context length** to 4096–8192 (higher will exhaust RAM).
- Leave GPU offload at 0 (CPU) — or try a few layers on your integrated GPU (Vulkan) for a
  small speed bump; if it gets unstable, set it back to 0.
- Enable **mmap** if offered (lets the OS page the model efficiently).

## Step 4 — Test + check speed
- In LM Studio chat: "Write a PowerShell function that returns the largest of three numbers."
- Watch the tok/s readout. Expect ~8–12 tok/s. If it's much lower, drop to Q3_K_M or reduce context.

## Step 5 — Turn on the local server (this is how Alfred uses it)
- LM Studio → **Developer / Local Server** tab → **Start Server**.
- Default endpoint: `http://localhost:1234/v1` (OpenAI-compatible).
- Quick check (PowerShell): `(Invoke-RestMethod http://localhost:1234/v1/models).data.id`

## Step 6 — Wire into Alfred (I'll do this once the server is up)
- `scripts/local-coder.ps1` currently targets Ollama (`:11434`). I'll retarget it to LM Studio's
  OpenAI endpoint (`:1234/v1/chat/completions`) and flip routing to hybrid: local Alfred-Coder for
  routine/low-stakes work, Kiro/Opus for the hard/architectural work.

## Notes
- This replaces the earlier Ollama plan. Same idea (local REST API), better host for you (GUI +
  optional iGPU offload).
- Fine-tuning happens later on Colab (see `notebooks/alfred-coder-finetune-colab.ipynb`), never on this CPU.
