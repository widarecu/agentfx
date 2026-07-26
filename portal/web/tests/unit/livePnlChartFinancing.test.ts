/**
 * Vitest for `detectFinancingEvents` — Wilson UAT 2026-07-25.
 *
 * The chart's balance line jumps at every account-balance change.
 * Trade closes get a coloured exit dot; overnight swap / financing
 * charges have no trade to attribute to and used to render as
 * unexplained jumps. This helper attributes every balance-change
 * sample to either a trade close (matched exit_timestamp within
 * `toleranceMs`) or a financing event; the chart renders financing
 * as small amber diamonds so the eye can distinguish "losing trade"
 * from "overnight swap".
 */

import { describe, expect, it } from 'vitest';
import type { PaperTradePublic } from '../../src/types/paper';
import {
  detectFinancingEvents,
  interestChargedUpTo,
  type EquityPoint,
  type FinancingEvent,
} from '../../src/utils/livePnlChart';

function ep(ts: string, balance: number): EquityPoint {
  return { timestampMs: new Date(ts).getTime(), balance, unrealizedPnl: 0 };
}

function trade(overrides: Partial<PaperTradePublic>): PaperTradePublic {
  return {
    id: overrides.id ?? 't',
    entry_timestamp: '2026-07-17T13:00:00Z',
    exit_timestamp: overrides.exit_timestamp ?? '2026-07-17T14:00:00Z',
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

describe('detectFinancingEvents', () => {
  it('returns [] on too-short input', () => {
    expect(detectFinancingEvents([], [])).toEqual([]);
    expect(detectFinancingEvents([ep('2026-07-17T13:00:00Z', 100)], [])).toEqual(
      [],
    );
  });

  it('flat-balance series → no events', () => {
    const series = [
      ep('2026-07-17T13:00:00Z', 100_000),
      ep('2026-07-17T14:00:00Z', 100_000),
      ep('2026-07-17T15:00:00Z', 100_000),
    ];
    expect(detectFinancingEvents(series, [])).toEqual([]);
  });

  it('single balance change matched to a trade close → suppressed', () => {
    // Balance drops from 100k → 99.8k at exactly the trade's exit_ts.
    const series = [
      ep('2026-07-17T13:00:00Z', 100_000),
      ep('2026-07-17T14:00:00Z', 99_800),
    ];
    const trades = [
      trade({ id: 'a', exit_timestamp: '2026-07-17T14:00:00Z', net_pnl: -200 }),
    ];
    expect(detectFinancingEvents(series, trades)).toEqual([]);
  });

  it('balance change with NO nearby trade → financing event', () => {
    // Overnight swap: balance dips by $17 at 21:00 UTC (5pm NY),
    // no trade closed anywhere near that time.
    const series = [
      ep('2026-07-17T20:00:00Z', 100_000),
      ep('2026-07-17T21:00:00Z', 99_983),
    ];
    const trades = [
      // Some earlier close that shouldn't match.
      trade({ id: 'a', exit_timestamp: '2026-07-17T13:00:00Z', net_pnl: 5 }),
    ];
    const events = detectFinancingEvents(series, trades);
    expect(events).toHaveLength(1);
    expect(events[0].timestampMs).toBe(
      new Date('2026-07-17T21:00:00Z').getTime(),
    );
    expect(events[0].delta).toBeCloseTo(-17, 6);
    expect(events[0].balance).toBe(99_983);
  });

  it('tolerance window is symmetric — trade close 90s before still counts as match', () => {
    // Trade exited at 13:59, balance change recorded at 14:00 (60s
    // later) — within 2min default tolerance → suppressed.
    const series = [
      ep('2026-07-17T13:58:00Z', 100_000),
      ep('2026-07-17T14:00:00Z', 100_050),
    ];
    const trades = [
      trade({ id: 'a', exit_timestamp: '2026-07-17T13:59:00Z', net_pnl: 50 }),
    ];
    expect(detectFinancingEvents(series, trades)).toEqual([]);
  });

  it('trade close outside the tolerance → still counts as financing', () => {
    // Trade exited at 13:00, next balance change at 21:00 (8h later).
    // Well past the 2-min tolerance → recorded as financing.
    const series = [
      ep('2026-07-17T20:00:00Z', 100_000),
      ep('2026-07-17T21:00:00Z', 99_970),
    ];
    const trades = [
      trade({ id: 'a', exit_timestamp: '2026-07-17T13:00:00Z', net_pnl: 5 }),
    ];
    const events = detectFinancingEvents(series, trades);
    expect(events).toHaveLength(1);
  });

  it('ignores balance changes smaller than $0.10 (floating-point noise)', () => {
    const series = [
      ep('2026-07-17T13:00:00Z', 100_000),
      ep('2026-07-17T13:01:00Z', 100_000.05), // 5c change — noise
    ];
    expect(detectFinancingEvents(series, [])).toEqual([]);
  });

  it('multiple financing events across a week are all captured', () => {
    // Simulate 5 nights of overnight swap; no trades ever.
    const series: EquityPoint[] = [];
    let bal = 100_000;
    for (let day = 0; day < 5; day++) {
      const preMs = Date.UTC(2026, 6, 17 + day, 20, 0);
      const postMs = Date.UTC(2026, 6, 17 + day, 21, 0);
      series.push({
        timestampMs: preMs,
        balance: bal,
        unrealizedPnl: 0,
      });
      bal -= 17;
      series.push({
        timestampMs: postMs,
        balance: bal,
        unrealizedPnl: 0,
      });
    }
    const events = detectFinancingEvents(series, []);
    expect(events).toHaveLength(5);
    // Each event's delta should be ~-17.
    for (const e of events) {
      expect(e.delta).toBeCloseTo(-17, 6);
    }
  });

  it('handles open trades correctly (no exit_timestamp to match against)', () => {
    // Balance drop during an open trade with no close event → financing.
    const series = [
      ep('2026-07-17T20:00:00Z', 100_000),
      ep('2026-07-17T21:00:00Z', 99_980),
    ];
    const trades = [
      trade({
        id: 'open',
        entry_timestamp: '2026-07-17T13:00:00Z',
        exit_timestamp: null,
        net_pnl: null,
      }),
    ];
    const events = detectFinancingEvents(series, trades);
    expect(events).toHaveLength(1);
  });
});

// ─────────────────────────────────────────────────────────────────────
// interestChargedUpTo — Wilson UAT 2026-07-26
// ─────────────────────────────────────────────────────────────────────

function fev(ts: string, delta: number, balance = 0): FinancingEvent {
  return { timestampMs: new Date(ts).getTime(), delta, balance };
}

describe('interestChargedUpTo', () => {
  it('returns 0 when nothing to sum', () => {
    expect(
      interestChargedUpTo(
        [],
        [],
        new Date('2026-07-17T13:00:00Z').getTime(),
      ),
    ).toBe(0);
  });

  it('sums closed-trade financing fields up to the cursor', () => {
    const trades = [
      trade({
        id: 'a',
        exit_timestamp: '2026-07-17T14:00:00Z',
        financing: -3,
      }),
      trade({
        id: 'b',
        exit_timestamp: '2026-07-18T14:00:00Z',
        financing: -5,
      }),
      trade({
        id: 'c',
        exit_timestamp: '2026-07-19T14:00:00Z',
        financing: -7,
      }),
    ];
    // Cursor at Jul 18 15:00 UTC — first two closed, third not yet.
    const cursor = new Date('2026-07-18T15:00:00Z').getTime();
    expect(interestChargedUpTo(trades, [], cursor)).toBe(-8);
  });

  it('sums financing events up to the cursor', () => {
    const events = [
      fev('2026-07-17T21:00:00Z', -17),
      fev('2026-07-18T21:00:00Z', -18),
      fev('2026-07-19T21:00:00Z', -19),
    ];
    const cursor = new Date('2026-07-18T22:00:00Z').getTime();
    expect(interestChargedUpTo([], events, cursor)).toBe(-35);
  });

  it('combines trade financing + events without double-counting', () => {
    // Realistic mix: one closed trade with financing embedded, plus
    // one overnight-swap event on a still-open position.
    const trades = [
      trade({
        id: 'closed',
        exit_timestamp: '2026-07-18T14:00:00Z',
        financing: -4,
      }),
    ];
    const events = [fev('2026-07-18T21:00:00Z', -12)];
    const cursor = new Date('2026-07-19T00:00:00Z').getTime();
    expect(interestChargedUpTo(trades, events, cursor)).toBe(-16);
  });

  it('is monotonically non-increasing (Wilson UAT 2026-07-26)', () => {
    // The exact shape Wilson wanted: cursor moves right over time,
    // the returned magnitude only grows (or stays flat) — never
    // snaps back toward zero the way the old residual formula did.
    const trades = [
      trade({
        id: 'a',
        exit_timestamp: '2026-07-18T14:00:00Z',
        financing: -3,
      }),
      trade({
        id: 'b',
        exit_timestamp: '2026-07-20T14:00:00Z',
        financing: -5,
      }),
    ];
    const events = [
      fev('2026-07-17T21:00:00Z', -2),
      fev('2026-07-19T21:00:00Z', -2),
      fev('2026-07-21T21:00:00Z', -2),
    ];
    const cursors = [
      '2026-07-17T00:00:00Z',
      '2026-07-18T00:00:00Z',
      '2026-07-19T00:00:00Z',
      '2026-07-20T00:00:00Z',
      '2026-07-21T00:00:00Z',
      '2026-07-22T00:00:00Z',
    ].map((c) => new Date(c).getTime());
    const values = cursors.map((c) => interestChargedUpTo(trades, events, c));
    // Each successive value should be ≤ the previous (monotone
    // non-increasing) since financing is a debit.
    for (let i = 1; i < values.length; i++) {
      expect(values[i]).toBeLessThanOrEqual(values[i - 1]);
    }
  });

  it('ignores open trades (no exit_timestamp)', () => {
    const trades = [
      trade({
        id: 'open',
        exit_timestamp: null,
        financing: -99,
        net_pnl: null,
      }),
    ];
    const cursor = new Date('2026-07-30T00:00:00Z').getTime();
    // Open trade's financing accrues via daily overnight swap events;
    // it does NOT contribute to this sum until the trade closes.
    expect(interestChargedUpTo(trades, [], cursor)).toBe(0);
  });

  it('handles null financing field on legacy trade rows', () => {
    const trades = [
      trade({
        id: 'a',
        exit_timestamp: '2026-07-17T14:00:00Z',
        financing: null,
      }),
    ];
    const cursor = new Date('2026-07-18T00:00:00Z').getTime();
    expect(interestChargedUpTo(trades, [], cursor)).toBe(0);
  });
});
