/**
 * Pure helpers for the redesigned `LivePnLChart` (v0.6.3).
 *
 * The chart itself is a React component full of SVG; keeping the
 * data-shaping in this module means it can be locked with Vitest in
 * the project's `node` environment (no jsdom, no
 * `@testing-library/react`) — same pattern already established by
 * `_scaleX` / `_scaleY` in `LivePnLChart.tsx`, `_buildRiskConfig` in
 * `DeployPaperModal.tsx`, `_pickEmptyBannerScenario` in
 * `ApprovalQueue.tsx`, `_canSubmit` in `ValidatorOverrideModal.tsx`.
 *
 * PR 1 (axis + summary strip) uses:
 * - `detectGaps` — weekend + ingestion-outage bands.
 * - `pickAxisTicks` — calendar-day ticks that replace `h0 → h167`.
 * - `computeSummaryStrip` — 8 tiles above the chart.
 *
 * PR 2-6 will add `nearestSample`, `projectTrade`, `filterByRange`,
 * `computeDrawdownSeries`, `interpolateOverlay`. Each helper takes
 * primitive inputs so the tests match `deployPaperModalRiskConfig.test.ts`.
 */

import type { EquitySnapshotPublic, PaperTradePublic } from '../types/paper';

// ─────────────────────────────────────────────────────────────────────
// Shared shapes
// ─────────────────────────────────────────────────────────────────────

/** One point on the live equity curve — timestamps normalized to ms. */
export interface EquityPoint {
  timestampMs: number;
  balance: number;
  unrealizedPnl: number;
}

/** Weekend / outage band on the chart canvas. */
export interface GapBand {
  /** Left edge in wall-clock ms. */
  startMs: number;
  /** Right edge in wall-clock ms. */
  endMs: number;
  /** True when the band corresponds to the FX-market closed window.
   *  Computed from calendar (Fri 22:00 UTC → Sun 22:00 UTC), NOT
   *  from data gaps — so the "MARKET CLOSED" label lines up with
   *  actual market hours rather than the last-sample-before-gap
   *  drift Wilson caught on 2026-07-25. */
  isWeekend: boolean;
}

// FX market weekly close/open, expressed in UTC:
// - Fri 22:00 UTC (17:00 NY close in DST)
// - Sun 22:00 UTC (17:00 NY open / Sydney open)
// Non-DST months shift by 1 hour but the majority of trading weeks
// use this window; the ±1h drift is acceptable for a visual band.
const WEEKEND_CLOSE_DOW_UTC = 5; // Friday
const WEEKEND_CLOSE_HOUR_UTC = 22;
const WEEKEND_OPEN_DOW_UTC = 0; // Sunday
const WEEKEND_OPEN_HOUR_UTC = 22;

/** Values consumed by the 8-tile summary strip above the chart. */
export interface SummaryStripData {
  liveBalance: number;
  realisedPnl: number;
  unrealisedPnl: number;
  maxDrawdownPct: number;
  /** Wall-clock window (start / end ms) over which the max DD was
   *  measured. Presented as a sub-line in the tile. */
  maxDrawdownWindow: { startMs: number; endMs: number } | null;
  tradeCount: number;
  closedCount: number;
  openCount: number;
  winRate: number | null;
  wins: number;
  losses: number;
  bestTrade: PaperTradePublic | null;
  worstTrade: PaperTradePublic | null;
}

/** A single tick on the calendar-day x-axis. */
export interface AxisTick {
  /** ms-since-epoch of the tick */
  timestampMs: number;
  /** Primary label, e.g. `Mon 13` or `13:00`. */
  label: string;
  /** Optional sub-label; on the `all` range the second row shows
   *  `Jul` at month boundaries. Absent otherwise. */
  sublabel?: string;
  /** True for minor ticks (dimmer, no label unless it's a boundary). */
  minor?: boolean;
}

// ─────────────────────────────────────────────────────────────────────
// Coercion — one place to normalise the API's ISO strings
// ─────────────────────────────────────────────────────────────────────

/**
 * Convert the wire-shape `EquitySnapshotPublic` (timestamps as ISO
 * strings) into the ms-based `EquityPoint` the chart maths uses.
 * Extracted so tests can pin the conversion behaviour without
 * standing up a whole component.
 */
export function toEquityPoints(items: EquitySnapshotPublic[]): EquityPoint[] {
  return items.map((s) => ({
    timestampMs: new Date(s.timestamp).getTime(),
    balance: s.balance,
    unrealizedPnl: s.unrealized_pnl ?? 0,
  }));
}

// ─────────────────────────────────────────────────────────────────────
// detectGaps — weekend + outage bands
// ─────────────────────────────────────────────────────────────────────

