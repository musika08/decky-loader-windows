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
  directionAction,
  pressA,
  pressB,
  aLabel,
  linePath,
  fillPath,
  floorPath,
  markerX,
  markerLabel,
  tailPath,
  tailFillPath,
  dotPoints,
  FANS,
  stackBadge,
  hasStacks,
  spreadPoints,
  selectNext as stepSelect,
  markerFor,
} from '../../src/fanChart.ts';
import { validateCurve, minSpeedAt, CURVE_TEMP_MIN, CURVE_TEMP_MAX, CURVE_SPEED_MAX, RECOMMENDED_CURVE } from '../../src/curve.ts';

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
  assert.equal(p.title, 'Point 3 of 8 · 59 °C · 18 %');
  assert.match(p.hint, /^Editing: .* · B to finish$/);
  const idle = caption(c, 2, 'idle');
  assert.equal(idle.title, 'Point 3 of 8 · 59 °C · 18 %');
  assert.equal(idle.hint, 'Press A to edit the curve');
  const t = caption(c, 2, 'temp');
  assert.equal(t.title, 'Point 3 of 8 · 59 °C · 18 %');
  assert.match(t.hint, /^Editing temperature: .* · B to finish$/);
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
  assert.equal(markerLabel(57.4, 'CPU '), 'CPU 57 °C', 'the GPU fan chart names the CPU sensor');
});

test('focused but not engaged: every D-pad direction navigates (no trap)', () => {
  for (const d of ['up', 'down', 'left', 'right']) assert.deepEqual(directionAction('idle', d), { kind: 'navigate' });
  assert.equal(aLabel('idle'), 'Edit curve');
});

test('A engages, then toggles point and temperature mode', () => {
  assert.equal(pressA('idle'), 'point');
  assert.equal(pressA('point'), 'temp');
  assert.equal(pressA('temp'), 'point');
  assert.deepEqual(directionAction('point', 'left'), { kind: 'select', dir: -1 });
  assert.deepEqual(directionAction('point', 'right'), { kind: 'select', dir: 1 });
  assert.deepEqual(directionAction('point', 'up'), { kind: 'speed', delta: 1 });
  assert.deepEqual(directionAction('point', 'down'), { kind: 'speed', delta: -1 });
  assert.deepEqual(directionAction('temp', 'left'), { kind: 'temp', delta: -1 });
  assert.deepEqual(directionAction('temp', 'right'), { kind: 'temp', delta: 1 });
  assert.deepEqual(directionAction('temp', 'up'), { kind: 'speed', delta: 1 });
});

test('B finishes editing from either mode and is left alone while idle', () => {
  assert.deepEqual(pressB('point'), { mode: 'idle', handled: true });
  assert.deepEqual(pressB('temp'), { mode: 'idle', handled: true });
  assert.deepEqual(pressB('idle'), { mode: 'idle', handled: false });
  // A full round: engage, temperature, finish, and the D-pad navigates again.
  let m = 'idle';
  m = pressA(m);
  m = pressA(m);
  m = pressB(m).mode;
  assert.deepEqual(directionAction(m, 'down'), { kind: 'navigate' });
});

test('the line continues flat from the last point to 110 °C', () => {
  const tail = tailPath(CURVE);
  assert.equal(tail, 'M' + r1(xOf(100)) + ' ' + r1(yOf(70)) + ' L' + r1(xOf(110)) + ' ' + r1(yOf(70)));
  const fill = tailFillPath(CURVE);
  assert.ok(fill.startsWith(tail) && fill.endsWith('Z'));
  assert.ok(fill.includes('L' + r1(xOf(110)) + ' ' + r1(PLOT.y1)), 'down to the 0 % axis at 110 °C');
  // Stacked firmware curve: the tail starts at the stack (62 °C, 22 %).
  assert.equal(tailPath(STACKED), 'M' + r1(xOf(62)) + ' ' + r1(yOf(22)) + ' L' + r1(xOf(110)) + ' ' + r1(yOf(22)));
  // A last point already at 110 °C has nothing to extend.
  const full = { temps: [30, 40, 50, 60, 70, 80, 90, 110], speeds: [0, 10, 20, 30, 40, 50, 60, 70] };
  assert.equal(tailPath(full), '');
  assert.equal(tailFillPath(full), '');
  assert.equal(tailPath({ temps: [], speeds: [] }), '');
});

