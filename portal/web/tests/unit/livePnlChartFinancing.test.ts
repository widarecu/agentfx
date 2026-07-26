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
  _isInFinancingWindow,
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
    expect(detectFinancingEvents([ep('2026-07-17T21:00:00Z', 100)], [])).toEqual(
      [],
    );
  });

  it('flat-balance series across the financing window → no events', () => {
    const series = [
      ep('2026-07-17T20:00:00Z', 100_000),
      ep('2026-07-17T21:00:00Z', 100_000),
      ep('2026-07-17T22:00:00Z', 100_000),
    ];
    expect(detectFinancingEvents(series, [])).toEqual([]);
  });

  it('balance drop at 21:00 UTC with no trades in the interval → financing event', () => {
    // OANDA's daily 21:00 UTC financing charge — no trades closing
    // nearby → residual is the full delta.
    const series = [
      ep('2026-07-17T20:59:00Z', 100_000),
      ep('2026-07-17T21:01:00Z', 99_983),
    ];
    const trades = [
      trade({ id: 'a', exit_timestamp: '2026-07-17T13:00:00Z', net_pnl: 5 }),
    ];
    const events = detectFinancingEvents(series, trades);
    expect(events).toHaveLength(1);
    expect(events[0].timestampMs).toBe(
      new Date('2026-07-17T21:01:00Z').getTime(),
    );
    expect(events[0].delta).toBeCloseTo(-17, 6);
  });

  it('balance drop INTRADAY (not near 21:00 UTC) → no financing event', () => {
    // Wilson UAT 2026-07-26 regression: a trade close at Jul 20
    // 00:01:09 that hit the balance while the runner was down and
    // only appeared in the equity series at 13:03 UTC — MUST NOT
    // be flagged as financing, because 13:03 is far from OANDA's
    // 21:00 UTC daily charge window. Under the old algorithm this
    // dropped -$81.16 into Wilson's cumulative "interest charged"
    // even though OANDA had charged $0 financing at that moment.
    const series = [
      ep('2026-07-19T23:14:00Z', 98_285.35),
      ep('2026-07-20T13:03:00Z', 98_138.82), // 13h gap, -$146.53 delta
    ];
    // Two real trade closes that happened during the equity gap.
    const trades = [
      trade({
        id: 'close-2314',
        exit_timestamp: '2026-07-19T23:14:01Z',
        net_pnl: -65.37,
      }),
      trade({
        id: 'close-0001',
        exit_timestamp: '2026-07-20T00:01:09Z',
        net_pnl: -81.16,
      }),
    ];
    // No financing event should be emitted — the delta is fully
    // explained by the sum of trade closes.
    expect(detectFinancingEvents(series, trades)).toEqual([]);
  });

  it('intraday balance movement WITHOUT any trade close → still no event (not financing time)', () => {
    // A mystery balance drop at 14:00 UTC (not near 21:00). Even
    // though no trade explains it, we do NOT emit an event — the
    // "Interest charged" total only counts real OANDA financing,
    // which always posts at ~21:00 UTC. Unexplained intraday
    // movement is a data-quality issue for a different report, not
    // something to bucket as interest.
    const series = [
      ep('2026-07-17T13:00:00Z', 100_000),
      ep('2026-07-17T14:00:00Z', 99_800),
    ];
    expect(detectFinancingEvents(series, [])).toEqual([]);
  });

  it('trade close AT 21:00 UTC alongside financing → residual is only the financing', () => {
    // Rare but real: a trade closes at 21:00 UTC on the same tick
    // as the daily financing charge. Balance moves by (pnl + fin);
    // the residual after subtracting the close is just the fin.
    const series = [
      ep('2026-07-17T20:58:00Z', 100_000),
      ep('2026-07-17T21:01:00Z', 100_040), // +$50 close + -$10 fin = +$40
    ];
    const trades = [
      trade({
        id: 'a',
        exit_timestamp: '2026-07-17T21:00:00Z',
        net_pnl: 50,
      }),
    ];
    const events = detectFinancingEvents(series, trades);
    expect(events).toHaveLength(1);
    expect(events[0].delta).toBeCloseTo(-10, 6);
  });

  it('ignores balance changes smaller than $0.10 (floating-point noise)', () => {
    const series = [
      ep('2026-07-17T20:59:00Z', 100_000),
      ep('2026-07-17T21:01:00Z', 100_000.05), // 5c change — noise
    ];
    expect(detectFinancingEvents(series, [])).toEqual([]);
  });

  it('multiple daily financing events across a week are all captured', () => {
    // 5 nights of overnight swap at 21:00 UTC; no trades ever.
    const series: EquityPoint[] = [];
    let bal = 100_000;
    for (let day = 0; day < 5; day++) {
      series.push({
        timestampMs: Date.UTC(2026, 6, 17 + day, 20, 59),
        balance: bal,
        unrealizedPnl: 0,
      });
      bal -= 17;
      series.push({
        timestampMs: Date.UTC(2026, 6, 17 + day, 21, 1),
        balance: bal,
        unrealizedPnl: 0,
      });
    }
    const events = detectFinancingEvents(series, []);
    expect(events).toHaveLength(5);
    for (const e of events) {
      expect(e.delta).toBeCloseTo(-17, 6);
    }
  });

  it('handles open trades correctly (no exit_timestamp to match against)', () => {
    // Balance drop at 21:00 with an open trade in flight.
    const series = [
      ep('2026-07-17T20:59:00Z', 100_000),
      ep('2026-07-17T21:01:00Z', 99_980),
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
    expect(events[0].delta).toBeCloseTo(-20, 6);
  });

  it('OANDA-truth reconciliation: 6 nights match to the cent', () => {
    // The exact sequence from Wilson's Jul 17–24 EUR_USD paper run,
    // reconciled against OANDA's DAILY_FINANCING transaction log.
    // OANDA charged/credited 6 times: -17.22, +2.98, +2.88, +8.61,
    // +2.89, +2.87 → total +3.01. This helper against the same
    // shape of samples must return the same set.
    const financing = [-17.22, 2.98, 2.88, 8.61, 2.89, 2.87];
    const dates = [
      '2026-07-17',
      '2026-07-20',
      '2026-07-21',
      '2026-07-22',
      '2026-07-23',
      '2026-07-24',
    ];
    const series: EquityPoint[] = [];
    let bal = 98_302.57;
    // Anchor sample at 20:00 UTC each day, financing post-charge at 21:01 UTC.
    dates.forEach((date, i) => {
      series.push({
        timestampMs: new Date(`${date}T20:00:00Z`).getTime(),
        balance: bal,
        unrealizedPnl: 0,
      });
      bal += financing[i];
      series.push({
        timestampMs: new Date(`${date}T21:01:00Z`).getTime(),
        balance: bal,
        unrealizedPnl: 0,
      });
    });
    const events = detectFinancingEvents(series, []);
    expect(events).toHaveLength(6);
    const total = events.reduce((s, e) => s + e.delta, 0);
    expect(total).toBeCloseTo(3.01, 2);
  });
});

describe('_isInFinancingWindow', () => {
  const check = (iso: string) =>
    _isInFinancingWindow(new Date(iso).getTime());

  it('accepts 21:00 UTC on the nose', () => {
    expect(check('2026-07-17T21:00:00Z')).toBe(true);
  });

  it('accepts through 21:20 UTC (upper bound)', () => {
    expect(check('2026-07-17T21:20:00Z')).toBe(true);
    expect(check('2026-07-17T21:21:00Z')).toBe(false);
  });

  it('accepts back to 20:40 UTC (lower bound)', () => {
    expect(check('2026-07-17T20:40:00Z')).toBe(true);
    expect(check('2026-07-17T20:39:00Z')).toBe(false);
  });

  it('rejects other times of day', () => {
    expect(check('2026-07-17T00:00:00Z')).toBe(false);
    expect(check('2026-07-17T12:00:00Z')).toBe(false);
    expect(check('2026-07-17T13:03:00Z')).toBe(false);
    expect(check('2026-07-17T22:00:00Z')).toBe(false);
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
