/**
 * Vitest for pure helpers in MobilePaperRunDetail.tsx (v0.7.0 mobile view).
 *
 * The rendering is exercised by manual testing at 375px. This file
 * pins the parts most likely to regress:
 *   - pipSizeFor (JPY vs non-JPY)
 *   - _computeScaleInner direction flip (LONG vs SHORT layout)
 *   - buildActivity ordering + noisy-decision filter
 */

import { describe, expect, it } from 'vitest';
import {
  _computeScaleInner,
  buildActivity,
  pipSizeFor,
} from '../../src/pages/MobilePaperRunDetail';
import type {
  PaperDecisionPublic,
  PaperTradePublic,
} from '../../src/types/paper';

// ──────────────────────────────────────────────────────────────────────
// pipSizeFor
// ──────────────────────────────────────────────────────────────────────

describe('pipSizeFor', () => {
  it('returns 0.0001 for non-JPY pairs', () => {
    expect(pipSizeFor('EUR_USD')).toBe(0.0001);
    expect(pipSizeFor('GBP_USD')).toBe(0.0001);
    expect(pipSizeFor('AUD_NZD')).toBe(0.0001);
  });

  it('returns 0.01 for JPY-quote pairs', () => {
    expect(pipSizeFor('USD_JPY')).toBe(0.01);
    expect(pipSizeFor('EUR_JPY')).toBe(0.01);
  });

  it('tolerates slash separator', () => {
    expect(pipSizeFor('EUR/USD')).toBe(0.0001);
    expect(pipSizeFor('USD/JPY')).toBe(0.01);
  });

  it('defaults to 0.0001 for unknown/malformed input', () => {
    expect(pipSizeFor(null)).toBe(0.0001);
    expect(pipSizeFor(undefined)).toBe(0.0001);
    expect(pipSizeFor('')).toBe(0.0001);
    expect(pipSizeFor('EURUSD')).toBe(0.0001);
  });
});

// ──────────────────────────────────────────────────────────────────────
// _computeScaleInner — the direction-flip logic Wilson chose
// ──────────────────────────────────────────────────────────────────────

function makeTrade(overrides: Partial<PaperTradePublic> = {}): PaperTradePublic {
  return {
    id: 't1',
    entry_timestamp: '2026-08-02T12:00:00Z',
    exit_timestamp: null,
    side: 'buy',
    quantity: 10_000,
    entry_price: 1.09342,
    exit_price: null,
    pnl: null,
    pnl_pct: null,
    origin: 'live',
    signal_reason: null,
    exit_reason: null,
    financing: null,
    commission: null,
    net_pnl: null,
    ...overrides,
  };
}

describe('_computeScaleInner — LONG', () => {
  const pip = 0.0001;
  const entry = 1.09342;
  const slPips = 22;
  const tpPips = 40;
  const nowPrice = 1.09400; // above entry → in favour for LONG

  it('places SL below entry and TP above entry', () => {
    const scale = _computeScaleInner(
      makeTrade({ side: 'buy', entry_price: entry }),
      true,
      nowPrice,
      slPips,
      tpPips,
      pip,
    );
    expect(scale.slPrice).toBeCloseTo(entry - slPips * pip, 6);
    expect(scale.tpPrice).toBeCloseTo(entry + tpPips * pip, 6);
    // Layout is price-ascending — SL (lower) should be at position 0
    // and TP (higher) at position 100 along the scale.
    expect(Math.min(scale.slPrice, scale.tpPrice)).toBe(scale.slPrice);
    expect(Math.max(scale.slPrice, scale.tpPrice)).toBe(scale.tpPrice);
  });

  it('reports pipsInFavour as positive when now > entry', () => {
    const scale = _computeScaleInner(
      makeTrade({ side: 'buy', entry_price: entry }),
      true,
      nowPrice,
      slPips,
      tpPips,
      pip,
    );
    expect(scale.pipsInFavour).toBeCloseTo((nowPrice - entry) / pip, 4);
    expect(scale.pipsInFavour).toBeGreaterThan(0);
  });

  it('clamps now-position when price runs beyond the scale', () => {
    const scaleAbove = _computeScaleInner(
      makeTrade({ side: 'buy', entry_price: entry }),
      true,
      entry + 100 * pip, // past TP
      slPips,
      tpPips,
      pip,
    );
    expect(scaleAbove.nowPctFromLeft).toBe(100);
  });
});