test('stacked points draw as one dot', () => {
  const dots = dotPoints(STACKED);
  assert.equal(dots.length, 4, '48, 54, 59 and the 62 °C stack');
  assert.deepEqual(dots[3], { index: 7, indices: [3, 4, 5, 6, 7], temp: 62, speed: 22 });
  assert.equal(dots[3].index, nearestPoint(STACKED, xOf(62), yOf(22)).index, 'the dot picks what a press picks');
  assert.equal(dotPoints(CURVE).length, 8, 'distinct points stay separate');
  // Same temperature, different speed: two dots (validation flags it; the chart still shows both).
  const split = { temps: [30, 30], speeds: [10, 20] };
  assert.equal(dotPoints(split).length, 2);
});

test('fan switch lists the CPU fan first, then the GPU fan', () => {
  assert.deepEqual(FANS.map((f) => f.fan), ['cpu', 'gpu']);
  assert.deepEqual(FANS.map((f) => f.label), ['CPU fan', 'GPU fan']);
});

// ---------- stacked points: badge, caption, Spread points ----------

const SILENT = { temps: [47, 49, 54, 54, 54, 54, 54, 54], speeds: [1, 9, 13, 13, 13, 13, 13, 13] };
const GPU_BALANCED = { temps: [48, 54, 59, 62, 62, 62, 62, 62], speeds: [1, 9, 13, 13, 13, 13, 13, 13] };
const TURBO = { temps: [48, 55, 58, 62, 66, 73, 73, 94], speeds: [9, 26, 39, 48, 56, 65, 65, 78] };

test('stack badge: one dot with x-N for stacked points, none for single points', () => {
  assert.deepEqual(dotPoints(STACKED).map((d) => stackBadge(d.indices.length)), ['', '', '', '×5']);
  assert.deepEqual(dotPoints(SILENT).map((d) => stackBadge(d.indices.length)), ['', '', '×6']);
  assert.deepEqual(dotPoints(TURBO).map((d) => stackBadge(d.indices.length)), ['', '', '', '', '', '×2', '']);
  assert.ok(dotPoints(CURVE).every((d) => stackBadge(d.indices.length) === ''));
  assert.equal(stackBadge(1), '');
  assert.equal(stackBadge(0), '');
});

test('caption reads Point K of 8 and the D-pad steps through every stacked point', () => {
  const seen = [];
  let i = 0;
  for (let n = 0; n < 10; n++) {
    seen.push(caption(STACKED, i, 'point').title);
    i = stepSelect(i, 1, 8);
  }
  assert.deepEqual(seen.slice(0, 8), [
    'Point 1 of 8 · 48 °C · 1 %',
    'Point 2 of 8 · 54 °C · 9 %',
    'Point 3 of 8 · 59 °C · 18 %',
    'Point 4 of 8 · 62 °C · 22 %',
    'Point 5 of 8 · 62 °C · 22 %',
    'Point 6 of 8 · 62 °C · 22 %',
    'Point 7 of 8 · 62 °C · 22 %',
    'Point 8 of 8 · 62 °C · 22 %',
  ]);
  assert.equal(seen[9], 'Point 8 of 8 · 62 °C · 22 %', 'stops at the last point');
  assert.equal(caption(STACKED, 5, 'idle').title, 'Point 6 of 8 · 62 °C · 22 %');
});

test('hasStacks finds neighbouring identical points only', () => {
  assert.equal(hasStacks(STACKED), true);
  assert.equal(hasStacks(SILENT), true);
  assert.equal(hasStacks(TURBO), true);
  assert.equal(hasStacks(CURVE), false);
  assert.equal(hasStacks(RECOMMENDED_CURVE), false);
});

