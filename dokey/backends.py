"""Bring-your-own OCR serving: discover, persist, and resolve a local endpoint.

dokey ships no models. Every scanned-PDF feature (the --toc-from-page OCR
fallback, `folios --source ocr`) talks to an OpenAI-compatible chat endpoint
that the user already runs on their own machine or LAN -- LM Studio, a
llama.cpp ``llama-server``, Ollama, vLLM. dokey stays a thin, dependency-light
layer over that serving, the way an editor sits over a language server: the
model runtime is the user's reusable infrastructure, not part of this package.

This module is the seam. It normalizes endpoint spellings, probes a server's
``/v1/models`` to see what it is actually serving, scans well-known local ports
so the UI/CLI can offer what it finds, and persists the chosen endpoint in a
small user config. The effective endpoint is resolved in a fixed order:

  1. an explicit ``--ocr-endpoint`` / ``--endpoint`` flag
  2. the saved choice (``dokey backend --set``) in ``~/.dokey/config.json``
  3. the built-in default (``http://127.0.0.1:8731/v1/chat/completions``)
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .ocr import DEFAULT_ENDPOINT

# Ports that commonly serve an OpenAI-compatible API on a workstation:
# 8731 dokey's llama-server convention, 1234 LM Studio, 8089/8090 companion
# pipeline conventions, 8080 llama.cpp default, 11434 Ollama.
WELL_KNOWN_PORTS = (8731, 1234, 8089, 8090, 8080, 11434)

_CHAT_SUFFIX = "/v1/chat/completions"


@dataclass(frozen=True)
class Backend:
    endpoint: str  # normalized chat-completions URL
    models: tuple[str, ...]


def chat_endpoint(url: str) -> str:
    """Normalize a bare host:port, base URL, or full URL to a chat endpoint."""
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = "http://" + url
    if url.endswith(_CHAT_SUFFIX):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    return url + _CHAT_SUFFIX


def models_url(url: str) -> str:
    base = chat_endpoint(url).split("/v1/", 1)[0]
    return base + "/v1/models"


def _fetch_json(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def probe(url: str, timeout: float = 1.0, fetch=_fetch_json) -> Backend | None:
    """Ask ``/v1/models``; a reachable OpenAI-compatible server answers with its
    model list. Returns None when unreachable or non-conforming."""
    endpoint = chat_endpoint(url)
    try:
        data = fetch(models_url(endpoint), timeout)
    except Exception:
        return None
    models: tuple[str, ...] = ()
    if isinstance(data, dict):
        rows = data.get("data")
        if not isinstance(rows, list):  # some servers answer {"data": null}
            rows = []
        models = tuple(
            str(item.get("id"))
            for item in rows
            if isinstance(item, dict) and item.get("id")
        )
    return Backend(endpoint=endpoint, models=models)


def discover(
    host: str = "127.0.0.1",
    ports: tuple[int, ...] = WELL_KNOWN_PORTS,
    timeout: float = 0.8,
    prober=probe,
) -> list[Backend]:
    """Probe well-known local ports and return every responding server."""
    found = []
    for port in ports:
        backend = prober(f"http://{host}:{port}", timeout)
        if backend is not None:
            found.append(backend)
    return found


def config_path() -> Path:
    root = os.environ.get("DOKEY_CONFIG_DIR")
    base = Path(root) if root else Path.home() / ".dokey"
    return base / "config.json"


def workspace_dir() -> Path:
    """Where a bare launch (double-clicked exe) keeps its lakes.

    A double-started dokey has no meaningful working directory (Windows hands
    it the Scripts folder), so discovered and newly ingested lakes need one
    stable, user-owned home. Overridable via the ``workspace`` config key."""
    value = load_config().get("workspace")
    if isinstance(value, str) and value.strip():
        return Path(value)
    return Path.home() / "dokey"


def load_config() -> dict:
    path = config_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_config(config: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def saved_endpoint() -> str | None:
    value = load_config().get("ocr_endpoint")
    if isinstance(value, str) and value.strip():
        return value
    return None


def set_saved_endpoint(url: str | None) -> Path:
    config = load_config()
    if url is None:
        config.pop("ocr_endpoint", None)
    else:
        config["ocr_endpoint"] = chat_endpoint(url)
    return save_config(config)


def resolve_endpoint(flag: str | None) -> tuple[str, str]:
    """Effective endpoint and its provenance: flag > saved config > default."""
    if flag:
        return chat_endpoint(flag), "flag"
    saved = saved_endpoint()
    if saved:
        return saved, "config"
    return DEFAULT_ENDPOINT, "default"
