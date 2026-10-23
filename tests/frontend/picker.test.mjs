// Run with: node --test "tests/frontend/*.test.mjs"
// Inline picker state rules (src/picker.ts): replaces the popup dropdowns for Fan preset and FPS limit.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { valueLabel, rowValue, pressRow, choose, pressCancel, focusIndex } from '../../src/picker.ts';
import { FAN_PRESETS as PRESETS } from '../../src/curve.ts';
const FPS = [0, 30, 60].map((f) => ({ data: f, label: f === 0 ? 'Off' : f + ' FPS' }));

test('row shows the current value and a chevron', () => {
  assert.equal(valueLabel(PRESETS, 'recommended'), 'Recommended');
  assert.equal(rowValue(PRESETS, 'recommended', false), 'Recommended ▾');
  assert.equal(rowValue(PRESETS, 'recommended', true), 'Recommended ▴');
  assert.equal(rowValue(FPS, 0, false), 'Off ▾', '0 is a real value, not missing');
  assert.equal(valueLabel(PRESETS, 'loud'), '');
  assert.equal(rowValue(PRESETS, 'loud', false), '▾');
});

test('dropdown order: Armoury default, Recommended, Quiet, Cool, Custom', () => {
  assert.deepEqual(PRESETS.map((p) => p.label), ['Armoury default', 'Recommended', 'Quiet', 'Cool', 'Custom']);
  assert.deepEqual(PRESETS.map((p) => p.data), ['default', 'recommended', 'quiet', 'cool', 'custom']);
});

test('pressing the row opens, pressing it again closes; disabled stays closed', () => {
  assert.equal(pressRow(false), true);
  assert.equal(pressRow(true), false);
  assert.equal(pressRow(false, true), false);
  assert.equal(pressRow(true, true), false);
});

test('choosing collapses and applies only a different value', () => {
  assert.deepEqual(choose('default', 'recommended'), { open: false, apply: true });
  assert.deepEqual(choose('recommended', 'recommended'), { open: false, apply: false });
  assert.deepEqual(choose(60, 0), { open: false, apply: true });
});

test('B collapses an open list without a change and is left alone while closed', () => {
  assert.deepEqual(pressCancel(true), { open: false, handled: true });
  assert.deepEqual(pressCancel(false), { open: false, handled: false });
});

test('focus goes to the current option while open and back to the row when closed', () => {
  assert.equal(focusIndex(PRESETS, 'quiet', true), 2);
  assert.equal(focusIndex(PRESETS, 'quiet', false), -1);
  assert.equal(focusIndex(FPS, 0, true), 0);
  assert.equal(focusIndex(PRESETS, 'loud', true), 0, 'unknown value: first option');
  assert.equal(focusIndex([], 'x', true), -1);
});
