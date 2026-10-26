# Agent Build Brief: Private LLM Gateway (Invite-Only, Production)

**Read this file first.** It is the source of truth for decisions. It works together with two earlier files:

1. `private-llm-gateway-spec.md`: product spec, console pages, visual design, API list.
2. `backend-design.md`: backend structure, request flow, router, database schema, security.

**Precedence rule:** where this brief conflicts with the two files above, **this brief wins**. Section 4 lists every override explicitly. Anything not overridden in this brief stays as written in the two files.

---

## 1. Project Context

We are building an **LLM gateway**: one OpenAI-compatible API in front of several AI providers (Gemini, Groq, OpenRouter, and others), plus an admin dashboard in a Claude Console style functionality with OpenRouter style visual design.

- **Business model:** it is a service we sell, but **invite-only**. There is no public signup. Customers are the owner's own websites and invited partners.
- **Main caller:** the owner's "dubber" website, plus other partner websites. They call the gateway from their **servers**, never from browsers.
- **Request content:** text in, text out. Typically transcripts. The gateway only receives a request and returns a response.
- **Expected scale:** about 100 or more end users in the first month across partners. Peak requests per second is not yet known (see section 11).
- **Language:** Python. The owner's other websites are also Python.

### Out of scope (do not build)

- Splitting or chunking transcripts. The calling website handles this.
- Speech-to-text and text-to-speech.
- Public signup, public marketing pages, self-serve payments.
- Streaming responses (planned later, not in v1).
- Image generation, fine-tuning, embeddings.

---

## 2. Core Decisions (Final Unless the Owner Changes Them)

| Topic | Decision |
|---|---|
| Language and framework | Python 3.11+, FastAPI, async |
| HTTP client | httpx (async) |
| Database | **PostgreSQL** from day one (SQLAlchemy 2.x + Alembic) |
| Shared state | **Redis** for rate counters, concurrency counters, circuit breaker state, and the job queue if used |
| Deployment shape | **Stateless gateway, 2 or more identical instances** behind a reverse proxy / load balancer; Docker + docker-compose |
| Access | Invite-only. Owner creates partners and keys. No public registration endpoint. |
| Gateway placement | Separate service. Websites call it over HTTPS. It is not embedded in any website. |
| Provider adapters | One `openai_compat` adapter for Gemini, Groq, OpenRouter, DeepSeek, Mistral (config, not new code). Separate adapter only when a provider is not OpenAI-compatible. |
| Primary routes | Paid, cheap models as the main route. Free tiers only as optional extra fallback, and **never as the default for partner traffic** because free tiers can have low limits, can change, and may use prompts for training. |
| Prompt logging | Default **metadata only**. Owner can switch to off or full. |
| Front end | React + Tailwind admin dashboard, served from the same domain as the API (`/admin`) |

---

## 3. Architecture

```
Partner websites (server side)
        │  HTTPS, Authorization: Bearer sk-gw-...
        ▼
Reverse proxy (HTTPS, optional IP rules)
        │
   ┌────┴─────┐
   ▼          ▼          (2+ identical, stateless instances)
Gateway A  Gateway B
   │          │
   ├── Redis (counters, concurrency, circuit breaker)
   ├── PostgreSQL (keys, partners, logs, billing data)
   └── Providers (Gemini, Groq, OpenRouter, ...)
Admin dashboard (React) ──► /admin API (same service, separate router)
```

Keep two planes separate in code and in auth:

- **Data plane** `/v1/*`: gateway key auth, fast path, no admin logic.
- **Control plane** `/admin/*`: login + 2FA, locked down harder.

---

## 4. Overrides to the Two Earlier Files

Apply these changes when reading `private-llm-gateway-spec.md` and `backend-design.md`.

| # | Earlier file says | Now |
|---|---|---|
| 1 | SQLite to start | **PostgreSQL from day one.** Use SQLite only for unit tests if convenient. |
| 2 | In-memory rate counters and circuit breaker | **Redis.** All instances must share counters and breaker state. |
| 3 | Single process is enough | **2 or more instances**, stateless. No local files, no in-process state that matters. |
| 4 | Members and Owner roles only | Add a **Partner** concept: a partner (company or website) owns one or more gateway keys, has its own limits, and its own usage view. Members from the old spec may be dropped or mapped to partner users. |
| 5 | Per-key limits | Add **per-partner limits** on top of per-key limits: concurrency, requests per minute, tokens per minute, monthly spend cap. |
| 6 | Free tiers as primary | **Paid tiers primary.** Free tiers are optional last-resort fallback and must be flagged per provider and excludable per partner. |
| 7 | Caching enabled by default for temperature 0 | Keep, but include the **partner id in the cache key** so no cache entries are shared across partners. |
| 8 | No end-user tracking | Accept an optional `X-End-User` header, store it on each request row, and support per-end-user limits when a partner asks for it. |
| 9 | No idempotency | Support an optional `Idempotency-Key` header so a retried request is not paid for twice. |
| 10 | Errors return basic status codes | On rate or capacity limits return **429 with a `Retry-After` header**. Never fail silently. |
| 11 | Milestones M1 to M8 order | Use the new order in section 7. |
| 12 | Dashboard has 11 pages | Build in phases (section 7). Customer-facing pages are limited to what partners need. |

