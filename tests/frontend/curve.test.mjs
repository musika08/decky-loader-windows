// Run with: node --test tests/frontend
// Node 22.18+ / 23.6+ strip TypeScript types natively, so the pure module is imported straight from src.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateCurve, minSpeedAt, stepValue, liveLines, defaultDraft, shownMode, powerLine } from '../../src/curve.ts';

const TEMPS = [30, 40, 50, 60, 70, 80, 90, 100];
const SPEEDS = [0, 10, 20, 30, 40, 50, 60, 70];

test('curve_editor_blocks_decreasing_points', () => {
  const speeds = [...SPEEDS];
  speeds[4] = 25; // point 5 below point 4 (30)
  assert.equal(validateCurve(TEMPS, speeds), "Point 5: fan speed can't be lower than point 4.");
});

test('curve_editor_blocks_falling_temps_and_two_speeds_at_one_temp', () => {
  const temps = [...TEMPS];
  temps[3] = 45; // point 4 below point 3
  assert.equal(validateCurve(temps, SPEEDS), "Point 4: temperature can't be lower than point 3.");
  temps[3] = 50; // point 4 equal to point 3, 30 % against 20 %
  assert.equal(validateCurve(temps, SPEEDS), 'Point 4: same temperature as point 3, so the fan speed must be the same.');
  const speeds = [...SPEEDS];
  speeds[3] = 20;
  assert.equal(validateCurve(temps, speeds), null);
});

