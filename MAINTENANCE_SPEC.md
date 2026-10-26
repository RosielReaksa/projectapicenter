# Maintenance Mode: Build Spec

Companion to `AGENT_BUILD_BRIEF.md`, `private-llm-gateway-spec.md`, `backend-design.md`, and `HOMEPAGE_SPEC.md`.

**Precedence:** this file adds a new feature. Where it conflicts with the earlier files, this file wins for maintenance behavior only. Section 12 lists the changes to earlier files.

---

## 1. Purpose

Partners' real users depend on the gateway. When something must be paused, stopped, or fixed, partners must get a **clear, machine-readable answer** instead of timeouts or confusing errors, and the owner must be able to pause things quickly and safely.

Maintenance is **not one switch**. There are four controls, from least to most disruptive.

| Level | Name | Effect | Partner impact |
|---|---|---|---|
| 1 | **Provider pause** | Router stops using one provider; traffic goes to the others | None if other providers can cover the load |
| 2 | **Degraded mode** | Gateway keeps working with restrictions (see 4.2) | Possible slower or limited service |
| 3 | **Partner suspension** | Blocks one partner's keys only | That partner gets 403 |
| 4 | **Full maintenance** | All `/v1` calls get a 503 with retry info | Everyone, until lifted |

Normal software updates must **not** need maintenance mode. With two or more gateway instances, deploy by rolling restart (one instance at a time, with graceful shutdown). Full maintenance is for rare cases such as a database migration that cannot run alongside the old version.

---

## 2. Principles

1. **Never lock the owner out.** Admin login, the admin API, and the dashboard stay reachable in every mode.
2. **Say something clear.** Every blocked request gets a structured response that clients can handle automatically.
3. **Drain, do not cut.** When maintenance starts, requests already in flight are allowed to finish (up to a limit) so paid tokens are not wasted.
4. **Shared state.** All gateway instances must see a change within a couple of seconds, and the state must survive restarts.
5. **Everything is audited.** Who, when, what, and why.
6. **Hard to trigger by accident, easy to undo.** Dangerous switches require confirmation; turning maintenance off is one click.

---

## 3. State and Storage

### 3.1 Where state lives

- **Redis** is the live source of truth read by every instance on each request (cheap key lookups, optionally cached in-process for at most 2 seconds).
- **PostgreSQL** holds the durable copy and the history. On startup, an instance loads state from PostgreSQL into Redis if Redis is empty.
- Every change writes both, then publishes a Redis pub/sub message so instances refresh immediately.

### 3.2 Redis keys

| Key | Value |
|---|---|
| `maint:global` | JSON: `{active, message, started_at, expected_end, allow_ips, started_by}` |
| `maint:degraded` | JSON: `{active, restrictions, message}` |
| `maint:provider:{id}` | `paused` (absent means active) |
| `maint:partner:{id}` | JSON: `{suspended, reason_public, since}` |

### 3.3 Database tables

```sql
CREATE TABLE maintenance_state (
    id            INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    global_active BOOLEAN NOT NULL DEFAULT false,
    global_message TEXT,
    global_started_at TIMESTAMP,
    global_expected_end TIMESTAMP,
    global_allow_ips TEXT,               -- JSON list
    degraded_active BOOLEAN NOT NULL DEFAULT false,
    degraded_config TEXT,                -- JSON
    updated_at    TIMESTAMP NOT NULL DEFAULT now()
);

-- provider pause and partner suspension reuse existing tables:
ALTER TABLE providers ADD COLUMN paused BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE providers ADD COLUMN paused_reason TEXT;
ALTER TABLE partners  ADD COLUMN suspended BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE partners  ADD COLUMN suspended_reason_internal TEXT;
ALTER TABLE partners  ADD COLUMN suspended_reason_public TEXT;

CREATE TABLE maintenance_windows (       -- scheduled maintenance
    id            SERIAL PRIMARY KEY,
    starts_at     TIMESTAMP NOT NULL,
    ends_at       TIMESTAMP NOT NULL,
    banner_text   TEXT NOT NULL,
    auto_enable   BOOLEAN NOT NULL DEFAULT false,
    status        TEXT NOT NULL DEFAULT 'scheduled'
                  CHECK (status IN ('scheduled','active','completed','cancelled')),
    created_by    INTEGER REFERENCES users(id),
    created_at    TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE maintenance_history (
    id            SERIAL PRIMARY KEY,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    user_id       INTEGER REFERENCES users(id),   -- null if done via CLI
    source        TEXT NOT NULL CHECK (source IN ('dashboard','cli','scheduler')),
    level         TEXT NOT NULL CHECK (level IN ('provider','degraded','partner','global')),
    target        TEXT,                            -- provider or partner id
    action        TEXT NOT NULL,                   -- on, off, schedule, cancel
    reason        TEXT
);
```

