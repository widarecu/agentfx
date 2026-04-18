/**
 * StrategyChart Component
 *
 * Candlestick chart with:
 * - OHLC candles (green up, red down)
 * - Entry markers (triangles)
 * - Exit markers (circles with P&L color)
 * - Indicator overlays (MA lines, Donchian bands) on the main chart
 * - Oscillator pane (RSI) rendered in a separate chart below the main chart
 * - Crosshair with price/time on hover
 * - Zoom/pan controls (synchronized across both panes)
 *
 * Uses lightweight-charts library for high performance.
 * lightweight-charts v4.x has no native multi-pane support, so oscillator
 * indicators are rendered in a second createChart() instance that is kept
 * in sync with the main chart's time scale.
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

/** Returns true when the indicator belongs in the oscillator pane (e.g. RSI). */
function isOscillatorIndicator(indicator: IndicatorLine): boolean {
  return indicator.name.toLowerCase().includes('rsi');
}

/** Convert an ISO timestamp string to the UTCTimestamp number lightweight-charts expects. */
const toChartTime = (timestamp: string): UTCTimestamp =>
  (new Date(timestamp).getTime() / 1000) as UTCTimestamp;

/** Build shared dark/light chart options so both panes look identical. */
function buildChartOptions(
  width: number,
  height: number,
  isDarkMode: boolean,
  showTimeAxis: boolean
) {
  return {
    layout: {
      background: { color: 'transparent' },
      textColor: isDarkMode ? '#9ca3af' : '#6b7280',
    },
    grid: {
      vertLines: { color: isDarkMode ? '#1f2937' : '#e5e7eb' },
      horzLines: { color: isDarkMode ? '#1f2937' : '#e5e7eb' },
    },
    width,
    height,
    timeScale: {
      timeVisible: showTimeAxis,
      secondsVisible: false,
      borderColor: isDarkMode ? '#374151' : '#d1d5db',
      // Hide the time axis on the main chart when an oscillator pane sits below it –
      // the oscillator pane shows the shared time axis instead.
      visible: showTimeAxis,
    },
    rightPriceScale: {
      borderColor: isDarkMode ? '#374151' : '#d1d5db',
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: {
        color: isDarkMode ? '#6b7280' : '#9ca3af',
        width: 1 as const,
        style: 1 as const,
        labelBackgroundColor: isDarkMode ? '#374151' : '#e5e7eb',
      },
      horzLine: {
        color: isDarkMode ? '#6b7280' : '#9ca3af',
        width: 1 as const,
        style: 1 as const,
        labelBackgroundColor: isDarkMode ? '#374151' : '#e5e7eb',
      },
    },
  };
}