describe('_computeScaleInner — SHORT', () => {
  const pip = 0.0001;
  const entry = 1.09342;
  const slPips = 22;
  const tpPips = 40;
  const nowPrice = 1.09298; // below entry → in favour for SHORT

  it('places SL above entry and TP below entry (physical price layout)', () => {
    const scale = _computeScaleInner(
      makeTrade({ side: 'sell', entry_price: entry }),
      false,
      nowPrice,
      slPips,
      tpPips,
      pip,
    );
    expect(scale.slPrice).toBeCloseTo(entry + slPips * pip, 6);
    expect(scale.tpPrice).toBeCloseTo(entry - tpPips * pip, 6);
    // For a SHORT: SL is the higher price, TP is the lower price.
    expect(scale.slPrice).toBeGreaterThan(entry);
    expect(scale.tpPrice).toBeLessThan(entry);
  });

  it('reports pipsInFavour as positive when now < entry for a SHORT', () => {
    const scale = _computeScaleInner(
      makeTrade({ side: 'sell', entry_price: entry }),
      false,
      nowPrice,
      slPips,
      tpPips,
      pip,
    );
    expect(scale.pipsInFavour).toBeCloseTo((entry - nowPrice) / pip, 4);
    expect(scale.pipsInFavour).toBeGreaterThan(0);
  });

  it('reports pipsInFavour as NEGATIVE when now > entry for a SHORT', () => {
    const scale = _computeScaleInner(
      makeTrade({ side: 'sell', entry_price: entry }),
      false,
      entry + 5 * pip,
      slPips,
      tpPips,
      pip,
    );
    expect(scale.pipsInFavour).toBeLessThan(0);
  });
});

// ──────────────────────────────────────────────────────────────────────
// buildActivity — interleave + filter
// ──────────────────────────────────────────────────────────────────────

function makeDecision(
  overrides: Partial<PaperDecisionPublic> = {},
): PaperDecisionPublic {
  return {
    id: 'd1',
    timestamp: '2026-08-02T12:30:00Z',
    action: 'USER_SIGNAL_SKIPPED_STALE',
    agent_type: 'runner',
    actor_user_id: null,
    reason: null,
    metadata_json: null,
    ...overrides,
  };
}

describe('buildActivity', () => {
  it('emits open+close rows for closed trades and one row for open trades', () => {
    const trades: PaperTradePublic[] = [
      makeTrade({
        id: 'closed',
        entry_timestamp: '2026-08-02T10:00:00Z',
        exit_timestamp: '2026-08-02T11:00:00Z',
        exit_price: 1.0950,
        pnl: 12,
      }),
      makeTrade({
        id: 'open',
        entry_timestamp: '2026-08-02T12:00:00Z',
        exit_timestamp: null,
      }),
    ];
    const rows = buildActivity(trades, [], 10);
    // 2 trades → 1 open row + 1 open+close pair = 3 rows total.
    expect(rows).toHaveLength(3);
    expect(rows.filter((r) => r.kind === 'trade-open')).toHaveLength(2);
    expect(rows.filter((r) => r.kind === 'trade-close')).toHaveLength(1);
  });

  it('orders newest-first', () => {
    const rows = buildActivity(
      [
        makeTrade({ id: 'a', entry_timestamp: '2026-08-02T10:00:00Z' }),
        makeTrade({ id: 'b', entry_timestamp: '2026-08-02T12:00:00Z' }),
      ],
      [],
      10,
    );
    expect(rows[0].ts).toBe('2026-08-02T12:00:00Z');
    expect(rows[1].ts).toBe('2026-08-02T10:00:00Z');
  });

  it('filters out USER_ORDER_PLACED and USER_POSITION_CLOSED (redundant with trade rows)', () => {
    const decisions = [
      makeDecision({ action: 'USER_ORDER_PLACED' }),
      makeDecision({ action: 'USER_POSITION_CLOSED' }),
      makeDecision({ action: 'USER_SIGNAL_SKIPPED_STALE' }),
    ];
    const rows = buildActivity([], decisions, 10);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe('decision');
    if (rows[0].kind === 'decision') {
      expect(rows[0].decision.action).toBe('USER_SIGNAL_SKIPPED_STALE');
    }
  });

  it('caps output to the requested limit', () => {
    const many = Array.from({ length: 20 }, (_, i) =>
      makeTrade({
        id: String(i),
        entry_timestamp: `2026-08-02T10:${String(i).padStart(2, '0')}:00Z`,
      }),
    );
    expect(buildActivity(many, [], 6)).toHaveLength(6);
  });
});