/** Anything longer than 6h between adjacent equity samples is a gap
 *  worth flagging. M1 signal tick cadence is 60s; even a run that
 *  survives a laptop lid-close should recover in minutes, so 6h
 *  cleanly separates real halts / weekends from routine jitter. */
export const DEFAULT_MIN_GAP_MS = 6 * 60 * 60 * 1000;

/** Two shaded bands whose visible gap ≤ this threshold get merged
 *  into a single visual band. Prevents the "confusing 2h break"
 *  Wilson caught on 2026-07-25 where the runner didn't pick up
 *  trading until ~2h after Sunday's market re-open — the calendar
 *  weekend band ended at Sun 22:00 UTC, then the runner-outage band
 *  started at Mon 00:00 UTC, rendering two adjacent dark regions
 *  with a thin trapezoidal live-strip between them. 4h is tight
 *  enough that only near-adjacent bands merge; a mid-week outage
 *  that happens 10+ hours after the previous weekend stays as its
 *  own GAP band. */
export const BAND_MERGE_GAP_MS = 4 * 60 * 60 * 1000;

/**
 * Return timestamp windows where the chart should render a shaded
 * band. Combines two sources:
 *
 * 1. **Weekend bands** — computed from the calendar. Every Fri
 *    22:00 UTC → Sun 22:00 UTC window intersecting `[axisStartMs,
 *    axisEndMs]` becomes an `isWeekend: true` band. This makes the
 *    "MARKET CLOSED" label line up with actual FX market hours —
 *    NOT with the last-sample-before-gap drift Wilson caught on
 *    2026-07-25 where the previous gap-based band ended near Mon
 *    00:00 instead of Sun 22:00.
 * 2. **Outage bands** — data gaps > `minGapMs` between adjacent
 *    equity samples that DO NOT overlap a weekend window. These
 *    label as "GAP" and cover unexpected runner outages / ingester
 *    failures.
 *
 * Assumes `equity` is sorted ascending by `timestampMs`.
 */
export function detectGaps(
  equity: EquityPoint[],
  minGapMs: number = DEFAULT_MIN_GAP_MS,
  axisStartMs?: number,
  axisEndMs?: number,
): GapBand[] {
  // Determine the visible range for weekend calculation. Fall back
  // to the equity series bounds when not passed (older callers).
  let rangeStart = axisStartMs;
  let rangeEnd = axisEndMs;
  if (rangeStart == null && equity.length > 0) {
    rangeStart = equity[0].timestampMs;
  }
  if (rangeEnd == null && equity.length > 0) {
    rangeEnd = equity[equity.length - 1].timestampMs;
  }

  const weekends: GapBand[] =
    rangeStart != null && rangeEnd != null
      ? _weekendBandsInRange(rangeStart, rangeEnd)
      : [];

  const outages: GapBand[] = [];
  for (let i = 1; i < equity.length; i++) {
    const startMs = equity[i - 1].timestampMs;
    const endMs = equity[i].timestampMs;
    const duration = endMs - startMs;
    if (duration < minGapMs) continue;
    // Suppress gaps that mostly (> 50%) overlap a weekend band —
    // the weekend already renders as MARKET CLOSED, no need for
    // a second GAP label on top. Wilson UAT 2026-07-25: the prior
    // "fully contained" check let outages that straddled the
    // weekend by a few minutes on either edge escape and render
    // as a bigger GAP band on top of the weekend.
    const overlapWithWeekend = weekends.reduce((sum, w) => {
      const overlap = Math.max(
        0,
        Math.min(w.endMs, endMs) - Math.max(w.startMs, startMs),
      );
      return sum + overlap;
    }, 0);
    if (overlapWithWeekend / duration > 0.5) continue;
    outages.push({ startMs, endMs, isWeekend: false });
  }

  // Merge + sort by startMs so the chart renders left-to-right.
  const sorted = [...weekends, ...outages].sort(
    (a, b) => a.startMs - b.startMs,
  );
  // Collapse near-adjacent bands into one visual region so the eye
  // doesn't see a suspicious 2h "live" strip separating a MARKET
  // CLOSED band from an immediately-following outage band. If any of
  // the merged bands is a weekend, the merged band is a weekend
  // (labels as MARKET CLOSED) — extended-halt-after-weekend reads
  // more naturally as "market was closed" than "gap in trading".
  return _mergeAdjacentBands(sorted, BAND_MERGE_GAP_MS);
}

/**
 * Collapse bands separated by <= `mergeGapMs` into a single band.
 * Assumes input is sorted ascending by `startMs`. Exported for
 * Vitest.
 */
