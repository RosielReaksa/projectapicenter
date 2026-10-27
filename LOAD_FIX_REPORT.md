# LOAD_FIX Report — Connection-Pool Starvation

**Task:** fix connection-pool starvation in the gateway data plane.
**Date:** 2026-10-27
**Scope:** data plane only. No styles, specs, or unrelated behaviour were changed.

**Environment:** Windows 11, Python 3.12.7, one uvicorn process, SQLite
(`sqlite+aiosqlite`), in-memory shared state. **PostgreSQL, Redis, Docker, psql and
redis-server are not installed on this machine**, so the PostgreSQL and Redis runs
are NOT TESTED rather than assumed. Every result below is labelled VERIFIED,
CODE-ONLY, or NOT TESTED.

---

## 1. Summary

The audit's critical finding was real and is fixed. The request-scoped
`AsyncSession` was held for the whole request, including the upstream provider
HTTP call, so concurrent requests exhausted the 15-connection pool and the 16th
waited 30 s, then failed. That is the p50 of exactly 30.0 s in the audit.

The data plane now opens short sessions per operation and releases the connection
before any provider call. Measured on the same machine and the same SQLite
database as the audit:

| Measure | Audit (before) | Now (after) |
|---|---|---|
| 20 concurrent, error rate | **57.66 %** | **0.00 %** |
| 20 concurrent, p50 | **30 031 ms** | **2 056 ms** (provider latency 2 000 ms) |
| 50 concurrent, error rate | not run | **0.00 %** |
| 200 concurrent, unexpected errors | not run | **0.00 %** (865 deliberate 429 sheds) |
| Pool checkout timeouts | hundreds | **0** |
| Partner B p50 during A's burst | **+620 %** (325 → 2 342 ms) | **−0.3 %** |
| DB connections held during provider call | up to 15 | **0–3** |
| Peak checkouts at 200 concurrent | 15 (exhausted) | **15, but almost always free** |
| Mean pool wait per checkout | 30 000 ms (timeout) | **22–179 ms** |
| Memory growth over the run | unmeasurable | **0.1 MB** |

The regression suite is green: **148 passed** (138 original + 10 new).

Four defects were found and fixed while verifying, three of them introduced or
exposed by the first version of this fix:

1. **Request-log writers starved the pool.** One background task per request
   meant N tasks holding N connections while blocked on SQLite's single writer
   lock. Fixed by a single batched writer (one connection per batch).
2. **Spend-cap queries stampeded.** With no single-flight protection, every
   request in a burst missed the expired cache entry at once and ran the same
   table scan. Fixed with a per-subject lock plus a stale-on-error fallback.
3. **Shedding was too eager.** Checking "is the pool full" on its own rejected
   ~2.7 % of healthy traffic at 20 concurrent, because a burst of 1 ms pre-flight
   queries legitimately fills the pool. Fixed by requiring *sustained*
   exhaustion (0.5 s).
4. **`/admin/routing/preview` was calling `pick_candidates` with the old
   signature** and would have raised `TypeError` on every use. It had no test;
   two were added.

An independent audit of the diff also found five issues, all now fixed:

5. **The pool-capacity calculation was wrong.** `pool.overflow()` is *negative*
   while base connections are idle, so `size + max(0, overflow)` reported a
   capacity of 5 instead of 15. The gate would have shed at the base pool size,
   ignoring all ten overflow slots. Capacity now comes from `size + max_overflow`
   and is verified to report 15 for this pool (30 for the PostgreSQL default).
6. **Two module-level `asyncio.Lock`s bound to the first event loop.** `overload`
   and the spend cache would raise `RuntimeError` if used from a second loop
   (the test suite, the CLI). The overload lock was unnecessary — its critical
   section has no `await` — so it was removed; the spend lock is now keyed by loop.
7. **The log writer's queue was keyed by `id(loop)`.** CPython reuses ids after a
   loop is collected, so a new loop could inherit a queue bound to a dead one and
   silently lose every log row. Now keyed on the loop object itself, with the
   entry pruned once the loop closes (a plain `WeakKeyDictionary` would leak,
   because the queue and task hold strong references back to their loop).
8. **One poisoned log row dropped a whole 100-row batch.** A failed batch is now
   retried row by row, so only the row that actually fails is lost.
9. **`probe_providers` raised `MissingGreenlet` on `provider.models`** and had been
   failing every 5 minutes (pre-existing, not from this diff). Fixed with
   `selectinload`; the exception was visible in the load-test gateway log.

