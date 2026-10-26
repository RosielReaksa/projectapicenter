# Private LLM Gateway: Backend Design

Companion to `private-llm-gateway-spec.md`. This file covers only the backend: structure, request flow, routing logic, database, security, and deployment.

---

## 1. Backend Responsibilities

The backend does two separate jobs. Keep them separate in the code.

| Plane | Purpose | Who calls it | Auth |
|---|---|---|---|
| **Data plane** (`/v1/*`) | Receives AI requests and routes them to providers | Your websites and apps (server side) | Gateway key (`sk-gw-...`) |
| **Control plane** (`/admin/*`) | Manages keys, providers, routing, members, reads logs | The admin dashboard | Login session + two-factor |

Why split: the data plane must be fast and simple. The control plane can be slower but must be locked down harder. A bug in the dashboard must never break AI calls.

---

## 2. Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | Fast to write, good async support |
| Framework | FastAPI | Async, automatic API docs, easy validation |
| HTTP client | httpx (async) | Calls providers without blocking |
| Database | SQLite (start), PostgreSQL (later) | SQLite is enough for personal use; same SQL schema moves to Postgres |
| ORM / queries | SQLAlchemy 2.x + Alembic migrations | Safe schema changes over time |
| Encryption | `cryptography` (Fernet) | Encrypt provider keys at rest |
| Password hashing | argon2-cffi | Strong password storage |
| Two-factor | pyotp (TOTP) | Works with any authenticator app |
| Cache / rate counters | In-memory first; Redis later if needed | Keep v1 simple |
| Server | Uvicorn behind Caddy or Nginx | HTTPS and reverse proxy |
| Packaging | Docker + docker-compose | Easy to move between machines |

---

## 3. Project Structure

```
gateway/
├── app/
│   ├── main.py                 # creates FastAPI app, mounts routers
│   ├── config.py               # reads environment settings
│   ├── db.py                   # database session setup
│   ├── models.py               # SQLAlchemy tables
│   ├── schemas.py              # request/response shapes (Pydantic)
│   │
│   ├── api/
│   │   ├── v1_chat.py          # POST /v1/chat/completions
│   │   ├── v1_models.py        # GET /v1/models
│   │   ├── v1_usage.py         # GET /v1/usage
│   │   ├── admin_auth.py       # login, logout, 2FA
│   │   ├── admin_keys.py       # gateway key management
│   │   ├── admin_providers.py  # provider key management
│   │   ├── admin_routing.py    # routes and rules
│   │   ├── admin_activity.py   # request logs
│   │   └── admin_usage.py      # totals and charts
│   │
│   ├── core/
│   │   ├── auth.py             # verify gateway keys and admin sessions
│   │   ├── limits.py           # daily limits, spend caps, rate limiting
│   │   ├── router.py           # picks provider order, runs fallback
│   │   ├── health.py           # provider health tracking (circuit breaker)
│   │   ├── cache.py            # response cache
│   │   ├── cost.py             # token cost calculation
│   │   ├── crypto.py           # encrypt/decrypt provider keys
│   │   └── audit.py            # writes audit log entries
│   │
│   └── providers/
│       ├── base.py             # common provider interface
│       ├── openai_compat.py    # works for Gemini, Groq, OpenRouter, DeepSeek...
│       └── registry.py         # list of provider definitions
│
├── migrations/                 # Alembic migration files
├── tests/
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

**Key idea:** most providers (Gemini, Groq, OpenRouter, Mistral, DeepSeek) offer an OpenAI-compatible endpoint. One class, `openai_compat.py`, handles all of them with different base URLs. Adding a provider becomes a config entry, not new code. A provider with a different format (for example native Anthropic) gets its own adapter file later.

---

## 4. Request Lifecycle (Data Plane)

What happens on `POST /v1/chat/completions`:

```
1. Receive request
2. Authenticate gateway key  ──► 401 if missing/invalid, 403 if disabled/expired/IP blocked
3. Validate body             ──► 400 if no messages or bad fields
4. Check limits              ──► 429 if daily limit, monthly spend cap, or rate limit hit
5. Check cache (if temp = 0) ──► return cached response, log as cached
6. Build candidate list      (router: apply rules, filter unhealthy, order by priority)
7. For each candidate:
      a. call provider with timeout
      b. success      ──► go to step 8
      c. 429/5xx/timeout ──► record failure, try next candidate
      d. 400 client error ──► try next candidate once, then stop
