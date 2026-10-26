# Private LLM Gateway: Full Spec and Admin Dashboard Plan

Draft v1 · Private use only · Functionality inspired by Claude Console, visual design inspired by OpenRouter

---

## 0. Summary of Decisions So Far

- **What it is:** a private "API call center" (LLM gateway). It puts many AI providers (Gemini, Groq, OpenRouter, others) behind one OpenAI-compatible endpoint.
- **Who can use it:** only the owner, plus people the owner approves. No public signup, no billing, no reselling.
- **Why private:** this avoids the terms-of-service and reliability problems of reselling free tiers, and removes most abuse risk.
- **Provider keys:** stored only on the server, encrypted. Never in browser JavaScript.
- **Website integration:** the website backend calls the gateway. The browser only calls the website.
- **Console style:** Claude Console style functionality (Playground, API Keys, Usage, Logs, Settings) with OpenRouter style visual design (clean, minimal, dense tables, dark and light themes).
- **Build order:** gateway API first, then admin dashboard with three screens (API Keys, Providers, Activity), then the rest.

---

## 1. Purpose

A private web console and API that gives one endpoint for all personal projects and websites.

**Goals**

- One OpenAI-compatible endpoint for all personal projects and websites.
- Cheapest-first routing with automatic fallback when a provider fails or hits a limit.
- A clean console to test prompts, manage keys, and see usage and cost.
- Hard spending and rate limits so a bug can never cause a surprise bill.

**Non-goals (v1)**

- Public signup, billing, or reselling access.
- Team or organization management beyond a small approved list.
- Streaming, image generation, and fine-tuning (see roadmap).

**Honest caveats about free tiers**

- Free tiers (Gemini, Groq, OpenRouter free models, and others) have rate limits that can shrink without notice.
- Some free tiers may use prompts for training. Do not send private or customer data through them.
- Do not create multiple accounts or keys per provider to get around quotas. This usually violates provider terms and can get every account banned. Spread load across different providers instead.

---

## 2. Access Model

| Role | Who | Can do |
|---|---|---|
| Owner | You | Everything: providers, routing, members, all keys, all logs |
| Member | People you invite | Create own gateway keys, use the Playground, see own usage |
| Gateway key | A project or app | Call the API only; scoped to one member and one project |

**Rules**

- No public registration. Members are added only by invitation from the Owner.
- Owner login uses a strong password plus two-factor authentication.
- Every gateway key can be disabled instantly and has its own daily limit and optional expiry.
- Optional network lock: the API accepts requests only from allowed IP addresses or through a private network such as a VPN (Tailscale or similar).
- Website backends call the gateway. Browser JavaScript never holds a gateway key.

**Access levels, from simple to strong**

1. One secret key, only you.
2. One key per person or project, each disable-able and tracked. (Recommended.)
3. Network-level lock on top of level 2, so a leaked key is useless to strangers. (Recommended if you control the servers.)

---

## 3. Information Architecture

Left sidebar (Claude Console style), content area on the right:

1. **Dashboard**: overview of today's usage, cost, errors, provider health.
2. **Playground**: test prompts against any model or against the router.
3. **Models**: all available models with price, context size, and status.
4. **Providers**: store and test upstream provider keys.
5. **Routing**: fallback order and rules.
6. **API Keys**: create, limit, disable gateway keys.
7. **Activity**: request log with filters.
8. **Usage and Cost**: charts and exports.
9. **Members** (Owner only): invite and remove people.
10. **Settings**: security, limits, network lock, danger zone.
11. **Docs**: quickstart and API reference.

Top bar: workspace name, environment badge (Production or Test), search, theme toggle, account menu.

---

## 4. Page Specifications

### 4.1 Dashboard

- Four stat cards: Requests today, Tokens today, Estimated cost today, Error rate.
- Line chart: requests over the last 7 or 30 days, split by provider.
- Provider health list: each provider with a status dot (healthy, rate limited, down), last error, and latency.
- Recent activity: last 10 requests.
- Alert banner when any limit is above 80 percent.

### 4.2 Playground (Workbench)

- Left: system prompt and message thread. Right: parameters panel.
- Model selector with a special entry `auto` that uses the router.
- Parameters: temperature, max tokens, top-p, stop sequences.
- Run button, copy button, and a View Code button that outputs cURL, Python, and JavaScript for the exact request.
- Under each response: provider used, latency, tokens in and out, cost, and whether it came from cache.
- Compare mode: run the same prompt on two to four models side by side.
- Save as Preset: stores system prompt, model, and parameters under a name that API calls can reference.

### 4.3 Models

- Searchable, filterable table (the main OpenRouter-style screen).
- Columns: Model name, Provider, Context length, Input price per 1M tokens, Output price per 1M tokens, Free tier (yes or no), Status.
- Filters: provider, free only, context length, capability (text, vision, tools).
- Row click opens a detail drawer with description, sample request, and a Try in Playground button.
- Owner can enable or disable individual models.

