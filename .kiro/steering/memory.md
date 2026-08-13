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

## Session capture (batch) — the practical "capture the conversation" step
At the end of a meaningful session, save several points at once (deliberate, not a silent hook):
```
powershell -NoProfile -File scripts/alfred-capture.ps1 `
  "decision|<topic>|<what + why>|tag1,tag2" `
  "preference|<topic>|<owner trait>|owner"
```
Each item is written to `memory.jsonl` (with an embedding) AND the SQLite megamind (`megamind.db`).

## Recall (remember relevant context) — do this before acting on a task
- **Current truth first (bi-temporal graph):** `python scripts/memgraph.py recall -q "<task>" -k 6`
  Returns a **token-bounded** block of facts true *now*, each citing the episode it came from.
  Superseded facts are excluded by default — this is the path that will not hand you a preference
  the Owner already changed. Add `--include-history` when you need what changed.
- **Offline / local:** `powershell -NoProfile -File scripts/alfred-recall.ps1 -Query "<task or question>" -TopK 4`
  (semantic search; keyword fallback if LM Studio is down). The local coder auto-injects memory with
  `scripts/local-coder.ps1 -Recall "<task>"`.
- **Fast path (SQLite FTS):** `python scripts/megamind.py recall -q "<task>" -k 5` — sub-millisecond
  indexed recall from the local megamind DB (`memory/megamind.db`), kept in sync by `alfred-remember.ps1`.
- **With Kiro:** query the memory knowledge base, or read `memory/*.md`.

## Correct memory when something changes (do NOT just append)
An append-only log recalls contradictions forever. When a fact **changes** — a preference, a chosen
model, a status — assert the new value so the old one is invalidated:
```
python scripts/memgraph.py assert --subject owner --predicate prefers-editor `
  --object "VS Codium" --kind preference --statement "<what + why>"
```
The old value is **kept as history** (`t_invalid` + `superseded_by`), never deleted, so both
questions stay answerable:
```
python scripts/memgraph.py current --subject owner                          # true now
python scripts/memgraph.py history --subject owner --predicate prefers-editor  # what changed
```
Use `--multi` for predicates that legitimately hold several values at once (e.g. `depends_on`).
Every fact must cite a source episode — a fact without provenance is refused by design.
Via the harness (policy-gated, so every surface can use it): `graph-recall`, `graph-current`,
`graph-history`, `graph-assert`, `graph-doctor`. The untrusted local model gets the **read** ones only.

## Learn (consolidate)
`alfred-memory-curator` periodically distills recurring items in `memory.jsonl` into `learnings.md`
and promotes durable lessons into steering/skills — so the system compounds over time.
