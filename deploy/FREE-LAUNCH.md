# Launch Alfred online — FREE ($0, two ways)

You don't need to pay. Both options cost nothing. The guard protects both from spam.

## Option A — Instant, no sign-ups (your PC hosts it)
Runs the app locally against our Alfred-Coder and prints a **free public link** (Gradio share tunnel,
lasts ~72h). Your PC must stay on and replies are slower (local CPU model), but it's live in a minute.

```powershell
cd C:\Alfred\deploy
pip install -r requirements.txt
powershell -File ..\scripts\lms-ready.ps1        # start Alfred-Coder
$env:SHARE = "1"; python app.py                  # prints a public https://xxxxx.gradio.live link
```
Share the printed link. Press Ctrl+C to stop.

## Option B — Proper free hosting (recommended): Hugging Face Space + Groq free tier
Always-on, fast, and smart (Llama-3.3-70B). Doesn't tie up your PC. ~10 minutes, two free accounts, no card.

1. **Free Groq key** — sign up at `console.groq.com` (free, no card) → *API Keys* → *Create Key* → copy it.
2. **Free Space** — `huggingface.co` → sign up → *New Space* → SDK **Gradio**, hardware **CPU basic (free)**.
3. **Upload** to the Space: `app.py`, `guard.py`, `persona.txt`, `requirements.txt`, and
   `SPACE_README.md` **renamed to `README.md`**.
4. **Space → Settings → Secrets**, add three:
   - `LLM_BASE_URL` = `https://api.groq.com/openai/v1`
   - `LLM_API_KEY`  = *(your Groq key)*
   - `LLM_MODEL`    = `llama-3.3-70b-versatile`  *(confirm it's current on Groq's model list)*
5. The Space builds itself and gives you a **public URL**. Done — people can talk to Alfred, free.

## Cost reality
- **$0.** Groq's free tier is rate-limited (plenty for a demo); the HF free CPU Space hosts the UI.
- The only thing I can't do for you is **create your accounts / key** (they're yours to sign up for).
  Everything else is ready — paste the key as a secret and it's live.
