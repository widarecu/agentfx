/**
 * Vitest for `_stepAfterPath` — Wilson UAT 2026-07-25.
 *
 * The equity curve used to render as a straight-line join between
 * samples, which drew a slanted line across trade-close events that
 * visually implied balance drifted between measurements. The truth:
 * balance stayed at A until the close, then jumped to B. Step-after
 * models that: horizontal from (t_prev, A) to (t_cur, A), then
 * vertical to (t_cur, B).
 */

import { describe, expect, it } from 'vitest';
import { _stepAfterPath } from '../../src/components/LivePnLChart';
import type { EquityPoint } from '../../src/utils/livePnlChart';

// Deterministic scaleX/scaleY: timestamps map at 1ms → 1 pixel, y
// stays as balance for easy assertions.
const scaleX = (ms: number) => ms;
const scaleY = (b: number) => b;

function ep(ts: number, balance: number): EquityPoint {
  return { timestampMs: ts, balance, unrealizedPnl: 0 };
}

describe('_stepAfterPath', () => {
  it('returns empty string on empty input', () => {
    expect(_stepAfterPath([], scaleX, scaleY)).toBe('');
  });

  it('a single point emits only an M command', () => {
    expect(_stepAfterPath([ep(100, 200)], scaleX, scaleY)).toBe('M100.0,200.0');
  });

  it('two samples at the same balance emit a flat step', () => {
    // Move to (100, 200); step-after → L(200, 200) horizontal; L(200, 200)
    // vertical (no-op). SVG collapses the second L visually but we still
    // emit it for consistency.
    expect(_stepAfterPath([ep(100, 200), ep(200, 200)], scaleX, scaleY)).toBe(
      'M100.0,200.0 L200.0,200.0 L200.0,200.0',
    );
  });

  it('a balance jump between samples renders as flat-then-vertical', () => {
    // Before Wilson's fix this would have drawn a slanted line from
    // (100, 200) to (200, 250). Now: horizontal to (200, 200) then
    // vertical to (200, 250) — the balance jump is visually LOCATED at
    // the close-event timestamp, not spread across the interval.
    expect(_stepAfterPath([ep(100, 200), ep(200, 250)], scaleX, scaleY)).toBe(
      'M100.0,200.0 L200.0,200.0 L200.0,250.0',
    );
  });

  it('a downward jump renders symmetrically', () => {
    expect(_stepAfterPath([ep(0, 500), ep(60, 450)], scaleX, scaleY)).toBe(
      'M0.0,500.0 L60.0,500.0 L60.0,450.0',
    );
  });

  it('multi-jump series stitches all steps together', () => {
    // Balance profile: 100 → 100 → 120 → 120 → 90 → 90. Each pair
    // adds two L commands to the path.
    const series = [
      ep(0, 100),
      ep(10, 100),
      ep(20, 120),
      ep(30, 120),
      ep(40, 90),
      ep(50, 90),
    ];
    const path = _stepAfterPath(series, scaleX, scaleY);
    // 1 M + 5 * (2 L commands) = 11 commands total.
    const commands = path.split(' ');
    expect(commands).toHaveLength(11);
    expect(commands[0]).toBe('M0.0,100.0');
    // Every "vertical" step lands at the correct next-balance.
    expect(path).toContain('L20.0,120.0');
    expect(path).toContain('L40.0,90.0');
    // Verify no slanted segments — every L pair either shares x
    // (vertical) or shares y (horizontal), never both differ at once.
    let prev = { x: 0, y: 100 };
    for (let i = 1; i < commands.length; i++) {
      const m = commands[i].match(/L([\d.]+),([\d.]+)/);
      if (!m) continue;
      const x = parseFloat(m[1]);
      const y = parseFloat(m[2]);
      const dx = Math.abs(x - prev.x);
      const dy = Math.abs(y - prev.y);
      // Either horizontal (dy≈0) or vertical (dx≈0) — never diagonal.
      const isFlat = dy < 1e-6;
      const isVertical = dx < 1e-6;
      expect(isFlat || isVertical).toBe(true);
      prev = { x, y };
    }
  });
});
