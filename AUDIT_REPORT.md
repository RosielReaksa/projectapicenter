# Audit Report — Private LLM Gateway

**Date:** 2026-10-26
**Mode:** Read-only audit. No source or config was changed.
**Environment:** Windows 11, Python 3.12.7, SQLite. **No Docker, PostgreSQL, Redis, psql or redis-server are installed on this machine**, so all cluster-level checks are UNKNOWN rather than PASS.

Every finding is labelled VERIFIED (ran it, saw the output), CODE-ONLY (read it, did not run it), NOT FOUND, or UNKNOWN.

---

## Headline

The single most important finding: **the gateway collapses under 20 concurrent requests.** A 10-minute load test produced a **57.66% error rate** with a **30-second p50 latency**. Root cause is a connection-pool starvation defect in the request pipeline. Details in Part 3.12. This alone makes the current build unfit for real partners.

---

## PART 1: STAGE STATUS

| Stage | Status | Evidence |
|---|---|---|
| S0 skeleton, config, compose, migrations, fake provider, CI | **Partial** | Files exist (`docker-compose.yml`, `Dockerfile`, `Caddyfile`, `.github/workflows/ci.yml`, `migrations/`). Compose was never run — no Docker on this machine. VERIFIED: migrations apply and roll back. |
| S1 data model | **Done** | 3 migrations exist; `alembic upgrade head` / `downgrade` / re-upgrade all exit 0 (VERIFIED). |
| S2 key auth + one provider | **Done** | VERIFIED: valid key → 200 `hello from fake`; missing/invalid → 401; disabled/expired → 403. |
| S3 multi-provider, fallback, breaker | **Done** | VERIFIED: 429, 500 and empty-response each fell back and returned 200 with `fallbacks_tried` populated; breaker reached `cooling_down`. |
| S4 limits, cost tracking | **Done** | VERIFIED: concurrency cap 3 rejected 8 of 12; cost/price recorded per request. |
| S5 multi-instance, noisy neighbour, idempotency, cache, X-End-User | **Partial** | Idempotency and noisy-neighbour VERIFIED. **Multi-instance NOT TESTED** — needs Redis + 2 processes. |
| S6 admin API | **Done** | VERIFIED: partners, keys, providers, models, activity, usage, maintenance all respond. |
| S7 admin auth, 2FA, CSRF, lockout, audit, IP restriction | **Partial** | VERIFIED: `/admin/*` returns 401 without a session; CSRF enforced; audit rows written. **2FA is available but not enforced** — login succeeded with password only. |
| S7b maintenance backend | **Done** | VERIFIED: structured 503, admin/health/public stay up, provider pause, partner suspension, degraded mode, history. |
| S8 dashboard | **Done** | 13 pages present including Maintenance and Access Requests (CODE-ONLY for layout; earlier browser runs VERIFIED rendering). |
| S9 observability, backups, retention, load test, guides | **Partial** | Metrics, retention jobs and both guides exist. **Load test FAILED** (Part 3.12). No backup/restore ever run. |
| H1–H7 public site | **Done** | VERIFIED: `/`, `/models`, `/docs`, `/request-access`, `/terms`, `/privacy` all 200; abuse protections active; legal pages are placeholders. |
| M-1 … M-8 maintenance | **Done** | All eight verified by test suite (21 tests) plus live probes. M-7 scheduler is CODE-ONLY (needs Redis lock). |
| V1–V6 style update | **Done** | VERIFIED: `check-colors` clean, `check-contrast` 40/40 pass, base-URL guard fails on localhost. |

---

## PART 2: ACCEPTANCE CRITERIA

### Test suite totals (VERIFIED)

```
Command: .venv\Scripts\python.exe -m pytest tests --tb=short
Result:  138 passed in 44.73s
Failed: 0   Skipped: 0   Errors: 0
```

### AGENT_BUILD_BRIEF §6

