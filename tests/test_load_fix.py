"""Regressions for the connection-pool starvation fix (LOAD_FIX).

These are the tests the fix exists to pass. Each asserts a property of the data
plane under concurrency rather than an implementation detail:

* no database connection is held while a provider call is in progress
* a large concurrent burst completes without pool timeouts
* overload is shed immediately with Retry-After instead of queueing
* connections and counters return to zero afterwards

These drive the running gateway over async HTTP so the assertions see the real
pool and the real counters, and the concurrency is genuine rather than
serialised behind a synchronous client's GIL.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.db import engine

PROVIDER_LATENCY = 2.0
PARTNER_ID = 1


@pytest.fixture()
def load_partner(admin_client, provider_id):
    """A partner whose limits are wide enough that only overload can reject it."""
    partner = admin_client.post(
        "/admin/partners",
        json={
            "name": f"Load {time.time_ns()}",
            "concurrency_limit": 1000,
            "rpm_limit": 100000,
            "allow_free_tier": True,
            "monthly_cap_usd": 100.0,
        },
    ).json()
    key = admin_client.post(
        "/admin/keys", json={"name": "load", "partner_id": partner["id"]}
    ).json()["key"]
    return {"partner": partner, "key": key}


def checked_out() -> int:
    return int(engine.pool.checkedout())


async def chat(client, key: str, model: str = "slow2-model", **body):
    return await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": "load"}], **body},
    )


def run(coro):
    return asyncio.run(coro)


def make_client(gateway_url: str, concurrency: int) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=gateway_url,
        timeout=120.0,
        limits=httpx.Limits(
            max_connections=concurrency + 10, max_keepalive_connections=concurrency + 10
        ),
    )


# ---------------------------------------------------------- the core fix
def test_no_connection_held_during_provider_call(gateway_url, load_partner):
    """Connections checked out while requests are parked upstream stay near zero.

    Before the fix this was the failure mode: every in-flight request kept its
    request-scoped session checked out for the whole call, so 20 requests
    exhausted the pool and the next one waited 30s for a connection.

    The count is sampled only while the fake provider is actually holding the
    requests. Two properties matter, and both are asserted:

    * inside the provider call the pool is essentially empty
    * the peak does not grow with the number of pending requests

    Short pre-flight sessions do legitimately overlap at the start of a burst,
    so the test proves the peak is the same small constant at 25 and at 50
    concurrent requests rather than scaling with them.
    """
    from tests import fake_provider

    async def measure(pending: int):
        async with make_client(gateway_url, pending) as client:
            inside: list[int] = []
            stop = asyncio.Event()

            async def sample():
                while not stop.is_set():
                    # Only sample once every request is parked upstream: at that
                    # point no request is doing pre-flight work, so any
                    # connection still checked out is held *across* the call.
                    if fake_provider.ACTIVE >= pending:
                        inside.append(checked_out())
                    await asyncio.sleep(0.005)

            async def one():
                return (await chat(client, load_partner["key"])).status_code

            sampler = asyncio.create_task(sample())
            responses = await asyncio.gather(*(one() for _ in range(pending)))
            stop.set()
            await sampler
            return responses, inside

    small_responses, small_peak = run(measure(25))
    large_responses, large_peak = run(measure(50))

    assert small_responses.count(200) == 25, f"unexpected statuses: {set(small_responses)}"
    assert large_responses.count(200) == 50, f"unexpected statuses: {set(large_responses)}"
    assert large_peak, "the sampler never observed all requests parked at the provider"

    # With everything in flight upstream, nothing may be holding a connection.
    assert max(large_peak) <= 3, (
        f"{max(large_peak)} DB connections were checked out while all "
        f"{len(large_responses)} requests were inside the provider call"
    )
    assert max(small_peak) <= 3, f"{max(small_peak)} connections checked out at 25 concurrent"

    # And the figure does not grow with concurrency: the pool stays free at 50
    # just as it does at 25. Before the fix both were the whole pool (15).
    assert max(large_peak) <= max(small_peak) + 1, (
        f"peak grew with load: {max(small_peak)} at 25 concurrent, "
        f"{max(large_peak)} at 50"
    )


def median(values: list[int]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0


def test_burst_has_no_pool_timeouts(gateway_url, load_partner):
    """100 concurrent requests at 2s provider latency: 0 errors, no timeouts."""
    concurrency = 100

    async def scenario():
        async with make_client(gateway_url, concurrency) as client:
            started = time.perf_counter()
            responses = await asyncio.gather(
                *(chat(client, load_partner["key"]) for _ in range(concurrency))
            )
            return responses, time.perf_counter() - started

    responses, elapsed = run(scenario())
    statuses = [r.status_code for r in responses]

    assert statuses.count(200) == concurrency, (
        f"{concurrency - statuses.count(200)} of {concurrency} failed: "
        f"{sorted(set(statuses))}"
    )
    for response in responses:
        assert "QueuePool" not in response.text
    # No request may sit behind a pool checkout timeout (3s in this build).
    assert elapsed < 15, f"burst took {elapsed:.1f}s"


def test_overload_is_shed_immediately(gateway_url, fake_provider_url, load_partner, monkeypatch):
    """Past the in-flight cap, an extra request is refused within 100ms.

    Measured on a single request over an already-warm connection while the cap
    is full, so the number is the shed decision itself rather than the cost of
    opening a burst of sockets.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "max_in_flight", 5)
    calls_url = f"{fake_provider_url.rsplit('/v1', 1)[0]}/__calls"

    async def scenario():
        async with make_client(gateway_url, 10) as client:
            async with httpx.AsyncClient(timeout=30.0) as probe:
                await probe.get(calls_url)

                # Fill the cap with parked requests.
                parked = [
                    asyncio.create_task(chat(client, load_partner["key"])) for _ in range(5)
                ]
                for _ in range(600):
                    if (await probe.get(calls_url)).json()["active"] >= 5:
                        break
                    await asyncio.sleep(0.01)

                samples = []
                for _ in range(10):
                    began = time.perf_counter()
                    response = await chat(client, load_partner["key"])
                    samples.append((response, (time.perf_counter() - began) * 1000))

                parked_statuses = [
                    (await task).status_code for task in asyncio.as_completed(parked)
                ]
                return samples, parked_statuses

    samples, parked_statuses = run(scenario())

    for response, latency_ms in samples:
        assert response.status_code == 429, (
            f"expected a shed request while the cap was full, got {response.status_code}"
        )
        assert response.headers.get("retry-after"), "a shed request must carry Retry-After"
        assert response.json()["error"]["code"] == "gateway_overloaded"
        assert latency_ms < 100, f"a shed request took {latency_ms:.0f}ms (must be under 100ms)"

    # Shedding is a capacity decision, not a pipeline error: the admitted
    # requests still succeed.
    assert all(status == 200 for status in parked_statuses), (
        f"parked requests: {parked_statuses}"
    )


