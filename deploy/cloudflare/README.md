# Alfred on Cloudflare — FREE, always-on

One self-contained Worker (`worker.js`) serves the chat page **and** runs the model, with the spam
guard built in. It uses **Cloudflare Workers AI** (Llama on Cloudflare — **no external key needed**).
Free tier: plenty for a personal bot, and it doesn't sleep.

## Deploy via the dashboard (no CLI, ~5 min)
1. **dash.cloudflare.com** → **Workers & Pages** → **Create** → **Create Worker** → name it `alfred` → **Deploy**.
2. **Edit code** → select-all, delete → paste ALL of `worker.js` → **Deploy**.
3. **Settings → Bindings → Add → Workers AI** → variable name **`AI`** → save & deploy.
   *(This binding is what lets the Worker run the model for free — it's the only required step.)*
4. *(Optional)* **Settings → Variables** → add `LLM_MODEL` to change the model. Default is
   `@cf/meta/llama-4-scout-17b-16e-instruct` (current, smart, multimodal). If Cloudflare ever deprecates
   it (you'll see a "5028 deprecated" message), open **AI → Models** in the dashboard and set `LLM_MODEL`
   to any current text-generation model id.
5. Open `https://alfred.<your-subdomain>.workers.dev` — that's your public Alfred. Share it.

## Alternative backend: Groq (also free)
Prefer Groq instead? Skip the AI binding; add a **Secret** `GROQ_API_KEY` (from `console.groq.com`) and a
var `LLM_MODEL=llama-3.3-70b-versatile`. The Worker tries the AI binding first, then Groq.

## Notes
- **Cost:** $0 — Workers AI + Workers free tiers, no card. (The Groq path is free too.)
- **Key safety:** with Workers AI there's no external key at all. With Groq, the key lives only as a
  Worker secret and is never sent to the browser.
- **Spam:** rate-limit + flood + auto-ban run inside the Worker (in-memory per isolate) — enough for a
  personal bot. Back it with KV / Durable Objects if you ever need heavy scale.
