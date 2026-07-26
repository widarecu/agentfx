/**
 * Live vs backtest equity chart (v0.6.3 redesign, PR 1).
 *
 * Wilson UAT 2026-07-24 replaced the placeholder chart. Design brief:
 * `portal/web/wireframes/paper-run-detail/README.md`. This PR delivers
 * the P0 slice: calendar-day X-axis + 8-tile summary strip + labelled
 * snapshot reference line + shaded weekend-market-closed band. Later
 * PRs will add hover tooltip (PR2), trade markers (PR3), range
 * selector (PR4), drawdown shading (PR5), and OOS overlay wiring (PR6).
 *
 * Data shaping (gap detection, tick placement, summary tile
 * computation) lives in `../utils/livePnlChart.ts` so it can be
 * unit-tested in Vitest's node env without jsdom, matching the
 * `_scaleX` / `_buildRiskConfig` pattern established elsewhere.
 */

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import type {
  BacktestOverlayReason,
  PaperTradePublic,
} from '../types/paper';
import {
  computeDrawdownSeries,
  computeStreaks,
  computeSummaryStrip,
  detectFinancingEvents,
  detectGaps,
  filterByRange,
  interpolateOverlay,
  nearestSample,
  pickAxisTicks,
  projectTrade,
  toEquityPoints,
  tradesUpTo,
  type EquityPoint,
  type FinancingEvent,
  type RangeKey,
} from '../utils/livePnlChart';
import { formatElapsed } from '../utils/time';

// Chart dimensions — 900×320 viewBox from the hi-fi mockup. Plot
// rect is (60,24)→(880,264); bottom 40px reserved for calendar-day
// labels, left 60px for balance labels.
const VIEWBOX_WIDTH = 900;
const VIEWBOX_HEIGHT = 320;
const PADDING_LEFT = 60;
const PADDING_RIGHT = 20;
const PADDING_TOP = 24;
const PADDING_BOTTOM = 56; // taller so 2-row calendar labels fit
const PLOT_WIDTH = VIEWBOX_WIDTH - PADDING_LEFT - PADDING_RIGHT;
const PLOT_HEIGHT = VIEWBOX_HEIGHT - PADDING_TOP - PADDING_BOTTOM;

// Re-exported from the helpers module so existing consumers (Vitest,
// pages/PaperRunDetail) don't have to relocate their import path.
export type { EquityPoint } from '../utils/livePnlChart';

export interface LivePnLChartProps {
  /** Wall-clock ms when the run's paper-trading lifecycle began.
   *  Anchors the chart X-axis at h0. Should be `run.approved_at`
   *  (never `started_at`, which gets rewritten on subprocess
   *  restart — see the started_at fix at commit 86d072c). */
  runStartMs: number;
  /** Balance at h0; anchors the labelled snapshot reference line. */
  snapshotBalance: number;
  /** Sampled while the user watches the run detail page (5s polling). */
  liveSeries: EquityPoint[];
  /** Trades for the run — used by the summary strip and, in PR 3, by
   *  the ▲ / ▼ / ● markers. Pass `[]` if the trades fetch failed. */
  trades: PaperTradePublic[];
  /** Optional backtest OOS overlay. Empty when the underlying
   *  ValidationFold.equity_series column isn't populated for the
   *  source genome (a v0.4.0 schema gap). */
  backtestSeries?: EquityPoint[];
  /** Backend hint about WHY the overlay is empty. When
   *  `{ available: false, reason: ... }`, the chart renders an
   *  amber chip explaining the schema gap in human terms instead of
   *  the overlay just silently missing. `null`/undefined = no chip. */
  overlayStatus?: { available: boolean; reason?: BacktestOverlayReason | null };
  /** Wall-clock "now" ms — anchors the chart's X-axis right edge.
   *  Distinct from the last equity sample's timestamp. Passing
   *  Date.now() (from a live-updating parent) lets weekend bands and
   *  the axis extend past the last real sample, so a mid-weekend view
   *  correctly shows we're inside a MARKET CLOSED region even though
   *  no new equity samples are being written. Defaults to Date.now()
   *  when omitted. Parents on TERMINAL runs should pass the last
   *  sample's timestamp so the axis doesn't drift into the future. */
  nowMs?: number;
}