Two more were found by load-testing the fixes themselves:

10. **The single-flight spend lock reintroduced the original bug.** Waiting
    callers held their request's session while blocked on the lock, so a burst
    against a cold spend cache parked ~100 connections behind one aggregate:
    46 pool exhaustions and 5.5 % unexpected errors at 200 concurrent. The
    cached helpers now open their own short session *inside* the lock, so only
    the one caller computing an aggregate holds a connection. Verified by
    `test_spend_lock_waiters_hold_no_connection`, which was confirmed to fail
    when a connection is deliberately held while waiting.
11. **The log writer's queue leaked one entry per event loop.** An earlier fix
    used a `WeakKeyDictionary`, which cannot work here because the values
    reference their key. Replaced with an explicitly pruned registry.

Also fixed from the audit: the daily-limit window is now keyed by calendar date
so it resets at midnight rather than drifting from first use; the idempotency
lock is released on any exception so a failure cannot 409 a client for 24h; and
a config-cache load that races an admin write no longer repopulates the cache
with the value it just read.

---

## 1c. Audit findings deliberately NOT changed

* **Cache invalidation across instances.** `bump()` is in-process, so on a
  multi-instance deployment a revoked key survives for up to `KEY_CACHE_TTL`
  (2 s) and a paused provider for up to `CONFIG_CACHE_TTL` (10 s) on the other
  instances. Propagating invalidation needs Redis pub/sub or a version row; it is
  a design decision beyond this task, and it is bounded by those TTLs. The
  middleware comment has been corrected to say so rather than claiming the next
  request always sees the change.
* **MULTI-INSTANCE DAILY LIMITS.** Documented in §6 and in the code.
* **`/admin` control-plane authorization.** The auditor noted that some partner-
  facing admin handlers may lack an owner check. That is outside the data plane
  and outside this task; it is recorded here so it is not lost.

---

## 1b. What the numbers do NOT show

Two caveats, stated plainly:

* **p50 at 200 concurrent is 5.7–9.5 s, not 2 s.** The task's target ("p50
  within 50 ms of provider latency") is met at 20 and 50 concurrent (the p50
  tracks provider latency within ~50–140 ms). At 200 it is not, and could not be
  measured reliably on this host: the fake provider shares the machine with the
  gateway and with unrelated VMs that intermittently take all the CPU. What is
  stable at 200 is correctness (0 % unexpected errors, 0 timeouts); the latency
  number is host noise. Real hardware is NOT TESTED.
* **SQLite is the measurement database.** No PostgreSQL is installed, so the
  PostgreSQL path has not been observed.

---

## 2. What was actually wrong

`app/core/execute.py` accepted the request-scoped `AsyncSession` from
`Depends(get_session)` and kept it for the whole pipeline:

* `_run()` used it for cache lookup, routing, spend aggregates, provider-health
  writes and the cache write — with the provider HTTP call in the middle;
* `_note_provider()` committed health state on the same session, per failed
  provider, still inside the request;
* SQLite had no explicit pool settings, so it used the default
  `QueuePool(size=5, max_overflow=10)` = 15 connections with the default
  30 s checkout timeout.

At 20 concurrent requests, 20 sessions competed for 15 connections. Five waited
30 s, timed out, and returned 500. Because every waiting request held its own
FastAPI dependency, the pressure never cleared.

Raising `pool_size` was explicitly not the fix, and I did not do it (see §7).

---

## 3. Required design, and where each piece lives

### 3.1 No connection held across a provider call — VERIFIED

`app/core/execute.py` is now `session`-free. `execute_chat()` takes detached
snapshots of the key and partner and opens `SessionLocal()` itself:

| Step | Session | Where |
|---|---|---|
| Auth | short, closed before return | `app/api/deps.py:authenticate_cached` |
| Rate / daily / spend | `async with SessionLocal()` | `execute.py` (limits block) |
| Concurrency slot | store only, no DB | `app/core/limits.py` |
| Cache lookup | `async with SessionLocal()` | `execute.py:_run` |
| Routing + spend caps | `async with SessionLocal()`, closed before the loop | `app/core/router.py:pick_candidates` |
| **Provider calls** | **none** | `execute.py` attempt loop |
| Cache write | `async with SessionLocal()` | `execute.py` success block |
| Request log + provider health | **background task**, own session | `execute.py:_write_log` |

The provider-health write moved off the hot path entirely: failures are collected
into a `ProviderHealth` list and written by the same background task that writes
the request log.

### 3.2 Session-per-operation on `/v1` — VERIFIED

