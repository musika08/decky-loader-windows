// Run with: node --test "tests/frontend/*.test.mjs"
// Pure chart maths for the fan curve chart (src/fanChart.ts), imported straight from src like curve.test.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  CHART,
  PLOT,
  TEMP_MIN,
  TEMP_MAX,
  xOf,
  yOf,
  tempAt,
  speedAt,
  toChart,
  clampRound,
  dragTo,
  movePoint,
  selectNext,
  nearestPoint,
  hitTest,
  invalidPoint,
  caption,
  linePath,
  fillPath,
  floorPath,
  markerX,
  markerLabel,
} from '../../src/fanChart.ts';
import { validateCurve, minSpeedAt, CURVE_TEMP_MIN, CURVE_TEMP_MAX, CURVE_SPEED_MAX } from '../../src/curve.ts';

const r1 = (n) => Math.round(n * 10) / 10;
const CURVE = { temps: [30, 40, 50, 60, 70, 80, 90, 100], speeds: [0, 10, 20, 30, 40, 50, 60, 70] };
// Ally firmware curve with stacked points 4..8 (62 °C, 22 %).
const STACKED = { temps: [48, 54, 59, 62, 62, 62, 62, 62], speeds: [1, 9, 18, 22, 22, 22, 22, 22] };

test('chart range matches the curve rules', () => {
  assert.equal(TEMP_MIN, CURVE_TEMP_MIN);
  assert.equal(TEMP_MAX, CURVE_TEMP_MAX);
  assert.equal(yOf(CURVE_SPEED_MAX), PLOT.y0);
});

test('x and y scales round-trip and hit the plot edges', () => {
  for (let t = 20; t <= 110; t++) assert.ok(Math.abs(tempAt(xOf(t)) - t) < 1e-9);
  for (let s = 0; s <= 100; s++) assert.ok(Math.abs(speedAt(yOf(s)) - s) < 1e-9);
  assert.equal(xOf(20), CHART.left);
  assert.equal(xOf(110), CHART.width - CHART.right);
  assert.equal(yOf(100), CHART.top);
  assert.equal(yOf(0), CHART.height - CHART.bottom);
  assert.ok(xOf(50) < xOf(60), 'hotter is further right');
  assert.ok(yOf(60) < yOf(50), 'faster is higher up');
});

test('client positions convert through the rendered size', () => {
  const rect = { left: 100, top: 50, width: CHART.width * 2, height: CHART.height * 2 };
  const p = toChart(100 + xOf(59) * 2, 50 + yOf(18) * 2, rect);
  assert.ok(Math.abs(p.x - xOf(59)) < 1e-9);
  assert.ok(Math.abs(p.y - yOf(18)) < 1e-9);
  // A zero-size box (not laid out yet) does not divide by zero.
  const z = toChart(10, 10, { left: 0, top: 0, width: 0, height: 0 });
  assert.ok(isFinite(z.x) && isFinite(z.y));
});

test('clampRound snaps to whole numbers inside the range', () => {
  assert.equal(clampRound(42.4, 20, 110), 42);
  assert.equal(clampRound(42.5, 20, 110), 43);
  assert.equal(clampRound(-5, 0, 100), 0);
  assert.equal(clampRound(250, 0, 100), 100);
  assert.equal(clampRound(NaN, 20, 110), 20);
});

test('dragging a point snaps and clamps to 20..110 °C and 0..100 %', () => {
  const moved = dragTo(CURVE, 2, xOf(53.4), yOf(27.6));
  assert.equal(moved.temps[2], 53);
  assert.equal(moved.speeds[2], 28);
  const far = dragTo(CURVE, 2, -500, -500); // left of and above the plot
  assert.equal(far.temps[2], 20);
  assert.equal(far.speeds[2], 100);
  const below = dragTo(CURVE, 7, 9999, 9999);
  assert.equal(below.temps[7], 110);
  assert.equal(below.speeds[7], 0);
  // Only the dragged point changes, and the input is not mutated.
  assert.deepEqual(moved.temps.filter((_, i) => i !== 2), CURVE.temps.filter((_, i) => i !== 2));
  assert.deepEqual(CURVE.temps, [30, 40, 50, 60, 70, 80, 90, 100]);
  // Out-of-range index leaves the curve alone.
  assert.equal(dragTo(CURVE, 8, 0, 0), CURVE);
});

