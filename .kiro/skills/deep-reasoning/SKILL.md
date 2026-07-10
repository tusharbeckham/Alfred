---
name: deep-reasoning
description: Structured heavy reasoning for hard, ambiguous, or high-stakes problems — decomposition, competing hypotheses, adversarial self-critique, and a pre-mortem before committing. Use when a task is complex, the cost of being wrong is high, or a first answer feels shaky.
---

# Deep Reasoning (ultra-think protocol)

Load this for the hard ones: architecture calls, subtle bugs, security/data-sensitive decisions,
tradeoff-heavy choices, or any time a quick answer would just be a guess. It is deliberate by design —
use normal reasoning for routine work.

## Protocol
1. FRAME — Restate the real goal and constraints in your own words. What does "done and correct"
   look like? What must be true? What is explicitly out of scope?
2. DECOMPOSE — Break the problem into parts you can reason about independently. Name the unknowns.
3. GATHER — Resolve the unknowns that change the answer: read the code, search, check memory. Don't
   theorize on top of guesses.
4. HYPOTHESES — Generate at least two candidate approaches/explanations. For each, note the strongest
   evidence for it and the cheapest test that would falsify it.
5. ADVERSARIAL SELF-CRITIQUE — Argue against your leading answer. Where does it break? What edge case,
   input, or failure mode kills it? What would a sharp reviewer flag?
6. PRE-MORTEM — Assume it shipped and failed. Write the most likely reason, then fix that weakness now.
7. DECIDE — Pick the approach with a one-line justification and the tradeoff you accepted. State your
   confidence and what you did NOT verify.

## Discipline
- Effort scales to stakes — this protocol is for the heavy 10%, not every task.
- Verify before asserting; distinguish "checked" from "assumed" explicitly.
- Two failed attempts = stop and re-frame from step 1 with a different tack (see `escalation.md`),
  never a third blind retry.
- Depth serves correctness, not the appearance of rigor. No fabricated certainty, ever.