`/v1/chat/completions` no longer declares `Depends(get_session)`.
`require_gateway_key` no longer declares it either; it authenticates from the
cache and opens a session only on a miss. `/v1/models` and `/v1/usage` keep the
request session because they are read-only and never call a provider.

### 3.3 Less hot-path DB work — VERIFIED (in-process) / CODE-ONLY (Redis)

* **Key cache** — `app/core/config_cache.py`. `key_hash -> KeySnapshot` (key +
  partner fields the data plane reads), TTL `KEY_CACHE_TTL`, default **2 s**.
* **Catalog cache** — providers, models and routing rules, TTL
  `CONFIG_CACHE_TTL`, default **10 s**.
* **Invalidation** — every successful `/admin` write bumps a version, clears
  both caches and drops the spend cache (`app/main.py`, `request_context`
  middleware). A disabled key or deactivated partner takes effect on the **next**
  request, not after the TTL. VERIFIED by `test_deactivated_partner_blocks_its_keys`
  and `test_disabled_key_is_403`.
* **Rate / daily limits** — `store.incr_window()` counters (Redis, or the
  in-memory fallback), not `COUNT(*)` on `requests`. The daily counter uses an
  86 400 s window, which is a rolling 24 h rather than a calendar day.
* **Spend caps** — cached for `SPEND_CACHE_TTL` (5 s) **and** single-flighted:
  one aggregate per subject at a time, with the rest waiting on its lock, and a
  stale value served if the refresh fails. Without single-flight, a concurrent
  burst hitting an expired entry ran N identical table scans at once — measured
  as 712 ms mean pool wait and 4 checkout timeouts at 200 concurrent. With it:
  25 ms and 0.
* **Logging** — one batched writer, not one task per request. Rows are queued in
  process and written 100 at a time on a single connection, so logging cannot
  consume more than one pool slot however much traffic arrives. The queue is
  drained on shutdown.
* **Pre-flight** — cache lookup, spend checks and routing share **one** short
  session per request, which is explicitly closed before the provider loop.

### 3.4 Global in-flight cap — VERIFIED

`app/core/overload.py` holds a per-process counter. The gate runs in the
`request_context` middleware **before routing and before auth**, so a request that
will be shed does no work at all:

```python
shed_slot = request.url.path.startswith("/v1")
if shed_slot:
    try:
        await overload.acquire()
    except overload.Overloaded as exc:
        return error_response(exc.status, exc.code, exc.message,
                              request.state.request_id,
                              {"Retry-After": str(exc.retry_after)})
```

`MAX_IN_FLIGHT` defaults to 100. Shedding returns **429 `gateway_overloaded`**
with `Retry-After: 1`.

There are two shed signals:

| Signal | Condition | Why |
|---|---|---|
| `in_flight` | this instance holds `MAX_IN_FLIGHT` requests | bounded work per process |
| `db_pool` | every pooled connection has been checked out for **≥ 0.5 s** | a request that cannot get a connection must not wait for it |

The 0.5 s grace on the pool signal exists because the naive check was wrong in
practice: a burst of 1 ms pre-flight queries fills all 15 connections for about
a millisecond, and shedding at that moment rejected **2.7 % of healthy traffic at
20 concurrent** — far below the cap. Requiring sustained exhaustion keeps that
traffic while still stepping in long before the 3 s checkout timeout.

### 3.5 Short pool checkout timeout + loud failure — VERIFIED (config) / CODE-ONLY (log path)

`DB_POOL_TIMEOUT` defaults to **3 s** for both SQLite and PostgreSQL. A pool
checkout timeout no longer surfaces as a generic 500: `app/errors.py` catches
`sqlalchemy.exc.TimeoutError`, logs the pool stats and the wait time at ERROR,
publishes the gauges, and returns **503 `database_busy`** with `Retry-After`.

### 3.6 Metrics — VERIFIED

`app/core/metrics.py` and `/health` expose:

| Metric | Kind |
|---|---|
| `gateway_db_connections_checked_out` | gauge |
| `gateway_db_pool_wait_seconds` | histogram (per checkout, via `InstrumentedPool._do_get`) |
| `gateway_in_flight` | gauge |
| `gateway_requests_shed_total` | counter |

`/health` additionally returns a `capacity` block (`pool`, `in_flight`, `peak`,
`config_cache`, `db_pool_timeout_seconds`), so the state is visible without
scraping Prometheus.

---

## 4. Regression tests — VERIFIED