def test_connections_and_counters_return_to_zero(gateway_url, load_partner):
    """After the load nothing leaks: connections, slots and the in-flight count."""
    from app.core import overload
    from app.core.store import get_store

    concurrency = 40

    async def scenario():
        async with make_client(gateway_url, concurrency) as client:
            responses = await asyncio.gather(
                *(chat(client, load_partner["key"]) for _ in range(concurrency))
            )
            # Background logging runs after the response.
            for _ in range(300):
                if checked_out() == 0:
                    break
                await asyncio.sleep(0.05)
            scope = f"partner:{load_partner['partner']['id']}"
            slots = await get_store().in_flight(scope)
            return responses, slots

    responses, slots = run(scenario())
    statuses = [r.status_code for r in responses]

    assert statuses.count(200) == concurrency, f"unexpected statuses: {sorted(set(statuses))}"
    assert checked_out() == 0, f"{checked_out()} connections still checked out"
    assert slots == 0, f"{slots} concurrency slots still held"
    assert overload.snapshot()["in_flight"] == 0, "in-flight counter leaked"


def test_fast_requests_still_work_after_a_burst(gateway_url, load_partner):
    """A burst must not leave the pool in a state where normal traffic fails."""

    async def scenario():
        async with make_client(gateway_url, 30) as client:
            await asyncio.gather(*(chat(client, load_partner["key"]) for _ in range(20)))
            return await chat(client, load_partner["key"], model="ok-model")

    response = run(scenario())
    assert response.status_code == 200
    assert response.json()["gateway"]["provider"] == "custom"

