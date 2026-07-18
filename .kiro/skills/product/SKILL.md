---
name: product
description: Product management — PRDs, user stories, testable acceptance criteria, prioritization (RICE/MoSCoW), scope/MVP definition, and success metrics. Use when defining what to build and why, framing trade-offs, or shaping scope.
---

# Product

## Golden rules
- Clarify the problem before proposing a solution. A solution without a stated user problem
  is speculation.
- Define what to build; hand implementation to engineers. Product does not write production code.
- Prioritize ruthlessly. Everything cannot be P0. Say what is NOT being built, and why.
- Never overpromise or commit the Owner to timelines, contracts, or deliverables. Draft and advise.

## Problem framing (do this first)
1. Who is affected, and what is their pain?
2. How is it solved today, and why is that inadequate?
3. What outcome would count as success (observable, measurable)?
4. What is the cost of doing nothing? (If low, maybe don't build it.)

## Requirements
- **User stories**: "As a <role>, I want <capability>, so that <outcome>." Value in every story.
- **Acceptance criteria**: binary and testable. Given/When/Then. If you cannot describe how to
  verify it, it is not ready to hand off. No "should feel fast" without a number.
- **PRD** (kept lean): problem, goals + non-goals, users, scope (in/out), requirements,
  success metrics, risks/open questions. Living document, not a novel.

## Prioritization
- **RICE**: Reach x Impact x Confidence / Effort — rank a backlog objectively.
- **MoSCoW**: Must / Should / Could / Won't — scope a release; "Won't (this time)" is a feature.
- **Impact vs Effort**: quick 2x2 for fast triage. Ship high-impact / low-effort first.
- Always state the reasoning behind a ranking; make the trade-off visible.

## Scope & MVP
- MVP = the smallest thing that delivers real value and tests the core hypothesis. Not a
  half-built full product — a complete thin slice.
- Separate the vision from the first increment. Defer explicitly; park ideas, don't smuggle them in.
- Cut anything that does not serve the core user problem in this iteration.

## Success metrics
- Every initiative gets a measurable, time-bound metric (adoption, retention, error reduction,
  time saved, conversion). Define the baseline and the target.
- Prefer outcome metrics (did it help the user?) over output metrics (did we ship it?).
- Beware vanity metrics; pick one primary metric per initiative.

## Definition of done (for a product artifact)
- Problem, users, scope (in/out), and prioritized requirements are explicit.
- Acceptance criteria are testable. Success metrics have baseline + target. Open questions listed.

## Anti-patterns
- Jumping to a solution before the problem is understood. Acceptance criteria you cannot test.
- A backlog where everything is P0. Vanity metrics. Overpromising scope or dates.