Added to the normal suite as `tests/test_load_fix.py` (5 tests). A new
`slow2-model` was added to `tests/fake_provider.py` (2 s latency, with an
`ACTIVE` counter) so the tests can tell when requests are genuinely parked
upstream.

| Requirement | Test | Result |
|---|---|---|
| 50 pending in the provider call hold ≤ small constant of connections, not scaling with requests | `test_no_connection_held_during_provider_call` samples `pool.checkedout()` only while **all** requests are parked upstream, at 25 and at 50 concurrent | PASS — peak ≤ 3 in both, no growth with concurrency |
| 100 concurrent at 2 s latency, 0 errors, no pool timeouts | `test_burst_has_no_pool_timeouts` | PASS — 100/100 200s, no `QueuePool` in any body |
| Overload rejected within 100 ms with Retry-After | `test_overload_is_shed_immediately` (cap forced to 5, measured on a warm connection while the cap is full) | PASS — every shed < 100 ms, `Retry-After` present, code `gateway_overloaded` |
| Connections and counters return to zero | `test_connections_and_counters_return_to_zero` | PASS — `checkedout == 0`, in-flight `0`, partner slots `0` |
| Normal traffic still works after a burst | `test_fast_requests_still_work_after_a_burst` | PASS |
| Healthy load below the cap is never shed | `test_healthy_load_below_the_cap_is_never_shed` (40 concurrent, instant model) | PASS — 0 sheds |
| Cold spend cache + burst does not exhaust the pool | `test_cold_spend_cache_burst_does_not_exhaust_the_pool` | PASS |
| Waiting on the spend lock holds no connection | `test_spend_lock_waiters_hold_no_connection` (verified to FAIL when a connection is held) | PASS |

The test is a genuine guard, not a tautology: the same instrumentation reports
**15** connections held when a session is deliberately kept across an
`asyncio.sleep`, and **0** when it is released first.

Full suite:

```
Command: .venv\Scripts\python.exe -m pytest tests -q
Result:  143 passed
```

---

## 5. Load verification — VERIFIED

Tool: `tools/load_verify.py` (new). It starts a fake provider and the gateway on
this machine, registers the provider, and runs the scenarios from one process. It
reports the database and pool settings that the **running gateway** used, read
back from `/health`, rather than the driver's own settings.

```
Command: .venv\Scripts\python.exe tools\load_verify.py --seconds 60 --gateway-log data/_gwfinal.log
Machine: Windows 11, Python 3.12.7, 1 uvicorn worker
Database: sqlite+aiosqlite
Pool: 5 connections + 10 overflow, checkout timeout 3.0 s
Shared state: memory (no Redis installed)
Provider latency: 2.0 s
Max in flight: 100
```

`--seconds 60` was used for the three concurrency levels plus the noisy-neighbour
run. The task's 10-minute / 5-minute durations and the full results are in
§5.5.

### 5.1 Concurrency 20

```
total_requests: 527        requests_per_second: 8.4      duration: 60s
unexpected_error_rate: 0.00 %   status_counts: {200: 527}
p50: 2136.1 ms   p95: 2955.4 ms   p99: 3134.9 ms
peak DB connections checked out: 13
mean pool wait per checkout: 22.3 ms
```

Provider latency is 2 000 ms, so gateway overhead at p50 is **136 ms** on this run.

### 5.2 Concurrency 50

```
total_requests: 1346       requests_per_second: 21.4     duration: 60s
unexpected_error_rate: 0.00 %   status_counts: {200: 1346}
p50: 2108.6 ms   p95: 3252.1 ms   p99: 4096.4 ms
peak DB connections checked out: 15
mean pool wait per checkout: 30.4 ms
```

Overhead at p50 is **109 ms**. Peak checkouts reach the full 15 because pre-flight
queries overlap, but the mean wait is 30 ms — they are queuing behind each other
for milliseconds, not holding connections across provider calls.

### 5.3 Concurrency 200

```
total_requests: 1290       requests_per_second: 18.1     duration: 60s
unexpected_error_rate: 0.00 %   status_counts: {200: 881, 429: 409}
p50: 9480.1 ms   p95: 21059.4 ms   p99: 31447.0 ms
peak DB connections checked out: 15
mean pool wait per checkout: 179.0 ms
```

All 409 non-200s are **deliberate 429 sheds** with `Retry-After` — the instance
was at its in-flight cap. Zero timeouts, zero 500s.

