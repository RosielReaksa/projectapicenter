/**
 * Contrast check for the design tokens (STYLE_UPDATE.md §3.2, acceptance 6).
 *
 * Computes WCAG 2.1 contrast ratios for every text/background pair the UI
 * actually uses, in both themes. Fails if any pair is below 4.5:1.
 *
 * Values mirror src/index.css. If you change a token there, change it here.
 */

const TOKENS = {
  light: {
    bg: '#FAF9F5',
    surface: '#F3F1EA',
    surfaceRaised: '#FFFFFF',
    border: '#E6E2D6',
    borderStrong: '#D3CEBF',
    text: '#1F1E1B',
    muted: '#6B675E',
    accent: '#2F6F5E',
    accentHover: '#265A4D',
    accentSoft: '#E4EFEA',
    onAccent: '#FFFFFF',
    success: '#276B44',
    warning: '#7E560E',
    danger: '#9E3727',
  },
  dark: {
    bg: '#1F1E1C',
    surface: '#2A2927',
    surfaceRaised: '#33312E',
    border: '#3A3834',
    borderStrong: '#4A4742',
    text: '#EFEDE6',
    muted: '#A19D92',
    accent: '#5FB39B',
    accentHover: '#7CC8B2',
    accentSoft: '#26352F',
    onAccent: '#10201B',
    success: '#6CC38C',
    warning: '#E0B04C',
    danger: '#E8806E',
  },
}

function srgbToLinear(c) {
  const v = c / 255
  return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
}

function luminance(hex) {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b)
}

function ratio(fg, bg) {
  const a = luminance(fg)
  const b = luminance(bg)
  const [light, dark] = a > b ? [a, b] : [b, a]
  return (light + 0.05) / (dark + 0.05)
}

// Blend a translucent tint over a base, as the badges do.
function blend(fgHex, bgHex, alpha) {
  const f = fgHex.replace('#', '')
  const b = bgHex.replace('#', '')
  const mix = (i) =>
    Math.round(
      parseInt(f.slice(i, i + 2), 16) * alpha + parseInt(b.slice(i, i + 2), 16) * (1 - alpha),
    )
  return (
    '#' +
    [0, 2, 4]
      .map((i) => mix(i).toString(16).padStart(2, '0'))
      .join('')
  )
}

// Every pair the interface actually renders.
const PAIRS = [
  ['body text on page', 'text', 'bg'],
  ['body text on card', 'text', 'surface'],
  ['body text on raised', 'text', 'surfaceRaised'],
  ['muted text on page', 'muted', 'bg'],
  ['muted text on card', 'muted', 'surface'],
  ['muted text on raised', 'muted', 'surfaceRaised'],
  ['link/accent text on page', 'accent', 'bg'],
  ['link/accent text on card', 'accent', 'surface'],
  ['accent text on accent-soft', 'accent', 'accentSoft'],
  ['button text on accent', 'onAccent', 'accent'],
  ['button text on accent hover', 'onAccent', 'accentHover'],
  ['success text on page', 'success', 'bg'],
  ['success text on card', 'success', 'surface'],
  ['warning text on page', 'warning', 'bg'],
  ['warning text on card', 'warning', 'surface'],
  ['danger text on page', 'danger', 'bg'],
  ['danger text on card', 'danger', 'surface'],
]

const TINTS = [
  ['success badge text', 'success', 'bg', 0.14],
  ['warning badge text', 'warning', 'bg', 0.16],
  ['danger badge text', 'danger', 'bg', 0.14],
]

const MIN = 4.5
let failures = 0

for (const [themeName, t] of Object.entries(TOKENS)) {
  console.log(`\n${themeName.toUpperCase()}`)
  for (const [label, fgKey, bgKey] of PAIRS) {
    const r = ratio(t[fgKey], t[bgKey])
    const ok = r >= MIN
    if (!ok) failures++
    console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${r.toFixed(2).padStart(5)}:1  ${label}`)
  }
  for (const [label, fgKey, bgKey, alpha] of TINTS) {
    const bg = blend(t[fgKey], t[bgKey], alpha)
    const r = ratio(t[fgKey], bg)
    const ok = r >= MIN
    if (!ok) failures++
    console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${r.toFixed(2).padStart(5)}:1  ${label}`)
  }
}

console.log(
  failures === 0
    ? '\ncontrast: all pairs pass at 4.5:1 or better'
    : `\ncontrast: ${failures} pair(s) below ${MIN}:1`,
)
process.exit(failures === 0 ? 0 : 1)
