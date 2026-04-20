/**
 * StrategyChart Component
 *
 * Single chart with candlesticks, trade markers, and indicators.
 * - MA / Donchian: overlay on the right (candle) price scale
 * - RSI: rendered on a separate 'left' price scale in the bottom portion
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
}

function isOscillator(ind: IndicatorLine): boolean {
  return ind.name.toLowerCase().includes('rsi');
}

const toTime = (ts: string): UTCTimestamp =>
  (new Date(ts).getTime() / 1000) as UTCTimestamp;

export default function StrategyChart({
  candles, signals, indicators, instrument, strategy, params,
  timeframe, isLoading = false, error = null,
}: StrategyChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const overlayRefs = useRef<ISeriesApi<'Line'>[]>([]);
  const oscRefs = useRef<ISeriesApi<'Line'>[]>([]);

  const [chartError, setChartError] = useState<string | null>(null);
  const [showIndicators, setShowIndicators] = useState(true);
  const [tooltip, setTooltip] = useState<{
    time: string; o: string; h: string; l: string; c: string; rsi?: string;
  } | null>(null);

  const overlays = indicators.filter(i => !isOscillator(i));
  const oscillators = indicators.filter(i => isOscillator(i));
  const hasOsc = oscillators.length > 0;

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
        height: hasOsc ? 600 : 500,
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: dark ? '#374151' : '#d1d5db' },
        rightPriceScale: {
          borderColor: dark ? '#374151' : '#d1d5db',
          scaleMargins: { top: 0.02, bottom: hasOsc ? 0.28 : 0.02 },
        },
        leftPriceScale: {
          visible: hasOsc,
          borderColor: dark ? '#374151' : '#d1d5db',
          scaleMargins: { top: 0.75, bottom: 0.02 },
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
        // Get RSI value from oscillator series
        let rsi: string | undefined;
        if (oscRefs.current.length > 0) {
          const oscData = param.seriesData.get(oscRefs.current[0]) as any;
          if (oscData?.value !== undefined) rsi = oscData.value.toFixed(1);
        }
        setTooltip({
          time: new Date((param.time as number) * 1000).toLocaleString('en-US', { timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          o: d.open.toFixed(5), h: d.high.toFixed(5), l: d.low.toFixed(5), c: d.close.toFixed(5),
          rsi,
        });
      });

      const onResize = () => {
        if (containerRef.current && chartRef.current)
          chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      };
      window.addEventListener('resize', onResize);
      return () => { window.removeEventListener('resize', onResize); chart.remove(); chartRef.current = null; candleRef.current = null; overlayRefs.current = []; oscRefs.current = []; };
    } catch (err) {
      setChartError(err instanceof Error ? err.message : 'Failed to init chart');
    }
  }, [hasOsc]);

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
          const rsi = Object.entries(sig.indicatorValues ?? {}).find(([k]) => k.toLowerCase().includes('rsi'));
          const label = isLong ? 'L' : 'S';
          const text = rsi ? `${label} ${Number(rsi[1]).toFixed(0)}` : label;
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
      chartRef.current?.timeScale().fitContent();
      setChartError(null);
    } catch (err) {
      setChartError(err instanceof Error ? err.message : 'Failed to update chart');
    }
  }, [candles, signals]);

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
          {tooltip.rsi && <span><span className="text-purple-500 font-medium">RSI:</span> <span className="font-mono font-medium text-purple-700 dark:text-purple-300">{tooltip.rsi}</span></span>}
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