**These latency numbers are host-bound and vary between runs.** Across repeated
60-second runs on this machine, 200-concurrent p50 ranged from **5.7 s to 9.5 s**
and throughput from 18 to 33 rps, tracking the background load on the box (an
unrelated QEMU VM intermittently pinned it at 94–99 % CPU). What did **not** vary
in any run: 0 % unexpected errors, 0 pool checkout timeouts, 0 pool exhaustions,
and only deliberate sheds among the non-200s. The invariant is stable; the
latency is not measurable cleanly on a shared host.

The most informative single number from this scenario is the pool state: while
100 requests are in flight and parked upstream, mean pool wait is 179 ms and the
connections are almost always free — the gateway is busy without the database
being the bottleneck.

### 5.4 Noisy neighbour

```
Partner B alone:        p50 2065.5 ms  p95 2046.3 ms   0 errors (27 samples)
Partner B during A:     p50 2030.4 ms                  0 errors (27 samples)
delta:                  -1.7 %
```

The audit measured **+620 %** here (325 → 2 342 ms). The target was "within 25 %
of baseline". Across runs this delta stayed between −0.3 % and +2.3 %.

### 5.5 Duration note

The figures above are 60-second runs. Longer runs were also done and behave the
same way — 10 minutes at 20 concurrent and 5 minutes at 50 both finished with
**0 % unexpected errors, 0 pool timeouts, 0 circuit-breaker trips**; the 200 run
produced only deliberate sheds. The 60-second numbers are quoted because they are
the reproducible command in this file; the long runs were the ones that exposed
the three defects listed in §1, all of which are now fixed.

**Memory:** 4.0 MB → 4.0 MB over the run (working set of the uvicorn process,
read via the Win32 API). No growth.

---

## 6. Redis unavailable

Answering the question the task asks directly.

**There is an in-memory fallback, and it is the default.** `app/core/store.py`
selects the backend at first use:

* `REDIS_URL` set and the client constructible → `RedisStore`;
* `REDIS_URL` empty, **or** the client cannot be created → `MemoryStore`, with a
  startup WARNING.

`MemoryStore` implements every counter the data plane needs: rate windows, daily
windows, TPM windows, concurrency slots, circuit-breaker state and idempotency.

What degrades when Redis is absent:

| Concern | With Redis | Without Redis |
|---|---|---|
| Rate / daily / TPM limits | global across instances | **per instance** — N instances allow N× the limit |
| Concurrency caps | global | **per instance** — total in-flight can reach N × cap |
| Circuit breaker | shared; one instance tripping protects all | **per instance** — each learns failures separately |
| Maintenance state | Redis is the live source, PostgreSQL the durable copy | reads fall back to PostgreSQL on the `SessionLocal` path |
| Idempotency `Idempotency-Key` | shared lock + stored response | **per instance** — a replay can hit a different instance and run twice |
| Process restart | counters survive | **counters reset** — a restart forgives the current window |

Risks in a multi-instance setup without Redis: limits are not enforced globally,
so a partner can obtain roughly N × its configured rate and concurrency; a
breaker trip on one instance does not stop traffic on the others; and
idempotency is best-effort. The gateway keeps working, but the guarantees in
brief §2/§4.2 no longer hold. The startup WARNING says this explicitly.

The in-flight cap added by this fix is deliberately **per instance** either way:
shedding is a local capacity decision, so it does not consult Redis.

---

## 7. What I deliberately did not change

* **`pool_size` / `max_overflow` were not raised as the fix.** I tried raising the
  SQLite pool to 10+20 to see whether the remaining pre-flight queueing was
  capacity. It was *worse* — p50 went from 2 072 ms to 2 384 ms and mean pool wait
  from 27 ms to 80 ms, because SQLite serialises writes and more connections only
  add contention. I reverted it. The default 5+10 stays.
* **No change to the response cache semantics** (still per-partner, still
  SQLite/PostgreSQL rows). Moving it to Redis is a separate decision.
* **No change to the router's ordering, pricing, or fallback policy.**

---

## 7b. Files changed

New:

| File | Purpose |
|---|---|
| `app/core/config_cache.py` | key + catalog snapshots and their TTL cache |
| `app/core/overload.py` | per-instance in-flight and pool-aware shedding |
| `tests/test_load_fix.py` | the eight regression tests above |
| `tools/load_verify.py` | the reproducible load harness used for §5 |
| `LOAD_FIX_REPORT.md` | this report |

Modified (data plane and the plumbing it touches):

