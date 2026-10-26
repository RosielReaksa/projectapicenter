# Homepage and Public Pages: Build Spec

Companion to `AGENT_BUILD_BRIEF.md`, `private-llm-gateway-spec.md`, and `backend-design.md`.

**Precedence:** where this file conflicts with the spec's page list or visual rules, this file wins for public pages only. Dashboard pages keep following the main spec.

---

## 1. Purpose

The service is **invite-only**. The homepage is not a sign-up funnel. Its jobs are:

1. Look credible and clear to a partner's developer or manager within 10 seconds.
2. Show exactly how simple the API is (real code).
3. Show what models are available.
4. Give invited users a way to **Sign in**.
5. Give everyone else a way to **Request access**, which the owner reviews by hand.

Never create an account automatically from any public form.

---

## 2. Routes

| Route | Page | Auth |
|---|---|---|
| `/` | Homepage | Public |
| `/models` | Public models list | Public |
| `/docs` | Quickstart and API reference | Public |
| `/request-access` | Access request form | Public |
| `/terms` | Terms of Service | Public |
| `/privacy` | Privacy Policy | Public |
| `/status` | Status page (optional, add when ready) | Public |
| `/login` | Sign in (email, password, 2FA) | Public |
| `/console/*` | Dashboard (existing) | Login required |

If a signed-in user visits `/`, show the homepage normally but change the top bar button from **Sign in** to **Open console**.

Serve everything from the same React + Tailwind app so the design system is shared. Public pages should be fast: code-split from the console bundle, no heavy libraries on the homepage.

---

## 2.1 Design Direction

Same visual system as the dashboard (see `private-llm-gateway-spec.md` section 5), so the homepage and console feel like one product.

- Dark theme by default, with a light theme toggle. Follow system preference on first visit.
- Lots of whitespace, thin 1px borders, no shadows, no stock photos, no illustrations.
- Real code and real data are the decoration.
- Inter (or similar) for interface text, a monospace font for code, keys, model names, and numbers.
- Accent color from the shared tokens, used sparingly: primary buttons, links, small highlights.
- Content max width about 1100px, centered, generous vertical spacing between sections (96px or more on desktop).
- Mobile first layout. Everything must work at 375px width. Code blocks scroll horizontally inside their own container; the page itself never scrolls sideways.
- Contrast ratio at least 4.5 to 1. Full keyboard navigation and visible focus rings.

---

## 3. Homepage Sections (Top to Bottom)

### 3.1 Top bar (sticky)

- Left: logo and product name.
- Center or right links: **Models**, **Docs**, **Status** (if enabled).
- Right buttons: **Sign in** (secondary, outline) and **Request access** (primary).
- On mobile: collapse links into a menu; keep both buttons reachable.

### 3.2 Hero

Two columns on desktop, stacked on mobile.

**Left column**

- Headline: `One API for fast, reliable AI models.`
- Subline: `A single endpoint with automatic fallback, per-key limits, and clear usage tracking. Available by invitation.`
- Buttons: **Request access** (primary), **Read the docs** (secondary).

**Right column: code card**

Tabs: `curl`, `Python`, `JavaScript`. Copy button. Content must use the real production base URL (from config) and a placeholder key.