export default function StrategyChart({
  candles,
  signals,
  indicators,
  instrument,
  strategy,
  params,
  timeframe,
  isLoading = false,
  error = null,
}: StrategyChartProps) {
  // ── DOM refs ─────────────────────────────────────────────────────────────
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const oscillatorContainerRef = useRef<HTMLDivElement>(null);

  // ── Chart API refs ────────────────────────────────────────────────────────
  const chartRef = useRef<IChartApi | null>(null);
  const oscillatorChartRef = useRef<IChartApi | null>(null);

  // ── Series refs ───────────────────────────────────────────────────────────
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  /** Series for overlay indicators (MA, Donchian) on the main chart. */
  const overlaySeriesRefs = useRef<ISeriesApi<'Line'>[]>([]);
  /** Series for oscillator indicators (RSI) on the oscillator chart. */
  const oscillatorSeriesRefs = useRef<ISeriesApi<'Line'>[]>([]);

  // ── Sync guard ────────────────────────────────────────────────────────────
  // Prevents the two subscribeVisibleTimeRangeChange callbacks from triggering
  // each other in an infinite loop.
  const isSyncingRef = useRef(false);

  // ── UI state ──────────────────────────────────────────────────────────────
  const [chartError, setChartError] = useState<string | null>(null);
  const [showIndicators, setShowIndicators] = useState(true);
  const [crosshairData, setCrosshairData] = useState<{
    time: string;
    price: string;
    ohlc?: { o: string; h: string; l: string; c: string };
  } | null>(null);

  // Derived indicator classification (stable reference for effects)
  const overlayIndicators = indicators.filter(i => !isOscillatorIndicator(i));
  const oscillatorIndicators = indicators.filter(i => isOscillatorIndicator(i));
  const hasOscillators = oscillatorIndicators.length > 0;

  // ── Initialize main chart ─────────────────────────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

    try {
      const isDarkMode = document.documentElement.classList.contains('dark');

      const chart = createChart(
        chartContainerRef.current,
        buildChartOptions(
          chartContainerRef.current.clientWidth,
          500,
          isDarkMode,
          // Show the main chart's time axis only when there is no oscillator pane
          !hasOscillators
        )
      );

      const candlestickSeries = chart.addCandlestickSeries({
        upColor: '#22c55e',
        downColor: '#ef4444',
        borderUpColor: '#22c55e',
        borderDownColor: '#ef4444',
        wickUpColor: '#22c55e',
        wickDownColor: '#ef4444',
      });

      chartRef.current = chart;
      candlestickSeriesRef.current = candlestickSeries;

      // Crosshair tooltip
      chart.subscribeCrosshairMove(param => {
        if (!param.time || !param.point) {
          setCrosshairData(null);
          return;
        }
        const ohlcData = param.seriesData.get(candlestickSeries) as any;
        if (ohlcData && ohlcData.open !== undefined) {
          setCrosshairData({
            time: new Date((param.time as number) * 1000).toLocaleString(),
            price: ohlcData.close.toFixed(5),
            ohlc: {
              o: ohlcData.open.toFixed(5),
              h: ohlcData.high.toFixed(5),
              l: ohlcData.low.toFixed(5),
              c: ohlcData.close.toFixed(5),
            },
          });
        } else {
          setCrosshairData(null);
        }
      });

      // Resize handler
      const handleResize = () => {
        if (chartContainerRef.current && chartRef.current) {
          const w = chartContainerRef.current.clientWidth;
          chartRef.current.applyOptions({ width: w });
          oscillatorChartRef.current?.applyOptions({ width: w });
        }
      };
      window.addEventListener('resize', handleResize);

      return () => {
        window.removeEventListener('resize', handleResize);
        chart.remove();
        chartRef.current = null;
        candlestickSeriesRef.current = null;
        overlaySeriesRefs.current = [];
      };
    } catch (err) {
      console.error('Failed to initialize main chart:', err);
      setChartError(err instanceof Error ? err.message : 'Failed to initialize chart');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Initialize oscillator chart ───────────────────────────────────────────
  useEffect(() => {
    if (!oscillatorContainerRef.current || !hasOscillators) return;

    try {
      const isDarkMode = document.documentElement.classList.contains('dark');

      const oscChart = createChart(
        oscillatorContainerRef.current,
        {
          ...buildChartOptions(
            oscillatorContainerRef.current.clientWidth,
            150,
            isDarkMode,
            true // always show time axis on the oscillator pane
          ),
          rightPriceScale: {
            borderColor: isDarkMode ? '#374151' : '#d1d5db',
            // Keep the scale fixed to 0-100 so RSI always fills the pane correctly
            scaleMargins: { top: 0.05, bottom: 0.05 },
          },
        }
      );

      // Fix the price range to 0–100
      oscChart.priceScale('right').applyOptions({
        autoScale: false,
        scaleMargins: { top: 0.05, bottom: 0.05 },
      });

      // Reference lines at 30 (oversold) and 70 (overbought)
      const refLineOptions = {
        lineWidth: 1 as const,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        color: isDarkMode ? '#4b5563' : '#9ca3af',
      };

      const oversoldLine = oscChart.addLineSeries({ ...refLineOptions, title: '' });
      const overboughtLine = oscChart.addLineSeries({ ...refLineOptions, title: '' });

      oscillatorChartRef.current = oscChart;

      // We need candle timestamps to anchor the reference lines across the full range.
      // Store the line series so we can set data once candles are available.
      // Use a short-lived ref trick: attach them to the chart object itself.
      (oscChart as any)._refLines = { oversoldLine, overboughtLine };

      // ── Time-scale synchronization ──
      // Main → oscillator
      chartRef.current?.timeScale().subscribeVisibleTimeRangeChange(range => {
        if (isSyncingRef.current || !range || range.from == null || range.to == null) return;
        isSyncingRef.current = true;
        try {
          oscChart.timeScale().setVisibleRange(range);
        } catch { /* oscillator chart may not have data yet */ }
        isSyncingRef.current = false;
      });

      // Oscillator → main
      oscChart.timeScale().subscribeVisibleTimeRangeChange(range => {
        if (isSyncingRef.current || !range || range.from == null || range.to == null) return;
        isSyncingRef.current = true;
        try {
          chartRef.current?.timeScale().setVisibleRange(range);
        } catch { /* main chart may not have data yet */ }
        isSyncingRef.current = false;
      });

      return () => {
        oscChart.remove();
        oscillatorChartRef.current = null;
        oscillatorSeriesRefs.current = [];
      };
    } catch (err) {
      console.error('Failed to initialize oscillator chart:', err);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasOscillators]);

  // ── Update candle data + markers ──────────────────────────────────────────
  useEffect(() => {
    if (!candlestickSeriesRef.current || candles.length === 0) return;

    try {
      const chartData: CandlestickData[] = candles
        .map(candle => ({
          time: toChartTime(candle.timestamp),
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        }))
        .sort((a, b) => (a.time as number) - (b.time as number));

      candlestickSeriesRef.current.setData(chartData);

      // Set reference line data spanning the full candle range
      if (oscillatorChartRef.current) {
        const { oversoldLine, overboughtLine } = (oscillatorChartRef.current as any)._refLines ?? {};
        if (oversoldLine && overboughtLine && chartData.length > 0) {
          const first = chartData[0].time as UTCTimestamp;
          const last = chartData[chartData.length - 1].time as UTCTimestamp;
          oversoldLine.setData([
            { time: first, value: 30 },
            { time: last, value: 30 },
          ] as LineData[]);
          overboughtLine.setData([
            { time: first, value: 70 },
            { time: last, value: 70 },
          ] as LineData[]);

          // Lock the visible price range after data is loaded
          oscillatorChartRef.current.priceScale('right').applyOptions({
            autoScale: false,
            scaleMargins: { top: 0.05, bottom: 0.05 },
          });
        }
      }

      // Clamp signals to the candle time range to prevent off-screen markers
      const candleTimeMin = (chartData[0].time as number) ?? 0;
      const candleTimeMax = (chartData[chartData.length - 1].time as number) ?? Infinity;
      const filteredSignals = signals.filter(s => {
        const t = toChartTime(s.timestamp);
        return t >= candleTimeMin && t <= candleTimeMax;
      });

      // Build entry-exit pairs to compute exit P&L from price differences.
      // The DB pnl field on signals is often null, so we derive it.
      const exitPnlMap = new Map<string, number>(); // exit timestamp -> pips
      let lastEntry: { price: number; direction: 'long' | 'short' } | null = null;
      for (const sig of filteredSignals) {
        if (sig.signalType.startsWith('entry')) {
          lastEntry = {
            price: sig.price,
            direction: sig.signalType.includes('long') ? 'long' : 'short',
          };
        } else if (lastEntry && sig.price) {
          const pips = lastEntry.direction === 'long'
            ? (sig.price - lastEntry.price) / 0.0001
            : (lastEntry.price - sig.price) / 0.0001;
          exitPnlMap.set(sig.timestamp, pips);
          lastEntry = null;
        }
      }

      const markers: SeriesMarker<Time>[] = filteredSignals.map(signal => {
        const isEntry = signal.signalType.startsWith('entry');
        const isLong = signal.signalType.includes('long');

        let color: string;
        let shape: 'arrowUp' | 'arrowDown' | 'circle';
        let position: 'belowBar' | 'aboveBar';
        let text: string;

        if (isEntry) {
          shape = isLong ? 'arrowUp' : 'arrowDown';
          color = isLong ? '#22c55e' : '#ef4444';
          position = isLong ? 'belowBar' : 'aboveBar';
          text = isLong ? 'L' : 'S';
        } else {
          shape = 'circle';
          const exitPips = signal.pnl != null
            ? signal.pnl * 10000  // pnl is stored as decimal return
            : exitPnlMap.get(signal.timestamp) ?? 0;
          const isProfitable = exitPips > 0;
          color = isProfitable ? '#22c55e' : '#ef4444';
          position = 'aboveBar';
          text = exitPips !== 0
            ? exitPips > 0 ? `+${exitPips.toFixed(0)}` : `${exitPips.toFixed(0)}`
            : 'X';
        }

        return {
          time: toChartTime(signal.timestamp),
          position,
          color,
          shape,
          text,
          size: 1,
        };
      });

      markers.sort((a, b) => (a.time as number) - (b.time as number));
      candlestickSeriesRef.current.setMarkers(markers);

      chartRef.current?.timeScale().fitContent();
      setChartError(null);
    } catch (err) {
      console.error('Failed to update chart data:', err);
      setChartError(err instanceof Error ? err.message : 'Failed to update chart data');
    }
  }, [candles, signals]);

  // ── Update overlay indicators (MA, Donchian) ─────────────────────────────
  useEffect(() => {
    if (!chartRef.current) return;

    // Remove previous overlay series
    overlaySeriesRefs.current.forEach(series => {
      try {
        chartRef.current?.removeSeries(series);
      } catch {
        // Series may already be gone
      }
    });
    overlaySeriesRefs.current = [];

    if (!showIndicators || overlayIndicators.length === 0) return;

    try {
      overlayIndicators.forEach(indicator => {
        const lineSeries = chartRef.current!.addLineSeries({
          color: indicator.color,
          lineWidth: 2,
          title: indicator.name,
          priceLineVisible: false,
          lastValueVisible: false,
        });

        const lineData: LineData[] = indicator.data
          .map(point => ({ time: toChartTime(point.timestamp), value: point.value }))
          .sort((a, b) => (a.time as number) - (b.time as number));

        lineSeries.setData(lineData);
        overlaySeriesRefs.current.push(lineSeries);
      });
    } catch (err) {
      console.error('Failed to update overlay indicators:', err);
    }
  }, [indicators, showIndicators]);

  // ── Update oscillator indicators (RSI) ───────────────────────────────────
  useEffect(() => {
    if (!oscillatorChartRef.current) return;

    // Remove previous oscillator series (but not the reference lines)
    oscillatorSeriesRefs.current.forEach(series => {
      try {
        oscillatorChartRef.current?.removeSeries(series);
      } catch {
        // Series may already be gone
      }
    });
    oscillatorSeriesRefs.current = [];

    if (!showIndicators || oscillatorIndicators.length === 0) return;

    try {
      oscillatorIndicators.forEach(indicator => {
        const lineSeries = oscillatorChartRef.current!.addLineSeries({
          color: indicator.color,
          lineWidth: 2,
          title: indicator.name,
          priceLineVisible: false,
          lastValueVisible: false,
        });

        const lineData: LineData[] = indicator.data
          .map(point => ({ time: toChartTime(point.timestamp), value: point.value }))
          .sort((a, b) => (a.time as number) - (b.time as number));

        lineSeries.setData(lineData);
        oscillatorSeriesRefs.current.push(lineSeries);
      });
    } catch (err) {
      console.error('Failed to update oscillator indicators:', err);
    }
  }, [indicators, showIndicators]);

  // ── Chart control handlers ────────────────────────────────────────────────
  const handleZoomIn = () => {
    if (!chartRef.current) return;
    const timeScale = chartRef.current.timeScale();
    const visibleRange = timeScale.getVisibleRange();
    if (visibleRange) {
      const center = ((visibleRange.from as number) + (visibleRange.to as number)) / 2;
      const halfRange = ((visibleRange.to as number) - (visibleRange.from as number)) / 4;
      const newRange = {
        from: (center - halfRange) as UTCTimestamp,
        to: (center + halfRange) as UTCTimestamp,
      };
      timeScale.setVisibleRange(newRange);
      // Oscillator chart syncs automatically via the subscribeVisibleTimeRangeChange listener
    }
  };

  const handleZoomOut = () => {
    if (!chartRef.current) return;
    const timeScale = chartRef.current.timeScale();
    const visibleRange = timeScale.getVisibleRange();
    if (visibleRange) {
      const center = ((visibleRange.from as number) + (visibleRange.to as number)) / 2;
      const halfRange = (visibleRange.to as number) - (visibleRange.from as number);
      const newRange = {
        from: (center - halfRange) as UTCTimestamp,
        to: (center + halfRange) as UTCTimestamp,
      };
      timeScale.setVisibleRange(newRange);
    }
  };

  const handleReset = () => {
    chartRef.current?.timeScale().fitContent();
    // Oscillator will sync via listener; additionally trigger its own fitContent
    // so the time labels render correctly before a pan event fires.
    oscillatorChartRef.current?.timeScale().fitContent();
  };

  // ── Loading state ─────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <div className="flex items-center justify-center h-[500px]">
          <div className="flex flex-col items-center gap-3">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400" />
            <p className="text-sm text-slate-600 dark:text-slate-400">Loading chart data...</p>
          </div>
        </div>
      </div>
    );
  }

  // ── Error state ───────────────────────────────────────────────────────────
  const displayError = error || chartError;
  if (displayError) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-red-200 dark:border-red-800 rounded-lg p-6">
        <div className="flex items-center justify-center h-[500px]">
          <div className="flex flex-col items-center gap-3 text-center max-w-md">
            <div className="w-12 h-12 rounded-full bg-red-100 dark:bg-red-900/50 flex items-center justify-center">
              <svg className="w-6 h-6 text-red-600 dark:text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <p className="text-sm font-medium text-red-600 dark:text-red-400">Failed to load chart</p>
            <p className="text-xs text-slate-600 dark:text-slate-400">{displayError}</p>
          </div>
        </div>
      </div>
    );
  }

  // ── No data state ─────────────────────────────────────────────────────────
  if (candles.length === 0) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <div className="flex items-center justify-center h-[500px]">
          <div className="flex flex-col items-center gap-3 text-center">
            <div className="w-12 h-12 rounded-full bg-slate-100 dark:bg-slate-700 flex items-center justify-center">
              <svg className="w-6 h-6 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
              </svg>
            </div>
            <p className="text-sm font-medium text-slate-600 dark:text-slate-400">No chart data available</p>
          </div>
        </div>
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
      {/* Chart Header */}
      <div className="px-4 py-3 border-b border-slate-200 dark:border-slate-700 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">{instrument}</h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Strategy: {strategy.toUpperCase()}
            {timeframe ? ` | Timeframe: ${timeframe}` : ''} | Params:{' '}
            {Object.entries(params).map(([k, v]) => `${k}=${v}`).join(', ')}
          </p>
        </div>

        {/* Chart Controls */}
        <div className="flex items-center gap-2">
          {/* Indicators Toggle */}
          <button
            onClick={() => setShowIndicators(!showIndicators)}
            className={`px-3 py-1.5 text-xs font-medium rounded-md border transition-colors ${
              showIndicators
                ? 'bg-blue-50 border-blue-200 text-blue-700 dark:bg-blue-900/30 dark:border-blue-800 dark:text-blue-400'
                : 'bg-slate-50 border-slate-200 text-slate-700 dark:bg-slate-700 dark:border-slate-600 dark:text-slate-300'
            }`}
          >
            {showIndicators ? 'Hide Indicators' : 'Show Indicators'}
          </button>

          <div className="h-4 w-px bg-slate-200 dark:bg-slate-700" />

          {/* Zoom Controls */}
          <button
            onClick={handleZoomIn}
            className="p-1.5 rounded-md hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-400 transition-colors"
            title="Zoom In"
          >
            <ZoomIn className="w-4 h-4" />
          </button>
          <button
            onClick={handleZoomOut}
            className="p-1.5 rounded-md hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-400 transition-colors"
            title="Zoom Out"
          >
            <ZoomOut className="w-4 h-4" />
          </button>
          <button
            onClick={handleReset}
            className="p-1.5 rounded-md hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-400 transition-colors"
            title="Reset View"
          >
            <Maximize2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Crosshair Data Display */}
      {crosshairData && (
        <div className="px-4 py-2 bg-slate-50 dark:bg-slate-900/50 border-b border-slate-200 dark:border-slate-700 flex items-center gap-6 text-xs">
          <span className="text-slate-500 dark:text-slate-400">{crosshairData.time}</span>
          {crosshairData.ohlc && (
            <>
              <span>
                <span className="text-slate-500 dark:text-slate-400">O:</span>{' '}
                <span className="font-mono text-slate-900 dark:text-slate-100">{crosshairData.ohlc.o}</span>
              </span>
              <span>
                <span className="text-slate-500 dark:text-slate-400">H:</span>{' '}
                <span className="font-mono text-slate-900 dark:text-slate-100">{crosshairData.ohlc.h}</span>
              </span>
              <span>
                <span className="text-slate-500 dark:text-slate-400">L:</span>{' '}
                <span className="font-mono text-slate-900 dark:text-slate-100">{crosshairData.ohlc.l}</span>
              </span>
              <span>
                <span className="text-slate-500 dark:text-slate-400">C:</span>{' '}
                <span className="font-mono font-medium text-slate-900 dark:text-slate-100">{crosshairData.ohlc.c}</span>
              </span>
            </>
          )}
        </div>
      )}

      {/* Main Candlestick Chart */}
      <div ref={chartContainerRef} className="w-full" />

      {/* Oscillator Pane — only rendered when there are oscillator indicators */}
      {hasOscillators && (
        <>
          {/* Divider with label */}
          <div className="flex items-center gap-2 px-4 py-1 border-t border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/30">
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wide">
              RSI
            </span>
            {showIndicators &&
              oscillatorIndicators.map(ind => (
                <span key={ind.name} className="flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400">
                  <span
                    className="inline-block w-3 h-0.5 rounded"
                    style={{ backgroundColor: ind.color }}
                  />
                  {ind.name}
                </span>
              ))}
            <span className="ml-auto text-xs text-slate-400 dark:text-slate-500">30 / 70</span>
          </div>
          <div ref={oscillatorContainerRef} className="w-full" />
        </>
      )}

      {/* Chart Legend */}
      <div className="px-4 py-3 border-t border-slate-200 dark:border-slate-700 flex flex-wrap items-center gap-4 text-xs">
        {/* Overlay Indicator Legend */}
        {showIndicators &&
          overlayIndicators.map(indicator => (
            <div key={indicator.name} className="flex items-center gap-1.5">
              <div className="w-4 h-0.5 rounded" style={{ backgroundColor: indicator.color }} />
              <span className="text-slate-600 dark:text-slate-400">{indicator.name}</span>
            </div>
          ))}

        <div className="h-3 w-px bg-slate-200 dark:bg-slate-700" />

        {/* Signal Legend */}
        <div className="flex items-center gap-1.5">
          <div className="w-0 h-0 border-l-4 border-r-4 border-b-8 border-transparent border-b-green-500" />
          <span className="text-slate-600 dark:text-slate-400">Long Entry</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-0 h-0 border-l-4 border-r-4 border-t-8 border-transparent border-t-red-500" />
          <span className="text-slate-600 dark:text-slate-400">Short Entry</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded-full bg-green-500" />
          <span className="text-slate-600 dark:text-slate-400">Profit Exit</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-3 h-3 rounded-full bg-red-500" />
          <span className="text-slate-600 dark:text-slate-400">Loss Exit</span>
        </div>
      </div>
    </div>
  );
}
