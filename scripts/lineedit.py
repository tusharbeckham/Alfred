#!/usr/bin/env python3
"""A line editor with history and tab completion - no dependencies.

WHY THIS EXISTS
---------------
`input()` on Windows gives no history recall and no completion, so an interactive
console built on it feels broken: no up-arrow, no tab. The usual answer is
`readline`, which Python does not ship on Windows, or `pyreadline3`, which is a
dependency this project has decided not to take.

`msvcrt` is in the standard library and gives raw key reads, so the editor is built
directly on it. On POSIX the real `readline` module is used when present, because it
is better than anything reimplemented here.

DELIBERATE LIMITS
-----------------
No multi-line editing, no reverse search, no kill ring. This is a command prompt for
short commands, and every feature added here is a feature that can break someone's
terminal. What is implemented: cursor movement, history, tab completion, and the
control keys people reflexively press (Ctrl+C, Ctrl+D, Ctrl+U, Ctrl+A/E).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Sequence

WINDOWS = os.name == "nt"

if WINDOWS:
    try:
        import msvcrt
    except ImportError:  # pragma: no cover - msvcrt is always present on Windows
        msvcrt = None
else:
    msvcrt = None
    try:
        import readline as _posix_readline
    except ImportError:
        _posix_readline = None


class History:
    """Command history, persisted so it survives across sessions."""

    def __init__(self, path: Path | None = None, limit: int = 500) -> None:
        self.path = path
        self.limit = limit
        self.items: list[str] = []
        if path and path.exists():
            try:
                self.items = [line.rstrip("\n") for line in
                              path.read_text(encoding="utf-8", errors="replace").splitlines()
                              if line.strip()][-limit:]
            except OSError:
                self.items = []
        self.cursor = len(self.items)

    def add(self, line: str) -> None:
        line = line.strip()
        # Skip blanks and immediate repeats: history full of duplicates is useless.
        if not line or (self.items and self.items[-1] == line):
            self.cursor = len(self.items)
            return
        self.items.append(line)
        del self.items[:-self.limit]
        self.cursor = len(self.items)
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(self.items) + "\n", encoding="utf-8")
        except OSError:
            pass  # history is a convenience; never fail a session over it

    def previous(self, current: str) -> str:
        if not self.items:
            return current
        self.cursor = max(0, self.cursor - 1)
        return self.items[self.cursor]

    def next(self, current: str) -> str:
        if not self.items:
            return current
        self.cursor = min(len(self.items), self.cursor + 1)
        return "" if self.cursor >= len(self.items) else self.items[self.cursor]


def common_prefix(options: Sequence[str]) -> str:
    if not options:
        return ""
    first, shortest = options[0], min(options, key=len)
    for index in range(len(shortest)):
        if any(option[index] != first[index] for option in options):
            return first[:index]
    return shortest


class LineEditor:
    """Reads one line with editing, history and completion."""

    def __init__(self, history: History | None = None,
                 completer: Callable[[str], list[str]] | None = None,
                 stream=sys.stdout) -> None:
        self.history = history or History()
        self.completer = completer
        self.stream = stream

    # -- capability -------------------------------------------------------
    @property
    def interactive(self) -> bool:
        """Raw editing needs a real console on both ends."""
        return bool(msvcrt) and sys.stdin.isatty() and self.stream.isatty()

    # -- painting ---------------------------------------------------------
    def _redraw(self, prompt: str, buffer: str, position: int) -> None:
        # \r to column 0, \x1b[K to clear the rest, then reposition the cursor.
        self.stream.write(f"\r\x1b[K{prompt}{buffer}")
        trailing = len(buffer) - position
        if trailing > 0:
            self.stream.write(f"\x1b[{trailing}D")
        self.stream.flush()

    # -- completion -------------------------------------------------------
    def _complete(self, prompt: str, buffer: str, position: int) -> tuple[str, int]:
        if not self.completer:
            return buffer, position
        options = self.completer(buffer[:position])
        if not options:
            return buffer, position

        head = buffer[:position]
        tail = buffer[position:]
        token_start = max(head.rfind(" ") + 1, 0)
        stem = head[token_start:]

        if len(options) == 1:
            completion = options[0] + " "
        else:
            shared = common_prefix(options)
            if len(shared) > len(stem):
                completion = shared
            else:
                # Ambiguous: show the choices rather than guessing.
                self.stream.write("\n  " + "  ".join(options[:24]) + "\n")
                self._redraw(prompt, buffer, position)
                return buffer, position

        new_head = head[:token_start] + completion
        return new_head + tail, len(new_head)

    # -- the read loop ----------------------------------------------------
    def read(self, prompt: str) -> str:
        """Read one line. Raises EOFError on Ctrl+D, KeyboardInterrupt on Ctrl+C."""
        if not self.interactive:
            # Piped, redirected, or POSIX with real readline: use the builtin, which
            # already handles those cases better than this loop would.
            line = input(prompt)
            self.history.add(line)
            return line

        buffer, position = "", 0
        self.stream.write(prompt)
        self.stream.flush()

        while True:
            char = msvcrt.getwch()

            if char in ("\r", "\n"):
                self.stream.write("\n")
                self.stream.flush()
                self.history.add(buffer)
                return buffer

            if char == "\x03":                      # Ctrl+C
                self.stream.write("\n")
                raise KeyboardInterrupt
            if char == "\x04":                      # Ctrl+D
                if not buffer:
                    self.stream.write("\n")
                    raise EOFError
                continue
            if char == "\x15":                      # Ctrl+U - clear the line
                buffer, position = "", 0
                self._redraw(prompt, buffer, position)
                continue
            if char == "\x01":                      # Ctrl+A - start of line
                position = 0
                self._redraw(prompt, buffer, position)
                continue
            if char == "\x05":                      # Ctrl+E - end of line
                position = len(buffer)
                self._redraw(prompt, buffer, position)
                continue
            if char == "\t":
                buffer, position = self._complete(prompt, buffer, position)
                self._redraw(prompt, buffer, position)
                continue
            if char == "\x08":                      # Backspace
                if position:
                    buffer = buffer[:position - 1] + buffer[position:]
                    position -= 1
                    self._redraw(prompt, buffer, position)
                continue

            if char in ("\x00", "\xe0"):            # special key: read the second byte
                code = msvcrt.getwch()
                if code == "H":                     # Up
                    buffer = self.history.previous(buffer)
                    position = len(buffer)
                elif code == "P":                   # Down
                    buffer = self.history.next(buffer)
                    position = len(buffer)
                elif code == "K":                   # Left
                    position = max(0, position - 1)
                elif code == "M":                   # Right
                    position = min(len(buffer), position + 1)
                elif code == "G":                   # Home
                    position = 0
                elif code == "O":                   # End
                    position = len(buffer)
                elif code == "S":                   # Delete
                    buffer = buffer[:position] + buffer[position + 1:]
                else:
                    continue
                self._redraw(prompt, buffer, position)
                continue

            if char.isprintable():
                buffer = buffer[:position] + char + buffer[position:]
                position += 1
                self._redraw(prompt, buffer, position)

    # -- convenience ------------------------------------------------------
    def show_hint(self, text: str) -> None:
        self.stream.write(f"  {text}\n")
        self.stream.flush()


def make_completer(commands: Sequence[str],
                   arg_options: dict[str, Sequence[str]] | None = None
                   ) -> Callable[[str], list[str]]:
    """Complete the command word, then that command's known arguments.

    Context matters: `run <TAB>` should offer specs, not commands. Anything without
    declared arguments simply offers nothing rather than something misleading.
    """
    arg_options = arg_options or {}

    def complete(text: str) -> list[str]:
        stripped = text.lstrip()
        if " " not in stripped:
            stem = stripped
            return sorted(c for c in commands if c.startswith(stem))
        verb, _, rest = stripped.partition(" ")
        options = arg_options.get(verb.lower())
        if not options:
            return []
        stem = rest.rsplit(" ", 1)[-1]
        return sorted(o for o in options if o.startswith(stem))

    return complete


if __name__ == "__main__":
    COMMANDS = ["status", "run", "graph", "recall", "help", "quit"]
    SPECS = ["feature-gated", "deploy-gated"]
    editor = LineEditor(
        history=History(Path.home() / ".alfred_lineedit_demo"),
        completer=make_completer(COMMANDS, {"run": SPECS, "graph": SPECS}),
    )
    print(f"line editor demo (interactive={editor.interactive})")
    print("try: tab completion, up/down history, Ctrl+U, Ctrl+A/E. 'quit' to exit.")
    while True:
        try:
            line = editor.read("demo > ")
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if line.strip() in ("quit", "exit"):
            print("bye")
            break
        print(f"  you typed: {line!r}")
