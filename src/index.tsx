import { callable, definePlugin } from '@decky/api';
import {
  ButtonItem,
  DialogButton,
  DialogButtonPrimary,
  DropdownItem,
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
import { FaBolt } from 'react-icons/fa';
import {
  CURVE_POINTS,
  CURVE_SPEED_MAX,
  CURVE_TEMP_MAX,
  CURVE_TEMP_MIN,
  Curve,
  defaultDraft,
  liveLine,
  stepValue,
  validateCurve,
} from './curve';

// ---------- backend (main.py) ----------

type Mode = 'silent' | 'balanced' | 'turbo' | 'manual';
type Preset = 'default' | 'quiet' | 'cool' | 'custom';
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
  armoury: { installed: boolean; replaced: boolean };
  unavailable: string;
  warning: string;
  fan_note: string;
  busy?: boolean; // ruling P12; read when the backend reports it
};
type Sensors = { ok: boolean; message: string; cpuTempC: number | null; fanRpm: number | null; errors: string[] };

const getSupport = callable<[], Support>('get_support');
const getState = callable<[], State>('get_state');
const getSensors = callable<[], Sensors>('get_sensors');
const setMode = callable<[Mode, number?, number?, number?], Result>('set_mode');
const setFps = callable<[number], Result>('set_fps');
const setFanPreset = callable<[Preset], Result>('set_fan_preset');
const setFanCurve = callable<[number[], number[]], Result>('set_fan_curve');
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
const PRESETS: { data: Preset; label: string }[] = [
  { data: 'default', label: 'Armoury default' },
  { data: 'quiet', label: 'Quiet' },
  { data: 'cool', label: 'Cool' },
  { data: 'custom', label: 'Custom' },
];
const POWER_MIN = 5;
const POWER_MAX = 50;
const POWER_DEBOUNCE_MS = 700;
const SENSOR_POLL_MS = 2000;

// An Armoury switch can outlive the panel (closing the menu unmounts it), so the pending call lives at module level.
let armouryPending: Promise<Result> | null = null;

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

// ---------- panel ----------

