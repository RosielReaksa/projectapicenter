/**
 * Acceptance 3 (STYLE_UPDATE.md §10): no hex colour and no colour-named
 * Tailwind class may exist outside the token definitions.
 *
 * Fails with exit code 1 so it can gate CI.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, extname } from 'node:path'

const SRC = 'src'
const TOKENS_FILE = 'index.css'

const HEX = /#[0-9a-fA-F]{3,8}\b/g
const NAMED =
  /\b(?:bg|text|border|from|to|via|ring|fill|stroke|divide|outline|decoration|caret|placeholder|accent)-(indigo|blue|slate|gray|grey|zinc|neutral|stone|red|green|yellow|amber|emerald|teal|cyan|sky|violet|purple|fuchsia|pink|rose|orange|lime)(?:-[0-9]{2,3})?\b/g

const EXT = new Set(['.jsx', '.js', '.css'])

function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) out.push(...walk(path))
    else if (EXT.has(extname(path))) out.push(path)
  }
  return out
}

const violations = []

for (const file of walk(SRC)) {
  // index.css is where the tokens are defined; it is the only allowed home.
  if (file.endsWith(TOKENS_FILE)) continue

  const lines = readFileSync(file, 'utf8').split('\n')
  lines.forEach((line, index) => {
    for (const match of line.matchAll(HEX)) {
      violations.push(`${file}:${index + 1}  hex colour ${match[0]}`)
    }
    for (const match of line.matchAll(NAMED)) {
      violations.push(`${file}:${index + 1}  named colour ${match[0]}`)
    }
  })
}

if (violations.length) {
  console.error(`\nFound ${violations.length} hard-coded colour(s):\n`)
  violations.forEach((v) => console.error('  ' + v))
  console.error('\nUse a design token from src/index.css instead.\n')
  process.exit(1)
}

console.log('colour check: clean — every colour comes from a token')
