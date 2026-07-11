#!/usr/bin/env python3
"""
Alfred deploy guard - the "no crashing from spam" layer for a public Alfred chatbot.

Framework-agnostic, stdlib-only. Plug `Guard.check(key, message)` into any backend (Gradio, FastAPI,
Flask, Next.js-via-subprocess) BEFORE calling the LLM. It enforces, per sender key (IP or session):

  - token-bucket RATE LIMIT   (steady rate + small burst)
  - input HYGIENE             (length cap, control-char strip, empty reject)
  - FLOOD / dedup             (same text hammered repeatedly -> throttled)
  - global CONCURRENCY cap    (bound in-flight work; shed load, never fall over)

check() returns a Decision(allowed, reason, cleaned). It never raises on bad input - it degrades.
"""
from __future__ import annotations
import time, re, threading
from collections import deque, defaultdict
from dataclasses import dataclass

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class Decision:
    allowed: bool
    reason: str
    cleaned: str = ""


class _Bucket:
    __slots__ = ("tokens", "cap", "rate", "ts")
    def __init__(self, cap: float, rate: float):
        self.tokens = cap; self.cap = cap; self.rate = rate; self.ts = time.monotonic()
    def take(self, n: float = 1.0) -> bool:
        now = time.monotonic()
        self.tokens = min(self.cap, self.tokens + (now - self.ts) * self.rate)
        self.ts = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


class Guard:
    def __init__(self, per_min: int = 20, burst: int = 5, max_len: int = 2000,
                 flood_window_s: float = 10.0, flood_repeats: int = 3, max_concurrency: int = 32):
        self.rate = per_min / 60.0
        self.burst = burst
        self.max_len = max_len
        self.flood_window_s = flood_window_s
        self.flood_repeats = flood_repeats
        self.max_concurrency = max_concurrency
        self._buckets: dict[str, _Bucket] = {}
        self._recent: dict[str, deque] = defaultdict(deque)  # key -> deque[(ts, text)]
        self._inflight = 0
        self._lock = threading.Lock()

    def sanitize(self, message: str) -> str:
        if not isinstance(message, str):
            message = str(message or "")
        message = _CONTROL.sub("", message).strip()
        if len(message) > self.max_len:
            message = message[: self.max_len]
        return message

    def check(self, key: str, message: str) -> Decision:
        key = key or "anon"
        cleaned = self.sanitize(message)
        if not cleaned:
            return Decision(False, "empty", "")

        with self._lock:
            # concurrency guard (shed load gracefully)
            if self._inflight >= self.max_concurrency:
                return Decision(False, "busy", cleaned)

            # rate limit (token bucket per key)
            b = self._buckets.get(key)
            if b is None:
                b = self._buckets[key] = _Bucket(cap=self.burst, rate=self.rate)
            if not b.take(1.0):
                return Decision(False, "rate_limited", cleaned)

            # flood / repeat detection
            now = time.monotonic()
            dq = self._recent[key]
            dq.append((now, cleaned))
            while dq and now - dq[0][0] > self.flood_window_s:
                dq.popleft()
            repeats = sum(1 for _, t in dq if t == cleaned)
            if repeats >= self.flood_repeats:
                return Decision(False, "flood", cleaned)

            return Decision(True, "ok", cleaned)

    # wrap the LLM call so concurrency is tracked even if it throws
    def enter(self):
        with self._lock:
            self._inflight += 1
    def exit(self):
        with self._lock:
            self._inflight = max(0, self._inflight - 1)


# A witty, non-abusive holding line for when we block or the LLM is unavailable.
HOLDING_LINES = {
    "rate_limited": "Easy, tiger. Even I need a breath between brilliancies - try again in a moment.",
    "flood": "You've said that. Repeatedly. I heard you the first time, and it wasn't better on replay.",
    "busy": "I'm rather in demand this second, sir. Give me a heartbeat and ask again.",
    "empty": "You'll have to actually say something. I'm sharp, not clairvoyant.",
    "error": "That one tripped a wire on my end - not yours. Ask me again in a moment.",
}


def _selftest() -> int:
    ok = True
    g = Guard(per_min=60, burst=3, max_len=20, flood_window_s=5.0, flood_repeats=3, max_concurrency=2)

    # sanitize: control chars stripped + length capped
    s = g.sanitize("hi\x00\x07 there this is way too long to keep")
    assert "\x00" not in s and len(s) <= 20, s
    print(f"  sanitize -> [{s}] len={len(s)}  OK")

    # rate limit: burst=3 then blocked
    res = [g.check("ip1", f"msg {i}").allowed for i in range(5)]
    assert res[:3] == [True, True, True] and res[3] is False, res
    print(f"  rate-limit burst -> {res}  OK")

    # flood: same text repeated -> blocked (fresh key, generous rate)
    g2 = Guard(per_min=600, burst=100, flood_repeats=3)
    floods = [g2.check("ip2", "same").reason for _ in range(4)]
    assert floods[-1] == "flood", floods
    print(f"  flood -> {floods}  OK")

    # empty rejected
    assert g2.check("ip3", "   ").reason == "empty"
    print("  empty -> rejected  OK")

    # concurrency shed
    g3 = Guard(per_min=600, burst=100, max_concurrency=1)
    g3.enter()
    assert g3.check("ip4", "hello").reason == "busy"
    g3.exit()
    assert g3.check("ip4", "hello").allowed is True
    print("  concurrency shed -> busy then ok  OK")

    print("ALL GUARD TESTS PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
