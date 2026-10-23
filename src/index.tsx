import { callable, definePlugin } from '@decky/api';
import {
  ButtonItem,
  DialogButton,
  DialogButtonPrimary,
  Field,
  Focusable,
  PanelSection,
  PanelSectionRow,
  SliderField,
  Spinner,
  ToggleField,
  staticClasses,
} from '@decky/ui';
import { useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { FaBolt, FaCheck } from 'react-icons/fa';
import {
  CURVE_POINTS,
  CURVE_SPEED_MAX,
  CURVE_TEMP_MAX,
  CURVE_TEMP_MIN,
  Curve,
  DeviceMode,
  FAN_PRESETS,
  MODE_OTHER_TEXT,
  Preset,
  defaultDraft,
  liveLines,
  minSpeedAt,
  powerLine,
  shownMode,
  stepValue,
  validateCurve,
} from './curve';
import {
  CHART,
  Direction,
  EditMode,
  FANS,
  Fan,
  GRID_SPEEDS,
  GRID_TEMPS,
  LABEL_SPEEDS,
  LABEL_TEMPS,
  PLOT,
  aLabel,
  caption,
  directionAction,
  dotPoints,
  dragTo,
  fillPath,
  floorPath,
  hasStacks,
  hitTest,
  invalidPoint,
  linePath,
  markerLabel,
  markerFor,
  markerX,
  movePoint,
  pressA,
  pressB,
  selectNext,
  spreadPoints,
  stackBadge,
  tailFillPath,
  tailPath,
  toChart,
  xOf,
  yOf,
} from './fanChart';
import { PickerOption, choose, focusIndex, pressCancel, pressRow, rowValue } from './picker';

// ---------- backend (main.py) ----------

type Mode = 'silent' | 'balanced' | 'turbo' | 'manual';
type Support = { supported: boolean; product: string };
type Result = { ok: boolean; message: string };
type State = {
  support: Support;
  mode: Mode;
  spl: number;
  sppt: number;
  fppt: number;
  fps: number;
  fan_preset: Preset;
  fan_curve: Curve | null;
  fan_curve_gpu?: Curve | null;
  armoury: { installed: boolean; replaced: boolean };
  unavailable: string;
  warning: string;
  fan_note: string;
  busy?: boolean; // ruling P12; read when the backend reports it
  user_set?: boolean;
  power_set?: boolean;
  // What the device reports (read only): mode "other" = a mode Overdrive does not set; null = not reported.
  device?: { mode: DeviceMode; spl: number | null; sppt: number | null; fppt: number | null };
};
type Sensors = {
  ok: boolean;
  message: string;
  cpuTempC: number | null;
  fanRpm: number | null;
  gpuFanRpm?: number | null;
  gpuTempC?: number | null;
  cpuUse?: number | null;
  gpuUse?: number | null;
  cpuPowerW?: number | null;
  gpuPowerW?: number | null;
  errors: string[];
};

const getSupport = callable<[], Support>('get_support');
const getState = callable<[], State>('get_state');
const getSensors = callable<[], Sensors>('get_sensors');
const setMode = callable<[Mode, number?, number?, number?], Result>('set_mode');
const setFps = callable<[number], Result>('set_fps');
const setFanPreset = callable<[Preset], Result>('set_fan_preset');
const setFanCurve = callable<[number[], number[], Fan], Result>('set_fan_curve');
const resetDefaults = callable<[], Result>('reset_defaults');
const setArmouryReplaced = callable<[boolean], Result>('set_armoury_replaced');

const NOT_ALLY = 'Overdrive supports the ROG Ally family only';
const ARMOURY_BUSY = 'Switching Armoury Crate services, this can take a few minutes';

const MODES: { mode: Mode; label: string }[] = [
  { mode: 'silent', label: 'Silent' },
  { mode: 'balanced', label: 'Balanced' },
  { mode: 'turbo', label: 'Turbo' },
  { mode: 'manual', label: 'Manual' },
];
const FPS_STEPS = [0, 30, 40, 45, 50, 60, 75, 90, 120];
const PRESETS = FAN_PRESETS;
const POWER_MIN = 5;
const POWER_MAX = 50;
const POWER_DEBOUNCE_MS = 700;
const SENSOR_POLL_MS = 2000;
const UNAVAILABLE_RECHECK_MS = 5000;
const BUSY_RECHECK_MS = 3000;
const CONFIRM_MS = 3000;
const CONFIRM_LABEL = 'Press again to confirm';

// An Armoury switch can outlive the panel (closing the menu unmounts it), so the pending call and its result live at
// module level: a panel opened later shows the result (a failure included) once.
let armouryPending: Promise<Result> | null = null;
let armouryResult: Result | null = null;

function errorText(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

async function safeCall(fn: () => Promise<Result>): Promise<Result> {
  try {
    const r = await fn();
    if (r && typeof r === 'object') return { ok: !!r.ok, message: String(r.message || '') };
    return { ok: false, message: 'Unexpected reply from Overdrive.' };
  } catch (e) {
    return { ok: false, message: errorText(e) };
  }
}

// ---------- small pieces ----------

type Status = { ok: boolean; text: string } | undefined;

function StatusRow({ status }: { status: Status }) {
  if (!status || !status.text) return null;
  return (
    <PanelSectionRow>
      <div style={{ fontSize: '12px', color: status.ok ? '#8bc34a' : '#ff6b6b', whiteSpace: 'normal' }}>{status.text}</div>
    </PanelSectionRow>
  );
}

function Note({ text, color }: { text: string; color?: string }) {
  if (!text) return null;
  return (
    <PanelSectionRow>
      <div style={{ fontSize: '12px', color: color || '#dcdedf', whiteSpace: 'normal' }}>{text}</div>
    </PanelSectionRow>
  );
}

// Full-width inline picker: the row "Label  Value ▾" expands its options as rows underneath, inside the panel
// (Decky's DropdownItem opens Steam's separate popup window). Choosing an option applies it and collapses; B or
// the row again collapses without a change. Controller focus moves to the current option on open, back on close.
function InlinePicker<T>(props: {
  label: string;
  options: PickerOption<T>[];
  value: T;
  disabled?: boolean;
  onChange: (v: T) => void;
}) {
  const { label, options, value, disabled, onChange } = props;
  const [open, setOpen] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<(HTMLDivElement | null)[]>([]);
  const wasOpen = useRef(false);
  const target = focusIndex(options, value, open);

  useEffect(() => {
    if (disabled && open) setOpen(false);
  }, [disabled, open]);

  useEffect(() => {
    if (open) optionRefs.current[target]?.focus();
    else if (wasOpen.current) rowRef.current?.focus();
    wasOpen.current = open;
  }, [open]);

  const toggle = () => setOpen((o) => pressRow(o, !!disabled));
  const cancel = (evt: CustomEvent) => {
    const r = pressCancel(open);
    if (!r.handled) return;
    // Collapse only; the menu must not close.
    evt.preventDefault();
    evt.stopPropagation();
    setOpen(r.open);
  };
  const pick = (v: T) => {
    const r = choose(value, v);
    setOpen(r.open);
    if (r.apply) onChange(v);
  };

  const rowStyle = {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    width: '100%',
    boxSizing: 'border-box' as const,
    padding: '8px 10px',
    borderRadius: '2px',
    background: 'rgba(255, 255, 255, 0.06)',
    color: disabled ? '#8b929a' : '#dcdedf',
    fontSize: '14px',
  };
  return (
    <Focusable style={{ width: '100%' }} flow-children="column">
      <Focusable
        ref={rowRef}
        style={rowStyle}
        onActivate={toggle}
        onClick={toggle}
        onCancelButton={cancel}
        onOKActionDescription={open ? 'Close' : 'Choose'}
      >
        <span>{label}</span>
        <span style={{ color: disabled ? '#8b929a' : '#ffffff' }}>{rowValue(options, value, open)}</span>
      </Focusable>
      {open &&
        options.map((o, i) => {
          const current = o.data === value;
          return (
            <Focusable
              key={String(o.data)}
              ref={(el: HTMLDivElement | null) => {
                optionRefs.current[i] = el;
              }}
              preferredFocus={i === target}
              style={{
                ...rowStyle,
                marginTop: '2px',
                paddingLeft: '18px',
                background: current ? 'rgba(26, 159, 255, 0.25)' : 'rgba(255, 255, 255, 0.03)',
              }}
              onActivate={() => pick(o.data)}
              onClick={() => pick(o.data)}
              onCancelButton={cancel}
              onCancelActionDescription="Close"
            >
              <span>{o.label}</span>
              <span style={{ width: '16px', textAlign: 'right' }}>{current ? <FaCheck size={12} /> : null}</span>
            </Focusable>
          );
        })}
    </Focusable>
  );
}

// D-pad and touch friendly number stepper: [-5] [-1] value [+1] [+5].
function Stepper(props: {
  label: string;
  value: number;
  min: number;
  max: number;
  unit: string;
  disabled: boolean;
  onChange: (v: number) => void;
}) {
  const btn = { minWidth: '0', width: '34px', padding: '4px 0', marginLeft: '4px' };
  const step = (d: number) => props.onChange(stepValue(props.value, d, props.min, props.max));
  return (
    <Field label={props.label} padding="compact" childrenContainerWidth="max">
      <Focusable flow-children="horizontal" style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end' }}>
        <DialogButton style={btn} disabled={props.disabled || props.value <= props.min} onClick={() => step(-5)}>
          -5
        </DialogButton>
        <DialogButton style={btn} disabled={props.disabled || props.value <= props.min} onClick={() => step(-1)}>
          -1
        </DialogButton>
        <div style={{ width: '56px', textAlign: 'center', marginLeft: '4px' }}>
          {props.value} {props.unit}
        </div>
        <DialogButton style={btn} disabled={props.disabled || props.value >= props.max} onClick={() => step(1)}>
          +1
        </DialogButton>
        <DialogButton style={btn} disabled={props.disabled || props.value >= props.max} onClick={() => step(5)}>
          +5
        </DialogButton>
      </Focusable>
    </Field>
  );
}

// ---------- fan curve chart ----------

// Steam's GamepadButton values (FooterLegend.d.ts); kept local so the bundle does not depend on the enum export.
const BTN_UP = 9;
const BTN_DOWN = 10;
const BTN_LEFT = 11;
const BTN_RIGHT = 12;

const C_LINE = '#1a9fff';
const C_FILL = 'rgba(26, 159, 255, 0.18)';
const C_TAIL = 'rgba(26, 159, 255, 0.55)';
const C_TAIL_FILL = 'rgba(26, 159, 255, 0.07)';
const C_GRID = 'rgba(255, 255, 255, 0.08)';
const C_AXIS = 'rgba(255, 255, 255, 0.25)';
const C_LABEL = '#8b929a';
const C_FLOOR = 'rgba(255, 183, 77, 0.6)';
const C_MARKER = 'rgba(220, 222, 223, 0.7)';
const C_BAD = '#ff6b6b';
const FLOOR_PATH = floorPath(minSpeedAt);

function ChartSvg(props: {
  curve: Curve | null;
  fan: Fan;
  selected: number;
  invalid: number;
  cpuTemp: number | null;
  gpuTemp: number | null;
  editable: boolean;
  onPointerDown?: (e: ReactPointerEvent<SVGSVGElement>) => void;
  onPointerMove?: (e: ReactPointerEvent<SVGSVGElement>) => void;
  onPointerEnd?: (e: ReactPointerEvent<SVGSVGElement>) => void;
}) {
  const { curve, fan, selected, invalid, editable } = props;
  // The GPU chart shows the GPU temperature when the device reports it, else the CPU temperature.
  const marker = markerFor(fan, props.cpuTemp, props.gpuTemp);
  const mx = markerX(marker.temp);
  const labelAnchor = mx !== null && mx > PLOT.x1 - 24 ? 'end' : mx !== null && mx < PLOT.x0 + 24 ? 'start' : 'middle';
  return (
    <svg
      viewBox={`0 0 ${CHART.width} ${CHART.height}`}
      width="100%"
      style={{ display: 'block', touchAction: editable ? 'none' : 'auto', userSelect: 'none' }}
      onPointerDown={props.onPointerDown}
      onPointerMove={props.onPointerMove}
      onPointerUp={props.onPointerEnd}
      onPointerCancel={props.onPointerEnd}
    >
      {GRID_TEMPS.map((t) => (
        <line key={'gx' + t} x1={xOf(t)} x2={xOf(t)} y1={PLOT.y0} y2={PLOT.y1} stroke={C_GRID} strokeWidth={1} />
      ))}
      {GRID_SPEEDS.map((s) => (
        <line key={'gy' + s} x1={PLOT.x0} x2={PLOT.x1} y1={yOf(s)} y2={yOf(s)} stroke={C_GRID} strokeWidth={1} />
      ))}
      <line x1={PLOT.x0} x2={PLOT.x1} y1={PLOT.y1} y2={PLOT.y1} stroke={C_AXIS} strokeWidth={1} />
      <line x1={PLOT.x0} x2={PLOT.x0} y1={PLOT.y0} y2={PLOT.y1} stroke={C_AXIS} strokeWidth={1} />
      {LABEL_TEMPS.map((t) => (
        <text key={'lx' + t} x={xOf(t)} y={CHART.height - 7} fill={C_LABEL} fontSize={9} textAnchor="middle">
          {t + '°'}
        </text>
      ))}
      {LABEL_SPEEDS.map((s) => (
        <text key={'ly' + s} x={PLOT.x0 - 4} y={yOf(s) + 3} fill={C_LABEL} fontSize={9} textAnchor="end">
          {s + '%'}
        </text>
      ))}
      <path d={FLOOR_PATH} fill="none" stroke={C_FLOOR} strokeWidth={1} strokeDasharray="3 3" />
      {mx !== null && marker.temp !== null && (
        <>
          <line x1={mx} x2={mx} y1={PLOT.y0 + 10} y2={PLOT.y1} stroke={C_MARKER} strokeWidth={1} />
          <text x={mx} y={PLOT.y0 + 7} fill={C_MARKER} fontSize={9} textAnchor={labelAnchor}>
            {markerLabel(marker.temp, marker.prefix)}
          </text>
        </>
      )}
      {curve ? (
        <>
          <path d={fillPath(curve)} fill={C_FILL} stroke="none" />
          {/* Past the last point the fan holds its speed up to 110 °C. */}
          <path d={tailFillPath(curve)} fill={C_TAIL_FILL} stroke="none" />
          <path d={tailPath(curve)} fill="none" stroke={C_TAIL} strokeWidth={2} strokeDasharray="4 3" />
          <path d={linePath(curve)} fill="none" stroke={C_LINE} strokeWidth={2} strokeLinejoin="round" />
          {dotPoints(curve).map((d) => (
            // Stacked points (same temperature and speed) are one dot.
            <circle
              key={'p' + d.index}
              cx={xOf(d.temp)}
              cy={yOf(d.speed)}
              r={4}
              fill={d.indices.indexOf(invalid) >= 0 ? C_BAD : C_LINE}
            />
          ))}
          {editable && selected >= 0 && selected < curve.temps.length && (
            // Drawn last so the selected point sits on top of stacked ones.
            <circle
              cx={xOf(curve.temps[selected])}
              cy={yOf(curve.speeds[selected])}
              r={6}
              fill={selected === invalid ? C_BAD : C_LINE}
              stroke="#ffffff"
              strokeWidth={2}
            />
          )}
          {dotPoints(curve)
            .filter((d) => d.indices.length > 1)
            .map((d) => {
              // "×N" beside a dot that stands for N stacked points; drawn after the selected point so it stays legible.
              const x = xOf(d.temp);
              const right = x < PLOT.x1 - 18;
              return (
                <text
                  key={'b' + d.index}
                  x={right ? x + 7 : x - 7}
                  y={Math.max(PLOT.y0 + 8, yOf(d.speed) - 6)}
                  fill="#dcdedf"
                  fontSize={9}
                  fontWeight="bold"
                  textAnchor={right ? 'start' : 'end'}
                >
                  {stackBadge(d.indices.length)}
                </text>
              );
            })}
        </>
      ) : (
        <text x={(PLOT.x0 + PLOT.x1) / 2} y={(PLOT.y0 + PLOT.y1) / 2} fill={C_LABEL} fontSize={10} textAnchor="middle">
          Curve not read yet
        </text>
      )}
    </svg>
  );
}

// The editable chart: one Focusable for the controller, pointer drag for touch and mouse.
function FanChartEditor(props: {
  curve: Curve;
  fan: Fan;
  selected: number;
  invalid: number;
  cpuTemp: number | null;
  gpuTemp: number | null;
  disabled: boolean;
  onSelect: (i: number) => void;
  onEdit: (fn: (c: Curve) => Curve) => void;
}) {
  const { curve, selected, disabled, onSelect, onEdit } = props;
  // Idle until A: a merely focused chart lets every D-pad press navigate, so it never traps focus.
  const [mode, setMode] = useState<EditMode>('idle');
  const dragging = useRef(-1);
  const count = curve.temps.length;
  const engaged = mode !== 'idle';

  const onButtonDown = (evt: CustomEvent<{ button: number; is_repeat?: boolean }>) => {
    const b = evt.detail?.button;
    const d: Direction | null =
      b === BTN_UP ? 'up' : b === BTN_DOWN ? 'down' : b === BTN_LEFT ? 'left' : b === BTN_RIGHT ? 'right' : null;
    if (d === null || disabled) return;
    const act = directionAction(mode, d);
    if (act.kind === 'navigate') return;
    // Held buttons arrive again with is_repeat from Steam's navigation, so holding keeps stepping.
    if (act.kind === 'speed') onEdit((c) => movePoint(c, selected, 0, act.delta));
    else if (act.kind === 'temp') onEdit((c) => movePoint(c, selected, act.delta, 0));
    else onSelect(selectNext(selected, act.dir, count));
    // Engaged: keep the press inside the chart instead of moving focus.
    evt.preventDefault();
    evt.stopPropagation();
  };

  const onCancel = (evt: CustomEvent) => {
    const r = pressB(mode);
    if (!r.handled) return;
    // Finish editing only; the menu must not close.
    evt.preventDefault();
    evt.stopPropagation();
    setMode(r.mode);
  };

  const pointer = (e: ReactPointerEvent<SVGSVGElement>) => toChart(e.clientX, e.clientY, e.currentTarget.getBoundingClientRect());

  const onPointerDown = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (disabled) return;
    const p = pointer(e);
    const i = hitTest(curve, p.x, p.y, selected);
    if (i < 0) return;
    onSelect(i);
    dragging.current = i;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // Capture is a nicety; the drag still works while the pointer stays over the chart.
    }
    e.preventDefault();
  };

  const onPointerMove = (e: ReactPointerEvent<SVGSVGElement>) => {
    const i = dragging.current;
    if (i < 0 || disabled) return;
    const p = pointer(e);
    onEdit((c) => dragTo(c, i, p.x, p.y));
    e.preventDefault();
  };

  const onPointerEnd = (e: ReactPointerEvent<SVGSVGElement>) => {
    if (dragging.current < 0) return;
    dragging.current = -1;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // Already released.
    }
  };

  const cap = caption(curve, Math.min(Math.max(selected, 0), count - 1), mode);
  return (
    <Focusable
      onButtonDown={onButtonDown}
      onOKButton={() => {
        if (!disabled) setMode((m) => pressA(m));
      }}
      onOKActionDescription={aLabel(mode)}
      onCancelButton={onCancel}
      onCancelActionDescription={engaged ? 'Finish editing' : undefined}
      onGamepadBlur={() => setMode('idle')}
      style={{
        width: '100%',
        borderRadius: '4px',
        padding: '2px',
        boxSizing: 'border-box',
        border: engaged ? '1px solid ' + C_LINE : '1px solid transparent',
        boxShadow: engaged ? '0 0 8px rgba(26, 159, 255, 0.6)' : 'none',
      }}
    >
      <ChartSvg
        curve={curve}
        fan={props.fan}
        selected={selected}
        invalid={props.invalid}
        cpuTemp={props.cpuTemp}
        gpuTemp={props.gpuTemp}
        editable={true}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerEnd={onPointerEnd}
      />
      <div style={{ fontSize: '12px', color: '#dcdedf', marginTop: '4px' }}>{cap.title}</div>
      <div style={{ fontSize: '11px', color: engaged ? C_LINE : C_LABEL, whiteSpace: 'normal' }}>{cap.hint}</div>
    </Focusable>
  );
}