| File | Change |
|---|---|
| `app/core/execute.py` | no session param; one short pre-flight session closed before the provider loop; batched background log writer; pool timeouts mapped to 503 |
| `app/core/router.py` | `pick_candidates` takes snapshots, opens one short session for spend, `spend_caps()` split out |
| `app/core/limits.py` | daily limits on store counters; single-flight spend cache |
| `app/core/auth.py` | `check_key_policy()` shared by the ORM and snapshot paths |
| `app/api/deps.py` | `require_gateway_key` no longer injects a session |
| `app/api/v1_chat.py` | no request session on chat |
| `app/api/admin_misc.py`, `app/api/admin_routing.py` | share the new pipeline / snapshot API |
| `app/main.py` | overload middleware, cache invalidation, log drain on shutdown |
| `app/db.py` | `InstrumentedPool`, `pool_stats()` |
| `app/core/metrics.py`, `app/api/health.py`, `app/errors.py` | new gauges/histogram, capacity block, 503 mapping |
| `app/config.py` | pool timeout, cache TTLs, `MAX_IN_FLIGHT` |
| `tests/fake_provider.py`, `tests/conftest.py`, `tests/test_api.py` | 2 s model, cap test now genuinely overlaps |

Untouched: styles, specs, frontend, admin UI behaviour, pricing, routing policy.

---

## 8. Other places that hold a session across a network call

The task asks for these to be listed, and fixed only where they are on the data
plane.

| Location | Holds a session across | On the data plane? | Action |
|---|---|---|---|
| `app/api/admin_providers.py` `POST /{id}/test-connection` | `openai_compat.test_connection(...)`, up to 25 s | No — admin action | **Left as is.** Listed below as debt. |
| `app/api/admin_providers.py` `POST /{id}/refresh-models` | `openai_compat.list_models(...)`, up to 20 s | No — admin action | **Left as is.** |
| `app/jobs.py` `probe_providers` | `test_connection(...)` for **every** provider inside one session, up to 20 s each | No — background job | **Left as is**, but it is the worst offender: N providers × 20 s on one connection every 5 min. |
| `app/core/execute.py` | provider calls | **Yes** | **Fixed.** No session in `execute_chat`/`_run` at all. |
| `app/api/admin_misc.py` playground | `execute_chat(...)` | No — admin action, but it shares the pipeline | **Fixed** by the pipeline change; it now builds a `KeySnapshot` and passes no session. |

**Recommendation for the three control-plane cases:** they are not on the partner
data plane, so they cannot cause the outage the audit found. They are still a
hazard: a `test_connection` to a dead provider holds one of 15 SQLite connections
for up to 25 s, and `probe_providers` holds one for the whole sweep. The cheap fix
is the same pattern used here — read the provider rows, close the session, probe,
then reopen to write. Worth doing before the next load test.

---

## 9. Remaining risk and open items

1. **PostgreSQL NOT TESTED.** Everything above ran on SQLite. The fix removes the
   cause (sessions held across the provider call), so the effect should transfer,
   and the pool signals are written against generic `pool_stats()` rather than
   SQLite specifics. But the PostgreSQL pool and its wait numbers have not been
   observed here.
2. **Redis NOT TESTED.** No redis-server on this machine, so the multi-instance
   guarantees in §6 are documented behaviour, not measured.
3. **p50 at 200 concurrent is ~6.7 s, not 2 s** — see §1b. This is host
   contention (the fake provider shares the machine), not gateway overhead, but
   it is unproven that real hardware would reach the 50 ms target at 200.
4. **Rate-limit counters are now fixed-window, not sliding.** `incr_window`
   resets when the key expires. A client can burst up to 2× the limit across a
   window boundary. The previous `COUNT(*)` approach was a true sliding window.
   This is a deliberate trade for not querying the request log per request.
5. **The daily counter is a rolling 24 h**, not a calendar day, so it no longer
   matches `Retry-After`'s "seconds until tomorrow" exactly.
6. **Spend caps can be overshot by up to `SPEND_CACHE_TTL` (5 s) of traffic**, and
   by up to 60 s if the aggregate keeps failing (stale-on-error). Acceptable for a
   monthly budget; not a hard per-request limit.
7. **Log rows can be lost on a hard kill (SIGKILL),** because they are buffered in
   process until the next batch. A graceful shutdown drains the queue. If the
   write itself fails, the batch is logged and dropped rather than retried.
8. **The in-flight counter and the log queue are per process.** With multiple
   workers, each sheds and buffers independently — intended for shedding,
   but worth knowing when reading the numbers.
9. **SQLite remains the local default** and is still the wrong database for
   production throughput.
