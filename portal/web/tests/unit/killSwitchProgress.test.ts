/**
 * Vitest for the pure math helpers in `KillSwitchButton.tsx` (PR-I).
 *
 * The component itself uses `setTimeout` + `setInterval` and would
 * need jsdom to exercise. The maths — progress-bar %, seconds
 * remaining — is pure and testable in the node env.
 */

import { describe, expect, it } from 'vitest';
import {
  KILL_UNDO_WINDOW_MS,
  _computeProgressPct,
  _computeRemainingSec,
} from '../../src/components/KillSwitchButton';

describe('_computeProgressPct', () => {
  it('is 0% at arm-time', () => {
    const now = 1_000_000;
    expect(_computeProgressPct(now + KILL_UNDO_WINDOW_MS, now)).toBe(0);
  });

  it('is 50% at half the window', () => {
    const now = 1_000_000;
    expect(
      _computeProgressPct(
        now + KILL_UNDO_WINDOW_MS / 2,
        now,
        KILL_UNDO_WINDOW_MS,
      ),
    ).toBe(50);
  });

  it('is 100% at deadline', () => {
    const deadline = 1_000_000;
    expect(_computeProgressPct(deadline, deadline)).toBe(100);
  });

  it('clamps past-deadline to 100% (never rolls over)', () => {
    const deadline = 1_000_000;
    expect(_computeProgressPct(deadline, deadline + 500)).toBe(100);
    expect(_computeProgressPct(deadline, deadline + 10_000)).toBe(100);
  });
});

describe('_computeRemainingSec', () => {
  it('rounds up to nearest second', () => {
    const deadline = 3_000; // 3s into the window
    expect(_computeRemainingSec(deadline, 0)).toBe(3);
    // 500ms into the window (2.5s left) → ceil to 3
    expect(_computeRemainingSec(deadline, 500)).toBe(3);
    // 1s into the window (2s left) → 2
    expect(_computeRemainingSec(deadline, 1_000)).toBe(2);
    // 2500ms into the window (0.5s left) → ceil to 1
    expect(_computeRemainingSec(deadline, 2_500)).toBe(1);
  });

  it('never returns a negative value', () => {
    const deadline = 3_000;
    expect(_computeRemainingSec(deadline, 5_000)).toBe(0);
  });

  it('is exactly 0 at deadline', () => {
    expect(_computeRemainingSec(1_000, 1_000)).toBe(0);
  });
});

describe('KILL_UNDO_WINDOW_MS', () => {
  it("locks Wilson's 3-second window from the plan", () => {
    // Not really a test of behaviour — a canary. If someone changes
    // the window without matching the wireframe copy, this fails
    // loudly instead of confusing the user.
    expect(KILL_UNDO_WINDOW_MS).toBe(3_000);
  });
});