Every row in `maintenance_history` also writes to the main `audit_log`.

---

## 4. Behavior of Each Level

### 4.1 Provider pause

- Router removes the provider from all candidate lists immediately.
- Requests already in flight to that provider finish normally.
- If no candidates remain for a request, return 503 with code `no_provider_available`.
- Dashboard shows paused providers in a distinct state and warns if remaining providers cannot cover current traffic (compare recent requests-per-minute to remaining capacity settings).
- Pausing differs from the circuit breaker: the breaker is automatic and temporary; a pause is a manual decision and stays until the owner lifts it.

### 4.2 Degraded mode

Owner picks one or more restrictions (all optional, configurable):

- Paid routes only, or free routes only.
- Reduce every partner's concurrency cap by a percentage.
- Reject requests above a max token size.
- Reject low-priority partners (if priority levels exist).

Rejected requests get 429 or 503 with `Retry-After` and code `degraded_mode`. A dashboard banner shows the active restrictions.

### 4.3 Partner suspension

- Suspended partner's keys fail authentication step with HTTP **403** and code `partner_suspended`.
- The `message` shown to the partner is `suspended_reason_public` (owner-written, may be blank, in which case use a generic message: "Access is temporarily suspended. Contact support.").
- Internal reason is never shown to the partner.
- In-flight requests of that partner finish (short limit), new ones are blocked.
- Suspension is stored on the partner so it survives restarts.

### 4.4 Full maintenance

- Check happens **first** in the data plane, before key lookup, so it costs almost nothing.
- Response for blocked `/v1/*` calls:

```http
HTTP/1.1 503 Service Unavailable
Retry-After: 900
Content-Type: application/json
X-Request-Id: req_...

{
  "error": {
    "code": "maintenance",
    "message": "We are upgrading. Back soon.",
    "expected_end": "2025-01-01T15:30:00Z",
    "request_id": "req_..."
  }
}
```

- `Retry-After` is seconds until `expected_end` if set (minimum 30), otherwise 300.
- `expected_end` is omitted if not set.
- `/health` returns 200 with body `{"status":"maintenance", ...}` so monitors know it is intentional. (A separate `/health/ready` used by load balancers may return 503 to drain traffic from an instance; keep these two distinct and document them.)
- **Allowed through:** requests from IPs in `allow_ips` (for owner testing), admin routes, `/health`, and public pages.
- **Drain on start:** after the owner starts maintenance, new requests are blocked immediately; in-flight requests may finish for up to `drain_timeout` (default 60 seconds, configurable). The dashboard shows a live count of in-flight requests during the drain, and the status becomes "Maintenance active, drained" when the count reaches zero or the timeout passes.
- **Never block:** `/admin/*`, `/login`, the dashboard assets, `/health`, `/public/*`.

---

## 5. Admin Dashboard: Maintenance Page

Route: `/console/maintenance` (Owner only). Add to the sidebar with a status dot: green normal, yellow degraded or something paused, red maintenance active.

### 5.1 Layout

