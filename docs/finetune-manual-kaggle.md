# Alfred-Coder — Manual Fine-Tune on Kaggle (Qwen2.5-Coder-7B)

Fine-tune Alfred's local coder on your own verified examples — free, on Kaggle's GPU.
Base: **Qwen2.5-Coder-7B-Instruct**. Dataset: **155 verified pairs** (coding + Alfred persona).

## 0. Files (already on your PC)
- `C:\Alfred\data\finetune\train.jsonl` (132 rows) + `val.jsonl` (23 rows).
- Rebuild anytime after adding pairs: `powershell -File scripts\build-finetune-jsonl.ps1`.

## 1. Kaggle setup
1. kaggle.com → **Create → New Notebook**.
2. Right panel → **Session options**: Accelerator = **GPU T4 x2** (or T4); **Internet = On**.
   (One-time: verify your phone under Settings to unlock GPU + internet.)
3. **Add Input → Upload** → drag `train.jsonl` (and `val.jsonl`). Mounts at `/kaggle/input/<name>/`.

## 2. Use Unsloth's maintained Qwen2.5-Coder notebook
- github.com/unslothai/unsloth → the **Qwen2.5-Coder (7B)** notebook → **Kaggle** badge → **Copy & Edit**.
- Its install cell is kept working — that's the exact piece our automation kept breaking.

## 3. Three edits
- **Model:** `unsloth/Qwen2.5-Coder-7B-Instruct`
- **Data cell:**
  ```python
  from datasets import load_dataset
  ds = load_dataset("json", data_files="/kaggle/input/<name>/train.jsonl", split="train")
  ds = ds.map(lambda ex: {"text": tokenizer.apply_chat_template(
      ex["messages"], tokenize=False, add_generation_prompt=False)})
  # pass ds as train_dataset
  ```
- **Training args:** with 132 rows, `max_steps=400` (~3 epochs), `learning_rate=2e-4`,
  `per_device_train_batch_size=2`, `gradient_accumulation_steps=4`.

## 4. Export + bring it home
- `model.save_pretrained_gguf("qwen-alfred", tokenizer, quantization_method="q4_k_m")`
- Download the `.gguf` from the **Output** panel (`/kaggle/working/`).
- Drop it in `C:\Users\tpanc\.lmstudio\models\alfred\qwen-alfred-GGUF\`.
- Tell Alfred — I'll `lms load` it and repoint `local-coder` to your bespoke model.

## 5. If the install errors with "no kernel image"
That's the torch-reinstall trap. Use Unsloth's `--no-deps` install cell (keeps Kaggle's GPU-matched
torch), or the stock stack (`transformers`+`peft`+`trl`+`bitsandbytes`, no torch reinstall). Send me the
log and I'll adjust.

## Notes
- 155 pairs is a solid first real run (coding + Alfred's voice/rules). Grow it over time and re-run.
- This changes model **weights** (real personalization) — different from Alfred's prompt/skill tuning.
