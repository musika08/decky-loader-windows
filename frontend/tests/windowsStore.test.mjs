// Run with: pnpm test (or node --test "tests/*.test.mjs") from frontend/
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { WINDOWS_STORE_URL, mergePluginLists, mergeVersionLists } from '../src/windowsStore.ts';

const official = [
  { id: 1, name: 'CSS Loader', versions: [], author: 'x', description: '', tags: [], image_url: '' },
  { id: 2, name: 'Overdrive', versions: [], author: 'someone else', description: '', tags: [], image_url: '' },
];
const ours = [
  { id: 900001, name: 'Overdrive', versions: [{ name: '0.1.0', hash: 'a', artifact: 'u' }], author: 'Musika', description: '', tags: [], image_url: '' },
  { id: 900002, name: 'Control Center', versions: [], author: 'Musika', description: '', tags: [], image_url: '' },
];

test('the Windows store address', () => {
  assert.equal(WINDOWS_STORE_URL, 'https://musika08.github.io/decky-windows-store/plugins');
});

test('our plugins are added after the official ones; ours wins a name clash', () => {
  const merged = mergePluginLists(official, ours);
  assert.deepEqual(merged.map((p) => p.name), ['CSS Loader', 'Overdrive', 'Control Center']);
  assert.equal(merged.find((p) => p.name === 'Overdrive').author, 'Musika');
});

test('one list missing still gives the other', () => {
  assert.deepEqual(mergePluginLists(null, ours).map((p) => p.name), ['Overdrive', 'Control Center']);
  assert.deepEqual(mergePluginLists(official, undefined).map((p) => p.name), ['CSS Loader', 'Overdrive']);
  assert.deepEqual(mergePluginLists(null, null), []);
  assert.deepEqual(mergePluginLists({ error: 'x' }, ours).length, 2);   // a non-list reply counts as missing
});

test('version lists merge, ours wins a name clash', () => {
  const merged = mergeVersionLists({ 'CSS Loader': ['1.0.0', 'h1'], Overdrive: ['9.9.9', 'h2'] },
    { Overdrive: ['0.1.0', 'h3', 'u3'], 'Control Center': ['0.1.0', 'h4', 'u4'] });
  assert.deepEqual(merged, {
    'CSS Loader': ['1.0.0', 'h1'],
    Overdrive: ['0.1.0', 'h3', 'u3'],
    'Control Center': ['0.1.0', 'h4', 'u4'],
  });
  assert.deepEqual(mergeVersionLists(null, { A: ['1', 'h'] }), { A: ['1', 'h'] });
  assert.deepEqual(mergeVersionLists(['bad'], null), {});
});
