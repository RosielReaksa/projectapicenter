# Style Update: Warm, Calm Visual Design and Bug Fixes

Companion to `AGENT_BUILD_BRIEF.md`, `private-llm-gateway-spec.md`, `HOMEPAGE_SPEC.md`, and `MAINTENANCE_SPEC.md`.

**Precedence:** this file **replaces** the visual design rules (color tokens, typography, component styling, spacing) in `private-llm-gateway-spec.md` section 5 and `HOMEPAGE_SPEC.md` section 2.1. Layout, page content, routes, and behavior stay as written in those files. It applies to **both** the public pages and the admin dashboard so the product feels like one thing.

---

## 1. Why This Change

The first build follows the old spec but looks like a generic developer-tool site: pure white background, heavy sans-serif headline, indigo accent, sharp outlines, and large empty gaps between sections. The owner wants a calmer, warmer, more human feel in the spirit of Claude's website, while staying clearly its own brand.

**Target feel:** quiet, friendly, editorial, trustworthy. Fewer hard lines, softer surfaces, text that breathes.

### Brand safety rule (important)

Take inspiration from the **feel** only. Do **not** copy Anthropic or Claude branding:

- Do not use Claude's orange or any near match as the accent color.
- Do not use Claude's logo, sparkle mark, wordmark, or any lookalike.
- Do not use the words "Claude" or "Anthropic" in the interface as if the service were affiliated.
- Use the product's own logo and its own accent color (section 3.3).

This avoids partners believing the service is affiliated with Anthropic.

---

## 2. Bugs to Fix First (Seen on the Current Homepage)

| # | Problem | Fix |
|---|---|---|
| B1 | Small text under the code card reads `[OI]-compatible`. It should read `OpenAI-compatible`. | Find where this string is produced. It looks like a text replacement or templating mistake, so check for any find-and-replace, regex, or markdown processing that could mangle the word. Fix the cause, not only the text, and search the whole codebase for similar damaged strings. Add a test that checks the final rendered text. |
| B2 | Code card tab labels are inconsistently capitalized (`Curl`, `Python`, `Javascript`). | Use `curl`, `Python`, `JavaScript`. |
| B3 | The homepage shows light mode while the spec said dark by default. | Follow the theme rule in section 6. |
| B4 | Large empty vertical gap between the hero and the "Why this service" section, and the hero text sits far from the top. | Apply the spacing scale in section 5. |
| B5 | The code card shows `http://127.0.0.1:8000`. | Fine in development. In production it must come from the `PUBLIC_BASE_URL` config. Add a check that fails the production build if the base URL contains `127.0.0.1` or `localhost`. |

---

## 3. Design Tokens (Replace the Old Ones)

Implement as CSS variables (or Tailwind theme tokens) so every page and component reads from them. No hard-coded colors anywhere else.

### 3.1 Color tokens

| Token | Light | Dark |
|---|---|---|
| `--bg` (page background) | `#FAF9F5` | `#1F1E1C` |
| `--surface` (cards, code card) | `#F3F1EA` | `#2A2927` |
| `--surface-raised` (menus, dialogs) | `#FFFFFF` | `#33312E` |
| `--border` | `#E6E2D6` | `#3A3834` |
| `--border-strong` | `#D3CEBF` | `#4A4742` |
| `--text` (primary) | `#1F1E1B` | `#EFEDE6` |
| `--text-muted` | `#6B675E` | `#A19D92` |
| `--code-bg` | `#F1EFE7` | `#262523` |
| `--success` | `#2E7D4F` | `#6CC38C` |
| `--warning` | `#9A6A12` | `#E0B04C` |
| `--danger` | `#B3412F` | `#E8806E` |

Avoid pure white page backgrounds and pure black dark backgrounds. Keep every tint slightly warm.

### 3.2 Contrast requirements

- Body text on `--bg` and `--surface`: at least 4.5 to 1 in both themes.
- Muted text: at least 4.5 to 1 as well (adjust the hex values slightly if the check fails).
- Button text on the accent color: at least 4.5 to 1.
- Add an automated contrast test over all token pairs used in the UI.

### 3.3 Accent color

Use **one** accent token, configurable, so the owner can change the brand color without touching components.

| Token | Default light | Default dark |
|---|---|---|
| `--accent` | `#2F6F5E` (deep green) | `#5FB39B` |
| `--accent-hover` | `#265A4D` | `#7CC8B2` |
| `--accent-soft` (tinted backgrounds, badges) | `#E4EFEA` | `#26352F` |
| `--on-accent` (text on accent buttons) | `#FFFFFF` | `#10201B` |

Alternative presets the owner may choose later (provide as commented options in the theme file, not active): muted blue `#3C5E8C`, warm plum `#7A4A6B`. Never use indigo, purple-blue gradients, or any orange close to Claude's brand orange.

