# Alfred — Hybrid Workflow (Offline + Kiro)

Alfred runs in two modes. The **Alfred overseer** decides which to use per task.

## The two modes
| | WITH Kiro (online) | OFFLINE (no Kiro) |
|---|---|---|
| Brain | Claude Opus 4.8/4.6 (the full team) | Granite 4.1 8B via LM Studio (`:1234`) |
| Best at | orchestration, architecture, hard reasoning, math/physics, multi-file work | quick code, single-file edits, drafts, simple automation |
| Cost | Kiro credits | free |
| Speed | fast | ~3–4 tok/s (short tasks) |

## Routing rule (the overseer applies this)
1. Hard / multi-step / architectural / security → **alfred-manager → alfred-leader → workers** (Opus).
2. Math / physics / scientific-ML → **alfred-math** and/or **alfred-physics** (Opus), together for problems spanning math+physics+code.
3. Routine / low-stakes coding → **local model** (free): `scripts/local-coder.ps1` or `scripts/alfred.ps1`.
4. Owner accounts (email/messaging) → **gated**: read/draft freely, never send/change without the Owner's explicit yes.
5. If Kiro is unavailable → do what the offline model can (coding + simple), and **queue** heavier work for when Kiro returns. Never fake full orchestration offline.

## Commands
Online (full power):
- `kiro-cli chat --agent alfred` — the overseer (Ctrl+Shift+O). Or `alfred-manager` (Ctrl+Shift+A).

Offline (free, no Kiro):
- `powershell -File scripts\alfred.ps1 "task"` — code on the local model.
- `powershell -File scripts\alfred.ps1 -Chat` — interactive local chat.
- `powershell -File scripts\alfred.ps1 -Push "msg"` — deterministic git add+commit+push (no model).
- `powershell -File scripts\alfred-mail.ps1 -Read` — read inbox; add `-To/-Subject/-Body` to draft; `-Send -Confirm` to send.
- After a reboot: `lms server start` then `lms load granite-4.1-8b -y`.

## Getting the offline model better over time (the training loop)
1. While online, **alfred-math** and **alfred-physics** generate VERIFIED (problem → worked-solution) pairs in the Owner's domain.
2. Pairs land in `data/finetune/` and become JSONL via `scripts/build-finetune-jsonl.ps1`.
3. Fine-tune Granite for free on Kaggle/Colab (`notebooks/alfred-coder-finetune-colab.ipynb`); load the result back into LM Studio.
4. Repeat on a cadence; re-seed onto a bigger base when hardware allows. Full design: `docs/local-coder/finetune-design.md`.

## Honest limits
- The offline 8B is a coder + simple-automator + (after fine-tuning) a domain-sharpened assistant. It is **not** a replacement for the Opus team's reasoning or orchestration.
- Fine-tuning personalizes it to the Owner's style/domain; it does not reach frontier-model capability.
- "Free + offline" and "frontier-smart" cannot both be true at once — the hybrid uses each where it wins.
