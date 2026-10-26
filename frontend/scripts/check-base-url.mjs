/**
 * B5 guard (STYLE_UPDATE.md §2, acceptance 12).
 *
 * A production build must not ship a localhost base URL: the homepage code
 * samples and the sitemap would tell partners to call 127.0.0.1.
 *
 * PUBLIC_BASE_URL is a backend setting, so this check runs at build time from
 * the same environment variable and fails `npm run build` when it is wrong.
 */

const raw = (process.env.PUBLIC_BASE_URL || '').trim()
const isProduction = process.env.NODE_ENV === 'production'
const isDev = process.env.VITE_ALLOW_LOCAL_BASE_URL === 'true'

const localPattern = /(127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])/i

if (!isProduction) {
  console.log('[base-url-check] skipped (not a production build)')
  process.exit(0)
}

if (isDev) {
  console.log('[base-url-check] skipped (VITE_ALLOW_LOCAL_BASE_URL=true)')
  process.exit(0)
}

if (!raw) {
  console.error(
    '\n[base-url-check] PUBLIC_BASE_URL is not set.\n' +
      'Set it to your public origin before building for production, e.g.\n' +
      '  PUBLIC_BASE_URL=https://gw.example.com npm run build\n' +
      'For a local build, set VITE_ALLOW_LOCAL_BASE_URL=true.\n',
  )
  process.exit(1)
}

if (localPattern.test(raw)) {
  console.error(
    `\n[base-url-check] PUBLIC_BASE_URL contains a local address: ${raw}\n` +
      'A production build must use the public domain.\n',
  )
  process.exit(1)
}

if (!/^https:\/\//i.test(raw)) {
  console.error(
    `\n[base-url-check] PUBLIC_BASE_URL must be an https:// URL, got: ${raw}\n`,
  )
  process.exit(1)
}

console.log(`[base-url-check] ok: ${raw}`)
