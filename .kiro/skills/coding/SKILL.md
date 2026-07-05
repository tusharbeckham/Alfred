---
name: coding
description: Expert software engineering practice — writing, editing, and structuring code across languages. Use when implementing features, editing code, or making implementation decisions.
---

# Coding

## Golden rules
- Read before you write. Match the project's language, style, framework, and structure.
- Smallest change that fully solves the problem. No unrequested scope creep.
- Correctness first, then clarity, then performance. Optimize only with evidence.
- Every feature/bugfix ships with tests. No dead code, no commented-out blocks.

## Before coding
1. Locate the relevant files (grep/glob/code search). Understand the existing pattern.
2. Identify the contract: inputs, outputs, error cases, side effects.
3. Check for existing utilities before adding new dependencies.

## Writing code
- Name things for intent. Keep functions short and single-purpose.
- Validate inputs at boundaries. Handle errors explicitly; fail loudly in dev.
- Never hardcode secrets or environment-specific values. Use config/env.
- Use secure patterns by default: parameterized queries, output encoding, safe
  deserialization, least privilege.
- Add comments only for non-obvious *why*, not *what*.

## Language quick-notes
- **Python**: type hints, `pytest`, `ruff`/`black`, virtualenv, no bare `except`.
- **JS/TS**: strict TS, `const` by default, no `any`, `eslint`+`prettier`, `vitest`/`jest`.
- **Rust**: `Result`/`?`, `clippy`, no `unwrap` in prod, `cargo test`.
- **Go**: `gofmt`, error wrapping, table tests.

## Definition of done
- Compiles/lints/type-checks clean. Tests pass. Behavior verified with real output.
- Diff is minimal and reviewed against project conventions.
- No secrets committed. Temp files cleaned up.

## Anti-patterns
- Guessing an API instead of reading it. Weakening a test to make it pass.
- Broad refactors mixed into a feature. Swallowing errors silently.
