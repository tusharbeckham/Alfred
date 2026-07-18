---
name: quality-assurance
description: Test strategy, test planning, coverage-gap analysis, the test pyramid, risk-based and exploratory testing, and quality gates. Use when deciding WHAT to test and HOW, prioritizing testing by risk, or defining release-readiness criteria.
---

# Quality Assurance

## Golden rules
- QA decides WHAT to test and HOW (strategy, risk, coverage). Writing and running the
  tests is the tester's job — hand execution off, review the evidence.
- Never sign off quality on opinion. A gate passes on evidence: green runs, coverage
  reports, documented exploratory results. "Looks fine" is not a pass.
- Prioritize by risk, not by what is easy to test. Weight effort toward the paths where
  failure hurts most.
- A test plan nobody reads is waste. Keep it short, actionable, and mapped to requirements.

## The test pyramid (where each test belongs)
- **Unit** (most): pure logic, fast, isolated. Milliseconds. The bulk of coverage.
- **Integration**: modules + real collaborators (DB, queue, filesystem). Contracts between units.
- **Contract**: API producer/consumer agreement; catches breaking changes across a boundary.
- **E2E** (fewest): critical user journeys through the whole system. Slow, brittle — reserve
  for the few flows that must never break.
- Anti-pattern: the "ice-cream cone" (mostly E2E, few unit) — slow, flaky, expensive.

## Risk-based prioritization
1. Enumerate what can go wrong (data loss, security, money, user-facing regressions, compliance).
2. Score each: likelihood x impact. Test the high-scorers hardest and first.
3. Cover negative paths and edge cases on critical flows; accept lighter coverage on cosmetic ones.

## Coverage-gap analysis
- Map tests to requirements/acceptance criteria — find requirements with no test.
- Use structural coverage (line/branch) as a floor, not a goal. 100% line coverage can still
  miss logic; a covered line is not a verified behavior.
- Flag redundant, overlapping, and flaky tests — they cost time and erode trust.

## Exploratory testing
- Use time-boxed charters ("explore checkout with expired cards, 45 min") where scripted
  tests are insufficient — new features, vague specs, gut-check on UX.
- Record what was tried, what broke, and what to automate next.

## Quality gates (release-readiness)
- Define measurable exit criteria up front: pass rate, coverage threshold, zero open
  critical/high defects, performance budget met, regression suite green.
- A gate is binary. If a criterion is not met, the gate fails — say so plainly and report why.

## Definition of done (for a QA plan)
- Scope, risks, pyramid levels, coverage targets, and gate criteria are written and testable.
- Gaps in existing coverage are listed with priority. Execution is handed to the tester.
- Sign-off references actual evidence, not impressions.

## Anti-patterns
- Signing off without evidence. Testing the easy paths and skipping the risky ones.
- Chasing a coverage percentage while missing whole requirement classes.
- Owning both strategy and execution and then rubber-stamping your own work.