8. Calculate cost, store response in cache if eligible
9. Write request log (async, must not slow the response)
10. Return response + gateway metadata
11. If all candidates failed ──► 503 with list of what was tried
```

Rules for this flow:

- Steps 2 to 5 must be fast (database reads cached in memory where possible).
- Logging (step 9) runs in a background task so the user does not wait for it.
- A provider key must never appear in a response, log, or error message.

---

## 5. Router Design

The router decides which provider and model answer a request.

### 5.1 Inputs

- Requested `model` (a specific model name or `auto`)
- Optional `preset`
- Request features: has image, estimated input size, needs tools
- Current provider health
- Routing rules configured by the Owner

### 5.2 Algorithm

```
function pick_candidates(request):
    if request.model is a specific model:
        candidates = [that model's provider/model pair] + configured fallbacks
    else:  # auto
        candidates = default ordered list from routing settings

    apply rules top to bottom:
        if rule matches request, reorder or replace candidates

    remove candidates where:
        provider has no key
        provider is disabled
        model is disabled
        circuit breaker is open
        provider monthly cap reached
        request needs a feature the model lacks (vision, tools, context size)

    return candidates in order
```

### 5.3 Circuit breaker (health tracking)

Stops the gateway from wasting time on a provider that is down or rate limited.

| State | Meaning | Behavior |
|---|---|---|
| Closed | Healthy | Send traffic normally |
| Open | Failed repeatedly | Skip this provider for a cool-down period |
| Half-open | Cool-down finished | Send one test request; success closes it, failure reopens it |

Suggested defaults: open after 3 failures in 60 seconds; cool-down 30 seconds, doubling up to 10 minutes. If a provider returns a `Retry-After` header on a 429, use that value as the cool-down.

### 5.4 Fallback rules

| Provider response | Action |
|---|---|
| 200 | Return result |
| 429 rate limit | Mark provider cooling down, try next |
| 500, 502, 503, 504 | Count failure, try next |
| Timeout | Count failure, try next |
| 401 or 403 from provider | Provider key problem: mark provider as misconfigured, alert in dashboard, try next |
| 400 | Try next once (model may reject input), then return error |
| Empty or malformed response | Treat as failure, try next |

Cap total attempts per request (default 3) and total time per request (default 60 seconds) so one request cannot loop through everything.

---

## 6. Database Design

SQLite to start. The schema below works unchanged on PostgreSQL.

```sql
-- People who can log into the dashboard
CREATE TABLE users (
    id              INTEGER PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('owner','member')),
    password_hash   TEXT NOT NULL,
    totp_secret     TEXT,                 -- encrypted
    totp_enabled    INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    created_at      TIMESTAMP NOT NULL
);

-- Keys YOU issue to apps and people
CREATE TABLE gateway_keys (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    name            TEXT NOT NULL,
    key_hash        TEXT UNIQUE NOT NULL, -- sha256 of full key
    key_prefix      TEXT NOT NULL,        -- first 8 chars, for display
    daily_limit     INTEGER,              -- requests per day
    monthly_cap_usd REAL,                 -- spend cap
    rpm_limit       INTEGER,              -- requests per minute
    allowed_models  TEXT,                 -- JSON list, null = all
    allowed_ips     TEXT,                 -- JSON list, null = any
    expires_at      TIMESTAMP,
    is_active       INTEGER DEFAULT 1,
    last_used_at    TIMESTAMP,
    created_at      TIMESTAMP NOT NULL
);

-- Your upstream provider accounts
CREATE TABLE providers (
    id              INTEGER PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL, -- gemini, groq, openrouter...
    base_url        TEXT NOT NULL,
    api_key_enc     TEXT NOT NULL,        -- encrypted with master key
    is_free_tier    INTEGER DEFAULT 0,
    monthly_cap_usd REAL,
    rpm_limit       INTEGER,
    priority        INTEGER DEFAULT 100,  -- lower = tried first
    is_active       INTEGER DEFAULT 1,
    created_at      TIMESTAMP NOT NULL
);

CREATE TABLE models (
    id              INTEGER PRIMARY KEY,
    provider_id     INTEGER NOT NULL REFERENCES providers(id),
    model_name      TEXT NOT NULL,        -- the provider's name for it
    alias           TEXT,                 -- your own friendly name
    context_length  INTEGER,
    input_price_per_m  REAL DEFAULT 0,    -- USD per 1M tokens
    output_price_per_m REAL DEFAULT 0,
    supports_vision INTEGER DEFAULT 0,
    supports_tools  INTEGER DEFAULT 0,
    is_enabled      INTEGER DEFAULT 1,
    UNIQUE(provider_id, model_name)
);