| # | Criterion | Result | How checked | Evidence |
|---|---|---|---|---|
| 1 | Valid key answers through fake + one real provider | **PASS (fake) / NOT TESTED (real)** | Live probe | `valid key -> 200 content='hello from fake'`. No real provider keys configured. |
| 2 | Disabled, expired, wrong-IP, unknown keys rejected | **PASS** | Live probe + tests | `disabled->403 key_disabled`, `expired->403 key_expired`, `invalid->401`, IP allow-list test passes. |
| 3 | 429/500 fall back, `fallbacks_tried` populated | **PASS** | Live probe | `ratelimit-model -> 200 ... status:429`; `fail-model -> 200 ... status:500`. |
| 4 | All providers fail → 503 with list | **PASS (code) / FAIL (message)** | Live probe | 503 returned, but with `providers_tried=[]` and the wrong message. See Part 7. |
| 5 | Breaker opens then recovers | **PASS** | Live probe | Two providers reached `cooling_down`, `retry_in` 28–58s. |
| 6 | Concurrency cap holds, no leaked slots | **PASS** | Live probe | `cap=3, 12 concurrent -> 200s=4 429s=8`; slot released afterwards → 200. |
| 7 | Partner A burst does not hurt B | **FAIL** | Live probe | B's p50 went **325ms → 2342ms** (+2017ms) during A's burst. Error rate stayed 0%. |
| 8 | Daily/rate/spend reject with 429 + Retry-After | **PASS** | Tests | `test_daily_limit_returns_429_with_retry_after`, `test_key_rpm_limit`, `test_key_monthly_spend_cap`. |
| 9 | Two instances share counters | **NOT TESTED** | — | No Redis, no second instance possible here. `shared_state=memory`. |
| 10 | Kill one instance, no corruption | **NOT TESTED** | — | Same reason. |
| 11 | Cost and tokens match provider usage | **PASS** | Tests | `test_cost_and_price_are_both_recorded` (11 in / 7 out × $1/$2 per M). |
| 12 | Idempotency replay not charged twice | **PASS** | Live probe | `replayed=true same_request_id=True`, `activity rows added: 1 (expected 1)`. |
| 13 | Keys never appear in logs | **PASS** | Tests | `tests/test_security.py` scans DB and responses; DB scan found no raw `sk-gw-` value outside `key_prefix`. |
| 14 | 30-min load, <1% errors, no memory growth | **FAIL** | Live probe, 10 min | **57.66% errors, p50 30s.** See Part 3.12. |

### HOMEPAGE_SPEC §10

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Renders all sections, usable at 375px | **PASS** | All public pages `hScroll=false` at 375px. |
| 2 | Both themes pass contrast | **PASS** | `check-contrast` 40/40 ≥ 4.5:1. |
| 3 | Code card uses real base URL, copy works | **PASS** | Base URL from `/public/config`; copy button verified earlier. |
| 4 | Models list hides provider names and ids | **PASS** | `test_public_models_hides_provider_and_ids`. |
| 5 | Form creates one pending record, no account | **PASS** | `test_request_creates_no_user_partner_or_key`. |
| 6 | Rate limit rejects excess submissions | **PASS** | Live: `[201,201,201,201,201,429,429,429]`. |
| 7 | Honeypot stores nothing, shows no error | **PASS** | `honeypot submission -> 201`, `honeypot stored? False`. |
| 8 | Admin can list/approve/reject, audited | **PASS** | `test_approve_returns_prefill_and_audits`. |
| 9 | HTML/script shown as plain text | **PASS** | Stored verbatim; browser check showed no `<script>` node, no alert. |
| 10 | Signed-in users see "Open console" | **PASS** | Header read `Models Docs ☾ Open console`. |
| 11 | `/console` blocked in robots, not in sitemap | **PASS** | `test_robots_blocks_console`, `test_sitemap_lists_public_pages_only`. |
| 12 | Placeholder legal pages flagged | **PASS** | Console shows the warning banner; `legal_reviewed=false`. |

