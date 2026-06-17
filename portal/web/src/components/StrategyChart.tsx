/**
 * StrategyChart Component
 *
 * Single chart with candlesticks, trade markers, and indicators.
 * - Price-scale overlays (MA, MACD, Donchian, Ichimoku, BB, ATR, OBV):
 *   drawn on the right (candle) price scale
 * - Bounded oscillators (RSI, Stoch, ADX — see OSCILLATOR_NAMES below):
 *   rendered on a separate 'left' price scale in the bottom portion
 *
 * Using ONE chart instance guarantees time alignment between all series.
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  createChart,
  IChartApi,
  ISeriesApi,
  CandlestickData,
  LineData,
  UTCTimestamp,
  CrosshairMode,
  SeriesMarker,
  Time,
  LineStyle,
} from 'lightweight-charts';
import { ZoomIn, ZoomOut, Maximize2 } from 'lucide-react';
import { CandleData, TradeSignal, IndicatorLine } from '../types/optimization';
import {
  OSCILLATOR_NAMES,
  classifyIndicator,
  offScaleId,
} from '../utils/indicatorScales';

interface StrategyChartProps {
  candles: CandleData[];
  signals: TradeSignal[];
  indicators: IndicatorLine[];
  instrument: string;
  strategy: string;
  params: Record<string, number>;
  timeframe?: string;
  isLoading?: boolean;
  error?: string | null;
  /**
   * Optional default visible bars. When set, after the data + markers
   * are loaded the chart zooms to show the *last* N bars rather than
   * fitting all of them in view. Lets the chart render readably even
   * for very-long M1 windows (e.g. a 9-month genome backtest = 277K
   * bars; auto-fit would compress them to a flat strip). User can still
   * pan/zoom freely from the default view.
   */
  defaultVisibleBars?: number;
}

const toTime = (ts: string): UTCTimestamp =>
  (new Date(ts).getTime() / 1000) as UTCTimestamp;

interface ScaleMargins {
  top: number;
  bottom: number;
}

/**
 * Compute per-scale vertical layout based on which indicator groups
 * the spec actually uses. Returns the candle scale's bottom margin
 * (how much vertical space is reserved for sub-panels), the
 * oscillator scale's margins, and a map of off-scale group → margins.
 *
 * Reasoning: the chart has ONE shared time axis but multiple value
 * scales. Without dedicated margins, an OBV series on the candle
 * scale auto-fits the price axis to span [0, ~100000] and the
 * candles collapse to a flat strip — the UAT bug reported
 * 2026-06-17.
 */
function computeLayout(
  hasOsc: boolean,
  offScaleGroups: string[],
): {
  candleMargins: ScaleMargins;
  oscMargins: ScaleMargins | null;
  offScaleMargins: Map<string, ScaleMargins>;
} {
  const nBottom = (hasOsc ? 1 : 0) + offScaleGroups.length;
  if (nBottom === 0) {
    return {
      candleMargins: { top: 0.02, bottom: 0.02 },
      oscMargins: null,
      offScaleMargins: new Map(),
    };
  }
  // Reserve 30% of the chart for a single sub-panel, 50% if more than one.
  const bottomReserved = nBottom === 1 ? 0.3 : 0.5;
  const sectionHeight = (bottomReserved - 0.05) / nBottom; // 5% inter-section gap
  const candleMargins: ScaleMargins = {
    top: 0.02,
    bottom: bottomReserved,
  };
  let cursor = 1 - bottomReserved + 0.03;
  let oscMargins: ScaleMargins | null = null;
  const offScaleMargins = new Map<string, ScaleMargins>();
  if (hasOsc) {
    oscMargins = { top: cursor, bottom: 1 - (cursor + sectionHeight) };
    cursor += sectionHeight + 0.02;
  }
  for (const id of offScaleGroups) {
    offScaleMargins.set(id, {
      top: cursor,
      bottom: Math.max(0.02, 1 - (cursor + sectionHeight)),
    });
    cursor += sectionHeight + 0.02;
  }
  return { candleMargins, oscMargins, offScaleMargins };
}

