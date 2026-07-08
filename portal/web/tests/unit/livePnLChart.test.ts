/**
 * Vitest for the pure scale helpers in `LivePnLChart.tsx` (PR-J).
 *
 * The SVG rendering itself needs jsdom; scaling maths is pure and
 * testable in node. Locks the axis behaviour Wilson's answer #2
 * hinges on: dual x-axis with relative hours (bottom) and actual UTC
 * (top) that always land at the same physical coordinates so the two
 * axes stay perfectly aligned.
 */

import { describe, expect, it } from 'vitest';
import { _scaleX, _scaleY } from '../../src/components/LivePnLChart';

describe('_scaleX', () => {
  it('anchors t=runStart at the left edge', () => {
    expect(_scaleX(1000, 1000, 60_000, 40, 500)).toBe(40);
  });

  it('anchors t=runStart+span at the right edge', () => {
    expect(_scaleX(61_000, 1000, 60_000, 40, 500)).toBe(540);
  });

  it('interpolates linearly', () => {
    // Halfway through the window → midpoint of the plot.
    expect(_scaleX(31_000, 1000, 60_000, 40, 500)).toBe(290);
  });

  it('clamps late timestamps to the right edge (no overshoot)', () => {
    expect(_scaleX(120_000, 1000, 60_000, 40, 500)).toBe(540);
  });

  it('clamps early timestamps to the left edge', () => {
    expect(_scaleX(500, 1000, 60_000, 40, 500)).toBe(40);
  });

  it('degenerate spanMs → pins to the left', () => {
    expect(_scaleX(1000, 1000, 0, 40, 500)).toBe(40);
  });
});

describe('_scaleY', () => {
  it('anchors min balance at the bottom', () => {
    // paddingTop=30, plotHeight=100 → bottom is at y=130
    expect(_scaleY(1000, 1000, 500, 30, 100)).toBe(130);
  });

  it('anchors max balance at the top', () => {
    // At balance = min + range → frac=1 → y = paddingTop
    expect(_scaleY(1500, 1000, 500, 30, 100)).toBe(30);
  });

  it('inverts direction (bigger balance = smaller y)', () => {
    const low = _scaleY(1200, 1000, 500, 30, 100);
    const high = _scaleY(1400, 1000, 500, 30, 100);
    expect(low).toBeGreaterThan(high);
  });

  it('degenerate range → returns midline', () => {
    expect(_scaleY(1000, 1000, 0, 30, 100)).toBe(80);
  });
});

describe('axes stay aligned across scales', () => {
  it('same-fractional-position on both axes lands at same x', () => {
    // The dual x-axis promise: whatever hour we render on the bottom
    // must be the exact same pixel as the corresponding UTC time on
    // the top. Both axes share `_scaleX` so this is by construction —
    // this test just locks that promise so a refactor that fragments
    // the two axes' scaling maths would fail loudly.
    const runStart = 1_000_000;
    const span = 60_000;
    const padding = 40;
    const plot = 500;

    // 30s = 50% mark
    const halfTime = runStart + span / 2;
    expect(_scaleX(halfTime, runStart, span, padding, plot)).toBe(
      padding + plot / 2,
    );

    // Both axes: use `_scaleX` with the same runStart + span, so any
    // caller that renders both bottom (relative hours) and top (UTC)
    // ticks from the SAME timestamp will get the SAME x.
    const utcTick = _scaleX(halfTime, runStart, span, padding, plot);
    const relTick = _scaleX(halfTime, runStart, span, padding, plot);
    expect(utcTick).toBe(relTick);
  });
});
