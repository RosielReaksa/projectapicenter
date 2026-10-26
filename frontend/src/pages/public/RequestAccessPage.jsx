import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../lib/api'
import { useSite } from '../../lib/site'
import { Section } from '../../components/PublicLayout'

const USAGE_OPTIONS = [
  'Under 1k requests/day',
  '1k to 10k',
  '10k to 100k',
  'Over 100k',
  'Not sure',
]

const EMPTY = {
  name: '',
  email: '',
  company: '',
  use_case: '',
  expected_usage: 'Under 1k requests/day',
  accepted_terms: false,
  website_url: '', // honeypot
}

export default function RequestAccessPage() {
  const site = useSite()
  const [form, setForm] = useState(EMPTY)
  const [errors, setErrors] = useState({})
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState(false)
  const [serverError, setServerError] = useState('')

  const set = (field, value) => {
    setForm((current) => ({ ...current, [field]: value }))
    setErrors((current) => ({ ...current, [field]: undefined }))
  }

  const validate = () => {
    const next = {}
    if (!form.name.trim()) next.name = 'Enter your name.'
    if (!form.email.trim()) next.email = 'Enter your email address.'
    else if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email.trim()))
      next.email = 'Enter a valid email address.'
    if (!form.company.trim()) next.company = 'Enter your company or website.'
    if (!form.use_case.trim()) next.use_case = 'Tell us what you plan to use it for.'
    if (!form.accepted_terms) next.accepted_terms = 'Please accept the Terms and Privacy Policy.'
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const submit = async (event) => {
    event.preventDefault()
    setServerError('')
    if (!validate()) return

    setSubmitting(true)
    try {
      await api.post('/public/access-requests', {
        name: form.name.trim(),
        email: form.email.trim(),
        company: form.company.trim(),
        use_case: form.use_case.trim(),
        expected_usage: form.expected_usage,
        accepted_terms: form.accepted_terms,
        website_url: form.website_url,
      })
      setDone(true)
    } catch (err) {
      setServerError(err.message || 'Something went wrong. Please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  if (done) {
    return (
      <Section className="py-16 sm:py-24">
        <div className="max-w-lg">
          <h1 className="text-2xl font-semibold tracking-tight">Request received</h1>
          <p className="mt-3 text-sm text-muted leading-relaxed">
            Thanks. We'll review your request and reply by email.
          </p>
          <div className="mt-8 flex gap-3">
            <Link to="/docs" className="btn-secondary">Read the docs</Link>
            <Link to="/" className="btn-ghost">Back home</Link>
          </div>
        </div>
      </Section>
    )
  }

  return (
    <Section className="py-12 sm:py-16">
      <div className="max-w-xl">
        <h1 className="text-2xl font-semibold tracking-tight">Request access</h1>
        <p className="mt-2 text-sm text-muted">
          This service is invite-only. Tell us about your project and we'll reply by email.
        </p>

        <form onSubmit={submit} className="mt-8 space-y-4" noValidate>
          <Field label="Name" error={errors.name} htmlFor="name">
            <input
              id="name"
              className="input"
              maxLength={100}
              value={form.name}
              onChange={(event) => set('name', event.target.value)}
              autoComplete="name"
              aria-invalid={!!errors.name}
              aria-describedby={errors.name ? 'name-error' : undefined}
            />
          </Field>

          <Field label="Email" error={errors.email} htmlFor="email">
            <input
              id="email"
              className="input"
              type="email"
              maxLength={255}
              value={form.email}
              onChange={(event) => set('email', event.target.value)}
              autoComplete="email"
              aria-invalid={!!errors.email}
              aria-describedby={errors.email ? 'email-error' : undefined}
            />
          </Field>

          <Field
            label="Company or website"
            error={errors.company}
            htmlFor="company"
          >
            <input
              id="company"
              className="input"
              maxLength={200}
              value={form.company}
              onChange={(event) => set('company', event.target.value)}
              autoComplete="organization"
              aria-invalid={!!errors.company}
            />
          </Field>

          <Field
            label="What will you use it for?"
            error={errors.use_case}
            htmlFor="use_case"
            hint={`${form.use_case.length}/1000`}
          >
            <textarea
              id="use_case"
              className="textarea text-sm"
              rows={4}
              maxLength={1000}
              value={form.use_case}
              onChange={(event) => set('use_case', event.target.value)}
              aria-invalid={!!errors.use_case}
            />
          </Field>

          <Field label="Expected usage" htmlFor="expected_usage">
            <select
              id="expected_usage"
              className="select"
              value={form.expected_usage}
              onChange={(event) => set('expected_usage', event.target.value)}
            >
              {USAGE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </Field>

          {/* Honeypot: hidden from people, tempting to bots. */}
          <div className="hidden" aria-hidden="true">
            <label htmlFor="website_url">Website</label>
            <input
              id="website_url"
              name="website_url"
              tabIndex={-1}
              autoComplete="off"
              value={form.website_url}
              onChange={(event) => set('website_url', event.target.value)}
            />
          </div>

          <div>
            <label className="flex items-start gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                className="mt-0.5 h-4 w-4 accent-[rgb(var(--accent))]"
                checked={form.accepted_terms}
                onChange={(event) => set('accepted_terms', event.target.checked)}
                aria-invalid={!!errors.accepted_terms}
              />
              <span className="text-sm text-muted">
                I accept the{' '}
                <Link to="/terms" className="text-accent hover:underline">Terms of Service</Link>{' '}
                and{' '}
                <Link to="/privacy" className="text-accent hover:underline">Privacy Policy</Link>.
              </span>
            </label>
            {errors.accepted_terms && (
              <p id="terms-error" className="mt-1 text-xs text-danger" role="alert">
                {errors.accepted_terms}
              </p>
            )}
          </div>

          {serverError && (
            <p className="text-sm text-danger" role="alert">
              {serverError}
            </p>
          )}

          <button className="btn-primary w-full h-10" disabled={submitting}>
            {submitting ? 'Sending…' : 'Request access'}
          </button>

          <p className="text-xs text-muted">
            We use this only to review your request. Questions?{' '}
            <a href={`mailto:${site.support_email}`} className="text-accent hover:underline">
              {site.support_email}
            </a>
          </p>
        </form>
      </div>
    </Section>
  )
}

function Field({ label, error, hint, htmlFor, children }) {
  return (
    <div>
      <label className="label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${htmlFor}-error`} className="mt-1 text-xs text-danger" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="mt-1 text-xs text-muted text-right mono">{hint}</p>
      ) : null}
    </div>
  )
}