---

## 5. Required Behaviors

### 5.1 Data plane (`/v1`)

- `POST /v1/chat/completions` (OpenAI-compatible), `GET /v1/models`, `GET /v1/usage`, `GET /health`.
- Authenticate with the gateway key (stored hashed). Check active, expiry, allowed IPs, allowed models.
- Check limits in this order: partner and key rate limit, concurrency, daily limit, spend caps. Reject with 429 and `Retry-After` when exceeded.
- Router tries candidates in order, skips providers whose circuit breaker is open, and falls back on 429, 5xx, timeout, or empty response. Max 3 attempts and 60 seconds total per request (configurable).
- Return the standard response plus a `gateway` object: provider used, cached flag, cost, fallbacks tried.
- Every response includes `X-Request-Id`.
- Write request logs in the background. Logging must never add latency to the response.
- Provider keys, gateway keys, and secrets must never appear in logs, errors, or responses (log key prefix only).

### 5.2 Noisy neighbor protection

One partner's burst must not starve another.

- Per-partner **concurrency cap** (requests in flight), enforced with Redis counters that are always released, even on errors or disconnects.
- Per-partner **requests per minute** and **tokens per minute**.
- Optional **reserved capacity** per partner (configurable, can be left off in v1).
- When over capacity: either reject fast with 429, or wait in a short queue up to a configurable timeout. Make this a per-partner setting, default to reject fast.

### 5.3 Money and cost

- Cost per request from token usage and per-model prices stored in the database.
- Per-key and per-partner **monthly spend caps** and a **global monthly cap** acting as a kill switch for paid routes.
- Provider cost versus partner price: store both `cost_usd` (what we pay) and `price_usd` (what the partner is charged, using a configurable markup per partner or per model). Billing and payment collection are **not** in v1, but the data must be recorded so invoicing can be added.

### 5.4 Security

- Gateway keys: random, at least 32 bytes, shown once, stored as SHA-256 hash.
- Provider keys: encrypted at rest (Fernet), master key from environment.
- Admin login: argon2 passwords, TOTP two-factor, secure cookies, CSRF protection, login lockout, audit log of every admin action.
- `/admin` restrictable by IP allow list at the proxy.
- `/v1` has no browser CORS by default.
- First owner account is created by a CLI command, not a web page.

### 5.5 Reliability

- Circuit breaker per provider with shared state in Redis (closed, open, half-open; open after 3 failures in 60 seconds, cool-down 30 seconds doubling to 10 minutes, honor `Retry-After`).
- Health endpoint reports status of the database, Redis, and each provider.
- Graceful shutdown: finish in-flight requests before an instance exits.
- Structured JSON logging. Expose basic metrics (request count, latency, error rate, per-provider failures, in-flight count) in a Prometheus-compatible endpoint, restricted to internal access.

---

## 6. Testing and Acceptance Criteria

Build a **fake provider server** first. It must be able to: answer after a configurable delay, return 429 with `Retry-After`, return 500, hang until timeout, return malformed JSON, and report token usage. All fallback and load tests run against it, not real providers.

**Acceptance criteria (v1 is done when all pass):**

1. A valid key gets a correct answer through the fake provider and through at least one real provider.
2. Disabled, expired, wrong-IP, and unknown keys are rejected with the correct status codes.
3. When the first provider returns 429 or 500, the request succeeds via the next provider, and `fallbacks_tried` shows it.
4. When all providers fail, the response is 503 with a clear list of what was tried.
5. A provider failing repeatedly gets its circuit opened and is skipped, then recovers via half-open.
6. Per-partner concurrency cap holds under load: with cap N, never more than N requests in flight for that partner, and counters return to zero afterward (no leaks).
7. A burst from partner A does not raise error rate or latency for partner B beyond an agreed small margin.
8. Daily limit, rate limit, and monthly spend cap each reject correctly with 429 and `Retry-After`.
9. Two gateway instances running together share counters correctly (a limit of 10 per minute holds across both).
10. Killing one gateway instance during load causes no data corruption and clients can retry successfully.
11. Cost and token totals in the database match the fake provider's reported usage.
12. `Idempotency-Key` replay returns the stored result and is not counted or charged twice.
13. Provider keys and gateway keys never appear in any log line (automated test that scans logs).
14. Load test: sustain the target in-flight request count (see section 11) against the fake provider with error rate under 1 percent and no memory growth over a 30-minute run.

