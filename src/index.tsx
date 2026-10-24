import { callable, definePlugin } from '@decky/api';
import {
  ButtonItem,
  Field,
  Focusable,
  PanelSection,
  PanelSectionRow,
  Spinner,
  ToggleField,
  staticClasses,
} from '@decky/ui';
import { useEffect, useRef, useState } from 'react';
import { FaCheck, FaGamepad } from 'react-icons/fa';
import {
  ARMOURY_BUSY,
  ARMOURY_DESCRIPTION,
  ARMOURY_LABEL,
  CONTROLLER_NOT_FOUND,
  CONTROLLER_OPTIONS,
  ControllerMode,
  GRIP_VIBRATION_DESCRIPTION,
  NOT_ALLY,
  TOUCHSCREEN_DESCRIPTION,
  TOUCH_POLL_MS,
  TOUCH_TIMEOUT_TEXT,
  armouryChecked,
  armouryDisabled,
  controllerBanner,
  controllerModeDescription,
  touchscreenCheck,
} from './controls';
import { PickerOption, choose, focusIndex, pressCancel, pressRow, rowValue } from './picker';

// ---------- backend (main.py) ----------

type Support = { supported: boolean; product: string };
type Result = { ok: boolean; message: string };
type State = {
  support: Support;
  armoury: { installed: boolean; replaced: boolean; warning?: string };
  controller: { mode: ControllerMode; found: boolean };
  vibration: boolean;
  touchscreen: { found: boolean; enabled: boolean };
  blocked: string;
  note: string;
  busy: boolean;
  warning: string;
  unavailable: string;
};

const getSupport = callable<[], Support>('get_support');
const getState = callable<[], State>('get_state');
const setControllerMode = callable<[ControllerMode], Result>('set_controller_mode');
const setGripVibration = callable<[boolean], Result>('set_grip_vibration');
const setTouchscreen = callable<[boolean], Result>('set_touchscreen');
const setArmouryReplaced = callable<[boolean], Result>('set_armoury_replaced');

const UNAVAILABLE_RECHECK_MS = 5000;
const BUSY_RECHECK_MS = 3000;

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
    return { ok: false, message: 'Unexpected reply from Traction.' };
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
  // Row value when the current value is not one of the options (e.g. "Set in Windows").
  unknownText?: string;
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
    gap: '8px',
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
        <span style={{ whiteSpace: 'nowrap' }}>{label}</span>
        <span style={{ color: disabled ? '#8b929a' : '#ffffff', whiteSpace: 'nowrap' }}>{options.some((o) => o.data === value) || !props.unknownText
            ? rowValue(options, value, open)
            : props.unknownText + (open ? ' ▴' : ' ▾')}
        </span>
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

