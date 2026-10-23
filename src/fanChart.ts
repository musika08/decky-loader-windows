// Pure maths for the fan curve chart. No imports, only erasable TypeScript, so `node --test` can load this file
// directly (same rule as curve.ts). The panel passes validateCurve's reason and minSpeedAt in from curve.ts.

export type ChartCurve = { temps: number[]; speeds: number[] };
// 'idle': the chart is only focused and the D-pad navigates past it; 'point' / 'temp': engaged by A.
export type EditMode = 'idle' | 'point' | 'temp';

export const TEMP_MIN = 20;
export const TEMP_MAX = 110;
export const SPEED_MIN = 0;
export const SPEED_MAX = 100;

// SVG user units; the svg scales to the panel width, so pointer positions are converted with toChart.
export const CHART = { width: 280, height: 160, left: 30, right: 8, top: 8, bottom: 22 };
const PLOT_W = CHART.width - CHART.left - CHART.right;
const PLOT_H = CHART.height - CHART.top - CHART.bottom;

// Radius (SVG units) within which a press picks up a point; generous for fingers.
export const HIT_RADIUS = 18;

export const GRID_TEMPS = [20, 30, 40, 50, 60, 70, 80, 90, 100, 110];
export const LABEL_TEMPS = [20, 40, 60, 80, 100];
export const GRID_SPEEDS = [0, 25, 50, 75, 100];
export const LABEL_SPEEDS = [0, 50, 100];

export function clampRound(v: number, min: number, max: number): number {
  if (!isFinite(v)) return min;
  return Math.min(max, Math.max(min, Math.round(v)));
}

// ---------- scales ----------

export function xOf(temp: number): number {
  return CHART.left + ((temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)) * PLOT_W;
}

export function yOf(speed: number): number {
  return CHART.top + (1 - (speed - SPEED_MIN) / (SPEED_MAX - SPEED_MIN)) * PLOT_H;
}

export function tempAt(x: number): number {
  return TEMP_MIN + ((x - CHART.left) / PLOT_W) * (TEMP_MAX - TEMP_MIN);
}

export function speedAt(y: number): number {
  return SPEED_MIN + (1 - (y - CHART.top) / PLOT_H) * (SPEED_MAX - SPEED_MIN);
}

export const PLOT = { x0: xOf(TEMP_MIN), x1: xOf(TEMP_MAX), y0: yOf(SPEED_MAX), y1: yOf(SPEED_MIN) };

// Screen (client) position to SVG units, given the svg's bounding box.
export function toChart(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
): { x: number; y: number } {
  const w = rect.width > 0 ? rect.width : CHART.width;
  const h = rect.height > 0 ? rect.height : CHART.height;
  return { x: ((clientX - rect.left) * CHART.width) / w, y: ((clientY - rect.top) * CHART.height) / h };
}

// ---------- paths ----------

function fmt(n: number): string {
  return String(Math.round(n * 10) / 10);
}

export function linePath(c: ChartCurve): string {
  return c.temps.map((t, i) => (i === 0 ? 'M' : 'L') + fmt(xOf(t)) + ' ' + fmt(yOf(c.speeds[i]))).join(' ');
}

// Area under the line, down to the 0 % axis.
export function fillPath(c: ChartCurve): string {
  if (c.temps.length === 0) return '';
  const first = c.temps[0];
  const last = c.temps[c.temps.length - 1];
  return linePath(c) + ' L' + fmt(xOf(last)) + ' ' + fmt(PLOT.y1) + ' L' + fmt(xOf(first)) + ' ' + fmt(PLOT.y1) + ' Z';
}

// The safety floor: the lowest fan % allowed at each whole temperature.
export function floorPath(minAt: (temp: number) => number): string {
  const parts: string[] = [];
  for (let t = TEMP_MIN; t <= TEMP_MAX; t++) {
    parts.push((t === TEMP_MIN ? 'M' : 'L') + fmt(xOf(t)) + ' ' + fmt(yOf(Math.min(SPEED_MAX, minAt(t)))));
  }
  return parts.join(' ');
}

// Current CPU temperature marker; null when there is no reading. Off-scale readings sit on the edge.
export function markerX(temp: number | null | undefined): number | null {
  if (typeof temp !== 'number' || !isFinite(temp)) return null;
  return xOf(Math.min(TEMP_MAX, Math.max(TEMP_MIN, temp)));
}

export function markerLabel(temp: number): string {
  return Math.round(temp) + ' °C';
}

// ---------- picking ----------