function Content() {
  const [support, setSupport] = useState<Support | null>(null);
  const [startError, setStartError] = useState('');
  const [state, setState] = useState<State | null>(null);
  const [sensors, setSensors] = useState('');
  const [statuses, setStatuses] = useState<Record<string, Status>>({});
  const [working, setWorking] = useState(false);
  const [armouryBusy, setArmouryBusy] = useState(armouryPending !== null);
  const [power, setPower] = useState({ spl: 30, sppt: 30, fppt: 30 });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Curve>(defaultDraft());
  const mounted = useRef(true);
  const powerTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setStatus = (key: string, r: Result, okText = 'Applied.') => {
    if (!mounted.current) return;
    setStatuses((s) => ({ ...s, [key]: { ok: r.ok, text: r.ok ? r.message || okText : r.message || 'Failed.' } }));
  };

  const refresh = async () => {
    try {
      const st = await getState();
      if (!mounted.current) return;
      setState(st);
      setPower({ spl: st.spl, sppt: st.sppt, fppt: st.fppt });
      setStartError('');
    } catch (e) {
      if (mounted.current) setStartError(errorText(e));
    }
  };

  const watchArmoury = (p: Promise<Result>) => {
    setArmouryBusy(true);
    p.then((r) => {
      if (!mounted.current) return;
      setStatus('armoury', r);
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
    return () => {
      mounted.current = false;
      if (powerTimer.current) clearTimeout(powerTimer.current);
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
        if (mounted.current) setSensors(s.ok ? liveLine(s.cpuTempC, s.fanRpm) : s.message || 'Sensors not available');
      } catch (e) {
        if (mounted.current) setSensors('Sensors not available: ' + errorText(e));
      } finally {
        inFlight = false;
      }
    };
    poll();
    const id = setInterval(poll, SENSOR_POLL_MS);
    return () => clearInterval(id);
  }, [support?.supported, busy, unavailable]);

  const run = async (key: string, fn: () => Promise<Result>) => {
    setWorking(true);
    const r = await safeCall(fn);
    setStatus(key, r);
    await refresh();
    if (mounted.current) setWorking(false);
  };

  // ---------- start-up screens ----------

  if (startError && !state) {
    return (
      <PanelSection title="Overdrive">
        <Note text="Overdrive could not start" color="#ff6b6b" />
        <Note text={startError} />
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => {
              setStartError('');
              setSupport(null);
              getSupport()
                .then((s) => {
                  setSupport(s);
                  if (s.supported) refresh();
                })
                .catch((e) => setStartError(errorText(e) || 'No reply from the plugin backend.'));
            }}
          >
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

  // ---------- actions ----------

  const chooseMode = (mode: Mode) => {
    if (mode === state.mode && mode !== 'manual') return;
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
    const c = state.fan_curve;
    setDraft(c && c.temps.length === CURVE_POINTS && c.speeds.length === CURVE_POINTS
      ? { temps: [...c.temps], speeds: [...c.speeds] }
      : defaultDraft());
    setStatuses((s) => ({ ...s, curve: undefined }));
    setEditing(true);
  };

  const editPoint = (kind: 'temps' | 'speeds', i: number, v: number) => {
    setDraft((d) => {
      const next = { temps: [...d.temps], speeds: [...d.speeds] };
      next[kind][i] = v;
      return next;
    });
  };

  const applyCurve = async () => {
    const reason = validateCurve(draft.temps, draft.speeds);
    if (reason) {
      // Blocked before the backend is called.
      setStatus('curve', { ok: false, message: reason });
      return;
    }
    setWorking(true);
    const r = await safeCall(() => setFanCurve(draft.temps, draft.speeds));
    setStatus('curve', r, 'Custom fan curve applied.');
    await refresh();
    if (!mounted.current) return;
    setWorking(false);
    if (r.ok) setEditing(false);
  };

  const toggleArmoury = (on: boolean) => {
    if (armouryPending) return;
    setStatuses((s) => ({ ...s, armoury: undefined }));
    const p = safeCall(() => setArmouryReplaced(on)).finally(() => {
      armouryPending = null;
    });
    armouryPending = p;
    watchArmoury(p);
  };

  const draftReason = validateCurve(draft.temps, draft.speeds);
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
        <Note text={sensors} />
      </PanelSection>

      <PanelSection title="Performance">
        <PanelSectionRow>
          <Field label="Mode" childrenLayout="below" padding="compact">
            <Focusable flow-children="horizontal" style={{ display: 'flex', gap: '4px' }}>
              {MODES.map((m) => {
                const Btn = state.mode === m.mode ? DialogButtonPrimary : DialogButton;
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
        <StatusRow status={statuses.mode} />
        {state.mode === 'manual' && (
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
          <DropdownItem
            label="FPS limit"
            rgOptions={fpsOptions}
            selectedOption={state.fps}
            disabled={disabled}
            onChange={(o) => run('fps', () => setFps(o.data as number))}
          />
        </PanelSectionRow>
        <StatusRow status={statuses.fps} />
      </PanelSection>

      <PanelSection title="Fans">
        <PanelSectionRow>
          <DropdownItem
            label="Fan preset"
            rgOptions={PRESETS}
            selectedOption={state.fan_preset}
            disabled={disabled}
            onChange={(o) => run('fans', () => setFanPreset(o.data as Preset))}
          />
        </PanelSectionRow>
        {state.fan_note && <Note text={state.fan_note} color="#ffb74d" />}
        <StatusRow status={statuses.fans} />
        {!editing && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={disabled} onClick={openEditor}>
              Edit curve
            </ButtonItem>
          </PanelSectionRow>
        )}
        {editing && (
          <>
            {draft.temps.map((t, i) => (
              <div key={i}>
                <PanelSectionRow>
                  <Stepper
                    label={`Point ${i + 1}`}
                    value={t}
                    min={CURVE_TEMP_MIN}
                    max={CURVE_TEMP_MAX}
                    unit="°C"
                    disabled={disabled}
                    onChange={(v) => editPoint('temps', i, v)}
                  />
                </PanelSectionRow>
                <PanelSectionRow>
                  <Stepper
                    label="Fan"
                    value={draft.speeds[i]}
                    min={0}
                    max={CURVE_SPEED_MAX}
                    unit="%"
                    disabled={disabled}
                    onChange={(v) => editPoint('speeds', i, v)}
                  />
                </PanelSectionRow>
              </div>
            ))}
            {draftReason && <Note text={draftReason} color="#ff6b6b" />}
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={disabled} onClick={applyCurve}>
                Apply curve
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
        {state.armoury.installed && (
          <PanelSectionRow>
            <ToggleField
              label="Use Overdrive instead of Armoury Crate"
              description="Stops the Armoury Crate services so they do not undo these settings. Turning it off restores them."
              checked={state.armoury.replaced}
              disabled={working || busy || !!unavailable}
              onChange={toggleArmoury}
            />
          </PanelSectionRow>
        )}
        <StatusRow status={statuses.armoury} />
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            description="Balanced, factory power limits, default fan curve, FPS limit off."
            disabled={disabled}
            onClick={() => {
              setEditing(false);
              run('reset', resetDefaults);
            }}
          >
            Reset to Armoury Crate defaults
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
