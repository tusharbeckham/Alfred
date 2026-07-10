---
inclusion: always
---

# Alfred — Memory (the "megamind")

Alfred has persistent, self-improving memory that works **with Kiro and fully offline**.

## Store
- `memory/memory.jsonl` — structured episodic memory (type, topic, text, tags + a local embedding).
- `memory/decisions.md`, `memory/learnings.md` — human-readable trail (keep appending, as before).

## Capture (remember) — do this after anything worth keeping
Significant reason, decision, fact, Owner preference, or hard-won learning:
```
powershell -NoProfile -File scripts/alfred-remember.ps1 -Type <decision|learning|fact|preference|outcome> -Topic "<short>" -Text "<what + why>" -Tags a,b
```
Embeds via the local nomic model so it's recallable offline. Never store secrets.

## Recall (remember relevant context) — do this before acting on a task
- **Offline / local:** `powershell -NoProfile -File scripts/alfred-recall.ps1 -Query "<task or question>" -TopK 4`
  (semantic search; keyword fallback if LM Studio is down). The local coder auto-injects memory with
  `scripts/local-coder.ps1 -Recall "<task>"`.
- **Fast path (SQLite FTS):** `python scripts/megamind.py recall -q "<task>" -k 5` — sub-millisecond
  indexed recall from the local megamind DB (`memory/megamind.db`), kept in sync by `alfred-remember.ps1`.
- **With Kiro:** query the memory knowledge base, or read `memory/*.md`.

## Learn (consolidate)
`alfred-memory-curator` periodically distills recurring items in `memory.jsonl` into `learnings.md`
and promotes durable lessons into steering/skills — so the system compounds over time.