function checkSpread(before, after) {
  assert.equal(validateCurve(after.temps, after.speeds), null, JSON.stringify(after));
  assert.equal(hasStacks(after), false);
  for (let i = 1; i < 8; i++) assert.ok(after.temps[i] > after.temps[i - 1], 'strictly increasing');
  for (let i = 0; i < 8; i++) {
    assert.ok(Number.isInteger(after.temps[i]));
    assert.ok(after.speeds[i] >= minSpeedAt(after.temps[i]));
  }
  assert.notEqual(after, before);
}

test('Spread points: Ally balanced CPU curve', () => {
  const r = spreadPoints(STACKED, minSpeedAt);
  assert.deepEqual(r, { temps: [48, 54, 59, 62, 72, 81, 91, 100], speeds: [1, 9, 18, 22, 22, 22, 26, 36] });
  checkSpread(STACKED, r);
  assert.deepEqual(STACKED.temps, [48, 54, 59, 62, 62, 62, 62, 62], 'input untouched');
});

test('Spread points: Ally balanced GPU curve', () => {
  const r = spreadPoints(GPU_BALANCED, minSpeedAt);
  assert.deepEqual(r, { temps: [48, 54, 59, 62, 72, 81, 91, 100], speeds: [1, 9, 13, 13, 13, 14, 26, 36] });
  checkSpread(GPU_BALANCED, r);
});

test('Spread points: Ally silent curve', () => {
  const r = spreadPoints(SILENT, minSpeedAt);
  // 54 + 46 * m / 5 -> 63.2, 72.4, 81.6, 90.8, 100
  assert.deepEqual(r, { temps: [47, 49, 54, 63, 72, 82, 91, 100], speeds: [1, 9, 13, 13, 13, 15, 26, 36] });
  checkSpread(SILENT, r);
});

test('Spread points: a stack inside the curve spreads before the next point and keeps it', () => {
  const r = spreadPoints(TURBO, minSpeedAt);
  assert.deepEqual(r, { temps: [48, 55, 58, 62, 66, 73, 84, 94], speeds: [9, 26, 39, 48, 56, 65, 65, 78] });
  checkSpread(TURBO, r);
});

test('Spread points: no room near the top goes to 110 °C, no room at all leaves the stack', () => {
  const high = { temps: [60, 70, 80, 90, 98, 98, 98, 98], speeds: [10, 20, 30, 40, 50, 50, 50, 50] };
  const r = spreadPoints(high, minSpeedAt);
  assert.deepEqual(r.temps, [60, 70, 80, 90, 98, 102, 106, 110]);
  checkSpread(high, r);
  const full = { temps: [104, 104, 104, 104, 104, 104, 104, 104], speeds: [50, 50, 50, 50, 50, 50, 50, 50] };
  assert.deepEqual(spreadPoints(full, minSpeedAt), full, '7 points do not fit in 105..110');
  assert.deepEqual(spreadPoints(CURVE, minSpeedAt), CURVE, 'nothing stacked: unchanged');
});

test('Recommended preset curve is valid and meets the G-Helper minimum', () => {
  assert.deepEqual(RECOMMENDED_CURVE, {
    temps: [40, 50, 60, 65, 70, 75, 80, 90],
    speeds: [10, 15, 25, 35, 45, 55, 70, 90],
  });
  assert.equal(validateCurve(RECOMMENDED_CURVE.temps, RECOMMENDED_CURVE.speeds), null);
  assert.deepEqual([75, 80, 90].map(minSpeedAt), [6, 12, 24]);
});

test('chart marker: GPU chart uses the GPU temperature when reported, else the CPU one', () => {
  assert.deepEqual(markerFor('gpu', 51, 49), { temp: 49, prefix: 'GPU ' });
  assert.equal(markerLabel(49, markerFor('gpu', 51, 49).prefix), 'GPU 49 °C');
  assert.deepEqual(markerFor('gpu', 51, null), { temp: 51, prefix: 'CPU ' });
  assert.deepEqual(markerFor('gpu', 51, undefined), { temp: 51, prefix: 'CPU ' });
  assert.deepEqual(markerFor('cpu', 51, 49), { temp: 51, prefix: '' });
  assert.deepEqual(markerFor('cpu', null, 49), { temp: null, prefix: '' });
  assert.deepEqual(markerFor('gpu', null, NaN), { temp: null, prefix: 'CPU ' });
});
