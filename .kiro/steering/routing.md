---
inclusion: always
---

# Alfred — Task Routing (local-coder vs Opus team)

> **STATUS: DISABLED.** Change the line below to `ENABLED` only after the Owner confirms
> Ollama is installed and `qwen2.5-coder` responds (see `docs/local-coder/SETUP.md`).
> While DISABLED, routing is unchanged — all coding goes to the Opus agents exactly as before.
> This file is additive: it does not modify any existing agent, hook, or MCP config.

**LOCAL_CODER_ROUTING = DISABLED**

## Why this exists
`local-coder` runs the free local model (qwen2.5-coder via Ollama) to handle routine work so we
stop spending Opus credits on trivial tasks. Opus 4.8 / 4.6 agents stay reserved for work that
actually needs them. Goal: cheap where safe, premium where it matters.

## The rule (applies to alfred-manager / alfred-leader when ENABLED)
When you receive a coding task, triage it BEFORE delegating:

**Send to `local-coder` (local, free) when ALL of these hold:**
- Single file or a small, self-contained script / snippet.
- Well-specified: little ambiguity about the desired outcome.
- Low stakes: not security/auth, not infra/prod, not data-destructive, not a public API contract.
- Shallow: little or no cross-file reasoning or architectural judgement needed.
- Examples: boilerplate, a regex, a helper function, a single-file bug fix, a quick lookup,
  a small PC-Ops helper snippet, a unit test for one function.

**Send to the Opus agents (alfred-coder / -architect / -security / -reviewer, etc.) when ANY hold:**
- Multi-file, cross-cutting, or architectural change; design decisions or tradeoffs.
- Security-, auth-, infra-, prod-, or data-sensitive work.
- Ambiguous requirements that materially change the outcome.
- The local model produced a visibly wrong or unsafe result **twice** (escalate, don't loop).

**Default when unsure:** prefer the Opus agents. Correctness beats credit-savings.

## Escalation path (never a silent downgrade)
- `local-coder` escalates back to the orchestrator with a one-line reason; it must NOT fall back
  to a premium model to "just do it," and the Opus agents must NOT be bypassed for anything that
  meets an escalation trigger above.
- If Ollama is unreachable, `scripts/local-coder.ps1` exits non-zero; treat that task as if
  routing were DISABLED (send it to the Opus agents) and note the local model is down.

## How to invoke the local path
- Preferred (zero Kiro credits): `powershell -File scripts/local-coder.ps1 "<task>"`.
- Or delegate to the `local-coder` agent, which wraps that same script.
