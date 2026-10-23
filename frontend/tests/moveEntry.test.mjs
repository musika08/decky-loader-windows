// Run with: pnpm test (or node --test "tests/*.test.mjs") from frontend/; needs Node 22.18+ / 23.6+ for built-in TypeScript type stripping
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { moveEntry, moveTargets } from '../src/components/settings/pages/plugin_list/moveEntry.ts';

const order = ['a', 'b', 'c', 'd'];

test('moves to the top', () => {
  assert.deepEqual(moveEntry(order, 'c', 0), ['c', 'a', 'b', 'd']);
});

test('moves to the bottom', () => {
  assert.deepEqual(moveEntry(order, 'b', 4), ['a', 'c', 'd', 'b']);
});

test('moves down into a middle gap', () => {
  // gap 3 is just above 'd'
  assert.deepEqual(moveEntry(order, 'a', 3), ['b', 'c', 'a', 'd']);
});

test('moves up into a middle gap', () => {
  // gap 1 is just above 'b'
  assert.deepEqual(moveEntry(order, 'd', 1), ['a', 'd', 'b', 'c']);
});

test('adjacent gaps leave the order unchanged', () => {
  assert.deepEqual(moveEntry(order, 'b', 1), order);
  assert.deepEqual(moveEntry(order, 'b', 2), order);
});

test('unknown name or bad gap returns an unchanged copy', () => {
  for (const [name, gap] of [
    ['zzz', 0],
    ['a', -1],
    ['a', 5],
    ['a', 1.5],
  ]) {
    const result = moveEntry(order, name, gap);
    assert.deepEqual(result, order);
    assert.notEqual(result, order);
  }
});

test('does not mutate the input', () => {
  const input = [...order];
  moveEntry(input, 'a', 4);
  assert.deepEqual(input, order);
});

test('every offered target actually changes the order', () => {
  for (let from = 0; from < order.length; from++) {
    const targets = moveTargets(order.length, from);
    assert.equal(targets.length, order.length - 1);
    for (const gap of targets) {
      assert.notDeepEqual(moveEntry(order, order[from], gap), order);
    }
  }
});

test('targets skip the two gaps touching the entry', () => {
  assert.deepEqual(moveTargets(4, 0), [2, 3, 4]);
  assert.deepEqual(moveTargets(4, 2), [0, 1, 4]);
  assert.deepEqual(moveTargets(4, 3), [0, 1, 2]);
  assert.deepEqual(moveTargets(1, 0), []);
});