export function _mergeAdjacentBands(
  bands: GapBand[],
  mergeGapMs: number,
): GapBand[] {
  if (bands.length === 0) return [];
  const merged: GapBand[] = [{ ...bands[0] }];
  for (let i = 1; i < bands.length; i++) {
    const prev = merged[merged.length - 1];
    const curr = bands[i];
    if (curr.startMs - prev.endMs <= mergeGapMs) {
      prev.endMs = Math.max(prev.endMs, curr.endMs);
      prev.isWeekend = prev.isWeekend || curr.isWeekend;
    } else {
      merged.push({ ...curr });
    }
  }
  return merged;
}

/** Every Fri 22:00 UTC → Sun 22:00 UTC window (inclusive of edges)
 *  intersecting `[rangeStart, rangeEnd]`. Bands are clipped to the
 *  visible range so the chart doesn't over-draw.
 *
 *  Exported for Vitest. */
export function _weekendBandsInRange(
  rangeStart: number,
  rangeEnd: number,
): GapBand[] {
  if (rangeEnd < rangeStart) return [];
  const bands: GapBand[] = [];
  // Find the Friday of the week containing rangeStart (or the week
  // before, if rangeStart is before Fri of its own week).
  const startD = new Date(rangeStart);
  // Rewind to the Friday at or before rangeStart.
  const dow = startD.getUTCDay();
  // Days to subtract to reach the Friday of this week (or last week).
  // Fri (5) → 0; Sat (6) → 1; Sun (0) → 2; Mon (1) → 3; Tue (2) → 4;
  // Wed (3) → 5; Thu (4) → 6.
  const daysBackToFri = (dow - WEEKEND_CLOSE_DOW_UTC + 7) % 7;
  const friAtOrBeforeStart = Date.UTC(
    startD.getUTCFullYear(),
    startD.getUTCMonth(),
    startD.getUTCDate() - daysBackToFri,
    WEEKEND_CLOSE_HOUR_UTC,
    0,
    0,
  );

  const oneWeekMs = 7 * 24 * 60 * 60 * 1000;
  const twoDaysMs = 2 * 24 * 60 * 60 * 1000;

  for (
    let friClose = friAtOrBeforeStart;
    friClose <= rangeEnd;
    friClose += oneWeekMs
  ) {
    const sunOpen = friClose + twoDaysMs; // Fri 22 + 48h = Sun 22
    // Skip weekends entirely outside the visible range.
    if (sunOpen < rangeStart || friClose > rangeEnd) continue;
    bands.push({
      startMs: Math.max(friClose, rangeStart),
      endMs: Math.min(sunOpen, rangeEnd),
      isWeekend: true,
    });
  }
  return bands;
}

// ─────────────────────────────────────────────────────────────────────
// pickAxisTicks — calendar-day x-axis
// ─────────────────────────────────────────────────────────────────────

export type RangeKey = '24h' | '7d' | 'all';

/** Weekday short-name lookup keyed by `Date.getUTCDay()`. */
const WEEKDAY_UTC = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH_UTC = [
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
];

/**
 * Produce human-readable x-axis ticks for the chart's visible range.
 *
 * - `24h`: one tick every 3 hours, labelled `HH:00`. Sub-label
 *   `Mon 13` on any tick that crosses a UTC midnight.
 * - `7d`: one tick per UTC day at 00:00, labelled `Mon 13`. Minor
 *   ticks at 12:00 (unlabelled) to keep the eye anchored.
 * - `all`: one tick per UTC day at 00:00, labelled `13`. Month name
 *   as a sub-label on the first tick of each month.
 *
 * `end` is exclusive-in-spirit — the last tick equal to `end` is
 * kept, but ticks past it are dropped.
 */
export function pickAxisTicks(
  startMs: number,
  endMs: number,
  rangeKey: RangeKey,
): AxisTick[] {
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) {
    return [];
  }
  if (rangeKey === '24h') return _pickTicks24h(startMs, endMs);
  if (rangeKey === '7d') return _pickTicks7d(startMs, endMs);
  return _pickTicksAll(startMs, endMs);
}

function _pickTicks24h(startMs: number, endMs: number): AxisTick[] {
  const ticks: AxisTick[] = [];
  const stepMs = 3 * 60 * 60 * 1000; // 3-hour spacing
  // Snap to the next 3-hour UTC boundary at/after startMs.
  const firstTs = _snapToUtcBoundary(startMs, stepMs);
  for (let t = firstTs; t <= endMs; t += stepMs) {
    const d = new Date(t);
    const hour = d.getUTCHours();
    ticks.push({
      timestampMs: t,
      label: `${hour.toString().padStart(2, '0')}:00`,
      sublabel: hour === 0 ? _weekdayLabel(t) : undefined,
    });
  }
  return ticks;
}

