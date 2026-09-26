#!/usr/bin/env python3
"""Together AI integration and interactive CLI.

Features:
  1. Live model list via GET https://api.together.xyz/v1/models (cached for 1 hour).
  2. Verified model registry (curated fallback for text and image models).
  3. Interactive CLI menu (chat, one-shot prompt, image generation, settings, model switching).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Try importing requests for dependency-light HTTP; fallback to urllib
try:
    import requests  # type: ignore

    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False

TOGETHER_BASE_URL = "https://api.together.xyz/v1"
CONFIG_FILE = Path(".together_config.json")
CACHE_TTL_SECONDS = 3600  # 1 hour in-memory cache

# Curated Fallback Text/Chat Models (exact Together AI IDs)
FALLBACK_TEXT_MODELS = [
    "zai-org/GLM-5.3",
    "zai-org/GLM-5.3-Flash",
    "zai-org/GLM-5.2",
    "moonshotai/Kimi-K3",
    "MiniMaxAI/MiniMax-M3",
    "thinkingmachines/Inkling",
    "deepseek-ai/DeepSeek-V4.1-Flash",
    "deepseek-ai/DeepSeek-V4-Pro-0813",
    "deepseek-ai/DeepSeek-V4-Flash-0731",
    "deepseek-ai/DeepSeek-V4-Pro",
    "meta-models/Muse-Glimmer-30B",
    "Qwen/Qwen3.8-2.4T-A95B",
    "Qwen/Qwen3.8-Flash",
    "Qwen/Qwen3.7-Plus",
    "google/gemma-4-31B-it",
    "nvidia/nemotron-3-ultra-550b-a55b",
]

# Curated Fallback Image Models (exact Together AI IDs)
FALLBACK_IMAGE_MODELS = [
    "black-forest-labs/FLUX.2-flex",
    "black-forest-labs/FLUX.2-pro",
    "black-forest-labs/FLUX.2-dev",
    "black-forest-labs/FLUX.1-pro",
    "black-forest-labs/FLUX-Kontext-pro",
    "google/nano-banana-pro",
    "google/nano-banana",
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "default_model": "deepseek-ai/DeepSeek-V4.1-Flash",
    "default_image_model": "black-forest-labs/FLUX.2-pro",
    "temperature": 0.7,
    "max_tokens": 1024,
}

# In-memory cache for live model list
_models_cache: list[str] = []
_models_cache_timestamp: float = 0.0

# Session indexed list of models for quick numeric selection
_session_indexed_models: list[str] = []


# --------------------------------------------------------------------------- Config & Keys
def get_api_key() -> str:
    """Retrieve TOGETHER_API_KEY from environment, config, secrets, or .env."""
    key = os.environ.get("TOGETHER_API_KEY", "").strip()
    if key:
        return key

    config = load_config()
    if config.get("api_key"):
        return str(config["api_key"]).strip()

    # Check secrets/models.json
    for secrets_path in [Path("secrets/models.json"), Path(__file__).resolve().parent / "secrets" / "models.json"]:
        if secrets_path.exists():
            try:
                data = json.loads(secrets_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("TOGETHER_API_KEY"):
                    return str(data["TOGETHER_API_KEY"]).strip()
            except Exception:
                pass

    # Check .env file
    for env_path in [Path(".env"), Path(__file__).resolve().parent / ".env"]:
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("TOGETHER_API_KEY="):
                        val = line.partition("=")[2].strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1]
                        if val:
                            return val
            except Exception:
                pass

    return ""


def load_config() -> dict[str, Any]:
    """Load settings from .together_config.json, with defaults."""
    cfg = dict(DEFAULT_SETTINGS)
    cfg_file = _resolve_config_path()
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass
    return cfg


def save_config(updates: dict[str, Any]) -> None:
    """Save updated settings to .together_config.json."""
    cfg = load_config()
    cfg.update(updates)
    cfg_file = _resolve_config_path()
    try:
        cfg_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"Warning: Failed to persist config to {cfg_file}: {exc}", file=sys.stderr)


def _resolve_config_path() -> Path:
    if CONFIG_FILE.exists():
        return CONFIG_FILE
    return Path(__file__).resolve().parent / ".together_config.json"


# --------------------------------------------------------------------------- Models & Caching
def list_models(force_refresh: bool = False) -> list[str]:
    """Fetch live model IDs from Together AI, cached in memory for 1 hour.
    
    Calls GET https://api.together.xyz/v1/models with Authorization Bearer header.
    Falls back to the curated fallback registry if unreachable or unauthenticated.
    """
    global _models_cache, _models_cache_timestamp
    now = time.time()

    if not force_refresh and _models_cache and (now - _models_cache_timestamp < CACHE_TTL_SECONDS):
        return list(_models_cache)

    key = get_api_key()
    url = f"{TOGETHER_BASE_URL}/models"
    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": "Together-CLI/1.0",
    }

    fetched_ids: list[str] = []

    if HAVE_REQUESTS:
        try:
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                fetched_ids = _extract_model_ids(data)
        except Exception:
            pass
    else:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                data = json.loads(response.read().decode("utf-8"))
                fetched_ids = _extract_model_ids(data)
        except Exception:
            pass

    if fetched_ids:
        _models_cache = fetched_ids
        _models_cache_timestamp = now
        return list(_models_cache)

    # If live call failed but we had a previous cache, return it
    if _models_cache:
        return list(_models_cache)

    # Curated fallback registry
    curated = list(FALLBACK_TEXT_MODELS) + list(FALLBACK_IMAGE_MODELS)
    return curated


def _extract_model_ids(data: Any) -> list[str]:
    """Extract model 'id' strings from varied API response structures."""
    ids: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
    elif isinstance(data, dict):
        items = data.get("data") or data.get("models") or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    ids.append(str(item["id"]))
    return ids


def group_models_by_family(model_ids: list[str]) -> dict[str, list[str]]:
    """Group model IDs by family/organization prefix."""
    grouped: dict[str, list[str]] = {}
    for mid in model_ids:
        if "/" in mid:
            family = mid.split("/")[0]
        else:
            family = "other"
        grouped.setdefault(family, []).append(mid)

    # Sort models within each family
    for f in grouped:
        grouped[f] = sorted(grouped[f], key=lambda s: s.lower())

    # Return dictionary sorted by family name
    return {k: grouped[k] for k in sorted(grouped.keys(), key=lambda s: s.lower())}


def _ensure_indexed_models() -> list[str]:
    """Ensure the session indexed model list is populated with consistent ordering."""
    global _session_indexed_models
    if not _session_indexed_models:
        models = list_models()
        grouped = group_models_by_family(models)
        indexed: list[str] = []
        for family in sorted(grouped.keys(), key=lambda s: s.lower()):
            for m in grouped[family]:
                indexed.append(m)
        _session_indexed_models = indexed
    return _session_indexed_models


# --------------------------------------------------------------------------- Chat Completion
def chat(
    messages: list[dict[str, str]] | str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Send a chat completion request to Together AI.
    
    Returns:
        dict: {"ok": bool, "text": str, "model": str, "usage": dict, "error": str | None}
    """
    key = get_api_key()
    if not key:
        return {
            "ok": False,
            "text": "",
            "model": model or "",
            "usage": {},
            "error": "TOGETHER_API_KEY is not set. Set it in your environment, .env, or via CLI Settings.",
        }

    cfg = load_config()
    chosen_model = model or cfg.get("default_model", DEFAULT_SETTINGS["default_model"])
    temp = temperature if temperature is not None else cfg.get("temperature", DEFAULT_SETTINGS["temperature"])
    m_tokens = max_tokens if max_tokens is not None else cfg.get("max_tokens", DEFAULT_SETTINGS["max_tokens"])

    msg_list = [{"role": "user", "content": messages}] if isinstance(messages, str) else list(messages)

    url = f"{TOGETHER_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "Together-CLI/1.0",
    }
    payload = {
        "model": chosen_model,
        "messages": msg_list,
        "temperature": temp,
        "max_tokens": m_tokens,
    }

    try:
        if HAVE_REQUESTS:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            status_code = resp.status_code
            try:
                body = resp.json()
            except Exception:
                body = {}
            raw_text = resp.text
        else:
            encoded_payload = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=encoded_payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    status_code = response.status
                    raw_text = response.read().decode("utf-8")
                    body = json.loads(raw_text)
            except urllib.error.HTTPError as http_err:
                status_code = http_err.code
                raw_text = http_err.read().decode("utf-8", errors="replace")
                try:
                    body = json.loads(raw_text)
                except Exception:
                    body = {}

        if status_code == 404 or "model not found" in raw_text.lower():
            # Silently skip / handle 404 gracefully
            return {
                "ok": False,
                "text": "",
                "model": chosen_model,
                "usage": {},
                "error": f"Model '{chosen_model}' was not found (404).",
                "not_found": True,
            }

        if status_code != 200:
            err_msg = (body.get("error") if isinstance(body, dict) else None) or raw_text[:200]
            if isinstance(err_msg, dict):
                err_msg = err_msg.get("message", str(err_msg))
            return {
                "ok": False,
                "text": "",
                "model": chosen_model,
                "usage": {},
                "error": f"HTTP {status_code}: {err_msg}",
            }

        choices = body.get("choices", [])
        if not choices:
            return {
                "ok": False,
                "text": "",
                "model": chosen_model,
                "usage": body.get("usage", {}),
                "error": "Empty choices in API response.",
            }

        reply_text = choices[0].get("message", {}).get("content", "")
        return {
            "ok": True,
            "text": reply_text,
            "model": body.get("model", chosen_model),
            "usage": body.get("usage", {}),
            "error": None,
        }

    except Exception as exc:
        return {
            "ok": False,
            "text": "",
            "model": chosen_model,
            "usage": {},
            "error": str(exc),
        }


