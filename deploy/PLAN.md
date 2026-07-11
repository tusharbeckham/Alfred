# Deploying Alfred publicly — plan

> Goal: let people talk to Alfred online. Fast (no lag), abuse-proof (no crashing from spam),
> and sharper than they expect — witty and unbothered, never abusive.

## What "public Alfred" actually is (honest architecture)
The full Kiro multi-agent system is a local dev tool — it is **not** publicly deployable. The public
Alfred is a **persona chatbot**: Alfred's system prompt + memory/RAG + safety/abuse guards, running on a
hosted LLM behind a web chat UI. The orchestration team stays local; the public face is one sharp,
well-guarded persona.

```
Visitor ─ Web chat UI (streaming)
             └─ Backend API
                  ├─ guard.py      (rate-limit, input hygiene, flood/abuse block)  ← no-crash layer
                  ├─ persona       (Alfred system prompt + few-shot voice)
                  ├─ memory/RAG    (optional: FAQ + facts so he's grounded)
                  └─ LLM backend   (hosted inference, streaming tokens)
```

## Platform recommendation
- **Fastest MVP:** Hugging Face **Spaces + Gradio `ChatInterface`** — built-in chat UI, streaming,
  shareable link, free tier. Best "people can talk to him today" option.
- **Custom brand / scale:** **Next.js on Vercel** (edge, streaming via SSE) for the UI + a small backend
  (**Render / Railway / Fly.io**) holding the persona + `guard.py` + LLM calls.
- **LLM backend (the brain):** a **hosted low-latency inference** provider (e.g. Groq / Fireworks / Together)
  for open models, or a frontier API. **Not** the local LM Studio box — a single home PC can't do public
  uptime/scale and would expose the machine. This needs an **API key (a credential the Owner must provide)**
  and has per-token cost — flagged, not assumed.

## No lag
- **Stream tokens** to the UI (perceived latency ≈ first token, not full answer).
- Pick a **fast provider** (Groq is notably low-latency) and a right-sized model.
- Keep the system prompt tight; cache static context; host near users (edge).

## No crashing from spam (the abuse layer — `deploy/guard.py`)
- **Rate limit** per IP + per session (token bucket): e.g. 20 msgs/min, burst 5.
- **Input hygiene:** hard length cap (e.g. 2000 chars), strip control chars, reject empties.
- **Flood/dedup:** same message repeated rapidly → throttle that sender, not the server.
- **Concurrency cap + queue:** bound in-flight requests; shed load gracefully, never crash.
- **Timeouts + graceful fallback:** if the LLM is slow/down, return a witty holding line, not a 500.
- **Optional:** Cloudflare Turnstile/CAPTCHA on abuse spikes; log + temp-ban repeat abusers.

## Persona & conduct (witty, not abusive)
- Dry butler wit, supreme confidence, genuinely helpful and **smart** — the brilliance is the flex.
- **Sparring/roasts:** allowed and encouraged when the visitor is clearly playing — clever, tasteful.
- **Under insult:** stays completely unbothered; disarms with a composed one-liner. Wins by being
  untouchable, never by melting down.
- **Hard lines:** no hate, slurs, protected-class attacks, harassment, or content aimed at genuinely
  hurting a person. Punch up, keep it fun, keep it classy. This is a ToS + reputation requirement.

## "Smarter than their expectation"
- A strong, specific system prompt (voice + do/don't + refusal-with-style).
- Grounding: small RAG/FAQ so answers are accurate, not hand-wavy.
- Streaming + fast model so he feels *quick and sharp*.
- Tight, confident answers — lead with the point; a wink on top.

## Phased rollout
1. **MVP:** Gradio Space, persona + `guard.py`, hosted LLM, streaming. Share link privately, test.
2. **Harden:** tune rate limits under load, add moderation + Turnstile, logging/alerts.
3. **Scale/brand:** custom Next.js UI, analytics, memory/FAQ, custom domain.

## Owner decisions needed
- LLM backend + **API key** (which provider / budget).
- Platform (Gradio Space vs custom) and domain.
- Rate-limit numbers + whether to require Turnstile from day one.
