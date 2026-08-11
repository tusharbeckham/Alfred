---
inclusion: always
---

# Alfred — Token & Credit Budget

The Owner pays for premium tokens and the context window is what kills sessions. Both are
managed on purpose. Deep detail lives in the `token-economy` skill; these are the always-on rules.

## Route to the cheapest tier that can do the job correctly

1. **Free local model** — boilerplate, regexes, single-file edits, snippets, first drafts.
2. **`claude-sonnet-4.6`** — planning, summarizing, extraction, lightweight dispatch.
3. **`claude-opus-4.6`** — real engineering, debugging, review, data/ML.
4. **`claude-opus-4.8`** — orchestration, architecture, genuinely hard reasoning. Protect this tier.

Escalate on evidence, never on vibes. Downshift the moment the hard part is done. Correctness
always beats credit-saving — but "correct" rarely requires the top tier.

## Context hygiene (always)

- **Search before read.** Locate with `grep`/`glob`, then read only the needed slice.
- **Never re-read** something already in this session's context.
- **Delegate bulk reading** to subagents — their context window is separate and free of yours.
- **Externalize state** into `todo_list` and `memory/`, not into the transcript.
- Checkpoint at ~85% context; stop starting new work at ~95% and hand off with evidence.

## Output discipline

- Cap every command's output (`| Select-Object -First 50`, `--quiet`, exit-code checks).
- Prefer exit codes and counts to full logs.
- Never echo back a file you just wrote. State the path and what changed.
- Ask subagents for bounded, structured answers with citations.

## Hard stops

- No unbounded loops. No third attempt at the same failing approach.
- No re-running a suite that already passed.
- No subagent chain deeper than 3 levels.
- No premium tokens on formatting, renames, comments, or restating context.

## Report the spend

After a long or wide task, one closing line:
`Spend: N subagents, M tool calls, tier mix, ~X% context used.`
