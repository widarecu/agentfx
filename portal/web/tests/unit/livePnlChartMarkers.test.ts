/**
 * Vitest for `projectTrade` — PR 3 of the LivePnLChart redesign.
 * Verifies pixel-space projection of entry / exit markers, outcome
 * classification, and the "still open" case.
 */

import { describe, expect, it } from 'vitest';
import type { PaperTradePublic } from '../../src/types/paper';
import {
  projectTrade,
  type EquityPoint,
} from '../../src/utils/livePnlChart';

const HOUR = 60 * 60 * 1000;

function ep(ts: string, balance: number): EquityPoint {
  return { timestampMs: new Date(ts).getTime(), balance, unrealizedPnl: 0 };
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

// Test scaleX/scaleY: convert timestamps + balances to pixels
// deterministically so the projection maths is verifiable.
// - Timeline: 2026-07-17T13:00 → x=100; 2026-07-17T15:00 → x=300 (100 px per hour).
// - Balance: 100 → y=200; 110 → y=100 (10 px per unit).
const T0 = new Date('2026-07-17T13:00:00Z').getTime();
const scaleX = (ms: number) => 100 + ((ms - T0) / HOUR) * 100;
const scaleY = (b: number) => 200 - (b - 100) * 10;

describe('projectTrade', () => {
  const series = [
    ep('2026-07-17T13:00:00Z', 100),
    ep('2026-07-17T14:00:00Z', 105), // exit-time sample
    ep('2026-07-17T15:00:00Z', 108),
  ];

  it('projects a LONG closed win to correct entry + exit pixels', () => {
    const t = trade({ id: 'w-long', side: 'buy', net_pnl: 5 });
    const p = projectTrade(t, series, scaleX, scaleY);
    expect(p.tradeId).toBe('w-long');
    expect(p.side).toBe('buy');
    // Entry at T0 → x=100.
    expect(p.entryX).toBe(100);
    expect(p.entryTsMs).toBe(T0);
    // Exit at T0+1h → x=200; balance at exit=105 → y=150.
    expect(p.exit).not.toBeNull();
    expect(p.exit!.x).toBe(200);
    expect(p.exit!.y).toBe(150);
    expect(p.exit!.balance).toBe(105);
    expect(p.outcome).toBe('win');
  });

  it('projects a SHORT closed loss with red outcome', () => {
    const t = trade({ id: 'l-short', side: 'sell', net_pnl: -8 });
    const p = projectTrade(t, series, scaleX, scaleY);
    expect(p.side).toBe('sell');
    expect(p.outcome).toBe('loss');
  });

  it('classifies a flat closed trade as "flat"', () => {
    const t = trade({ id: 'flat', net_pnl: 0, pnl: 0 });
    const p = projectTrade(t, series, scaleX, scaleY);
    expect(p.outcome).toBe('flat');
  });

  it('leaves exit=null and outcome=null for an OPEN trade', () => {
    const t = trade({
      id: 'open',
      exit_timestamp: null,
      exit_price: null,
      pnl: null,
      net_pnl: null,
    });
    const p = projectTrade(t, series, scaleX, scaleY);
    expect(p.exit).toBeNull();
    expect(p.outcome).toBeNull();
    // Entry pixel still projected so the ▲/▼ triangle renders.
    expect(p.entryX).toBe(100);
  });

  it('snaps the exit circle to the nearest equity sample at the exit time', () => {
    // Exit at T0+1h30 (13:00 + 90 min). Nearest sample is T0+1h (105)
    // — 30 min away — vs T0+2h (108) — 30 min away. Tie-break in
    // `nearestSample` favours the earlier (<=), so we should see
    // balance=105.
    const t = trade({
      id: 'snap',
      exit_timestamp: '2026-07-17T14:30:00Z',
      net_pnl: 3,
    });
    const p = projectTrade(t, series, scaleX, scaleY);
    expect(p.exit!.balance).toBe(105);
    expect(p.exit!.y).toBe(150);
    // Exit x still lands at the actual exit ts, not the snapped sample.
    // T0 + 1.5h → x = 100 + 1.5*100 = 250.
    expect(p.exit!.x).toBe(250);
  });

  it('falls back to net_pnl when pnl differs (financing subtraction)', () => {
    // pnl=+2 but net_pnl=-1 (financing eat 3) → outcome must be loss.
    const t = trade({ id: 'net-flip', pnl: 2, net_pnl: -1 });
    const p = projectTrade(t, series, scaleX, scaleY);
    expect(p.outcome).toBe('loss');
  });

  it('falls back to pnl when net_pnl is null (legacy row)', () => {
    const t = trade({ id: 'legacy', pnl: 4, net_pnl: null });
    const p = projectTrade(t, series, scaleX, scaleY);
    expect(p.outcome).toBe('win');
  });
});