Use the accent sparingly: primary buttons, links, focus rings, selected states, and small highlights. Not for large fills or backgrounds.

---

## 4. Typography

| Use | Font | Notes |
|---|---|---|
| Large headlines (hero, section titles on public pages) | A readable serif: first choice **Newsreader**, alternatives **Source Serif 4** or **Lora** | Weight 400 to 500, not bold. Slightly tighter letter spacing on large sizes. |
| Body, buttons, tables, dashboard UI | **Inter** | 15px base on public pages, 14px in dashboard tables |
| Code, keys, model names, numbers in tables | **JetBrains Mono** | 13px |

Rules:

- **Self-host fonts** (for example via Fontsource packages) rather than loading from a third-party CDN, for speed and privacy. Use `font-display: swap` and subset to the needed character ranges.
- Provide real fallback stacks (`Georgia, serif`; `system-ui, sans-serif`; `ui-monospace, monospace`).
- The dashboard uses the serif only for page titles (for example "Maintenance", "API Keys"); everything else is sans-serif, so data stays easy to scan.
- Type scale on public pages: hero 56px desktop and 38px mobile, line height 1.1 to 1.15; section titles 36px and 28px; body 17px at line height 1.6; small text 14px.
- Maximum line length for paragraphs: about 65 characters.

---

## 5. Spacing, Shape, and Depth

**Spacing scale** (use only these): 4, 8, 12, 16, 24, 32, 48, 64, 96.

- Vertical padding between public sections: 96px desktop, 64px mobile. Hero top padding: 64px desktop, 40px mobile. The gap between the hero and the first section must not exceed 96px.
- Content max width: 1100px on public pages, with 24px side padding on mobile.

**Shape**

- Cards and code card: 16px radius. Buttons and inputs: 10px radius. Pills and badges: fully rounded.
- Borders: 1px using `--border`; use `--border-strong` only for inputs and focus states. Prefer a subtle fill (`--surface`) over an outline to separate sections, so there are fewer hard lines.

**Depth**

- No heavy shadows. Allowed: one very soft shadow for menus and dialogs only (for example `0 8px 24px rgba(0,0,0,0.08)` in light and `rgba(0,0,0,0.4)` in dark).
- No gradients except an optional, extremely subtle one on the hero background. No glows, no glassmorphism, no decorative blobs.

**Motion**

- Short transitions only (120 to 180 ms) on hover, focus, and theme change. Respect `prefers-reduced-motion` by disabling them.

---

## 6. Theme Behavior

- First visit follows the system preference (`prefers-color-scheme`).
- Theme toggle in the top bar stores the choice in local storage on public pages. The dashboard keeps the same choice via its existing mechanism.
- Warm dark theme must be fully designed and tested, not an inversion of light.
- Avoid a flash of the wrong theme on load (set the theme class before first paint using a tiny inline script).

---

## 7. Component Changes

| Component | Change |
|---|---|
| Primary button | Fill `--accent`, text `--on-accent`, 10px radius, 40px height on public pages (36px in dashboard), hover `--accent-hover`. No borders. |
| Secondary button | Transparent or `--surface` fill, 1px `--border-strong`, text `--text`, same height and radius. |
| Top bar | Background `--bg` with a 1px bottom border `--border`; logo plus product name; calm, no shadow. The theme toggle becomes an obvious icon button with an accessible label. |
| Hero | Left-aligned serif headline, muted subline in `--text-muted`, two buttons, and the code card on the right. Remove the extra empty vertical space. |
| Code card | `--code-bg` fill, 16px radius, 1px `--border`. Tabs styled as small pills with the active tab in `--surface-raised`. Copy button shows a "Copied" state for 2 seconds. Code uses a restrained syntax palette built from the tokens (no bright neon colors). |
| Feature cards ("Why this service") | `--surface` fill, 16px radius, no outline or a very light one, 24px padding, serif card titles at 20px. |
| Models table | Row separators only (no vertical lines), sticky header with `--surface` fill, monospace for model names and numbers, hover row `--accent-soft`. |
| Badges | Pill shape. Status badges use soft tinted backgrounds (`--accent-soft`, or tinted success, warning, danger) with matching text. |
| Inputs and forms | `--surface-raised` fill, 1px `--border-strong`, 10px radius, clear focus ring (2px `--accent` with offset). Error text in `--danger` with an icon, and announced to screen readers. |
| Dashboard sidebar | `--surface` fill, selected item uses `--accent-soft` fill with `--accent` text, 10px radius. |
| Dashboard cards and tables | Same card and table rules as above; stat cards use serif numerals at 28px. |
| Maintenance banners | Keep functional color meaning: danger-tinted bar for maintenance, warning-tinted for degraded, using the new tokens. |
| Footer | `--bg` with a top border, muted links, generous spacing, no heavy dark band. |