function _pickTicks7d(startMs: number, endMs: number): AxisTick[] {
  const ticks: AxisTick[] = [];
  const dayMs = 24 * 60 * 60 * 1000;
  // Snap to next UTC midnight.
  const firstMidnight = _snapToUtcMidnight(startMs);
  for (let t = firstMidnight; t <= endMs; t += dayMs) {
    ticks.push({
      timestampMs: t,
      label: _weekdayLabel(t),
    });
    // Minor tick at noon (12h later), unlabelled.
    const noon = t + 12 * 60 * 60 * 1000;
    if (noon <= endMs) {
      ticks.push({ timestampMs: noon, label: '', minor: true });
    }
  }
  return ticks;
}

function _pickTicksAll(startMs: number, endMs: number): AxisTick[] {
  const ticks: AxisTick[] = [];
  const dayMs = 24 * 60 * 60 * 1000;
  const spanDays = Math.ceil((endMs - startMs) / dayMs);
  // Ratchet down tick density on long spans so labels don't collide.
  // <=14 days → daily; <=90 days → every 3 days; else weekly.
  const stride = spanDays <= 14 ? 1 : spanDays <= 90 ? 3 : 7;
  const firstMidnight = _snapToUtcMidnight(startMs);
  let seenMonth: number | null = null;
  for (let i = 0, t = firstMidnight; t <= endMs; i++, t += dayMs * stride) {
    const d = new Date(t);
    const month = d.getUTCMonth();
    const showMonth = month !== seenMonth;
    seenMonth = month;
    ticks.push({
      timestampMs: t,
      label: d.getUTCDate().toString(),
      sublabel: showMonth ? MONTH_UTC[month] : undefined,
    });
  }
  return ticks;
}

/** Round `ts` up to the next multiple of `stepMs` in UTC. */
function _snapToUtcBoundary(ts: number, stepMs: number): number {
  const remainder = ts % stepMs;
  return remainder === 0 ? ts : ts + (stepMs - remainder);
}

/** Round `ts` up to the next UTC midnight. */
function _snapToUtcMidnight(ts: number): number {
  const d = new Date(ts);
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth();
  const day = d.getUTCDate();
  const hour = d.getUTCHours();
  const min = d.getUTCMinutes();
  const sec = d.getUTCSeconds();
  const ms = d.getUTCMilliseconds();
  if (hour === 0 && min === 0 && sec === 0 && ms === 0) return ts;
  return Date.UTC(y, m, day + 1);
}

function _weekdayLabel(ts: number): string {
  const d = new Date(ts);
  return `${WEEKDAY_UTC[d.getUTCDay()]} ${d.getUTCDate().toString().padStart(2, '0')}`;
}

// ─────────────────────────────────────────────────────────────────────
// computeSummaryStrip — 8 tiles above the chart
// ─────────────────────────────────────────────────────────────────────

/**
 * Reduce equity + trades into the eight numbers rendered above the
 * chart. Everything memoisable so the React render doesn't recompute
 * on every hover-position update.
 *
 * All numbers are in home currency (AUD for Wilson's staging), not
 * pips. The chart component is responsible for formatting.
 */
export function computeSummaryStrip(
  equity: EquityPoint[],
  trades: PaperTradePublic[],
  snapshotBalance: number,
): SummaryStripData {
  const liveBalance =
    equity.length > 0 ? equity[equity.length - 1].balance : snapshotBalance;
  const unrealisedPnl =
    equity.length > 0 ? equity[equity.length - 1].unrealizedPnl : 0;

  const closedTrades = trades.filter(
    (t) => t.exit_timestamp != null && t.pnl != null,
  );
  const openTrades = trades.filter((t) => t.exit_timestamp == null);

  const realisedPnl = closedTrades.reduce(
    (sum, t) => sum + (t.net_pnl ?? t.pnl ?? 0),
    0,
  );

  const wins = closedTrades.filter((t) => (t.net_pnl ?? t.pnl ?? 0) > 0).length;
  const losses = closedTrades.filter(
    (t) => (t.net_pnl ?? t.pnl ?? 0) < 0,
  ).length;
  const winRate =
    closedTrades.length > 0 ? wins / closedTrades.length : null;

  let bestTrade: PaperTradePublic | null = null;
  let worstTrade: PaperTradePublic | null = null;
  for (const t of closedTrades) {
    const p = t.net_pnl ?? t.pnl ?? 0;
    if (bestTrade == null || p > (bestTrade.net_pnl ?? bestTrade.pnl ?? 0)) {
      bestTrade = t;
    }
    if (worstTrade == null || p < (worstTrade.net_pnl ?? worstTrade.pnl ?? 0)) {
      worstTrade = t;
    }
  }

  const { maxDrawdownPct, window } = _computeMaxDrawdown(
    equity,
    snapshotBalance,
  );

  return {
    liveBalance,
    realisedPnl,
    unrealisedPnl,
    maxDrawdownPct,
    maxDrawdownWindow: window,
    tradeCount: trades.length,
    closedCount: closedTrades.length,
    openCount: openTrades.length,
    winRate,
    wins,
    losses,
    bestTrade,
    worstTrade,
  };
}

