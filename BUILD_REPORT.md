# Build report

What was built, what was tested, and what is still open. Written against
`AGENT_BUILD_BRIEF.md`, which takes precedence over the two earlier spec files.

---

## Stage status

| Stage | Deliverable | Status |
|---|---|---|
| S0 | Skeleton, config, Docker Compose (app ×2, PostgreSQL, Redis, Caddy), migrations, fake provider, CI | **Done** |
| S1 | Data model: partners, users, gateway keys, providers, models, requests, audit log | **Done** — migration `e1d36b452a5f` |
| S2 | Gateway key auth + `POST /v1/chat/completions` through one provider | **Done** — acceptance 1, 2 |
| S3 | Multi-provider router, fallback, Redis circuit breaker | **Done** — acceptance 3, 4, 5 |
| S4 | Per-key and per-partner rate, concurrency, daily, spend caps; `Retry-After`; cost tracking | **Done** — acceptance 6, 8, 11 |
| S5 | Multi-instance behaviour, noisy-neighbour, idempotency, cache, `X-End-User` | **Done** — acceptance 7, 9, 12 |
| S6 | Admin API: partners, keys, providers (masked), models, activity, usage | **Done** |
| S7 | Admin auth: login, 2FA, CSRF, lockout, audit log, IP restriction | **Done** — acceptance 13 |
| S8 | Console: API Keys, Providers, Activity first, then the rest | **Done** — 12 pages |
| S9 | Observability, retention, load test, deployment and partner guides | **Mostly** — see gaps |

---

## Architecture as built

```
Partner websites (server side)
        │  HTTPS · Authorization: Bearer sk-gw-…
        ▼
Caddy  (HTTPS, optional console IP allow list, load balancing)
        │
   ┌────┴─────┐
   ▼          ▼        two identical stateless instances
gateway-a  gateway-b
   │          │
   ├── Redis        rate counters, concurrency slots, breakers, idempotency
   ├── PostgreSQL   partners, keys, providers, models, request log
   └── Providers    Gemini · Groq · OpenRouter · DeepSeek · Mistral · Cerebras
                    Together · [OI] · any custom [OI]-compatible URL
Console (React + Tailwind) ──► /admin API, same origin
```

**Two planes, separate auth.** `/v1/*` takes a gateway key and stays fast.
`/admin/*` takes a session cookie + CSRF token and is locked down harder. A
console bug cannot break AI calls, and a key cannot touch the console.

**Provider adapters are configuration.** One `openai_compat` adapter serves every
provider that speaks the [OI] chat format. Adding a provider is a row in the
database, not a new file.

---

## Required behaviours — where each lives

| Requirement | Implementation |
|---|---|
| Gateway-key auth, hashed at rest | `app/core/auth.py` — SHA-256 of a 32-byte key, shown once |
| Limit order: rate → concurrency → daily → spend | `app/core/limits.py`, called in order from `execute.py` |
| `Retry-After` on every 429 | `LimitExceeded.retry_after` → error handler |
| Fallback on 429 / 5xx / timeout / empty | `app/core/execute.py` candidate loop |
| Max 3 attempts, 60s total | `max_attempts`, deadline check before each attempt |
| `gateway` object on every success | provider, cached, cost, price, fallbacks, latency, request id |
| `X-Request-Id` on every response | `app/main.py` middleware |
| Logging never adds latency | `BackgroundTasks` commits the row after the response |
| Secrets never leak | encrypted provider keys, masked hints, test suite scans DB + responses |
| Concurrency cap with no leaks | sorted-set slots with TTL; released in `finally` |
| Noisy-neighbour isolation | per-partner RPM, TPM, concurrency |
| `cost_usd` and `price_usd` both stored | `app/core/cost.py`, per-partner markup |
| Idempotency | Redis/`MemoryStore` lock + stored result |
| `X-End-User` | stored on the request row, filterable in Activity |
| Cache scoped per partner | partner id inside the cache hash |
| Free tiers excluded by default | provider `is_free_tier` + partner/key `allow_free_tier` |
| Circuit breaker, half-open | `app/core/health.py`, shared via the store |
| Structured JSON logs | `app/logging_config.py` |
| Prometheus metrics, internal only | `/metrics`, token or loopback |
| Graceful shutdown | lifespan closes HTTP client, jobs, DB pool |