// A curve from the backend the chart can draw (8 points each), else null.
function usableCurve(c: Curve | null | undefined): Curve | null {
  return c && Array.isArray(c.temps) && Array.isArray(c.speeds) && c.temps.length === CURVE_POINTS && c.speeds.length === CURVE_POINTS
    ? c
    : null;
}

// The editor's starting draft for one fan: its current curve, or a valid placeholder when it is not known yet.
function draftFrom(c: Curve | null | undefined): Curve {
  const u = usableCurve(c);
  return u ? { temps: [...u.temps], speeds: [...u.speeds] } : defaultDraft();
}

// ---------- panel ----------

function Content() {
  const [support, setSupport] = useState<Support | null>(null);
  const [startError, setStartError] = useState('');
  const [state, setState] = useState<State | null>(null);
  const [sensors, setSensors] = useState<string[]>([]);
  const [cpuTemp, setCpuTemp] = useState<number | null>(null);
  const [gpuTemp, setGpuTemp] = useState<number | null>(null);
  const [selected, setSelected] = useState(0);
  const [fan, setFan] = useState<Fan>('cpu');
  const [statuses, setStatuses] = useState<Record<string, Status>>({});
  const [working, setWorking] = useState(false);
  const [armouryBusy, setArmouryBusy] = useState(armouryPending !== null);
  const [power, setPower] = useState({ spl: 30, sppt: 30, fppt: 30 });
  const [editing, setEditing] = useState(false);
  // One draft per fan, so switching fans while editing keeps both; Apply sends the selected fan's.
  const [drafts, setDrafts] = useState<Record<Fan, Curve>>({ cpu: defaultDraft(), gpu: defaultDraft() });
  const [confirm, setConfirm] = useState<'reset' | null>(null);
  const mounted = useRef(true);
  const powerTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // A pending slider write must not fire after a later action (it would put Manual back over it).
  const cancelPowerTimer = () => {
    if (powerTimer.current) clearTimeout(powerTimer.current);
    powerTimer.current = null;
  };

  const setStatus = (key: string, r: Result, okText = 'Applied.') => {
    if (!mounted.current) return;
    setStatuses((s) => ({ ...s, [key]: { ok: r.ok, text: r.ok ? r.message || okText : r.message || 'Failed.' } }));
  };

  // Returns '' on success, else the reason the state could not be read.
  const refresh = async (): Promise<string> => {
    try {
      const st = await getState();
      if (!mounted.current) return '';
      setState(st);
      // Slider edits still waiting for the debounce are not thrown away.
      if (powerTimer.current === null) setPower({ spl: st.spl, sppt: st.sppt, fppt: st.fppt });
      setStartError('');
      return '';
    } catch (e) {
      const reason = errorText(e) || 'No reply from the plugin backend.';
      if (mounted.current) setStartError(reason);
      return reason;
    }
  };

  const showArmouryResult = () => {
    if (!armouryResult || !mounted.current) return;
    setStatus('armoury', armouryResult);
    armouryResult = null;
  };

  const watchArmoury = (p: Promise<Result>) => {
    setArmouryBusy(true);
    p.then(() => {
      if (!mounted.current) return;
      showArmouryResult();
      setArmouryBusy(false);
      refresh();
    });
  };

  useEffect(() => {
    mounted.current = true;
    getSupport()
      .then((s) => {
        if (!mounted.current) return;
        setSupport(s);
        if (s.supported) refresh();
      })
      .catch((e) => {
        // Task 1 note: a failed call is not an unsupported device.
        if (mounted.current) setStartError(errorText(e) || 'No reply from the plugin backend.');
      });
    if (armouryPending) watchArmoury(armouryPending);
    else showArmouryResult();
    return () => {
      mounted.current = false;
      if (powerTimer.current) clearTimeout(powerTimer.current);
      if (confirmTimer.current) clearTimeout(confirmTimer.current);
    };
  }, []);

  const busy = armouryBusy || !!state?.busy;
  const unavailable = state?.unavailable || '';

  // Live line: every 2 s while the panel is mounted, paused while Armoury services switch (the helper is occupied).
  useEffect(() => {
    if (!support?.supported || busy || unavailable) return;
    let inFlight = false;
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const s = await getSensors();
        if (!mounted.current) return;
        setSensors(s.ok ? liveLines(s) : [s.message || 'Sensors not available']);
        setCpuTemp(s.ok && typeof s.cpuTempC === 'number' && isFinite(s.cpuTempC) ? s.cpuTempC : null);
        setGpuTemp(s.ok && typeof s.gpuTempC === 'number' && isFinite(s.gpuTempC) ? s.gpuTempC : null);
      } catch (e) {
        if (!mounted.current) return;
        setSensors(['Sensors not available: ' + errorText(e)]);
        setCpuTemp(null);
        setGpuTemp(null);
      } finally {
        inFlight = false;
      }
    };
    poll();
    const id = setInterval(poll, SENSOR_POLL_MS);
    return () => clearInterval(id);
  }, [support?.supported, busy, unavailable]);

  // While the helper is unavailable, re-read the state every 5 s so the panel recovers without reopening.
  useEffect(() => {
    if (!unavailable) return;
    const id = setInterval(() => {
      refresh();
    }, UNAVAILABLE_RECHECK_MS);
    return () => clearInterval(id);
  }, [unavailable]);

  // A switch this panel did not start (the load-time re-stop or repair, or one from an earlier panel whose promise
  // is gone): re-read every 3 s while the backend reports busy, so the controls come back without reopening.
  const backendBusy = !!state?.busy && !armouryBusy;
  useEffect(() => {
    if (!backendBusy) return;
    const id = setInterval(() => {
      refresh();
    }, BUSY_RECHECK_MS);
    return () => clearInterval(id);
  }, [backendBusy]);

  const run = async (key: string, fn: () => Promise<Result>) => {
    if (key !== 'power') cancelPowerTimer();
    setWorking(true);
    const r = await safeCall(fn);
    const failed = await refresh();
    if (!mounted.current) return;
    if (failed) {
      const first = r.message || (r.ok ? 'Applied.' : 'Failed.');
      setStatus(key, { ok: false, message: `${first} The panel could not reload the state: ${failed}` });
    } else {
      setStatus(key, r);
    }
    setWorking(false);
  };

  // Reset to defaults takes two presses within 3 s.
  const confirmed = (key: 'reset'): boolean => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    confirmTimer.current = null;
    if (confirm === key) {
      setConfirm(null);
      return true;
    }
    setConfirm(key);
    confirmTimer.current = setTimeout(() => {
      confirmTimer.current = null;
      if (mounted.current) setConfirm(null);
    }, CONFIRM_MS);
    return false;
  };

  const retryStart = () => {
    setStartError('');
    setSupport(null);
    getSupport()
      .then((s) => {
        if (!mounted.current) return;
        setSupport(s);
        if (s.supported) refresh();
      })
      .catch((e) => {
        if (mounted.current) setStartError(errorText(e) || 'No reply from the plugin backend.');
      });
  };

  // ---------- start-up screens ----------

  if (startError && !state) {
    return (
      <PanelSection title="Overdrive">
        <Note text="Overdrive could not start" color="#ff6b6b" />
        <Note text={startError} />
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={retryStart}>
            Try again
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }
  if (support === null || (support.supported && state === null)) {
    return (
      <PanelSection title="Overdrive">
        <PanelSectionRow>
          <Spinner />
        </PanelSectionRow>
      </PanelSection>
    );
  }
  if (!support.supported || !state) {
    return (
      <PanelSection title="Overdrive">
        <Note text={NOT_ALLY} />
      </PanelSection>
    );
  }

  const disabled = working || busy || !!unavailable;
  const shown = shownMode(state.mode, state.user_set, state.device?.mode);

  // ---------- actions ----------

  const chooseMode = (mode: Mode) => {
    cancelPowerTimer();
    // Until the user has chosen a mode, the shown one is only what the device reads, so pressing it still saves it.
    if (mode === shown && mode !== 'manual' && state.user_set !== false) return;
    if (mode === 'manual') run('mode', () => setMode('manual', power.spl, power.sppt, power.fppt));
    else run('mode', () => setMode(mode));
  };

  const changePower = (key: 'spl' | 'sppt' | 'fppt', v: number) => {
    const next = { ...power, [key]: v };
    setPower(next);
    if (powerTimer.current) clearTimeout(powerTimer.current);
    // Apply once the slider rests, so a D-pad sweep sends one write instead of one per watt.
    powerTimer.current = setTimeout(() => {
      powerTimer.current = null;
      run('power', () => setMode('manual', next.spl, next.sppt, next.fppt));
    }, POWER_DEBOUNCE_MS);
  };

  const openEditor = () => {
    setDrafts({ cpu: draftFrom(state.fan_curve), gpu: draftFrom(state.fan_curve_gpu) });
    setStatuses((s) => ({ ...s, curve: undefined }));
    setSelected(0);
    setEditing(true);
  };

  // Edits go to the draft of the fan shown now.
  const setDraft = (fn: (c: Curve) => Curve) => setDrafts((d) => ({ ...d, [fan]: fn(d[fan]) }));

  const editPoint = (kind: 'temps' | 'speeds', i: number, v: number) => {
    setDraft((d) => {
      const next = { temps: [...d.temps], speeds: [...d.speeds] };
      next[kind][i] = v;
      return next;
    });
  };

  const choosePreset = (p: Preset) => {
    // The chart then shows the preset's curve as soon as the state is re-read.
    setEditing(false);
    run('fans', () => setFanPreset(p));
  };

  const draft = drafts[fan];

  const applyCurve = async () => {
    const reason = validateCurve(draft.temps, draft.speeds);
    if (reason) {
      // Blocked before the backend is called.
      setStatus('curve', { ok: false, message: reason });
      return;
    }
    cancelPowerTimer();
    setWorking(true);
    const target = fan;
    const r = await safeCall(() => setFanCurve(draft.temps, draft.speeds, target));
    setStatus('curve', r, target === 'gpu' ? 'Custom GPU fan curve applied.' : 'Custom CPU fan curve applied.');
    await refresh();
    if (!mounted.current) return;
    setWorking(false);
    if (r.ok) setEditing(false);
  };

  const toggleArmoury = () => {
    if (armouryPending) return;
    // The target is always the opposite of the stored value, whatever the toggle reported.
    const on = !state.armoury.replaced;
    cancelPowerTimer();
    setStatuses((s) => ({ ...s, armoury: undefined }));
    armouryResult = null;
    const p = safeCall(() => setArmouryReplaced(on)).then((r) => {
      armouryResult = r;
      armouryPending = null;
      return r;
    });
    armouryPending = p;
    watchArmoury(p);
  };

  const draftReason = validateCurve(draft.temps, draft.speeds);
  const sel = Math.min(Math.max(selected, 0), CURVE_POINTS - 1);
  const shownCurve = usableCurve(fan === 'gpu' ? state.fan_curve_gpu : state.fan_curve);
  const fpsOptions = FPS_STEPS.map((f) => ({ data: f, label: f === 0 ? 'Off' : `${f} FPS` }));

  // ---------- layout ----------

  return (
    <>
      <PanelSection title="Overdrive">
        {unavailable && <Note text={unavailable} color="#ff6b6b" />}
        {busy && (
          <PanelSectionRow>
            <Field label={ARMOURY_BUSY} padding="compact">
              <Spinner style={{ width: '20px', height: '20px' }} />
            </Field>
          </PanelSectionRow>
        )}
        {state.warning && <Note text={state.warning} color="#ffb74d" />}
        {sensors.map((line, i) => (
          <Note key={'live' + i} text={line} />
        ))}
      </PanelSection>

      <PanelSection title="Performance">
        <PanelSectionRow>
          <Field label="Mode" childrenLayout="below" padding="compact">
            <Focusable flow-children="horizontal" style={{ display: 'flex', gap: '4px' }}>
              {MODES.map((m) => {
                const Btn = shown === m.mode ? DialogButtonPrimary : DialogButton;
                return (
                  <Btn
                    key={m.mode}
                    style={{ minWidth: '0', flex: '1 1 0', padding: '6px 2px', fontSize: '12px' }}
                    disabled={disabled}
                    onClick={() => chooseMode(m.mode)}
                  >
                    {m.label}
                  </Btn>
                );
              })}
            </Focusable>
          </Field>
        </PanelSectionRow>
        {shown === 'other' && <Note text={'Mode: ' + MODE_OTHER_TEXT} />}
        {state.user_set !== false && state.armoury.installed && !state.armoury.replaced && (
          <Note text="Armoury Crate is still running and can override this mode. Turn on Use Overdrive instead of Armoury Crate below." />
        )}
        <StatusRow status={statuses.mode} />
        {shown !== 'manual' && (
          <Note text={powerLine(state.device?.spl, state.device?.sppt, state.device?.fppt)} />
        )}
        {shown === 'manual' && (
          <>
            {(
              [
                ['spl', 'Sustained'],
                ['sppt', 'Slow boost'],
                ['fppt', 'Fast boost'],
              ] as const
            ).map(([key, label]) => (
              <PanelSectionRow key={key}>
                <SliderField
                  label={`${label}: ${power[key]} W`}
                  value={power[key]}
                  min={POWER_MIN}
                  max={POWER_MAX}
                  step={1}
                  validValues="range"
                  disabled={disabled}
                  onChange={(v) => changePower(key, v)}
                />
              </PanelSectionRow>
            ))}
            <Note text="Fast boost stays at or above Slow boost, which stays at or above Sustained." />
            <StatusRow status={statuses.power} />
          </>
        )}
        <PanelSectionRow>
          <InlinePicker
            label="FPS limit"
            options={fpsOptions}
            value={state.fps}
            disabled={disabled}
            onChange={(v) => run('fps', () => setFps(v))}
          />
        </PanelSectionRow>
        <StatusRow status={statuses.fps} />
      </PanelSection>

      <PanelSection title="Fans">
        <PanelSectionRow>
          <InlinePicker
            label="Fan preset"
            options={PRESETS}
            value={state.fan_preset}
            disabled={disabled}
            onChange={choosePreset}
          />
        </PanelSectionRow>
        {state.fan_note && <Note text={state.fan_note} color="#ffb74d" />}
        <StatusRow status={statuses.fans} />
        <PanelSectionRow>
          <Focusable flow-children="horizontal" style={{ display: 'flex', gap: '4px' }}>
            {FANS.map((f) => {
              const Btn = fan === f.fan ? DialogButtonPrimary : DialogButton;
              return (
                <Btn
                  key={f.fan}
                  style={{ minWidth: '0', flex: '1 1 0', padding: '6px 2px', fontSize: '12px' }}
                  onClick={() => setFan(f.fan)}
                >
                  {f.label}
                </Btn>
              );
            })}
          </Focusable>
        </PanelSectionRow>
        {!editing && (
          <>
            <PanelSectionRow>
              <ChartSvg curve={shownCurve} fan={fan} selected={-1} invalid={-1} cpuTemp={cpuTemp} gpuTemp={gpuTemp} editable={false} />
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={disabled} onClick={openEditor}>
                Edit curve
              </ButtonItem>
            </PanelSectionRow>
          </>
        )}
        {editing && (
          <>
            <PanelSectionRow>
              <FanChartEditor
                curve={draft}
                fan={fan}
                selected={sel}
                invalid={invalidPoint(draftReason)}
                cpuTemp={cpuTemp}
                gpuTemp={gpuTemp}
                disabled={disabled}
                onSelect={setSelected}
                onEdit={setDraft}
              />
            </PanelSectionRow>
            {draftReason && <Note text={draftReason} color="#ff6b6b" />}
            {hasStacks(draft) && (
              // Only changes the draft; nothing is sent until Apply.
              <PanelSectionRow>
                <ButtonItem layout="below" disabled={disabled} onClick={() => setDraft((c) => spreadPoints(c, minSpeedAt))}>
                  Spread points
                </ButtonItem>
              </PanelSectionRow>
            )}
            <PanelSectionRow>
              <Stepper
                label={`Point ${sel + 1} temperature`}
                value={draft.temps[sel]}
                min={CURVE_TEMP_MIN}
                max={CURVE_TEMP_MAX}
                unit="°C"
                disabled={disabled}
                onChange={(v) => editPoint('temps', sel, v)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <Stepper
                label={`Point ${sel + 1} fan`}
                value={draft.speeds[sel]}
                min={0}
                max={CURVE_SPEED_MAX}
                unit="%"
                disabled={disabled}
                onChange={(v) => editPoint('speeds', sel, v)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={disabled || !!draftReason} onClick={applyCurve}>
                {fan === 'gpu' ? 'Apply GPU fan curve' : 'Apply CPU fan curve'}
              </ButtonItem>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={working} onClick={() => setEditing(false)}>
                Cancel
              </ButtonItem>
            </PanelSectionRow>
          </>
        )}
        <StatusRow status={statuses.curve} />
      </PanelSection>

      <PanelSection title="Armoury Crate">
        {(state.armoury.installed || state.armoury.replaced) && (
          <PanelSectionRow>
            <ToggleField
              label="Use Overdrive instead of Armoury Crate"
              description="Stops the Armoury Crate services so they do not undo these settings. Turning it off restores them."
              // Steam's toggle flips itself on every press, so it is fed what it should show: the target while
              // switching, else the stored state. A failed switch changes the value back and the toggle follows.
              checked={busy ? !state.armoury.replaced : state.armoury.replaced}
              disabled={working || busy || !!unavailable}
              onChange={toggleArmoury}
            />
          </PanelSectionRow>
        )}
        <StatusRow status={statuses.armoury} />
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Balanced, factory power limits, default fan curves, FPS limit off."
            disabled={disabled}
            onClick={() => {
              if (!confirmed('reset')) return;
              cancelPowerTimer();
              setEditing(false);
              run('reset', resetDefaults);
            }}
          >
            {confirm === 'reset' ? CONFIRM_LABEL : 'Reset to Armoury Crate defaults'}
          </ButtonItem>
        </PanelSectionRow>
        <StatusRow status={statuses.reset} />
        <Note text="The battery charge limit is set in Control Center." />
      </PanelSection>
    </>
  );
}

export default definePlugin(() => ({
  name: 'Overdrive',
  titleView: <div className={staticClasses.Title}>Overdrive</div>,
  content: <Content />,
  icon: <FaBolt />,
}));
