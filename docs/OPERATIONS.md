# Gateway operations

## Daily use

| Task | Where |
|---|---|
| Add a provider key | Providers → Add provider |
| Create a key for a website | API Keys → Create key |
| See what a partner is spending | Usage & Cost → By partner |
| Investigate one request | Activity → click a row |
| Stop all paid spend now | Settings → Global monthly spend cap → set to current spend |
| Kill one partner | Partners → Deactivate (disables all its keys) |
| Kill one key | API Keys → Disable |
| Free a stuck provider | Settings → Reset circuit breakers |

## What happens when limits are hit

| Limit | Response | `Retry-After` |
|---|---|---|
| Requests per minute | 429 `rate_limit_exceeded` | 60 |
| Tokens per minute | 429 `token_rate_limit_exceeded` | 60 |
| Concurrency cap | 429 `concurrency_limit_exceeded` | 1 |
| Daily requests | 429 `daily_limit_reached` | until UTC midnight |
| Key or partner spend cap | 429 `spend_cap_reached` | none |
| Global cap | paid models skipped; free only, else 503 | — |

## Backups

Two things must be backed up, and they must be backed up **separately**:

1. **PostgreSQL** — `pg_dump` on a schedule.
2. **`MASTER_KEY`** — from the environment or `data/master.key`.

If you lose the master key, provider keys cannot be decrypted. You can recover
by re-entering each provider key; nothing else is lost.

```bash
docker compose exec -T postgres pg_dump -U gateway gateway | gzip > backup-$(date +%F).sql.gz
```

## Redis down: what to expect

The gateway fails **open** on rate limits and **closed** on spend caps:

- Rate, TPM and concurrency counters become per-instance, so a partner with two
  instances effectively gets twice its limit. `GET /health` reports
  `checks.shared_state: false` and the dashboard shows `memory`.
- Spend caps are read from PostgreSQL, so money limits keep working.
- `/health` returns `degraded`.

Restore Redis promptly, or run a single instance until it is back.

## Log rotation and retention

- Request rows older than `RETENTION_DAYS` (default 30) are summarised into
  `daily_summary` and deleted at 03:30 UTC.
- Cached responses are pruned every 30 minutes.
- Provider health is probed every 5 minutes.
- Container logs are capped at 10 MB × 3 files by docker-compose.

## Scaling

The gateway is stateless. To add capacity:

1. Add another `gateway-*` service to `docker-compose.yml`, or raise `--workers`.
2. Make sure `REDIS_URL` points at the shared Redis. Without it, limits are
   per-instance and are **not** safe for multi-instance use.

Sizing: 1 CPU / 1 GB handles a few hundred requests per minute easily, because
almost all wall time is spent waiting on providers.

## Upgrading

```bash
docker compose pull
docker compose up -d
```

Schema changes ship as Alembic migrations. For a database created by an older
version, run:

```bash
docker compose run --rm gateway-a alembic upgrade head
```