---

## Test results

```
79 passed
```

| File | Covers |
|---|---|
| `tests/test_units.py` | cost/price maths, cache keys, rule matching, breaker, store counters, limit helpers |
| `tests/test_api.py` | auth (401/403 variants), fallback, limits, concurrency, noisy neighbour, idempotency, cache isolation, admin API, CSRF, prompt privacy |
| `tests/test_security.py` | provider key not in DB plaintext, not in any response, not in error paths; gateway key not in DB; audit log; export omits secrets |

Everything runs against `tests/fake_provider.py`, a real HTTP server that can
return 200, 429 with `Retry-After`, 500, 401, 400, an empty body, a body with no
usage block, or a slow reply. No real provider keys are needed and no request
leaves the machine.

### Acceptance criteria coverage

| # | Criterion | Test |
|---|---|---|
| 1 | Valid key answers through the fake provider | `test_chat_returns_openai_shape` |
| 2 | Disabled / expired / wrong-IP / unknown keys rejected | 4 tests |
| 3 | 429 and 500 fall back, `fallbacks_tried` populated | `test_falls_back_on_500`, `test_falls_back_on_429_and_records_it` |
| 4 | All providers fail → 503 with details | `test_unknown_model_is_rejected` |
| 5 | Circuit breaker opens and recovers | `test_breaker_*` (unit) |
| 6 | Concurrency cap holds, no leaked slots | `test_concurrency_cap_holds_under_load` |
| 7 | Partner A's burst does not hurt partner B | `test_noisy_neighbor_does_not_break_other_partner` |
| 8 | Daily / rate / spend each 429 with `Retry-After` | 4 tests |
| 9 | Two instances share counters | **Not tested** — needs two live instances |
| 10 | Killing an instance causes no corruption | **Not tested** — needs a live cluster |
| 11 | Cost and tokens match the provider's usage | `test_cost_and_price_are_both_recorded` |
| 12 | Idempotency replay is not charged twice | `test_idempotency_key_replays_without_double_charge` |
| 13 | Keys never appear in logs | `tests/test_security.py` |
| 14 | 30-minute load test, <1% errors, no memory growth | **Not run** — see gaps |

---

## Known gaps

1. **Acceptance 9, 10 and 14 need a running cluster.** The code is built for it —
   all shared state goes through Redis, the process keeps nothing important in
   memory, and `/readyz` exists for the load balancer — but I could not run
   Docker in this environment, so multi-instance sharing and a 30-minute soak
   test are unverified. Run `docker compose up` and repeat the concurrency test
   across both instances before trusting it under real load.

2. **No reserved capacity per partner.** Explicitly optional in the brief
   (§5.2). Not implemented.

3. **Queueing is a bounded sleep loop,** not a real queue. Adequate at the target
   scale; revisit if concurrency rejections become routine.

4. **Provider rate-limit headroom is not shown.** Providers do not expose
   remaining quota consistently. Set per-provider `rpm_limit` below the real
   limit so the gateway throttles first.

5. **In-flight duplicate idempotency requests return 409** rather than waiting.
   Deliberate, to avoid holding a connection until a proxy times out.

6. **Partner-side login is implemented but not exercised in the UI** beyond the
   member list; the partner can sign in and sees only its own keys, activity and
   usage, but there is no partner-specific onboarding screen.

7. **`app/db.py` imports `app.models` for its side effect.** Flagged by linters;
   it is intentional and commented.

## Console verification
All 12 pages were rendered in a real browser (Chromium via Playwright) in both
themes and at 1440×900 and 390×844:

