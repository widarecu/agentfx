/**
 * Vitest coverage for `computeSummaryStrip` — the 8-tile computation
 * that sits above the redesigned LivePnLChart. Locks:
 *
 * - Realised P&L reads from `net_pnl` first (financing + commission
 *   already netted), falls back to `pnl` for legacy rows.
 * - Only closed trades count toward realised / win rate.
 * - Open trades count in `tradeCount` and `openCount`.
 * - Best / worst trade selection.
 * - Max drawdown is negative-or-zero and its window brackets the
 *   peak → trough interval.
 */

import { describe, expect, it } from 'vitest';
import type { PaperTradePublic } from '../../src/types/paper';
import {
  computeSummaryStrip,
  type EquityPoint,
} from '../../src/utils/livePnlChart';

function point(ts: string, balance: number, unrealizedPnl = 0): EquityPoint {
  return { timestampMs: new Date(ts).getTime(), balance, unrealizedPnl };
}

function closedTrade(overrides: Partial<PaperTradePublic>): PaperTradePublic {
  return {
    id: overrides.id ?? 'trade-x',
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

function openTrade(overrides: Partial<PaperTradePublic> = {}): PaperTradePublic {
  return closedTrade({
    id: 'trade-open',
    exit_timestamp: null,
    exit_price: null,
    pnl: null,
    pnl_pct: null,
    net_pnl: null,
    ...overrides,
  });
}

describe('computeSummaryStrip', () => {
  it('empty inputs collapse to snapshot-only defaults', () => {
    const s = computeSummaryStrip([], [], 100_000);
    expect(s.liveBalance).toBe(100_000);
    expect(s.realisedPnl).toBe(0);
    expect(s.unrealisedPnl).toBe(0);
    expect(s.maxDrawdownPct).toBe(0);
    expect(s.maxDrawdownWindow).toBeNull();
    expect(s.tradeCount).toBe(0);
    expect(s.closedCount).toBe(0);
    expect(s.openCount).toBe(0);
    expect(s.winRate).toBeNull();
    expect(s.wins).toBe(0);
    expect(s.losses).toBe(0);
    expect(s.bestTrade).toBeNull();
    expect(s.worstTrade).toBeNull();
  });

  it('reads liveBalance + unrealisedPnl from the latest equity sample', () => {
    const s = computeSummaryStrip(
      [
        point('2026-07-17T13:00:00Z', 100_000, 0),
        point('2026-07-17T13:05:00Z', 100_150, 42.5),
      ],
      [],
      100_000,
    );
    expect(s.liveBalance).toBe(100_150);
    expect(s.unrealisedPnl).toBe(42.5);
  });

  it('sums realised P&L across closed trades using net_pnl first', () => {
    const trades = [
      closedTrade({ id: '1', pnl: 20, net_pnl: 18 }),
      closedTrade({ id: '2', pnl: -5, net_pnl: -7 }),
      // Legacy row with net_pnl missing → falls back to pnl.
      closedTrade({ id: '3', pnl: 10, net_pnl: null as unknown as number }),
    ];
    const s = computeSummaryStrip([], trades, 100_000);
    // 18 + (-7) + 10 = 21
    expect(s.realisedPnl).toBe(21);
    expect(s.closedCount).toBe(3);
  });

  it('excludes open trades from realised / win rate', () => {
    const trades = [
      closedTrade({ id: 'w', net_pnl: 30 }),
      closedTrade({ id: 'l', net_pnl: -10 }),
      openTrade(),
    ];
    const s = computeSummaryStrip([], trades, 100_000);
    expect(s.tradeCount).toBe(3);
    expect(s.closedCount).toBe(2);
    expect(s.openCount).toBe(1);
    expect(s.realisedPnl).toBe(20);
    expect(s.winRate).toBe(0.5);
    expect(s.wins).toBe(1);
    expect(s.losses).toBe(1);
  });

  it('picks best and worst trades by net P&L', () => {
    const trades = [
      closedTrade({ id: 'a', net_pnl: 12 }),
      closedTrade({ id: 'b', net_pnl: 54 }), // best
      closedTrade({ id: 'c', net_pnl: -28 }), // worst
      closedTrade({ id: 'd', net_pnl: 3 }),
      openTrade(), // ignored
    ];
    const s = computeSummaryStrip([], trades, 100_000);
    expect(s.bestTrade?.id).toBe('b');
    expect(s.worstTrade?.id).toBe('c');
  });

  it('winRate is null when there are zero closed trades', () => {
    const s = computeSummaryStrip([], [openTrade()], 100_000);
    expect(s.winRate).toBeNull();
  });

  it('max drawdown is 0 on a monotonically rising curve', () => {
    const s = computeSummaryStrip(
      [
        point('2026-07-17T13:00:00Z', 100_000),
        point('2026-07-17T14:00:00Z', 100_100),
        point('2026-07-17T15:00:00Z', 100_200),
      ],
      [],
      100_000,
    );
    expect(s.maxDrawdownPct).toBe(0);
    expect(s.maxDrawdownWindow).toBeNull();
  });

  it('max drawdown captures the deepest peak-to-trough dip', () => {
    // Peak at $101,000 (14:00), trough at $99,000 (16:00) → -1.98%
    const s = computeSummaryStrip(
      [
        point('2026-07-17T13:00:00Z', 100_000),
        point('2026-07-17T14:00:00Z', 101_000), // peak
        point('2026-07-17T15:00:00Z', 100_500),
        point('2026-07-17T16:00:00Z', 99_000), // trough
        point('2026-07-17T17:00:00Z', 100_200), // recovery
      ],
      [],
      100_000,
    );
    expect(s.maxDrawdownPct).toBeCloseTo(-(2000 / 101_000) * 100, 4);
    expect(s.maxDrawdownWindow?.startMs).toBe(
      new Date('2026-07-17T14:00:00Z').getTime(),
    );
    expect(s.maxDrawdownWindow?.endMs).toBe(
      new Date('2026-07-17T16:00:00Z').getTime(),
    );
  });

  it('treats the snapshot balance as the initial peak reference', () => {
    // Balance never rises above snapshot; every dip below the snapshot
    // is a drawdown from `snapshot` (not from the highest observed
    // sample, which would understate DD for perpetually-underwater runs).
    const s = computeSummaryStrip(
      [
        point('2026-07-17T13:00:00Z', 99_800),
        point('2026-07-17T14:00:00Z', 99_500),
      ],
      [],
      100_000,
    );
    expect(s.maxDrawdownPct).toBeCloseTo(-0.5, 4);
    expect(s.maxDrawdownWindow?.startMs).toBeLessThanOrEqual(
      new Date('2026-07-17T13:00:00Z').getTime(),
    );
  });
});
