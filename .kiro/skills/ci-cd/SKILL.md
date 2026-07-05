---
name: ci-cd
description: Continuous integration and delivery — detecting the stack, running lint/build/test gates, and gating commits. Use for CI runs, build verification, and release readiness.
---

# CI/CD

## The CI gate
A commit only lands if the gate is green. Order of checks (stop at first hard failure):
1. Install/restore dependencies.
2. Lint / format check.
3. Type-check (if the language has one).
4. Build / compile.
5. Tests (unit → integration).

Emit a final line `CI: PASS` or `CI: FAIL` so scripts can grep the verdict. Write a summary
to `memory/ci-results.md`.

## Stack detection
| Marker | Stack | Typical commands |
|--------|-------|------------------|
| package.json | Node | `npm ci`, `npm run lint`, `npm run build`, `npm test` |
| pyproject.toml / requirements.txt | Python | `pip install -e .`, `ruff check`, `pytest` |
| Cargo.toml | Rust | `cargo clippy`, `cargo build`, `cargo test` |
| go.mod | Go | `go vet`, `go build ./...`, `go test ./...` |
| Makefile | any | `make lint`, `make build`, `make test` |

## Delivery (safety-gated)
- Deployment to production is ALWAYS Owner-approved. Never deploy unsupervised.
- Pre-deploy: CI green on the exact commit, correct target, migration + rollback plan.
- Post-deploy: smoke-test key flows; on failure recommend immediate rollback.

## Principles
- Never make the gate pass by disabling checks or deleting tests.
- Keep CI fast and deterministic. Cache dependencies. Fail loudly with actionable output.
- The overnight run commits only behind a green gate.