```
Maintenance
────────────────────────────────────────────────────────
[ Status banner ]
● All systems normal
● Degraded: 1 provider paused
● MAINTENANCE ACTIVE since 14:02  (drained)

────────────────────────────────────────────────────────
Full maintenance                                [ OFF | ON ]
Stops all API calls. Partners get a 503 with a retry time.
   Message to partners:   [ text, max 300 chars              ]
   Expected end time:     [ date and time ]   (optional)
   Allow my current IP:   [x]
   Reason (internal):     [ required ]
                                       [ Start maintenance ]

────────────────────────────────────────────────────────
Degraded mode                                   [ OFF | ON ]
   [ ] Paid routes only        [ ] Free routes only
   [ ] Reduce concurrency by [ 50 ]%
   [ ] Max tokens per request [ 2000 ]

────────────────────────────────────────────────────────
Providers
   Name          Status           Control
   Gemini        ● Healthy        [ Active | Paused ]
   Groq          ● Rate limited   [ Active | Paused ]
   OpenRouter    ● Healthy        [ Active | Paused ]

────────────────────────────────────────────────────────
Partners
   Name          Status           Control
   Dubber site   ● Active         [ Suspend ]
   Partner B     ● Active         [ Suspend ]

────────────────────────────────────────────────────────
Scheduled maintenance                       [ + Schedule ]
   Start, end, banner text, auto-enable toggle

────────────────────────────────────────────────────────
History
   Time    Who    Level    Action    Reason
```

### 5.2 Interaction rules

- **Full maintenance ON:** red styled card; clicking Start opens a confirm dialog: `This will block all partner requests. Type MAINTENANCE to confirm.` The button stays disabled until the text matches. Reason is required.
- **Full maintenance OFF:** one click, no typed confirmation. Before lifting, show a short pre-check list (database reachable, Redis reachable, at least one provider healthy) with a warning if any fails; the owner may still proceed.
- **Provider pause and resume:** immediate toggle; pausing shows a note if remaining capacity looks too low. Reason optional.
- **Partner suspension:** confirm dialog with two fields: internal reason (required) and public message (optional). Resume is one click.
- **Global red bar** at the top of every console page while full maintenance is active, and a yellow bar for degraded mode or paused providers, linking to this page.
- Show who changed what and when, using the history table.
- Dashboard tolerates Redis being down by showing the last known state from PostgreSQL with a warning.

---

## 6. Scheduled Maintenance

- Owner creates a window: start, end, banner text, and `auto_enable` (whether maintenance turns on automatically at start and off at end).
- **Public banner:** from 24 hours before start (configurable), show a banner on the homepage and docs pages: `Scheduled maintenance: {start} to {end}. {banner_text}` Times shown in UTC with the visitor's local time on hover or in a tooltip.
- **Partner notice:** the `GET /v1/usage` and `GET /health` responses include an `upcoming_maintenance` field so partner systems can read it programmatically. Email notification to partners is optional and controlled by a setting (default off, since email may not be configured).
- A scheduler job runs every 30 seconds in exactly one instance (use a Redis lock) to start and end windows. If the scheduler misses a start, the next run applies it. If the end time passes while an owner has manually extended maintenance, do not auto-disable.
- Owner can cancel or edit a window before it starts. Editing an active window only allows changing the end time and message.

---

## 7. Emergency CLI

For use when the dashboard is unavailable. Runs on the server, reads the same configuration, and writes the same Redis and PostgreSQL state.

```
gateway maintenance status
gateway maintenance on  --message "Back soon" --until "2025-01-01T15:30:00Z" --allow-ip 1.2.3.4 --reason "DB migration"
gateway maintenance off
gateway provider pause  <name> --reason "..."
gateway provider resume <name>
gateway partner suspend <name> --public "..." --reason "..."
gateway partner resume  <name>
```

- Every CLI action is written to `maintenance_history` with `source = cli`.
- CLI requires shell access on the server; it does not use the web login.
- If Redis is unreachable, the CLI writes to PostgreSQL and warns that instances will pick up the change when Redis recovers (instances also re-read PostgreSQL every 30 seconds as a safety net).

---

