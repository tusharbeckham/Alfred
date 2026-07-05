# local-coder — Phase 1 setup (Ollama + qwen2.5-coder)

This is the ONE step that needs your approval: installing Ollama. Everything else in
Alfred for local-coder is already built and inert. Nothing here runs `ollama run` — Alfred
talks to the local server over its REST API.

## Verified state (checked 2026-07-05)
- Ollama: **NOT installed** (not on PATH, not in default dirs, API on :11434 not responding).
- RAM: **23.5 GB total, ~12.6 GB free** at check time. Fine for the 7B model.
- No dedicated GPU → inference runs on CPU. 7B is the comfortable ceiling; 14B is slower.

## Step 1 — Install Ollama (needs your OK; installs software system-wide)
Pick one:
- Download the installer: https://ollama.com/download/windows → run `OllamaSetup.exe`.
- Or via winget (PowerShell): `winget install --id Ollama.Ollama -e`

After install, Ollama runs as a background service and a tray icon; the API comes up at
`http://localhost:11434`. Verify (read-only):
```powershell
(Invoke-RestMethod http://localhost:11434/api/version).version
```

## Step 2 — Pull the model and test it (via REST, not `ollama run`)
```powershell
ollama pull qwen2.5-coder:7b        # ~4.7 GB download, one time
```
Then test through Alfred's client (this uses the REST API and reports speed):
```powershell
powershell -File C:\Alfred\scripts\local-coder.ps1 -ShowStats "Write a PowerShell function that returns the largest of three numbers."
```
The `[local-coder] ... ~N tok/s` line at the end is your speed on this hardware.

### Reading the speed
- **≥ ~8 tok/s** — comfortable for interactive routine tasks. Good to go.
- **~4–8 tok/s** — usable; short tasks feel fine, longer generations take a bit.
- **< ~3 tok/s** — painful for interactive use. Tell me and we reconsider (option C/D).

## Step 3 — Is qwen2.5-coder:14b worth it on 24 GB? (tradeoff only — do NOT auto-install)
| | 7b (Q4) | 14b (Q4) |
|---|---|---|
| Disk | ~4.7 GB | ~9 GB |
| RAM while running | ~5–6 GB | ~9–11 GB (tight vs your ~12.6 GB free — close other apps) |
| Speed on CPU | baseline | **roughly half** — noticeably slower |
| Quality | strong for routine work | better reasoning on trickier single-file tasks |

**Recommendation:** start with 7b for everything interactive. Only pull 14b
(`ollama pull qwen2.5-coder:14b`) if you hit tasks where 7b's quality isn't enough AND you
can tolerate the slower speed. On CPU-only, 14b is a "when I can wait" model, not a daily
driver. Anything bigger (32b) is not worth it without a real GPU.

## Step 4 — Turn routing on (only after Step 2 works)
Edit `.kiro/steering/routing.md` and change `LOCAL_CODER_ROUTING = DISABLED` to `ENABLED`.
Until you do, the whole Alfred team behaves exactly as before.

## Rollback (fully reversible)
- Set routing back to `DISABLED`.
- `ollama rm qwen2.5-coder:7b` to reclaim disk.
- Uninstall Ollama from Windows "Apps" if you want it gone entirely.
- Delete `.kiro/agents/local-coder.json` + `.kiro/brains/local-coder/` to remove the agent.
