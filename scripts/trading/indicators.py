"""
Trading indicators — pure Python stdlib, no external dependencies.

⚠️ EDUCATIONAL ONLY — NOT FINANCIAL ADVICE.
These functions are for learning and paper-backtesting. They do NOT constitute
a recommendation to buy or sell any security.
"""

from typing import List


def sma(prices: List[float], period: int) -> List[float]:
    """Simple Moving Average.

    Returns a list the same length as `prices`. The first (period-1) values
    are None (not enough data). From index (period-1) onward, each value is
    the arithmetic mean of the preceding `period` prices (inclusive).
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    result: List[float] = [None] * len(prices)  # type: ignore[list-item]
    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1: i + 1]
        result[i] = sum(window) / period
    return result


def ema(prices: List[float], period: int) -> List[float]:
    """Exponential Moving Average.

    Uses the standard multiplier: k = 2 / (period + 1).
    Seeds with SMA of the first `period` values.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    if len(prices) < period:
        return [None] * len(prices)  # type: ignore[list-item]

    k = 2.0 / (period + 1)
    result: List[float] = [None] * len(prices)  # type: ignore[list-item]
    # Seed: SMA of first `period` values
    seed = sum(prices[:period]) / period
    result[period - 1] = seed
    for i in range(period, len(prices)):
        result[i] = prices[i] * k + result[i - 1] * (1 - k)
    return result


def rsi(prices: List[float], period: int = 14) -> List[float]:
    """Relative Strength Index (Wilder's smoothing).

    Returns list same length as `prices`. First `period` values are None.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    if len(prices) < period + 1:
        return [None] * len(prices)  # type: ignore[list-item]

    result: List[float] = [None] * len(prices)  # type: ignore[list-item]
    gains: List[float] = []
    losses: List[float] = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    # First average: simple mean of first `period` changes
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    # Subsequent values: Wilder's smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return result


def daily_returns(prices: List[float]) -> List[float]:
    """Percentage daily returns. First value is 0.0 (no prior bar)."""
    if not prices:
        return []
    returns = [0.0]
    for i in range(1, len(prices)):
        if prices[i - 1] == 0:
            returns.append(0.0)
        else:
            returns.append((prices[i] - prices[i - 1]) / prices[i - 1])
    return returns


def max_drawdown(prices: List[float]) -> float:
    """Maximum peak-to-trough drawdown as a negative fraction (e.g., -0.15 = -15%).

    Returns 0.0 if prices never decline.
    """
    if len(prices) < 2:
        return 0.0
    peak = prices[0]
    worst = 0.0
    for p in prices[1:]:
        if p > peak:
            peak = p
        dd = (p - peak) / peak if peak != 0 else 0.0
        if dd < worst:
            worst = dd
    return worst
