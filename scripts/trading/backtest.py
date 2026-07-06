"""
SMA Crossover Paper Backtest — pure Python stdlib.

⚠️ EDUCATIONAL ONLY — NOT FINANCIAL ADVICE.
This script simulates a simple moving-average crossover strategy on historical
CSV data. NO real trades are executed. Past results do NOT guarantee future
performance. Alfred NEVER executes real-money trades.

Usage:
    python backtest.py [path/to/prices.csv] [fast_period] [slow_period]

Defaults:
    CSV  = sample_prices.csv (in same directory)
    Fast = 5
    Slow = 10
"""

import csv
import os
import sys
from typing import List, Tuple

# Import indicators from the same package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import sma, max_drawdown


def load_csv(path: str) -> Tuple[List[str], List[float]]:
    """Load date and close columns from OHLC CSV."""
    dates: List[str] = []
    closes: List[float] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dates.append(row["date"])
            closes.append(float(row["close"]))
    return dates, closes


def run_backtest(
    dates: List[str],
    closes: List[float],
    fast_period: int = 5,
    slow_period: int = 10,
    initial_capital: float = 10000.0,
    data_path: str = "",
) -> None:
    """Run SMA crossover (long-only) and print results."""

    fast_ma = sma(closes, fast_period)
    slow_ma = sma(closes, slow_period)

    # State
    in_position = False
    entry_price = 0.0
    entry_date = ""
    shares = 0.0
    cash = initial_capital
    trades: List[dict] = []
    equity_curve: List[float] = [initial_capital]

    # Start after slow MA is available
    start_idx = slow_period  # first index where both MAs exist

    for i in range(start_idx, len(closes)):
        # Track equity
        if in_position:
            equity_curve.append(cash + shares * closes[i])
        else:
            equity_curve.append(cash)

        # Signals
        if fast_ma[i] is None or slow_ma[i] is None:
            continue
        if fast_ma[i - 1] is None or slow_ma[i - 1] is None:
            continue

        # Buy signal: fast crosses above slow
        if not in_position and fast_ma[i] > slow_ma[i] and fast_ma[i - 1] <= slow_ma[i - 1]:
            entry_price = closes[i]
            entry_date = dates[i]
            shares = cash / entry_price
            cash = 0.0
            in_position = True

        # Sell signal: fast crosses below slow
        elif in_position and fast_ma[i] < slow_ma[i] and fast_ma[i - 1] >= slow_ma[i - 1]:
            exit_price = closes[i]
            cash = shares * exit_price
            pnl_pct = (exit_price - entry_price) / entry_price * 100
            trades.append({
                "entry_date": entry_date,
                "exit_date": dates[i],
                "entry": entry_price,
                "exit": exit_price,
                "pnl_pct": pnl_pct,
            })
            shares = 0.0
            in_position = False

    # Final equity (mark-to-market if still in position)
    if in_position:
        final_equity = cash + shares * closes[-1]
    else:
        final_equity = cash
    equity_curve.append(final_equity)

    total_return_pct = (final_equity - initial_capital) / initial_capital * 100
    mdd = max_drawdown(equity_curve) * 100
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    win_rate = (wins / len(trades) * 100) if trades else 0.0

    # --- Output ---
    print("=" * 70)
    print("  [!] EDUCATIONAL PAPER BACKTEST -- NOT FINANCIAL ADVICE")
    print("  Simulated SMA-crossover strategy on historical CSV data.")
    print("  NO real trades are executed. Past results != future performance.")
    print("=" * 70)
    print()
    print(f"  Data:        {data_path}")
    print(f"  Bars:        {len(closes)}")
    print(f"  Date range:  {dates[0]} -> {dates[-1]}")
    print(f"  Strategy:    SMA({fast_period}) / SMA({slow_period}) crossover (long only)")
    print()
    print("-" * 40)
    print("  RESULTS (paper/simulated)")
    print("-" * 40)
    print(f"  Total Return:    {'+' if total_return_pct >= 0 else ''}{total_return_pct:.2f}%")
    print(f"  Closed Trades:   {len(trades)}")
    print(f"  Win Rate:        {win_rate:.1f}%")
    print(f"  Max Drawdown:    {mdd:.2f}%")
    print(f"  Final Equity:    ${final_equity:,.2f}  (from ${initial_capital:,.2f})")
    if in_position:
        print("  (Still in position at end of data -- mark-to-market included)")
    print("-" * 40)
    print()

    if trades:
        print("  Trade log:")
        for idx, t in enumerate(trades, 1):
            sign = "+" if t["pnl_pct"] >= 0 else ""
            print(
                f"    #{idx}  {t['entry_date']} -> {t['exit_date']}  "
                f"entry={t['entry']:.2f}  exit={t['exit']:.2f}  "
                f"P&L={sign}{t['pnl_pct']:.2f}%"
            )
        print()

    print("  [!] This is a SIMULATION for learning. Not financial advice.")
    print()


if __name__ == "__main__":
    # Parse args
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(script_dir, "sample_prices.csv")
    fast = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    slow = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    if not os.path.isfile(csv_path):
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    dates, closes = load_csv(csv_path)
    if len(closes) < slow + 1:
        print(f"ERROR: Need at least {slow + 1} bars, got {len(closes)}", file=sys.stderr)
        sys.exit(1)

    run_backtest(dates, closes, fast, slow, data_path=csv_path)
