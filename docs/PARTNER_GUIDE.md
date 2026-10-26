# Partner integration guide

Give this to each partner. It is also available in the console under **Docs**.

---

## 1. What you get

One HTTPS endpoint that answers like the OpenAI chat API, backed by several
providers with automatic fallback. You do not manage provider accounts.

```
Base URL:  https://gw.example.com/v1
Auth:      Authorization: Bearer sk-gw-...
```

## 2. First request

```bash
curl https://gw.example.com/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

`"model": "auto"` lets the gateway pick the cheapest healthy model. To pin a
model, use an id from `GET /v1/models`.

## 3. Call it from the server only

Never put a gateway key in browser JavaScript, a mobile app, or a public repo.
Anyone who reads the key can spend your quota. Your server holds the key; your
frontend calls your server.

## 4. Handle these statuses

| Status | Meaning | What to do |
|---|---|---|
| 200 | OK | Use `choices[0].message.content` |
| 400 | Bad request | Fix the request; do not retry |
| 401 | Bad or missing key | Fix the key; do not retry |
| 403 | Key disabled, expired, or IP blocked | Contact the owner |
| 429 | Limit reached | Wait for `Retry-After` seconds, then retry |
| 503 | Every provider failed | Retry with backoff |

Every response carries `X-Request-Id`. Quote it when reporting a problem.

## 5. Retry correctly

Retry **only** 429 and 5xx. Use exponential backoff with jitter, and always
honour `Retry-After` when present.

```python
import random, time, httpx

def call(payload, key, gateway, attempts=4):
    for attempt in range(attempts):
        response = httpx.post(
            f"{gateway}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=90,
        )
        if response.status_code < 400:
            return response.json()
        if response.status_code not in (429, 500, 502, 503, 504):
            response.raise_for_status()
        wait = float(response.headers.get("retry-after", 0)) or min(2 ** attempt, 30)
        time.sleep(wait + random.uniform(0, 0.5))
    raise RuntimeError("gateway unavailable")
```

### Do not pay twice

Send an `Idempotency-Key` header. A replay of the same key returns the original
result instead of running — and charging — the request again.

```python
headers = {"Idempotency-Key": f"job-{job_id}"}
```

Keys are scoped to your partner, so they cannot collide with anyone else's.

## 6. Per-end-user tracking

Send `X-End-User` with a stable id for the person the request is for. Usage and
per-user limits are then attributed correctly.

```python
headers = {"X-End-User": "user-8842"}
```

Use an internal id, not an email address.

## 7. Your limits

Your partner account has:

- **Concurrency** — requests in flight at once
- **Requests per minute**
- **Tokens per minute**
- **Daily requests** (optional)
- **Monthly spend cap**

Ask the owner for your values. When you exceed a limit you get `429` with
`Retry-After`. Exceeding concurrency is the usual cause of a 429 under load, so
queue work server-side rather than firing everything at once.

## 8. Content

Send text. Transcripts are the intended workload.

- Do not send content you are not allowed to process.
- The gateway stores **metadata only** by default: counts and timings, not your
  text. Ask the owner if full logging is enabled before sending sensitive data.
- Free-tier providers are disabled for partner traffic unless the owner enables
  them, because some free tiers train on prompts.

## 9. Uptime

The service runs on a single deployment with no SLA unless the owner states one
in writing. Build your integration to degrade gracefully: time out, retry, and
have a fallback path.