## 8. Admin API

All under `/admin`, Owner only, behind login and 2FA, and **exempt from maintenance blocking**.

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/maintenance` | Full state: global, degraded, paused providers, suspended partners, upcoming windows |
| POST | `/admin/maintenance/global/start` | Body: message, expected_end, allow_ips, reason |
| POST | `/admin/maintenance/global/stop` | Body: reason (optional) |
| POST | `/admin/maintenance/degraded` | Body: on/off and restrictions |
| POST | `/admin/providers/{id}/pause` | Body: reason |
| POST | `/admin/providers/{id}/resume` | |
| POST | `/admin/partners/{id}/suspend` | Body: reason_internal, reason_public |
| POST | `/admin/partners/{id}/resume` | |
| GET / POST | `/admin/maintenance/windows` | List or create scheduled windows |
| PATCH / DELETE | `/admin/maintenance/windows/{id}` | Edit or cancel |
| GET | `/admin/maintenance/history` | Paginated history |

Public (no auth), read-only:

| Method | Path | Purpose |
|---|---|---|
| GET | `/public/status` | `{state: normal | degraded | maintenance, message, expected_end, upcoming_window}`; cached for a few seconds; exposes no internal details such as provider names |

---

## 9. Public Maintenance Experience

- **Website pages:** during full maintenance, public pages stay up (homepage, docs, models). Show a red-toned banner with the message and end time. The `/console` login page also shows the banner but stays usable.
- **Dedicated maintenance page:** only used if the whole site is down for deployment reasons (the reverse proxy serves a static page). Plain design in the site's dark style: logo, headline `We'll be right back`, the message, expected end time if known, and the support email. No animations, no external scripts. The proxy must be able to serve this static page even when the app is down.
- **Optional status page** (`/status`): shows state from `/public/status` and recent maintenance windows. Hidden until monitoring exists (see `HOMEPAGE_SPEC.md`).

---

## 10. Partner Guidance (Add to `/docs` and Integration Guide)

Document for partners:

1. A 503 with code `maintenance` means the service is intentionally paused. Do not retry rapidly: wait at least the `Retry-After` seconds.
2. A 403 with `partner_suspended` means contact support; retrying will not help.
3. Show end users a friendly message ("Our AI service is temporarily unavailable"), and queue or pause jobs instead of failing them permanently.
4. Check `upcoming_maintenance` in `/v1/usage` or `/public/status` to plan around scheduled windows.
5. Always send `Idempotency-Key` on retries so repeated requests after maintenance are not double processed.

---

## 11. Failure Handling

| Situation | Behavior |
|---|---|
| Redis unreachable, maintenance state unknown | Use last known state cached in the instance (max 30 seconds old). After that, **fail safe**: refuse requests that would incur provider cost (503 code `state_unavailable`), allow `/health` and admin routes. Alert the owner. This default is configurable via `MAINT_FAIL_MODE=closed|open`. |
| Instances disagree briefly | Acceptable for up to a few seconds due to propagation; pub/sub plus 2-second cache bound it |
| Owner starts maintenance and the dashboard session expires | Use the CLI |
| Maintenance left on by mistake | Red banner on every console page; optional daily reminder email if enabled; if `expected_end` passed more than 1 hour ago, show a prominent "maintenance overdue" warning |
| Scheduler instance dies | Redis lock expires; another instance takes over within 30 seconds |

---

## 12. Changes to Earlier Files

| File | Change |
|---|---|
| `backend-design.md` | Data-plane flow gets a new first step: check `maint:global` before authentication. Router candidate filter also excludes paused providers. Auth step also rejects suspended partners. |
| `AGENT_BUILD_BRIEF.md` | Add stage **S7b** after S7: maintenance backend (state, checks, endpoints, CLI). Add the dashboard Maintenance page to stage S8, after API Keys, Providers, and Activity. |
| `private-llm-gateway-spec.md` | Add **Maintenance** to the dashboard sidebar and to Settings danger-zone references. |
| `HOMEPAGE_SPEC.md` | Homepage and docs read `/public/status` for the banner. Sitemap and routes unchanged. |