// ─────────────────────────────────────────────────────────────────────
// nearestSample + interpolateOverlay + tradesUpTo (PR 2)
// ─────────────────────────────────────────────────────────────────────

/**
 * Return the equity sample whose timestamp is closest to `targetMs`,
 * or null if the series is empty. Binary search since the series is
 * already sorted ascending by the API. Used by the hover crosshair
 * to snap the tooltip dot to the actual curve rather than a
 * hypothetical interpolated point.
 */
export function nearestSample(
  equity: EquityPoint[],
  targetMs: number,
): EquityPoint | null {
  if (equity.length === 0) return null;
  if (equity.length === 1) return equity[0];
  // Clamp to bounds so hover past the last sample snaps to the last.
  if (targetMs <= equity[0].timestampMs) return equity[0];
  if (targetMs >= equity[equity.length - 1].timestampMs) {
    return equity[equity.length - 1];
  }
  let lo = 0;
  let hi = equity.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >>> 1;
    if (equity[mid].timestampMs < targetMs) lo = mid;
    else hi = mid;
  }
  // lo and hi bracket targetMs — pick whichever is closer.
  return targetMs - equity[lo].timestampMs <=
    equity[hi].timestampMs - targetMs
    ? equity[lo]
    : equity[hi];
}

/**
 * Linearly interpolate the backtest overlay's balance at `targetMs`.
 * Returns `null` when the overlay is empty or `targetMs` sits
 * outside the overlay's span (no extrapolation — the tooltip should
 * show `—` for backtest OOS in those regions).
 *
 * The overlay is already sorted ascending; binary search is fine.
 */
export function interpolateOverlay(
  overlay: EquityPoint[],
  targetMs: number,
): number | null {
  if (overlay.length === 0) return null;
  if (targetMs < overlay[0].timestampMs) return null;
  if (targetMs > overlay[overlay.length - 1].timestampMs) return null;
  if (overlay.length === 1) return overlay[0].balance;
  let lo = 0;
  let hi = overlay.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >>> 1;
    if (overlay[mid].timestampMs <= targetMs) lo = mid;
    else hi = mid;
  }
  const a = overlay[lo];
  const b = overlay[hi];
  if (b.timestampMs === a.timestampMs) return a.balance;
  const t = (targetMs - a.timestampMs) / (b.timestampMs - a.timestampMs);
  return a.balance + t * (b.balance - a.balance);
}

// ─────────────────────────────────────────────────────────────────────
// computeDrawdownSeries (PR 5) — running-peak underwater shape
// ─────────────────────────────────────────────────────────────────────

export interface DrawdownPoint {
  timestampMs: number;
  /** Running maximum balance up to and including this point. */
  peakBalance: number;
  /** Actual balance at this point (echo of the equity sample). */
  balance: number;
  /** Non-positive: 0 when at-peak, negative when underwater. In %. */
  drawdownPct: number;
}

/**
 * Walk the equity series left-to-right, tracking the running peak.
 * Emits one `DrawdownPoint` per sample. Consumers (the chart's
 * shaded band, the summary strip's max-DD tile) can either plot the
 * `peakBalance` vs `balance` region as a filled polygon or reduce
 * `drawdownPct` to the min for the run-total figure.
 *
 * `initialPeak` seeds the running peak — pass `snapshotBalance` so
 * a perpetually-underwater run still shows the correct drawdown
 * from the snapshot line rather than degenerating to 0 for the
 * first samples.
 */
export function computeDrawdownSeries(
  equity: EquityPoint[],
  initialPeak: number,
): DrawdownPoint[] {
  const out: DrawdownPoint[] = [];
  let peak = initialPeak;
  for (const p of equity) {
    if (p.balance > peak) peak = p.balance;
    const dd = peak > 0 ? (p.balance - peak) / peak : 0;
    out.push({
      timestampMs: p.timestampMs,
      peakBalance: peak,
      balance: p.balance,
      drawdownPct: dd * 100,
    });
  }
  return out;
}

// ─────────────────────────────────────────────────────────────────────
// filterByRange (PR 4) — range selector filter
// ─────────────────────────────────────────────────────────────────────

