// Run with: node --test tests/frontend
// Node 22.18+ / 23.6+ strip TypeScript types natively, so the pure module is imported straight from src.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateCurve, minSpeedAt, stepValue, liveLine, defaultDraft } from '../../src/curve.ts';

const TEMPS = [30, 40, 50, 60, 70, 80, 90, 100];
const SPEEDS = [0, 10, 20, 30, 40, 50, 60, 70];

test('curve_editor_blocks_decreasing_points', () => {
  const speeds = [...SPEEDS];
  speeds[4] = 25; // point 5 below point 4 (30)
  assert.equal(validateCurve(TEMPS, speeds), "Point 5: fan speed can't be lower than point 4.");
});

test('curve_editor_blocks_non_rising_temps', () => {
  const temps = [...TEMPS];
  temps[3] = 50; // point 4 equal to point 3
  assert.equal(validateCurve(temps, SPEEDS), 'Point 4: temperature must be higher than point 3.');
  temps[3] = 45;
  assert.equal(validateCurve(temps, SPEEDS), 'Point 4: temperature must be higher than point 3.');
});

test('valid_curve_passes', () => {
  assert.equal(validateCurve(TEMPS, SPEEDS), null);
  const d = defaultDraft();
  assert.equal(validateCurve(d.temps, d.speeds), null);
});

test('curve messages match Validation.CheckCurve', () => {
  assert.equal(validateCurve(TEMPS.slice(0, 7), SPEEDS), 'The fan curve needs 8 points.');
  assert.equal(validateCurve(TEMPS, SPEEDS.slice(0, 7)), 'The fan curve needs 8 points.');
  assert.equal(validateCurve([19, ...TEMPS.slice(1)], SPEEDS), 'Point 1: temperature must be between 20 and 110 °C.');
  assert.equal(validateCurve([...TEMPS.slice(0, 7), 111], SPEEDS), 'Point 8: temperature must be between 20 and 110 °C.');
  assert.equal(validateCurve(TEMPS, [...SPEEDS.slice(0, 7), 101]), 'Point 8: fan speed must be between 0 and 100 %.');
  assert.equal(validateCurve(TEMPS, [-1, ...SPEEDS.slice(1)]), 'Point 1: fan speed must be between 0 and 100 %.');
  // At 100 °C G-Helper's minimum is ceil(30 * 1.2) = 36 %.
  assert.equal(validateCurve(TEMPS, [0, 10, 20, 30, 30, 30, 30, 30]), 'Point 8: at 100 °C the fan must be at least 36 %.');
  assert.equal(validateCurve(TEMPS, [0, 1, 2, 3, 4, 12, 23, 70]), 'Point 7: at 90 °C the fan must be at least 24 %.');
});

test('curve_editor_blocks_non_integer_entries', () => {
  const msg = (n) => `Point ${n}: temperature and fan speed must be whole numbers.`;
  assert.equal(validateCurve([NaN, ...TEMPS.slice(1)], SPEEDS), msg(1));
  assert.equal(validateCurve(TEMPS, [...SPEEDS.slice(0, 3), undefined, ...SPEEDS.slice(4)]), msg(4));
  assert.equal(validateCurve(TEMPS, [...SPEEDS.slice(0, 7), 70.5]), msg(8));
  assert.equal(validateCurve([...TEMPS.slice(0, 5), null, ...TEMPS.slice(6)], SPEEDS), msg(6));
  assert.equal(validateCurve(TEMPS, [...SPEEDS.slice(0, 1), '10', ...SPEEDS.slice(2)]), msg(2));
});

test('minSpeedAt mirrors Validation.MinSpeedAt', () => {
  assert.equal(minSpeedAt(70), 0);
  assert.equal(minSpeedAt(20), 0);
  assert.equal(minSpeedAt(71), 2); // 1.2 -> 2
  assert.equal(minSpeedAt(75), 6);
  assert.equal(minSpeedAt(110), 48);
});

test('stepValue clamps to the range', () => {
  assert.equal(stepValue(108, 5, 20, 110), 110);
  assert.equal(stepValue(22, -5, 20, 110), 20);
  assert.equal(stepValue(50, 1, 0, 100), 51);
});

test('liveLine formats sensors', () => {
  assert.equal(liveLine(62, 3100), 'CPU 62 °C · Fan 3100 RPM');
  assert.equal(liveLine(null, null), 'CPU -- °C · Fan -- RPM');
  assert.equal(liveLine(61.6, 0), 'CPU 62 °C · Fan 0 RPM');
});