---

## 13. Build Order

| Step | Deliverable |
|---|---|
| M-1 | DB migrations, Redis keys, state load, pub/sub refresh, history and audit writes |
| M-2 | Data plane checks: global maintenance first, provider pause in router, partner suspension in auth, degraded restrictions; structured 503 and 403 responses |
| M-3 | Drain behavior and in-flight tracking; `/health` and `/health/ready` behavior |
| M-4 | Admin API endpoints and `/public/status` |
| M-5 | CLI commands |
| M-6 | Dashboard Maintenance page, status dot, global bars, confirm dialogs, history table |
| M-7 | Scheduled windows, scheduler with Redis lock, public banner |
| M-8 | Partner docs updates, failure-mode tests, final acceptance run |

---

## 14. Acceptance Criteria

1. With full maintenance on, any `/v1/*` call returns 503, code `maintenance`, correct `Retry-After`, the message, and `X-Request-Id`, even with an invalid key.
2. Admin login, admin API, dashboard, `/health`, and public pages remain reachable during full maintenance.
3. An IP in `allow_ips` can still make `/v1` requests during maintenance; others cannot.
4. A request already in flight when maintenance starts completes successfully; new requests are blocked immediately. The drain counter reaches zero or times out at the configured limit.
5. With two gateway instances, toggling maintenance in the dashboard affects both within 3 seconds.
6. Restarting all instances during maintenance keeps maintenance on.
7. Pausing a provider stops new traffic to it within 3 seconds; requests succeed via other providers; no failed requests caused by the pause itself.
8. Pausing the only provider yields 503 `no_provider_available`, not a hang.
9. Suspending a partner blocks only that partner's keys (403 `partner_suspended`) and does not affect others; the internal reason never appears in any response.
10. Degraded mode restrictions are enforced and responses include `Retry-After` and code `degraded_mode`.
11. Dashboard requires typing `MAINTENANCE` to start full maintenance, requires a reason, and turning it off needs one click.
12. Every change from the dashboard, CLI, and scheduler appears in `maintenance_history` and `audit_log` with the correct source.
13. CLI works with the web app stopped and changes take effect when instances are running.
14. A scheduled window with `auto_enable` starts and ends within 60 seconds of the set times; only one instance runs the scheduler at a time.
15. The public banner appears on the homepage and docs 24 hours before a window and disappears after it ends.
16. With Redis stopped, the gateway behaves according to `MAINT_FAIL_MODE` (default closed for provider-cost requests) and the dashboard shows a warning rather than crashing.
17. A load test running during a rolling deploy (one instance at a time) shows no failed requests beyond those in flight during the restart of an instance, and graceful shutdown completes in-flight requests.

---

## 15. Instructions for the Building Agent

1. Read `AGENT_BUILD_BRIEF.md` first, then this file. Apply section 12 changes to the earlier files' plans.
2. Implement the data-plane checks (M-2) with minimal latency: one Redis read per request at most, with the short in-process cache.
3. Use the fake provider server from the brief for all tests of pause, drain, and fallback.
4. Never let maintenance logic block `/admin`, `/login`, `/health`, or `/public`. Add tests that prove this.
5. Do not hard-code messages or durations; use configuration with the defaults given here.
6. If something is unclear, write it to `QUESTIONS.md` and continue with the safest assumption.

---

## 16. Open Items (Owner to Confirm; Agent Uses Defaults Until Answered)

| Item | Default |
|---|---|
| Drain timeout | 60 seconds |
| Fail mode when Redis is down | closed for provider-cost requests |
| Email notifications to partners | Off |
| Banner lead time before scheduled window | 24 hours |
| Default `Retry-After` when no end time set | 300 seconds |
| Degraded mode restrictions offered | The four listed in 4.2 |
| Who may use maintenance controls | Owner only |
| Public status page | Hidden until monitoring exists |
| Time display | UTC with local time tooltip |