CREATE TABLE routing_rules (
    id              INTEGER PRIMARY KEY,
    position        INTEGER NOT NULL,     -- evaluation order
    condition_json  TEXT NOT NULL,        -- e.g. {"has_image": true}
    action_json     TEXT NOT NULL,        -- e.g. {"use_models": [3,7]}
    is_active       INTEGER DEFAULT 1
);

CREATE TABLE presets (
    id              INTEGER PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    system_prompt   TEXT,
    model_id        INTEGER REFERENCES models(id),
    params_json     TEXT
);

-- One row per request. Biggest table.
CREATE TABLE requests (
    id              INTEGER PRIMARY KEY,
    gateway_key_id  INTEGER REFERENCES gateway_keys(id),
    created_at      TIMESTAMP NOT NULL,
    requested_model TEXT,
    provider_id     INTEGER REFERENCES providers(id),
    model_id        INTEGER REFERENCES models(id),
    status_code     INTEGER,
    latency_ms      INTEGER,
    prompt_tokens   INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0,
    cached          INTEGER DEFAULT 0,
    fallbacks_tried TEXT,                 -- JSON list of failed attempts
    error           TEXT,
    payload_json    TEXT                  -- only if prompt logging is on
);
CREATE INDEX idx_req_key_time ON requests(gateway_key_id, created_at);
CREATE INDEX idx_req_time ON requests(created_at);

CREATE TABLE cache (
    hash            TEXT PRIMARY KEY,
    response_json   TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL
);

CREATE TABLE audit_log (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    action          TEXT NOT NULL,        -- key.create, provider.update...
    target          TEXT,
    ip              TEXT,
    created_at      TIMESTAMP NOT NULL
);

CREATE TABLE settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL         -- global cap, log mode, retention...
);
```

**Retention:** a scheduled job deletes `requests` payloads older than the retention setting (default 30 days) and aggregates old rows into a daily summary table, so the database does not grow forever.

---

## 7. Security Design

### 7.1 Key handling

| Secret | How it is stored |
|---|---|
| Gateway key (`sk-gw-...`) | Only the SHA-256 hash is stored. Full key shown once at creation. |
| Provider API key | Encrypted with Fernet using a master key from the environment. Decrypted in memory only when calling the provider. |
| Master encryption key | Environment variable or secret manager. Never in the database or git. |
| User password | argon2 hash |
| TOTP secret | Encrypted with the master key |

Gateway keys are long random values (at least 32 bytes from a secure generator), so a fast hash is safe for them.

### 7.2 Admin login

- Email + password, then TOTP code.
- Session cookie: `HttpOnly`, `Secure`, `SameSite=Strict`, expiry 8 hours idle.
- CSRF protection on all state-changing admin requests.
- Login rate limiting: 5 failed attempts triggers a 15-minute lockout.
- First owner account created through a one-time setup command, not a public page.

### 7.3 Network protection

- The control plane (`/admin`) can be restricted to allowed IPs or a VPN at the reverse proxy.
- Per-key `allowed_ips` is checked on the data plane.
- CORS: the `/v1` endpoints allow no browser origins by default (server-to-server only).

### 7.4 Abuse and cost protection

| Protection | Where enforced |
|---|---|
| Per-key requests per minute | In-memory counter in `limits.py` |
| Per-key daily request limit | Query on `requests` (cached counter) |
| Per-key monthly spend cap | Sum of `cost_usd` this month |
| Per-provider monthly spend cap | Same, grouped by provider |
| Global monthly spend cap | Kill switch: all paid routes stop, free routes continue |
| Max tokens ceiling | Reject or clamp `max_tokens` above a set limit |
| Max request size | Reject bodies above a set size |

### 7.5 Logging rules

- Never log provider keys or gateway keys (log only the prefix).
- Prompt and response content is stored only if the Owner enables Full logging.
- Audit log is append-only from the application's point of view.

---

## 8. Cost Tracking

Cost per request:

```
cost = (prompt_tokens / 1,000,000) * input_price_per_m
     + (completion_tokens / 1,000,000) * output_price_per_m