```bash
curl https://YOUR_DOMAIN/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

Under the card, one small line: `OpenAI-compatible. Change the base URL and key, keep your existing code.`

### 3.3 Why this service

Four short cards in a grid (2 by 2 on desktop, 1 column on mobile). Each has a title and one or two sentences.

| Title | Text |
|---|---|
| One endpoint | Use many models through one API and one key. |
| Automatic fallback | If a model provider is slow or down, your request is routed to another. |
| Limits you control | Per-key rate limits, concurrency limits, and spend caps. |
| Clear usage | See requests, tokens, and cost per key in your dashboard. |

### 3.4 Models preview

- A table of 5 to 8 models pulled from the same data as `/models` (do not hard-code).
- Columns: Model name, Context length, Capabilities (text, vision, tools). Show **price columns only if the owner enables public pricing** (setting `public_pricing`, default off).
- Button below: **View all models**.
- If the data fails to load, hide the section rather than showing an error.

### 3.5 How it works

Three numbered steps, horizontal on desktop:

1. **Request access.** Tell us about your project.
2. **Get your key.** We review your request and issue an API key.
3. **Send your first request.** Use the quickstart in the docs.

### 3.6 Trust and security

Short list of factual statements. **Only include a statement if it is true for the deployed system.** The owner must confirm each one before launch.

- API keys are stored hashed and shown only once.
- Prompts and responses are not stored by default (only metadata such as token counts).
- Per-key limits and instant key shutdown.
- Server-to-server use: keys are never meant for browsers.

Do not make claims about uptime percentages, speed rankings, or "no training on your data" unless the owner has verified and approved the exact wording.

### 3.7 Request access (inline call to action)

A full-width band with a headline `Request access` and a short line `We review every request and reply by email.` plus the primary button that goes to `/request-access`.

### 3.8 Footer

- Columns: Product (Models, Docs, Status), Legal (Terms, Privacy), Contact (support email).
- Small copyright line.
- Do not list upstream provider names anywhere on public pages unless the owner approves.

---

## 4. Request Access Page and Flow

### 4.1 Form fields

| Field | Type | Required | Notes |
|---|---|---|---|
| Name | text | yes | Max 100 characters |
| Email | email | yes | Validate format |
| Company or website | text | yes | Max 200 characters |
| What will you use it for? | textarea | yes | Max 1000 characters |
| Expected usage | select | yes | Options: Under 1k requests/day, 1k to 10k, 10k to 100k, Over 100k, Not sure |
| Accept Terms and Privacy | checkbox | yes | Links to `/terms` and `/privacy` |

### 4.2 Behavior

- Submit calls `POST /public/access-requests`.
- On success show: `Thanks. We'll review your request and reply by email.` Do not reveal whether the email already exists.
- The request is stored with status `pending`. **No user, partner, or key is created.**
- The owner is notified by email (if email is configured) and sees the request in the admin dashboard.

### 4.3 Abuse protection (required)

- Rate limit by IP (for example 5 submissions per hour) using Redis.
- Honeypot hidden field; silently discard submissions that fill it.
- Optional CAPTCHA behind a setting (default off, because it adds a third party; supports a privacy-friendly option when enabled).
- Server-side validation and length limits on every field. Escape all output in the admin view to prevent script injection.
- Deduplicate: the same email submitting repeatedly within 24 hours updates one record rather than creating many.

### 4.4 Backend additions

New table:

```sql
CREATE TABLE access_requests (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    company       TEXT NOT NULL,
    use_case      TEXT NOT NULL,
    expected_usage TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','approved','rejected')),
    ip            TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    reviewed_at   TIMESTAMP,
    reviewed_by   INTEGER REFERENCES users(id),
    notes         TEXT
);
CREATE INDEX idx_access_status ON access_requests(status, created_at);
```

