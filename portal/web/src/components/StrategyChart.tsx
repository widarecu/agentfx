/**
 * StrategyChart Component
 *
 * Candlestick chart with trade markers and indicator overlays.
 * - MA / Donchian: overlay lines on the main price chart
 * - RSI: rendered in a separate panel below (like TradingView)
 *
 * The two panels use subscribeVisibleLogicalRangeChange for sync,
 * which works reliably even before data is loaded.
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
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

function isOscillator(indicator: IndicatorLine): boolean {
  return indicator.name.toLowerCase().includes('rsi');
}

const toChartTime = (ts: string): UTCTimestamp =>
  (new Date(ts).getTime() / 1000) as UTCTimestamp;

export default function StrategyChart({
  candles, signals, indicators, instrument, strategy, params,
  timeframe, isLoading = false, error = null,
}: StrategyChartProps) {
  const mainContainerRef = useRef<HTMLDivElement>(null);
  const oscContainerRef = useRef<HTMLDivElement>(null);
  const mainChartRef = useRef<IChartApi | null>(null);
  const oscChartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const overlayRefs = useRef<ISeriesApi<'Line'>[]>([]);
  const oscSeriesRefs = useRef<ISeriesApi<'Line'>[]>([]);
  const syncingRef = useRef(false);

  const [chartError, setChartError] = useState<string | null>(null);
  const [showIndicators, setShowIndicators] = useState(true);
  const [crosshairData, setCrosshairData] = useState<{
    time: string; price: string;
    ohlc?: { o: string; h: string; l: string; c: string };
    rsi?: string;
  } | null>(null);
  const crosshairSyncingRef = useRef(false);

  const overlayIndicators = indicators.filter(i => !isOscillator(i));
  const oscIndicators = indicators.filter(i => isOscillator(i));
  const hasOscillator = oscIndicators.length > 0;

  // Shared chart options builder
  const chartOpts = useCallback((width: number, height: number, showTimeAxis: boolean) => {
    const dark = document.documentElement.classList.contains('dark');
    return {
      layout: { background: { color: 'transparent' }, textColor: dark ? '#9ca3af' : '#6b7280' },
      grid: {
        vertLines: { color: dark ? '#1f2937' : '#e5e7eb' },
        horzLines: { color: dark ? '#1f2937' : '#e5e7eb' },
      },
      width, height,
      timeScale: { timeVisible: true, secondsVisible: false, visible: showTimeAxis, borderColor: dark ? '#374151' : '#d1d5db' },
      rightPriceScale: { borderColor: dark ? '#374151' : '#d1d5db' },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: dark ? '#6b7280' : '#9ca3af', width: 1 as const, style: 1 as const, labelBackgroundColor: dark ? '#374151' : '#e5e7eb' },
        horzLine: { color: dark ? '#6b7280' : '#9ca3af', width: 1 as const, style: 1 as const, labelBackgroundColor: dark ? '#374151' : '#e5e7eb' },
      },
    };
  }, []);

  // ── Main chart init ────────────────────────────────────────────────
  useEffect(() => {
    if (!mainContainerRef.current) return;
    try {
      const w = mainContainerRef.current.clientWidth;
      // Hide time axis on main chart when oscillator pane is shown (it shows the axis)
      const chart = createChart(mainContainerRef.current, chartOpts(w, hasOscillator ? 420 : 500, !hasOscillator));
      const series = chart.addCandlestickSeries({
        upColor: '#22c55e', downColor: '#ef4444',
        borderUpColor: '#22c55e', borderDownColor: '#ef4444',
        wickUpColor: '#22c55e', wickDownColor: '#ef4444',
      });
      mainChartRef.current = chart;
      candleSeriesRef.current = series;

      chart.subscribeCrosshairMove(param => {
        if (!param.time || !param.point) {
          setCrosshairData(null);
          // Sync crosshair clear to oscillator
          if (!crosshairSyncingRef.current && oscChartRef.current) {
            crosshairSyncingRef.current = true;
            oscChartRef.current.clearCrosshairPosition();
            crosshairSyncingRef.current = false;
          }
          return;
        }
        const d = param.seriesData.get(series) as any;
        if (d?.open !== undefined) {
          // Look up RSI value at this timestamp from the indicator data
          let rsiVal: string | undefined;
          if (hasOscillator && oscIndicators.length > 0 && param.time) {
            const ts = (param.time as number) * 1000;
            const rsiPoint = oscIndicators[0].data.find(p => Math.abs(new Date(p.timestamp).getTime() - ts) < 1000);
            if (rsiPoint) rsiVal = rsiPoint.value.toFixed(1);
          }
          setCrosshairData({
            time: new Date((param.time as number) * 1000).toLocaleString(),
            price: d.close.toFixed(5),
            ohlc: { o: d.open.toFixed(5), h: d.high.toFixed(5), l: d.low.toFixed(5), c: d.close.toFixed(5) },
            rsi: rsiVal,
          });
        } else { setCrosshairData(null); }
        // Sync crosshair position to oscillator chart
        if (!crosshairSyncingRef.current && oscChartRef.current && oscSeriesRefs.current.length > 0) {
          crosshairSyncingRef.current = true;
          try { oscChartRef.current.setCrosshairPosition(0, param.time, oscSeriesRefs.current[0]); } catch { /* */ }
          crosshairSyncingRef.current = false;
        }
      });

      const onResize = () => {
        if (!mainContainerRef.current) return;
        const nw = mainContainerRef.current.clientWidth;
        mainChartRef.current?.applyOptions({ width: nw });
        oscChartRef.current?.applyOptions({ width: nw });
      };
      window.addEventListener('resize', onResize);

      return () => {
        window.removeEventListener('resize', onResize);
        chart.remove();
        mainChartRef.current = null;
        candleSeriesRef.current = null;
        overlayRefs.current = [];
      };
    } catch (err) {
      setChartError(err instanceof Error ? err.message : 'Failed to init chart');
    }
  }, [chartOpts, hasOscillator]);

  // ── Oscillator chart init ──────────────────────────────────────────
  useEffect(() => {
    if (!oscContainerRef.current || !hasOscillator) {
      if (oscChartRef.current) { oscChartRef.current.remove(); oscChartRef.current = null; }
      return;
    }
    const w = oscContainerRef.current.clientWidth;
    const osc = createChart(oscContainerRef.current, chartOpts(w, 150, true));
    oscChartRef.current = osc;

    // Sync scroll/zoom between charts using logical range
    const syncFrom = (source: IChartApi, target: IChartApi) => {
      source.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (syncingRef.current || !range) return;
        syncingRef.current = true;
        try { target.timeScale().setVisibleLogicalRange(range); } catch { /* */ }
        syncingRef.current = false;
      });
    };
    if (mainChartRef.current) {
      syncFrom(mainChartRef.current, osc);
      syncFrom(osc, mainChartRef.current);
    }

    // Add a hidden anchor series with the same timestamps as candles.
    // This ensures both charts have identical logical bar indices so
    // logical range sync maps to the same calendar times.
    if (candles.length > 0) {
      const anchor = osc.addLineSeries({
        color: 'transparent', lineWidth: 1 as const, visible: false, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false,
      });
      const anchorData: LineData[] = candles
        .map(c => ({ time: toChartTime(c.timestamp), value: 0 }))
        .sort((a, b) => (a.time as number) - (b.time as number));
      anchor.setData(anchorData);
      (osc as any)._anchor = anchor;
    }

    // Crosshair sync: oscillator → main + RSI tooltip
    osc.subscribeCrosshairMove(param => {
      if (!param.time || !param.point) {
        if (!crosshairSyncingRef.current && mainChartRef.current) {
          crosshairSyncingRef.current = true;
          mainChartRef.current.clearCrosshairPosition();
          crosshairSyncingRef.current = false;
        }
        return;
      }
      // Update RSI value in tooltip when hovering on RSI pane
      if (oscIndicators.length > 0) {
        const ts = (param.time as number) * 1000;
        const rsiPoint = oscIndicators[0].data.find(p => Math.abs(new Date(p.timestamp).getTime() - ts) < 1000);
        if (rsiPoint) {
          setCrosshairData(prev => prev ? { ...prev, rsi: rsiPoint.value.toFixed(1) } : {
            time: new Date(ts).toLocaleString(), price: '', rsi: rsiPoint.value.toFixed(1),
          });
        }
      }
      if (!crosshairSyncingRef.current && mainChartRef.current && candleSeriesRef.current) {
        crosshairSyncingRef.current = true;
        try { mainChartRef.current.setCrosshairPosition(0, param.time, candleSeriesRef.current); } catch { /* */ }
        crosshairSyncingRef.current = false;
      }
    });

    return () => { osc.remove(); oscChartRef.current = null; oscSeriesRefs.current = []; };
  }, [chartOpts, hasOscillator, candles]);

  // ── Set candle data + markers ──────────────────────────────────────
  useEffect(() => {
    if (!candleSeriesRef.current || candles.length === 0) return;
    try {
      const chartData: CandlestickData[] = candles
        .map(c => ({ time: toChartTime(c.timestamp), open: c.open, high: c.high, low: c.low, close: c.close }))
        .sort((a, b) => (a.time as number) - (b.time as number));
      candleSeriesRef.current.setData(chartData);

      const tMin = chartData[0].time as number;
      const tMax = chartData[chartData.length - 1].time as number;
      const filtered = signals.filter(s => { const t = toChartTime(s.timestamp); return t >= tMin && t <= tMax; });

      // Build exit P&L map
      const exitPnl = new Map<string, number>();
      let lastEntry: { price: number; dir: string } | null = null;
      for (const s of filtered) {
        if (s.signalType.startsWith('entry')) { lastEntry = { price: s.price, dir: s.signalType.includes('long') ? 'long' : 'short' }; }
        else if (lastEntry && s.price) {
          const pips = lastEntry.dir === 'long' ? (s.price - lastEntry.price) / 0.0001 : (lastEntry.price - s.price) / 0.0001;
          exitPnl.set(s.timestamp, pips);
          lastEntry = null;
        }
      }

      const markers: SeriesMarker<Time>[] = filtered.map(sig => {
        const isEntry = sig.signalType.startsWith('entry');
        const isLong = sig.signalType.includes('long');
        if (isEntry) {
          // Show RSI value on entry if available
          const rsi = Object.entries(sig.indicatorValues ?? {}).find(([k]) => k.toLowerCase().includes('rsi'));
          const label = isLong ? 'L' : 'S';
          const text = rsi ? `${label} ${Number(rsi[1]).toFixed(0)}` : label;
          const position: 'belowBar' | 'aboveBar' = isLong ? 'belowBar' : 'aboveBar';
          const shape: 'arrowUp' | 'arrowDown' = isLong ? 'arrowUp' : 'arrowDown';
          return { time: toChartTime(sig.timestamp), position, color: isLong ? '#22c55e' : '#ef4444', shape, text, size: 1 };
        }
        const pips = sig.pnl != null ? sig.pnl * 10000 : exitPnl.get(sig.timestamp) ?? 0;
        const win = pips > 0;
        return { time: toChartTime(sig.timestamp), position: 'aboveBar' as const, color: win ? '#22c55e' : '#ef4444', shape: 'circle' as const, text: pips !== 0 ? (win ? `+${pips.toFixed(0)}` : pips.toFixed(0)) : 'X', size: 1 };
      });
      markers.sort((a, b) => (a.time as number) - (b.time as number));
      candleSeriesRef.current.setMarkers(markers);
      mainChartRef.current?.timeScale().fitContent();
      oscChartRef.current?.timeScale().fitContent();
      setChartError(null);
    } catch (err) {
      setChartError(err instanceof Error ? err.message : 'Failed to update chart');
    }
  }, [candles, signals]);

  // ── Overlay indicators (MA, Donchian) ──────────────────────────────
  useEffect(() => {
    if (!mainChartRef.current) return;
    overlayRefs.current.forEach(s => { try { mainChartRef.current?.removeSeries(s); } catch { /* */ } });
    overlayRefs.current = [];
    if (!showIndicators || overlayIndicators.length === 0) return;
    overlayIndicators.forEach(ind => {
      const ls = mainChartRef.current!.addLineSeries({ color: ind.color, lineWidth: 2, title: ind.name, priceLineVisible: false, lastValueVisible: false });
      const data: LineData[] = ind.data.map(p => ({ time: toChartTime(p.timestamp), value: p.value })).sort((a, b) => (a.time as number) - (b.time as number));
      ls.setData(data);
      overlayRefs.current.push(ls);
    });
  }, [indicators, showIndicators]);

  // ── Oscillator indicators (RSI) ────────────────────────────────────
  useEffect(() => {
    if (!oscChartRef.current) return;
    oscSeriesRefs.current.forEach(s => { try { oscChartRef.current?.removeSeries(s); } catch { /* */ } });
    oscSeriesRefs.current = [];
    if (!showIndicators || oscIndicators.length === 0) return;

    const dark = document.documentElement.classList.contains('dark');
    oscIndicators.forEach(ind => {
      const ls = oscChartRef.current!.addLineSeries({ color: ind.color, lineWidth: 2, title: ind.name, priceLineVisible: false, lastValueVisible: false });
      const data: LineData[] = ind.data.map(p => ({ time: toChartTime(p.timestamp), value: p.value })).sort((a, b) => (a.time as number) - (b.time as number));
      ls.setData(data);
      oscSeriesRefs.current.push(ls);
    });

    // Reference lines at oversold/overbought from trial params (fallback 30/70)
    const oversold = params.oversold ?? params.lower ?? 30;
    const overbought = params.overbought ?? params.upper ?? 70;
    if (candles.length > 0) {
      const sorted = [...candles].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
      const t0 = toChartTime(sorted[0].timestamp);
      const t1 = toChartTime(sorted[sorted.length - 1].timestamp);
      const refOpts = { lineWidth: 1 as const, lineStyle: LineStyle.Dashed, color: dark ? '#4b5563' : '#9ca3af', priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: '' };
      const lineLow = oscChartRef.current!.addLineSeries(refOpts);
      lineLow.setData([{ time: t0, value: oversold }, { time: t1, value: oversold }] as LineData[]);
      const lineHigh = oscChartRef.current!.addLineSeries(refOpts);
      lineHigh.setData([{ time: t0, value: overbought }, { time: t1, value: overbought }] as LineData[]);
      oscSeriesRefs.current.push(lineLow, lineHigh);
    }

    oscChartRef.current.timeScale().fitContent();
  }, [indicators, showIndicators, candles]);

  // ── Zoom handlers ──────────────────────────────────────────────────
  const zoom = (factor: number) => {
    if (!mainChartRef.current) return;
    const ts = mainChartRef.current.timeScale();
    const range = ts.getVisibleLogicalRange();
    if (!range) return;
    const center = (range.from + range.to) / 2;
    const half = (range.to - range.from) / 2 * factor;
    ts.setVisibleLogicalRange({ from: center - half, to: center + half });
  };
  const handleReset = () => {
    mainChartRef.current?.timeScale().fitContent();
    oscChartRef.current?.timeScale().fitContent();
  };

  // ── Loading / Error / Empty states ─────────────────────────────────
  if (isLoading) return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
      <div className="flex items-center justify-center h-[500px]">
        <div className="flex flex-col items-center gap-3">
          <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400" />
          <p className="text-sm text-slate-600 dark:text-slate-400">Loading chart data...</p>
        </div>
      </div>
    </div>
  );

  const displayError = error || chartError;
  if (displayError) return (
    <div className="bg-white dark:bg-slate-800 border border-red-200 dark:border-red-800 rounded-lg p-6">
      <div className="flex items-center justify-center h-[500px]">
        <p className="text-sm text-red-600 dark:text-red-400">{displayError}</p>
      </div>
    </div>
  );

  if (candles.length === 0) return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
      <div className="flex items-center justify-center h-[500px]">
        <p className="text-sm text-slate-600 dark:text-slate-400">No chart data available</p>
      </div>
    </div>
  );

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
          <button onClick={handleReset} className="p-1.5 rounded-md hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-400" title="Reset"><Maximize2 className="w-4 h-4" /></button>
        </div>
      </div>

      {/* OHLC tooltip */}
      {crosshairData && (
        <div className="px-4 py-2 bg-slate-50 dark:bg-slate-900/50 border-b border-slate-200 dark:border-slate-700 flex items-center gap-6 text-xs">
          <span className="text-slate-500 dark:text-slate-400">{crosshairData.time}</span>
          {crosshairData.ohlc && <>
            <span><span className="text-slate-500 dark:text-slate-400">O:</span> <span className="font-mono text-slate-900 dark:text-slate-100">{crosshairData.ohlc.o}</span></span>
            <span><span className="text-slate-500 dark:text-slate-400">H:</span> <span className="font-mono text-slate-900 dark:text-slate-100">{crosshairData.ohlc.h}</span></span>
            <span><span className="text-slate-500 dark:text-slate-400">L:</span> <span className="font-mono text-slate-900 dark:text-slate-100">{crosshairData.ohlc.l}</span></span>
            <span><span className="text-slate-500 dark:text-slate-400">C:</span> <span className="font-mono font-medium text-slate-900 dark:text-slate-100">{crosshairData.ohlc.c}</span></span>
          </>}
          {crosshairData.rsi && (
            <span><span className="text-purple-500 dark:text-purple-400 font-medium">RSI:</span> <span className="font-mono font-medium text-purple-700 dark:text-purple-300">{crosshairData.rsi}</span></span>
          )}
        </div>
      )}

      {/* Main candle chart */}
      <div ref={mainContainerRef} className="w-full" />

      {/* RSI panel */}
      {hasOscillator && showIndicators && (
        <>
          <div className="px-3 py-1 border-t border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/50 flex items-center justify-between">
            <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
              <span className="font-medium">RSI</span>
              <span className="text-slate-400 dark:text-slate-500">
                {(params.oversold ?? 30).toFixed(1)} / {(params.overbought ?? 70).toFixed(1)}
              </span>
            </div>
          </div>
          <div ref={oscContainerRef} className="w-full" />
        </>
      )}

      {/* Legend */}
      <div className="px-4 py-3 border-t border-slate-200 dark:border-slate-700 flex flex-wrap items-center gap-4 text-xs">
        {showIndicators && overlayIndicators.map(ind => (
          <div key={ind.name} className="flex items-center gap-1.5">
            <div className="w-4 h-0.5 rounded" style={{ backgroundColor: ind.color }} />
            <span className="text-slate-600 dark:text-slate-400">{ind.name}</span>
          </div>
        ))}
        {showIndicators && oscIndicators.map(ind => (
          <div key={ind.name} className="flex items-center gap-1.5">
            <div className="w-4 h-0.5 rounded" style={{ backgroundColor: ind.color }} />
            <span className="text-slate-600 dark:text-slate-400">{ind.name}</span>
          </div>
        ))}
        <div className="h-3 w-px bg-slate-200 dark:bg-slate-700" />
        <div className="flex items-center gap-1.5"><div className="w-0 h-0 border-l-4 border-r-4 border-b-8 border-transparent border-b-green-500" /><span className="text-slate-600 dark:text-slate-400">Long Entry</span></div>
        <div className="flex items-center gap-1.5"><div className="w-0 h-0 border-l-4 border-r-4 border-t-8 border-transparent border-t-red-500" /><span className="text-slate-600 dark:text-slate-400">Short Entry</span></div>
        <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded-full bg-green-500" /><span className="text-slate-600 dark:text-slate-400">Profit Exit</span></div>
        <div className="flex items-center gap-1.5"><div className="w-3 h-3 rounded-full bg-red-500" /><span className="text-slate-600 dark:text-slate-400">Loss Exit</span></div>
      </div>
    </div>
  );
}