### MAINTENANCE_SPEC §14

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | `/v1` → 503 maintenance with Retry-After, even with bad key | **PASS** | `503 code=maintenance retry-after=300`; also blocked with `sk-gw-totally-invalid`. |
| 2 | Admin, health, public stay reachable | **PASS** | `admin 200`, `/health 200 status=maintenance`, `/readyz 503`, homepage 200. |
| 3 | Allow-listed IP passes | **PASS** | `test_allow_listed_ip_passes`. |
| 4 | In-flight completes, drain counter reaches zero | **PASS (unit) / NOT TESTED (live)** | `test_` suite covers drain status; no live in-flight test. |
| 5 | Two instances see the toggle in 3s | **NOT TESTED** | No Redis. |
| 6 | Restart keeps maintenance on | **CODE-ONLY** | `restore_redis_on_startup()` loads from PostgreSQL at boot. |
| 7 | Pausing a provider stops traffic, others succeed | **PASS** | Paused provider skipped; request still 200. |
| 8 | Pausing the only provider → 503, not a hang | **PASS (status) / FAIL (message)** | 503 returned, but says "No providers are configured yet". |
| 9 | Suspension blocks only that partner; internal reason never leaks | **PASS** | `403 partner_suspended msg='Contact billing.'`, `internal reason leaked? False`; other partner unaffected. |
| 10 | Degraded restrictions enforced, 429/503 + Retry-After | **PASS** | `429 code=degraded_mode` with Retry-After. |
| 11 | Typing MAINTENANCE required; off is one click | **CODE-ONLY** | Start button disabled until `confirm === 'MAINTENANCE'`; stop uses a plain confirm. |
| 12 | Every change in history + audit with correct source | **PASS** | `test_every_action_is_recorded`. |
| 13 | CLI works with the web app stopped | **CODE-ONLY** | `app/cli.py` exists; never executed. |
| 14 | Scheduled window starts/ends within 60s, one scheduler | **CODE-ONLY** | Job runs every 30s behind a Redis lock; never run. |
| 15 | Public banner 24h before, gone after | **PASS (data) / CODE-ONLY (banner)** | `upcoming_window` returned by `/public/status`; banner component reads it. |
| 16 | Redis down behaves per `MAINT_FAIL_MODE` | **CODE-ONLY** | Falls back to PostgreSQL, `stale` flag set. Never run with Redis present. |
| 17 | Rolling deploy shows no extra failures | **NOT TESTED** | No Docker. |

### STYLE_UPDATE §10

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Renders `OpenAI-compatible`, no bracketed fragment | **PASS** | `test_style_guards.py`; 8 files fixed. |
| 2 | Tabs read curl / Python / JavaScript | **PASS** | `test_code_tab_labels_are_explicit`. |
| 3 | No hard-coded colours outside tokens | **PASS** | `check-colors` → "clean". |
| 4 | Warm palette, no pure white/black background | **PASS** | `rgb(250,249,245)` / `rgb(31,30,28)`. |
| 5 | Accent changeable from one token | **CODE-ONLY** | All greens derive from `--accent`. |
| 6 | Contrast ≥ 4.5:1 all pairs | **PASS** | 40/40. |
| 7 | Serif headlines, Inter body, mono code, no CDN | **PASS** | Fonts resolved to Newsreader / Inter / JetBrains Mono; zero external font requests. |
| 8 | Spacing scale, no scroll at 375px | **PASS** | `hScroll=false` on all public pages. |
| 9 | Theme before first paint, persists | **PASS** | `<html class="dark">` set by inline script. |
| 10 | 16px cards, 10px controls, no shadows | **PASS** | Measured `16px` / `10px` / `box-shadow: none`. |
| 11 | Reduced motion disables transitions | **PASS** | `1e-05s`. |
| 12 | Production build fails on localhost base URL | **PASS** | exit 1 on `http://127.0.0.1:8020`, exit 1 when unset, exit 0 on `https://gw.example.com`. |
| 13 | No Claude/Anthropic branding | **PASS** | Page text contains neither word. |
| 14 | Status distinguishable without colour alone | **PASS** | Status shown with text labels. |
| 15 | Visual regression screenshots | **PARTIAL** | Screenshots were captured during development, not archived in the repo. |

---

## PART 3: RUNTIME CHECKS

Setup for every check below: the app on `127.0.0.1:8020` with SQLite and **in-memory** shared state, plus `tests/fake_provider.py` on a free port, registered as provider `custom`.

### 3.1 Start the full stack — **PARTIAL**

`docker`, `docker-compose`, `psql` and `redis-server` are all NOT FOUND on this machine, so PostgreSQL + Redis + Caddy could not be started. The application alone starts cleanly:

```
GET /health  → 200
{"status":"degraded","checks":{"database":true,"shared_state":true},
 "shared_state":"memory","usable_providers":0,
 "maintenance":{"active":false,...}}
GET /readyz  → 200 {"status":"ready"}
```