// Nearest point to (x, y). Stacked points (firmware curves repeat their last temperature) tie: the selected one wins,
// else the highest index, so dragging a stack apart starts with the point that may move right.
export function nearestPoint(c: ChartCurve, x: number, y: number, selected = -1): { index: number; dist: number } {
  let best = -1;
  let bestD = Infinity;
  for (let i = 0; i < c.temps.length; i++) {
    const d = Math.hypot(xOf(c.temps[i]) - x, yOf(c.speeds[i]) - y);
    if (d < bestD - 1e-9 || (Math.abs(d - bestD) <= 1e-9 && (i === selected || best !== selected))) {
      best = i;
      bestD = d;
    }
  }
  return { index: best, dist: bestD };
}

// Point under a press, or -1 when the press is not on a point.
export function hitTest(c: ChartCurve, x: number, y: number, selected = -1, radius = HIT_RADIUS): number {
  const n = nearestPoint(c, x, y, selected);
  return n.index >= 0 && n.dist <= radius ? n.index : -1;
}

// ---------- editing ----------

function withPoint(c: ChartCurve, i: number, temp: number, speed: number): ChartCurve {
  const temps = [...c.temps];
  const speeds = [...c.speeds];
  temps[i] = clampRound(temp, TEMP_MIN, TEMP_MAX);
  speeds[i] = clampRound(speed, SPEED_MIN, SPEED_MAX);
  return { temps, speeds };
}

// Drag point i to an SVG position: horizontal = temperature, vertical = fan %, clamped and whole numbers.
export function dragTo(c: ChartCurve, i: number, x: number, y: number): ChartCurve {
  if (i < 0 || i >= c.temps.length) return c;
  return withPoint(c, i, tempAt(x), speedAt(y));
}

// Step point i by whole degrees / percent (D-pad and steppers).
export function movePoint(c: ChartCurve, i: number, dTemp: number, dSpeed: number): ChartCurve {
  if (i < 0 || i >= c.temps.length) return c;
  return withPoint(c, i, c.temps[i] + dTemp, c.speeds[i] + dSpeed);
}

export function selectNext(i: number, dir: number, count: number): number {
  if (count <= 0) return 0;
  return Math.min(count - 1, Math.max(0, i + dir));
}

// Which point validateCurve's reason is about ("Point 3: ..."), as an index; -1 when none.
export function invalidPoint(reason: string | null | undefined): number {
  if (!reason) return -1;
  const m = /^Point (\d+):/.exec(reason);
  return m ? Number(m[1]) - 1 : -1;
}

// ---------- controller state machine ----------

export type Direction = 'up' | 'down' | 'left' | 'right';

// What a D-pad press does in a mode. 'navigate' means the chart must not take the press (Steam moves focus).
export type ChartAction =
  | { kind: 'navigate' }
  | { kind: 'select'; dir: number }
  | { kind: 'temp'; delta: number }
  | { kind: 'speed'; delta: number };

export function directionAction(mode: EditMode, d: Direction): ChartAction {
  if (mode === 'idle') return { kind: 'navigate' };
  if (d === 'up' || d === 'down') return { kind: 'speed', delta: d === 'up' ? 1 : -1 };
  const dir = d === 'right' ? 1 : -1;
  return mode === 'temp' ? { kind: 'temp', delta: dir } : { kind: 'select', dir };
}

// A engages editing, then toggles point / temperature mode.
export function pressA(mode: EditMode): EditMode {
  return mode === 'point' ? 'temp' : 'point';
}

// B finishes editing. handled=false while idle: the press then does what B normally does.
export function pressB(mode: EditMode): { mode: EditMode; handled: boolean } {
  return mode === 'idle' ? { mode, handled: false } : { mode: 'idle', handled: true };
}

export function aLabel(mode: EditMode): string {
  return mode === 'idle' ? 'Edit curve' : mode === 'point' ? 'Temperature mode' : 'Point mode';
}

// ---------- caption ----------

export function caption(c: ChartCurve, i: number, mode: EditMode): { title: string; hint: string } {
  const title = 'Point ' + (i + 1) + ' · ' + c.temps[i] + ' °C · ' + c.speeds[i] + ' %';
  const hint =
    mode === 'idle'
      ? 'Press A to edit the curve'
      : mode === 'temp'
        ? 'Editing temperature: left/right change °C, up/down change fan %, A for points · B to finish'
        : 'Editing: left/right pick a point, up/down change fan %, A for temperature · B to finish';
  return { title, hint };
}