New admin endpoints (Owner only, behind login and 2FA):

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/access-requests` | List with status filter |
| POST | `/admin/access-requests/{id}/approve` | Mark approved and start the partner creation flow |
| POST | `/admin/access-requests/{id}/reject` | Mark rejected (optional note) |

**Approve** opens the existing "create partner" form prefilled from the request. The owner sets limits and creates the first key. The key is shown once in the dashboard and is never sent automatically by email.

New dashboard page: **Access Requests** (table with name, company, usage, date, status, and Approve and Reject buttons). Show a badge with the pending count in the sidebar.

---

## 5. Public Models Page (`/models`)

- Same table design as the console Models page, read-only.
- Columns: Model, Context length, Capabilities, and Price (only if `public_pricing` is on).
- Search box, and filters for capability and context length.
- Data comes from a public endpoint `GET /public/models`, which returns only enabled models and only fields safe for public view (no provider names, no internal ids, no keys).
- Cache the response for a short time (for example 60 seconds) so this page cannot load the database heavily.

---

## 6. Docs Page (`/docs`)

Minimum content:

1. Quickstart: request access, get a key, first request.
2. Authentication: header format; keys are for servers only.
3. Chat completions endpoint: parameters and example request and response.
4. Models endpoint.
5. Limits: rate, concurrency, and what a 429 means.
6. Errors: table of status codes and meanings.
7. Retry guide: exponential backoff, honor `Retry-After`, use `Idempotency-Key`, send `X-End-User`.
8. Code samples in curl, Python, and JavaScript.

Content from the partner integration guide in `AGENT_BUILD_BRIEF.md` section 8 should be reused here.

---

## 7. Legal Pages

Create `/terms` and `/privacy` as pages with clearly marked **placeholder content**, and a visible note in the admin dashboard until the owner replaces them: `Legal pages still contain placeholder text.` The agent must not present invented legal text as final. The owner should have these reviewed by a qualified person before launch.

Topics to cover (for the owner to review): acceptable use, what content may be sent, data handling and retention, no uptime guarantee unless stated, suspension rights, liability, contact details.

---

## 8. Technical Requirements

- **SEO and sharing:** page titles, meta descriptions, Open Graph tags, a sitemap listing public pages, a `robots.txt` that blocks `/console`, `/login` indexing is optional.
- **Performance targets:** homepage loads in under 2 seconds on a typical mobile connection; no layout shift; lazy load anything below the fold.
- **Security headers:** Content-Security-Policy, X-Content-Type-Options, Referrer-Policy, frame protection.
- **No tracking by default.** If analytics are added later, use a privacy-friendly option and update the Privacy page.
- **Configurable text:** product name, domain, support email, and base URL come from config, not hard-coded strings.
- **Theme:** respects system preference and stores the user's choice in local storage (public pages only; the console follows the existing rule).
- **Accessibility:** semantic headings, alt text, labels on all form fields, and form errors announced to screen readers.

---

## 9. Build Order

| Step | Deliverable |
|---|---|
| H1 | Shared layout: top bar, footer, theme toggle, routing for public pages |
| H2 | Homepage sections 3.1 to 3.8 with static content and the code card tabs |
| H3 | `GET /public/models` endpoint and the `/models` page; homepage models preview |
| H4 | `access_requests` table, `POST /public/access-requests`, form page, abuse protection |
| H5 | Admin **Access Requests** page and approve and reject flow |
| H6 | Docs page, placeholder legal pages with admin warning |
| H7 | SEO, security headers, performance and accessibility pass |

This work can start once the admin auth (S7) and dashboard shell (S8) from `AGENT_BUILD_BRIEF.md` exist, since it reuses their login and design system.

---

## 10. Acceptance Criteria

1. Homepage renders all sections and is usable at 375px width with no horizontal page scroll.
2. Dark and light themes both pass contrast checks.
3. The code card shows the real base URL from config, and the copy button copies the exact text.
4. Models preview and `/models` show only enabled models and never expose provider names, internal ids, or secrets.
5. Submitting the form creates one `pending` record and **no user, partner, or key**.
6. More than the allowed submissions from one IP within the window are rejected with a friendly message.
7. A filled honeypot field results in no stored record and no visible error.
8. Admin can list, approve, and reject requests; approve leads to the partner creation form prefilled; every action appears in the audit log.
9. Request text containing HTML or script is displayed as plain text in the admin view.
10. Signed-in users see **Open console** instead of **Sign in**.
11. `/console` and `/login` are excluded from the sitemap, and `/console` is blocked in `robots.txt`.
12. Placeholder legal pages are clearly flagged in the admin dashboard.

---

## 11. Open Items (Owner to Confirm; Agent Uses Defaults Until Answered)

| Item | Default |
|---|---|
| Product name, logo, domain, support email | Placeholders from config |
| Show prices publicly | No (`public_pricing` off) |
| Status page | Skip until monitoring exists; hide the link |
| Email sending for notifications | Optional; if no email service is configured, only show requests in the dashboard |
| CAPTCHA | Off |
| Trust statements | Only the four listed in 3.6, pending owner confirmation |
| Dashboard and site language | English |
| Legal text | Placeholder until reviewed by the owner |