`degraded` is correct here: no provider has a key.

### 3.2 Key auth — **PASS**

```
valid key     -> 200 content='hello from fake'
missing key   -> 401 code=invalid_api_key
invalid key   -> 401 code=invalid_api_key
disabled key  -> 403 code=key_disabled
expired key   -> 403 code=key_expired
```

### 3.3 Fallback on 429 / 500 / empty — **PASS**

```
ratelimit-model -> 200 answered_by=ok-model
  fallbacks=[{"provider":"custom","model":"ratelimit-model","status":429,"error":"slow down","latency_ms":7}]
fail-model      -> 200 answered_by=ok-model
  fallbacks=[{"provider":"custom","model":"fail-model","status":500,"error":"upstream exploded","latency_ms":5}]
empty-model     -> 200 answered_by=ok-model
  fallbacks=[{"provider":"custom","model":"empty-model","status":200,"error":"Provider returned no choices.","latency_ms":3}]
```

The "hang" case is covered by `slow-model` and the timeout path in tests, not by a live probe.

### 3.4 All providers fail — **PASS on status, FAIL on message**

```
only provider paused -> 503 code=no_provider_available
   msg='No providers are configured yet. Add a provider key in the dashboard first.'
```

When the provider is paused rather than absent, the message is wrong and tells a partner to do something only the owner can. Also `fallbacks_tried` came back empty because the candidate list was filtered before any attempt.

### 3.5 Circuit breaker — **PASS**

```
attempt 1..5 (only failing provider): status=503 providers_tried=[]
after: custom: status=cooling_down retry_in=58s
       custom2: status=cooling_down retry_in=28s
```

An earlier probe that interleaved successful requests showed `status=degraded` instead; that was a flaw in my probe (successes reset the breaker), not in the product.

### 3.6 Per-partner concurrency cap — **PASS**

```
cap=3, 12 concurrent -> statuses=[200, 429] 200s=4 429s=8
slot released afterwards -> 200
```

Maximum observed in flight is not directly exposed; the 429 pattern implies the cap held. Counters return to zero.

### 3.7 Noisy neighbour — **FAIL**

```
quiet baseline:     error_rate=0%  p50=325ms
quiet during burst: error_rate=0%  p50=2342ms
delta:              error +0%,     latency +2017ms
```

Partner B's error rate is unaffected, but its **latency increases 7×** during A's burst. Acceptance criterion 7 requires B to be unaffected "beyond an agreed small margin". 2 seconds is not a small margin. Root cause is the same pool starvation as 3.12 — the burst consumes the shared DB pool.

### 3.8 Two instances, shared limit — **NOT TESTED**

```
shared_state = memory
```

Without `REDIS_URL` each instance counts separately, so a limit of 10/minute across two instances cannot hold. This is documented behaviour, but it means acceptance criteria 9 and 10 are unproven.

### 3.9 Kill one instance during load — **NOT TESTED**

Could not run two instances.

### 3.10 Idempotency — **PASS**

```
first=200 second=200 replayed=true same_request_id=True
activity rows added: 1 (expected 1)
```

### 3.11 Maintenance mode — **PASS**

```
start -> 200 {'active': True, 'retry_after': 300}
/v1 during maintenance -> 503 code=maintenance retry-after=300
admin still works      -> 200
/health                -> 200 status=maintenance
/readyz                -> 503
public homepage        -> 200
/public/status         -> {'state': 'maintenance', ...}
after stop, /v1        -> 200

suspended -> 403 code=partner_suspended msg='Contact billing.'
internal reason leaked? False
resumed -> 200
```

The in-flight-finishes-on-start case was not exercised live; drain logic is covered by unit tests only.

### 3.12 Load test — **FAIL**

Command: `AUDIT_LOAD_SECONDS=600 AUDIT_LOAD_CONCURRENCY=20 python tools/audit_load.py`

```json
{
  "duration_seconds": 607.7,
  "concurrency": 20,
  "total_requests": 725,
  "requests_per_second": 1.2,
  "error_rate_pct": 57.66,
  "status_counts": {"200": 307, "500": 300, "0": 117, "503": 1},
  "latency_ms": {"p50": 30031.8, "p95": 30243.0, "p99": 30288.6, "max": 30328.0},
  "memory_mb": {"before": null, "after": null, "samples": []}
}
```

