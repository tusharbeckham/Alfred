#!/usr/bin/env python3
"""
Alfred public chat app — Gradio ChatInterface wired to the abuse guard, the persona, and any
OpenAI-compatible LLM backend (Groq / Fireworks / Together / OpenAI / local LM Studio), with token
streaming and graceful fallbacks. Run locally or deploy to a Hugging Face Space.

Config via environment:
  LLM_BASE_URL  (default http://localhost:1234/v1  — local LM Studio, for testing)
  LLM_API_KEY   (default 'not-needed' locally; set your provider key as a SECRET in prod)
  LLM_MODEL     (default 'alfred-coder-7b')
  RATE_PER_MIN, RATE_BURST, MAX_LEN, MAX_CONCURRENCY, PORT

Heavy deps (gradio, openai) are imported lazily so the pure logic is testable without them.
"""
from __future__ import annotations
import os
from pathlib import Path
from guard import Guard, HOLDING_LINES

HERE = Path(__file__).parent
_p = HERE / "persona.txt"
PERSONA = _p.read_text(encoding="utf-8") if _p.exists() else "You are Alfred, a witty, helpful AI."

GUARD = Guard(
    per_min=int(os.getenv("RATE_PER_MIN", "20")),
    burst=int(os.getenv("RATE_BURST", "5")),
    max_len=int(os.getenv("MAX_LEN", "2000")),
    max_concurrency=int(os.getenv("MAX_CONCURRENCY", "32")),
)

# Thin SECONDARY moderation net. Primary safety = the persona + the LLM's own safety training +
# (in prod) the provider's moderation endpoint. Extend via deploy/blocklist.txt (one term/line; gitignored).
def _load_blocklist() -> set[str]:
    f = HERE / "blocklist.txt"
    terms: set[str] = set()
    if f.exists():
        for ln in f.read_text(encoding="utf-8").splitlines():
            ln = ln.strip().lower()
            if ln and not ln.startswith("#"):
                terms.add(ln)
    return terms

BLOCKLIST = _load_blocklist()

def moderate(text: str) -> str:
    """Return text unchanged, or a composed deflection if it trips the blocklist (defense in depth)."""
    low = (text or "").lower()
    if BLOCKLIST and any(t in low for t in BLOCKLIST):
        return "Let's keep this civil — I'll help, but not like that."
    return text

def _client():
    from openai import OpenAI
    return OpenAI(
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:1234/v1"),
        api_key=os.getenv("LLM_API_KEY", "not-needed"),
    )

def _session_key(request) -> str:
    try:
        if request is not None:
            host = getattr(getattr(request, "client", None), "host", None)
            if host:
                return host
            if getattr(request, "session_hash", None):
                return request.session_hash
    except Exception:
        pass
    return "anon"

def _history_messages(history):
    out = []
    for h in (history or [])[-6:]:
        if isinstance(h, dict) and h.get("role"):
            out.append({"role": h["role"], "content": h.get("content", "")})
        elif isinstance(h, (list, tuple)) and len(h) == 2:
            if h[0]:
                out.append({"role": "user", "content": h[0]})
            if h[1]:
                out.append({"role": "assistant", "content": h[1]})
    return out

def respond(message, history, request=None):
    key = _session_key(request)
    decision = GUARD.check(key, message)
    if not decision.allowed:
        yield HOLDING_LINES.get(decision.reason, HOLDING_LINES["error"])
        return

    msgs = [{"role": "system", "content": PERSONA}] + _history_messages(history)
    msgs.append({"role": "user", "content": decision.cleaned})

    GUARD.enter()
    acc = ""
    try:
        stream = _client().chat.completions.create(
            model=os.getenv("LLM_MODEL", "alfred-coder-7b"),
            messages=msgs, temperature=0.6, max_tokens=400, stream=True,
        )
        for chunk in stream:
            delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if delta:
                acc += delta
                yield moderate(acc)
        if not acc:
            yield HOLDING_LINES["error"]
    except Exception:
        yield HOLDING_LINES["error"]
    finally:
        GUARD.exit()

def main():
    import gradio as gr
    demo = gr.ChatInterface(
        fn=respond,
        title="Alfred",
        description="Ask Alfred anything. He's sharp, he's quick, and he doesn't rattle.",
        theme="soft",
    )
    demo.queue(default_concurrency_limit=int(os.getenv("MAX_CONCURRENCY", "32")))
    demo.launch(server_name="0.0.0.0", server_port=int(os.getenv("PORT", "7860")))

if __name__ == "__main__":
    main()