/**
 * Client-side filter over the equity + trades arrays for the range
 * pill selector. The chart re-renders with a narrower window without
 * needing a new fetch — the runner writes every sample; the "last
 * 24h" view is just a slice of what's already in memory.
 *
 * Window semantics:
 * - `24h` → [nowMs - 24h, nowMs], clamped to runStartMs on the left.
 * - `7d`  → [nowMs - 7d, nowMs], clamped to runStartMs on the left.
 * - `all` → [runStartMs, nowMs].
 *
 * Trades are included when the trade's timeline touches the window
 * — i.e. entry ≤ end AND (exit == null OR exit ≥ start). Straddling
 * trades stay visible so their markers don't wink out at the window
 * boundary.
 *
 * Returns a bounded `{startMs, endMs}` that the chart uses as the
 * X-axis anchor and span. Never returns endMs ≤ startMs — collapses
 * to a 60s minimum window so the axis is still legible.
 */
export interface RangeFilterInput {
  rangeKey: RangeKey;
  runStartMs: number;
  nowMs: number;
  equity: EquityPoint[];
  trades: PaperTradePublic[];
}

export interface RangeFilterOutput {
  startMs: number;
  endMs: number;
  equity: EquityPoint[];
  trades: PaperTradePublic[];
}

const ONE_DAY_MS = 24 * 60 * 60 * 1000;

export function filterByRange(input: RangeFilterInput): RangeFilterOutput {
  const { rangeKey, runStartMs, nowMs, equity, trades } = input;
  let startMs: number;
  const endMs = nowMs;
  if (rangeKey === '24h') {
    startMs = Math.max(runStartMs, nowMs - ONE_DAY_MS);
  } else if (rangeKey === '7d') {
    startMs = Math.max(runStartMs, nowMs - 7 * ONE_DAY_MS);
  } else {
    startMs = runStartMs;
  }
  // Guarantee a legible axis even on brand-new runs (or if the input
  // window degenerates for any reason).
  const safeEnd = Math.max(endMs, startMs + 60_000);

  const filteredEquity = equity.filter(
    (p) => p.timestampMs >= startMs && p.timestampMs <= safeEnd,
  );
  const filteredTrades = trades.filter((t) => {
    const entryMs = new Date(t.entry_timestamp).getTime();
    if (entryMs > safeEnd) return false;
    if (t.exit_timestamp == null) return true; // still open — always show
    const exitMs = new Date(t.exit_timestamp).getTime();
    return exitMs >= startMs;
  });
  return {
    startMs,
    endMs: safeEnd,
    equity: filteredEquity,
    trades: filteredTrades,
  };
}

// ─────────────────────────────────────────────────────────────────────
// projectTrade (PR 3) — marker geometry
// ─────────────────────────────────────────────────────────────────────

export interface TradeProjection {
  tradeId: string;
  side: 'buy' | 'sell';
  /** Entry marker triangle apex position (at the bottom of the plot). */
  entryX: number;
  entryTsMs: number;
  /** Exit circle position on the equity curve. Null when the trade is
   *  still open. */
  exit: { x: number; y: number; balance: number } | null;
  /** Realised P&L sign — drives the exit circle colour. Null when
   *  the trade is open. */
  outcome: 'win' | 'loss' | 'flat' | null;
}

/**
 * Project a `PaperTradePublic` into pixel coordinates for the SVG.
 * The exit circle sits at the equity value AT the exit timestamp
 * (from the nearest sample in `liveSeries`), so it always lands
 * exactly on the drawn curve.
 *
 * Trades whose entry falls outside the visible range (e.g. before
 * the range selector's window) are still projected — the chart
 * component decides whether to render them by testing against the
 * plot rect.
 */
export function projectTrade(
  trade: PaperTradePublic,
  liveSeries: EquityPoint[],
  scaleX: (ms: number) => number,
  scaleY: (balance: number) => number,
): TradeProjection {
  const entryMs = new Date(trade.entry_timestamp).getTime();
  const entryX = scaleX(entryMs);
  let exit: TradeProjection['exit'] = null;
  let outcome: TradeProjection['outcome'] = null;
  if (trade.exit_timestamp) {
    const exitMs = new Date(trade.exit_timestamp).getTime();
    const nearest = nearestSample(liveSeries, exitMs);
    const balance =
      nearest?.balance ??
      // Very defensive fallback for an exit outside the equity series
      // range — synthesise a balance from trade quantities. In
      // practice `nearest` is virtually always non-null since the
      // exit tick writes an equity snapshot.
      trade.exit_price ?? 0;
    exit = { x: scaleX(exitMs), y: scaleY(balance), balance };
    const p = trade.net_pnl ?? trade.pnl ?? 0;
    outcome = p > 0 ? 'win' : p < 0 ? 'loss' : 'flat';
  }
  return {
    tradeId: trade.id,
    side: trade.side === 'buy' ? 'buy' : 'sell',
    entryX,
    entryTsMs: entryMs,
    exit,
    outcome,
  };
}

