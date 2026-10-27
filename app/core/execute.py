"""The request pipeline (data plane).

Order (brief §5.1): auth -> limits -> concurrency -> cache -> route -> fallback
-> cost/price -> log. Both `/v1/chat/completions` and the Playground call
`execute_chat`, so what you test in the console is what your apps get.

Connection discipline (LOAD_FIX): no database connection is held across a
provider HTTP call. The pipeline is

    1. short session  -> limits, spend caps, cache lookup, candidate routing
       (the connection returns to the pool here)
    2. provider calls -> no connection checked out anywhere
    3. short session  -> cache write, then the request log in a background task

Auth runs in `app.api.deps` on its own short session before this function is
called. ``key`` and ``partner`` are detached snapshots from
app.core.config_cache, so nothing here needs a live session to identify the
caller.

Provider health bookkeeping also happens off the hot path: it is queued with the
log row and written by the same background task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.config import settings
from app.core import cache as response_cache
from app.core import config_cache, health, limits, metrics, settings_store
from app.core.cost import compute_cost, compute_price
from app.core.crypto import decrypt
from app.core.router import Candidate, NoCandidate, extract_features, pick_candidates
from app.db import SessionLocal
from app.models import GatewayKey, Provider, RequestLog
from app.providers import openai_compat
from app.providers.base import (
    BadRequest,
    EmptyResponse,
    ProviderAuthError,
    ProviderError,
    ProviderCall,
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


@dataclass(slots=True)
class ProviderHealth:
    """Queued health bookkeeping, applied by the background writer."""

    provider_id: int
    ok: bool
    error: str | None = None
    auth_error: bool = False
    rate_limited: bool = False
    latency_ms: int | None = None
    health: str = "healthy"


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
    key: GatewayKey | Any | None,
    partner: Any | None,
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


async def _write_log(
    row: RequestLog,
    key_id: int | None,
    health_updates: list[ProviderHealth],
    request_id: str,
) -> None:
    """Queue the post-flight work for the batch writer.

    LOAD_FIX requirement 3: request-log writes must not be able to starve the
    pool. One task per request is not enough on SQLite, where a single writer
    lock makes N tasks hold N connections while they block behind each other.
    Instead every request appends to an in-process queue and one writer task
    drains it in batches, so concurrent logging costs at most one connection.
    """
    enqueue(row, key_id, health_updates, request_id)


LOG_BATCH_SIZE = 100

# An asyncio.Queue binds to the loop that first uses it, and this process can
# run more than one loop (the test server thread, asyncio.run, the CLI). Both
# the queue and its writer are therefore kept per loop, keyed by the loop
# object itself: keying by id() alone is unsafe because CPython reuses ids once
# a loop is collected, and a new loop would inherit a queue bound to a dead one.
#
# A WeakKeyDictionary is NOT enough here: the queue and the writer task each
# hold a strong reference back to their loop, so the key can never die and the
# entry would leak one slot per loop. Entries are therefore held explicitly,
# guarded by a weak reference (so a reused id is detected), and pruned as soon
# as their loop is closed. One entry per live loop, bounded.
_loop_state: dict[int, list] = {}


def _prune_closed_loops() -> None:
    for key, (ref, _queue_obj, _task) in list(_loop_state.items()):
        loop = ref()
        if loop is None or loop.is_closed():
            _loop_state.pop(key, None)


def _state() -> list:
    """[weakref, queue, writer_task] for the running loop, created on demand."""
    loop = asyncio.get_running_loop()
    key = id(loop)

    entry = _loop_state.get(key)
    if entry is not None:
        if entry[0]() is loop:
            return entry
        # The id was reused by a different loop: the old entry is stale.
        _loop_state.pop(key, None)

    _prune_closed_loops()
    entry = [weakref.ref(loop), asyncio.Queue(), None]
    _loop_state[key] = entry
    return entry


def _queue() -> asyncio.Queue:
    return _state()[1]


def _ensure_writer() -> None:
    entry = _state()
    task = entry[2]
    if task is None or task.done():
        entry[2] = asyncio.get_running_loop().create_task(_log_writer(entry[1]))


def enqueue(
    row: RequestLog,
    key_id: int | None,
    health_updates: list[ProviderHealth],
    request_id: str = "",
) -> None:
    """Hand one finished request to the batch writer. Never blocks, never I/O."""
    if not settings.log_writer_enabled:  # pragma: no cover - diagnostics only
        return
    _queue().put_nowait((row, key_id, health_updates))
    _ensure_writer()


async def _log_writer(queue: asyncio.Queue) -> None:
    """Drain the log queue in batches, on one short session per batch."""
    while True:
        try:
            first = await queue.get()
        except asyncio.CancelledError:  # pragma: no cover - shutdown
            return
        except Exception:  # pragma: no cover - defensive
            log.exception("Log queue reader failed; retrying")
            await asyncio.sleep(1)
            continue

        batch = [first]
        # Take whatever else is already queued, up to the batch limit.
        while len(batch) < LOG_BATCH_SIZE:
            try:
                batch.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        try:
            await _flush(batch)
        except Exception:  # pragma: no cover - logging must never break a response
            log.exception(
                "Failed to write request log batch of %d", len(batch),
                extra={"request_id": batch[0][0].request_id},
            )
        finally:
            for _ in batch:
                queue.task_done()


async def _flush(batch: list[tuple[RequestLog, int | None, list[ProviderHealth]]]) -> None:
    """Write a batch of log rows and de-duplicated health updates.

    A single bad row (for example one whose key was deleted mid-flight) would
    otherwise take the whole batch down, so a failed batch is retried row by
    row before anything is dropped.
    """
    try:
        await _flush_rows(batch)
        return
    except Exception:
        if len(batch) == 1:
            raise
        log.warning(
            "Batch of %d log rows failed; retrying individually", len(batch),
            exc_info=True,
        )

    for item in batch:
        try:
            await _flush_rows([item])
        except Exception:  # pragma: no cover - one poison row
            log.exception(
                "Dropping one request log row",
                extra={"request_id": item[0].request_id},
            )


async def _flush_rows(
    batch: list[tuple[RequestLog, int | None, list[ProviderHealth]]]
) -> None:
    rows = [row for row, _, _ in batch]
    key_ids = {key_id for _, key_id, _ in batch if key_id is not None}

    # Collapse health updates: only the last word per provider matters.
    health: dict[int, ProviderHealth] = {}
    for _, _, updates in batch:
        for update in updates:
            health[update.provider_id] = update

    async with SessionLocal() as session:
        session.add_all(rows)
        for key_id in key_ids:
            key = await session.get(GatewayKey, key_id)
            if key is not None:
                key.last_used_at = utcnow()
        for provider_id, update in health.items():
            provider = await session.get(Provider, provider_id)
            if provider is None:
                continue
            provider.last_checked_at = utcnow()
            provider.health_status = update.health
            provider.last_error = update.error[:500] if update.error else None
            if update.ok and update.latency_ms is not None:
                provider.last_latency_ms = update.latency_ms
        await session.commit()


async def drain_log_queue(timeout: float = 10.0) -> None:
    """Wait for queued log rows to be written (shutdown and tests)."""
    try:
        await asyncio.wait_for(_queue().join(), timeout=timeout)
    except (asyncio.TimeoutError, RuntimeError):  # pragma: no cover - shutdown only
        log.warning("Log queue did not drain within %.0fs", timeout)


# ---------------------------------------------------------------- execute
async def execute_chat(
    *,
    key,
    partner,
    body: dict[str, Any],
    client: httpx.AsyncClient,
    source: str = "api",
    end_user: str | None = None,
) -> ChatResult:
    """Run one chat request.

    ``key`` and ``partner`` are snapshots from app.core.config_cache, not ORM
    rows, so this function needs no live session or request state at all. Every
    database touch below opens its own short session and releases it before any
    network call.
    """
    request_id = new_request_id()
    started = time.perf_counter()
    requested_model = str(body.get("model") or "auto")

    health_updates: list[ProviderHealth] = []

    def finish_log(**kwargs) -> None:
        """Hand the log row to the batch writer, after the response is sent.

        Enqueueing is non-blocking and opens no connection, so a logging
        backlog can never consume the pool.
        """
        row = _build_log_row(
            request_id=request_id,
            key=key,
            partner=partner,
            end_user=end_user,
            requested_model=requested_model,
            body=body,
            **kwargs,
        )
        enqueue(row, key.id if key else None, list(health_updates), request_id)

    # ---- 0. degraded mode restrictions (MAINTENANCE_SPEC §4.2) --------
    from app.core import maintenance as maint

    snapshot = await maint.get_state()
    degraded = snapshot.degraded
    if degraded.active and degraded.max_tokens:
        requested = body.get("max_tokens")
        if requested and int(requested) > degraded.max_tokens:
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
                error="degraded_mode: max_tokens exceeded",
                source=source,
            )
            raise ChatError(
                429,
                "degraded_mode",
                f"The service is running with reduced limits: max_tokens is capped at "
                f"{degraded.max_tokens}. Please retry with a smaller value.",
                request_id=request_id,
                retry_after=60,
            )

    try:
        # ---- 1-3. limits: rate, daily, spend, concurrency -----------------
        # One short session covers the spend aggregates; it is released before
        # anything is sent upstream.
        try:
            async with SessionLocal() as session:
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

        try:
            slot = limits.concurrency_slot(
                partner, key, reduce_pct=degraded.reduce_concurrency_pct if degraded.active else 0
            )
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

        maint.track_start()
        try:
            return await _run(
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
                health_updates=health_updates,
            )
        finally:
            maint.track_end()
            await slot.__aexit__(None, None, None)
    except ChatError:
        raise
    except Exception as exc:
        if isinstance(exc, SQLAlchemyTimeoutError):
            # A pool checkout timeout is a capacity signal, not a bug: fail
            # fast with a retryable status instead of a generic 500, and let the
            # error handler log the pool state (LOAD_FIX requirement 5).
            metrics.refresh_pool_gauges()
            log.error(
                "DB pool exhausted during a chat request [%s]; pool=%s",
                request_id,
                _pool_snapshot(),
            )
            raise ChatError(
                503,
                "database_busy",
                "The gateway is busy. Please retry shortly.",
                request_id=request_id,
                retry_after=1,
            ) from exc
        log.exception("Unexpected failure in the chat pipeline", extra={"request_id": request_id})
        raise ChatError(500, "internal_error", "Unexpected server error.", request_id=request_id)


def _pool_snapshot() -> dict:
    try:
        from app.db import pool_stats

        return pool_stats()
    except Exception:  # pragma: no cover
        return {}


async def _run(
    *,
    key,
    partner,
    body: dict[str, Any],
    client: httpx.AsyncClient,
    request_id: str,
    requested_model: str,
    started: float,
    source: str,
    end_user: str | None,
    finish_log,
    health_updates: list[ProviderHealth],
) -> ChatResult:
    # ---- 4-6. pre-flight ------------------------------------------------
    # Cache lookup, the spend aggregates behind the caps, and the spend figures
    # routing needs are all "is this request allowed to run" questions. Each
    # opens a short session and releases it immediately; none is held while a
    # request waits on a lock or a network call (LOAD_FIX requirement 1/2).
    estimated_tokens = estimate_tokens(json.dumps(body.get("messages") or []))
    return await _preflight_and_run(
        key=key,
        partner=partner,
        body=body,
        client=client,
        request_id=request_id,
        requested_model=requested_model,
        started=started,
        source=source,
        finish_log=finish_log,
        health_updates=health_updates,
        estimated_tokens=estimated_tokens,
    )


async def _preflight_and_run(
    *,
    key,
    partner,
    body: dict[str, Any],
    client: httpx.AsyncClient,
    request_id: str,
    requested_model: str,
    started: float,
    source: str,
    finish_log,
    health_updates: list[ProviderHealth],
    estimated_tokens: int,
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
        async with SessionLocal() as session:
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
    # Routing opens and closes its own short sessions. Nothing is held across
    # the provider loop below (LOAD_FIX requirement 1).
    features = extract_features(body, body.get("preset"))
    try:
        candidates = await pick_candidates(
            requested_model, key, features, partner=partner
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
            metrics.record_provider_failure(candidate.provider.name)
            health_updates.append(
                ProviderHealth(
                    provider_id=candidate.provider.id,
                    ok=False,
                    error=f"{exc.code}: {exc.message}",
                    auth_error=isinstance(exc, ProviderAuthError),
                    rate_limited=exc.status == 429,
                    health=_failure_health(exc),
                )
            )

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

        health_updates.append(
            ProviderHealth(
                provider_id=candidate.provider.id,
                ok=True,
                latency_ms=result.latency_ms,
                health="healthy",
            )
        )

        # The cache entry is observable to the next request, so it is written now
        # rather than in the background. This opens its own short session: the
        # pre-flight one was released before the provider call. Best-effort: a
        # cache failure must never turn a successful answer into an error.
        if cache_key:
            # Attribution travels with the entry so a cache hit still logs the origin.
            try:
                async with SessionLocal() as cache_session:
                    await response_cache.put(
                        cache_session,
                        cache_key,
                        {
                            **response_body,
                            "_provider_id": candidate.provider.id,
                            "_model_id": candidate.model.id,
                        },
                    )
            except Exception:  # pragma: no cover - caching is optional
                log.warning(
                    "Cache write failed; serving the response uncached",
                    extra={"request_id": request_id},
                    exc_info=True,
                )

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


def _failure_health(exc: ProviderError) -> str:
    if isinstance(exc, ProviderAuthError):
        return "misconfigured"
    if exc.status == 429:
        return "rate_limited"
    return "degraded"