---

## 7. Build Order

Work in this order. Complete and test each stage before the next. Open a short summary after each stage listing what was built, what was tested, and anything uncertain.

| Stage | Deliverable | Done when |
|---|---|---|
| S0 | Repo skeleton, config, Docker Compose (app, PostgreSQL, Redis, proxy), migrations, fake provider server, CI running tests | `docker compose up` works and tests run |
| S1 | Data model for users, partners, gateway keys, providers, models, requests, audit log | Migrations apply cleanly |
| S2 | Gateway key auth and `POST /v1/chat/completions` through one provider | Acceptance 1, 2 |
| S3 | Multi-provider router, fallback, circuit breaker in Redis | Acceptance 3, 4, 5 |
| S4 | Limits: per-key and per-partner rate, concurrency, daily, spend caps, `Retry-After`; cost tracking | Acceptance 6, 8, 11 |
| S5 | Multi-instance behavior, noisy neighbor tests, idempotency, caching, `X-End-User` | Acceptance 7, 9, 10, 12 |
| S6 | Admin API: partners, keys, providers (masked), models and prices, activity, usage | Everything manageable without touching the database |
| S7 | Admin auth: login, 2FA, CSRF, lockout, audit log, IP restriction | Acceptance 13; admin routes locked |
| S8 | Admin dashboard (React + Tailwind): **API Keys, Providers, Activity first**, then Dashboard overview, Usage and Cost, Models, Routing, Playground, Settings | Owner can run the system from the UI |
| S9 | Observability, backups, retention jobs, load test, deployment guide, partner integration guide | Acceptance 14 |

Dashboard visual rules, page contents, and design tokens come from `private-llm-gateway-spec.md` sections 3 to 5 (clean, dense tables, dark and light themes, monospace for keys and numbers).

---

## 8. Partner Integration Guide (Write This at S9)

Deliver a short document partners can follow, covering:

- Base URL, authentication header, one working example request.
- Call the gateway only from server code, never from a browser or mobile app.
- Retry rules: retry on 429 and 5xx with exponential backoff and jitter, honor `Retry-After`, set client timeouts, and send an `Idempotency-Key` on retries.
- Send `X-End-User` so usage and limits can be tracked per end user.
- Their limits (concurrency, requests and tokens per minute) and what happens when exceeded.
- What content is allowed, and that the service makes no uptime promise beyond what the owner states in writing.

---

## 9. Instructions for the Building Agent

1. Read this brief, then both earlier files, before writing code.
2. Follow section 7 in order. Do not start the dashboard before the admin API and auth exist.
3. Write tests alongside code. Use the fake provider for all provider behavior. Never put real keys in tests, code, or git.
4. Use environment variables for every secret. Provide `.env.example` with no real values.
5. Keep the gateway stateless: no important data in process memory or local disk.
6. Do not invent features outside this brief. If something is unclear or a decision is missing, list it in a `QUESTIONS.md` file and continue with the safest assumption, noting it.
7. Verify current provider details (base URLs, model names, prices, rate limits, terms) from each provider's official documentation at build time. Do not rely on memory, since these change often. Put them in configuration, not hard-coded.
8. The earlier small prototype (`main.py` from the first discussion) is a throwaway sketch. Do not extend it; build from the structure in `backend-design.md`.
9. After each stage, report: what changed, tests run and results, known gaps.

---

## 10. Risks to Keep in Mind

- **Provider terms:** reselling or serving third parties through a provider account may be restricted. The owner must confirm each provider's current terms before onboarding partners.
- **Privacy:** partners' end users send their own content. Do not route partner traffic to providers or tiers that may train on prompts unless the owner has approved that in writing. Keep a per-provider flag for this.
- **Provider capacity:** the total of all partners' demand is limited by the sum of provider account limits. Surface remaining headroom in the dashboard when possible.
- **Single points of failure:** PostgreSQL and Redis need backups and a recovery plan. Document what happens if Redis is down (recommended: fail closed for spend caps, fail open with conservative local limits for rate limits, and alert).

---

## 11. Open Items (Owner to Confirm; Agent Uses Defaults Until Answered)

| Item | Default the agent uses until told otherwise |
|---|---|
| Peak requests per second, all partners combined | Design and load test for **50 requests in flight** and 20 requests per second, with settings to scale up |
| Which paid providers and models first | Gemini Flash class, one Groq model, one OpenRouter model; all configurable |
| Payment collection method and currency | Not in v1. Only record `cost_usd` and `price_usd`. |
| Hosting target | Any Linux VPS with Docker; keep it portable |
| Prompt logging mode | Metadata only |
| Global monthly spend cap | A low placeholder (for example 50 USD), set by the owner |
| Number of partners at launch | 1 to 3 |
| Dashboard language | English |
| Provider markup | A single configurable multiplier per partner |
