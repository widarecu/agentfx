/**
 * Vitest for `computeDrawdownSeries` — PR 5 of the LivePnLChart
 * redesign. Locks the running-peak math + drawdownPct sign so the
 * SVG shaded polygon renders where the run was actually underwater.
 */

import { describe, expect, it } from 'vitest';
import {
  computeDrawdownSeries,
  type EquityPoint,
} from '../../src/utils/livePnlChart';

function ep(ts: string, balance: number): EquityPoint {
  return { timestampMs: new Date(ts).getTime(), balance, unrealizedPnl: 0 };
}

describe('computeDrawdownSeries', () => {
  it('returns [] on empty input', () => {
    expect(computeDrawdownSeries([], 100_000)).toEqual([]);
  });

  it('monotonically rising curve → drawdownPct always 0', () => {
    const dd = computeDrawdownSeries(
      [
        ep('2026-07-17T12:00:00Z', 100_000),
        ep('2026-07-17T13:00:00Z', 100_100),
        ep('2026-07-17T14:00:00Z', 100_250),
      ],
      100_000,
    );
    expect(dd.every((p) => p.drawdownPct === 0)).toBe(true);
    expect(dd[dd.length - 1].peakBalance).toBe(100_250);
  });

  it('peakBalance ratchets up but never down', () => {
    const dd = computeDrawdownSeries(
      [
        ep('2026-07-17T12:00:00Z', 100_000),
        ep('2026-07-17T13:00:00Z', 101_000), // peak
        ep('2026-07-17T14:00:00Z', 100_500),
        ep('2026-07-17T15:00:00Z', 100_800),
      ],
      100_000,
    );
    expect(dd.map((p) => p.peakBalance)).toEqual([
      100_000,
      101_000,
      101_000,
      101_000,
    ]);
  });

  it('drawdownPct is negative-or-zero and captures the depth', () => {
    // Peak $101,000, dips to $99,000 → drawdownPct at trough =
    // -(2000/101_000) ~= -1.9802%.
    const dd = computeDrawdownSeries(
      [
        ep('2026-07-17T12:00:00Z', 100_000),
        ep('2026-07-17T13:00:00Z', 101_000),
        ep('2026-07-17T14:00:00Z', 99_000), // trough
        ep('2026-07-17T15:00:00Z', 100_200), // recovery
      ],
      100_000,
    );
    // dd points map 1:1 to input samples.
    expect(dd).toHaveLength(4);
    expect(dd[0].drawdownPct).toBe(0);
    expect(dd[1].drawdownPct).toBe(0);
    expect(dd[2].drawdownPct).toBeCloseTo(-(2000 / 101_000) * 100, 6);
    expect(dd[3].drawdownPct).toBeCloseTo(-(800 / 101_000) * 100, 6);
  });

  it('initialPeak seeds the running peak so underwater-from-start counts', () => {
    // Balance never rises above snapshot; if `initialPeak` weren't
    // seeded to the snapshot, the first sample's drawdown would be 0
    // (peak=first balance), understating true DD.
    const dd = computeDrawdownSeries(
      [
        ep('2026-07-17T12:00:00Z', 99_800),
        ep('2026-07-17T13:00:00Z', 99_500),
      ],
      100_000,
    );
    expect(dd[0].drawdownPct).toBeCloseTo(-0.2, 4);
    expect(dd[1].drawdownPct).toBeCloseTo(-0.5, 4);
  });

  it('preserves each sample timestamp and balance', () => {
    const in0 = ep('2026-07-17T12:00:00Z', 100_100);
    const in1 = ep('2026-07-17T13:00:00Z', 99_800);
    const dd = computeDrawdownSeries([in0, in1], 100_000);
    expect(dd[0].timestampMs).toBe(in0.timestampMs);
    expect(dd[0].balance).toBe(in0.balance);
    expect(dd[1].timestampMs).toBe(in1.timestampMs);
    expect(dd[1].balance).toBe(in1.balance);
  });

  it('handles zero/negative initialPeak defensively (drawdownPct=0)', () => {
    // Contrived: an account initialised with 0. peak/(peak > 0 ? ...)
    // branch returns 0 for the drawdown to avoid divide-by-zero.
    const dd = computeDrawdownSeries(
      [ep('2026-07-17T12:00:00Z', 0), ep('2026-07-17T13:00:00Z', 100)],
      0,
    );
    // First sample: peak=0, balance=0, dd=0.
    expect(dd[0].drawdownPct).toBe(0);
    // Second sample: peak lifts to 100, dd=0.
    expect(dd[1].peakBalance).toBe(100);
    expect(dd[1].drawdownPct).toBe(0);
  });
});
