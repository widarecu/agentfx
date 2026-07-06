/**
 * Big red kill switch + 3-second undo toast (US-023 / PR-I).
 *
 * Wilson's decision (locked in the plan): "Big red button + 3-second
 * undo toast — one click starts a 3-second countdown; if Undo isn't
 * pressed the run halts. No modal, no type-name-to-confirm — a real
 * emergency can't afford friction."
 *
 * The toast overlays the underlying page and dims interactions
 * behind it so double-click / spam is impossible during the window.
 * State machine:
 *
 *   idle → armed(deadline=+3s) → committing → idle-after-request
 *          ↓ Undo before deadline → idle
 *
 * If `kill_requested_at` on the run is already set (kill was
 * requested from another tab / by admin), the button renders as
 * disabled with a small "halt requested" hint. That mirrors the
 * runner's own idempotence — the DB is the source of truth, the
 * frontend just surfaces it.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2 } from 'lucide-react';

/** Milliseconds the user has to press Undo. */
export const KILL_UNDO_WINDOW_MS = 3_000;
/** How often the progress bar re-renders. Cheap; jsdom-free. */
const TICK_INTERVAL_MS = 50;

interface KillSwitchButtonProps {
  /** Called after the 3s window elapses without Undo. */
  onKill: () => Promise<void>;
  /** When set, the button is disabled — kill already requested. */
  disabledReason?: string | null;
}

type State =
  | { kind: 'idle' }
  | { kind: 'armed'; deadline: number }
  | { kind: 'committing' };

export function KillSwitchButton({ onKill, disabledReason }: KillSwitchButtonProps) {
  const [state, setState] = useState<State>({ kind: 'idle' });
  // For the progress bar animation. Kept as its own state so a re-render
  // per tick doesn't churn the whole state machine.
  const [tickNow, setTickNow] = useState<number>(0);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearTimers = useCallback(() => {
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    if (intervalRef.current !== null) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  useEffect(() => () => clearTimers(), [clearTimers]);

  function arm() {
    if (state.kind !== 'idle' || disabledReason) return;
    const deadline = Date.now() + KILL_UNDO_WINDOW_MS;
    setState({ kind: 'armed', deadline });
    setTickNow(Date.now());

    intervalRef.current = setInterval(
      () => setTickNow(Date.now()),
      TICK_INTERVAL_MS,
    );
    timeoutRef.current = setTimeout(async () => {
      clearTimers();
      setState({ kind: 'committing' });
      try {
        await onKill();
      } finally {
        // Whether the request succeeded or not, unmount the toast —
        // the parent will observe the terminal state via polling
        // and re-render the header with a disabled button.
        setState({ kind: 'idle' });
      }
    }, KILL_UNDO_WINDOW_MS);
  }

  function undo() {
    if (state.kind !== 'armed') return;
    clearTimers();
    setState({ kind: 'idle' });
  }

  if (disabledReason) {
    return (
      <button
        type="button"
        disabled
        className="inline-flex items-center gap-2 px-5 py-3 bg-slate-300 dark:bg-slate-700 text-slate-500 rounded-lg font-bold cursor-not-allowed"
        aria-label={disabledReason}
        title={disabledReason}
      >
        <AlertTriangle className="w-4 h-4" />
        Halt requested
      </button>
    );
  }

  return (
    <>
      <button
        type="button"
        onClick={arm}
        disabled={state.kind !== 'idle'}
        className="inline-flex items-center gap-2 px-5 py-3 bg-rose-600 hover:bg-rose-700 text-white rounded-lg font-bold shadow-md disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <AlertTriangle className="w-4 h-4" />
        Kill switch
      </button>
      {state.kind === 'armed' && (
        <ArmedToast
          deadline={state.deadline}
          tickNow={tickNow}
          onUndo={undo}
        />
      )}
      {state.kind === 'committing' && <CommittingToast />}
    </>
  );
}

function ArmedToast({
  deadline,
  tickNow,
  onUndo,
}: {
  deadline: number;
  tickNow: number;
  onUndo: () => void;
}) {
  const remainingMs = Math.max(0, deadline - tickNow);
  const remainingSec = Math.ceil(remainingMs / 1_000);
  const pct = 100 - Math.round((remainingMs / KILL_UNDO_WINDOW_MS) * 100);

  return (
    <div
      role="alertdialog"
      aria-label="Confirm kill switch"
      className="fixed top-20 left-1/2 -translate-x-1/2 z-50 bg-rose-700 text-rose-50 rounded-lg shadow-2xl p-4 flex items-center gap-4 min-w-[420px] border border-rose-900"
    >
      <div className="text-3xl font-bold font-mono" aria-live="polite">
        {remainingSec}
      </div>
      <div className="flex-1">
        <div className="text-sm font-semibold">
          Halting run in {remainingSec}{' '}
          {remainingSec === 1 ? 'second' : 'seconds'}…
        </div>
        <div className="text-xs opacity-80 mt-0.5">
          Positions will close on OANDA. This cannot be undone once complete.
        </div>
        <div className="mt-2 h-1.5 bg-rose-900 rounded overflow-hidden">
          <div
            className="h-full bg-rose-300 transition-all duration-100 ease-linear"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
      <button
        type="button"
        onClick={onUndo}
        className="bg-white text-rose-900 hover:bg-rose-100 font-bold text-sm px-4 py-2 rounded shadow shrink-0"
      >
        Undo
      </button>
    </div>
  );
}

function CommittingToast() {
  return (
    <div
      role="status"
      aria-label="Halt in progress"
      className="fixed top-20 left-1/2 -translate-x-1/2 z-50 bg-rose-700 text-rose-50 rounded-lg shadow-2xl p-4 flex items-center gap-3 min-w-[420px] border border-rose-900"
    >
      <Loader2 className="w-6 h-6 animate-spin" />
      <div className="flex-1 text-sm font-semibold">
        Halting run… positions closing on OANDA.
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Pure helper — exposed for Vitest to lock the progress-bar maths
// without needing jsdom. Not exported from any package barrel.
// ─────────────────────────────────────────────────────────────────────

export function _computeProgressPct(
  deadline: number,
  now: number,
  windowMs: number = KILL_UNDO_WINDOW_MS,
): number {
  const remainingMs = Math.max(0, deadline - now);
  return 100 - Math.round((remainingMs / windowMs) * 100);
}

export function _computeRemainingSec(
  deadline: number,
  now: number,
): number {
  const remainingMs = Math.max(0, deadline - now);
  return Math.ceil(remainingMs / 1_000);
}
