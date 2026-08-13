#!/usr/bin/env python3
"""Alfred model providers - one registry for local and hosted models.

WHY THIS EXISTS
---------------
Alfred routes work to the cheapest tier that can do it correctly (see
`.kiro/steering/token-budget.md`). That needs one place that knows which models
exist, what they cost, and whether they are actually reachable right now.

All of these speak the **OpenAI-compatible** chat-completions shape, so one client
covers LM Studio, NVIDIA NIM and DeepSeek. Adding a provider is a table entry, not
new code.

KEY HANDLING - read this before adding a provider
-------------------------------------------------
* Keys are read from the environment first, then `secrets/models.json`.
  `secrets/` is git-ignored and is on the dashboard's forbidden-path list.
* Keys are **never printed, logged, or returned**. Every display path shows a
  presence flag and a masked fingerprint (last 4 chars), never the value.
* There is no "write a key" command that echoes it back. `set-key` reads from a
  prompt or stdin, writes, and reports only the key *name*.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets" / "models.json"


@dataclass(frozen=True)
class Provider:
    """One OpenAI-compatible endpoint.

    ``usd_in`` / ``usd_out`` are per MILLION tokens and are used for budget
    estimates only - never billed against. ``None`` means free (local).
    """

    name: str
    base_url: str
    default_model: str
    env_key: str | None = None          # None = no key needed (local)
    usd_in: float | None = None
    usd_out: float | None = None
    tier: str = "api"                   # local | cheap | mid | frontier
    notes: str = ""

    @property
    def needs_key(self) -> bool:
        return self.env_key is not None

    @property
    def free(self) -> bool:
        """Only LOCAL providers are free.

        Deliberately not inferred from missing prices: a hosted provider whose
        rates we have not filled in would then report $0 for billable calls, which
        silently under-reports spend. Unknown cost must surface as unknown.
        """
        return self.tier == "local"


#: The registry. Prices are indicative and MUST be re-checked against the
#: provider's own pricing page before anyone relies on them for a budget.
PROVIDERS: dict[str, Provider] = {
    "lmstudio": Provider(
        name="lmstudio",
        base_url=os.environ.get("ALFRED_LMSTUDIO", "http://localhost:1234") + "/v1",
        default_model="alfred-coder-7b",
        env_key=None,
        tier="local",
        notes="Local, free, offline. The default for routine work.",
    ),
    "ollama": Provider(
        name="ollama",
        base_url=os.environ.get("ALFRED_OLLAMA", "http://localhost:11434") + "/v1",
        default_model="qwen2.5-coder:7b",
        env_key=None,
        tier="local",
        notes="Alternative local runtime, OpenAI-compatible endpoint.",
    ),
    "nvidia": Provider(
        name="nvidia",
        base_url="https://integrate.api.nvidia.com/v1",
        default_model="deepseek-ai/deepseek-r1",
        env_key="NVIDIA_API_KEY",
        tier="cheap",
        notes="NVIDIA NIM hosts DeepSeek and others behind an OpenAI-compatible API.",
    ),
    "deepseek": Provider(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        env_key="DEEPSEEK_API_KEY",
        tier="cheap",
        notes="DeepSeek direct. Cheapest capable API tier for bulk work.",
    ),
    "freebuff": Provider(
        name="freebuff",
        # A LOCAL gateway that fans out to free upstreams (freebuff.llm.pm,
        # OpenRouter's free tier, Ollama). Start it with:
        #   pip install freebuff-gateway && freebuff-gateway start --port 8080
        # Verified default from its README: http://localhost:8080/v1
        base_url=os.environ.get("ALFRED_FREEBUFF", "http://localhost:8080") + "/v1",
        default_model="gpt-3.5-turbo",   # the gateway maps whatever name you send
        env_key=None,                    # the gateway holds any upstream keys itself
        tier="cheap",
        notes="FreeBuff gateway (local proxy to free upstreams). Free, but rate-limited "
              "and third-party - do not send secrets or private code through it.",
    ),
    "openrouter": Provider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        default_model="deepseek/deepseek-chat",
        env_key="OPENROUTER_API_KEY",
        tier="mid",
        notes="Aggregator; useful for reaching many models with one key.",
    ),
}


# ------------------------------------------------------------------ key storage


def _load_secrets() -> dict[str, str]:
    if not SECRETS.exists():
        return {}
    try:
        data = json.loads(SECRETS.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_key(provider: Provider) -> str | None:
    """Environment first, then secrets/models.json. Never logged."""
    if not provider.env_key:
        return None
    from_env = os.environ.get(provider.env_key)
    if from_env:
        return from_env.strip()
    return _load_secrets().get(provider.env_key, "").strip() or None


def fingerprint(key: str | None) -> str:
    """A safe way to show that a key is present without revealing it."""
    if not key:
        return "absent"
    return f"set (...{key[-4:]})" if len(key) > 4 else "set"


def set_key(env_key: str, value: str) -> Path:
    """Persist a key to secrets/models.json. Returns the path, never the value."""
    if not value or not value.strip():
        raise ValueError("refusing to store an empty key")
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    store = _load_secrets()
    store[env_key] = value.strip()
    SECRETS.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    try:  # best effort on Windows; the folder is already agent-denied by policy
        os.chmod(SECRETS, 0o600)
    except OSError:
        pass
    return SECRETS


# --------------------------------------------------------------------- probing


@dataclass
class ProviderStatus:
    name: str
    tier: str
    configured: bool
    reachable: bool | None = None      # None = not probed (needs a key first)
    models: list[str] = field(default_factory=list)
    key: str = "absent"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tier": self.tier, "configured": self.configured,
                "reachable": self.reachable, "models": self.models[:8],
                "key": self.key, "detail": self.detail}


def probe(provider: Provider, *, timeout: float = 6.0, list_models: bool = True) -> ProviderStatus:
    """Check whether a provider is usable right now. Never raises, never leaks keys."""
    key = get_key(provider)
    configured = (not provider.needs_key) or bool(key)
    status = ProviderStatus(name=provider.name, tier=provider.tier,
                            configured=configured, key=fingerprint(key))
    if not configured:
        status.detail = f"needs {provider.env_key}"
        return status
    if not list_models:
        return status

    request = urllib.request.Request(f"{provider.base_url}/models", method="GET")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
        status.reachable = True
        status.models = [m.get("id") for m in body.get("data", []) if m.get("id")]
    except urllib.error.HTTPError as exc:
        status.reachable = False
        # 401/403 means the key is wrong - worth saying, without echoing the key.
        status.detail = f"HTTP {exc.code}" + (" (key rejected)" if exc.code in (401, 403) else "")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        status.reachable = False
        status.detail = str(exc)[:120]
    return status


def probe_all(*, timeout: float = 6.0) -> list[ProviderStatus]:
    import threading

    results: dict[str, ProviderStatus] = {}
    threads = []
    for name, provider in PROVIDERS.items():
        def worker(name=name, provider=provider):
            results[name] = probe(provider, timeout=timeout)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout=timeout + 4)
    order = list(PROVIDERS)
    return [results[n] for n in order if n in results]


# ----------------------------------------------------------------------- client


def chat(prompt: str, *, provider: str = "lmstudio", model: str | None = None,
         system: str | None = None, temperature: float = 0.3,
         max_tokens: int = 600, timeout: float = 180.0) -> dict[str, Any]:
    """One chat completion. Returns {ok, text, usage, model, provider, error}."""
    spec = PROVIDERS.get(provider)
    if spec is None:
        return {"ok": False, "error": f"unknown provider '{provider}'"}
    key = get_key(spec)
    if spec.needs_key and not key:
        return {"ok": False, "error": f"{provider} needs {spec.env_key}"}

    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    payload = json.dumps({
        "model": model or spec.default_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{spec.base_url}/chat/completions", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    if key:
        request.add_header("Authorization", f"Bearer {key}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": f"HTTP {exc.code} {detail}", "provider": provider}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)[:200], "provider": provider}

    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "error": "unexpected response shape", "provider": provider}

    usage = body.get("usage") or {}
    return {
        "ok": True, "text": text, "provider": provider,
        "model": body.get("model", model or spec.default_model),
        "usage": usage,
        "estimatedUsd": estimate_cost(spec, usage),
    }


def estimate_cost(spec: Provider, usage: dict[str, Any]) -> float | None:
    """Indicative only. Returns None for local/free providers."""
    if spec.free:
        return 0.0
    if spec.usd_in is None or spec.usd_out is None:
        return None
    prompt_tokens = float(usage.get("prompt_tokens") or 0)
    completion_tokens = float(usage.get("completion_tokens") or 0)
    return round((prompt_tokens / 1e6) * spec.usd_in + (completion_tokens / 1e6) * spec.usd_out, 6)


def pick(prefer_local: bool = True) -> str | None:
    """The cheapest provider that is actually reachable right now."""
    statuses = {s.name: s for s in probe_all(timeout=4.0)}
    order = (["lmstudio", "ollama", "deepseek", "nvidia", "openrouter"] if prefer_local
             else ["deepseek", "nvidia", "openrouter", "lmstudio", "ollama"])
    for name in order:
        status = statuses.get(name)
        if status and status.configured and status.reachable:
            return name
    return None


# ------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="providers", description="Alfred model providers: list, probe, set keys.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="show every provider and whether it is usable")
    p_ask = sub.add_parser("ask", help="one chat completion")
    p_ask.add_argument("prompt")
    p_ask.add_argument("--provider", default=None)
    p_ask.add_argument("--model", default=None)
    p_key = sub.add_parser("set-key", help="store an API key in secrets/models.json")
    p_key.add_argument("env_key", help="e.g. DEEPSEEK_API_KEY or NVIDIA_API_KEY")
    p_key.add_argument("--stdin", action="store_true",
                       help="read the key from stdin instead of prompting")
    sub.add_parser("pick", help="print the cheapest reachable provider")

    args = parser.parse_args(argv)

    if args.command == "list":
        for status in probe_all():
            spec = PROVIDERS[status.name]
            state = ("reachable" if status.reachable else
                     ("unreachable" if status.configured else "not configured"))
            print(f"{status.name:<12} {status.tier:<9} {state:<16} key={status.key}"
                  f"{'  ' + status.detail if status.detail else ''}")
            if status.models:
                print(f"             models: {', '.join(status.models[:4])}")
            print(f"             {spec.notes}")
        return 0

    if args.command == "pick":
        chosen = pick()
        print(chosen or "(nothing reachable)")
        return 0 if chosen else 1

    if args.command == "set-key":
        # Never echo the value, and never accept it as an argv element where it
        # would land in shell history and the process list.
        if args.stdin:
            value = sys.stdin.read().strip()
        else:
            import getpass

            value = getpass.getpass(f"{args.env_key} (input hidden): ").strip()
        try:
            path = set_key(args.env_key, value)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(f"stored {args.env_key} in {path} (value not echoed)")
        return 0

    if args.command == "ask":
        chosen = args.provider or pick() or "lmstudio"
        result = chat(args.prompt, provider=chosen, model=args.model)
        if not result["ok"]:
            print(f"failed via {chosen}: {result['error']}", file=sys.stderr)
            return 1
        print(result["text"].strip())
        usage = result.get("usage") or {}
        print(f"\n[{result['provider']} {result['model']} | "
              f"in {usage.get('prompt_tokens','?')} out {usage.get('completion_tokens','?')} | "
              f"est ${result.get('estimatedUsd')}]", file=sys.stderr)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
