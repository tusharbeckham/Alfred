---
name: token-economy
description: Managing the Owner's tokens, credits, and context window — model tiering, context hygiene, delegation cost math, and the hard stops that prevent context-overflow crashes and runaway spend. Use before any multi-stage or long-running task, and whenever a session starts feeling large.
---

# Token Economy

Credits are a finite resource the Owner pays for. Context is a finite resource that **crashes
the session when exhausted**. Both are managed deliberately, not hoped about.

## The cost model (what actually spends)

| Spend source | Relative cost | How to control it |
|---|---|---|
| Opus 4.8 tokens | highest | tier down; delegate; shorten context |
| Re-sending a huge context every turn | silent killer | context hygiene (below) |
| Reading whole files when a grep would do | high | search first, read the slice |
| A subagent that re-reads what you already read | duplicated | put the facts in the brief |
| Retry loops | multiplies everything | bounded retries, anti-thrash rule |
| Long tool outputs pasted back in full | high | cap output, summarize, keep the path |
| Local model (LM Studio / Ultron local) | **$0** | route routine work here |

## Model tiering — pick the cheapest capable tier

1. **Free local** (`local-coder`, Qwen2.5-Coder-7B / Ultron local providers) — boilerplate,
   regexes, single-file edits, quick snippets, format conversions, first-draft docs.
2. **`claude-sonnet-4.6`** — planning, prompt writing, lightweight dispatch, summarization,
   structured extraction, most single-purpose worker tasks.
3. **`claude-opus-4.6`** — real engineering: multi-file implementation, debugging, security
   review, data/ML work.
4. **`claude-opus-4.8` (ultrathink)** — reserved for orchestration, architecture, and
   genuinely hard reasoning. This is the tier to *protect*, not the tier to default to.

Rule: **escalate on evidence, not on vibes.** Downshift as soon as the hard part is done —
the write-up of a hard result does not need the tier that produced the result.

## Context hygiene (the anti-crash discipline)

The session dies when the window fills. Treat the window as a budget with four rules:

1. **Search before read.** `grep`/`glob` to locate, then read only the relevant range with
   `offset`/`limit`. Never read a >2000-line file whole "for context".
2. **Never re-read what is already in context.** If you read it this session, it is still there.
3. **Summarize and drop.** Once a large output has served its purpose, restate the 3 facts you
   need and stop referring to the raw blob.
4. **Externalize state.** Long-running work lives in `todo_list` and in `memory/`, not in the
   transcript. After a compaction you re-read the todo list, not the history.

Budget checkpoints — act at these thresholds, don't wait for the wall:

| Context used | Action |
|---|---|
| ~50% | Stop exploratory reading. Write the plan/state to `todo_list`. |
| ~70% | Delegate all remaining bulk reading/writing to subagents (their context is separate). |
| ~85% | Checkpoint: append state to `memory/` + todo list, finish the current stage only. |
| ~95% | Report a partial result with evidence and hand off. Do not start new work. |

**Subagents are the primary context-saving instrument.** A subagent burns *its own* window and
returns a small summary. Fanning out 4 file audits to 4 subagents costs a fraction of the
orchestrator's context that reading 4 files inline would.

## Output discipline (cheap in, cheap out)

- Cap shell output: `| Select-Object -First 50`, `--quiet`, `-q`, `2>&1 | tail`.
- Prefer exit codes and counts over full logs. `; echo "EXIT=$LASTEXITCODE"` beats a 500-line dump.
- Ask subagents to return **structured, bounded** answers ("≤10 bullets, cite file:line").
- Don't echo a file back after writing it. State the path and the change.
- Don't pretty-print JSON you are only checking for validity.

## Delegation cost math

Delegate when: `cost(brief) + cost(summary) < cost(doing it inline)`.

- Reading and reasoning over >300 lines → delegate.
- Any task with >3 independent sub-parts → delegate in parallel.
- A 2-line fix you already have the context for → do it yourself; a subagent spawn has fixed overhead.
- Repeated identical work across N targets → one subagent per target, always.

## Hard stops (never negotiable)

- Never run an unbounded loop. Every retry loop has a max attempt count.
- Never retry the same failing approach a third time (`escalation.md` anti-loop rule).
- Never re-run a passing test suite "to be sure".
- Never spawn a subagent chain deeper than 3 levels — flatten it.
- Never spend Opus tokens on: boilerplate, formatting, renames, comment writing, or
  restating something already in context.

## Reporting spend

When a task ran long or fanned out wide, close the report with one line:
`Spend: N subagents, M tool calls, tier mix (opus/sonnet/local), ~X% context used.`
That is how the Owner learns where his credits go — and it is cheap to produce.
