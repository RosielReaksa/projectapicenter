import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useSite } from '../../lib/site'
import { Section } from '../../components/PublicLayout'
import { CopyButton } from '../../components/ui'
import { CodeTabs } from '../../components/CodeTabs'

const ERRORS = [
  ['400', 'invalid_request', 'The request body is malformed, or the input was rejected.'],
  ['401', 'invalid_api_key', 'Missing, malformed, or unknown API key.'],
  ['403', 'key_disabled / key_expired / ip_not_allowed', 'The key cannot be used from here.'],
  ['429', 'rate_limit_exceeded', 'Rate, token, concurrency, daily, or spend limit reached.'],
  ['503', 'no_provider_available', 'Every candidate model failed. The body lists what was tried.'],
]

export default function PublicDocsPage() {
  const site = useSite()
  const [tab, setTab] = useState('python')
  const base = site.api_base_url || `${window.location.origin}/v1`

  const samples = {
    python: `import httpx, os

GATEWAY = "${base}"
KEY = os.environ["GATEWAY_KEY"]        # sk-gw-...

response = httpx.post(
    f"{GATEWAY}/chat/completions",
    headers={
        "Authorization": f"Bearer {KEY}",
        "X-End-User": "end-user-123",   # optional
        "Idempotency-Key": "job-8821",  # optional, makes retries free
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
    curl: `curl ${base}/chat/completions \\
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
            f"{GATEWAY}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=90,
        )
        if response.status_code < 400:
            return response.json()

        if response.status_code not in (429, 500, 502, 503, 504):
            response.raise_for_status()   # a real client error: do not retry

        wait = float(response.headers.get("retry-after", 0)) or min(2 ** attempt, 30)
        time.sleep(wait + random.uniform(0, 0.5))   # jitter

    raise RuntimeError("gateway unavailable after retries")`,
  }

  return (
    <Section className="py-12 sm:py-16">
      <div className="grid gap-10 lg:grid-cols-[200px_1fr]">
        <nav aria-label="Contents" className="lg:sticky lg:top-20 lg:self-start">
          <p className="text-xs font-medium text-muted uppercase tracking-wide mb-3">On this page</p>
          <ul className="space-y-1.5 text-sm">
            {[
              ['quickstart', 'Quickstart'],
              ['authentication', 'Authentication'],
              ['chat', 'Chat completions'],
              ['models', 'Models'],
              ['limits', 'Limits'],
              ['errors', 'Errors'],
              ['retries', 'Retries'],
            ].map(([id, label]) => (
              <li key={id}>
                <a href={`#${id}`} className="text-muted hover:text-fg">
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">Documentation</h1>
          <p className="mt-2 text-sm text-muted">
            Everything you need to call the gateway. Server-side only.
          </p>

          <Doc id="quickstart" title="Quickstart">
            <ol className="space-y-2 text-sm list-decimal pl-5 text-muted">
              <li>
                <Link to="/request-access" className="text-accent hover:underline">
                  Request access
                </Link>{' '}
                and wait for approval.
              </li>
              <li>You receive an API key (<code className="mono">sk-gw-…</code>) by email.</li>
              <li>
                Store it in your server's environment variables. Never ship it to a browser,
                mobile app, or public repository.
              </li>
              <li>
                Send your first request to <code className="mono">{base}</code>.
              </li>
            </ol>
          </Doc>

          <Doc id="authentication" title="Authentication">
            <p className="text-sm text-muted">
              Every request needs an{' '}
              <code className="mono">Authorization: Bearer sk-gw-…</code> header. Keys are scoped
              to one partner and can be limited by model, IP, requests per minute, concurrency and
              monthly spend. A key can be revoked instantly.
            </p>
            <p className="mt-3 text-sm text-muted">
              Keys are for servers only. A key in browser JavaScript can be copied by anyone who
              opens developer tools.
            </p>
          </Doc>

          <Doc id="chat" title="Chat completions">
            <p className="text-sm text-muted">
              <code className="mono">POST /v1/chat/completions</code> — OpenAI-compatible. Use{' '}
              <code className="mono">"model": "auto"</code> to let the gateway pick a healthy
              model, or name a specific one from <code className="mono">/v1/models</code>.
            </p>

            <div className="mt-4">
              <CodeTabs snippets={samples} defaultTab="python" onTabChange={setTab} />
            </div>

            <p className="mt-4 text-sm text-muted">Responses add a gateway object:</p>
            <pre className="mt-2 rounded-card border border-border bg-surface p-4 overflow-x-auto">
              <code className="mono text-xs">{`{
  "gateway": {
    "provider": "gemini",
    "model": "Gemini 2.5 Flash",
    "cached": false,
    "cost": 0.000043,
    "price": 0.000054,
    "fallbacks_tried": [],
    "latency_ms": 812,
    "request_id": "req_8f3a…"
  }
}`}</code>
            </pre>
          </Doc>

          <Doc id="models" title="Models">
            <p className="text-sm text-muted">
              <code className="mono">GET /v1/models</code> lists the models your key may use,
              including context length, pricing and capabilities. Model ids are stable and safe to
              hard-code.
            </p>
          </Doc>

          <Doc id="limits" title="Limits">
            <p className="text-sm text-muted">
              Your account has limits for requests per minute, tokens per minute, concurrency
              (requests in flight at once), and optionally a daily request cap and a monthly spend
              cap. When you exceed one, the response is <code className="mono">429</code> with a{' '}
              <code className="mono">Retry-After</code> header.
            </p>
            <p className="mt-3 text-sm text-muted">
              Exceeding concurrency is the usual cause of a 429 under load. Queue work server-side
              rather than firing everything at once.
            </p>
          </Doc>

          <Doc id="errors" title="Errors">
            <div className="card overflow-hidden">
              <div className="overflow-x-auto">
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
              </div>
            </div>
            <p className="mt-3 text-xs text-muted">
              Every response carries <code className="mono">X-Request-Id</code>. Quote it when
              reporting a problem.
            </p>
          </Doc>

          <Doc id="retries" title="Retries">
            <ul className="space-y-2 text-sm list-disc pl-5 text-muted">
              <li>Retry only <code className="mono">429</code> and <code className="mono">5xx</code>.</li>
              <li>Use exponential backoff with jitter, and honour <code className="mono">Retry-After</code>.</li>
              <li>
                Send an <code className="mono">Idempotency-Key</code> on retries so a retried
                request is not charged twice.
              </li>
              <li>
                Send <code className="mono">X-End-User</code> so usage can be attributed per end
                user.
              </li>
            </ul>
            <div className="mt-4">
              <pre className="rounded-card border border-border bg-surface p-4 overflow-x-auto">
                <code className="mono text-xs whitespace-pre">{samples.retry}</code>
              </pre>
            </div>
          </Doc>
        </div>
      </div>
    </Section>
  )
}

function Doc({ id, title, children }) {
  return (
    <section id={id} className="mt-10 scroll-mt-20">
      <h2 className="text-base font-semibold mb-3">{title}</h2>
      {children}
    </section>
  )
}
