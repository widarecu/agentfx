/**
 * Vitest coverage for the hover-related pure helpers used by PR 2 of
 * the LivePnLChart redesign: `nearestSample`, `interpolateOverlay`,
 * `tradesUpTo`, and the axis-inverse `_clientXToTimestamp`.
 *
 * Node env, no jsdom. Same pattern as `livePnlChartHelpers.test.ts`
 * and `deployPaperModalRiskConfig.test.ts`.
 */

import { describe, expect, it } from 'vitest';
import { _clientXToTimestamp } from '../../src/components/LivePnLChart';
import type { PaperTradePublic } from '../../src/types/paper';
import {
  interpolateOverlay,
  nearestSample,
  tradesUpTo,
  type EquityPoint,
} from '../../src/utils/livePnlChart';

const MIN = 60_000;

function ep(ts: string, balance: number, unrealizedPnl = 0): EquityPoint {
  return { timestampMs: new Date(ts).getTime(), balance, unrealizedPnl };
}

function trade(overrides: Partial<PaperTradePublic>): PaperTradePublic {
  return {
    id: 'trade-x',
    entry_timestamp: '2026-07-17T13:00:00Z',
    exit_timestamp: '2026-07-17T14:00:00Z',
    side: 'buy',
    quantity: 10_000,
    entry_price: 1.1,
    exit_price: 1.1005,
    pnl: 5,
    pnl_pct: 0.05,
    origin: 'live',
    signal_reason: 'sma > sma',
    exit_reason: 'target',
    financing: 0,
    commission: 0,
    net_pnl: 5,
    ...overrides,
  };
}

// ─────────────────────────────────────────────────────────────────────
// nearestSample
// ─────────────────────────────────────────────────────────────────────

describe('nearestSample', () => {
  const series = [
    ep('2026-07-17T13:00:00Z', 100),
    ep('2026-07-17T13:01:00Z', 101),
    ep('2026-07-17T13:02:00Z', 102),
    ep('2026-07-17T13:03:00Z', 103),
    ep('2026-07-17T13:05:00Z', 105),
  ];

  it('returns null on an empty series', () => {
    expect(nearestSample([], Date.now())).toBeNull();
  });

  it('returns the sole sample when the series has one point', () => {
    const [only] = [ep('2026-07-17T13:00:00Z', 42)];
    expect(nearestSample([only], Date.now())).toBe(only);
  });

  it('snaps to the first sample when target is before the series', () => {
    const target = new Date('2026-07-17T12:00:00Z').getTime();
    expect(nearestSample(series, target)).toBe(series[0]);
  });

  it('snaps to the last sample when target is after the series', () => {
    const target = new Date('2026-07-17T14:00:00Z').getTime();
    expect(nearestSample(series, target)).toBe(series[series.length - 1]);
  });

  it('picks the closer of the two bracketing samples', () => {
    // Cursor at 13:03:20 → nearest is 13:03 (20s away) not 13:05 (100s away).
    const target = new Date('2026-07-17T13:03:20Z').getTime();
    expect(nearestSample(series, target)).toBe(series[3]);
  });

  it('picks the LEFT bracket on an exact-midpoint tie', () => {
    // Cursor at 13:04:00 sits exactly between 13:03 and 13:05. Both
    // deltas are 60s; the tie-break in nearestSample favours the
    // earlier sample (<=) which matches "snap to the most recent
    // confirmed point" — better UX than jumping forward in time on a
    // tie.
    const target = new Date('2026-07-17T13:04:00Z').getTime();
    expect(nearestSample(series, target)).toBe(series[3]);
  });

  it('binary search finds the right sample in a large series', () => {
    // Build 10,000 samples one second apart; cursor at sample #7500.
    const big: EquityPoint[] = Array.from({ length: 10_000 }, (_, i) =>
      ep(new Date(1_700_000_000_000 + i * 1000).toISOString(), 100 + i),
    );
    const target = 1_700_000_000_000 + 7500 * 1000 + 100;
    const hit = nearestSample(big, target);
    expect(hit?.balance).toBe(100 + 7500);
  });
});

// ─────────────────────────────────────────────────────────────────────
// interpolateOverlay
// ─────────────────────────────────────────────────────────────────────

describe('interpolateOverlay', () => {
  const overlay = [
    ep('2026-07-17T13:00:00Z', 100),
    ep('2026-07-17T13:10:00Z', 110),
    ep('2026-07-17T13:20:00Z', 120),
  ];

  it('returns null when overlay is empty', () => {
    expect(interpolateOverlay([], Date.now())).toBeNull();
  });

  it('returns null when cursor is before the overlay range (no extrapolation)', () => {
    const before = new Date('2026-07-17T12:59:00Z').getTime();
    expect(interpolateOverlay(overlay, before)).toBeNull();
  });

  it('returns null when cursor is past the overlay range', () => {
    const after = new Date('2026-07-17T13:20:01Z').getTime();
    expect(interpolateOverlay(overlay, after)).toBeNull();
  });

  it('returns the exact sample balance on an overlay-point hit', () => {
    expect(
      interpolateOverlay(overlay, new Date('2026-07-17T13:10:00Z').getTime()),
    ).toBe(110);
  });

  it('linearly interpolates between two adjacent samples', () => {
    // Halfway between 13:00 (100) and 13:10 (110) → 105.
    const half = new Date('2026-07-17T13:05:00Z').getTime();
    expect(interpolateOverlay(overlay, half)).toBeCloseTo(105, 6);
    // 30% of the way between 13:10 (110) and 13:20 (120) → 113.
    const p30 = new Date('2026-07-17T13:13:00Z').getTime();
    expect(interpolateOverlay(overlay, p30)).toBeCloseTo(113, 6);
  });

  it('returns the single-sample balance on a one-point overlay', () => {
    const one = [ep('2026-07-17T13:00:00Z', 42)];
    expect(
      interpolateOverlay(one, new Date('2026-07-17T13:00:00Z').getTime()),
    ).toBe(42);
  });
});

