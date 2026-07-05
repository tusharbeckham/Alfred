---
inclusion: always
---

# Alfred — Conventions

## Code
- Match the existing style, language, and libraries of whatever project Alfred is working
  in. Read before writing.
- Prefer clear, small, well-named functions. Comment only non-obvious logic.
- Use secure patterns by default: input validation, parameterized queries, proper error
  handling. Never hardcode secrets.
- Every new feature or bug fix ships with tests. If no test framework exists, set up the
  standard one for the ecosystem.

## Git
- Never push directly to `main`/`master` unless the Owner explicitly asks.
- Work on branches; use worktrees for parallel/multi-repo work.
- Stage specific files, not `git add .`. Flag any file that may contain secrets before
  committing (`.env`, credentials, tokens).
- Only create commits when the Owner asks or the workflow (e.g., CI-gated overnight run)
  authorizes it. Prefer new commits over `--amend`. Never force-push without approval.
- PR titles < 70 chars; description covers summary, tests, and anything blocked.

## Files & Output
- Use dedicated file tools, not shell `cat`/`echo`, for reading/writing.
- Create markdown files only when asked. Keep steering and skills concise.

## Verification
- After a code change, run the project's build/tests before declaring success.
- State what was verified and what was not. Clean up temp files.
