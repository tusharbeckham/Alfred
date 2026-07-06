# 📈 Trading Learning Kit

> **⚠️ EDUCATION ONLY — NOT FINANCIAL ADVICE**
>
> This document is a structured self-study resource. It does NOT constitute financial,
> investment, or trading advice. Alfred NEVER executes real-money trades, recommends
> specific securities, or manages funds. Past performance of any strategy does not
> guarantee future results. **74–89% of retail CFD accounts lose money** (ESMA data).
> Consult a licensed financial advisor before risking real capital.

---

## Learning Roadmap

### Phase 1: Markets & Instruments

Understand WHAT you can trade before learning HOW.

| Market | Instruments | Key traits |
|--------|------------|------------|
| **Equities** | Stocks, ETFs | Ownership in companies; dividends; regulated exchanges |
| **Fixed Income** | Bonds, T-bills | Debt instruments; yield; interest-rate sensitivity |
| **Forex (FX)** | Currency pairs (EUR/USD) | 24h market; high liquidity; leverage common |
| **Futures** | Contracts on commodities, indices | Expiry dates; margin; standardized on exchanges |
| **Options** | Calls, Puts | Right (not obligation) to buy/sell; time decay; Greeks |
| **Crypto** | BTC, ETH, tokens | 24/7; volatile; varying regulation |
| **Commodities** | Gold, oil, agriculture | Physical or derivative; macro-driven |

**Study goals:**
- [ ] Explain the difference between spot and derivative markets
- [ ] Describe what "going long" vs "going short" means
- [ ] Understand leverage and margin (and why they amplify losses too)
- [ ] Know which markets have circuit breakers / trading hours

