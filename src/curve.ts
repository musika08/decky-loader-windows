// Pure helpers for the panel. No imports, only erasable TypeScript, so `node --test` can load this file directly.
// validateCurve copies helper\src\Validation.cs CheckCurve, reason strings included (ruling P1); change both together.

export const CURVE_POINTS = 8;
export const CURVE_TEMP_MIN = 20;
export const CURVE_TEMP_MAX = 110;
export const CURVE_SPEED_MAX = 100;

export type Curve = { temps: number[]; speeds: number[] };

// Lowest fan percentage G-Helper's editor allows at this temperature: ceil((temp - 70) * 1.2), at least 0.
export function minSpeedAt(temp: number): number {
  const tenths = (temp - 70) * 12;
  if (tenths <= 0) return 0;
  return Math.floor((tenths + 9) / 10);
}

export function validateCurve(temps: number[], speeds: number[]): string | null {
  if (!Array.isArray(temps) || !Array.isArray(speeds) || temps.length !== CURVE_POINTS || speeds.length !== CURVE_POINTS) {
    return 'The fan curve needs 8 points.';
  }
  for (let i = 0; i < CURVE_POINTS; i++) {
    const n = i + 1;
    // The helper takes C# int[]; NaN, undefined or fractions can never be sent (not a CheckCurve message).
    if (!Number.isInteger(temps[i]) || !Number.isInteger(speeds[i])) {
      return 'Point ' + n + ': temperature and fan speed must be whole numbers.';
    }
    if (temps[i] < CURVE_TEMP_MIN || temps[i] > CURVE_TEMP_MAX) {
      return 'Point ' + n + ': temperature must be between ' + CURVE_TEMP_MIN + ' and ' + CURVE_TEMP_MAX + ' °C.';
    }
    if (speeds[i] < 0 || speeds[i] > CURVE_SPEED_MAX) {
      return 'Point ' + n + ': fan speed must be between 0 and ' + CURVE_SPEED_MAX + ' %.';
    }
    if (i > 0 && temps[i] <= temps[i - 1]) {
      return 'Point ' + n + ': temperature must be higher than point ' + i + '.';
    }
    if (i > 0 && speeds[i] < speeds[i - 1]) {
      return 'Point ' + n + ": fan speed can't be lower than point " + i + '.';
    }
    const min = minSpeedAt(temps[i]);
    if (speeds[i] < min) {
      return 'Point ' + n + ': at ' + temps[i] + ' °C the fan must be at least ' + min + ' %.';
    }
  }
  return null;
}

// Starting point for the editor when the backend has no curve yet (device default curve not read).
export function defaultDraft(): Curve {
  return { temps: [30, 40, 50, 60, 70, 80, 90, 100], speeds: [0, 10, 20, 30, 40, 50, 60, 70] };
}

export function stepValue(value: number, delta: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value + delta)));
}

function show(v: number | null | undefined): string {
  return typeof v === 'number' && isFinite(v) ? String(Math.round(v)) : '--';
}

export function liveLine(cpuTempC: number | null | undefined, fanRpm: number | null | undefined): string {
  return 'CPU ' + show(cpuTempC) + ' °C · Fan ' + show(fanRpm) + ' RPM';
}
