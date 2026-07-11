# Alfred on Cloudflare — FREE, always-on

One self-contained Worker (`worker.js`) serves the chat page and proxies **Groq** with your key kept
server-side, plus the spam guard. Free tier: 100k requests/day, no credit card, doesn't sleep.

## Easiest: the dashboard (no CLI, ~5 min)
1. Get a **free Groq key**: `console.groq.com` -> API Keys -> Create -> copy.
2. Go to **dash.cloudflare.com** -> sign up (free, no card).
3. Left menu -> **Workers & Pages** -> **Create** -> **Create Worker** -> give it a name (e.g. `alfred`) -> **Deploy** (the starter).
4. Click **Edit code**. Select all, delete, then **paste the entire contents of `worker.js`**. Click **Deploy**.
5. Worker -> **Settings** -> **Variables and Secrets**:
   - Add **Secret** `GROQ_API_KEY` = your Groq key. (Secret = encrypted, never shown again.)
   - (Optional) Add **Variable** `LLM_MODEL` = `llama-3.3-70b-versatile` (confirm it's current on Groq).
   - **Save and deploy.**
6. Open your Worker URL: `https://alfred.<your-subdomain>.workers.dev` — that's your public Alfred. Share it.

## Alternative: CLI (if you have Node)
```
cd deploy/cloudflare
npx wrangler deploy
npx wrangler secret put GROQ_API_KEY   # paste the key when prompted
```

## Notes
- **Key safety:** the Groq key lives only as a Worker secret, server-side. It is never sent to the browser.
- **Spam:** rate-limit + flood + auto-ban run inside the Worker (in-memory per isolate) — enough for a
  personal bot. For heavy scale, back it with Cloudflare KV/Durable Objects later.
- **Cost:** $0 on Cloudflare's free tier + Groq's free tier. Set a Groq usage cap if you want a hard stop.