export default function StrategyChart({
  candles, signals, indicators, instrument, strategy, params,
  timeframe, isLoading = false, error = null, defaultVisibleBars,
}: StrategyChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const overlayRefs = useRef<ISeriesApi<'Line'>[]>([]);
  const oscRefs = useRef<ISeriesApi<'Line'>[]>([]);
  // Each entry is { scaleId, series } — kept alongside the scale ID so
  // we can address each off-scale series for tooltip lookups by group.
  const offScaleRefs = useRef<
    Array<{ scaleId: string; label: string; series: ISeriesApi<'Line'> }>
  >([]);

  const [chartError, setChartError] = useState<string | null>(null);
  const [showIndicators, setShowIndicators] = useState(true);
  const [tooltip, setTooltip] = useState<{
    time: string; o: string; h: string; l: string; c: string;
    osc?: { label: string; value: string };
    offScales?: Array<{ label: string; value: string }>;
  } | null>(null);

  // Three render-scale buckets — see utils/indicatorScales.ts.
  // Off-scale indicators (obv, macd_line/signal) must NOT share the
  // candle scale; each off-scale group gets its own overlay scale at
  // the bottom of the chart so its auto-fit can't squash the candles.
  const overlays = indicators.filter((i) => classifyIndicator(i.name) === 'price');
  const oscillators = indicators.filter(
    (i) => classifyIndicator(i.name) === 'oscillator',
  );
  const offScales = indicators.filter(
    (i) => classifyIndicator(i.name) === 'off_scale',
  );
  const hasOsc = oscillators.length > 0;
  // De-duplicated list of off-scale group IDs (e.g. 'macd' is shared
  // by macd_line and macd_signal so they auto-fit together).
  const offScaleGroups = Array.from(
    new Set(
      offScales
        .map((i) => offScaleId(i.name))
        .filter((id): id is string => id !== null),
    ),
  );
  const hasOff = offScaleGroups.length > 0;
  const layout = computeLayout(hasOsc, offScaleGroups);

  // ── Chart init ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current) return;
    try {
      const dark = document.documentElement.classList.contains('dark');
      const chart = createChart(containerRef.current, {
        layout: { background: { color: 'transparent' }, textColor: dark ? '#9ca3af' : '#6b7280' },
        grid: {
          vertLines: { color: dark ? '#1f2937' : '#e5e7eb' },
          horzLines: { color: dark ? '#1f2937' : '#e5e7eb' },
        },
        width: containerRef.current.clientWidth,
        // Bigger height when any sub-panel is present so the candles
        // still have a usable vertical range. 700px when osc + off-scale
        // are both stacked at the bottom; 600 when one is present; 500
        // when none.
        height: hasOsc && hasOff ? 700 : hasOsc || hasOff ? 600 : 500,
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: dark ? '#374151' : '#d1d5db' },
        rightPriceScale: {
          borderColor: dark ? '#374151' : '#d1d5db',
          scaleMargins: layout.candleMargins,
        },
        leftPriceScale: {
          visible: hasOsc,
          borderColor: dark ? '#374151' : '#d1d5db',
          scaleMargins: layout.oscMargins ?? { top: 0.75, bottom: 0.02 },
        },
        crosshair: {
          mode: CrosshairMode.Normal,
          vertLine: { color: dark ? '#6b7280' : '#9ca3af', width: 1 as const, style: 1 as const, labelBackgroundColor: dark ? '#374151' : '#e5e7eb' },
          horzLine: { color: dark ? '#6b7280' : '#9ca3af', width: 1 as const, style: 1 as const, labelBackgroundColor: dark ? '#374151' : '#e5e7eb' },
        },
      });

      const series = chart.addCandlestickSeries({
        upColor: '#22c55e', downColor: '#ef4444',
        borderUpColor: '#22c55e', borderDownColor: '#ef4444',
        wickUpColor: '#22c55e', wickDownColor: '#ef4444',
      });
      chartRef.current = chart;
      candleRef.current = series;

      // Crosshair tooltip
      chart.subscribeCrosshairMove(param => {
        if (!param.time || !param.point) { setTooltip(null); return; }
        const d = param.seriesData.get(series) as any;
        if (d?.open === undefined) { setTooltip(null); return; }
        // Pull the first oscillator's current value, if any. Label tracks
        // the indicator name (e.g. rsi/stoch/adx) so the tooltip says
        // what the user is actually looking at.
        let osc: { label: string; value: string } | undefined;
        if (oscRefs.current.length > 0 && oscillators[0]) {
          const oscData = param.seriesData.get(oscRefs.current[0]) as any;
          if (oscData?.value !== undefined) {
            osc = { label: oscillators[0].name, value: oscData.value.toFixed(1) };
          }
        }
        // Surface every off-scale indicator's current value with the
        // same precision the value warrants — MACD is around ±0.001
        // for FX so 6dp; OBV is in the tens of thousands so integer.
        // Both render as a labelled chip in the tooltip strip.
        const offScalesTooltip: Array<{ label: string; value: string }> = [];
        for (const { label, scaleId, series: offSeries } of offScaleRefs.current) {
          const d = param.seriesData.get(offSeries) as any;
          if (d?.value === undefined) continue;
          const formatted =
            scaleId === 'obv'
              ? Math.round(d.value).toLocaleString()
              : d.value.toFixed(6);
          offScalesTooltip.push({ label, value: formatted });
        }
        setTooltip({
          time: new Date((param.time as number) * 1000).toLocaleString('en-US', { timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          o: d.open.toFixed(5), h: d.high.toFixed(5), l: d.low.toFixed(5), c: d.close.toFixed(5),
          osc,
          offScales: offScalesTooltip.length > 0 ? offScalesTooltip : undefined,
        });
      });

      const onResize = () => {
        if (containerRef.current && chartRef.current)
          chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      };
      window.addEventListener('resize', onResize);
      return () => {
        window.removeEventListener('resize', onResize);
        chart.remove();
        chartRef.current = null;
        candleRef.current = null;
        overlayRefs.current = [];
        oscRefs.current = [];
        offScaleRefs.current = [];
      };
    } catch (err) {
      setChartError(err instanceof Error ? err.message : 'Failed to init chart');
    }
    // Recreate chart when the *shape* of the layout changes — i.e.
    // gaining/losing an osc panel or gaining/losing a distinct
    // off-scale group. We join offScaleGroups into a stable string so
    // the effect re-fires for any add/remove/reorder.
  }, [hasOsc, hasOff, offScaleGroups.join('|')]);

  // ── Candle data + markers ──────────────────────────────────────────
  useEffect(() => {
    if (!candleRef.current || candles.length === 0) return;
    try {
      const data: CandlestickData[] = candles
        .map(c => ({ time: toTime(c.timestamp), open: c.open, high: c.high, low: c.low, close: c.close }))
        .sort((a, b) => (a.time as number) - (b.time as number));
      candleRef.current.setData(data);

      const tMin = data[0].time as number;
      const tMax = data[data.length - 1].time as number;
      const filtered = signals.filter(s => { const t = toTime(s.timestamp); return t >= tMin && t <= tMax; });

      // Exit P&L map
      const exitPnl = new Map<string, number>();
      let lastEntry: { price: number; dir: string } | null = null;
      for (const s of filtered) {
        if (s.signalType.startsWith('entry')) { lastEntry = { price: s.price, dir: s.signalType.includes('long') ? 'long' : 'short' }; }
        else if (lastEntry && s.price) {
          exitPnl.set(s.timestamp, lastEntry.dir === 'long' ? (s.price - lastEntry.price) / 0.0001 : (lastEntry.price - s.price) / 0.0001);
          lastEntry = null;
        }
      }

      const markers: SeriesMarker<Time>[] = filtered.map(sig => {
        const isEntry = sig.signalType.startsWith('entry');
        const isLong = sig.signalType.includes('long');
        if (isEntry) {
          // Annotate the entry marker with whichever indicator carried
          // the trigger value the backend recorded against this signal.
          // `indicatorValues` is captured at the SIGNAL bar (one bar
          // before the entry bar — the no-lookahead shift in
          // src/agentfx/core/dsl/compiler.py:317), so what shows here
          // is exactly the value the spec evaluated to fire the trade.
          //
          // Skip `close` because it's just price (already on the chart
          // axis) and prefer non-price-scale indicators that the user
          // actually needs the value of to verify the trigger
          // (oscillators, MACD, OBV). Falls back to whatever first
          // entry exists.
          const allValues = Object.entries(sig.indicatorValues ?? {});
          const nonClose = allValues.filter(([k]) => k.toLowerCase() !== 'close');
          // Prefer the most-diagnostic indicator family in order:
          // oscillator → off-scale → first-available. For an OBV
          // genome this picks OBV; for RSI it picks RSI; for an MA
          // crossover it picks whichever MA was captured first.
          const pick =
            nonClose.find(([k]) => classifyIndicator(k) === 'oscillator') ??
            nonClose.find(([k]) => classifyIndicator(k) === 'off_scale') ??
            nonClose[0];
          const label = isLong ? 'L' : 'S';
          // Magnitude-aware formatting: OBV is thousands → integer
          // with thousands-separator; MACD is ±0.001 → 4dp; everything
          // else → 1dp (RSI 67.4, SMA 1.0850).
          let text = label;
          if (pick) {
            const v = Number(pick[1]);
            const abs = Math.abs(v);
            const formatted =
              abs >= 1000
                ? Math.round(v).toLocaleString()
                : abs < 0.01
                  ? v.toFixed(4)
                  : v.toFixed(1);
            text = `${label} ${formatted}`;
          }
          const position: 'belowBar' | 'aboveBar' = isLong ? 'belowBar' : 'aboveBar';
          const shape: 'arrowUp' | 'arrowDown' = isLong ? 'arrowUp' : 'arrowDown';
          return { time: toTime(sig.timestamp), position, color: isLong ? '#22c55e' : '#ef4444', shape, text, size: 1 };
        }
        const pips = sig.pnl != null ? sig.pnl * 10000 : exitPnl.get(sig.timestamp) ?? 0;
        const win = pips > 0;
        return { time: toTime(sig.timestamp), position: 'aboveBar' as const, color: win ? '#22c55e' : '#ef4444', shape: 'circle' as const, text: pips !== 0 ? (win ? `+${pips.toFixed(0)}` : pips.toFixed(0)) : 'X', size: 1 };
      });
      markers.sort((a, b) => (a.time as number) - (b.time as number));
      candleRef.current.setMarkers(markers);
      // Default view: if the caller passed `defaultVisibleBars` and the
      // dataset is bigger than that, scroll to the LAST N bars so the
      // candles render at a useful density. Setting `barSpacing` first
      // forces the chart to allocate at least 1px per bar — without this,
      // lightweight-charts auto-fits all 277K bars into the available
      // pixel width on first paint, which silently overrides any
      // subsequent setVisibleLogicalRange call. Otherwise auto-fit the
      // whole dataset, matching the legacy behaviour trials rely on.
      const applyDefaultView = () => {
        const chart = chartRef.current;
        if (!chart) return;
        if (
          defaultVisibleBars != null &&
          candles.length > defaultVisibleBars
        ) {
          const ts = chart.timeScale();
          // Force a minimum bar width so the chart can't compress 277K
          // bars into a flat strip. With barSpacing=3 and a typical
          // chart width ~1500px, ~500 bars fit on screen — readable.
          ts.applyOptions({ barSpacing: 3, rightOffset: 12 });
          ts.scrollToPosition(0, false); // 0 = latest bar
        } else {
          chart.timeScale().fitContent();
        }
      };
      // Defer past the current task so lightweight-charts has finished
      // its internal layout for setData before we override the view.
      requestAnimationFrame(applyDefaultView);
      setChartError(null);
    } catch (err) {
      setChartError(err instanceof Error ? err.message : 'Failed to update chart');
    }
  }, [candles, signals, defaultVisibleBars]);

  // ── Overlay indicators (MA, Donchian) ──────────────────────────────
  useEffect(() => {
    if (!chartRef.current) return;
    overlayRefs.current.forEach(s => { try { chartRef.current?.removeSeries(s); } catch { /* */ } });
    overlayRefs.current = [];
    if (!showIndicators || overlays.length === 0) return;
    overlays.forEach(ind => {
      const ls = chartRef.current!.addLineSeries({ color: ind.color, lineWidth: 2, title: ind.name, priceLineVisible: false, lastValueVisible: false });
      ls.setData(ind.data.map(p => ({ time: toTime(p.timestamp), value: p.value } as LineData)).sort((a, b) => (a.time as number) - (b.time as number)));
      overlayRefs.current.push(ls);
    });
  }, [indicators, showIndicators]);

  // ── Oscillator indicators (RSI) on left price scale ────────────────
  useEffect(() => {
    if (!chartRef.current) return;
    oscRefs.current.forEach(s => { try { chartRef.current?.removeSeries(s); } catch { /* */ } });
    oscRefs.current = [];
    if (!showIndicators || oscillators.length === 0) return;

    const dark = document.documentElement.classList.contains('dark');
    oscillators.forEach(ind => {
      const ls = chartRef.current!.addLineSeries({
        color: ind.color, lineWidth: 2, title: ind.name,
        priceScaleId: 'left',
        priceLineVisible: false, lastValueVisible: false,
      });
      ls.setData(ind.data.map(p => ({ time: toTime(p.timestamp), value: p.value } as LineData)).sort((a, b) => (a.time as number) - (b.time as number)));
      oscRefs.current.push(ls);
    });

    // Reference lines at the trial's actual oversold/overbought thresholds
    const oversold = params.oversold ?? params.lower ?? 30;
    const overbought = params.overbought ?? params.upper ?? 70;
    if (candles.length > 0) {
      const sorted = [...candles].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
      const t0 = toTime(sorted[0].timestamp);
      const t1 = toTime(sorted[sorted.length - 1].timestamp);
      const refOpts = {
        lineWidth: 1 as const, lineStyle: LineStyle.Dashed,
        color: dark ? '#4b5563' : '#9ca3af',
        priceScaleId: 'left' as const,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: '',
      };
      const lo = chartRef.current!.addLineSeries(refOpts);
      lo.setData([{ time: t0, value: oversold }, { time: t1, value: oversold }] as LineData[]);
      const hi = chartRef.current!.addLineSeries(refOpts);
      hi.setData([{ time: t0, value: overbought }, { time: t1, value: overbought }] as LineData[]);
      oscRefs.current.push(lo, hi);
    }
  }, [indicators, showIndicators, candles, params]);

  // ── Off-scale indicators (OBV, MACD) on dedicated overlay scales ────
  // Each off-scale GROUP (macd line + signal share 'macd'; obv alone)
  // gets its own priceScaleId so its auto-fit is isolated from both
  // the candle scale (avoiding the OBV-squashes-candles bug) and from
  // other off-scale groups (so OBV's tens-of-thousands magnitude can't
  // squash MACD's ±0.001 range).
  useEffect(() => {
    if (!chartRef.current) return;
    offScaleRefs.current.forEach((s) => {
      try { chartRef.current?.removeSeries(s.series); } catch { /* */ }
    });
    offScaleRefs.current = [];
    if (!showIndicators || offScales.length === 0) return;

    for (const ind of offScales) {
      const scaleId = offScaleId(ind.name);
      if (!scaleId) continue;
      const ls = chartRef.current!.addLineSeries({
        color: ind.color,
        lineWidth: 2,
        title: ind.name,
        priceScaleId: scaleId,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      ls.setData(
        ind.data
          .map((p) => ({ time: toTime(p.timestamp), value: p.value } as LineData))
          .sort((a, b) => (a.time as number) - (b.time as number)),
      );
      offScaleRefs.current.push({ scaleId, label: ind.name, series: ls });
    }
    // Apply scaleMargins per group so each occupies its own vertical
    // strip. Lightweight-charts creates overlay scales on demand when
    // a series references them, so it's safe to applyOptions here.
    for (const scaleId of offScaleGroups) {
      const margins = layout.offScaleMargins.get(scaleId);
      if (!margins) continue;
      chartRef.current!.priceScale(scaleId).applyOptions({
        scaleMargins: margins,
        // Keep the overlay scale visible so the user can read the
        // magnitude — without this lightweight-charts hides overlay
        // scales by default and the line is unlabelled.
        visible: true,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [indicators, showIndicators, candles]);

  // ── Zoom ───────────────────────────────────────────────────────────
  const zoom = (factor: number) => {
    const ts = chartRef.current?.timeScale();
    const r = ts?.getVisibleLogicalRange();
    if (!ts || !r) return;
    const c = (r.from + r.to) / 2, h = (r.to - r.from) / 2 * factor;
    ts.setVisibleLogicalRange({ from: c - h, to: c + h });
  };

  // ── States ─────────────────────────────────────────────────────────
  if (isLoading) return <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6"><div className="flex items-center justify-center h-[500px]"><div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600" /></div></div>;
  const displayError = error || chartError;
  if (displayError) return <div className="bg-white dark:bg-slate-800 border border-red-200 dark:border-red-800 rounded-lg p-6"><div className="flex items-center justify-center h-[500px]"><p className="text-sm text-red-600">{displayError}</p></div></div>;
  if (candles.length === 0) return <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6"><div className="flex items-center justify-center h-[500px]"><p className="text-sm text-slate-600">No chart data</p></div></div>;

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-slate-200 dark:border-slate-700 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">{instrument}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Strategy: {strategy.toUpperCase()}{timeframe ? ` | Timeframe: ${timeframe}` : ''} | Params: {Object.entries(params).map(([k, v]) => `${k}=${v}`).join(', ')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowIndicators(!showIndicators)} className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors ${showIndicators ? 'bg-blue-50 border-blue-200 text-blue-700 dark:bg-blue-900/30 dark:border-blue-800 dark:text-blue-400' : 'bg-slate-50 border-slate-200 text-slate-700 dark:bg-slate-700 dark:border-slate-600 dark:text-slate-300'}`}>
            {showIndicators ? 'Hide Indicators' : 'Show Indicators'}
          </button>
          <div className="h-4 w-px bg-slate-200 dark:bg-slate-700" />
          <button onClick={() => zoom(0.5)} className="p-1.5 rounded-md hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-400" title="Zoom In"><ZoomIn className="w-4 h-4" /></button>
          <button onClick={() => zoom(2)} className="p-1.5 rounded-md hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-400" title="Zoom Out"><ZoomOut className="w-4 h-4" /></button>
          <button onClick={() => chartRef.current?.timeScale().fitContent()} className="p-1.5 rounded-md hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-400" title="Reset"><Maximize2 className="w-4 h-4" /></button>
        </div>
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div className="px-4 py-2 bg-slate-50 dark:bg-slate-900/50 border-b border-slate-200 dark:border-slate-700 flex items-center gap-6 text-xs">
          <span className="text-slate-500 dark:text-slate-400">{tooltip.time}</span>
          <span><span className="text-slate-500">O:</span> <span className="font-mono text-slate-900 dark:text-slate-100">{tooltip.o}</span></span>
          <span><span className="text-slate-500">H:</span> <span className="font-mono text-slate-900 dark:text-slate-100">{tooltip.h}</span></span>
          <span><span className="text-slate-500">L:</span> <span className="font-mono text-slate-900 dark:text-slate-100">{tooltip.l}</span></span>
          <span><span className="text-slate-500">C:</span> <span className="font-mono font-medium text-slate-900 dark:text-slate-100">{tooltip.c}</span></span>
          {tooltip.osc && <span><span className="text-purple-500 font-medium">{tooltip.osc.label}:</span> <span className="font-mono font-medium text-purple-700 dark:text-purple-300">{tooltip.osc.value}</span></span>}
          {tooltip.offScales?.map((o) => (
            <span key={o.label}>
              <span className="text-amber-500 font-medium">{o.label}:</span>{' '}
              <span className="font-mono font-medium text-amber-700 dark:text-amber-300">{o.value}</span>
            </span>
          ))}
        </div>
      )}

      {/* Single chart */}
      <div ref={containerRef} className="w-full" />

      {/* Legend */}
      <div className="px-4 py-3 border-t border-slate-200 dark:border-slate-700 flex flex-wrap items-center gap-4 text-xs">
        {showIndicators && [...overlays, ...oscillators].map(ind => (
          <div key={ind.name} className="flex items-center gap-1.5">
            <div className="w-4 h-0.5 rounded" style={{ backgroundColor: ind.color }} />
            <span className="text-slate-600 dark:text-slate-400">{ind.name}</span>
          </div>
        ))}
        {hasOsc && showIndicators && (
          <span className="text-slate-400 dark:text-slate-500 text-xs">
            {(params.oversold ?? 30).toFixed(1)} / {(params.overbought ?? 70).toFixed(1)}
          </span>
        )}
        <div className="h-3 w-px bg-slate-200 dark:bg-slate-700" />
        <div className="flex items-center gap-1.5"><div className="w-0 h-0 border-l-4 border-r-4 border-b-8 border-transparent border-b-green-500" /><span className="text-slate-600 dark:text-slate-400">Long Entry</span></div>
        <div className="flex items-center gap-1.5"><div className="w-0 h-0 border-l-4 border-r-4 border-t-8 border-transparent border-t-red-500" /><span className="text-slate-600 dark:text-slate-400">Short Entry</span></div>
        <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded-full bg-green-500" /><span className="text-slate-600 dark:text-slate-400">Profit Exit</span></div>
        <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded-full bg-red-500" /><span className="text-slate-600 dark:text-slate-400">Loss Exit</span></div>
      </div>
    </div>
  );
}
