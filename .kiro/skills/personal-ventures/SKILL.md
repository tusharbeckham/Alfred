---
name: personal-ventures
description: The Owner's trading-learning kit and business/freelancing toolkit. Use when the Owner asks about learning trading, markets, backtesting, starting a business, business plans, or freelancing/proposals/profiles. Education and drafting only — never financial or legal advice, never execute trades, never operate the Owner's accounts.
---

# Personal Ventures — trading learning + business/freelancing

Alfred helps the Owner learn and build, honestly. Hard boundaries: no financial/legal advice, no
real-money trading, no operating his social/freelance accounts (Alfred drafts; the Owner submits).

## Trading (EDUCATION ONLY)
- Roadmap, risk management, glossary, cited resources: `docs/trading/README.md`.
- Tools (pure stdlib, paper/simulated only): `scripts/trading/`
  - `indicators.py` — SMA, EMA, RSI, daily_returns, max_drawdown.
  - `backtest.py` — SMA-crossover paper backtest over an OHLC CSV.
  - `sample_prices.csv` — sample data. Run: `python scripts/trading/backtest.py`.
- Alfred NEVER executes real-money trades. Every output stays labelled educational.

## Business
- Lean one-page plan template + idea-validation checklist + go-to-market + pricing:
  `docs/business/business-plan.md`.

## Freelancing
- Proposal templates, Upwork/Fiverr/LinkedIn profile checklist, lead/gig tracker, rate guidance:
  `docs/business/freelancing-toolkit.md`.
- Alfred drafts proposals and posts; the Owner reviews and submits (ToS/ban safety).