**Sources:**
- [Investopedia — Financial Markets](https://www.investopedia.com/terms/f/financial-market.asp)
- [AlgoStorm — Financial Markets: Stocks, Futures & Forex](https://algostorm.com/trading-markets-types/)
- [CFA Institute — Derivative Markets and Instruments](https://www.cfainstitute.org/en/membership/professional-development/refresher-readings/derivative-markets-instruments)

---

### Phase 2: Order Types

How instructions reach the market — and why the wrong order type can cost you.

| Order type | What it does | When to use |
|-----------|-------------|-------------|
| **Market** | Buy/sell immediately at best available price | Need instant execution; liquid markets |
| **Limit** | Buy/sell only at your price or better | Want price control; willing to wait |
| **Stop (stop-loss)** | Triggers a market order when price hits your stop | Protect against further losses |
| **Stop-limit** | Triggers a limit order at your stop price | Control slippage; risk non-execution in gaps |
| **Trailing stop** | Stop moves with price; locks in gains | Ride trends while protecting profit |
| **GTC / Day / IOC / FOK** | Time-in-force modifiers | Control how long the order lives |

**Study goals:**
- [ ] Place each order type in a paper-trading simulator
- [ ] Understand slippage and why market orders cost more in illiquid instruments
- [ ] Know what happens to stop orders during a gap (weekend, earnings)

**Sources:**
- [Investopedia — Stock Order Types Explained](https://www.investopedia.com/investing/basics-trading-stock-know-your-orders/)
- [Investopedia — Stop-Loss vs. Stop-Limit Order](https://www.investopedia.com/articles/active-trading/091813/which-order-use-stoploss-or-stoplimit-orders.asp)
- [Investopedia — Market Order](https://www.investopedia.com/terms/m/marketorder.asp)

---

### Phase 3: Risk Management & Position Sizing ⚡ (THE MOST IMPORTANT PHASE)

> "Risk management is the ONLY thing that separates survivors from blow-ups."

**Core principles:**
1. **Risk per trade:** Never risk more than 1–2% of total account on a single trade.
2. **Position size formula:**
   ```
   Position Size = (Account Risk $) / (Entry Price − Stop-Loss Price)
   
   Example: $10,000 account, 1% risk = $100 risk budget
   Entry $50, Stop $48 → risk per share = $2
   Position size = $100 / $2 = 50 shares
   ```
3. **Correlated exposure:** Cap total exposure to correlated positions at 4–6% of account.
4. **Quarter-Kelly ceiling:** If using Kelly Criterion, use ¼ Kelly as maximum — full Kelly risks ruin.
5. **Risk:Reward ratio:** Only take trades where potential reward ≥ 2× the risk (2:1 R:R minimum).
6. **Max drawdown budget:** Define a daily/weekly loss limit (e.g., 3% daily, 6% weekly). Stop trading when hit.

**Study goals:**
- [ ] Calculate position size for 5 hypothetical trades
- [ ] Explain why averaging down is dangerous without a pre-planned scale-in
- [ ] Define YOUR maximum drawdown tolerance before you ever trade
- [ ] Understand correlation risk (e.g., holding 5 tech stocks ≠ diversified)

**Sources:**
- [Investopedia — How to Reduce Risk with Optimal Position Size](https://www.investopedia.com/articles/trading/09/determine-position-size.asp/)
- [Britannica Money — Position Sizing in Trading](https://www.britannica.com/money/calculating-position-size)
- [CIBC — Trade Risk Management: Position Sizing](https://www.investorsedge.cibc.com/en/learn/investing/portfolio-strategies/position-sizing.html)
- [TradeAlgo — Position Size Calculator](https://www.tradealgo.com/trading-guides/options/position-size-calculator-trading)

---

### Phase 4: Technical vs Fundamental Analysis

Two lenses for the same market. Most practitioners use BOTH.

| Aspect | Technical Analysis | Fundamental Analysis |
|--------|-------------------|---------------------|
| **Question** | "When to buy/sell?" | "What to buy/sell and why?" |
| **Data** | Price, volume, charts | Financials, earnings, macro |
| **Timeframe** | Short to medium-term | Medium to long-term |
| **Tools** | Indicators (MA, RSI, MACD), patterns, S/R | P/E, DCF, revenue growth, moats |
| **Assumption** | Price discounts everything | Market can misprice in the short term |

**Technical basics to learn:**
- Support & resistance levels
- Moving averages (SMA, EMA) and crossovers
- RSI (overbought/oversold), MACD
- Volume confirmation
- Candlestick patterns (doji, engulfing, hammer)
- Trendlines and channels

**Fundamental basics to learn:**
- Reading income statements, balance sheets, cash flow
- Valuation metrics: P/E, P/B, EV/EBITDA, FCF yield
- Earnings quality vs accounting tricks
- Macro factors: interest rates, GDP, inflation
- Sector/industry analysis

**Practical approach:** Use fundamentals to select *what* to trade; use technicals to decide *when* to enter and exit.

**Sources:**
- [Investopedia — Fundamental vs. Technical Analysis](https://www.investopedia.com/ask/answers/difference-between-fundamental-and-technical-analysis/)
- [Schwab — How to Pick Stocks: Fundamentals vs. Technicals](https://www.schwab.com/learn/story/trading-up-close-technical-vs-fundamental-analysis/)
- [WealthSimple — Technical vs Fundamental Analysis](https://www.wealthsimple.com/en-ca/learn/technical-vs-fundamental-analysis)

---

### Phase 5: Backtesting

Test your ideas on HISTORICAL data before risking a single dollar.

**What backtesting is:**
- Simulate a strategy's trades on past data → measure performance metrics
- Gives insight into drawdowns, win rate, Sharpe ratio, max loss
- Does NOT guarantee future results (markets change, regimes shift)

**Critical pitfalls:**
- **Overfitting:** Curve-fitting to historical noise → breaks in live trading
- **Survivorship bias:** Only testing on stocks that survived (ignoring delistings)
- **Look-ahead bias:** Accidentally using future data in signals
- **Transaction costs:** Ignoring slippage, commissions, and spread

**Python tools (stdlib-friendly starting points):**
- `backtesting.py` — lightweight, pandas-based, good for learning
- `backtrader` — event-driven, more features
- `vectorbt` — vectorized, fast for parameter sweeps
- Pure stdlib: build your own loop with `csv` + basic math (great exercise)

**Study goals:**
- [ ] Backtest a simple moving-average crossover on free historical data
- [ ] Calculate: total return, max drawdown, Sharpe ratio, win rate
- [ ] Deliberately overfit a strategy, then see it fail on out-of-sample data
- [ ] Understand walk-forward analysis and out-of-sample testing

**Sources:**
- [PapersWithBacktest — Backtesting with Python](https://paperswithbacktest.com/wiki/backtesting-with-python)
- [Interactive Brokers — Backtesting.py Introductory Guide](https://www.interactivebrokers.com/campus/ibkr-quant-news/backtesting-py-an-introductory-guide-to-backtesting-with-python/)
- [PyQuant News — Building and Backtesting Trading Strategies](https://www.pyquantnews.com/free-python-resources/building-and-backtesting-trading-strategies-with-python)

---

### Phase 6: Trading Psychology

> "The market did not destroy these accounts. Psychology did." — ESMA data analysis

**The four emotions that kill accounts:**
1. **Fear** → premature exits, missing valid setups
2. **Greed** → overleveraging, holding losers hoping for recovery
3. **Revenge** → revenge-trading after a loss (doubling down to "get it back")
4. **FOMO** → chasing moves that already happened

**Discipline framework:**
- Trade a WRITTEN plan. No plan = no trade.
- Journal every trade: entry reason, exit reason, emotional state, lesson
- Weekly review: did you follow your rules? (process > outcome)
- Accept that losses are a cost of business — like rent for a shop
- Define "tilt" rules: if you're emotional, step away (literal rule, not suggestion)

**Key concepts:**
- **Loss aversion** (Kahneman): losses feel 2× stronger than equivalent gains
- **Confirmation bias:** seeking info that confirms your existing position
- **Recency bias:** overweighting recent events
- **Disposition effect:** selling winners too early, holding losers too long

**Study goals:**
- [ ] Start a trading journal (even in paper trading)
- [ ] Write personal "tilt rules" — conditions where you STOP trading
- [ ] Read about cognitive biases that affect financial decisions
- [ ] Track your emotional state alongside your P&L

**Sources:**
- [SuperTrade — Trading Psychology in 2026](https://supertrade.com/blog/trading-psychology/)
- [Investopedia — Common Investor and Trader Blunders](https://www.investopedia.com/articles/active-trading/013015/worst-mistakes-beginner-traders-make.asp)
- [TradeZella — Master Your Mind (2026 Guide)](https://www.tradezella.com/blog/trading-psychology)
- [TradingSimulator — Avoid These Common Mistakes](https://www.tradingsim.com/blog/avoid-these-common-mistakes-in-day-trading)

---

### Phase 7: Common Beginner Mistakes

| # | Mistake | Why it kills you | Fix |
|---|---------|-----------------|-----|
| 1 | **No trading plan** | Random entries → random results | Write rules BEFORE market opens |
| 2 | **Risking too much per trade** | One bad trade wipes weeks of gains | Hard 1–2% rule per trade |
| 3 | **No stop-loss** | "It'll come back" → account ruin | Every trade has a predefined exit |
| 4 | **Overtrading** | Commissions + bad setups + fatigue | Quality > quantity; set daily trade limits |
| 5 | **Chasing entries** | Buying after the move; poor R:R | Wait for YOUR setup; missed trades are free |
| 6 | **Ignoring fees/spread** | Scalping with wide spreads = guaranteed loss | Calculate break-even including all costs |
| 7 | **Strategy-hopping** | Never learns one system deeply | Commit to one approach for 100+ trades |
| 8 | **Confusing paper gains with skill** | Bull market ≠ genius | Track vs benchmark; know your edge |
| 9 | **Trading with scared money** | Emotional decisions; can't tolerate normal drawdown | Only trade money you can afford to lose |
| 10 | **Skipping the journal** | No feedback loop → no improvement | Log every trade; review weekly |

---

## Glossary

| Term | Definition |
|------|-----------|
| **Ask** | Lowest price a seller will accept |
| **Bid** | Highest price a buyer will pay |
| **Spread** | Difference between bid and ask |
| **Liquidity** | How easily an asset can be bought/sold without moving price |
| **Leverage** | Using borrowed capital to amplify position size (amplifies losses too) |
| **Margin** | Collateral required to hold a leveraged position |
| **Margin call** | Broker demands more collateral or liquidates your position |
| **Drawdown** | Peak-to-trough decline in account value |
| **Sharpe ratio** | Risk-adjusted return: (return − risk-free rate) / standard deviation |
| **R:R (Risk:Reward)** | Ratio of potential loss to potential gain on a trade |
| **Stop-loss** | Predefined exit price to cap losses |
| **Take-profit** | Predefined exit price to lock in gains |
| **Slippage** | Difference between expected and actual execution price |
| **Volatility** | Magnitude of price fluctuations (often measured by ATR or std dev) |
| **ATR** | Average True Range — measure of daily price movement |
| **RSI** | Relative Strength Index — momentum oscillator (0–100) |
| **MACD** | Moving Average Convergence Divergence — trend/momentum indicator |
| **S/R** | Support and Resistance — price levels where buying/selling concentrates |
| **Backtesting** | Simulating a strategy on historical data to evaluate performance |
| **Paper trading** | Simulated trading with fake money to practice |
| **Edge** | Statistical advantage that produces positive expectancy over many trades |
| **Expectancy** | (Win% × Avg Win) − (Loss% × Avg Loss); must be positive to profit long-term |
| **Correlation** | How closely two assets move together (−1 to +1) |
| **Black swan** | Rare, extreme event that normal models don't predict |
| **Gap** | Price jump between sessions (e.g., overnight news) |

---

## Curated Resources

### Free Courses & Platforms
| Resource | URL | Notes |
|----------|-----|-------|
| Investopedia Academy (articles) | https://www.investopedia.com | Gold-standard reference for terms and concepts |
| Schwab/thinkorswim Education | https://www.schwab.com/learn | Structured courses, free with account |
| Tastytrade | https://www.tastytrade.com/learn | Options-focused; excellent free video content |
| Class Central — Best Trading Courses 2026 | https://www.classcentral.com/report/best-trading-courses | Curated list of academic & professional courses |
| Chart Academy (via TradeZella) | https://www.tradezella.com/blog/free-trading-education | 30+ trader masterclasses, free |
| Khan Academy (economics/finance) | https://www.khanacademy.org/economics-finance-domain | Fundamentals of markets, interest, valuation |

### Books (classics)
- *Trading in the Zone* — Mark Douglas (psychology)
- *Reminiscences of a Stock Operator* — Edwin Lefèvre (market wisdom)
- *Market Wizards* — Jack Schwager (interviews with top traders)
- *A Random Walk Down Wall Street* — Burton Malkiel (efficient markets perspective)
- *The Intelligent Investor* — Benjamin Graham (value investing fundamentals)
- *Technical Analysis of the Financial Markets* — John Murphy (TA reference)
- *Position Sizing* — Van Tharp (risk management deep-dive)

### Paper Trading / Simulators
| Platform | URL | Notes |
|----------|-----|-------|
| TradingView Paper Trading | https://www.tradingview.com | Charts + sim; free tier available |
| Investopedia Simulator | https://www.investopedia.com/simulator/ | $100K virtual account |
| TradingSim | https://www.tradingsim.com | Replay historical days |
| thinkorswim paperMoney | https://www.schwab.com/trading/thinkorswim | Full-featured sim |

### Backtesting (Python)
| Library | URL | Notes |
|---------|-----|-------|
| Backtesting.py | https://kernc.github.io/backtesting.py/ | Lightweight, pandas-based |
| Backtrader | https://www.backtrader.com | Event-driven, feature-rich |
| VectorBT | https://vectorbt.dev | Vectorized, fast parameter sweeps |

### Risk Management Deep-Dives
- [Investopedia — Position Sizing](https://www.investopedia.com/articles/trading/09/determine-position-size.asp/)
- [Britannica — Position Sizing in Trading](https://www.britannica.com/money/calculating-position-size)
- [Horizon Trading — Risk Management Guide](https://www.horizontrading.ai/learn/trading-risk-management-guide)

---

## Suggested Learning Schedule

| Week | Focus | Deliverable |
|------|-------|-------------|
| 1 | Markets & instruments; open a paper account | Written notes on each market type |
| 2 | Order types; place each type in simulator | Screenshot journal of orders |
| 3 | Risk management & position sizing | Position-size calculator (build one!) |
| 4 | Technical analysis basics | Chart 5 setups with annotated S/R, MA, RSI |
| 5 | Fundamental analysis basics | Analyze 2 companies' financial statements |
| 6 | Backtesting | Backtest 1 simple strategy in Python |
| 7 | Psychology + journaling | Start a trade journal; write tilt rules |
| 8 | Integration | Paper-trade for a week with full plan; review |

---

## Reminders

- **Paper trade for at least 2–3 months** before considering real money.
- A strategy needs 50–100+ trades to have statistical significance.
- If you can't explain your edge in one sentence, you don't have one.
- The market will always be there tomorrow. There is no rush.
- **Alfred will help you LEARN. Alfred will NEVER tell you to buy or sell anything.**

---

*Last updated: 2026-07-06*
*Built by Alfred — education only, never financial advice.*
