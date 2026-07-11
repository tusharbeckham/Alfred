# Deploying Alfred publicly

`app.py` is a Gradio chat app = **abuse guard** + **Alfred's persona** + **any OpenAI-compatible LLM**,
with token streaming and graceful fallbacks. See `PLAN.md` for the full architecture.

## Run locally (free — uses our Alfred-Coder in LM Studio)
```powershell
pip install -r requirements.txt
powershell -File ..\scripts\lms-ready.ps1      # ensure LM Studio + alfred-coder-7b are up
python app.py                                   # -> http://localhost:7860
```
Defaults to `http://localhost:1234/v1`. Perfect for testing the wiring, persona, and guard — **not**
for public (a home PC + code model won't do public scale/latency).

## Go public (powerful, low-latency, scalable)
Point the app at a fast **hosted** model instead — one flip, no code change. Copy `.env.example` → `.env`
(or set host secrets) and choose a preset. **Groq** is recommended for lag-free streaming.
Set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.

### Hugging Face Space (easiest public host)
1. Create a new **Gradio** Space.
2. Upload `app.py`, `guard.py`, `persona.txt`, `requirements.txt`.
3. Space **Settings → Secrets**: set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.
4. It builds and serves a public chat URL — streaming + request queue already on.

## Safety / no-crash
- `guard.py`: rate-limit + input hygiene + flood/dedup + concurrency shedding → spam can't crash it.
- `persona.txt`: witty and unbothered, never abusive → ToS-safe and reputation-safe.
- Optional `deploy/blocklist.txt` (gitignored) adds a secondary output net; in prod also use the
  provider's moderation endpoint.
- **Never commit API keys.** Use `.env` (gitignored) or host secrets only.

## Go-live checklist (once you have a provider key)
1. Pick provider + model; set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (see `.env.example`).
2. `pip install -r requirements.txt`
3. Pre-flight (all offline, no key needed): `python guard.py` · `python stress_test.py` · `python redteam.py`.
4. Deploy: new Gradio HF Space → upload `app.py`, `guard.py`, `persona.txt`, `requirements.txt` → set the 3 secrets.
5. Live check: run the red-team attacks against the deployed model, confirm it holds, then tune rate limits under real load.