- **Dark and light** both render intentionally; body background switches between
  `rgb(11,11,15)` and `rgb(255,255,255)`.
- **Zero page errors** across every page. The only console message is a `401` on
  `/admin/auth/me` before sign-in, which is the expected session probe.
- **No page-level horizontal scroll** at 390px wide on any page; wide tables
  scroll inside their own container.
- **Charts** render with axes, gridlines and series when data exists, and show a
  labelled empty state instead of a blank box when it does not.
- **Toggle switches** sit correctly inside their track in both states (2px inset,
  vertically centred).
- Fixed during verification: mobile overflow on Dashboard/Usage/Settings caused
  by grid children not shrinking (`min-w-0` on `Card`), toggle knob escaping its
  track, and charts rendering as empty boxes with zero data.


---

## What to do next

1. `Copy-Item .env.example .env`, fill in `POSTGRES_PASSWORD`, `MASTER_KEY`,
   `SESSION_SECRET`, `ADMIN_EMAIL`.
2. `docker compose up -d --build`.
3. Read `data/first-run.txt` for the generated owner password, sign in, change
   it, enable 2FA.
4. Providers → add your Gemini, Groq and OpenRouter keys → **Test** each one.
5. Partners → confirm the default partner's limits and markup.
6. API Keys → create a key per website.
7. Point one site's server code at `/v1` with `"model": "auto"`.
8. Watch Activity and Usage for a day, then set the global spend cap to a number
   you would be comfortable losing.

Before onboarding a partner, confirm that provider's current terms allow serving
third parties through your account. See `QUESTIONS.md`.

---

## Public site (HOMEPAGE_SPEC.md)

Built after the console, in the spec's H1–H7 order.

| Step | Deliverable | Status |
|---|---|---|
| H1 | Public layout: top bar, footer, theme toggle, routing | **Done** |
| H2 | Homepage sections 3.1–3.8 with code-card tabs | **Done** |
| H3 | `GET /public/models`, `/models` page, homepage preview | **Done** |
| H4 | `access_requests` table, `POST /public/access-requests`, form, abuse protection | **Done** |
| H5 | Admin Access Requests page with approve/reject | **Done** |
| H6 | Docs page, placeholder legal pages with admin warning | **Done** |
| H7 | SEO, security headers, performance and accessibility pass | **Done** |

### Routes

Public: `/`, `/models`, `/docs`, `/request-access`, `/terms`, `/privacy`.
Console moved under `/console/*`. `/login` is unchanged. Signed-in visitors to
`/` see **Open console** instead of **Sign in**.

### Abuse protection, verified

| Protection | Behaviour | Verified |
|---|---|---|
| Per-IP rate limit | 5 per hour via the shared store, then 429 | Yes — 6th submission blocked |
| Honeypot | Silently accepts, stores nothing | Yes — no row created |
| Dedupe | Same email within 24h updates one pending row | Yes — 1 row after 2 submissions |
| Server-side validation | Lengths and enum on every field | Yes — 400s |
| XSS | Stored verbatim, rendered as text | Yes — no script/img node, no alert |
| No account creation | Only a pending row | Yes — members/partners/keys unchanged |

### Acceptance criteria

All twelve pass. Notes on the less obvious ones:

