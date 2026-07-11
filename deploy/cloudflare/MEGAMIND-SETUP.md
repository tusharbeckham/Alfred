# Alfred (Cloudflare) — Megamind setup

Everything to turn on Alfred's cloud memory + knowledge. All free tier. `worker.js` already contains
the code; each feature switches on when you add its binding (nothing breaks if a binding is missing).

## How it works (short version)
- **KV memory** — each visitor gets a `alfred_sid` cookie; their conversation is saved in a Cloudflare
  KV store under that key and reloaded on their next visit (`GET /api/history`). Cross-reload memory.
- **RAG knowledge** — you POST facts to `/api/learn`; each is embedded (Cloudflare Workers AI) and stored
  in a **Vectorize** vector index. On every question, Alfred embeds the question, pulls the closest facts,
  and answers grounded in them. That's the "megamind."

## Bindings / secrets (Worker → Settings)
| Name | Type | Purpose | Required for |
|------|------|---------|--------------|
| `AI` | Workers AI binding | embeddings + model fallback | RAG, fallback |
| `GROQ_API_KEY` | Secret | the sharp brain (Groq) | chat (recommended) |
| `MEMORY` | KV namespace binding | conversation memory | KV memory |
| `VEC` | Vectorize binding | knowledge base | RAG |
| `ADMIN_KEY` | Secret | protects `/api/learn` | teaching facts |
| `GROQ_MODEL` | Variable (optional) | override Groq model | — |

## 1. Deploy the code
Worker → **Edit code** → paste all of `worker.js` → **Deploy**.

## 2. Memory (KV) — dashboard, ~2 min
1. **Workers & Pages → KV → Create a namespace** → name `alfred-memory`.
2. Worker → **Settings → Bindings → Add → KV namespace** → variable **`MEMORY`** → pick `alfred-memory` → Deploy.
→ Alfred now remembers each visitor across reloads.

## 3. Knowledge base (RAG / Vectorize) — needs the CLI
```powershell
npx wrangler login
npx wrangler vectorize create alfred-kb --dimensions=768 --metric=cosine
```
- Worker → **Settings → Bindings → Add → Vectorize** → variable **`VEC`** → index `alfred-kb` → Deploy.
- Worker → **Settings → Variables and Secrets → Secret** → `ADMIN_KEY` = a strong password only you know.
- Make sure the **`AI`** binding is present (it does the embeddings).

### Teach Alfred facts (PowerShell)
```powershell
$b = '{"facts":[
  "Tushar built Alfred, a self-improving multi-agent AI system.",
  "Alfred runs on Groq Llama 3.3 70B, with Cloudflare Workers AI as fallback.",
  "Tushar shipped a solar-forecasting ML project and a portfolio site."
]}'
Invoke-RestMethod "https://alfred.tusharentheoria.workers.dev/api/learn" -Method Post `
  -Headers @{"x-admin-key"="YOUR_ADMIN_KEY";"content-type"="application/json"} -Body $b
```
Re-run any time to add more. Ask Alfred about those topics → he recalls them.

## 4. Rate-limit backstop (before going wide)
The in-app guard is per-edge, so add a global rule:
- Cloudflare dashboard → your domain/Worker → **Security → Rate limiting rules** → new rule on the
  Worker route: e.g. **30 requests / minute per IP → Block (429)**. (Free tier includes one rule.)
- You're keyless on Groq's free tier, so worst case is throttling, never a bill.

## Endpoints
- `GET /` — chat UI
- `POST /api/chat` — chat (guarded, RAG-grounded, streamed)
- `GET /api/history` — restore this visitor's conversation
- `POST /api/learn` — add facts (needs `x-admin-key`)

## Troubleshooting
- **Reply says "upstream 5028 … deprecated"** → the model id retired. Set `GROQ_MODEL` (or `LLM_MODEL`
  for Workers AI) to a current one from the provider's model list.
- **Nothing recalled** → check the `VEC` + `AI` bindings exist and you've taught facts via `/api/learn`.
- **See errors** → Worker → **Observability → Logs** (rag_error, kv_put_error, groq_error are logged there).