export function LivePnLChart({
  runStartMs,
  snapshotBalance,
  liveSeries,
  trades,
  backtestSeries = [],
  overlayStatus,
  nowMs: nowMsProp,
}: LivePnLChartProps) {
  // PR 4: range selector. Default 7d; `1 / 2 / A` keyboard shortcuts
  // switch between 24h / 7d / All. Summary strip is always computed
  // from the FULL series so the tiles reflect run-total not window-
  // total; the chart itself narrows to the selected window.
  const [rangeKey, setRangeKey] = useState<RangeKey>('7d');

  const summary = useMemo(
    () => computeSummaryStrip(liveSeries, trades, snapshotBalance),
    [liveSeries, trades, snapshotBalance],
  );
  const streaks = useMemo(() => computeStreaks(trades), [trades]);

  // "Now" for axis + weekend-band purposes = wall clock (or an
  // explicit override from the parent for terminal runs). The last
  // equity sample can be hours behind — the runner stops writing
  // samples over the weekend, but we still want the axis to advance
  // so a mid-weekend view renders the current MARKET CLOSED region.
  const lastSampleMs = liveSeries.length
    ? liveSeries[liveSeries.length - 1].timestampMs
    : runStartMs;
  const nowMs = Math.max(
    lastSampleMs,
    nowMsProp ?? Date.now(),
  );

  // Local name intentionally NOT `window` — shadowing the global
  // hoses the `window.addEventListener` call in the keyboard-shortcut
  // effect below.
  const rangeFilter = useMemo(
    () =>
      filterByRange({
        rangeKey,
        runStartMs,
        nowMs,
        equity: liveSeries,
        trades,
      }),
    [rangeKey, runStartMs, nowMs, liveSeries, trades],
  );

  // Alias the window bounds for scale-math clarity. `axisStartMs`
  // anchors h0 on the plot; `runStartMs` remains the run's original
  // paper-trading start for the header's "elapsed" sub-line and
  // tooltip.
  const axisStartMs = rangeFilter.startMs;
  const axisEndMs = rangeFilter.endMs;
  const spanMs = Math.max(axisEndMs - axisStartMs, 60_000);
  const windowedSeries = rangeFilter.equity;
  const windowedTrades = rangeFilter.trades;

  // Anchor the live line at (axisStartMs, snapshotBalance) when there
  // isn't already a sample close to the window start.
  const anchoredSeries: EquityPoint[] =
    windowedSeries.length === 0 ||
    windowedSeries[0].timestampMs - axisStartMs > 30_000
      ? [
          {
            timestampMs: axisStartMs,
            balance: snapshotBalance,
            unrealizedPnl: 0,
          },
          ...windowedSeries,
        ]
      : windowedSeries;

  const allBalances = [
    snapshotBalance,
    ...anchoredSeries.map((p) => p.balance),
    ...backtestSeries.map((p) => p.balance),
  ];
  const rawMin = Math.min(...allBalances);
  const rawMax = Math.max(...allBalances);
  const rawRange = Math.max(rawMax - rawMin, snapshotBalance * 0.01);
  const minBalance = rawMin - rawRange * 0.08;
  const maxBalance = rawMax + rawRange * 0.08;
  const balanceRange = maxBalance - minBalance;

  const scaleX = (ms: number) =>
    _scaleX(ms, axisStartMs, spanMs, PADDING_LEFT, PLOT_WIDTH);
  const scaleY = (balance: number) =>
    _scaleY(balance, minBalance, balanceRange, PADDING_TOP, PLOT_HEIGHT);

  // Extend the visible line horizontally from the last real sample
  // to `axisEndMs` when the runner has stopped writing samples
  // (weekend, ingester lag). This is a DISPLAY-only extension: the
  // synthetic tail point is not used for hover snap or trade-marker
  // projections, so nothing downstream can accidentally treat it as
  // real data. Skip if the tail would be < 5min (visually
  // indistinguishable) or if the run is terminal and the sample
  // is already at/past the axis end.
  const renderLiveSeries: EquityPoint[] = useMemo(() => {
    if (anchoredSeries.length === 0) return anchoredSeries;
    const last = anchoredSeries[anchoredSeries.length - 1];
    const tailMs = Math.min(nowMs, axisEndMs);
    if (tailMs - last.timestampMs < 5 * 60_000) return anchoredSeries;
    return [
      ...anchoredSeries,
      {
        timestampMs: tailMs,
        balance: last.balance,
        unrealizedPnl: last.unrealizedPnl,
      },
    ];
  }, [anchoredSeries, nowMs, axisEndMs]);

  const livePath = pointsToPath(renderLiveSeries, scaleX, scaleY);
  const backtestPath = pointsToPath(backtestSeries, scaleX, scaleY);

  const gaps = useMemo(
    () => detectGaps(anchoredSeries, undefined, axisStartMs, axisEndMs),
    [anchoredSeries, axisStartMs, axisEndMs],
  );

  // ── Drawdown shading polygon (PR 5) ────────────────────────────
  //
  // Build a filled area between the running peak and the current
  // balance, filled rose at low opacity so it reads as background
  // context without competing with the emerald equity line.
  // Produces a polygon path only when the run has EVER been
  // underwater within the visible window — no shading on a
  // monotonically rising curve avoids clutter.
  const drawdownPath = useMemo(() => {
    if (anchoredSeries.length === 0) return null;
    const dd = computeDrawdownSeries(anchoredSeries, snapshotBalance);
    if (dd.every((p) => p.drawdownPct === 0)) return null;
    // Wilson UAT 2026-07-25: both edges of the drawdown band now
    // use step-after so the shading follows the equity line's
    // staircase profile exactly. Straight-line edges bled outside
    // the emerald line at every close event.
    const topEdge = _stepAfterPath(
      dd.map((p) => ({
        timestampMs: p.timestampMs,
        balance: p.peakBalance,
        unrealizedPnl: 0,
      })),
      scaleX,
      scaleY,
    );
    // Bottom edge walks right-to-left over the balance curve so the
    // polygon closes. Reverse the series + reverse the step pattern:
    // for a right-to-left step-after equivalent, we do step-BEFORE
    // (vertical first, then horizontal to the prev sample's x).
    const rev = [...dd].reverse();
    const bottomSegs: string[] = [];
    let prevY = scaleY(rev[0].balance);
    // Path continues from topEdge's last point at (rev[0].x, peakBalance).
    // Move vertically to (rev[0].x, balance) then horizontally step-before.
    bottomSegs.push(`L${scaleX(rev[0].timestampMs).toFixed(1)},${prevY.toFixed(1)}`);
    for (let i = 1; i < rev.length; i++) {
      const p = rev[i];
      const x = scaleX(p.timestampMs).toFixed(1);
      const y = scaleY(p.balance);
      // step-before (walking right→left): vertical first at prev x,
      // then horizontal to (x, prevY = y)... but here we're going
      // backwards, so we go horizontal to x at prevY, then vertical
      // to y. Same shape as step-after in the other direction.
      bottomSegs.push(`L${x},${prevY.toFixed(1)}`);
      bottomSegs.push(`L${x},${y.toFixed(1)}`);
      prevY = y;
    }
    return `${topEdge} ${bottomSegs.join(' ')} Z`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    anchoredSeries,
    snapshotBalance,
    axisStartMs,
    spanMs,
    minBalance,
    balanceRange,
  ]);

  const xTicks = useMemo(
    () => pickAxisTicks(axisStartMs, axisEndMs, rangeKey),
    [axisStartMs, axisEndMs, rangeKey],
  );

  // ── Keyboard shortcuts (PR 4) ──────────────────────────────────
  //
  // 1 → 24h, 2 → 7d, A → All. Only fire when the chart's own DOM
  // subtree is mounted (any-focus-outside checks live in the
  // shortcut hint — we don't consume input when the user is typing
  // in a form). Uses `keydown` on window with a targeted guard
  // against text-input focus.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === '1') setRangeKey('24h');
      else if (e.key === '2') setRangeKey('7d');
      else if (e.key === 'a' || e.key === 'A') setRangeKey('all');
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  // Y-axis ticks — 5 evenly-spaced balance labels.
  const yTicks = useMemo(() => {
    const step = balanceRange / 4;
    return [0, 1, 2, 3, 4].map((i) => {
      const value = maxBalance - i * step;
      return { y: scaleY(value), value };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [minBalance, balanceRange]);

  const currentLive = liveSeries[liveSeries.length - 1];
  const elapsedLabel = formatElapsed(runStartMs, nowMs);

  // ── Hover crosshair state (PR 2) ────────────────────────────────
  //
  // Track a single ms timestamp for the cursor position. When null,
  // no crosshair renders. Pointer events fire on a full-width invisible
  // capture rect over the plot; `_clientXToTimestamp` translates the
  // viewport X to the run timeline using getBoundingClientRect on the
  // SVG (accounts for the container's actual pixel width vs the
  // 900-unit viewBox).
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [hoverMs, setHoverMs] = useState<number | null>(null);
  const handlePointerMove = useCallback(
    (e: React.PointerEvent<SVGRectElement>) => {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const ts = _clientXToTimestamp({
        clientX: e.clientX,
        rectLeft: rect.left,
        rectWidth: rect.width,
        runStartMs: axisStartMs, // clamp to the visible WINDOW, not run
        spanMs,
        paddingLeft: PADDING_LEFT,
        plotWidth: PLOT_WIDTH,
        viewBoxWidth: VIEWBOX_WIDTH,
      });
      setHoverMs(ts);
    },
    [axisStartMs, spanMs],
  );
  const handlePointerLeave = useCallback(() => setHoverMs(null), []);

  // Snap the crosshair to the nearest sample so the tooltip's balance
  // matches the drawn curve (not a hypothetical interpolated point).
  const hoverPoint = useMemo(
    () => (hoverMs != null ? nearestSample(windowedSeries, hoverMs) : null),
    [hoverMs, windowedSeries],
  );
  const hoverBacktest = useMemo(
    () =>
      hoverMs != null ? interpolateOverlay(backtestSeries, hoverMs) : null,
    [hoverMs, backtestSeries],
  );
  const hoverTradeStats = useMemo(
    () => (hoverMs != null ? tradesUpTo(trades, hoverMs) : null),
    [hoverMs, trades],
  );

  // ── Trade markers (PR 3) ───────────────────────────────────────
  const projections = useMemo(
    () =>
      windowedTrades.map((t) =>
        projectTrade(t, anchoredSeries, scaleX, scaleY),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [windowedTrades, anchoredSeries, axisStartMs, spanMs, minBalance, balanceRange],
  );

  // ── Financing events (Wilson UAT 2026-07-25) ────────────────────
  // Balance changes not matched to a trade close = overnight
  // financing / swap. Rendered as small amber diamonds so the eye
  // can tell "this drop was a losing trade" from "this drop was
  // overnight interest". Restricted to the visible window so the
  // range selector prunes them too.
  const financingEvents: FinancingEvent[] = useMemo(
    () => detectFinancingEvents(windowedSeries, trades),
    [windowedSeries, trades],
  );
  const [hoverFinancingIdx, setHoverFinancingIdx] = useState<number | null>(
    null,
  );
  const [hoverTradeId, setHoverTradeId] = useState<string | null>(null);
  const hoveredTrade =
    hoverTradeId != null
      ? trades.find((t) => t.id === hoverTradeId) ?? null
      : null;
  const hoveredProjection =
    hoverTradeId != null
      ? projections.find((p) => p.tradeId === hoverTradeId) ?? null
      : null;

  const handleTradeClick = useCallback((tradeId: string) => {
    // Scroll the matching trade row into view. The row is tagged with
    // `data-trade-id` by `PaperRunTradesTable`. If the table isn't
    // mounted yet (unlikely) we just no-op.
    const row = document.querySelector(
      `[data-trade-id="${CSS.escape(tradeId)}"]`,
    );
    if (row && 'scrollIntoView' in row) {
      (row as HTMLElement).scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      });
      row.classList.add('ring-2', 'ring-amber-400');
      setTimeout(() => {
        row.classList.remove('ring-2', 'ring-amber-400');
      }, 1500);
    }
  }, []);
  const pnlTotal =
    summary.liveBalance - snapshotBalance + summary.unrealisedPnl;
  const pnlTotalPct = snapshotBalance > 0
    ? (pnlTotal / snapshotBalance) * 100
    : 0;

  return (
    <div className="w-full">
      {/* ── Header row: chart title + inline stats + range pills ──── */}
      <div className="flex items-start justify-between gap-6 flex-wrap mb-3">
        <div>
          <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Live vs backtest equity
          </div>
          <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400 font-mono flex flex-wrap items-center gap-x-3 gap-y-0.5">
            <span>
              snapshot{' '}
              <span className="text-slate-700 dark:text-slate-200 tabular-nums">
                {_fmtCurrency(snapshotBalance)}
              </span>
            </span>
            <span className="text-slate-400 dark:text-slate-600">·</span>
            <span>
              now{' '}
              <span className="text-slate-700 dark:text-slate-200 tabular-nums">
                {_fmtCurrency(summary.liveBalance)}
              </span>
            </span>
            <span className="text-slate-400 dark:text-slate-600">·</span>
            <span
              className={
                pnlTotal >= 0
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : 'text-rose-600 dark:text-rose-400'
              }
            >
              {_fmtSigned(pnlTotal)} (
              {_fmtSignedPct(pnlTotalPct)})
            </span>
            <span className="text-slate-400 dark:text-slate-600">·</span>
            <span>
              elapsed{' '}
              <span className="text-slate-700 dark:text-slate-200 tabular-nums">
                {elapsedLabel}
              </span>
            </span>
          </div>
        </div>

        {/* ── Range selector (PR 4) ─────────────────────────────── */}
        <div
          className="inline-flex items-center rounded-md border border-slate-200 dark:border-slate-700 overflow-hidden text-xs"
          role="tablist"
          aria-label="Chart time range"
        >
          <RangePill
            active={rangeKey === '24h'}
            onClick={() => setRangeKey('24h')}
            label="24h"
            shortcut="1"
          />
          <RangePill
            active={rangeKey === '7d'}
            onClick={() => setRangeKey('7d')}
            label="7d"
            shortcut="2"
          />
          <RangePill
            active={rangeKey === 'all'}
            onClick={() => setRangeKey('all')}
            label="All"
            shortcut="A"
          />
        </div>
      </div>

      {/* ── 8-tile summary strip ───────────────────────────────────── */}
      <div className="mb-4 grid grid-cols-2 md:grid-cols-4 gap-2">
        <SummaryTile
          label="Live balance"
          value={_fmtCurrency(summary.liveBalance)}
        />
        <SummaryTile
          label="Realised P&L"
          value={_fmtSigned(summary.realisedPnl)}
          valueClass={_pnlColor(summary.realisedPnl)}
          sublabel={`${summary.closedCount} closed trade${summary.closedCount === 1 ? '' : 's'}`}
        />
        <SummaryTile
          label="Unrealised P&L"
          value={_fmtSigned(summary.unrealisedPnl)}
          valueClass={_pnlColor(summary.unrealisedPnl)}
          sublabel={`${summary.openCount} open position${summary.openCount === 1 ? '' : 's'}`}
        />
        <SummaryTile
          label="Max drawdown"
          value={
            summary.maxDrawdownPct === 0
              ? '—'
              : `${summary.maxDrawdownPct.toFixed(2)}%`
          }
          valueClass={
            summary.maxDrawdownPct < 0
              ? 'text-rose-600 dark:text-rose-400'
              : undefined
          }
          sublabel={
            summary.maxDrawdownWindow
              ? `${_fmtShortDate(summary.maxDrawdownWindow.startMs)} → ${_fmtShortDate(summary.maxDrawdownWindow.endMs)}`
              : undefined
          }
        />
        <SummaryTile
          label="Trades"
          value={
            summary.openCount > 0 ? (
              <>
                {summary.tradeCount}{' '}
                <span className="text-amber-600 dark:text-amber-400 text-xs font-normal">
                  · {summary.openCount} open
                </span>
              </>
            ) : (
              String(summary.tradeCount)
            )
          }
          sublabel={_streakSublabel(streaks)}
        />
        <SummaryTile
          label="Win rate"
          value={summary.winRate == null ? '—' : `${(summary.winRate * 100).toFixed(1)}%`}
          sublabel={
            summary.wins + summary.losses > 0
              ? `${summary.wins} W / ${summary.losses} L`
              : undefined
          }
        />
        <SummaryTile
          label="Best trade"
          value={
            summary.bestTrade
              ? _fmtSigned(summary.bestTrade.net_pnl ?? summary.bestTrade.pnl ?? 0)
              : '—'
          }
          valueClass={
            summary.bestTrade ? 'text-emerald-600 dark:text-emerald-400' : undefined
          }
          sublabel={
            summary.bestTrade
              ? `${_fmtShortDate(new Date(summary.bestTrade.exit_timestamp!).getTime())} · ${_sideLabel(summary.bestTrade.side)}`
              : undefined
          }
        />
        <SummaryTile
          label="Worst trade"
          value={
            summary.worstTrade
              ? _fmtSigned(summary.worstTrade.net_pnl ?? summary.worstTrade.pnl ?? 0)
              : '—'
          }
          valueClass={
            summary.worstTrade ? 'text-rose-600 dark:text-rose-400' : undefined
          }
          sublabel={
            summary.worstTrade
              ? `${_fmtShortDate(new Date(summary.worstTrade.exit_timestamp!).getTime())} · ${_sideLabel(summary.worstTrade.side)}`
              : undefined
          }
        />
      </div>

      {/* ── OOS overlay unavailable chip (PR 6) ───────────────────── */}
      {overlayStatus && overlayStatus.available === false && (
        <OverlayUnavailableChip reason={overlayStatus.reason ?? null} />
      )}

      {/* ── Chart canvas ───────────────────────────────────────────── */}
      <div
        className="relative bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded"
        style={{ height: 320 }}
      >
        {/* Legend row */}
        <div className="absolute top-2 right-3 z-20 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500 dark:text-slate-400">
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 bg-emerald-500 inline-block" /> Live
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0 border-t border-dashed border-slate-400 inline-block" />{' '}
            Backtest OOS
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="w-3 h-2 inline-block"
              style={{ background: 'rgba(244,63,94,0.2)' }}
            />{' '}
            Drawdown
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-2 h-2 rounded-full bg-emerald-500" />
            <span>/</span>
            <span className="inline-block w-2 h-2 rounded-full bg-rose-500" />
            Trade exit (W / L)
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="inline-block w-2 h-2 bg-amber-500"
              style={{ transform: 'rotate(45deg)' }}
            />
            Financing / swap
          </span>
          {/* Wilson UAT 2026-07-25: direction markers removed — the
              StrategyChart candles below already surface trade
              direction against price, and the extra triangles cluttered
              the equity plot without adding signal. */}
        </div>

        <svg
          ref={svgRef}
          viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
          preserveAspectRatio="none"
          className="w-full h-full block"
          role="img"
          aria-label="Live equity vs backtest overlay with weekend gap band"
        >
          {/* ── Weekend / outage bands ─────────────────────────────── */}
          {gaps.map((g, i) => {
            const x1 = scaleX(g.startMs);
            const x2 = scaleX(g.endMs);
            const w = Math.max(1, x2 - x1);
            return (
              <g key={`gap-${i}`}>
                <rect
                  x={x1}
                  y={PADDING_TOP}
                  width={w}
                  height={PLOT_HEIGHT}
                  fill="currentColor"
                  className="text-slate-200 dark:text-slate-800"
                  opacity={0.55}
                />
                {w > 90 && (
                  <text
                    x={x1 + w / 2}
                    y={PADDING_TOP + 14}
                    textAnchor="middle"
                    fontSize={8}
                    fill="currentColor"
                    className="text-slate-500 dark:text-slate-500"
                    style={{ letterSpacing: '1px' }}
                  >
                    {g.isWeekend ? 'MARKET CLOSED' : 'GAP'}
                  </text>
                )}
              </g>
            );
          })}

          {/* ── Y-axis grid lines + labels ─────────────────────────── */}
          <g
            stroke="currentColor"
            strokeWidth={0.5}
            strokeDasharray="1 3"
            className="text-slate-300 dark:text-slate-700"
          >
            {yTicks.map((t, i) => (
              <line
                key={`hg-${i}`}
                x1={PADDING_LEFT}
                y1={t.y}
                x2={PADDING_LEFT + PLOT_WIDTH}
                y2={t.y}
              />
            ))}
          </g>
          <g
            fill="currentColor"
            fontSize={9}
            textAnchor="end"
            className="text-slate-500 dark:text-slate-500 tabular-nums"
          >
            {yTicks.map((t, i) => (
              <text key={`yl-${i}`} x={PADDING_LEFT - 5} y={t.y + 3}>
                {_fmtCurrencyShort(t.value)}
              </text>
            ))}
          </g>

          {/* ── Snapshot reference line (labelled) ─────────────────── */}
          <line
            x1={PADDING_LEFT}
            y1={scaleY(snapshotBalance)}
            x2={PADDING_LEFT + PLOT_WIDTH}
            y2={scaleY(snapshotBalance)}
            stroke="currentColor"
            strokeWidth={0.75}
            strokeDasharray="4 3"
            opacity={0.7}
            className="text-slate-400 dark:text-slate-500"
          />
          <text
            x={PADDING_LEFT + PLOT_WIDTH - 4}
            y={scaleY(snapshotBalance) - 4}
            textAnchor="end"
            fontSize={8}
            fill="currentColor"
            className="text-slate-500 dark:text-slate-400"
          >
            snapshot {_fmtCurrencyShort(snapshotBalance)}
          </text>

          {/* ── Drawdown shaded region (PR 5) ──────────────────────── */}
          {drawdownPath && (
            <path
              d={drawdownPath}
              fill="#f43f5e"
              opacity={0.1}
              stroke="none"
            />
          )}

          {/* ── Backtest OOS overlay (dashed) ──────────────────────── */}
          {backtestPath && (
            <path
              d={backtestPath}
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              strokeDasharray="4,3"
              className="text-slate-400 dark:text-slate-500"
            />
          )}

          {/* ── Live equity curve ──────────────────────────────────── */}
          {livePath && (
            <path
              d={livePath}
              fill="none"
              stroke="#10b981"
              strokeWidth={2}
            />
          )}

          {/* ── Current point marker ───────────────────────────────── */}
          {currentLive && (
            <circle
              cx={scaleX(currentLive.timestampMs)}
              cy={scaleY(currentLive.balance)}
              r={4}
              fill="#10b981"
            />
          )}

          {/* ── X-axis line + calendar-day ticks ───────────────────── */}
          <line
            x1={PADDING_LEFT}
            y1={PADDING_TOP + PLOT_HEIGHT}
            x2={PADDING_LEFT + PLOT_WIDTH}
            y2={PADDING_TOP + PLOT_HEIGHT}
            stroke="currentColor"
            strokeWidth={0.5}
            className="text-slate-400 dark:text-slate-600"
          />
          {xTicks.map((t, i) => {
            const x = scaleX(t.timestampMs);
            // Drop ticks that fall outside the plot rect.
            if (x < PADDING_LEFT || x > PADDING_LEFT + PLOT_WIDTH) return null;
            return (
              <g key={`xt-${i}`}>
                <line
                  x1={x}
                  y1={PADDING_TOP + PLOT_HEIGHT}
                  x2={x}
                  y2={PADDING_TOP + PLOT_HEIGHT + (t.minor ? 3 : 6)}
                  stroke="currentColor"
                  strokeWidth={0.5}
                  className="text-slate-400 dark:text-slate-600"
                />
                {!t.minor && (
                  <text
                    x={x}
                    y={PADDING_TOP + PLOT_HEIGHT + 16}
                    textAnchor="middle"
                    fontSize={9}
                    fill="currentColor"
                    className="text-slate-500 dark:text-slate-400"
                  >
                    {t.label}
                  </text>
                )}
                {t.sublabel && (
                  <text
                    x={x}
                    y={PADDING_TOP + PLOT_HEIGHT + 28}
                    textAnchor="middle"
                    fontSize={8}
                    fill="currentColor"
                    className="text-slate-400 dark:text-slate-500"
                  >
                    {t.sublabel}
                  </text>
                )}
              </g>
            );
          })}

          {/* ── Financing markers (Wilson UAT 2026-07-25) ──────────── */}
          {/* Rendered BEFORE trade dots so a coincident trade dot
              stacks on top (a rare case: the runner writes a
              close-fill sample within seconds of the daily financing
              debit — in practice the tolerance in
              `detectFinancingEvents` suppresses one or the other). */}
          <g>
            {financingEvents.map((ev, i) => {
              const x = scaleX(ev.timestampMs);
              if (x < PADDING_LEFT || x > PADDING_LEFT + PLOT_WIDTH) {
                return null;
              }
              const y = scaleY(ev.balance);
              const hovered = hoverFinancingIdx === i;
              const size = hovered ? 5 : 3.5;
              // Diamond via rotated square: 4-point polygon.
              const pts = `${x},${y - size} ${x + size},${y} ${x},${y + size} ${x - size},${y}`;
              return (
                <polygon
                  key={`fin-${i}`}
                  points={pts}
                  fill="#f59e0b"
                  stroke="white"
                  strokeWidth={1}
                  className="dark:stroke-slate-900"
                  style={{ cursor: 'help' }}
                  onPointerEnter={() => setHoverFinancingIdx(i)}
                  onPointerLeave={() => setHoverFinancingIdx(null)}
                >
                  <title>
                    Financing / swap on{' '}
                    {new Date(ev.timestampMs).toISOString().slice(0, 16)}Z ·{' '}
                    {ev.delta >= 0 ? '+' : ''}
                    {ev.delta.toFixed(2)} (balance {ev.balance.toFixed(2)})
                  </title>
                </polygon>
              );
            })}
          </g>

          {/* ── Trade exit markers (PR 3, revised 2026-07-25) ──────── */}
          {/* Wilson UAT 2026-07-25: direction (long/short) markers
              removed — the sibling StrategyChart owns direction
              analysis against price. Only exit circles remain
              here, positioned on the equity curve at (exitTs,
              equityAtExit) and coloured by W/L. Trades that have
              no P&L yet (null net_pnl AND null pnl — reconciler
              hasn't finalised) DON'T render a dot; a flat trade
              (exactly 0) renders neither W nor L colour so it
              doesn't get mistaken for a financing charge. */}
          <g>
            {projections.map((p) => {
              if (!p.exit) return null;
              // Only render dot when outcome is a definitive W or L.
              // Null (pending reconciliation) and 'flat' (exact 0)
              // are both skipped — they'd otherwise appear as grey
              // and be indistinguishable from a financing marker.
              if (p.outcome !== 'win' && p.outcome !== 'loss') return null;
              const inRange =
                p.exit.x >= PADDING_LEFT &&
                p.exit.x <= PADDING_LEFT + PLOT_WIDTH;
              if (!inRange) return null;
              const isHovered = p.tradeId === hoverTradeId;
              return (
                <circle
                  key={`m-${p.tradeId}`}
                  cx={p.exit.x}
                  cy={p.exit.y}
                  r={isHovered ? 5 : 3.5}
                  fill={p.outcome === 'win' ? '#10b981' : '#f43f5e'}
                  stroke="white"
                  strokeWidth={1}
                  className="dark:stroke-slate-900"
                  style={{ cursor: 'pointer' }}
                  onPointerEnter={() => setHoverTradeId(p.tradeId)}
                  onPointerLeave={() => setHoverTradeId(null)}
                  onClick={() => handleTradeClick(p.tradeId)}
                />
              );
            })}
          </g>

          {/* ── Hover crosshair (PR 2) ─────────────────────────────── */}
          {hoverPoint && hoverMs != null && (
            <g pointerEvents="none">
              <line
                x1={scaleX(hoverPoint.timestampMs)}
                y1={PADDING_TOP}
                x2={scaleX(hoverPoint.timestampMs)}
                y2={PADDING_TOP + PLOT_HEIGHT}
                stroke="currentColor"
                strokeWidth={0.75}
                strokeDasharray="2 2"
                className="text-slate-400 dark:text-slate-500"
              />
              <circle
                cx={scaleX(hoverPoint.timestampMs)}
                cy={scaleY(hoverPoint.balance)}
                r={4}
                fill="#10b981"
                stroke="white"
                strokeWidth={1.5}
                className="dark:stroke-slate-900"
              />
            </g>
          )}

          {/* ── Pointer capture rect ───────────────────────────────── */}
          <rect
            x={PADDING_LEFT}
            y={PADDING_TOP}
            width={PLOT_WIDTH}
            height={PLOT_HEIGHT}
            fill="transparent"
            style={{ cursor: 'crosshair' }}
            onPointerMove={handlePointerMove}
            onPointerLeave={handlePointerLeave}
          />
        </svg>

        {/* ── Floating tooltip (PR 2 crosshair + PR 3 marker swap) ── */}
        {hoveredTrade && hoveredProjection ? (
          <TradeTooltip
            trade={hoveredTrade}
            projection={hoveredProjection}
          />
        ) : (
          hoverPoint &&
          hoverMs != null && (
            <ChartTooltip
              hoverMs={hoverMs}
              hoverPoint={hoverPoint}
              hoverBacktest={hoverBacktest}
              hoverTradeStats={hoverTradeStats}
              runStartMs={runStartMs}
              snapshotBalance={snapshotBalance}
              scaleX={scaleX}
            />
          )
        )}
      </div>

      {/* ── Keyboard-shortcut hint (PR 4) ──────────────────────────── */}
      <div className="mt-2 text-[10px] text-slate-400 dark:text-slate-500 flex items-center gap-3">
        <span>
          <kbd className="px-1 py-0.5 border border-slate-300 dark:border-slate-600 rounded font-mono">
            1
          </kbd>{' '}
          24h
        </span>
        <span>
          <kbd className="px-1 py-0.5 border border-slate-300 dark:border-slate-600 rounded font-mono">
            2
          </kbd>{' '}
          7d
        </span>
        <span>
          <kbd className="px-1 py-0.5 border border-slate-300 dark:border-slate-600 rounded font-mono">
            A
          </kbd>{' '}
          All
        </span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Floating tooltip — sits over the chart at the crosshair X
// ─────────────────────────────────────────────────────────────────────

interface ChartTooltipProps {
  hoverMs: number;
  hoverPoint: EquityPoint;
  hoverBacktest: number | null;
  hoverTradeStats: {
    wins: number;
    losses: number;
    closed: number;
    realisedPnl: number;
  } | null;
  runStartMs: number;
  snapshotBalance: number;
  scaleX: (ms: number) => number;
}

function ChartTooltip({
  hoverMs,
  hoverPoint,
  hoverBacktest,
  hoverTradeStats,
  runStartMs,
  snapshotBalance,
  scaleX,
}: ChartTooltipProps) {
  // Position the tooltip near the crosshair X, flipping to the LEFT of
  // the cursor when we're past the plot midpoint so the tooltip never
  // extends off the right edge. The viewBox X → pixel offset uses the
  // container width via CSS `left: %` — accurate at any responsive
  // width without needing the SVG's actual bounding rect.
  const vbX = scaleX(hoverPoint.timestampMs);
  const leftFrac = vbX / VIEWBOX_WIDTH;
  const flipLeft = leftFrac > 0.55;
  const style: React.CSSProperties = flipLeft
    ? { right: `${(1 - leftFrac) * 100}%`, top: 40 }
    : { left: `${leftFrac * 100}%`, top: 40 };
  const nudge = flipLeft ? 'mr-2' : 'ml-2';

  // Tooltip title shows the sample's timestamp — matches the numbers
  // the body renders (which come from the snapped sample, not from
  // a hypothetical interpolated point under the cursor).
  const sampleMs = hoverPoint.timestampMs;
  const equity = hoverPoint.balance;
  const unreal = hoverPoint.unrealizedPnl;
  const balanceChange = equity - snapshotBalance;
  // Wilson UAT 2026-07-26: split account movement into its three
  // meaningful components rather than showing the raw balance
  // delta. `tradesRealised` = sum of closed-trade net_pnl up to
  // the cursor; `interestCharged` = residual (balance change
  // minus trade P&L) — typically overnight financing / swap, but
  // absorbs commission and any other non-trade account activity
  // too. Rendered separately so Wilson can eyeball how much the
  // strategy actually earned vs how much interest ate.
  const tradesRealised = hoverTradeStats?.realisedPnl ?? 0;
  const interestCharged = balanceChange - tradesRealised;
  const total = balanceChange + unreal;
  const drift = hoverBacktest != null ? equity - hoverBacktest : null;

  return (
    <div
      className={`absolute z-30 pointer-events-none ${nudge} min-w-[240px] rounded-md bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 shadow-lg text-xs font-mono tabular-nums`}
      style={style}
    >
      <div className="px-3 py-1.5 border-b border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-200 font-semibold">
        {_fmtTooltipDate(sampleMs)}
      </div>
      <div className="px-3 py-1.5 space-y-1">
        <TooltipRow
          label="Elapsed"
          value={formatElapsed(runStartMs, sampleMs)}
        />
        <TooltipRow
          label="Live equity"
          value={_fmtCurrency(equity)}
          tone={equity >= snapshotBalance ? 'up' : 'down'}
        />
        <TooltipRow
          label="Backtest OOS"
          value={hoverBacktest == null ? '—' : _fmtCurrency(hoverBacktest)}
          muted={hoverBacktest == null}
        />
        <TooltipRow
          label="Drift vs BT"
          value={drift == null ? '—' : _fmtSigned(drift)}
          tone={drift == null ? undefined : drift >= 0 ? 'up' : 'down'}
          muted={drift == null}
        />
        <div className="border-t border-slate-200 dark:border-slate-700 my-1" />
        <TooltipRow
          label="Total P&L"
          value={_fmtSigned(total)}
          tone={total >= 0 ? 'up' : 'down'}
        />
        <TooltipRow
          label="Realised"
          value={_fmtSigned(tradesRealised)}
          tone={tradesRealised >= 0 ? 'up' : 'down'}
        />
        <TooltipRow
          label="Interest charged"
          value={_fmtSigned(interestCharged)}
          tone={interestCharged >= 0 ? 'up' : 'down'}
        />
        <TooltipRow
          label="Unrealised"
          value={_fmtSigned(unreal)}
          tone={unreal >= 0 ? 'up' : 'down'}
        />
        <div className="border-t border-slate-200 dark:border-slate-700 my-1" />
        <TooltipRow
          label="Trades to date"
          value={
            hoverTradeStats == null
              ? '—'
              : `${hoverTradeStats.closed} · ${hoverTradeStats.wins}W / ${hoverTradeStats.losses}L`
          }
        />
      </div>
    </div>
  );
}

function TooltipRow({
  label,
  value,
  tone,
  muted,
}: {
  label: string;
  value: string;
  tone?: 'up' | 'down';
  muted?: boolean;
}) {
  const valueColor = muted
    ? 'text-slate-400 dark:text-slate-500'
    : tone === 'up'
      ? 'text-emerald-600 dark:text-emerald-400'
      : tone === 'down'
        ? 'text-rose-600 dark:text-rose-400'
        : 'text-slate-700 dark:text-slate-200';
  return (
    <div className="flex justify-between gap-4">
      <span className="text-slate-500 dark:text-slate-400">{label}</span>
      <span className={valueColor}>{value}</span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// TradeTooltip — PR 3, swaps in when a marker is hovered
// ─────────────────────────────────────────────────────────────────────

interface TradeTooltipProps {
  trade: PaperTradePublic;
  projection: import('../utils/livePnlChart').TradeProjection;
}

function TradeTooltip({ trade, projection }: TradeTooltipProps) {
  const isLong = trade.side === 'buy';
  const isOpen = trade.exit_timestamp == null;
  const pnl = trade.net_pnl ?? trade.pnl ?? 0;
  const pips = _computePipsMove(trade);
  const leftFrac = projection.entryX / VIEWBOX_WIDTH;
  const flipLeft = leftFrac > 0.55;
  const style: React.CSSProperties = flipLeft
    ? { right: `${(1 - leftFrac) * 100}%`, top: 40 }
    : { left: `${leftFrac * 100}%`, top: 40 };
  const nudge = flipLeft ? 'mr-2' : 'ml-2';

  return (
    <div
      className={`absolute z-30 pointer-events-none ${nudge} min-w-[260px] rounded-md bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 shadow-lg text-xs font-mono tabular-nums`}
      style={style}
    >
      <div className="px-3 py-1.5 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
        <span className="font-semibold text-slate-700 dark:text-slate-200">
          {isLong ? '▲ LONG' : '▼ SHORT'}{' '}
          <span className="text-slate-500 dark:text-slate-500 ml-1">
            {trade.quantity.toLocaleString()} units
          </span>
        </span>
        <span
          className={
            isOpen
              ? 'text-amber-600 dark:text-amber-400 text-[10px] uppercase'
              : 'text-slate-500 dark:text-slate-500 text-[10px] uppercase'
          }
        >
          {isOpen ? 'open' : trade.exit_reason ?? 'closed'}
        </span>
      </div>
      <div className="px-3 py-1.5 space-y-1">
        <TooltipRow
          label="Entry"
          value={`${_fmtTooltipDate(projection.entryTsMs)} · ${trade.entry_price.toFixed(5)}`}
        />
        <TooltipRow
          label="Exit"
          value={
            trade.exit_timestamp && trade.exit_price != null
              ? `${_fmtTooltipDate(new Date(trade.exit_timestamp).getTime())} · ${trade.exit_price.toFixed(5)}`
              : 'still open'
          }
          muted={isOpen}
        />
        {pips != null && (
          <TooltipRow
            label="Move"
            value={`${pips >= 0 ? '+' : ''}${pips.toFixed(1)} pips`}
            tone={pips >= 0 ? 'up' : 'down'}
          />
        )}
        {!isOpen && (
          <TooltipRow
            label="Net P&L"
            value={_fmtSigned(pnl)}
            tone={pnl >= 0 ? 'up' : 'down'}
          />
        )}
        {trade.financing != null && trade.financing !== 0 && (
          <TooltipRow
            label="Financing"
            value={_fmtSigned(trade.financing)}
            tone={trade.financing >= 0 ? 'up' : 'down'}
          />
        )}
        {trade.commission != null && trade.commission !== 0 && (
          <TooltipRow
            label="Commission"
            value={`-${Math.abs(trade.commission).toFixed(2)}`}
            tone="down"
          />
        )}
        {trade.signal_reason && (
          <div className="pt-1 border-t border-slate-200 dark:border-slate-700 text-[10px] text-slate-500 dark:text-slate-400 leading-tight break-words">
            <span className="uppercase mr-1">Signal:</span>
            {trade.signal_reason}
          </div>
        )}
        <div className="pt-1 text-[10px] text-slate-400 dark:text-slate-500 italic">
          Click to jump to the trade row.
        </div>
      </div>
    </div>
  );
}

/** Pip move for the trade — takes JPY vs non-JPY pip size from the
 *  instrument's quote-currency slot. Returns null for open trades. */
function _computePipsMove(trade: PaperTradePublic): number | null {
  if (trade.exit_price == null) return null;
  // We don't have the instrument on the trade payload, but every
  // trade table cell already assumes 0.0001 as a safe default and
  // handles JPY separately in the sibling column formatting. The
  // chart tooltip mirrors that convention.
  const pipSize = 0.0001;
  const sign = trade.side === 'buy' ? 1 : -1;
  return ((trade.exit_price - trade.entry_price) * sign) / pipSize;
}

function _fmtTooltipDate(ms: number): string {
  const d = new Date(ms);
  const wk = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getUTCDay()];
  const day = d.getUTCDate().toString().padStart(2, '0');
  const mon = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ][d.getUTCMonth()];
  const hh = d.getUTCHours().toString().padStart(2, '0');
  const mm = d.getUTCMinutes().toString().padStart(2, '0');
  return `${wk} ${day} ${mon} · ${hh}:${mm} UTC`;
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────

interface SummaryTileProps {
  label: string;
  value: React.ReactNode;
  valueClass?: string;
  sublabel?: string;
}

interface OverlayUnavailableChipProps {
  reason: BacktestOverlayReason | null;
}

function OverlayUnavailableChip({ reason }: OverlayUnavailableChipProps) {
  return (
    <div
      role="status"
      className="mb-3 flex items-start gap-2 rounded border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-xs text-amber-800 dark:text-amber-200"
    >
      <span className="mt-0.5 font-semibold">Backtest OOS overlay unavailable</span>
      <span className="text-amber-700 dark:text-amber-300">
        {_overlayReasonMessage(reason)}
      </span>
    </div>
  );
}

/** Human-readable explanation for each `BacktestOverlayReason`.
 *  Exported so a future test can lock the wording without spinning
 *  up a DOM. */
export function _overlayReasonMessage(
  reason: BacktestOverlayReason | null,
): string {
  switch (reason) {
    case 'no_associated_evolution_run':
      return "This run isn't linked to an evolution — the overlay needs a source genome to pull the walk-forward OOS equity from.";
    case 'no_oos_equity_persisted':
      return 'No OOS folds were persisted for the source genome, so there is no walk-forward equity to draw.';
    case 'oos_equity_series_not_persisted':
      return "The OOS equity-series column isn't populated on the ValidationFold rows for this genome. That per-fold curve was planned for v0.4.0 but never landed; the overlay will remain unavailable until the schema + evolution runner both persist it.";
    case null:
    default:
      return 'The overlay endpoint returned no data.';
  }
}

interface RangePillProps {
  active: boolean;
  onClick: () => void;
  label: string;
  shortcut: string;
}

function RangePill({ active, onClick, label, shortcut }: RangePillProps) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={`px-3 py-1.5 border-l first:border-l-0 border-slate-200 dark:border-slate-700 transition-colors ${
        active
          ? 'bg-slate-100 dark:bg-slate-700 text-slate-900 dark:text-slate-100 font-semibold'
          : 'text-slate-500 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800'
      }`}
      title={`Keyboard shortcut: ${shortcut}`}
    >
      {label}
    </button>
  );
}

function SummaryTile({ label, value, valueClass, sublabel }: SummaryTileProps) {
  return (
    <div className="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded p-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-500 dark:text-slate-500">
        {label}
      </div>
      <div
        className={`text-base font-mono font-semibold mt-0.5 tabular-nums ${valueClass ?? 'text-slate-800 dark:text-slate-100'}`}
      >
        {value}
      </div>
      {sublabel && (
        <div className="text-[10px] text-slate-500 dark:text-slate-500 mt-0.5">
          {sublabel}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Pure helpers (exposed for Vitest)
// ─────────────────────────────────────────────────────────────────────

/**
 * Translate a mouse `clientX` into a wall-clock timestamp along the
 * chart's X-axis. The SVG is rendered at a variable pixel width but
 * has a fixed viewBox; scaling by `rectWidth / viewBoxWidth` maps
 * screen pixels back to viewBox units, then linear-inversed with the
 * plot rect gives us the fraction of `spanMs` under the cursor.
 *
 * Extracted for Vitest so PR 2's hover interaction is testable
 * without spinning up a DOM. Clamps to [runStartMs, runStartMs +
 * spanMs] so the crosshair never wanders past the visible range.
 */
export interface ClientXToTimestampArgs {
  clientX: number;
  rectLeft: number;
  rectWidth: number;
  runStartMs: number;
  spanMs: number;
  paddingLeft: number;
  plotWidth: number;
  viewBoxWidth: number;
}

export function _clientXToTimestamp(a: ClientXToTimestampArgs): number {
  if (a.rectWidth <= 0 || a.viewBoxWidth <= 0 || a.spanMs <= 0) {
    return a.runStartMs;
  }
  const scale = a.rectWidth / a.viewBoxWidth;
  const vbX = (a.clientX - a.rectLeft) / scale;
  const fracInPlot = (vbX - a.paddingLeft) / a.plotWidth;
  const clampedFrac = Math.min(1, Math.max(0, fracInPlot));
  return a.runStartMs + clampedFrac * a.spanMs;
}

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
  // Wilson UAT 2026-07-25: switched from a straight-line join to a
  // "step-after" path. Realised balance only changes at trade-close
  // moments; the samples in between hold the previous balance flat.
  // A straight-line join between two samples across a close event
  // draws a slanted line that visually implies balance drifted
  // between measurements — the truth is that balance stayed at A
  // until the close, then jumped to B. Step-after models that:
  // horizontal segment at A until the next sample's x, then a
  // vertical jump to B, then continue horizontally.
  if (!series.length) return null;
  return _stepAfterPath(series, scaleX, scaleY);
}

/** Build a step-after SVG path string. Exported for Vitest. */
export function _stepAfterPath(
  series: EquityPoint[],
  scaleX: (ms: number) => number,
  scaleY: (balance: number) => number,
): string {
  if (series.length === 0) return '';
  const segs: string[] = [];
  const first = series[0];
  let prevY = scaleY(first.balance);
  segs.push(`M${scaleX(first.timestampMs).toFixed(1)},${prevY.toFixed(1)}`);
  for (let i = 1; i < series.length; i++) {
    const p = series[i];
    const x = scaleX(p.timestampMs).toFixed(1);
    const y = scaleY(p.balance);
    // Horizontal segment to (x, prevY), then vertical to (x, y).
    // When y === prevY the vertical L is a no-op; SVG collapses it.
    segs.push(`L${x},${prevY.toFixed(1)}`);
    segs.push(`L${x},${y.toFixed(1)}`);
    prevY = y;
  }
  return segs.join(' ');
}

// ─────────────────────────────────────────────────────────────────────
// Format helpers — small, local, no need to test in isolation
// ─────────────────────────────────────────────────────────────────────

function _fmtCurrency(v: number): string {
  const sign = v < 0 ? '-' : '';
  return `${sign}$${Math.abs(v).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

/** Compact currency for axis labels: `$100,150` (no cents). */
function _fmtCurrencyShort(v: number): string {
  const sign = v < 0 ? '-' : '';
  return `${sign}$${Math.abs(v).toLocaleString('en-US', {
    maximumFractionDigits: 0,
  })}`;
}

function _fmtSigned(v: number): string {
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  return `${sign}$${Math.abs(v).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function _fmtSignedPct(v: number): string {
  const sign = v > 0 ? '+' : v < 0 ? '' : '';
  return `${sign}${v.toFixed(2)}%`;
}

function _pnlColor(v: number): string | undefined {
  if (v > 0) return 'text-emerald-600 dark:text-emerald-400';
  if (v < 0) return 'text-rose-600 dark:text-rose-400';
  return undefined;
}

/** Short calendar label for summary tile sublabels — `Wed 14:00`. */
function _fmtShortDate(ms: number): string {
  const d = new Date(ms);
  const wk = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getUTCDay()];
  const day = d.getUTCDate().toString().padStart(2, '0');
  const hh = d.getUTCHours().toString().padStart(2, '0');
  const mm = d.getUTCMinutes().toString().padStart(2, '0');
  return `${wk} ${day} ${hh}:${mm}`;
}

/** Sublabel for the Trades summary tile — current + max streaks
 *  across the WHOLE run. Wilson UAT 2026-07-25: previous format
 *  "1W streak · max 2W / 7L" got misread as "1-week view", the "W"
 *  looking like a week abbreviation rather than "win". Now uses
 *  plain words and an explicit "whole run" qualifier so it can't
 *  be mistaken for the visible-range summary. */
function _streakSublabel(streaks: import('../utils/livePnlChart').StreakStats): string | undefined {
  const { current, maxWinStreak, maxLossStreak } = streaks;
  if (current == null) return undefined;
  const currentLabel =
    current.kind === 'win'
      ? `${current.count} win${current.count === 1 ? '' : 's'} in a row`
      : current.kind === 'loss'
        ? `${current.count} loss${current.count === 1 ? '' : 'es'} in a row`
        : `${current.count} flat`;
  const maxParts: string[] = [];
  if (maxWinStreak > 0)
    maxParts.push(`${maxWinStreak} win${maxWinStreak === 1 ? '' : 's'}`);
  if (maxLossStreak > 0)
    maxParts.push(`${maxLossStreak} loss${maxLossStreak === 1 ? '' : 'es'}`);
  const maxLabel = maxParts.length ? ` · max ${maxParts.join(' / ')} (whole run)` : '';
  return `${currentLabel}${maxLabel}`;
}

function _sideLabel(side: string): string {
  return side === 'buy' ? 'LONG' : side === 'sell' ? 'SHORT' : side.toUpperCase();
}

/** Re-export the coercion helper for pages that fetch the raw
 *  `EquitySnapshotPublic` API shape and want to hand normalised
 *  `EquityPoint`s to the chart. */
export { toEquityPoints };
