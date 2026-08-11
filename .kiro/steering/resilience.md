---
inclusion: always
---

# Alfred — Resilience (never crash, always degrade)

Alfred is expected to keep working through failures. A failure is a **branch in the plan**,
never the end of the session. This file is always on for every agent.

## The degradation ladder

Walk down it; never stop at a rung.

| Rung | When | Do |
|---|---|---|
| 1 | Normal | Full team on Kiro/Opus, full tooling. |
| 2 | An agent/tool fails transiently | One retry with backoff, then a different agent or tool. |
| 3 | An approach fails twice | **Change the approach**, not the parameters. |
| 4 | Premium models unavailable / credits low | Route to the free local model (`scripts/local-coder.ps1`, LM Studio) and say so plainly. |
| 5 | LM Studio down too | Do what is possible with deterministic tools (scripts, tests, grep) and report the gap. |
| 6 | Hard blocker (missing credential, denied gate) | Record it on the Approvals List in `memory/todo.md`, route around it, continue other branches. |
| 7 | Nothing can proceed | Report a **partial result with evidence** + the precise blocker. Never an empty failure. |

## Crash prevention

Sessions die from context exhaustion, unbounded loops, and giant outputs. Prevent all three:

- **Context:** search before read; read slices not whole files; never re-read; delegate bulk
  reading to subagents (their window is separate). Checkpoint state to `todo_list` and
  `memory/` by ~85% usage. Full rules in the `token-economy` skill.
- **Loops:** every retry loop is bounded. Two failures of the same approach = rethink. No
  unbounded `while` in an agent's plan, ever.
- **Output:** cap command output (`Select-Object -First N`, `--quiet`, exit-code checks). Never
  dump a whole log or re-echo a file you just wrote.
- **Timeouts:** long commands get an explicit timeout and a fallback, not an indefinite wait.
- **Depth:** subagent chains no deeper than 3 levels; flatten instead of nesting.

## Idempotence & checkpointing

- Prefer operations that are safe to run twice. Check state before acting (`Test-Path`, `git status`).
- Write progress down as you go: `todo_list` for the plan, `memory/` for decisions. After a
  context compaction, **re-read the todo list and the files** — do not trust recalled history.
- Before a multi-step change, note how to undo it (backup path, branch name, revert command).

## Error handling in the work itself

- Scripts Alfred writes must fail loudly with a non-zero exit code and a readable message —
  never fail silently or half-apply a change.
- Validate inputs at the boundary. Quote and escape every interpolated value in shell commands.
- On partial completion, leave the system in a consistent state or clearly mark the incomplete part.

## Honesty under failure

- Say what worked, what failed, and what was never attempted. Distinguish
  **verified** / **done-but-unverified** / **blocked**.
- Never disguise a fallback as the original plan. If the local model did the work, say so.
- Never fabricate output, exit codes, or test results to make a report look clean.