```

- Prices live in the `models` table and are edited by the Owner. Provider prices change, so the dashboard should show a "prices last checked" date.
- Free-tier models have price 0, but the gateway still counts their requests against rate limits.
- If a provider does not return token counts, estimate them and mark the row as estimated.

---

## 9. Caching

- Cache only when `temperature` is 0 (or unset with a configured default of 0) and the request has no tools or images.
- Cache key: hash of model alias, messages, `max_tokens`, and relevant parameters.
- Time-to-live from settings (default 1 hour).
- Cached responses cost 0 and are marked `cached = 1` in the log.
- Do not share cache entries across gateway keys if prompts may be private. Include the key's user id in the hash.

---

## 10. Error Format

All errors use one consistent shape:

```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Daily limit of 100 requests reached.",
    "request_id": "req_8f3a..."
  }
}
```

Every response, success or failure, includes an `X-Request-Id` header so a problem can be found in the Activity log.

---

## 11. Background Jobs

| Job | Frequency | Purpose |
|---|---|---|
| Provider health probe | Every 60 seconds | Light test call to detect recovery |
| Cache cleanup | Hourly | Delete expired cache rows |
| Log retention | Daily | Remove old payloads, aggregate old rows |
| Spend cap check | After each request, plus hourly | Enforce monthly caps |
| Backup | Daily | Copy the database file somewhere safe |

For v1, run these with a simple in-process scheduler (APScheduler). Move to a separate worker only if needed.

---

## 12. Deployment

```
Internet ──► Caddy (HTTPS, optional IP rules) ──► Uvicorn (FastAPI app)
                                                      │
                                                SQLite file (volume)
```

`docker-compose.yml` runs two containers: the app and Caddy. The database file and `.env` sit on a mounted volume.

**Environment settings**

```
MASTER_KEY=            # Fernet key for encrypting provider keys
SESSION_SECRET=        # signs admin session cookies
DATABASE_URL=sqlite:///data/gateway.db
ADMIN_ALLOWED_IPS=     # optional, comma separated
GLOBAL_MONTHLY_CAP_USD=10
LOG_PROMPTS=metadata   # off | metadata | full
```

**Backups:** copy the database daily and keep the master key stored separately. If the master key is lost, the encrypted provider keys cannot be recovered, though you can simply re-enter them.

**Sizing:** a small VPS (1 CPU, 1 to 2 GB RAM) is enough for personal use. Most request time is spent waiting on providers, not computing.

---

## 13. Testing Plan

| Type | What to test |
|---|---|
| Unit | Cost calculation, limit checks, rule matching, circuit breaker states |
| Integration | Full request flow against a fake provider server that can return 200, 429, 500, timeouts |
| Security | Disabled key rejected, expired key rejected, IP block works, keys never in logs, admin routes need login and 2FA |
| Failure | All providers down returns 503 with details; one provider down falls back correctly |
| Load (light) | 50 concurrent requests do not block the admin API |

The fake provider server is the most valuable test tool. It lets you test fallback behavior without spending money or hitting real rate limits.

---

## 14. Build Order (Backend Milestones)

| Milestone | Deliverable | Done when |
|---|---|---|
| M1 | Project skeleton, config, database, migrations | App starts, tables created |
| M2 | Gateway key auth and `/v1/chat/completions` with one provider | A curl call works with a key |
| M3 | Multiple providers, fallback, circuit breaker | Killing one provider still returns answers |
| M4 | Limits (rpm, daily, spend caps) and cost tracking | Limits block correctly; cost appears in logs |
| M5 | Admin API for keys, providers, activity | Everything manageable without touching the database |
| M6 | Admin login, 2FA, audit log | Admin routes locked down |
| M7 | Caching, routing rules, presets | Rules change which model answers |
| M8 | Background jobs, retention, backups, Docker | Runs unattended on a server |

After M6, the dashboard frontend can be built against a stable API. The three first screens (API Keys, Providers, Activity) need only M5 and M6.

---

## 15. Decisions Needed

1. **Language and framework:** is Python with FastAPI acceptable, or do you prefer Node.js?
2. **Database:** SQLite to start, or PostgreSQL from day one?
3. **Where it runs:** own computer, VPS, or existing hosting? This affects the network lock and backups.
4. **Prompt logging:** off, metadata only, or full?
5. **Global spend cap:** what monthly maximum (in USD) should stop paid routes?
6. **Provider list:** which providers will you add first?
