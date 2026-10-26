import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../lib/api'
import { number } from '../../lib/format'
import { useSite } from '../../lib/site'
import { Section } from '../../components/PublicLayout'
import { CodeTabs } from '../../components/CodeTabs'
import { Badge } from '../../components/ui'

const FEATURES = [
  {
    title: 'One endpoint',
    body: 'Use many models through one API and one key.',
  },
  {
    title: 'Automatic fallback',
    body: 'If a model provider is slow or down, your request is routed to another.',
  },
  {
    title: 'Limits you control',
    body: 'Per-key rate limits, concurrency limits, and spend caps.',
  },
  {
    title: 'Clear usage',
    body: 'See requests, tokens, and cost per key in your dashboard.',
  },
]

const STEPS = [
  { title: 'Request access', body: 'Tell us about your project.' },
  { title: 'Get your key', body: 'We review your request and issue an API key.' },
  { title: 'Send your first request', body: 'Use the quickstart in the docs.' },
]

export default function HomePage() {
  return (
    <>
      <Hero />
      <Features />
      <ModelsPreview />
      <HowItWorks />
      <Trust />
      <FinalCta />
    </>
  )
}

/* ------------------------------------------------------------------ hero */
function Hero() {
  const site = useSite()
  const [tab, setTab] = useState('curl')
  const apiBase = site.api_base_url || `${window.location.origin}/v1`

  const snippets = {
    curl: `curl ${apiBase}/chat/completions \\
  -H "Authorization: Bearer sk-gw-YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "auto",
    "messages": [
      {"role": "user", "content": "Hello"}
    ]
  }'`,
    python: `import httpx

response = httpx.post(
    "${apiBase}/chat/completions",
    headers={"Authorization": "Bearer sk-gw-YOUR_KEY"},
    json={
        "model": "auto",
        "messages": [{"role": "user", "content": "Hello"}],
    },
    timeout=60,
)
print(response.json()["choices"][0]["message"]["content"])`,
    javascript: `const response = await fetch("${apiBase}/chat/completions", {
  method: "POST",
  headers: {
    Authorization: "Bearer sk-gw-YOUR_KEY",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: "auto",
    messages: [{ role: "user", content: "Hello" }],
  }),
})

const data = await response.json()
console.log(data.choices[0].message.content)`,
  }

  return (
    <Section className="pt-16 lg:pt-24 pb-16 lg:pb-24">
      <div className="grid gap-12 lg:grid-cols-2 lg:items-center">
        <div>
          <h1 className="hero-title">{site.tagline}</h1>
          <p className="mt-6 text-base text-muted prose-body">
            A single endpoint with automatic fallback, per-key limits, and clear usage tracking.
            Available by invitation.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link to="/request-access" className="btn-primary">
              Request access
            </Link>
            <Link to="/docs" className="btn-secondary">
              Read the docs
            </Link>
          </div>
          <p className="mt-6 text-sm text-muted">
            No self-serve signup. We review every request and reply by email.
          </p>
        </div>

        <div className="min-w-0">
          <CodeTabs snippets={snippets} defaultTab="curl" onTabChange={setTab} />
          <p className="mt-3 text-xs text-muted">
            OpenAI-compatible. Change the base URL and key, keep your existing code.
          </p>
        </div>
      </div>
    </Section>
  )
}

/* -------------------------------------------------------------- features */
function Features() {
  return (
    <Section className="pb-16 lg:pb-24">
      <h2 className="section-heading mb-8">Why this service</h2>
      <div className="grid gap-4 sm:grid-cols-2">
        {FEATURES.map((feature) => (
          <div key={feature.title} className="card p-6">
            <h3 className="text-xl serif">{feature.title}</h3>
            <p className="mt-2 text-sm text-muted leading-relaxed">{feature.body}</p>
          </div>
        ))}
      </div>
    </Section>
  )
}

/* --------------------------------------------------------- models preview */
function ModelsPreview() {
  const site = useSite()
  const [models, setModels] = useState(null)

  useEffect(() => {
    api
      .get('/public/models')
      .then((data) => setModels(data.models.slice(0, 6)))
      .catch(() => setModels([])) // hide the section rather than show an error (§3.4)
  }, [])

  if (!models || models.length === 0) return null

  return (
    <Section className="pb-16 lg:pb-24">
      <div className="flex items-end justify-between gap-4 mb-8">
        <h2 className="section-heading">Models</h2>
        <Link to="/models" className="text-sm text-accent hover:underline">
          View all models
        </Link>
      </div>
      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="th">Model</th>
                <th className="th text-right">Context</th>
                <th className="th">Capabilities</th>
                {site.public_pricing && <th className="th text-right">Input /1M</th>}
                {site.public_pricing && <th className="th text-right">Output /1M</th>}
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model.id}>
                  <td className="td mono text-xs">{model.name}</td>
                  <td className="td text-right mono text-xs">
                    {model.context_length ? number(model.context_length) : '—'}
                  </td>
                  <td className="td">
                    <div className="flex gap-1">
                      {model.supports_vision && <Badge tone="accent">vision</Badge>}
                      {model.supports_tools && <Badge tone="accent">tools</Badge>}
                      {!model.supports_vision && !model.supports_tools && (
                        <span className="text-xs text-muted">text</span>
                      )}
                    </div>
                  </td>
                  {site.public_pricing && (
                    <td className="td text-right mono text-xs">
                      {model.free ? 'Free' : `$${model.input_price_per_m}`}
                    </td>
                  )}
                  {site.public_pricing && (
                    <td className="td text-right mono text-xs">
                      {model.free ? 'Free' : `$${model.output_price_per_m}`}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Section>
  )
}

/* ------------------------------------------------------------ how it works */
function HowItWorks() {
  return (
    <Section className="pb-16 lg:pb-24">
      <h2 className="section-heading mb-8">How it works</h2>
      <ol className="grid gap-6 sm:grid-cols-3">
        {STEPS.map((step, index) => (
          <li key={step.title}>
            <div className="flex items-center gap-2.5">
              <span className="h-6 w-6 rounded-full border border-border-strong grid place-items-center text-xs font-medium mono">
                {index + 1}
              </span>
              <h3 className="text-sm font-semibold">{step.title}</h3>
            </div>
            <p className="mt-2 text-sm text-muted leading-relaxed">{step.body}</p>
          </li>
        ))}
      </ol>
    </Section>
  )
}

/* ------------------------------------------------------------------ trust */
function Trust() {
  const points = [
    'API keys are stored hashed and shown only once.',
    'Prompts and responses are not stored by default — only metadata such as token counts.',
    'Per-key limits and instant key shutdown.',
    'Server-to-server use: keys are never meant for browsers.',
  ]
  return (
    <Section className="pb-16 lg:pb-24">
      <h2 className="section-heading mb-6">Trust and security</h2>
      <ul className="space-y-3">
        {points.map((point) => (
          <li key={point} className="flex gap-3 text-sm text-muted">
            <span className="text-success shrink-0" aria-hidden="true">✓</span>
            <span>{point}</span>
          </li>
        ))}
      </ul>
    </Section>
  )
}

/* -------------------------------------------------------------------- cta */
function FinalCta() {
  return (
    <Section className="pb-4">
      <div className="card p-12 text-center">
        <h2 className="section-heading">Request access</h2>
        <p className="mt-3 text-sm text-muted prose-body mx-auto">
          We review every request and reply by email.
        </p>
        <Link to="/request-access" className="btn-primary h-10 px-5 mt-6 inline-flex">
          Request access
        </Link>
      </div>
    </Section>
  )
}