# --------------------------------------------------------------------------- Image Generation
def generate_image(
    prompt: str,
    *,
    model: str | None = None,
    steps: int = 20,
    n: int = 1,
    height: int | None = None,
    width: int | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Generate image(s) using Together AI /v1/images/generations endpoint.
    
    Returns:
        dict: {"ok": bool, "url": str, "urls": list[str], "model": str, "error": str | None}
    """
    key = get_api_key()
    if not key:
        return {
            "ok": False,
            "url": "",
            "urls": [],
            "model": model or "",
            "error": "TOGETHER_API_KEY is not set. Set it in your environment, .env, or via CLI Settings.",
        }

    cfg = load_config()
    chosen_model = model or cfg.get("default_image_model", DEFAULT_SETTINGS["default_image_model"])

    url = f"{TOGETHER_BASE_URL}/images/generations"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "Together-CLI/1.0",
    }
    payload: dict[str, Any] = {
        "model": chosen_model,
        "prompt": prompt,
        "n": n,
        "steps": steps,
    }
    if height is not None:
        payload["height"] = height
    if width is not None:
        payload["width"] = width

    try:
        if HAVE_REQUESTS:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            status_code = resp.status_code
            try:
                body = resp.json()
            except Exception:
                body = {}
            raw_text = resp.text
        else:
            encoded_payload = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=encoded_payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    status_code = response.status
                    raw_text = response.read().decode("utf-8")
                    body = json.loads(raw_text)
            except urllib.error.HTTPError as http_err:
                status_code = http_err.code
                raw_text = http_err.read().decode("utf-8", errors="replace")
                try:
                    body = json.loads(raw_text)
                except Exception:
                    body = {}

        if status_code == 404 or "model not found" in raw_text.lower():
            return {
                "ok": False,
                "url": "",
                "urls": [],
                "model": chosen_model,
                "error": f"Image model '{chosen_model}' was not found (404).",
                "not_found": True,
            }

        if status_code != 200:
            err_msg = (body.get("error") if isinstance(body, dict) else None) or raw_text[:200]
            if isinstance(err_msg, dict):
                err_msg = err_msg.get("message", str(err_msg))
            return {
                "ok": False,
                "url": "",
                "urls": [],
                "model": chosen_model,
                "error": f"HTTP {status_code}: {err_msg}",
            }

        data_list = body.get("data", [])
        urls = [item.get("url") for item in data_list if isinstance(item, dict) and item.get("url")]
        primary_url = urls[0] if urls else ""

        # Log generated image to .together_images.jsonl
        try:
            log_entry = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "model": chosen_model,
                "prompt": prompt,
                "url": primary_url,
            }
            log_file = Path(".together_images.jsonl")
            with log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            pass

        return {
            "ok": True,
            "url": primary_url,
            "urls": urls,
            "model": chosen_model,
            "error": None,
        }

    except Exception as exc:
        return {
            "ok": False,
            "url": "",
            "urls": [],
            "model": chosen_model,
            "error": str(exc),
        }


# --------------------------------------------------------------------------- Interactive CLI
def _mask_key(key: str) -> str:
    if not key:
        return "Not Set"
    if len(key) <= 6:
        return "Set (***)"
    return f"Set (...{key[-4:]})"


def _print_header() -> None:
    print("\n" + "=" * 60)
    print("                 TOGETHER AI CLI")
    print("=" * 60)


def cli_list_models() -> list[str]:
    """Execute Option 1: List all models grouped by family with numbers."""
    global _session_indexed_models
    print("\nFetching models (authoritative live list / curated fallback)...")
    models = list_models()
    grouped = group_models_by_family(models)

    _session_indexed_models = []
    print("\n" + "-" * 60)
    print("AVAILABLE MODELS (GROUPED BY FAMILY)")
    print("-" * 60)

    for family in sorted(grouped.keys(), key=lambda s: s.lower()):
        print(f"\n[Family: {family}]")
        for m in grouped[family]:
            _session_indexed_models.append(m)
            idx = len(_session_indexed_models)
            print(f"  [{idx:3d}] {m}")

    print(f"\nTotal: {len(_session_indexed_models)} models listed.")
    print("-" * 60)
    return _session_indexed_models


def cli_switch_default_model(indexed_models: list[str] | None = None) -> None:
    """Execute Option 2: Switch default model by number or full ID."""
    models = indexed_models or _ensure_indexed_models()

    cfg = load_config()
    print(f"\nCurrent Default Model: {cfg.get('default_model')}")
    print("Enter a number from the model list or paste a full model ID (or 'back' to cancel):")
    choice = input("Select model: ").strip()

    if not choice or choice.lower() in ("back", "cancel", "q"):
        print("Model switch cancelled.")
        return

    new_model = ""
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(models):
            new_model = models[idx - 1]
        else:
            print(f"Invalid selection: number {idx} is out of range (1 - {len(models)}).")
            return
    else:
        new_model = choice

    save_config({"default_model": new_model})
    print(f"-> Default model updated to: {new_model} (saved to .together_config.json)")


def cli_chat() -> None:
    """Execute Option 3: Multi-turn chat loop keeping full history in memory."""
    models = _ensure_indexed_models()
    cfg = load_config()
    current_model = cfg.get("default_model", DEFAULT_SETTINGS["default_model"])
    history: list[dict[str, str]] = []

    print("\n" + "-" * 60)
    print(f"STARTING MULTI-TURN CHAT (Model: {current_model})")
    print("Commands:")
    print("  'clear'  - Reset conversation history")
    print("  'switch' - Change model mid-chat")
    print("  'quit'   - Return to main menu")
    print("-" * 60)

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting chat...")
            break

        if not user_input:
            continue

        if user_input.lower() == "quit":
            print("Returning to main menu...")
            break

        if user_input.lower() == "clear":
            history.clear()
            print("-> Conversation history cleared.")
            continue

        if user_input.lower() == "switch":
            print(f"Current chat model: {current_model}")
            new_id = input("Enter new model ID or number from list: ").strip()
            if new_id:
                if new_id.isdigit():
                    idx = int(new_id)
                    if 1 <= idx <= len(models):
                        current_model = models[idx - 1]
                        print(f"-> Switched chat model to: {current_model}")
                    else:
                        print("Invalid number; keeping previous model.")
                else:
                    current_model = new_id
                    print(f"-> Switched chat model to: {current_model}")
            continue

        # Add message and call API
        history.append({"role": "user", "content": user_input})
        print(f"[{current_model} is thinking...]")
        res = chat(history, model=current_model)

        if res["ok"]:
            reply = res["text"].strip()
            print(f"\nAssistant:\n{reply}")
            history.append({"role": "assistant", "content": reply})
            usage = res.get("usage", {})
            if usage:
                p_tok = usage.get("prompt_tokens", "?")
                c_tok = usage.get("completion_tokens", "?")
                print(f"\n(tokens: in={p_tok}, out={c_tok})")
        else:
            err = res.get("error", "Unknown error")
            print(f"\n[Error: {err}]")
            if res.get("not_found"):
                print("Tip: Use 'switch' to pick an available model ID.")
            # Remove failed turn from history
            history.pop()


def cli_one_shot() -> None:
    """Execute Option 4: One-shot prompt (single message, no history)."""
    cfg = load_config()
    current_model = cfg.get("default_model", DEFAULT_SETTINGS["default_model"])
    print(f"\n[One-shot prompt using: {current_model}]")

    try:
        prompt = input("Enter prompt: ").strip()
    except (KeyboardInterrupt, EOFError):
        return

    if not prompt:
        print("Empty prompt; returning to menu.")
        return

    print(f"[{current_model} is responding...]")
    res = chat(prompt, model=current_model)

    if res["ok"]:
        print(f"\nAssistant:\n{res['text'].strip()}")
    else:
        print(f"\n[Error: {res.get('error', 'Unknown error')}]")


def cli_generate_image() -> None:
    """Execute Option 5: Image generation via /v1/images/generations."""
    cfg = load_config()
    default_img_model = cfg.get("default_image_model", DEFAULT_SETTINGS["default_image_model"])

    print("\n" + "-" * 60)
    print("IMAGE GENERATION (/v1/images/generations)")
    print("-" * 60)
    print("Available Image Models:")
    for i, m in enumerate(FALLBACK_IMAGE_MODELS, 1):
        marker = " (current default)" if m == default_img_model else ""
        print(f"  [{i}] {m}{marker}")

    sel = input(f"\nPick model [1-{len(FALLBACK_IMAGE_MODELS)}] or press Enter to keep default: ").strip()
    chosen_model = default_img_model
    if sel.isdigit():
        idx = int(sel)
        if 1 <= idx <= len(FALLBACK_IMAGE_MODELS):
            chosen_model = FALLBACK_IMAGE_MODELS[idx - 1]
    elif sel:
        chosen_model = sel

    prompt = input("Enter image description/prompt: ").strip()
    if not prompt:
        print("Empty prompt; returning to menu.")
        return

    print(f"\nGenerating image with '{chosen_model}'...")
    res = generate_image(prompt, model=chosen_model)

    if res["ok"]:
        url = res["url"]
        print("\nSUCCESS! Image generated:")
        print(f"  URL: {url}")
        print("  (URL saved to .together_images.jsonl)")

        # Offer to save image
        save_opt = input("\nSave image locally? (Enter filename or press Enter to skip): ").strip()
        if save_opt:
            save_path = Path(save_opt)
            if not save_path.suffix:
                save_path = save_path.with_suffix(".png")
            try:
                print(f"Downloading to {save_path}...")
                if HAVE_REQUESTS:
                    img_data = requests.get(url, timeout=30).content
                else:
                    with urllib.request.urlopen(url, timeout=30) as r:
                        img_data = r.read()
                save_path.write_bytes(img_data)
                print(f"Saved image to: {save_path.resolve()}")
            except Exception as e:
                print(f"Could not download image: {e}")
    else:
        print(f"\n[Image Generation Failed: {res.get('error', 'Unknown error')}]")


def cli_show_settings() -> None:
    """Execute Option 6: Show current model + settings and allow edits."""
    cfg = load_config()
    key = get_api_key()

    print("\n" + "-" * 60)
    print("CURRENT CONFIGURATION & SETTINGS")
    print("-" * 60)
    print(f"  Default Chat Model:   {cfg.get('default_model')}")
    print(f"  Default Image Model:  {cfg.get('default_image_model')}")
    print(f"  Temperature:          {cfg.get('temperature')}")
    print(f"  Max Tokens:           {cfg.get('max_tokens')}")
    print(f"  TOGETHER_API_KEY:     {_mask_key(key)}")
    print(f"  Configuration File:   {_resolve_config_path()}")
    print("-" * 60)
    print("Options:")
    print("  [1] Change temperature")
    print("  [2] Change max_tokens")
    print("  [3] Set TOGETHER_API_KEY in config")
    print("  [Enter] Back to main menu")

    sub = input("Select setting to modify: ").strip()
    if sub == "1":
        val = input(f"Enter new temperature (current: {cfg.get('temperature')}): ").strip()
        try:
            t = float(val)
            save_config({"temperature": t})
            print(f"Temperature updated to {t}")
        except ValueError:
            print("Invalid float value.")
    elif sub == "2":
        val = input(f"Enter new max_tokens (current: {cfg.get('max_tokens')}): ").strip()
        try:
            m = int(val)
            save_config({"max_tokens": m})
            print(f"max_tokens updated to {m}")
        except ValueError:
            print("Invalid integer value.")
    elif sub == "3":
        val = input("Enter TOGETHER_API_KEY: ").strip()
        if val:
            save_config({"api_key": val})
            print("API key saved to .together_config.json")


def main() -> int:
    """Interactive CLI menu loop."""
    while True:
        cfg = load_config()
        current_model = cfg.get("default_model", DEFAULT_SETTINGS["default_model"])

        _print_header()
        print(f"Current Model: {current_model}")
        print("-" * 60)
        print("  [1] List all models (from live API, grouped by family)")
        print("  [2] Switch default model")
        print("  [3] Chat (multi-turn, keeps history)")
        print("  [4] One-shot prompt (single message, no history)")
        print("  [5] Generate image (uses /v1/images/generations)")
        print("  [6] Show current model + settings")
        print("  [7] Quit")
        print("=" * 60)

        try:
            choice = input("Enter choice [1-7] (or 'quit'): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting Together AI CLI. Goodbye!")
            return 0

        if choice in ("7", "quit", "q", "exit"):
            print("Goodbye!")
            return 0

        if choice == "1":
            models = cli_list_models()
            sub = input("\nType a model number to switch default, or press Enter to return: ").strip()
            if sub.isdigit():
                idx = int(sub)
                if 1 <= idx <= len(models):
                    new_m = models[idx - 1]
                    save_config({"default_model": new_m})
                    print(f"-> Default model updated to: {new_m}")
        elif choice == "2":
            cli_switch_default_model()
        elif choice == "3":
            cli_chat()
        elif choice == "4":
            cli_one_shot()
        elif choice == "5":
            cli_generate_image()
        elif choice == "6":
            cli_show_settings()
        else:
            print(f"Unknown option '{choice}'. Please choose 1-7 or 'quit'.")


if __name__ == "__main__":
    sys.exit(main())
