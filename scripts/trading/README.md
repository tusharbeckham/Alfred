# 📊 Trading Scripts — Educational Tools

> **⚠️ EDUCATION ONLY — NOT FINANCIAL ADVICE.**
> These scripts are for learning about technical analysis and backtesting concepts.
> They do NOT constitute trading recommendations. Alfred NEVER executes real trades.

---

## Files

| File | Purpose |
|------|---------|
| `indicators.py` | Pure-stdlib indicator library: SMA, EMA, RSI, daily returns, max drawdown |
| `backtest.py` | SMA crossover paper backtest — loads CSV, simulates trades, prints metrics |
| `sample_prices.csv` | 50 bars of synthetic OHLC data (2024-01-02 to 2024-03-11) |

---

## Quick Start

```powershell
# Run the backtest with defaults (SMA(5)/SMA(10) on sample data)
python C:\Alfred\scripts\trading\backtest.py

# Custom parameters
python C:\Alfred\scripts\trading\backtest.py path\to\data.csv 10 20
```

**Requirements:** Python 3.8+ (stdlib only — zero pip installs).

---

## Using indicators.py as a library

```python
from indicators import sma, ema, rsi, daily_returns, max_drawdown

prices = [100, 101, 102, 101, 103, 104, 105, 103, 102, 104, 106]

print(sma(prices, 5))       # [None, None, None, None, 101.4, ...]
print(ema(prices, 5))       # [None, None, None, None, 101.4, ...]
print(rsi(prices, 5))       # [None, None, None, None, None, 80.0, ...]
print(daily_returns(prices)) # [0.0, 0.01, 0.0099..., ...]
print(max_drawdown(prices))  # -0.0285... (worst peak-to-trough)
```

---

## CSV Data Format

```
date,open,high,low,close,volume
2024-01-02,100.00,101.50,99.50,101.00,15000
```

- **Required columns:** `date`, `close` (backtest uses these)
- **Optional columns:** `open`, `high`, `low`, `volume` (present in sample for completeness)
- No header row variants — first row must be the header

---

## Extending

- Add more indicators to `indicators.py` (MACD, Bollinger Bands, ATR, etc.)
- Modify `backtest.py` to test different strategies (RSI mean-reversion, breakout, etc.)
- Bring your own CSV data (Yahoo Finance, Alpha Vantage free tier, etc.)

---

## Disclaimer

This is a **learning exercise**. Real trading involves slippage, commissions, liquidity
constraints, and psychological factors that no simple backtest captures. Paper-trade
extensively before risking real capital. See `docs/trading/README.md` for the full
learning roadmap.