# --------------------------------------------------- shedding must be minimal
def test_healthy_load_below_the_cap_is_never_shed(gateway_url, load_partner):
    """A burst of short pre-flight queries must not look like overload.

    Every request opens a short session before its provider call, so all 15 pool
    connections are briefly busy at the start of a burst. Shedding at that
    instant would reject healthy traffic well below the in-flight cap, which is
    why the pool signal requires sustained exhaustion.
    """
    from app.core import overload

    before = overload.snapshot()["shed_total"]

    async def scenario():
        async with make_client(gateway_url, 40) as client:
            return await asyncio.gather(
                *(chat(client, load_partner["key"], model="ok-model") for _ in range(40))
            )

    responses = run(scenario())
    statuses = [r.status_code for r in responses]
    shed = overload.snapshot()["shed_total"] - before

    assert statuses.count(200) == 40, f"unexpected statuses: {sorted(set(statuses))}"
    assert shed == 0, f"{shed} healthy requests were shed below the in-flight cap"


def test_cold_spend_cache_burst_does_not_exhaust_the_pool(
    gateway_url, load_partner, monkeypatch
):
    """A burst with an expired spend cache must not park connections on a lock.

    The spend aggregates are single-flighted, so one caller computes while the
    rest wait. If those waiters hold a database connection while they wait, a
    concurrent burst parks the whole pool behind a single aggregate and starves
    every other request - the original bug, reintroduced through a different
    door. This forces a cold cache, then bursts.
    """
    from app.core import limits, overload

    limits.invalidate_spend()
    before = overload.snapshot()["shed_total"]

    async def scenario():
        async with make_client(gateway_url, 60) as client:
            return await asyncio.gather(
                *(chat(client, load_partner["key"], model="slow2-model") for _ in range(60))
            )

    responses = run(scenario())
    statuses = [r.status_code for r in responses]
    shed = overload.snapshot()["shed_total"] - before

    assert statuses.count(200) == 60, f"unexpected statuses: {sorted(set(statuses))}"
    assert shed == 0, f"{shed} requests were shed after a cold spend cache"
    # And the pool is genuinely free afterwards, not just fast.
    assert checked_out() <= 2, f"{checked_out()} connections still held after the burst"


def test_spend_lock_waiters_hold_no_connection(gateway_url, load_partner):
    """Waiting on the single-flight spend lock must not cost a connection.

    This is the invariant the cold-cache burst depends on. The cached spend
    helpers open their own short session *inside* the lock, so only the caller
    that actually computes the aggregate is holding a connection; the others
    wait with none. If a caller ever passes its own session in again, every
    waiter parks a pooled connection and a burst exhausts the pool.
    """
    from app.core import limits

    async def scenario():
        limits.invalidate_spend()
        peak = 0
        stop = asyncio.Event()

        async def sample():
            nonlocal peak
            while not stop.is_set():
                peak = max(peak, checked_out())
                await asyncio.sleep(0.005)

        sampler = asyncio.create_task(sample())
        values = await asyncio.gather(
            *(limits.cached_partner_cost(load_partner["partner"]["id"]) for _ in range(40))
        )
        stop.set()
        await sampler
        return values, peak

    values, peak = run(scenario())

    assert len(set(values)) == 1, "single-flight returned inconsistent values"
    assert peak <= 2, (
        f"{peak} connections were checked out while 40 callers waited on one "
        f"spend aggregate; waiters must hold none"
    )