/**
 * Cumulative interest / financing charged from run start to the
 * cursor. Wilson UAT 2026-07-26: the prior residual formula
 * `balanceChange - tradesRealised` was non-monotonic — a trade
 * close moves financing that had been "residual" into that trade's
 * `net_pnl` (which is `pnl + financing - commission`), so the
 * residual snapped back toward zero at every close. Wrong shape:
 * Wilson wanted a running total that only grows (more negative)
 * over time as more swap is paid.
 *
 * Two sources for a strictly-cumulative number:
 *   - Every closed trade's `financing` field, summed for trades
 *     whose `exit_timestamp <= cutoffMs`. Captures financing that
 *     was accrued during the trade's hold and posted at close.
 *   - Every `FinancingEvent` (balance delta not matched to a trade
 *     close), summed for `timestampMs <= cutoffMs`. Captures the
 *     daily overnight swap that hits the account while a position
 *     is still open (no close event nearby to attribute it to).
 *
 * The two sources do NOT overlap: `detectFinancingEvents` explicitly
 * suppresses balance changes that fall within 2min of any trade's
 * `exit_timestamp`, so a close-event financing hit is counted only
 * via the trade row.
 *
 * Sign convention matches the underlying fields — negative for a
 * charge (typical), positive for a credit (rare short-position
 * swap). The tooltip renders with `_fmtSigned` which prefixes the
 * sign.
 */
export function interestChargedUpTo(
  trades: PaperTradePublic[],
  events: FinancingEvent[],
  cutoffMs: number,
): number {
  let sum = 0;
  for (const t of trades) {
    if (!t.exit_timestamp) continue;
    const exitMs = new Date(t.exit_timestamp).getTime();
    if (exitMs > cutoffMs) continue;
    sum += t.financing ?? 0;
  }
  for (const e of events) {
    if (e.timestampMs > cutoffMs) break;
    sum += e.delta;
  }
  return sum;
}

/** Count wins / losses among trades with `exit_timestamp <= cutoffMs`.
 *  Also sums realised net_pnl so the tooltip's "Realised (trades)"
 *  row can render an honest number that agrees with the summary
 *  strip tile (both compute from the trades array, not from balance
 *  delta — balance delta additionally reflects overnight financing
 *  and any external account activity).
 *
 *  Used by the tooltip's "Trades to date · W / L" row so the number
 *  reflects the moment the cursor is over, not the run-total. */
export function tradesUpTo(
  trades: PaperTradePublic[],
  cutoffMs: number,
): { wins: number; losses: number; closed: number; realisedPnl: number } {
  let wins = 0;
  let losses = 0;
  let closed = 0;
  let realisedPnl = 0;
  for (const t of trades) {
    if (!t.exit_timestamp) continue;
    const exitMs = new Date(t.exit_timestamp).getTime();
    if (exitMs > cutoffMs) continue;
    closed++;
    const p = t.net_pnl ?? t.pnl ?? 0;
    realisedPnl += p;
    if (p > 0) wins++;
    else if (p < 0) losses++;
  }
  return { wins, losses, closed, realisedPnl };
}

// ─────────────────────────────────────────────────────────────────────
// detectFinancingEvents — balance changes not caused by a trade close
// ─────────────────────────────────────────────────────────────────────

export interface FinancingEvent {
  /** Millisecond timestamp of the equity sample where the change
   *  first became visible. */
  timestampMs: number;
  /** Balance AT this event (after the delta). */
  balance: number;
  /** Change from the prior sample. Negative = charge, positive =
   *  credit (rare but possible on OANDA short positions). */
  delta: number;
}

/**
 * Wilson UAT 2026-07-25: the chart's balance line jumps at every
 * OANDA account-balance change. Most jumps are trade closes and get
 * a green/red exit-dot marker. Overnight financing (swap) also
 * changes the balance without a trade close — no dot, so the jump
 * looked "unexplained". This helper attributes every balance-change
 * sample to either a trade close (matched exit_timestamp within
 * `toleranceMs`) or a financing event. The chart renders the
 * financing events as small amber diamonds so the eye can tell
 * "this drop was overnight swap" from "this drop was a losing
 * trade".
 *
 * Threshold: any balance change ≥ $0.10 that isn't within
 * `toleranceMs` of a closed-trade `exit_timestamp`.
 */
