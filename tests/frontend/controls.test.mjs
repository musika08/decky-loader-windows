// Run with: node --test tests/frontend/*.test.mjs
// Node 22.18+ / 23.6+ strip TypeScript types natively, so the pure modules are imported straight from src.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ARMOURY_BUSY,
  ARMOURY_LABEL,
  CONTROLLER_OPTIONS,
  armouryChecked,
  controllerBanner,
  controllerModeDescription,
  touchscreenCheck,
} from '../../src/controls.ts';
import { rowValue, pressRow, choose, pressCancel, focusIndex } from '../../src/picker.ts';

test('row labels and busy text', () => {
  assert.equal(ARMOURY_LABEL, 'Use Traction instead of Armoury Crate');
  assert.equal(ARMOURY_BUSY, 'Switching Armoury Crate services, this can take a few minutes');
});

test('controller mode options are Gamepad, Desktop, Auto in that order', () => {
  assert.deepEqual(CONTROLLER_OPTIONS.map((o) => o.label), ['Gamepad', 'Desktop', 'Auto']);
  assert.deepEqual(CONTROLLER_OPTIONS.map((o) => o.data), ['gamepad', 'desktop', 'auto']);
});

test('controllerModeDescription gives the three texts', () => {
  assert.equal(controllerModeDescription('gamepad'), 'The controller works as a gamepad (Steam and games).');
  assert.equal(controllerModeDescription('desktop'), 'Sticks and triggers work as a mouse; Steam menus cannot be steered.');
  assert.equal(controllerModeDescription('auto'), 'Gamepad in Steam and games, mouse on the desktop.');
});

test('touchscreenCheck waits for the device to match the target, then times out', () => {
  assert.equal(touchscreenCheck(true, true, 0), 'done');
  assert.equal(touchscreenCheck(true, false, 1500), 'wait');
  assert.equal(touchscreenCheck(undefined, false, 14999), 'wait');
  assert.equal(touchscreenCheck(true, false, 15000), 'timeout');
  assert.equal(touchscreenCheck(false, false, 20000), 'done');
});

test('blocked wins over note and disables the rows; note alone does not', () => {
  assert.deepEqual(controllerBanner('Update Overdrive', 'Armoury running'), { text: 'Update Overdrive', color: '#ff6b6b', disabled: true });
  assert.deepEqual(controllerBanner('', 'Armoury running'), { text: 'Armoury running', color: '#dcdedf', disabled: false });
  assert.deepEqual(controllerBanner(undefined, undefined), { text: '', color: '#dcdedf', disabled: false });
});

test('armoury toggle shows the stored state, or the target while switching', () => {
  assert.equal(armouryChecked(false, false), false);
  assert.equal(armouryChecked(true, false), true);
  assert.equal(armouryChecked(false, true), true);
  assert.equal(armouryChecked(true, true), false);
});

test('inline picker rules', () => {
  assert.equal(rowValue(CONTROLLER_OPTIONS, 'auto', false), 'Auto ▾');
  assert.equal(rowValue(CONTROLLER_OPTIONS, 'auto', true), 'Auto ▴');
  assert.equal(pressRow(false), true);
  assert.equal(pressRow(false, true), false);
  assert.deepEqual(choose('auto', 'auto'), { open: false, apply: false });
  assert.deepEqual(choose('auto', 'desktop'), { open: false, apply: true });
  assert.deepEqual(pressCancel(true), { open: false, handled: true });
  assert.deepEqual(pressCancel(false), { open: false, handled: false });
  assert.equal(focusIndex(CONTROLLER_OPTIONS, 'desktop', true), 1);
  assert.equal(focusIndex(CONTROLLER_OPTIONS, 'desktop', false), -1);
});