---

## 8. Logo and Favicon

- Provide a simple original logo placeholder: a rounded square in `--accent` containing a letter mark, plus the product name in Inter semibold. Make it replaceable via one config entry and one asset file.
- Provide favicon and social share image placeholders using the same mark. Do not use any sparkle, asterisk-burst, or other shape resembling Claude's mark.

---

## 9. Files and Areas to Change

The agent should locate and update:

1. The theme file or Tailwind configuration that defines tokens (replace values, add new tokens).
2. Global stylesheet and font loading (self-hosted fonts, fallbacks, `font-display`).
3. Shared layout components: top bar, footer, buttons, cards, badges, inputs, tables, code block, tabs.
4. Homepage sections from `HOMEPAGE_SPEC.md` (spacing and typography only; content stays the same).
5. Dashboard shell and every page that has hard-coded colors (search for hex values and Tailwind color classes such as `indigo`, `slate`, `gray` and replace with tokens).
6. The maintenance banners and status dots to the new status tokens.

Do not change routes, API behavior, copy text (other than the bug fixes in section 2), or data models.

---

## 10. Acceptance Criteria

1. The rendered homepage text reads `OpenAI-compatible`, and a test asserts it. A codebase search finds no similar broken strings (for example bracketed fragments such as `[OI]`).
2. Code card tabs read `curl`, `Python`, `JavaScript`.
3. No hex color or color-named Tailwind class (`indigo`, `blue`, `slate`, and so on) remains outside the theme/token definitions. A lint rule or search check enforces this.
4. Light and dark themes both use the warm palette in section 3.1; neither uses pure `#FFFFFF` or `#000000` as the page background.
5. The accent is changeable from one token (change `--accent` and all primary buttons, links, focus rings, and selected states update). Default is the deep green, not indigo or orange.
6. Automated contrast checks pass for all text and background pairs, including button text on accent, in both themes.
7. Headlines on public pages and page titles in the dashboard render in the serif font; body, tables, and buttons render in Inter; code and numbers in JetBrains Mono. Fonts are self-hosted and no request goes to a third-party font CDN.
8. Vertical spacing follows the scale; the gap between hero and first section is at most 96px on desktop; layout has no horizontal page scroll at 375px width.
9. First load follows system theme with no flash of the wrong theme; toggle persists on reload.
10. Cards use 16px radius, buttons and inputs 10px; no shadows except on menus and dialogs; no gradients other than the optional subtle hero one.
11. `prefers-reduced-motion` disables transitions.
12. A production build fails if `PUBLIC_BASE_URL` contains `127.0.0.1` or `localhost`.
13. The interface contains no Claude or Anthropic logo, mark, wordmark, or orange accent, and no text implying affiliation.
14. Maintenance banners and status dots remain clearly distinguishable (normal, degraded, maintenance) in both themes, including for color-blind users (use icons or labels, not color alone).
15. Visual regression screenshots of the homepage, models page, request access form, login, and three main dashboard pages are captured in both themes and attached to the change summary.

---

## 11. Build Order

| Step | Deliverable |
|---|---|
| V1 | Fix bugs B1, B2, B5 and add their tests |
| V2 | New tokens, theme file, theme switching without flash, self-hosted fonts |
| V3 | Shared components restyled (buttons, cards, inputs, badges, tables, code block, tabs, top bar, footer) |
| V4 | Homepage and public pages restyled; spacing fix (B4) |
| V5 | Dashboard shell and pages restyled; remove hard-coded colors |
| V6 | Contrast, lint, and visual checks; screenshots; final report |

---

## 12. Instructions for the Building Agent

1. Read this file together with `HOMEPAGE_SPEC.md`. Keep all content and behavior; change only the visual layer and the listed bugs.
2. Work through steps V1 to V6 in order and report after each: what changed, what you tested, and any gaps.
3. Do not introduce new third-party scripts, fonts from CDNs, or analytics.
4. If a design decision is unclear, choose the calmer, simpler option and note it in `QUESTIONS.md`.
5. Do not imitate Claude or Anthropic branding beyond the general warmth and calmness described here.

---

## 13. Open Items (Owner to Confirm; Agent Uses Defaults Until Answered)

| Item | Default |
|---|---|
| Accent color | Deep green (`#2F6F5E`) |
| Serif font | Newsreader |
| Default theme | Follow system preference |
| Product name and logo | Current name "Private LLM Gateway" with the placeholder mark; owner to supply final logo |
| Dashboard uses serif for page titles | Yes |
| Hero background | Flat `--bg`, optional subtle gradient off by default |