test('the Ally firmware curves and their Quiet/Cool presets pass', () => {
  // Read-only probe on the device, 2026-10-23; Quiet = -10 (at least 0), Cool = +15, as the backend derives them.
  const curves = [
    [[48, 54, 59, 62, 62, 62, 62, 62], [1, 9, 18, 22, 22, 22, 22, 22]],
    [[48, 55, 58, 62, 66, 73, 73, 94], [9, 26, 39, 48, 56, 65, 65, 78]],
    [[47, 49, 54, 54, 54, 54, 54, 54], [1, 9, 13, 13, 13, 13, 13, 13]],
  ];
  for (const [t, s] of curves) {
    assert.equal(validateCurve(t, s), null);
    assert.equal(validateCurve(t, s.map((v) => Math.max(0, v - 10))), null);
    assert.equal(validateCurve(t, s.map((v) => Math.min(100, v + 15))), null);
  }
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

test('live readout: CPU line, then GPU line, each temperature · RPM · usage · power', () => {
  const all = { cpuTempC: 51, fanRpm: 3300, cpuUse: 23, cpuPowerW: 8, gpuTempC: 49, gpuFanRpm: 3700, gpuUse: 62, gpuPowerW: 6 };
  assert.deepEqual(liveLines(all), ['CPU 51 °C · 3300 RPM · 23 % · 8 W', 'GPU 49 °C · 3700 RPM · 62 % · 6 W']);
  assert.deepEqual(liveLines({ cpuTempC: 61.6, fanRpm: 0 }), ['CPU 62 °C · 0 RPM'], 'rounded; 0 RPM is a value');
});

test('mode read back as other shows Set by Armoury Crate, never a stored mode', () => {
  assert.equal(shownMode('balanced', false, 'other'), 'other');
  assert.equal(shownMode('manual', true, 'other'), 'manual'); // the user's choice wins over the Ally's mode 7
  assert.equal(shownMode('balanced', false, 'turbo'), 'turbo'); // untouched install: what the device runs
  assert.equal(shownMode('balanced', false, null), null);
  assert.equal(shownMode('manual', true, 'balanced'), 'manual'); // Manual reads back as Balanced
  assert.equal(shownMode('silent', undefined, undefined), 'silent'); // older backend without read-back
});

test('power limits outside Manual are the firmware default; 0 or null means not reported', () => {
  assert.equal(powerLine(null, null, null), 'Power limits: Firmware default');
  assert.equal(powerLine(0, 0, 0), 'Power limits: Firmware default');
  assert.equal(powerLine(undefined, 20, 25), 'Power limits: Firmware default');
  assert.equal(powerLine(15, 20, 25), 'Power limits: Firmware default (15 / 20 / 25 W)');
});

test('live readout leaves out what is not reported and hides an empty line', () => {
  // first poll: no CPU % yet; ADL missing: GPU temperature from ACPI only
  assert.deepEqual(liveLines({ cpuTempC: 51, fanRpm: 3300, cpuUse: null, cpuPowerW: null, gpuTempC: 47, gpuFanRpm: 3700 }), [
    'CPU 51 °C · 3300 RPM',
    'GPU 47 °C · 3700 RPM',
  ]);
  assert.deepEqual(liveLines({ cpuTempC: null, fanRpm: 3300, cpuUse: 23, cpuPowerW: 8 }), ['CPU 3300 RPM · 23 % · 8 W']);
  assert.deepEqual(liveLines({ cpuTempC: 51, gpuUse: 62 }), ['CPU 51 °C', 'GPU 62 %'], 'order kept');
  assert.deepEqual(liveLines({ cpuTempC: 51, gpuTempC: NaN, gpuFanRpm: undefined }), ['CPU 51 °C'], 'GPU line hidden');
  assert.deepEqual(liveLines({}), []);
});

test('each mode shows its own watts; other modes keep the firmware line', async () => {
  const { modePowerLine } = await import('../../src/curve.ts');
  assert.equal(modePowerLine('silent'), 'Power limit: 13 W');
  assert.equal(modePowerLine('balanced'), 'Power limit: 17 W');
  assert.equal(modePowerLine('turbo'), 'Power limit: 35 W');
  assert.equal(modePowerLine('other'), null);
  assert.equal(modePowerLine(null), null);
});

test('Auto TDP texts', async () => {
  const { autoTdpDescription, autoTdpLine, modePowerLine, AUTO_TDP_UNSUPPORTED } = await import('../../src/curve.ts');
  assert.equal(autoTdpDescription(60), 'Holds 60 FPS at the lowest watts.');
  assert.equal(autoTdpDescription(45), 'Holds 45 FPS at the lowest watts.');
  assert.equal(autoTdpDescription(0), 'FPS limit Off: the most frames, trimming only watts that add none.');
  assert.equal(autoTdpLine({ watts: 11, fps: 60, best: null, paused: '' }), 'Auto TDP: 11 W · 60 FPS');
  assert.equal(autoTdpLine({ watts: 11.4, fps: 59.6, best: null, paused: '' }), 'Auto TDP: 11 W · 60 FPS');
  assert.equal(autoTdpLine({ watts: 14, fps: 72, best: 73.6, paused: '' }), 'Auto TDP: 14 W · 72 FPS (best 74)');
  assert.equal(autoTdpLine({ watts: 11, fps: 0, best: null, paused: '' }), 'Auto TDP: 11 W · -- FPS');
  assert.equal(autoTdpLine({ watts: 11, fps: null, best: null, paused: '' }), 'Auto TDP: 11 W · -- FPS');
  assert.equal(autoTdpLine({ watts: null, fps: null, best: null, paused: '' }), 'Auto TDP: -- W · -- FPS');
  assert.equal(autoTdpLine({ watts: 11, fps: 60, best: null, paused: 'Auto TDP paused: charging' }), 'Auto TDP paused: charging');
  assert.equal(modePowerLine('balanced', true), 'Power limit: up to 17 W (Auto TDP)');
  assert.equal(modePowerLine('balanced', false), 'Power limit: 17 W');
  assert.equal(modePowerLine('other', true), null);
  assert.equal(AUTO_TDP_UNSUPPORTED, "Auto TDP needs the AMD driver's frame-rate reading, which this driver does not offer.");
});

test('Auto TDP line after a refusal and the switch when unsupported', async () => {
  const { autoTdpLiveLine, autoTdpToggleDisabled } = await import('../../src/curve.ts');
  const idle = { on: false, watts: null, fps: null, best: null, paused: '' };
  assert.equal(autoTdpLiveLine({ ...idle, on: true, watts: 11, fps: 60 }), 'Auto TDP: 11 W · 60 FPS');
  // a set_power refusal stops the loop (on false) and leaves the device's message: it is still shown
  assert.equal(autoTdpLiveLine({ ...idle, paused: 'The power limit was refused by the firmware.' }),
    'The power limit was refused by the firmware.');
  assert.equal(autoTdpLiveLine(idle), null);
  assert.equal(autoTdpLiveLine(undefined), null);
  assert.equal(autoTdpToggleDisabled(true, false), false);
  assert.equal(autoTdpToggleDisabled(true, true), false);
  assert.equal(autoTdpToggleDisabled(false, true), false);   // stored on but unsupported: can be turned off
  assert.equal(autoTdpToggleDisabled(false, false), true);   // cannot be turned on
});

test('powerModeLabel names the three Windows power modes and anything else', async () => {
  const { powerModeLabel } = await import('../../src/curve.ts');
  assert.equal(powerModeLabel('eco'), 'Eco');
  assert.equal(powerModeLabel('balanced'), 'Balanced');
  assert.equal(powerModeLabel('best'), 'Best performance');
  assert.equal(powerModeLabel('other'), 'Set in Windows');
  assert.equal(powerModeLabel(undefined), 'Set in Windows');
});

test('controllerModeDescription gives the spec 3.3 texts', async () => {
  const { controllerModeDescription } = await import('../../src/curve.ts');
  assert.equal(controllerModeDescription('gamepad'), 'The controller works as a gamepad (Steam and games).');
  assert.equal(controllerModeDescription('desktop'), 'Sticks and triggers work as a mouse; Steam menus cannot be steered.');
  assert.equal(controllerModeDescription('auto'), 'Gamepad in Steam and games, mouse on the desktop.');
});

test('touchscreenCheck waits for the device to match the target, then times out', async () => {
  const { touchscreenCheck } = await import('../../src/curve.ts');
  assert.equal(touchscreenCheck(true, true, 0), 'done');
  assert.equal(touchscreenCheck(true, false, 1500), 'wait');
  assert.equal(touchscreenCheck(undefined, false, 14999), 'wait');
  assert.equal(touchscreenCheck(true, false, 15000), 'timeout');
  assert.equal(touchscreenCheck(false, false, 20000), 'done');
});
