import { Link } from 'react-router-dom'
import { useSite } from '../../lib/site'
import { Section } from '../../components/PublicLayout'

/**
 * Legal pages are deliberately placeholders (HOMEPAGE_SPEC §7).
 * Do not replace this with invented legal text presented as final — the owner
 * must have real terms reviewed by a qualified person before launch.
 */
function LegalNotice() {
  return (
    <div className="card border-warning/40 bg-warning/5 p-4 mb-8">
      <p className="text-sm font-medium text-warning">Placeholder content</p>
      <p className="mt-1 text-xs text-muted">
        This page is a structural placeholder. The text below is not legal advice and has not been
        reviewed. It must be replaced with terms prepared for your business before you invite anyone.
      </p>
    </div>
  )
}

export function TermsPage() {
  const site = useSite()
  return (
    <Section className="py-12 sm:py-16">
      <div className="max-w-2xl">
        <h1 className="text-2xl font-semibold tracking-tight">Terms of Service</h1>
        <p className="mt-2 text-xs text-muted">Last updated: placeholder</p>
        <div className="mt-8">
          <LegalNotice />
          <Prose>
            <h2>1. Service</h2>
            <p>
              {site.app_name} provides API access to third-party AI models. Access is by invitation
              only. Accounts are created by the operator, not through public signup.
            </p>

            <h2>2. Acceptable use</h2>
            <p>
              You agree not to use the service to send unlawful content, to attempt to bypass
              rate or spending limits, to resell access without written permission, or to
              interfere with the service or other users.
            </p>

            <h2>3. Content you send</h2>
            <p>
              You are responsible for the content you submit and for having the right to process
              it. Do not send content you are not permitted to share with a third-party model
              provider.
            </p>

            <h2>4. Data handling</h2>
            <p>
              Request and response content is not stored by default; only metadata such as token
              counts, latency and cost is recorded. See the{' '}
              <Link to="/privacy" className="text-accent hover:underline">Privacy Policy</Link> for
              details and retention.
            </p>

            <h2>5. Availability</h2>
            <p>
              No uptime guarantee is made unless the operator has stated one in writing. The
              service may be suspended or withdrawn at any time.
            </p>

            <h2>6. Suspension</h2>
            <p>
              Keys may be disabled immediately for abuse, non-payment, or breach of these terms.
            </p>

            <h2>7. Liability</h2>
            <p>
              The service is provided "as is". The operator is not liable for indirect or
              consequential loss arising from use of the service, to the extent permitted by law.
            </p>

            <h2>8. Contact</h2>
            <p>
              Questions about these terms:{' '}
              <a href={`mailto:${site.support_email}`} className="text-accent hover:underline">
                {site.support_email}
              </a>
              .
            </p>
          </Prose>
        </div>
      </div>
    </Section>
  )
}

export function PrivacyPage() {
  const site = useSite()
  return (
    <Section className="py-12 sm:py-16">
      <div className="max-w-2xl">
        <h1 className="text-2xl font-semibold tracking-tight">Privacy Policy</h1>
        <p className="mt-2 text-xs text-muted">Last updated: placeholder</p>
        <div className="mt-8">
          <LegalNotice />
          <Prose>
            <h2>1. What we collect</h2>
            <p>
              Account details (name, email, company) from access requests; API request metadata
              (timestamp, model, token counts, latency, cost, key used, and an optional end-user
              identifier you send); and the content of messages only if full prompt logging has
              been explicitly enabled.
            </p>

            <h2>2. Prompt content</h2>
            <p>
              By default, the text of your prompts and the model's responses is{' '}
              <strong>not stored</strong>. Only metadata is recorded, for usage reporting and
              billing.
            </p>

            <h2>3. Retention</h2>
            <p>
              Request metadata is retained for a limited period and then aggregated or deleted.
              Access requests are retained until reviewed.
            </p>

            <h2>4. Third parties</h2>
            <p>
              Requests are processed by third-party AI model providers. Their handling of data is
              governed by their own terms. Do not send data you would not want processed by a
              third-party provider.
            </p>

            <h2>5. Cookies</h2>
            <p>
              A session cookie is used to keep you signed in to the console. No advertising or
              cross-site tracking cookies are used.
            </p>

            <h2>6. Your rights</h2>
            <p>
              You can request access to, correction of, or deletion of your account data by
              contacting{' '}
              <a href={`mailto:${site.support_email}`} className="text-accent hover:underline">
                {site.support_email}
              </a>
              .
            </p>
          </Prose>
        </div>
      </div>
    </Section>
  )
}

function Prose({ children }) {
  return (
    <div className="space-y-4 text-sm text-muted leading-relaxed [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:text-fg [&_h2]:mt-6 [&_h2]:mb-1.5 [&_strong]:text-fg">
      {children}
    </div>
  )
}