### 4.4 Providers

- One card per provider with logo, name, status, and masked key (last 4 characters only).
- Actions: Add key, Test connection, Replace key, Remove.
- Per provider settings: monthly spend cap, request-per-minute limit, treat as free tier (shows a warning that free tiers can change or use prompts for training).
- Keys are encrypted at rest and never shown in full after saving.

### 4.5 Routing

- Drag-and-drop ordered list of providers and models for the `auto` route.
- Fallback triggers: rate limit (429), server error (5xx), timeout, empty response.
- Rules (evaluated top to bottom):
  - If request is short and simple, use a cheap model.
  - If request contains an image, use a vision model.
  - If preset X is used, force model Y.
- Retry settings: attempts, timeout, backoff.
- Cache toggle with time-to-live; applies to requests with temperature 0.
- Test panel: enter a sample request and see which route it would take.

### 4.6 API Keys

- Table: Name, Owner, Key prefix (for example `sk-gw-ab12...`), Created, Last used, Daily limit, Monthly cap, Status.
- Create key dialog: name, daily request limit, monthly spend cap, allowed models, allowed IPs, expiry.
- The full key is shown once at creation with a copy button.
- Disable and delete actions require confirmation.

### 4.7 Activity

- Log table: Time, Key, Model requested, Provider used, Status, Latency, Tokens, Cost.
- Filters: date range, key, provider, status, cached.
- Row click opens a drawer with request and response. Prompt storage is a setting: Off, Metadata only, or Full. Default is Metadata only for privacy.
- Export to CSV.

### 4.8 Usage and Cost

- Charts: cost by day, tokens by model, requests by provider, cache hit rate.
- Breakdown tables by key, member, and model.
- Budget bar showing month-to-date against the cap.

### 4.9 Members, Settings, Docs

- **Members:** invite by email, assign limits, remove. Removing a member disables all of their keys.
- **Settings:** two-factor authentication, session list, IP allow list, global spend cap, prompt logging mode, data retention period, export all data, delete all data.
- **Docs:** quickstart, authentication, endpoint reference, error codes, and copyable code samples.

---

## 5. Visual Design System (OpenRouter-inspired)

**Overall feel:** clean, minimal, data-dense, developer-focused. Plenty of whitespace, thin borders, almost no shadows, no decorative illustrations.

**Color tokens**

| Token | Light | Dark |
|---|---|---|
| Background | #FFFFFF | #0B0B0F |
| Surface (cards) | #F7F7F8 | #141418 |
| Border | #E5E5E8 | #26262C |
| Text primary | #111114 | #F2F2F4 |
| Text secondary | #6B6B76 | #9A9AA6 |
| Accent | #6366F1 | #818CF8 |
| Success | #16A34A | #4ADE80 |
| Warning | #D97706 | #FBBF24 |
| Danger | #DC2626 | #F87171 |

Dark and light themes both ship in v1, with the system preference as default.

**Typography**

- Interface: Inter or a similar clean sans-serif, 14px base.
- Code, keys, model names, numbers in tables: a monospace font such as JetBrains Mono.
- Headings are semibold, never larger than 24px inside the console.

**Layout**

- Fixed left sidebar, 240px wide, collapsible to icons on small screens.
- Content max width 1200px, 24px page padding, 8px spacing grid.
- Tables are full width with sticky headers and compact rows.

**Components**

- Cards: 1px border, 10px radius, no shadow.
- Buttons: primary (accent fill), secondary (outline), danger (red outline); 36px height.
- Badges: small pill shapes for status and free or paid.
- Code blocks: dark background, copy button, language tabs.
- Toasts for confirmations; confirm dialogs for destructive actions.
- Empty states: one line of text and one action button.

**Responsive and accessibility**

- Usable on mobile for dashboard, keys, and activity; Playground is desktop-first.
- Contrast ratio at least 4.5 to 1, full keyboard navigation, visible focus rings.

---

## 6. API Specification

Base URL: `https://your-gateway-domain/v1`

Authentication header: `Authorization: Bearer sk-gw-...`

| Method | Path | Purpose |
|---|---|---|
| POST | /v1/chat/completions | Chat request, OpenAI-compatible |
| GET | /v1/models | List models available to the key |
| GET | /v1/usage | Usage for the calling key |
| GET | /health | Gateway and provider status |

**Request fields:** `model` (a specific model or `auto`), `messages`, `temperature`, `max_tokens`, and optional `preset`.

**Response additions:** the standard response plus `gateway.provider`, `gateway.cached`, `gateway.cost`, and `gateway.fallbacks_tried`.

**Example call**

