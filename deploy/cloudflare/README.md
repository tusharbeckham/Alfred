# Alfred on Cloudflare — always-on public chat

One self-contained Worker (`worker.js`) serves a polished, Claude-style chat UI **and** runs the model,
with the spam guard built in. It streams replies and renders markdown (code, bold, lists).

## Backend (the Worker auto-detects)
- **Groq — recommended (sharper/faster mind):** add a **Secret** `GROQ_API_KEY` (from `console.groq.com`).
  Default model `llama-3.3-70b-versatile` (override with a `GROQ_MODEL` variable).
- **Cloudflare Workers AI — free, no key:** add a **Workers AI binding** named `AI`. Used automatically
  when no Groq key is set. Default `@cf/meta/llama-4-scout-17b-16e-instruct` (override with `LLM_MODEL`).

Priority: Groq if a key is present, else Workers AI.

## Deploy via the dashboard (no CLI, ~5 min)
1. **dash.cloudflare.com** → **Workers & Pages** → **Create** → **Create Worker** → name `alfred` → **Deploy**.
2. **Edit code** → select-all, delete → paste ALL of `worker.js` → **Deploy**.
3. Wire a backend: add the **`GROQ_API_KEY`** secret (recommended) and/or the **`AI`** Workers AI binding.
4. Open `https://alfred.<your-subdomain>.workers.dev` — your public Alfred.

## UI
- Warm Claude-style interface; header is just "Alfred"; a time-aware greeting (not a pre-filled chat).
- Streaming markdown replies (sanitized), branded favicon, link-preview meta tags.

## Notes
- **Key safety:** the Groq key lives only as a Worker secret — never sent to the browser. Workers AI needs no key.
- **Spam:** rate-limit + flood + auto-ban run inside the Worker; enough for a personal bot.
- **Model deprecated?** If a reply says "5028 ... deprecated", open **AI → Models** (Cloudflare) or Groq's
  model list and set `LLM_MODEL` / `GROQ_MODEL` to a current id.
