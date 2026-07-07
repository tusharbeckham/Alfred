---
inclusion: always
---

# Alfred — Task Routing (local-coder vs Opus team)

> **STATUS: ENABLED.** The local model (Alfred-Coder = fine-tuned Qwen2.5-Coder-7B, via LM Studio) is the free local coder.
> Routine, low-stakes coding goes to `local-coder` (free); complex/architectural work stays with the
> Opus agents. Set this back to DISABLED to send everything to the Opus agents as before.

**LOCAL_CODER_ROUTING = ENABLED**

## Why this exists
`local-coder` runs the free local model (Qwen2.5-Coder-7B via LM Studio at http://localhost:1234) so we
stop spending Opus credits on trivial tasks. Opus 4.8 / 4.6 stay reserved for work that needs them.

## The rule (alfred-manager / alfred-leader)
Triage each coding task BEFORE delegating.

**Send to `local-coder` (local, free) when ALL hold:**
- Single file or a small, self-contained script/snippet.
- Well-specified; little ambiguity.
- Low stakes: not security/auth, not infra/prod, not data-destructive, not a public API contract.
- Shallow: little cross-file or architectural reasoning.
- Examples: boilerplate, a regex, a helper function, a single-file fix, a quick lookup, a small
  PC-Ops snippet, a unit test for one function.
- Speed note: Qwen2.5-Coder-7B runs on CPU here — great for short outputs; for large generations prefer Opus.

**Send to the Opus agents when ANY hold:**
- Multi-file, cross-cutting, or architectural; design decisions/tradeoffs.
- Security-, auth-, infra-, prod-, or data-sensitive work.
- Ambiguous requirements that materially change the outcome.
- The local model produced a visibly wrong or unsafe result twice (escalate, don't loop).

**Default when unsure:** prefer the Opus agents. Correctness beats credit-savings.

## Escalation (never a silent downgrade)
- `local-coder` escalates back to the orchestrator with a one-line reason; it must NOT fall back to a
  premium model to "just do it," and the Opus agents must NOT be bypassed for anything meeting a trigger above.
- If LM Studio is unreachable, `scripts/local-coder.ps1` exits non-zero; treat that task as if routing were
  DISABLED (send it to the Opus agents) and note the local model is down.

## How to invoke the local path
- Preferred (zero Kiro credits): `powershell -File scripts/local-coder.ps1 "<task>"`.
- Requires LM Studio running with the model loaded: `lms server start` + `lms load alfred-coder-7b -y`.
- Or delegate to the `local-coder` agent, which wraps that same script.