function Content() {
  const [support, setSupport] = useState<Support | null>(null);
  const [startError, setStartError] = useState('');
  const [state, setState] = useState<State | null>(null);
  const [statuses, setStatuses] = useState<Record<string, Status>>({});
  const [working, setWorking] = useState(false);
  // The target while a toggle's call runs, so a failed change falls back to the stored value.
  const [toggleTarget, setToggleTarget] = useState<Record<string, boolean | undefined>>({});
  const [armouryBusy, setArmouryBusy] = useState(armouryPending !== null);
  const mounted = useRef(true);

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

  const start = () => {
    getSupport()
      .then((s) => {
        if (!mounted.current) return;
        setSupport(s);
        if (s.supported) refresh();
      })
      .catch((e) => {
        // A failed call is not an unsupported device.
        if (mounted.current) setStartError(errorText(e) || 'No reply from the plugin backend.');
      });
  };

  useEffect(() => {
    mounted.current = true;
    start();
    if (armouryPending) watchArmoury(armouryPending);
    else showArmouryResult();
    return () => {
      mounted.current = false;
    };
  }, []);

  const busy = armouryBusy || !!state?.busy;
  const unavailable = state?.unavailable || '';

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

  const retryStart = () => {
    setStartError('');
    setSupport(null);
    start();
  };

  // ---------- start-up screens ----------

  if (startError && !state) {
    return (
      <PanelSection title="Traction">
        <Note text="Traction could not start" color="#ff6b6b" />
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
      <PanelSection title="Traction">
        <PanelSectionRow>
          <Spinner />
        </PanelSectionRow>
      </PanelSection>
    );
  }
  if (!support.supported || !state) {
    return (
      <PanelSection title="Traction">
        <Note text={NOT_ALLY} />
      </PanelSection>
    );
  }

  const banner = controllerBanner(state.blocked, state.note);
  const disabled = working || busy || !!unavailable;
  const controllerDisabled = disabled || banner.disabled;
  const touch = state.touchscreen;

  // ---------- actions ----------

  const toggleSetting = async (key: string, stored: boolean, call: (on: boolean) => Promise<Result>) => {
    // A single press; the target is always the opposite of the stored value, whatever the toggle reported.
    const on = !stored;
    setToggleTarget((t) => ({ ...t, [key]: on }));
    await run(key, () => call(on));
    if (mounted.current) setToggleTarget((t) => ({ ...t, [key]: undefined }));
  };

  // The helper answers at once; the device changes a little later. Keep the toggle on its target and re-read the state
  // every 1.5 s (up to 15 s) until the device agrees. Stops when the panel unmounts.
  const toggleTouchscreen = async () => {
    const on = !state.touchscreen.enabled;
    setToggleTarget((t) => ({ ...t, touch: on }));
    setWorking(true);
    const r = await safeCall(() => setTouchscreen(on));
    if (r.ok) {
      const began = Date.now();
      let verdict: 'done' | 'wait' | 'timeout' = 'wait';
      while (mounted.current && verdict === 'wait') {
        await new Promise((res) => setTimeout(res, TOUCH_POLL_MS));
        if (!mounted.current) return;
        try {
          const st = await getState();
          if (!mounted.current) return;
          setState(st);
          verdict = touchscreenCheck(st.touchscreen?.enabled, on, Date.now() - began);
        } catch {
          verdict = touchscreenCheck(undefined, on, Date.now() - began);
        }
      }
      if (!mounted.current) return;
      setStatus('touch', verdict === 'done' ? r : { ok: false, message: TOUCH_TIMEOUT_TEXT });
    } else {
      await refresh();
      if (!mounted.current) return;
      setStatus('touch', r);
    }
    setToggleTarget((t) => ({ ...t, touch: undefined }));
    setWorking(false);
  };

  const toggleArmoury = () => {
    if (armouryPending) return;
    // The target is always the opposite of the stored value, whatever the toggle reported.
    const on = !state.armoury.replaced;
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

  // ---------- layout ----------

  return (
    <PanelSection title="Traction">
      {unavailable && <Note text={unavailable} color="#ff6b6b" />}
      {busy && (
        <PanelSectionRow>
          <Field label={ARMOURY_BUSY} padding="compact">
            <Spinner style={{ width: '20px', height: '20px' }} />
          </Field>
        </PanelSectionRow>
      )}
      {state.warning && <Note text={state.warning} color="#ffb74d" />}
      {state.armoury.warning && <Note text={state.armoury.warning} color="#ffb74d" />}
      {(state.armoury.installed || state.armoury.replaced) && (
        <PanelSectionRow>
          <ToggleField
            label={ARMOURY_LABEL}
            description={ARMOURY_DESCRIPTION}
            // Steam's toggle flips itself on every press, so it is fed what it should show: the target while
            // switching, else the stored state. A failed switch changes the value back and the toggle follows.
            checked={armouryChecked(state.armoury.replaced, busy)}
            disabled={armouryDisabled(disabled, state.blocked)}
            onChange={toggleArmoury}
          />
        </PanelSectionRow>
      )}
      <StatusRow status={statuses.armoury} />
      <Note text={banner.text} color={banner.color} />
      {state.controller.found ? (
        <>
          <PanelSectionRow>
            <InlinePicker
              label="Controller mode"
              options={CONTROLLER_OPTIONS}
              value={state.controller.mode ?? 'gamepad'}
              disabled={controllerDisabled}
              onChange={(v) => run('controller', () => setControllerMode(v))}
            />
          </PanelSectionRow>
          <Note text={controllerModeDescription(state.controller.mode)} />
          <StatusRow status={statuses.controller} />
          <PanelSectionRow>
            <ToggleField
              label="Grip vibration"
              description={GRIP_VIBRATION_DESCRIPTION}
              checked={toggleTarget.grip ?? !!state.vibration}
              disabled={controllerDisabled}
              onChange={() => toggleSetting('grip', !!state.vibration, setGripVibration)}
            />
          </PanelSectionRow>
          <StatusRow status={statuses.grip} />
        </>
      ) : (
        <Note text={CONTROLLER_NOT_FOUND} />
      )}
      {touch.found && (
        <>
          <PanelSectionRow>
            <ToggleField
              label="Touch Screen"
              description={TOUCHSCREEN_DESCRIPTION}
              checked={toggleTarget.touch ?? touch.enabled}
              disabled={disabled}
              onChange={toggleTouchscreen}
            />
          </PanelSectionRow>
          <StatusRow status={statuses.touch} />
        </>
      )}
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: 'Traction',
  titleView: <div className={staticClasses.Title}>Traction</div>,
  content: <Content />,
  icon: <FaGamepad />,
}));
