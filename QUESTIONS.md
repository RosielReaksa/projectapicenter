# Open questions and assumptions

Per `AGENT_BUILD_BRIEF.md` §9.6: unclear decisions are listed here, and the
safest assumption was used in the meantime. Nothing here blocks use today.

## Answered by defaults

| # | Question | Assumption used | Where to change |
|---|---|---|---|
| 1 | Peak load | Designed for 50 in flight / 20 rps; tuned via `--workers`, DB pool and instance count | `docker-compose.yml`, `.env` |
| 2 | Which paid providers first | Gemini Flash, Groq, OpenRouter seeded as configuration; all editable | Providers page |
| 3 | Global monthly cap | `$50` placeholder | Settings → spend cap |
| 4 | Prompt logging | Metadata only | Settings → privacy |
| 5 | Dashboard language | English only | — |
| 6 | Partner markup | Single multiplier per partner, default ×1.0 | Partners page |
| 7 | Number of partners at launch | 1 to 3; a `Default` partner is created on first boot | Partners page |

## Still genuinely open

1. **Reselling terms.** The brief notes that serving third parties through a
   provider account may be restricted. Each provider's current terms must be
   confirmed by the owner before onboarding partners. This is a business risk,
   not something the code can enforce. The `may_train_on_prompts` flag exists so
   the owner can mark providers that are unsafe for partner data, but nothing
   blocks traffic automatically.

2. **Provider capacity headroom.** The dashboard shows per-provider health and
   monthly spend against each provider's cap, but not remaining provider-side
   rate-limit headroom, because providers do not expose it in a standard way.
   If this matters, the practical approach is to set `rpm_limit` per provider in
   the console below the provider's real limit, so the gateway throttles before
   the provider does.

3. **Reserved capacity per partner** (brief §5.2, "can be left off in v1").
   Not implemented. Concurrency caps and rate limits are enforced, but there is
   no reserved pool. Add it when one partner's traffic must be guaranteed
   regardless of others.

4. **Queueing on over-capacity.** Implemented as a short bounded wait
   (`queue_timeout_ms`) when a partner is set to `queue`. It is a per-request
   sleep loop, not a real queue. Fine at the target scale; replace with a
   proper queue if concurrency limits start being hit routinely.

5. **Streaming.** Explicitly out of scope for v1 (brief §1). Requests with
   `stream: true` receive a clear `400 streaming_not_supported` rather than a
   silently buffered reply.

6. **Invoicing and payments.** Out of scope for v1, as instructed. The data
   needed for invoicing is recorded on every request: `cost_usd` (what we pay)
   and `price_usd` (what the partner is charged).

7. **Redis-down policy.** The brief recommends "fail closed for spend caps, fail
   open with conservative local limits for rate limits, and alert". Implemented
   as: spend caps read from PostgreSQL so they keep working; rate, TPM and
   concurrency counters fall back to per-instance memory; `GET /health` reports
   `checks.shared_state: false` and the console shows `memory`. There is no
   separate alerting hook — wire `/health` or `/metrics` into your monitoring.

8. **Idempotency for in-flight duplicates.** A replay of a *completed* request
   returns the stored result. A replay that arrives while the original is still
   running returns `409 idempotency_in_progress` instead of waiting. This is
   deliberate: holding the connection risks a proxy timeout. If partners would
   rather wait, that is a small change in `app/api/v1_chat.py`.

9. **Free-tier default for partner traffic.** Disabled by default (brief §4.6).
   A partner or key can opt in. The seeded free-tier providers are flagged
   `may_train_on_prompts` conservatively; adjust per provider as you confirm
   terms.

10. **TOTP for partner logins.** Available to every console user, but not
    forced. Only the owner is strongly recommended to enable it.

---

## Style update (STYLE_UPDATE.md)

| # | Question | Assumption used |
|---|---|---|
| S1 | Accent colour | Deep green `#2F6F5E` (light) / `#5FB39B` (dark). Two alternatives (muted blue, warm plum) are commented out in `index.css`. |
| S2 | Serif font | Newsreader, self-hosted via `@fontsource/newsreader`. |
| S3 | Default theme | Follows `prefers-color-scheme`, applied by an inline script before first paint. |
| S4 | Dashboard page titles in serif | Yes — only page titles; all data stays sans-serif/mono. |
| S5 | Hero background | Flat `--bg`, no gradient. |
| S6 | Product logo | Placeholder letter mark in `frontend/public/favicon.svg`, replaceable via one file. |

### Notes on decisions the spec left open

- **Light-theme status colours were darkened.** The spec's `--success`/`--warning`/
  `--danger` values failed 4.5:1 on card backgrounds and in tinted badges. Changed
  to `#276B44`, `#7E560E`, `#9E3727`, which pass everywhere. Dark theme values
  were already compliant and are unchanged.
- **The §5 spacing scale is not enforced by overriding Tailwind's.** Overriding
  `theme.spacing` deletes `1.5`, `2.5`, `3.5`, `5`, `7`, `9` and silently breaks
  existing utilities. The scale is a subset of Tailwind's defaults, so it is
  documented and followed in markup instead.
- **`PUBLIC_BASE_URL` check runs at build time.** It reads the same environment
  variable the backend uses. Local builds need `npm run build:local` (sets
  `VITE_ALLOW_LOCAL_BASE_URL=true`).
- **Login page theme.** It was not mounting a theme hook, so it stayed light in
  dark mode. It now uses the shared `usePublicTheme`.
