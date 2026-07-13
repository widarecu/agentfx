/**
 * Live equity chart with dual x-axis (US-022 / US-026).
 *
 * Wilson's answer #2 (locked in the v2 UX): dual x-axis — bottom
 * shows relative hours since run start, top shows actual local
 * timestamps. Live equity drawn in emerald; backtest OOS overlay
 * (dashed grey) sourced from `GET /paper-runs/:id/backtest-overlay`
 * — the walk-forward OOS equity series of the source genome,
 * anchored at `runStartMs` so the two curves share an x-axis.
 *
 * `liveSeries` is seeded from `GET /paper-runs/:id/equity` on mount
 * (reload-safe) and appended-to by the 5s poll loop. Scaling helpers
 * are exposed as `_scaleX`/`_scaleY` so Vitest can lock the axis
 * maths without jsdom.
 */

import React from 'react';

// Chart dimensions — matched to the run-detail card padding so the
// SVG fills its container without a layout shift on first paint.
const VIEWBOX_WIDTH = 600;
const VIEWBOX_HEIGHT = 220;
const PADDING_LEFT = 40;
const PADDING_RIGHT = 20;
const PADDING_TOP = 30;
const PADDING_BOTTOM = 30;
const PLOT_WIDTH = VIEWBOX_WIDTH - PADDING_LEFT - PADDING_RIGHT;
const PLOT_HEIGHT = VIEWBOX_HEIGHT - PADDING_TOP - PADDING_BOTTOM;

export interface EquityPoint {
  /** Milliseconds since epoch. */
  timestampMs: number;
  /** Account balance at this tick — already in home currency. */
  balance: number;
}

export interface LivePnLChartProps {
  /** Point at which the runner snapshotted `snapshot_balance`. Used to
   *  anchor the bottom axis at `h0` and the top axis at that timestamp. */
  runStartMs: number;
  snapshotBalance: number;
  /** Sampled while the user watches the run detail page (5s polling). */
  liveSeries: EquityPoint[];
  /** Optional backtest OOS overlay. Left as an empty array for PR-J's
   *  ship — real data wires up in v0.6.1. */
  backtestSeries?: EquityPoint[];
}

