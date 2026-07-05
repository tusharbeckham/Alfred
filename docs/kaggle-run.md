# Run the fine-tune on Kaggle (free GPU)

Training runs on Kaggle's GPU **under your account**. Alfred cannot log into your Kaggle, so
either (A) drop a Kaggle API token and Alfred drives it, or (B) do a 5-step browser run.

> This first run is a **SMOKE TEST** (11 pairs → 10 train rows): it confirms the pipeline works
> end-to-end. Do NOT expect real behavior change until the dataset reaches ~50–100+ pairs.

- Model fine-tuned: **IBM Granite 4.1 8B** (matches your local model).
- Dataset: `data/finetune/train.jsonl`.
- Notebook: `notebooks/alfred-coder-finetune-colab.ipynb`.

## Path A — Alfred drives it (Kaggle API)
1. Get a token: kaggle.com → your avatar → **Settings** → **API** → **Create New Token** (downloads `kaggle.json`).
2. Place it at `C:\Users\tpanc\.kaggle\kaggle.json` (Alfred can't write there — it's your credential).
3. Also enable Kaggle **phone verification** (required for GPU + internet in kernels).
4. Tell Alfred "kaggle token ready" — Alfred will upload `train.jsonl` as a dataset, push the notebook
   as a GPU kernel, run it, and fetch the resulting GGUF.

## Path B — Browser (5 steps; fastest for a one-off smoke test)
1. kaggle.com → **Create → New Notebook** → **File → Import Notebook** → upload
   `notebooks/alfred-coder-finetune-colab.ipynb`.
2. Right panel → **Accelerator → GPU T4**, and turn **Internet ON**.
3. **Add Input → Upload** `data/finetune/train.jsonl`; note where it lands
   (e.g. `/kaggle/input/<name>/train.jsonl`) and set `fname` in the data cell to that path.
4. **Run All** — installs Unsloth, loads Granite 8B (4-bit), trains on the 10 rows, exports a GGUF.
5. **Output** panel → download `alfred-coder-gguf.zip` → hand it to Alfred to load into LM Studio.

## After the smoke test
Keep collecting real examples toward 50–100+ in `data/finetune/*.md`, rebuild with
`scripts/build-finetune-jsonl.ps1`, then re-run — that's when a fine-tune becomes "real".
