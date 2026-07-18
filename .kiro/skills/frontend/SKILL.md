---
name: frontend
description: Frontend engineering - accessible, performant UI with HTML/CSS/JS/TS and component frameworks, design systems, client state, and component testing. Use when building or reviewing user interfaces.
---

# Frontend

## Non-negotiables
Accessibility and performance are requirements, not polish. They ship in the first pass, not a
follow-up. Match the project's existing framework, conventions, and design system - read before
you write; do not introduce a new framework or UI library without approval.

## Accessibility (WCAG 2.1 AA by default)
- **Semantic HTML first** — a `<button>` is a button. Reach for ARIA only to fill real gaps.
- Full **keyboard operability**: logical tab order, visible focus, no traps, manage focus on
  route/modal changes.
- Sufficient **color contrast** (4.5:1 text). Never rely on color alone to convey meaning.
- Label every control; `alt` on meaningful images, empty `alt` on decorative ones.
- Respect `prefers-reduced-motion`. Test with a screen reader and axe/Lighthouse.

## Performance (Core Web Vitals)
- Optimize **LCP** (fast, prioritized hero/content), **INP** (keep the main thread free), **CLS**
  (reserve space for media/ads — no layout shift).
- Ship less JS: code-split, lazy-load below the fold, tree-shake, defer non-critical work.
- Right-size images (modern formats, responsive `srcset`), preconnect/preload critical assets.
- Measure with Lighthouse/WebPageTest; watch bundle size in CI (budgets).

## Structure and state
- Small, focused, composable components. Separate logic from presentation. Prop-drill shallow;
  lift state only as far as needed.
- **Simplest state that fits**: local state before global; **URL state** before a store; a store
  only for genuinely shared, cross-cutting state. Document data flow for complex interactions.
- Prefer composition over inheritance. Keep side effects at the edges.

## Testing
- **Testing-Library patterns**: assert on rendered output and user events, not implementation
  details. Unit-test logic; component-test behavior. Snapshot sparingly.
- Build clean: no type errors, no lint errors, no console warnings. Ship tests with every change.

## Security (client-side)
- Escape/encode output; never `dangerouslySetInnerHTML` with untrusted data (XSS). CSP where
  possible. No secrets or API keys in client code. Auth/CSRF flows → confirm with `alfred-security`.

## Handoffs
- API contracts → `alfred-backend`. Design-system/architecture calls → `alfred-architect`.
- Security review (XSS/CSRF/auth) → `alfred-security`.

## Anti-patterns
- Div-soup with click handlers instead of real controls. Inaccessible custom widgets.
- Giant client bundles; blocking the main thread. Global state for everything.