export function detectFinancingEvents(
  equity: EquityPoint[],
  trades: PaperTradePublic[],
  toleranceMs: number = 2 * 60_000,
): FinancingEvent[] {
  if (equity.length < 2) return [];
  const exitTimes = trades
    .filter((t) => t.exit_timestamp != null)
    .map((t) => new Date(t.exit_timestamp!).getTime())
    .sort((a, b) => a - b);

  const events: FinancingEvent[] = [];
  for (let i = 1; i < equity.length; i++) {
    const prev = equity[i - 1];
    const curr = equity[i];
    const delta = curr.balance - prev.balance;
    if (Math.abs(delta) < 0.1) continue;
    // Binary search for a trade exit within tolerance of curr.timestampMs.
    let lo = 0;
    let hi = exitTimes.length - 1;
    let hasNearbyTrade = false;
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1;
      const diff = exitTimes[mid] - curr.timestampMs;
      if (Math.abs(diff) <= toleranceMs) {
        hasNearbyTrade = true;
        break;
      }
      if (diff < 0) lo = mid + 1;
      else hi = mid - 1;
    }
    if (!hasNearbyTrade) {
      events.push({
        timestampMs: curr.timestampMs,
        balance: curr.balance,
        delta,
      });
    }
  }
  return events;
}

// ─────────────────────────────────────────────────────────────────────
// computeStreaks — consecutive W/L runs for the Trades summary tile
// ─────────────────────────────────────────────────────────────────────

export interface StreakStats {
  /** The currently-active streak (based on the most recently closed
   *  trade). `null` when there are no closed trades. Flat trades
   *  (net_pnl == 0) break both streaks — a flat sequence has
   *  `kind: 'flat'`, count = 1 (or however many consecutive flats). */
  current: { kind: 'win' | 'loss' | 'flat'; count: number } | null;
  /** Longest winning streak ever recorded across all closed trades. */
  maxWinStreak: number;
  /** Longest losing streak ever recorded across all closed trades. */
  maxLossStreak: number;
}

/**
 * Compute current + max consecutive-win / consecutive-loss streaks
 * across a run's closed trades. Wilson UAT 2026-07-25: an "N W in
 * a row" or "M L in a row" is a much sharper signal than "win rate
 * 60%" when watching a live paper run — you want to spot a
 * regime-change losing streak the moment it starts, not average it
 * across the whole run.
 *
 * Reads `net_pnl` (post financing / commission), falls back to
 * `pnl` for legacy rows. Trades are re-sorted internally by
 * `exit_timestamp` ascending so the caller doesn't need to
 * pre-sort — the equity endpoint returns oldest-first but callers
 * that mix sources shouldn't have to know.
 */
export function computeStreaks(trades: PaperTradePublic[]): StreakStats {
  const closed = trades
    .filter((t) => t.exit_timestamp != null)
    .slice()
    .sort(
      (a, b) =>
        new Date(a.exit_timestamp!).getTime() -
        new Date(b.exit_timestamp!).getTime(),
    );

  let current: StreakStats['current'] = null;
  let maxWinStreak = 0;
  let maxLossStreak = 0;
  let runningKind: 'win' | 'loss' | 'flat' | null = null;
  let runningCount = 0;

  for (const t of closed) {
    const p = t.net_pnl ?? t.pnl ?? 0;
    const kind: 'win' | 'loss' | 'flat' =
      p > 0 ? 'win' : p < 0 ? 'loss' : 'flat';
    if (kind === runningKind) {
      runningCount += 1;
    } else {
      runningKind = kind;
      runningCount = 1;
    }
    if (kind === 'win' && runningCount > maxWinStreak) {
      maxWinStreak = runningCount;
    }
    if (kind === 'loss' && runningCount > maxLossStreak) {
      maxLossStreak = runningCount;
    }
    current = { kind, count: runningCount };
  }

  return { current, maxWinStreak, maxLossStreak };
}

/**
 * Running-peak drawdown across the equity curve. Result is a
 * NEGATIVE percentage (or 0 if no drawdown occurred). Includes the
 * window over which the DD was measured — from the peak to the
 * lowest subsequent point — so the tile's sub-line can render
 * `Thu 06 → Fri 11:30`.
 */
function _computeMaxDrawdown(
  equity: EquityPoint[],
  snapshotBalance: number,
): { maxDrawdownPct: number; window: { startMs: number; endMs: number } | null } {
  if (equity.length === 0) return { maxDrawdownPct: 0, window: null };
  let peak = snapshotBalance;
  let peakMs = equity[0].timestampMs;
  let maxDd = 0;
  let worstStartMs: number | null = null;
  let worstEndMs: number | null = null;
  for (const p of equity) {
    if (p.balance > peak) {
      peak = p.balance;
      peakMs = p.timestampMs;
    }
    const dd = peak > 0 ? (p.balance - peak) / peak : 0;
    if (dd < maxDd) {
      maxDd = dd;
      worstStartMs = peakMs;
      worstEndMs = p.timestampMs;
    }
  }
  return {
    maxDrawdownPct: maxDd * 100,
    window:
      worstStartMs != null && worstEndMs != null
        ? { startMs: worstStartMs, endMs: worstEndMs }
        : null,
  };
}