Server log, repeated hundreds of times:

```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
connection timed out, timeout 30.00
ERROR gateway | Unhandled error [req_...]
```

**Machine size:** Windows 11, single uvicorn process, SQLite, in-memory store. Exact CPU/RAM not recorded — **UNKNOWN**.

**Root cause (CODE-ONLY, confirmed against the log):** the request-scoped `AsyncSession` from `get_session()` is held for the **entire** request, including the upstream provider HTTP call (`app/core/execute.py:532`), cache writes (`:585`) and background log work. The SQLite path in `app/db.py:22` uses the default pool (`pool_size=5`, `max_overflow=10` → 15 connections). At 20 concurrent requests, 20 sessions compete for 15 connections; each waiter blocks 30s and then raises. **This is not SQLite-specific** — the PostgreSQL path uses the same request-scoped session, so a slow provider will starve the pool there too, just with a larger configured size.

Memory growth could not be measured (the `powershell` RSS probe returned null).

---

## PART 4: SECURITY

| Check | Result | Evidence |
|---|---|---|
| Gateway keys hashed | **PASS** | `gateway_keys.key_hash VARCHAR(64)`, `key_prefix VARCHAR(32)`. DB scan: only `key_prefix` contains `sk-gw-` values (9 rows). |
| Provider keys encrypted | **PASS** | `providers.api_key_enc TEXT`; 0 rows contain the plaintext test key. |
| Passwords hashed | **PASS** | `users.password_hash VARCHAR(255)`; `totp_secret VARCHAR(512)`. |
| Full keys in logs or DB | **PASS** | No raw `sk-gw-` value found in any DB column; tests scan responses and logs. |
| `/admin` without login | **PASS** | 401 `not_authenticated` on keys, providers, overview, maintenance, access-requests. |
| 2FA enforced | **FAIL** | Login succeeded with email + password only. TOTP is available but not required. |
| Public signup creating an account | **NOT FOUND** | `/public/access-requests` 400 without a valid body; `/admin/auth/register` 403; `/public/register` 405; `/v1/keys` 405. Only a pending row is created. |
| Request-access protections | **PASS** | Rate limit `[201×5, 429×3]`; honeypot returns 201 and stores nothing. |
| CORS closed on `/v1` | **PASS** | Preflight 405, no `access-control-allow-origin`. |
| Security headers | **PASS** | CSP, nosniff, referrer-policy, frame DENY, permissions-policy all present. |
| Secrets committed to git | **PASS** | Only `.env.example` tracked; no `.env`, `master.key`, `session.secret` or `data/` in the tree; one commit total. |

---

## PART 5: REAL PROVIDERS

- **Configured with real keys:** none. All nine seeded providers have `has_key=False`. The only two with keys (`custom`, `custom2`) point at the local fake server used for this audit.
- **Tiny real request per provider:** **NOT POSSIBLE** — no real keys exist, and I will not invent them.
- **Model names / base URLs / prices verified against official docs:** **NO**. They were seeded from memory in `app/providers/registry.py` and never checked against provider documentation. Every price and model name there should be treated as **unverified**.
- **Free-tier providers in the default route for partner traffic:** **NO**. `allow_free_tier` defaults to false on both partner and key, and the router sorts free-tier providers last.

---

## PART 6: INTEGRATION AND DEPLOYMENT

| Question | Answer | Label |
|---|---|---|
| Has any real client called the gateway? | No. Only the audit probes and tests. No access logs from an external client exist. | VERIFIED (absent) |
| Deployed anywhere other than localhost? | No. Only `127.0.0.1`. | VERIFIED (absent) |
| HTTPS working? | **UNKNOWN** — no proxy run; Caddyfile exists but was never executed. | UNKNOWN |
| Backups configured? | A `pg_dump` command is documented in `docs/OPERATIONS.md`. **No schedule exists**, and no restore has ever been tested. | CODE-ONLY / NOT TESTED |
| Alerts configured? | No. `/metrics` and `/health` exist; nothing scrapes them. | NOT FOUND |
| Global monthly spend cap set | Default 50 USD (`GLOBAL_MONTHLY_CAP_USD`), overridable in Settings. Tested only indirectly via `test_key_monthly_spend_cap`. | PARTIAL |
| Production build rejects localhost base URL | Yes — exit 1 on `127.0.0.1`, exit 1 when unset, exit 0 on `https://`. | VERIFIED |