test('stepping the selected point keeps the curve editable', () => {
  let c = CURVE;
  for (let k = 0; k < 15; k++) c = movePoint(c, 3, 0, 1); // hold up 15 times
  assert.equal(c.speeds[3], 45);
  assert.equal(validateCurve(c.temps, c.speeds), "Point 5: fan speed can't be lower than point 4.");
  assert.equal(invalidPoint(validateCurve(c.temps, c.speeds)), 4);
  // Walking it back down makes the curve valid again: nothing got stuck.
  for (let k = 0; k < 15; k++) c = movePoint(c, 3, 0, -1);
  assert.deepEqual(c, CURVE);
  assert.equal(validateCurve(c.temps, c.speeds), null);
  // Clamped at the ends, still whole numbers.
  let t = CURVE;
  for (let k = 0; k < 200; k++) t = movePoint(t, 7, 1, 1);
  assert.equal(t.temps[7], 110);
  assert.equal(t.speeds[7], 100);
  for (let k = 0; k < 200; k++) t = movePoint(t, 0, -1, -1);
  assert.equal(t.temps[0], 20);
  assert.equal(t.speeds[0], 0);
});

test('point selection stays within the 8 points', () => {
  assert.equal(selectNext(0, -1, 8), 0);
  assert.equal(selectNext(0, 1, 8), 1);
  assert.equal(selectNext(7, 1, 8), 7);
  assert.equal(selectNext(3, -1, 8), 2);
});

test('hit-testing finds the point under a press and ignores empty space', () => {
  assert.equal(hitTest(CURVE, xOf(60) + 3, yOf(30) - 3), 3);
  assert.equal(hitTest(CURVE, xOf(105), yOf(5)), -1);
  assert.equal(nearestPoint(CURVE, xOf(105), yOf(68)).index, 7);
  assert.equal(nearestPoint(CURVE, xOf(25), yOf(2)).index, 0);
});

test('stacked points: the selected one wins, else the last one', () => {
  const x = xOf(62);
  const y = yOf(22);
  assert.equal(hitTest(STACKED, x, y), 7);
  assert.equal(hitTest(STACKED, x, y, 5), 5);
  assert.equal(hitTest(STACKED, x, y, 1), 7); // selected point is not in the stack
  // Dragging the last stacked point right keeps the firmware curve valid.
  const c = dragTo(STACKED, 7, xOf(70), yOf(30));
  assert.equal(validateCurve(c.temps, c.speeds), null);
});

test('invalid point index comes from the validation reason', () => {
  assert.equal(invalidPoint(null), -1);
  assert.equal(invalidPoint(''), -1);
  assert.equal(invalidPoint('The fan curve needs 8 points.'), -1);
  const temps = [...CURVE.temps];
  temps[3] = 45;
  assert.equal(invalidPoint(validateCurve(temps, CURVE.speeds)), 3);
  const speeds = [...CURVE.speeds];
  speeds[7] = 20; // below the 100 °C floor and below point 7
  assert.equal(invalidPoint(validateCurve(CURVE.temps, speeds)), 7);
  const hot = { temps: [...CURVE.temps], speeds: [...CURVE.speeds] };
  hot.temps[7] = 110;
  hot.speeds = [0, 10, 20, 30, 40, 42, 44, 47]; // floor at 110 °C is 48 %
  assert.equal(validateCurve(hot.temps, hot.speeds), 'Point 8: at 110 °C the fan must be at least 48 %.');
  assert.equal(invalidPoint(validateCurve(hot.temps, hot.speeds)), 7);
});

test('caption names the point and the active mode', () => {
  const c = { temps: [48, 54, 59, 62, 62, 62, 62, 62], speeds: [1, 9, 18, 22, 22, 22, 22, 22] };
  const p = caption(c, 2, 'point');
  assert.equal(p.title, 'Point 3 · 59 °C · 18 %');
  assert.match(p.hint, /^Point mode/);
  const t = caption(c, 2, 'temp');
  assert.equal(t.title, 'Point 3 · 59 °C · 18 %');
  assert.match(t.hint, /^Temperature mode/);
});

test('paths: line through 8 points, closed fill, floor follows minSpeedAt', () => {
  const line = linePath(CURVE);
  assert.equal((line.match(/[ML]/g) || []).length, 8);
  assert.ok(line.startsWith('M' + r1(xOf(30)) + ' ' + r1(yOf(0))));
  const fill = fillPath(CURVE);
  assert.ok(fill.startsWith(line) && fill.endsWith('Z'));
  const floor = floorPath(minSpeedAt);
  assert.equal((floor.match(/[ML]/g) || []).length, 91);
  assert.ok(floor.includes('L' + r1(xOf(70)) + ' ' + r1(yOf(0))), 'flat at 0 % up to 70 °C');
  assert.ok(floor.endsWith('L' + r1(xOf(110)) + ' ' + r1(yOf(48))), '48 % at 110 °C');
});

test('CPU temperature marker: position, clamping and label', () => {
  assert.equal(markerX(null), null);
  assert.equal(markerX(undefined), null);
  assert.equal(markerX(NaN), null);
  assert.equal(markerX(57), xOf(57));
  assert.equal(markerX(5), xOf(20));
  assert.equal(markerX(130), xOf(110));
  assert.equal(markerLabel(57.4), '57 °C');
});
