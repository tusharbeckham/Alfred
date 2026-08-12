#!/usr/bin/env python3
"""Alfred branding - one place for the logo, colours and glyphs.

Every entrypoint (console, harness, gauntlet, dashboard) should look like part of
the same system, so the marks live here rather than being re-typed per script.

Encoding is the constraint that shapes this file: the Windows console defaults to
cp1252 and raises UnicodeEncodeError on box-drawing characters, which has broken
this repo's output twice. So every glyph has an ASCII fallback, chosen once from the
real stdout encoding, and `NO_COLOR` is honoured.
"""

from __future__ import annotations

import os
import shutil
import sys


def _ansi_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.name != "nt":
        return sys.stdout.isatty()
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        return True
    except Exception:  # noqa: BLE001
        return False


def _unicode_ok() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    if "utf" in encoding:
        return True
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        return True
    except Exception:  # noqa: BLE001
        return False


ANSI = _ansi_enabled()
UNICODE = _unicode_ok()


def _c(code: str) -> str:
    return f"\x1b[{code}m" if ANSI else ""


RESET, BOLD, DIM = _c("0"), _c("1"), _c("2")
RED, GREEN, YELLOW = _c("91"), _c("92"), _c("93")
BLUE, MAGENTA, CYAN, GREY = _c("94"), _c("95"), _c("96"), _c("90")

OK = "\u2713" if UNICODE else "OK"
FAIL = "\u2717" if UNICODE else "X"
WARN = "!"
PENDING = "\u25cb" if UNICODE else "."
WORK = "\u25cf" if UNICODE else "*"
GATE = "\u25c6" if UNICODE else "<>"
APPROVAL = "\u25b2" if UNICODE else "!"
ARROW = "\u2192" if UNICODE else "->"
TEE = "\u251c\u2500" if UNICODE else "+-"
ELBOW = "\u2570\u2500" if UNICODE else "\\-"
BAR_FULL = "\u2588" if UNICODE else "#"
BAR_EMPTY = "\u2591" if UNICODE else "-"

#: Deliberately ASCII: this renders identically everywhere, including cp1252
#: consoles, PuTTY and CI logs.
LOGO = r"""
    _    _     _____ ____  _____ ____
   / \  | |   |  ___|  _ \| ____|  _ \
  / _ \ | |   | |_  | |_) |  _| | | | |
 / ___ \| |___|  _| |  _ <| |___| |_| |
/_/   \_\_____|_|   |_| \_\_____|____/
"""

WORDMARK = f"{CYAN}{BOLD}ALFRED{RESET}"


def width(default: int = 100) -> int:
    return max(60, min(shutil.get_terminal_size((default, 30)).columns, 120))


def rule(label: str = "", char: str = "-") -> str:
    if not label:
        return f"{GREY}{char * (width() - 2)}{RESET}"
    tail = max(0, width() - len(label) - 6)
    return f"{GREY}{char * 2} {label} {char * tail}{RESET}"


def logo(tagline: str = "") -> str:
    parts = [f"{CYAN}{BOLD}{LOGO}{RESET}"]
    if tagline:
        parts.append(f"  {DIM}{tagline}{RESET}\n")
    return "\n".join(parts)


def chip(label: str, state: bool | None, detail: str = "") -> str:
    """A status chip: green tick, red cross, or amber question mark."""
    if state is None:
        mark, colour = WARN, YELLOW
    elif state:
        mark, colour = OK, GREEN
    else:
        mark, colour = FAIL, RED
    text = f"{colour}{mark} {label}{RESET}"
    return f"{text} {DIM}{detail}{RESET}" if detail else text


def bar(done: int, total: int, size: int = 24) -> str:
    ratio = 0.0 if total <= 0 else max(0.0, min(1.0, done / total))
    filled = int(ratio * size)
    return (f"{BLUE}{BAR_FULL * filled}{GREY}{BAR_EMPTY * (size - filled)}{RESET} "
            f"{int(ratio * 100):3d}% {DIM}{done}/{total}{RESET}")


VERDICT_COLOURS = {
    "PASS": GREEN, "RETRY": YELLOW, "REROUTE": MAGENTA,
    "ESCALATE": CYAN, "ABORT": RED,
}


def verdict(name: str) -> str:
    return f"{VERDICT_COLOURS.get(name, GREY)}{name}{RESET}"


if __name__ == "__main__":
    print(logo("personal multi-agent system | policy-gated | offline-capable"))
    print(rule("brand check"))
    print("  " + chip("ansi", ANSI, "colour enabled" if ANSI else "plain text"))
    print("  " + chip("unicode", UNICODE, "box glyphs" if UNICODE else "ascii fallback"))
    print(f"  glyphs: {WORK} work  {GATE} gate  {APPROVAL} approval  {ARROW} route")
    print(f"  verdicts: " + "  ".join(verdict(v) for v in VERDICT_COLOURS))
    print("  " + bar(7, 10))
    print(rule())
