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
    // Firmware curves repeat their last temperature: non-decreasing temps, one speed per temperature.
    if (i > 0 && temps[i] < temps[i - 1]) {
      return 'Point ' + n + ": temperature can't be lower than point " + i + '.';
    }
    if (i > 0 && speeds[i] < speeds[i - 1]) {
      return 'Point ' + n + ": fan speed can't be lower than point " + i + '.';
    }
    if (i > 0 && temps[i] === temps[i - 1] && speeds[i] !== speeds[i - 1]) {
      return 'Point ' + n + ': same temperature as point ' + i + ', so the fan speed must be the same.';
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

// The GPU fan is added when the device reports it (gpuFanRpm a number); otherwise the line keeps its one-fan form.
export function liveLine(
  cpuTempC: number | null | undefined,
  fanRpm: number | null | undefined,
  gpuFanRpm?: number | null,
): string {
  if (typeof gpuFanRpm === 'number' && isFinite(gpuFanRpm)) {
    return 'CPU ' + show(cpuTempC) + ' °C · CPU fan ' + show(fanRpm) + ' RPM · GPU fan ' + show(gpuFanRpm) + ' RPM';
  }
  return 'CPU ' + show(cpuTempC) + ' °C · Fan ' + show(fanRpm) + ' RPM';
}

// ---------- mode and power display (probe follow-ups) ----------

export type DeviceMode = 'silent' | 'balanced' | 'turbo' | 'other' | null;
export type ShownMode = 'silent' | 'balanced' | 'turbo' | 'manual' | 'other' | null;

// Which mode button the panel highlights. A device mode Overdrive does not set ("other", e.g. Armoury Crate's own)
// always wins, as nothing else is true then; before the owner changed anything the device's mode is shown (the
// stored default is only a default); after that the stored mode (Manual reads back as Balanced on the device).
export function shownMode(stored: string, userSet: boolean | undefined, device: DeviceMode | undefined): ShownMode {
  // Once the user has chosen a mode, show it: the Ally always reads back 7 ("other"), so the device value would
  // otherwise hide every choice. Before that, show what the device runs.
  if (userSet !== false) return stored as ShownMode;
  return device ?? null;
}

export const MODE_OTHER_TEXT = 'Set by Armoury Crate';
export const FIRMWARE_DEFAULT_TEXT = 'Firmware default';

// Power limits line outside Manual: the firmware's limits apply. The device reads 0 (not reported) on the Ally,
// so watts are only added when it reports real values.
export function powerLine(spl: number | null | undefined, sppt: number | null | undefined, fppt: number | null | undefined): string {
  const ok = (v: number | null | undefined) => typeof v === 'number' && isFinite(v) && v > 0;
  if (ok(spl) && ok(sppt) && ok(fppt)) return 'Power limits: ' + FIRMWARE_DEFAULT_TEXT + ' (' + spl + ' / ' + sppt + ' / ' + fppt + ' W)';
  return 'Power limits: ' + FIRMWARE_DEFAULT_TEXT;
}
