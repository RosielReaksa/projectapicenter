"""The request pipeline (data plane).

Order (brief §5.1): auth -> limits -> concurrency -> cache -> route -> fallback
-> cost/price -> log. Both `/v1/chat/completions` and the Playground call
`execute_chat`, so what you test in the console is what your apps get.

Two things are deliberate:
* Logging happens in a background task, so it never adds latency to the reply.
* Provider keys are decrypted per call and never leave this module.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import cache as response_cache
from app.core import health, limits, metrics, settings_store
from app.core.cost import compute_cost, compute_price
from app.core.crypto import decrypt
from app.core.router import Candidate, NoCandidate, extract_features, pick_candidates
from app.models import GatewayKey, Partner, RequestLog
from app.providers import openai_compat
from app.providers.base import (
    BadRequest,
    EmptyResponse,
    ProviderAuthError,
    ProviderCall,
    ProviderError,
)
from app.utils import estimate_tokens, new_request_id, utcnow

log = logging.getLogger("gateway.chat")

# Gateway-owned fields; never forwarded upstream.
_GATEWAY_FIELDS = {"preset", "gateway", "gateway_key"}


class ChatError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        request_id: str = "",
        attempts: list[dict] | None = None,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.attempts = attempts or []
        self.retry_after = retry_after


@dataclass(slots=True)
class Attempt:
    provider: str
    model: str
    status: int
    error: str
    latency_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


@dataclass(slots=True)
class ChatResult:
    body: dict[str, Any]
    request_id: str
    provider: str
    model: str
    cached: bool
    cost_usd: float
    price_usd: float
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    attempts: list[Attempt] = field(default_factory=list)
    estimated_usage: bool = False


# --------------------------------------------------------------- helpers
def _upstream_body(body: dict[str, Any], model_name: str) -> dict[str, Any]:
    payload = {k: v for k, v in body.items() if k not in _GATEWAY_FIELDS}
    payload["model"] = model_name
    if payload.get("max_tokens"):
        payload["max_tokens"] = limits.clamp_max_tokens(int(payload["max_tokens"]))
    return payload


def _provider_headers(provider: Any) -> dict[str, str]:
    if provider.name == "openrouter":
        return {"HTTP-Referer": "http://localhost", "X-Title": settings.app_name}
    return {}


def _payload_for_log(
    body: dict[str, Any], response: dict[str, Any] | None, error: str | None
) -> str | None:
    """Honour the prompt-logging setting. Metadata mode stores shapes, never text."""
    mode = str(settings_store.get("log_prompts", settings.log_prompts))
    if mode == "off":
        return None
    if mode == "full":
        return json.dumps({"request": body, "response": response, "error": error})
    return json.dumps(
        {
            "message_count": len(body.get("messages") or []),
            "roles": [m.get("role") for m in (body.get("messages") or [])],
            "chars": sum(
                len(m["content"]) if isinstance(m.get("content"), str) else 0
                for m in (body.get("messages") or [])
            ),
            "error": error,
        }
    )


def _build_log_row(
    *,
    request_id: str,
    key: GatewayKey | None,
    partner: Partner | None,
    end_user: str | None,
    requested_model: str,
    provider_id: int | None,
    model_id: int | None,
    status: int,
    latency_ms: int,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    price: float,
    cached: bool,
    attempts: list[Attempt],
    body: dict[str, Any],
    response: dict[str, Any] | None,
    error: str | None,
    source: str,
) -> RequestLog:
    return RequestLog(
        request_id=request_id,
        gateway_key_id=key.id if key else None,
        partner_id=partner.id if partner else None,
        end_user=end_user,
        created_at=utcnow(),
        requested_model=requested_model,
        provider_id=provider_id,
        model_id=model_id,
        status_code=status,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost,
        price_usd=price,
        cached=cached,
        fallbacks_tried=json.dumps([a.as_dict() for a in attempts]) if attempts else None,
        error=error,
        payload_json=_payload_for_log(body, response, error),
        source=source,
    )


async def _commit_log(
    row: RequestLog, key_id: int | None, request_id: str
) -> None:
    """Commit the log row on a fresh session.

    Runs as a background task, so it never delays the response, and it never
    touches the request-scoped session (which FastAPI closes as soon as the
    response is sent).
    """
    from app.db import SessionLocal

    try:
        async with SessionLocal() as session:
            session.add(row)
            if key_id is not None:
                key = await session.get(GatewayKey, key_id)
                if key is not None:
                    key.last_used_at = row.created_at
            await session.commit()
    except Exception:  # pragma: no cover - logging must never break a response
        log.exception("Failed to write request log", extra={"request_id": request_id})


async def _note_provider(
    session: AsyncSession, candidate: Candidate, exc: ProviderError
) -> None:
    provider = candidate.provider
    provider.last_checked_at = utcnow()
    provider.last_error = f"{exc.code}: {exc.message}"[:500]
    if isinstance(exc, ProviderAuthError):
        provider.health_status = "misconfigured"
    elif exc.status == 429:
        provider.health_status = "rate_limited"
    else:
        provider.health_status = "degraded"
    try:
        await session.commit()
    except Exception:  # pragma: no cover
        await session.rollback()


# ---------------------------------------------------------------- execute
async def execute_chat(
    session: AsyncSession,
    *,
    key: GatewayKey,
    partner: Partner,
    body: dict[str, Any],
    client: httpx.AsyncClient,
    source: str = "api",
    end_user: str | None = None,
    background: Any = None,
) -> ChatResult:
    request_id = new_request_id()
    started = time.perf_counter()
    requested_model = str(body.get("model") or "auto")
    log_mode = str(settings_store.get("log_prompts", settings.log_prompts))

    def finish_log(**kwargs) -> None:
        """Queue the log row so it is written after the response is sent."""
        row = _build_log_row(
            request_id=request_id,
            key=key,
            partner=partner,
            end_user=end_user,
            requested_model=requested_model,
            body=body,
            **kwargs,
        )
        key_id = key.id if key else None
        if background is not None:
            background.add_task(_commit_log, row, key_id, request_id)
        else:
            # No background runner (direct calls from tests): schedule it.
            import asyncio

            asyncio.get_event_loop().create_task(_commit_log(row, key_id, request_id))

    try:
        # ---- 1-2. rate + daily + spend ---------------------------------
        try:
            await limits.check_rate_and_daily(session, partner, key)
        except limits.LimitExceeded as exc:
            metrics.record_request(partner.slug, "429", 0)
            finish_log(
                provider_id=None,
                model_id=None,
                status=429,
                latency_ms=int((time.perf_counter() - started) * 1000),
                prompt_tokens=0,
                completion_tokens=0,
                cost=0.0,
                price=0.0,
                cached=False,
                attempts=[],
                response=None,
                error=exc.message,
                source=source,
            )
            raise ChatError(
                429, exc.code, exc.message, request_id=request_id, retry_after=exc.retry_after
            ) from exc

        # ---- 3. concurrency --------------------------------------------
        try:
            slot = limits.concurrency_slot(partner, key)
            await slot.__aenter__()
        except limits.LimitExceeded as exc:
            metrics.record_request(partner.slug, "429", 0)
            finish_log(
                provider_id=None,
                model_id=None,
                status=429,
                latency_ms=int((time.perf_counter() - started) * 1000),
                prompt_tokens=0,
                completion_tokens=0,
                cost=0.0,
                price=0.0,
                cached=False,
                attempts=[],
                response=None,
                error=exc.message,
                source=source,
            )
            raise ChatError(
                429, exc.code, exc.message, request_id=request_id, retry_after=exc.retry_after
            ) from exc

        try:
            return await _run(
                session,
                key=key,
                partner=partner,
                body=body,
                client=client,
                request_id=request_id,
                requested_model=requested_model,
                started=started,
                source=source,
                end_user=end_user,
                finish_log=finish_log,
                log_mode=log_mode,
            )
        finally:
            await slot.__aexit__(None, None, None)
    except ChatError:
        raise
    except Exception:
        log.exception("Unexpected failure in the chat pipeline", extra={"request_id": request_id})
        raise ChatError(500, "internal_error", "Unexpected server error.", request_id=request_id)


async def _run(
    session: AsyncSession,
    *,
    key: GatewayKey,
    partner: Partner,
    body: dict[str, Any],
    client: httpx.AsyncClient,
    request_id: str,
    requested_model: str,
    started: float,
    source: str,
    end_user: str | None,
    finish_log,
    log_mode: str,
) -> ChatResult:
    # ---- 4. cache -------------------------------------------------------
    ttl = int(settings_store.get("cache_ttl_seconds", 3600) or 0)
    cache_enabled = bool(settings_store.get("cache_enabled", True)) and ttl > 0
    cache_key = None
    if cache_enabled and response_cache.is_cacheable(body, requested_model, ttl):
        cache_key = response_cache.cache_key(
            partner_id=partner.id,
            model=requested_model,
            messages=body.get("messages") or [],
            params={
                "temperature": body.get("temperature"),
                "max_tokens": body.get("max_tokens"),
                "top_p": body.get("top_p"),
                "stop": body.get("stop"),
                "preset": body.get("preset"),
            },
        )
        cached_body = await response_cache.get(session, cache_key, ttl)
        if cached_body:
            latency_ms = int((time.perf_counter() - started) * 1000)
            usage = cached_body.get("usage") or {}
            gateway = cached_body.get("gateway") or {}
            provider_id = cached_body.pop("_provider_id", None)
            model_id = cached_body.pop("_model_id", None)
            cached_body["gateway"] = {
                **gateway,
                "cached": True,
                "cost": 0.0,
                "price": 0.0,
                "latency_ms": latency_ms,
                "request_id": request_id,
            }
            finish_log(
                provider_id=provider_id,
                model_id=model_id,
                status=200,
                latency_ms=latency_ms,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                cost=0.0,
                price=0.0,
                cached=True,
                attempts=[],
                response=cached_body,
                error=None,
                source=source,
            )
            metrics.record_cache_hit()
            metrics.record_request(partner.slug, "200", latency_ms)
            return ChatResult(
                body=cached_body,
                request_id=request_id,
                provider=str(gateway.get("provider") or "cache"),
                model=str(gateway.get("model") or requested_model),
                cached=True,
                cost_usd=0.0,
                price_usd=0.0,
                latency_ms=latency_ms,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            )

    # ---- 5. tokens per minute -------------------------------------------
    estimated_tokens = estimate_tokens(json.dumps(body.get("messages") or []))
    try:
        await limits.check_tpm(partner, key, estimated_tokens)
    except limits.LimitExceeded as exc:
        metrics.record_request(partner.slug, "429", 0)
        finish_log(
            provider_id=None,
            model_id=None,
            status=429,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=0,
            completion_tokens=0,
            cost=0.0,
            price=0.0,
            cached=False,
            attempts=[],
            response=None,
            error=exc.message,
            source=source,
        )
        raise ChatError(
            429, exc.code, exc.message, request_id=request_id, retry_after=exc.retry_after
        ) from exc

    # ---- 6. candidates ---------------------------------------------------
    features = extract_features(body, body.get("preset"))
    try:
        candidates = await pick_candidates(
            session, requested_model, key, features, partner=partner
        )
    except NoCandidate as exc:
        metrics.record_request(partner.slug, "503", 0)
        finish_log(
            provider_id=None,
            model_id=None,
            status=503,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=0,
            completion_tokens=0,
            cost=0.0,
            price=0.0,
            cached=False,
            attempts=[],
            response=None,
            error=exc.message,
            source=source,
        )
        raise ChatError(503, "no_provider_available", exc.message, request_id=request_id) from exc

    # ---- 7. attempt each candidate --------------------------------------
    attempts: list[Attempt] = []
    deadline = time.perf_counter() + float(
        settings_store.get("request_timeout_seconds", settings.request_timeout_seconds)
    )
    client_error_seen = False

    for index, candidate in enumerate(candidates):
        remaining = deadline - time.perf_counter()
        if remaining <= 1:
            attempts.append(
                Attempt(candidate.provider.name, candidate.model.public_name, 0, "Deadline exceeded", 0)
            )
            break

        try:
            api_key = decrypt(candidate.provider.api_key_enc or "")
        except ValueError as exc:
            attempts.append(
                Attempt(candidate.provider.name, candidate.model.public_name, 0, str(exc), 0)
            )
            continue

        call = ProviderCall(
            base_url=candidate.provider.base_url,
            api_key=api_key,
            model_name=candidate.model.model_name,
            payload=_upstream_body(body, candidate.model.model_name),
            timeout=min(settings.provider_timeout_seconds, max(5.0, remaining)),
            extra_headers=_provider_headers(candidate.provider),
        )

        attempt_started = time.perf_counter()
        try:
            result = await openai_compat.chat(client, call)
        except ProviderError as exc:
            latency = int((time.perf_counter() - attempt_started) * 1000)
            attempts.append(
                Attempt(
                    candidate.provider.name,
                    candidate.model.public_name,
                    exc.status or 0,
                    exc.message,
                    latency,
                )
            )
            await health.record_failure(candidate.provider.id, exc.retry_after)
            await _note_provider(session, candidate, exc)
            metrics.record_provider_failure(candidate.provider.name)

            if isinstance(exc, BadRequest):
                # The request itself is likely wrong: retry once elsewhere, then stop.
                if client_error_seen or index + 1 >= len(candidates):
                    break
                client_error_seen = True
                continue
            if isinstance(exc, EmptyResponse):
                continue
            continue

        # ---- 8. success ---------------------------------------------
        await health.record_success(candidate.provider.id)
        latency_ms = int((time.perf_counter() - started) * 1000)
        cost = compute_cost(candidate.model, result.prompt_tokens, result.completion_tokens)
        price = compute_price(cost, partner)

        response_body = dict(result.body)
        if result.estimated_usage:
            response_body["usage"] = {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.prompt_tokens + result.completion_tokens,
                "estimated": True,
            }
        response_body["gateway"] = {
            "provider": candidate.provider.name,
            "model": candidate.model.public_name,
            "cached": False,
            "cost": cost,
            "price": price,
            "fallbacks_tried": [a.as_dict() for a in attempts],
            "latency_ms": latency_ms,
            "request_id": request_id,
        }

        if cache_key:
            # Attribution travels with the entry so a cache hit still logs the origin.
            await response_cache.put(
                session,
                cache_key,
                {
                    **response_body,
                    "_provider_id": candidate.provider.id,
                    "_model_id": candidate.model.id,
                },
            )

        candidate.provider.health_status = "healthy"
        candidate.provider.last_error = None
        candidate.provider.last_latency_ms = result.latency_ms
        candidate.provider.last_checked_at = utcnow()

        finish_log(
            provider_id=candidate.provider.id,
            model_id=candidate.model.id,
            status=200,
            latency_ms=latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost=cost,
            price=price,
            cached=False,
            attempts=attempts,
            response=response_body,
            error=None,
            source=source,
        )
        metrics.record_request(partner.slug, "200", latency_ms)
        metrics.record_tokens(candidate.provider.name, result.prompt_tokens, result.completion_tokens)

        return ChatResult(
            body=response_body,
            request_id=request_id,
            provider=candidate.provider.name,
            model=candidate.model.public_name,
            cached=False,
            cost_usd=cost,
            price_usd=price,
            latency_ms=latency_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            attempts=attempts,
            estimated_usage=result.estimated_usage,
        )

    # ---- 9. every candidate failed ---------------------------------------
    latency_ms = int((time.perf_counter() - started) * 1000)
    detail = "; ".join(f"{a.provider}: {a.error}" for a in attempts) or "no attempts made"
    all_client_errors = bool(attempts) and all(
        400 <= (a.status or 0) < 500 for a in attempts
    )
    status = 400 if client_error_seen and all_client_errors else 503
    code = "invalid_request" if status == 400 else "no_provider_available"

    metrics.record_request(partner.slug, str(status), latency_ms)
    finish_log(
        provider_id=None,
        model_id=None,
        status=status,
        latency_ms=latency_ms,
        prompt_tokens=0,
        completion_tokens=0,
        cost=0.0,
        price=0.0,
        cached=False,
        attempts=attempts,
        response=None,
        error=detail,
        source=source,
    )

    raise ChatError(
        status,
        code,
        f"All providers failed: {detail}",
        request_id=request_id,
        attempts=[a.as_dict() for a in attempts],
    )
