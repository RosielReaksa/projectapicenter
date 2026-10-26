"""One adapter for every provider that speaks the OpenAI chat format.

Gemini, Groq, OpenRouter, DeepSeek, Mistral, Cerebras and Together all expose an
OpenAI-compatible endpoint, so adding a provider is a registry entry, not new code.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.providers.base import (
    BadRequest,
    EmptyResponse,
    ProviderAuthError,
    ProviderCall,
    ProviderError,
    ProviderResult,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from app.utils import estimate_tokens


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # HTTP-date form: fall back to the breaker default


def _error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        return (response.text or "").strip()[:300]

    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or "")[:300]
        if isinstance(err, str):
            return err[:300]
        if data.get("message"):
            return str(data["message"])[:300]
    return (response.text or "").strip()[:300]


def _estimate_from_payload(payload: dict[str, Any]) -> int:
    total = 0
    for message in payload.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += estimate_tokens(str(part.get("text", "")))
    return total


def _completion_text(body: dict[str, Any]) -> str:
    choices = body.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # some providers return content parts
        return "".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict)
        )
    return ""


async def chat(
    client: httpx.AsyncClient,
    call: ProviderCall,
    *,
    extra_body: dict[str, Any] | None = None,
) -> ProviderResult:
    url = call.base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {call.api_key}",
        "Content-Type": "application/json",
        **call.extra_headers,
    }
    body = dict(call.payload)
    if extra_body:
        body.update(extra_body)

    started = time.perf_counter()
    try:
        response = await client.post(url, json=body, headers=headers, timeout=call.timeout)
    except httpx.TimeoutException as exc:
        raise ProviderTimeout(f"Provider timed out after {call.timeout:.0f}s.") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(f"Could not reach provider: {exc.__class__.__name__}.") from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    status = response.status_code

    if status == 429:
        raise RateLimited(_error_message(response), status, _retry_after(response))
    if status in (401, 403):
        raise ProviderAuthError(_error_message(response), status)
    if status == 408 or status == 504:
        raise ProviderTimeout(_error_message(response), status)
    if status >= 500:
        raise ProviderUnavailable(_error_message(response), status, _retry_after(response))
    if status >= 400:
        raise BadRequest(_error_message(response), status)
    if status != 200:
        raise ProviderError(f"Unexpected status {status}.", status)

    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise EmptyResponse("Provider returned a non-JSON body.", status) from exc

    if not isinstance(data, dict) or not data.get("choices"):
        raise EmptyResponse("Provider returned no choices.", status)
    # An empty assistant message is a valid answer (e.g. a refusal or a
    # tool-only reply); only a missing/blank structure counts as a failure.
    if _completion_text(data) == "" and not data["choices"][0].get("message"):
        raise EmptyResponse("Provider returned a malformed message.", status)

    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    estimated = False
    if prompt_tokens == 0 and completion_tokens == 0:
        prompt_tokens = _estimate_from_payload(call.payload)
        completion_tokens = estimate_tokens(_completion_text(data))
        estimated = True

    return ProviderResult(
        body=data,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_usage=estimated,
    )


async def list_models(client: httpx.AsyncClient, base_url: str, api_key: str, timeout: float = 20.0) -> list[dict]:
    """Best-effort model listing; providers that lack the endpoint return []."""
    url = base_url.rstrip("/") + "/models"
    try:
        response = await client.get(
            url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )
        if response.status_code != 200:
            return []
        data = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return []
    items = data.get("data") if isinstance(data, dict) else data
    return [m for m in items or [] if isinstance(m, dict)]


async def test_connection(
    client: httpx.AsyncClient, base_url: str, api_key: str, model_name: str, timeout: float = 25.0
) -> dict:
    """One tiny real request - the only reliable check that a key works."""
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
        "temperature": 0,
    }
    started = time.perf_counter()
    try:
        result = await chat(
            client,
            ProviderCall(base_url=base_url, api_key=api_key, model_name=model_name, payload=payload, timeout=timeout),
        )
        return {
            "ok": True,
            "latency_ms": result.latency_ms,
            "model": model_name,
            "message": "Connection OK.",
        }
    except ProviderError as exc:
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "model": model_name,
            "error": exc.message,
            "code": exc.code,
            "status": exc.status,
        }
