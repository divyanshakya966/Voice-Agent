from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import cfg_get


class OllamaError(RuntimeError):
    pass


def _url(config: dict, path: str) -> str:
    base = str(cfg_get(config, "llm.url", "http://127.0.0.1:11434")).rstrip("/")
    return base + path


def _request(config: dict, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(_url(config, path), data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout or float(cfg_get(config, "llm.timeout", 180))) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(detail).get("error", detail)
        except json.JSONDecodeError:
            message = detail
        raise OllamaError(f"Ollama HTTP {exc.code}: {message}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise OllamaError(f"Cannot reach Ollama at {_url(config, '')}: {exc}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Invalid response from Ollama: {body[:300]}") from exc
    if isinstance(parsed, dict) and parsed.get("error"):
        raise OllamaError(str(parsed["error"]))
    return parsed


def version(config: dict) -> str:
    parsed = _request(config, "/api/version", timeout=10)
    return str(parsed.get("version", "unknown"))


def loaded_models(config: dict) -> list[dict]:
    parsed = _request(config, "/api/ps", timeout=10)
    return list(parsed.get("models", []))


def chat(config: dict, messages: list[dict], tools: list[dict] | None = None, model: str | None = None) -> dict:
    model = model or str(cfg_get(config, "llm.model", "qwen3:1.7b"))
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": str(cfg_get(config, "llm.keep_alive", "10s")),
        "think": bool(cfg_get(config, "llm.thinking", False)),
        "options": {
            "temperature": float(cfg_get(config, "llm.temperature", 0.2)),
            "num_ctx": int(cfg_get(config, "llm.num_ctx", 4096)),
            "num_predict": int(cfg_get(config, "llm.num_predict", 600)),
        },
    }
    if tools:
        payload["tools"] = tools
    parsed = _request(config, "/api/chat", payload)
    message = parsed.get("message")
    if not isinstance(message, dict):
        raise OllamaError("Ollama returned no message")
    message["_meta"] = {
        key: parsed.get(key)
        for key in ("prompt_eval_count", "eval_count", "load_duration", "prompt_eval_duration", "eval_duration", "total_duration")
        if key in parsed
    }
    return message


def unload(config: dict, model: str | None = None) -> None:
    model = model or str(cfg_get(config, "llm.model", "qwen3:1.7b"))
    _request(
        config,
        "/api/generate",
        {
            "model": model,
            "prompt": "",
            "stream": False,
            "keep_alive": 0,
        },
        timeout=10,
    )


def tags(config: dict) -> list[dict]:
    parsed = _request(config, "/api/tags", timeout=10)
    return list(parsed.get("models", []))