// ─────────────────────────────────────────────────────────────────────
// tradesUpTo
// ─────────────────────────────────────────────────────────────────────

describe('tradesUpTo', () => {
  const trades = [
    trade({ id: 'a', exit_timestamp: '2026-07-17T13:00:00Z', net_pnl: 5 }),
    trade({ id: 'b', exit_timestamp: '2026-07-17T14:00:00Z', net_pnl: -3 }),
    trade({ id: 'c', exit_timestamp: '2026-07-17T15:00:00Z', net_pnl: 8 }),
    trade({ id: 'd', exit_timestamp: null, net_pnl: null }), // open
  ];

  it('counts nothing when cutoff precedes every exit', () => {
    const cutoff = new Date('2026-07-17T12:00:00Z').getTime();
    expect(tradesUpTo(trades, cutoff)).toEqual({
      wins: 0,
      losses: 0,
      closed: 0,
    });
  });

  it('counts only trades exited at or before the cutoff', () => {
    // Cursor at 14:30 → a (13:00 W) + b (14:00 L) counted; c (15:00) and
    // d (open) excluded.
    const cutoff = new Date('2026-07-17T14:30:00Z').getTime();
    expect(tradesUpTo(trades, cutoff)).toEqual({
      wins: 1,
      losses: 1,
      closed: 2,
    });
  });

  it('includes trades that exit exactly at the cutoff', () => {
    const cutoff = new Date('2026-07-17T14:00:00Z').getTime();
    // 14:00 is inclusive — b counts.
    expect(tradesUpTo(trades, cutoff)).toEqual({
      wins: 1,
      losses: 1,
      closed: 2,
    });
  });

  it('never counts open trades regardless of cutoff', () => {
    const cutoff = new Date('2030-01-01T00:00:00Z').getTime();
    const stats = tradesUpTo(trades, cutoff);
    // All 3 closed + 0 open.
    expect(stats.closed).toBe(3);
    expect(stats.wins).toBe(2);
    expect(stats.losses).toBe(1);
  });

  it('treats net_pnl=0 as neither win nor loss', () => {
    const flat = [
      trade({ id: 'flat', exit_timestamp: '2026-07-17T13:00:00Z', net_pnl: 0 }),
    ];
    expect(tradesUpTo(flat, new Date('2026-07-17T14:00:00Z').getTime())).toEqual({
      wins: 0,
      losses: 0,
      closed: 1,
    });
  });

  it('falls back to pnl when net_pnl is null (legacy rows)', () => {
    const legacy = [
      trade({
        id: 'legacy',
        exit_timestamp: '2026-07-17T13:00:00Z',
        pnl: 12,
        net_pnl: null,
      }),
    ];
    expect(tradesUpTo(legacy, new Date('2026-07-17T14:00:00Z').getTime())).toEqual({
      wins: 1,
      losses: 0,
      closed: 1,
    });
  });
});

// ─────────────────────────────────────────────────────────────────────
// _clientXToTimestamp
// ─────────────────────────────────────────────────────────────────────

describe('_clientXToTimestamp', () => {
  const base = {
    rectLeft: 100, // container starts 100px into the viewport
    rectWidth: 900, // rendered at 1:1 with the viewBox
    runStartMs: 1_700_000_000_000,
    spanMs: 100 * MIN,
    paddingLeft: 60,
    plotWidth: 820,
    viewBoxWidth: 900,
  };

  it('returns runStartMs on a degenerate rectangle', () => {
    expect(
      _clientXToTimestamp({ ...base, rectWidth: 0, clientX: 250 }),
    ).toBe(base.runStartMs);
    expect(
      _clientXToTimestamp({ ...base, viewBoxWidth: 0, clientX: 250 }),
    ).toBe(base.runStartMs);
    expect(_clientXToTimestamp({ ...base, spanMs: 0, clientX: 250 })).toBe(
      base.runStartMs,
    );
  });

  it('clamps cursor to runStartMs when clientX is left of the plot', () => {
    // Cursor at clientX=110 → viewBox X=10, which is left of paddingLeft=60.
    // fracInPlot goes negative → clamped to 0.
    expect(_clientXToTimestamp({ ...base, clientX: 110 })).toBe(
      base.runStartMs,
    );
  });

  it('clamps cursor to runStartMs + spanMs when clientX is right of the plot', () => {
    // clientX=1100 → viewBox X=1000, well past paddingLeft+plotWidth=880.
    expect(_clientXToTimestamp({ ...base, clientX: 1100 })).toBe(
      base.runStartMs + base.spanMs,
    );
  });

  it('maps the plot midpoint to spanMs / 2', () => {
    // Plot rect spans viewBox X 60→880 (width 820).
    // Container starts at clientX=100, scale=1 → plot midpoint at
    // clientX = 100 + 60 + 410 = 570.
    const mid = _clientXToTimestamp({ ...base, clientX: 570 });
    expect(mid).toBe(base.runStartMs + base.spanMs / 2);
  });

  it('scales correctly when the container is rendered at half the viewBox width', () => {
    // Same viewBox but container rendered 450px wide → scale=0.5.
    // Plot rect in viewport pixels: from clientX=100+30=130 to
    // clientX=100+30+410=540 (paddingLeft 60 × 0.5, plotWidth 820 × 0.5).
    // Midpoint clientX = 100 + 30 + 205 = 335.
    const mid = _clientXToTimestamp({
      ...base,
      rectWidth: 450,
      clientX: 335,
    });
    expect(mid).toBe(base.runStartMs + base.spanMs / 2);
  });
});
