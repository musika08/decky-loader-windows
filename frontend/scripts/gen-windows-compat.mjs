// Regenerates src/store/windows-compat.json from the plugin Windows survey.
//
// Usage (from frontend/): node scripts/gen-windows-compat.mjs [path/to/plugin-windows-survey.json]
// The survey defaults to plugin-windows-survey.json next to the repository folder.
//
// Output shape: { ids: { "<store id>": { name, tier, reason } }, names: { "<lowercase name>": "<store id>" } }
// The store UI looks a plugin up by id (when the stored name matches) and falls back to its name,
// so testing/custom stores with different ids still get a badge.
import { readFileSync, writeFileSync } from 'fs';
import { dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

const here = dirname(fileURLToPath(import.meta.url));
const input = resolve(process.argv[2] ?? resolve(here, '../../../plugin-windows-survey.json'));
const output = resolve(here, '../src/store/windows-compat.json');
const MAX_REASON = 120;
const TIERS = new Set(['A', 'B', 'C', 'D']);

function shortReason(entry) {
  // Tier A has no real blockers, so its porting note says more; everything else explains what blocks it
  const blockers = String(entry.blockers ?? '').trim();
  let text = entry.tier === 'A' || !blockers || /^none\b/i.test(blockers) ? String(entry.port ?? '') : blockers;
  text = text
    .trim()
    .replace(/\s+/g, ' ')
    // drop a leading "main.py:" / "py_modules/foo.py:" source location
    .replace(/^[\w./-]+\.(py|sh|js|ts|tsx|json)\s*:\s*/i, '');
  // keep the first clause or sentence
  const clause = text.split(/;\s|\.\s/)[0].replace(/[.;]\s*$/, '');
  if (/^(none|nothing needed)\b/i.test(clause)) return 'No known Windows blockers';
  text = clause.length >= 20 ? clause : text;
  if (text.length > MAX_REASON) {
    const cut = text.slice(0, MAX_REASON - 3);
    const space = cut.lastIndexOf(' ');
    text = (space > 60 ? cut.slice(0, space) : cut).replace(/[\s,;:(]+$/, '') + '...';
  }
  // don't capitalise a leading file name such as main.py or bin/backend
  return /^\S*[./_]/.test(text) ? text : text.charAt(0).toUpperCase() + text.slice(1);
}

const survey = JSON.parse(readFileSync(input, 'utf-8'));
const ids = {};
const names = {};
for (const entry of [...survey].sort((a, b) => a.id - b.id)) {
  if (!TIERS.has(entry.tier)) throw new Error(`unknown tier ${entry.tier} for ${entry.name}`);
  const id = String(entry.id);
  ids[id] = { name: entry.name, tier: entry.tier, reason: shortReason(entry) };
  names[entry.name.toLowerCase()] = id;
}

writeFileSync(output, JSON.stringify({ ids, names }, null, 2) + '\n');
console.log(`wrote ${Object.keys(ids).length} plugins to ${output}`);
