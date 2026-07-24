/**
 * Vitest for `filterByRange` — PR 4 of the LivePnLChart redesign.
 * Verifies window semantics for each `RangeKey`, trade inclusion
 * (straddlers stay visible), and degenerate-input safety.
 */

import { describe, expect, it } from 'vitest';
import type { PaperTradePublic } from '../../src/types/paper';
import {
  filterByRange,
  type EquityPoint,
  type RangeKey,
} from '../../src/utils/livePnlChart';

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

function ep(ts: string, balance: number): EquityPoint {
  return { timestampMs: new Date(ts).getTime(), balance, unrealizedPnl: 0 };
}

function tr(
  entryTs: string,
  exitTs: string | null,
  id = 't',
): PaperTradePublic {
  return {
    id,
    entry_timestamp: entryTs,
    exit_timestamp: exitTs,
    side: 'buy',
    quantity: 10_000,
    entry_price: 1.1,
    exit_price: exitTs ? 1.1005 : null,
    pnl: exitTs ? 5 : null,
    pnl_pct: exitTs ? 0.05 : null,
    origin: 'live',
    signal_reason: 'sma > sma',
    exit_reason: exitTs ? 'target' : null,
    financing: 0,
    commission: 0,
    net_pnl: exitTs ? 5 : null,
  };
}

// Standard fixture: a 7-day-old run with samples every 6h and a
// handful of trades scattered across the timeline.
const runStartMs = new Date('2026-07-17T12:00:00Z').getTime();
const nowMs = new Date('2026-07-24T12:00:00Z').getTime(); // exactly 7 days later
const equity: EquityPoint[] = [];
for (let t = runStartMs; t <= nowMs; t += 6 * HOUR) {
  equity.push(ep(new Date(t).toISOString(), 100_000 + (t - runStartMs) / HOUR));
}
const trades: PaperTradePublic[] = [
  tr('2026-07-17T14:00:00Z', '2026-07-17T15:00:00Z', 'day1-early'),
  tr('2026-07-19T09:00:00Z', '2026-07-19T11:00:00Z', 'day3'),
  tr('2026-07-23T20:00:00Z', '2026-07-23T23:00:00Z', 'yesterday-late'),
  tr('2026-07-24T10:00:00Z', null, 'open'), // still open
];

describe('filterByRange', () => {
  it('rangeKey=24h → window is [nowMs - 24h, nowMs]', () => {
    const r = filterByRange({
      rangeKey: '24h',
      runStartMs,
      nowMs,
      equity,
      trades,
    });
    expect(r.startMs).toBe(nowMs - DAY);
    expect(r.endMs).toBe(nowMs);
    // Only the last 24h of samples included.
    expect(r.equity.every((p) => p.timestampMs >= r.startMs)).toBe(true);
    expect(r.equity[0].timestampMs).toBeGreaterThanOrEqual(nowMs - DAY);
    // 'yesterday-late' (exit 23:00 previous day, entry 20:00 previous
    // day) touches the 24h window → included.
    expect(r.trades.map((t) => t.id)).toContain('yesterday-late');
    // 'open' entry inside 24h → included.
    expect(r.trades.map((t) => t.id)).toContain('open');
    // Day-3 trade is 5 days ago → excluded.
    expect(r.trades.map((t) => t.id)).not.toContain('day3');
    expect(r.trades.map((t) => t.id)).not.toContain('day1-early');
  });

  it('rangeKey=7d → window covers roughly the whole run', () => {
    const r = filterByRange({
      rangeKey: '7d',
      runStartMs,
      nowMs,
      equity,
      trades,
    });
    expect(r.startMs).toBe(nowMs - 7 * DAY);
    // Since the run is exactly 7d old, the window aligns with run start.
    expect(r.startMs).toBe(runStartMs);
    expect(r.trades).toHaveLength(trades.length); // all included
  });

  it('rangeKey=all → window is [runStartMs, nowMs]', () => {
    const r = filterByRange({
      rangeKey: 'all',
      runStartMs,
      nowMs,
      equity,
      trades,
    });
    expect(r.startMs).toBe(runStartMs);
    expect(r.endMs).toBe(nowMs);
    expect(r.equity).toHaveLength(equity.length);
    expect(r.trades).toHaveLength(trades.length);
  });

  it('rangeKey=7d clamps window start to runStartMs for a young run', () => {
    // Only 2 days into the run → 7d window should collapse to 2d.
    const youngNow = runStartMs + 2 * DAY;
    const r = filterByRange({
      rangeKey: '7d',
      runStartMs,
      nowMs: youngNow,
      equity: equity.filter((p) => p.timestampMs <= youngNow),
      trades: [tr('2026-07-17T14:00:00Z', '2026-07-17T15:00:00Z', 'inside')],
    });
    expect(r.startMs).toBe(runStartMs);
    expect(r.endMs).toBe(youngNow);
  });

  it('includes a trade that straddles the window boundary', () => {
    // Entry BEFORE window start, exit AFTER window start → still shown.
    const straddling = tr(
      '2026-07-23T00:00:00Z', // entry 36h before now
      '2026-07-24T02:00:00Z', // exit inside 24h window
      'straddle',
    );
    const r = filterByRange({
      rangeKey: '24h',
      runStartMs,
      nowMs,
      equity,
      trades: [straddling],
    });
    expect(r.trades.map((t) => t.id)).toContain('straddle');
  });

  it('drops a trade whose ENTRY is past the window end', () => {
    // Contrived — a trade that hasn't happened yet (data corruption
    // or clock skew). Must not appear in any window.
    const future = tr(
      '2027-01-01T00:00:00Z',
      '2027-01-02T00:00:00Z',
      'future',
    );
    const r = filterByRange({
      rangeKey: 'all',
      runStartMs,
      nowMs,
      equity,
      trades: [future],
    });
    expect(r.trades).toEqual([]);
  });

  it('always includes open trades whose entry is inside the window', () => {
    for (const rk of ['24h', '7d', 'all'] as RangeKey[]) {
      const r = filterByRange({
        rangeKey: rk,
        runStartMs,
        nowMs,
        equity,
        trades: [tr('2026-07-24T10:00:00Z', null, 'op')],
      });
      expect(r.trades.some((t) => t.id === 'op')).toBe(true);
    }
  });

  it('never returns endMs <= startMs (60s minimum window)', () => {
    // nowMs equal to runStartMs — degenerate but possible on a
    // brand-new run.
    const r = filterByRange({
      rangeKey: 'all',
      runStartMs,
      nowMs: runStartMs,
      equity: [],
      trades: [],
    });
    expect(r.endMs - r.startMs).toBeGreaterThanOrEqual(60_000);
  });
});
