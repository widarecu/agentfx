/**
 * Vitest for `computeStreaks` — Wilson UAT 2026-07-25 addition to
 * the LivePnLChart Trades tile. Verifies:
 * - current streak reads the LAST closed trade's kind + running count
 * - max streaks accumulate across the whole run
 * - flat (net_pnl=0) trades break both win + loss streaks
 * - net_pnl wins over pnl (financing / commission flip case)
 * - trades are re-sorted internally so unsorted callers still work
 */

import { describe, expect, it } from 'vitest';
import type { PaperTradePublic } from '../../src/types/paper';
import { computeStreaks } from '../../src/utils/livePnlChart';

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

describe('computeStreaks', () => {
  it('empty input → all zero + null current', () => {
    const s = computeStreaks([]);
    expect(s.current).toBeNull();
    expect(s.maxWinStreak).toBe(0);
    expect(s.maxLossStreak).toBe(0);
  });

  it('ignores open trades entirely', () => {
    const s = computeStreaks([
      trade({ id: 'open1', exit_timestamp: null, net_pnl: null }),
      trade({ id: 'open2', exit_timestamp: null, net_pnl: null }),
    ]);
    expect(s.current).toBeNull();
  });

  it('single winning trade → current 1W, max 1W', () => {
    const s = computeStreaks([trade({ net_pnl: 10 })]);
    expect(s.current).toEqual({ kind: 'win', count: 1 });
    expect(s.maxWinStreak).toBe(1);
    expect(s.maxLossStreak).toBe(0);
  });

  it('three consecutive wins → current 3W, max 3W', () => {
    const s = computeStreaks([
      trade({ id: 'a', exit_timestamp: '2026-07-17T13:00:00Z', net_pnl: 5 }),
      trade({ id: 'b', exit_timestamp: '2026-07-17T14:00:00Z', net_pnl: 3 }),
      trade({ id: 'c', exit_timestamp: '2026-07-17T15:00:00Z', net_pnl: 8 }),
    ]);
    expect(s.current).toEqual({ kind: 'win', count: 3 });
    expect(s.maxWinStreak).toBe(3);
    expect(s.maxLossStreak).toBe(0);
  });

  it('W W L L L → current 3L, max is 3L on loss side, 2W on win side', () => {
    const s = computeStreaks([
      trade({ id: 'a', exit_timestamp: '2026-07-17T13:00:00Z', net_pnl: 5 }),
      trade({ id: 'b', exit_timestamp: '2026-07-17T14:00:00Z', net_pnl: 3 }),
      trade({ id: 'c', exit_timestamp: '2026-07-17T15:00:00Z', net_pnl: -2 }),
      trade({ id: 'd', exit_timestamp: '2026-07-17T16:00:00Z', net_pnl: -4 }),
      trade({ id: 'e', exit_timestamp: '2026-07-17T17:00:00Z', net_pnl: -6 }),
    ]);
    expect(s.current).toEqual({ kind: 'loss', count: 3 });
    expect(s.maxWinStreak).toBe(2);
    expect(s.maxLossStreak).toBe(3);
  });

  it('W W L W W W → current 3W, max 3W (rebound), max loss 1', () => {
    const s = computeStreaks([
      trade({ id: 'a', exit_timestamp: '2026-07-17T13:00:00Z', net_pnl: 5 }),
      trade({ id: 'b', exit_timestamp: '2026-07-17T14:00:00Z', net_pnl: 3 }),
      trade({ id: 'c', exit_timestamp: '2026-07-17T15:00:00Z', net_pnl: -1 }),
      trade({ id: 'd', exit_timestamp: '2026-07-17T16:00:00Z', net_pnl: 4 }),
      trade({ id: 'e', exit_timestamp: '2026-07-17T17:00:00Z', net_pnl: 2 }),
      trade({ id: 'f', exit_timestamp: '2026-07-17T18:00:00Z', net_pnl: 6 }),
    ]);
    expect(s.current).toEqual({ kind: 'win', count: 3 });
    expect(s.maxWinStreak).toBe(3);
    expect(s.maxLossStreak).toBe(1);
  });

  it('flat trade breaks both win and loss streaks', () => {
    const s = computeStreaks([
      trade({ id: 'a', exit_timestamp: '2026-07-17T13:00:00Z', net_pnl: 5 }),
      trade({ id: 'b', exit_timestamp: '2026-07-17T14:00:00Z', net_pnl: 3 }),
      trade({ id: 'c', exit_timestamp: '2026-07-17T15:00:00Z', net_pnl: 0 }),
      trade({ id: 'd', exit_timestamp: '2026-07-17T16:00:00Z', net_pnl: 4 }),
    ]);
    // 2W streak, then flat resets to 1 flat, then 1W. Current = 1W.
    expect(s.current).toEqual({ kind: 'win', count: 1 });
    expect(s.maxWinStreak).toBe(2);
    expect(s.maxLossStreak).toBe(0);
  });

  it('prefers net_pnl over pnl (financing flip case)', () => {
    // pnl says +2 win but net_pnl says -1 (financing eats 3) → loss.
    const s = computeStreaks([
      trade({ id: 'flip', exit_timestamp: '2026-07-17T13:00:00Z', pnl: 2, net_pnl: -1 }),
    ]);
    expect(s.current).toEqual({ kind: 'loss', count: 1 });
  });

  it('falls back to pnl when net_pnl is null (legacy rows)', () => {
    const s = computeStreaks([
      trade({
        id: 'legacy',
        exit_timestamp: '2026-07-17T13:00:00Z',
        pnl: 10,
        net_pnl: null,
      }),
    ]);
    expect(s.current).toEqual({ kind: 'win', count: 1 });
  });

  it('re-sorts internally so out-of-order input still resolves current correctly', () => {
    // Passed in newest-first (as `PaperRunTradesTable` renders).
    // `current` must reflect the CHRONOLOGICALLY last trade.
    const s = computeStreaks([
      trade({ id: 'newest', exit_timestamp: '2026-07-17T17:00:00Z', net_pnl: -5 }),
      trade({ id: 'mid', exit_timestamp: '2026-07-17T14:00:00Z', net_pnl: 3 }),
      trade({ id: 'oldest', exit_timestamp: '2026-07-17T13:00:00Z', net_pnl: 8 }),
    ]);
    // Chronological: 8W, 3W, -5L → current = 1L.
    expect(s.current).toEqual({ kind: 'loss', count: 1 });
    expect(s.maxWinStreak).toBe(2);
    expect(s.maxLossStreak).toBe(1);
  });
});