---

## PART 7: DEVIATIONS AND DEBT

### Deviations from the specs

1. **Light-theme status colours darkened.** Spec values failed 4.5:1 on card backgrounds and in tinted badges. Now `#276B44` / `#7E560E` / `#9E3727`.
2. **Tailwind's spacing scale was not overridden.** Overriding `theme.spacing` deletes `1.5`, `2.5`, `3.5` and breaks existing utilities; the §5 scale is a subset of the defaults and is followed in markup instead.
3. **No reserved capacity per partner** (brief §5.2, marked optional).
4. **Queueing is a bounded sleep loop**, not a real queue.
5. **Idempotency in-flight duplicates return 409** instead of waiting.
6. **Email notifications for access requests not implemented** (spec marks optional).

### Known bugs found in this audit

| # | Bug | Severity |
|---|---|---|
| 1 | **Connection-pool starvation**: the request session is held across the provider HTTP call; 20 concurrent requests → 57.66% errors, 30s p50. | **Critical** |
| 2 | Noisy neighbour: partner B's p50 rises 325ms → 2342ms during A's burst (same root cause). | High |
| 3 | Wrong error message when all providers are paused: "No providers are configured yet. Add a provider key in the dashboard first." | Medium |
| 4 | `fallbacks_tried` empty when the candidate list is filtered out before any attempt. | Low |
| 5 | 2FA is optional for the owner. | Medium |
| 6 | Load-test memory growth unmeasurable — the RSS probe returns null. | Low |

### Hard-coded values

Drain timeout 60s, Retry-After 300s, banner lead 24h, scheduler 30s, `MAINT_FAIL_MODE=closed`, pool 5+10. All are in `app/config.py` or documented defaults, not scattered literals.

### Skipped / disabled tests

None. 138 passed, 0 skipped.

### Placeholder content still present

- `/terms` and `/privacy` — placeholder text; `LEGAL_REVIEWED=false` and the console warns.
- Logo — letter mark in `frontend/public/favicon.svg`.
- `SUPPORT_EMAIL=support@example.com`.
- Social share image — `og-image.svg` placeholder.
- All model prices and names — unverified against provider docs.

---

## PART 8: SUMMARY

### Top 5 risks to a production launch

1. **The gateway falls over at 20 concurrent requests.** 57.66% error rate and 30-second responses in a 10-minute test. Nothing else matters until this is fixed.
2. **No real provider has ever been exercised.** Every model name, base URL and price is unverified. The first real call may fail or cost the wrong amount.
3. **No HTTPS, no proxy, no deployment.** Everything has only ever run on `127.0.0.1` over plain HTTP.
4. **No backups, no alerting, no restore test.** A database loss is unrecoverable, and a silent spend event would go unnoticed.
5. **2FA is optional and no real partner integration exists.** The security posture depends on the owner turning 2FA on, and the dubber site has never called the gateway.

### Ready vs not ready

Ready: the API contract, key auth, fallback, limits, maintenance controls, public site, console, and design system all behave as specified under light load and are covered by 138 tests.

Not ready: throughput, real-provider behaviour, deployment, HTTPS, backups, alerting, and 2FA enforcement.

### Recommended next 5 tasks

| # | Task | Size |
|---|---|---|
| 1 | Fix connection-pool starvation: don't hold the session across the provider call — commit/close before the HTTP request, reopen for logging. Then re-run the 10-minute load test to <1% errors. | **Medium** |
| 2 | Add real provider keys for Gemini and Groq and make one live call each; correct every price and model name against the official docs. | **Small** |
| 3 | Stand up `docker compose` (PostgreSQL + Redis + 2 instances + Caddy), verify HTTPS, then re-run acceptance 9, 10, 17. | **Medium** |
| 4 | Force 2FA for the owner account and make it required at first login. | **Small** |
| 5 | Fix the paused-provider message and populate `fallbacks_tried` on a filtered candidate list. | **Small** |
