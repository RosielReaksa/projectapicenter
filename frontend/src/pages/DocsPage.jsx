import { useState } from 'react'
import { Card, CopyButton } from '../components/ui'
import { useToast } from '../lib/toast'

const origin = () => window.location.origin

const ERRORS = [
  ['400', 'invalid_request', 'The request body is malformed, or the input was rejected.'],
  ['401', 'invalid_api_key', 'Missing, malformed or unknown API key.'],
  ['403', 'key_disabled / key_expired / ip_not_allowed', 'The key cannot be used from here.'],
  ['429', 'rate_limit_exceeded', 'RPM, TPM, concurrency, daily or spend cap reached. Honour Retry-After.'],
  ['503', 'no_provider_available', 'Every candidate provider failed. The body lists what was tried.'],
]

export default function DocsPage() {
  const toast = useToast()
  const [tab, setTab] = useState('python')

  const samples = {
    python: `import httpx, os

GATEWAY = os.environ["GATEWAY_URL"]        # e.g. https://gw.example.com
KEY = os.environ["GATEWAY_KEY"]            # sk-gw-...

response = httpx.post(
    f"{GATEWAY}/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {KEY}",
        "X-End-User": "end-user-123",       # optional, for per-user tracking
        "Idempotency-Key": "job-8821",      # optional, makes retries free
    },
    json={
        "model": "auto",
        "messages": [{"role": "user", "content": "Summarise this transcript…"}],
        "max_tokens": 800,
    },
    timeout=90,
)

response.raise_for_status()
data = response.json()
print(data["choices"][0]["message"]["content"])
print(data["gateway"]["provider"], data["gateway"]["cost"])`,
    curl: `curl ${origin()}/v1/chat/completions \\
  -H "Authorization: Bearer $GATEWAY_KEY" \\
  -H "Content-Type: application/json" \\
  -H "X-End-User: end-user-123" \\
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello"}]
  }'`,
    retry: `import time, random, httpx

def call_gateway(payload, key, attempts=4):
    """Retry only what is safe to retry: 429 and 5xx."""
    for attempt in range(attempts):
        response = httpx.post(
            f"{GATEWAY}/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=90,
        )
        if response.status_code < 400:
            return response.json()

        if response.status_code not in (429, 500, 502, 503, 504):
            response.raise_for_status()   # a real client error, do not retry

        wait = float(response.headers.get("retry-after", 0)) or min(2 ** attempt, 30)
        time.sleep(wait + random.uniform(0, 0.5))   # jitter

    raise RuntimeError("gateway unavailable after retries")`,
  }

  return (
    <div className="space-y-5">
      <header>
        <h1 className="page-title">Docs</h1>
        <p className="text-xs text-muted mt-1">
          Everything a partner needs to call the gateway. Server-side only.
        </p>
      </header>

      <Card title="Quickstart">
        <ol className="space-y-2 text-sm list-decimal pl-5">
          <li>Ask the owner for a gateway key (<code className="mono">sk-gw-…</code>).</li>
          <li>
            Store it in your server's environment variables. Never ship it to a browser, mobile
            app, or public repository.
          </li>
          <li>
            Point your HTTP client at <code className="mono">{origin()}/v1</code> and send the key
            as a bearer token.
          </li>
          <li>
            Send <code className="mono">"model": "auto"</code> and let the gateway choose the
            cheapest healthy model, or name a specific model.
          </li>
        </ol>
      </Card>

      <Card title="Authentication">
        <p className="text-sm text-muted">
          Every request needs an{' '}
          <code className="mono">Authorization: Bearer sk-gw-…</code> header. Keys are scoped to
          one partner, can be limited by model, IP, requests per minute, concurrency and monthly
          spend, and can be revoked instantly.
        </p>
      </Card>

      <Card title="Endpoints" bodyClass="p-0">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th">Method</th>
              <th className="th">Path</th>
              <th className="th">Purpose</th>
            </tr>
          </thead>
          <tbody>
            {[
              ['POST', '/v1/chat/completions', 'Chat completion. OpenAI-compatible.'],
              ['GET', '/v1/models', 'Models this key may use.'],
              ['GET', '/v1/usage', 'Usage and limits for the calling key.'],
              ['GET', '/health', 'Gateway and provider status. No auth required.'],
            ].map(([method, path, purpose]) => (
              <tr key={path}>
                <td className="td mono text-xs">{method}</td>
                <td className="td mono text-xs">{path}</td>
                <td className="td text-xs">{purpose}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card
        title="Code samples"
        action={
          <div className="flex gap-1">
            {Object.keys(samples).map((key) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`px-2 h-7 rounded text-xs ${
                  tab === key ? 'bg-accent-soft text-accent font-medium' : 'text-muted hover:text-fg'
                }`}
              >
                {key}
              </button>
            ))}
          </div>
        }
        bodyClass="p-0"
      >
        <div className="relative">
          <div className="absolute right-2 top-2 z-10">
            <CopyButton
              value={samples[tab]}
              label="Copy"
              onCopied={() => toast.success('Copied')}
            />
          </div>
          <pre className="p-4 overflow-x-auto">
            <code className="mono text-xs whitespace-pre">{samples[tab]}</code>
          </pre>
        </div>
      </Card>

      <Card title="Response additions">
        <p className="text-sm text-muted mb-2">
          Standard completion fields, plus a <code className="mono">gateway</code> object:
        </p>
        <pre className="rounded-md border border-border bg-surface p-3 overflow-x-auto">
          <code className="mono text-xs">{`{
  "gateway": {
    "provider": "gemini",
    "model": "Gemini 2.5 Flash",
    "cached": false,
    "cost": 0.000043,
    "price": 0.000054,
    "fallbacks_tried": [{ "provider": "groq", "status": 429, "error": "rate limited" }],
    "latency_ms": 812,
    "request_id": "req_8f3a…"
  }
}`}</code>
        </pre>
      </Card>

      <Card title="Errors" bodyClass="p-0">
        <table className="w-full text-sm">
          <thead>
            <tr>
              <th className="th">Status</th>
              <th className="th">Code</th>
              <th className="th">Meaning</th>
            </tr>
          </thead>
          <tbody>
            {ERRORS.map(([status, code, meaning]) => (
              <tr key={status}>
                <td className="td mono text-xs">{status}</td>
                <td className="td mono text-xs">{code}</td>
                <td className="td text-xs">{meaning}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Rules for partners">
        <ul className="space-y-2 text-sm list-disc pl-5 text-muted">
          <li>
            Call the gateway only from server code. A browser-held key can be copied by anyone who
            opens developer tools.
          </li>
          <li>
            Retry <code className="mono">429</code> and <code className="mono">5xx</code> with
            exponential backoff and jitter, and honour{' '}
            <code className="mono">Retry-After</code>. Never retry{' '}
            <code className="mono">4xx</code> other than 429.
          </li>
          <li>
            Send an <code className="mono">Idempotency-Key</code> on retries so a retried request is
            not charged twice.
          </li>
          <li>
            Send <code className="mono">X-End-User</code> so usage can be attributed per end user.
          </li>
          <li>
            Do not send data you would not want stored unless prompt logging is set to full. Ask the
            owner which mode is active.
          </li>
        </ul>
      </Card>
    </div>
  )
}