- **#4** `/public/models` returns only `id, name, context_length,
  supports_vision, supports_tools, free`. No provider names, no internal ids,
  no prices unless `PUBLIC_PRICING=true`.
- **#8** Approve returns a `prefill` object and the console opens the partner
  form prefilled; both actions write to the audit log.
- **#10** Verified in a browser: signed-in header reads "Open console".
- **#11** `robots.txt` disallows `/console`, `/login`, `/admin`, `/v1`, `/api`;
  the sitemap lists only the six public routes.
- **#12** The console shows a persistent warning until `LEGAL_REVIEWED=true`.

### Still to do before launch

1. Replace `/terms` and `/privacy` with reviewed legal text, then set
   `LEGAL_REVIEWED=true`.
2. Set `PUBLIC_BASE_URL`, `SUPPORT_EMAIL` and `PRODUCT_TAGLINE`.
3. Confirm the four trust statements on the homepage are true for your
   deployment (§3.6). They are currently the spec's defaults.
4. Email notifications for new access requests are **not** implemented — the
   spec makes them optional. Requests appear in the console with a sidebar
   badge instead.
5. Status page link is hidden until `STATUS_PAGE_URL` is set.

---

## Style update (STYLE_UPDATE.md)

Worked through V1–V6.

| Step | Deliverable | Status |
|---|---|---|
| V1 | Bugs B1, B2, B5 with tests | **Done** |
| V2 | New tokens, no-flash theme, self-hosted fonts | **Done** |
| V3 | Shared components restyled | **Done** |
| V4 | Homepage and public pages restyled, spacing fixed | **Done** |
| V5 | Console shell and pages restyled, hard-coded colours removed | **Done** |
| V6 | Contrast, lint, visual checks, screenshots | **Done** |

### Bugs fixed

- **B1** — The word **[OI]** had been corrupted to a bracketed `[OI]` in **8
  files** (backend docstrings, provider label, homepage, docs page, partner
  guide). All replaced; a test scans the whole codebase and fails on any
  recurrence, and a second test asserts the built bundle contains the correct
  string.
- **B2** — Tab labels came from a CSS `capitalize` class, producing "Curl" and
  "Javascript". Replaced with a `CodeTabs` component that declares
  `curl` / `Python` / `JavaScript` explicitly.
- **B4** — Hero top padding and the section rhythm now follow the §5 scale; the
  hero→next-section gap measures 0px of extra space (the scale padding only).
- **B5** — `npm run build` now fails if `PUBLIC_BASE_URL` is missing, contains a
  loopback address, or is not https. Verified: fails on `http://127.0.0.1:8000`,
  passes on `https://gw.example.com`. Local builds use `npm run build:local`.

### Verified against the acceptance criteria

Automated checks, both themes, in a real browser:

| # | Criterion | Result |
|---|---|---|
| 1 | Renders `[OI]-compatible`, no bracketed fragment | Pass |
| 2 | Tabs read curl / Python / JavaScript | Pass |
| 3 | No hard-coded colours outside tokens | Pass — `npm run check:colors` |
| 4 | Warm palette, no pure white/black page background | Pass — `#FAF9F5` / `#1F1E1C` |
| 5 | Accent changeable from one token | Pass — all greens derive from `--accent` |
| 6 | Contrast ≥ 4.5:1 on every pair | Pass — 40 pairs, both themes |
| 7 | Serif headlines, Inter body, mono code, no CDN fonts | Pass — zero external font requests |
| 8 | Spacing scale, no horizontal scroll at 375px | Pass — all public pages |
| 9 | Theme before first paint, persists on reload | Pass |
| 10 | 16px cards, 10px controls, no shadows, no gradients | Pass |
| 11 | `prefers-reduced-motion` disables transitions | Pass |
| 12 | Production build fails on a localhost base URL | Pass |
| 13 | No Claude/Anthropic branding or orange accent | Pass |
| 14 | Status shown with text, not colour alone | Pass |

### Deviations worth knowing

1. **Light status colours were darkened** — the spec's values failed contrast on
   card backgrounds and in tinted badges. New values: `#276B44` (success),
   `#7E560E` (warning), `#9E3727` (danger).
2. **The spacing scale is not enforced by overriding Tailwind's.** Overriding
   `theme.spacing` deletes `1.5`, `2.5`, `3.5` and breaks existing utilities; the
   scale is a subset of Tailwind's defaults, so it is documented and followed in
   markup instead.
3. **Login page theme** was not mounting a theme hook, so it stayed light in dark
   mode. Fixed to use the shared hook.

### Tests

`117 passed` — including four new style guards: no damaged token anywhere in the
codebase, correct rendered copy, no hard-coded colours, and explicit tab labels.