export function LivePnLChart({
  runStartMs,
  snapshotBalance,
  liveSeries,
  backtestSeries = [],
}: LivePnLChartProps) {
  const now = liveSeries.length
    ? liveSeries[liveSeries.length - 1].timestampMs
    : runStartMs;
  const spanMs = Math.max(now - runStartMs, 60_000); // Min 1min so the axis is legible on brand-new runs.

  // Anchor the live line at (runStartMs, snapshotBalance). Without
  // this, the curve only starts wherever the user opened the tab —
  // any run older than that shows a floating line detached from
  // h0.00 which is misleading. Only prepend when there ISN'T
  // already an equity sample near the run start (within 30s).
  const anchoredSeries: EquityPoint[] =
    liveSeries.length === 0 ||
    liveSeries[0].timestampMs - runStartMs > 30_000
      ? [{ timestampMs: runStartMs, balance: snapshotBalance }, ...liveSeries]
      : liveSeries;

  const allBalances = [
    snapshotBalance,
    ...anchoredSeries.map((p) => p.balance),
    ...backtestSeries.map((p) => p.balance),
  ];
  const minBalance = Math.min(...allBalances) - snapshotBalance * 0.005;
  const maxBalance = Math.max(...allBalances) + snapshotBalance * 0.005;
  const balanceRange = Math.max(maxBalance - minBalance, snapshotBalance * 0.01);

  const scaleX = (ms: number) =>
    _scaleX(ms, runStartMs, spanMs, PADDING_LEFT, PLOT_WIDTH);
  const scaleY = (balance: number) =>
    _scaleY(balance, minBalance, balanceRange, PADDING_TOP, PLOT_HEIGHT);

  const livePath = pointsToPath(anchoredSeries, scaleX, scaleY);
  const backtestPath = pointsToPath(backtestSeries, scaleX, scaleY);

  // Bottom axis ticks — 5 evenly spaced relative-hour labels.
  const relativeTicks = [0, 0.25, 0.5, 0.75, 1].map((frac) => ({
    frac,
    hours: (spanMs / 3_600_000) * frac,
    x: PADDING_LEFT + PLOT_WIDTH * frac,
  }));

  // Top axis ticks — same positions, but as actual wall-clock
  // timestamps in UTC (matches the candle chart axis, decision log,
  // and Lifecycle panel — see utils/time.ts).
  const utcTicks = relativeTicks.map((t) => ({
    ...t,
    label: formatUtcHHMM(runStartMs + spanMs * t.frac),
  }));

  const currentLive = liveSeries[liveSeries.length - 1];

  return (
    <div className="relative w-full h-full">
      <svg
        viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
        preserveAspectRatio="none"
        className="w-full h-full block"
        role="img"
        aria-label="Live equity vs backtest overlay"
      >
        {/* Top axis (actual UTC timestamps) */}
        <line
          x1={PADDING_LEFT}
          y1={PADDING_TOP - 8}
          x2={PADDING_LEFT + PLOT_WIDTH}
          y2={PADDING_TOP - 8}
          stroke="#94a3b8"
          strokeWidth={0.5}
        />
        {utcTicks.map((t) => (
          <text
            key={`utc-${t.frac}`}
            x={t.x}
            y={PADDING_TOP - 12}
            textAnchor="middle"
            fontSize={8}
            fill="#94a3b8"
          >
            {t.label}
          </text>
        ))}

        {/* Bottom axis (relative hours) */}
        <line
          x1={PADDING_LEFT}
          y1={PADDING_TOP + PLOT_HEIGHT}
          x2={PADDING_LEFT + PLOT_WIDTH}
          y2={PADDING_TOP + PLOT_HEIGHT}
          stroke="#94a3b8"
          strokeWidth={0.5}
        />
        {relativeTicks.map((t) => (
          <text
            key={`rel-${t.frac}`}
            x={t.x}
            y={PADDING_TOP + PLOT_HEIGHT + 12}
            textAnchor="middle"
            fontSize={8}
            fill="#64748b"
          >
            h{t.hours.toFixed(t.hours >= 1 ? 1 : 2)}
          </text>
        ))}

        {/* Y axis */}
        <line
          x1={PADDING_LEFT}
          y1={PADDING_TOP}
          x2={PADDING_LEFT}
          y2={PADDING_TOP + PLOT_HEIGHT}
          stroke="#94a3b8"
          strokeWidth={0.5}
        />
        <text
          x={PADDING_LEFT - 4}
          y={PADDING_TOP - 4}
          textAnchor="end"
          fontSize={9}
          fill="#64748b"
        >
          Equity
        </text>

        {/* Snapshot reference line — where the runner started. */}
        <line
          x1={PADDING_LEFT}
          y1={scaleY(snapshotBalance)}
          x2={PADDING_LEFT + PLOT_WIDTH}
          y2={scaleY(snapshotBalance)}
          stroke="#cbd5e1"
          strokeWidth={0.5}
          strokeDasharray="2,3"
        />

        {/* Backtest overlay (dashed grey). */}
        {backtestPath && (
          <path
            d={backtestPath}
            fill="none"
            stroke="#94a3b8"
            strokeWidth={1.5}
            strokeDasharray="4,3"
          />
        )}

        {/* Live equity curve (solid emerald). */}
        {livePath && (
          <path
            d={livePath}
            fill="none"
            stroke="#10b981"
            strokeWidth={2}
          />
        )}

        {/* Current point marker. */}
        {currentLive && (
          <circle
            cx={scaleX(currentLive.timestampMs)}
            cy={scaleY(currentLive.balance)}
            r={4}
            fill="#10b981"
          />
        )}

        {/* Bottom axis title */}
        <text
          x={PADDING_LEFT + PLOT_WIDTH / 2}
          y={VIEWBOX_HEIGHT - 4}
          textAnchor="middle"
          fontSize={8}
          fill="#94a3b8"
        >
          hours since run start
        </text>
      </svg>
      {/* Backtest-not-wired-yet notice is intentionally dropped —
          the parent card's subtitle already says "Live samples
          accumulate every 5s while this tab is open" which covers
          the same ground. Keeping it inside the chart wrapper
          caused overflow into the panels below when the SVG
          rendered at its intrinsic aspect ratio (viewBox 600×220
          into a full-width parent). */}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Pure helpers (exposed for Vitest)
// ─────────────────────────────────────────────────────────────────────

/**
 * Map a timestamp to an SVG x-coordinate. Clamps to the plot area so
 * a stale sample after `runStartMs + spanMs` doesn't render off-canvas.
 */
export function _scaleX(
  timestampMs: number,
  runStartMs: number,
  spanMs: number,
  paddingLeft: number,
  plotWidth: number,
): number {
  if (spanMs <= 0) return paddingLeft;
  const frac = Math.min(
    1,
    Math.max(0, (timestampMs - runStartMs) / spanMs),
  );
  return paddingLeft + frac * plotWidth;
}

/**
 * Map a balance to an SVG y-coordinate. Larger balance = smaller y
 * (SVG y grows downward, so we invert).
 */
export function _scaleY(
  balance: number,
  minBalance: number,
  balanceRange: number,
  paddingTop: number,
  plotHeight: number,
): number {
  if (balanceRange <= 0) return paddingTop + plotHeight / 2;
  const frac = (balance - minBalance) / balanceRange;
  return paddingTop + plotHeight - frac * plotHeight;
}

function pointsToPath(
  series: EquityPoint[],
  scaleX: (ms: number) => number,
  scaleY: (balance: number) => number,
): string | null {
  if (!series.length) return null;
  const segments = series.map((p, i) => {
    const cmd = i === 0 ? 'M' : 'L';
    return `${cmd}${scaleX(p.timestampMs).toFixed(1)},${scaleY(p.balance).toFixed(1)}`;
  });
  return segments.join(' ');
}

function formatUtcHHMM(ms: number): string {
  const d = new Date(ms);
  const hh = String(d.getUTCHours()).padStart(2, '0');
  const mm = String(d.getUTCMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}