```bash
curl https://your-gateway-domain/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

**Errors**

| Code | Meaning |
|---|---|
| 401 | Missing or invalid key |
| 403 | Key disabled, expired, or IP not allowed |
| 429 | Daily limit or spend cap reached |
| 502 or 503 | All providers failed; response lists what was tried |

Because the endpoint is OpenAI-compatible, existing SDKs work by changing only the base URL and key.

**Admin API (used by the dashboard, Owner only)**

| Method | Path | Purpose |
|---|---|---|
| POST | /admin/keys | Create gateway key (full key returned once) |
| POST | /admin/keys/{id}/disable | Disable a key |
| GET | /admin/keys | List keys |
| GET | /admin/providers | List providers (keys masked) |
| POST | /admin/providers | Add or replace a provider key |
| POST | /admin/providers/{id}/test | Test connection |
| GET | /admin/activity | Filtered request log |
| GET | /admin/usage | Totals and breakdowns |

---

## 7. Data Model

- **users:** id, email, role, password hash, two-factor secret, created.
- **provider_keys:** id, provider, encrypted key, monthly cap, rpm limit, is_free_tier, status.
- **models:** id, provider, name, context length, input price, output price, enabled.
- **gateway_keys:** id, user id, name, key hash, prefix, daily limit, monthly cap, allowed models, allowed IPs, expires, status.
- **routes and rules:** id, ordered provider list, conditions, fallback settings.
- **presets:** id, name, system prompt, model, parameters.
- **requests:** id, key id, time, model, provider, status, latency, tokens in, tokens out, cost, cached, optional payload.
- **cache:** hash, response, created.
- **audit_log:** id, user id, action, target, time.

Gateway keys are stored only as hashes. Provider keys are stored encrypted.

---

## 8. Architecture and Security

```
Your websites and apps (backend) -> Gateway API -> Router -> Providers
                                        |
                  Auth, limits, cache, logging, cost tracking
                                        |
                       Database  <-  Console web app
```

**Security checklist**

- HTTPS only; provider keys encrypted at rest; gateway keys hashed.
- Two-factor authentication for Owner and Members.
- Per-key and global limits; automatic shutoff when the global cap is reached.
- Optional IP allow list or private network access.
- Audit log for key creation, deletion, and settings changes.
- Prompt content not stored by default.
- Backups of the database, with provider keys excluded or separately encrypted.
- Provider keys never appear in logs, error messages, or API responses.

**Suggested stack (not final)**

- Backend: Python with FastAPI, or Node.js.
- Database: SQLite to start, PostgreSQL when it grows.
- Frontend: React or Next.js with Tailwind CSS.
- Hosting: a small VPS, with Docker for easy moves.

---

## 9. Admin Dashboard Build Plan

The admin dashboard is Phase 2 of the roadmap. This is the recommended build order.

**Step 1: Backend first, screens second.**
The dashboard is a window onto the gateway's data. Get the admin API working (create key, disable key, list requests, list providers, usage totals) before building screens. Otherwise screens end up with fake data and get redone.

**Step 2: Start with three screens, not eleven.**

| Screen | Why first |
|---|---|
| API Keys | Create, limit, and disable keys. This is what makes the gateway private. |
| Providers | Add and test Gemini, Groq, and OpenRouter keys. |
| Activity | See every request, which provider answered, and what it cost. |

Dashboard overview, Playground, Routing rules, Usage charts, and Members follow. The overview is easy once Activity exists, since it only shows totals from the same data.

**Step 3: Pick the frontend.**

| Option | Pros | Cons |
|---|---|---|
| React + Tailwind | Closest to the OpenRouter look; best for long-term growth | More setup and a build step |
| Server-rendered pages + HTMX | Few moving parts, no build step | More effort to reach a polished look |

Recommendation: React + Tailwind, served from the same domain as the gateway to avoid cross-site problems.

**Step 4: Treat dashboard security as part of the first build.**
Anyone who gets into the dashboard can read the provider setup and create keys. Requirements:

- Login with strong password plus two-factor authentication.
- Sessions that expire.
- Provider keys always masked after saving.
- `/admin` reachable only from your IP or over a VPN.

**Step 5: Mock before wiring.**
Make a clickable mockup of the three first screens in the OpenRouter style and review it before connecting the backend. Fixing a layout is cheap at that stage and expensive later.

---

## 10. Roadmap

**Phase 1: Core (usable by you)**
Gateway endpoint, three providers, fallback, gateway keys, daily limits, simple activity log.

**Phase 2: Console**
API Keys, Providers, and Activity screens first. Then Dashboard, Playground, Usage and Cost pages. Dark and light themes.

**Phase 3: Smart routing**
Rules, presets, caching, spend caps, provider health checks, compare mode.

**Phase 4: Hardening and extras**
Members and invitations, two-factor authentication, IP lock, audit log, streaming, tool calling support.

---

## 11. Open Questions

1. Where will it run: your own computer, a VPS, or existing website hosting?
2. Will anyone besides you use it in the first version?
3. Which providers do you already have keys for, and are any of them paid?
4. Should prompts ever be stored, or only metadata?
5. Do you need Khmer language support in the console interface?
6. Is there a specific feature you want that OpenRouter does not have? Capture it here as the main differentiator.
7. Next step: visual mockup of the three first screens, or a working version straight away?
