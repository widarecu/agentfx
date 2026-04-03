/**
 * StrategyChart Component
 *
 * Candlestick chart with:
 * - OHLC candles (green up, red down)
 * - Entry markers (triangles)
 * - Exit markers (circles with P&L color)
 * - Indicator overlays (MA lines, RSI panel, Donchian bands)
 * - Crosshair with price/time on hover
 * - Zoom/pan controls
 *
 * Uses lightweight-charts library for high performance
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
} from 'lightweight-charts';
import { ZoomIn, ZoomOut, Maximize2, RefreshCw } from 'lucide-react';
import { CandleData, TradeSignal, IndicatorLine } from '../types/optimization';

interface StrategyChartProps {
  candles: CandleData[];
  signals: TradeSignal[];
  indicators: IndicatorLine[];
  instrument: string;
  strategy: string;
  params: Record<string, number>;
  isLoading?: boolean;
  error?: string | null;
}

export default function StrategyChart({
  candles,
  signals,
  indicators,
  instrument,
  strategy,
  params,
  isLoading = false,
  error = null,
}: StrategyChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const indicatorSeriesRefs = useRef<ISeriesApi<'Line'>[]>([]);
  const [chartError, setChartError] = useState<string | null>(null);
  const [showIndicators, setShowIndicators] = useState(true);
  const [crosshairData, setCrosshairData] = useState<{
    time: string;
    price: string;
    ohlc?: { o: string; h: string; l: string; c: string };
  } | null>(null);

  // Convert candle timestamp to UTCTimestamp
  const toChartTime = (timestamp: string): UTCTimestamp => {
    return (new Date(timestamp).getTime() / 1000) as UTCTimestamp;
  };

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    try {
      // Check for dark mode
      const isDarkMode = document.documentElement.classList.contains('dark');

      // Create chart
      const chart = createChart(chartContainerRef.current, {
        layout: {
          background: { color: 'transparent' },
          textColor: isDarkMode ? '#9ca3af' : '#6b7280',
        },
        grid: {
          vertLines: { color: isDarkMode ? '#1f2937' : '#e5e7eb' },
          horzLines: { color: isDarkMode ? '#1f2937' : '#e5e7eb' },
        },
        width: chartContainerRef.current.clientWidth,
        height: 500,
        timeScale: {
          timeVisible: true,
          secondsVisible: false,
          borderColor: isDarkMode ? '#374151' : '#d1d5db',
        },
        rightPriceScale: {
          borderColor: isDarkMode ? '#374151' : '#d1d5db',
          scaleMargins: {
            top: 0.1,
            bottom: 0.1,
          },
        },
        crosshair: {
          mode: CrosshairMode.Normal,
          vertLine: {
            color: isDarkMode ? '#6b7280' : '#9ca3af',
            width: 1,
            style: 1,
            labelBackgroundColor: isDarkMode ? '#374151' : '#e5e7eb',
          },
          horzLine: {
            color: isDarkMode ? '#6b7280' : '#9ca3af',
            width: 1,
            style: 1,
            labelBackgroundColor: isDarkMode ? '#374151' : '#e5e7eb',
          },
        },
      });

      // Create candlestick series
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

      // Handle crosshair move for price display
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

      // Handle resize
      const handleResize = () => {
        if (chartContainerRef.current && chartRef.current) {
          chartRef.current.applyOptions({
            width: chartContainerRef.current.clientWidth,
          });
        }
      };

      window.addEventListener('resize', handleResize);

      return () => {
        window.removeEventListener('resize', handleResize);
        chart.remove();
        chartRef.current = null;
        candlestickSeriesRef.current = null;
        indicatorSeriesRefs.current = [];
      };
    } catch (err) {
      console.error('Failed to initialize chart:', err);
      setChartError(err instanceof Error ? err.message : 'Failed to initialize chart');
    }
  }, []);

  // Update candle data
  useEffect(() => {
    if (!candlestickSeriesRef.current || candles.length === 0) return;

    try {
      // Convert candles to chart format
      const chartData: CandlestickData[] = candles.map(candle => ({
        time: toChartTime(candle.timestamp),
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      }));

      // Sort by time
      chartData.sort((a, b) => (a.time as number) - (b.time as number));

      // Set data
      candlestickSeriesRef.current.setData(chartData);

      // Filter signals to candle time range to prevent markers stacking off-screen
      const candleTimeMin = chartData[0]?.time as number ?? 0;
      const candleTimeMax = chartData[chartData.length - 1]?.time as number ?? Infinity;
      const filteredSignals = signals.filter(s => {
        const t = toChartTime(s.timestamp);
        return t >= candleTimeMin && t <= candleTimeMax;
      });

      // Add trade signal markers
      const markers: SeriesMarker<Time>[] = filteredSignals.map(signal => {
        const isEntry = signal.signalType.startsWith('entry');
        const isLong = signal.signalType.includes('long');
        const isProfitable = signal.pnl !== undefined && signal.pnl > 0;

        let color: string;
        let shape: 'arrowUp' | 'arrowDown' | 'circle';
        let position: 'belowBar' | 'aboveBar';
        let text: string;

        if (isEntry) {
          // Entry markers
          shape = isLong ? 'arrowUp' : 'arrowDown';
          color = isLong ? '#22c55e' : '#ef4444';
          position = isLong ? 'belowBar' : 'aboveBar';
          text = isLong ? 'L' : 'S';
        } else {
          // Exit markers
          shape = 'circle';
          color = isProfitable ? '#22c55e' : '#ef4444';
          position = 'aboveBar';
          text = signal.pnl ? (signal.pnl > 0 ? `+${(signal.pnl * 10000).toFixed(0)}` : `${(signal.pnl * 10000).toFixed(0)}`) : 'X';
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

      // lightweight-charts requires markers sorted by time ascending
      markers.sort((a, b) => (a.time as number) - (b.time as number));
      candlestickSeriesRef.current.setMarkers(markers);

      // Fit content
      if (chartRef.current) {
        chartRef.current.timeScale().fitContent();
      }

      setChartError(null);
    } catch (err) {
      console.error('Failed to update chart data:', err);
      setChartError(err instanceof Error ? err.message : 'Failed to update chart data');
    }
  }, [candles, signals]);

  // Update indicator lines
  useEffect(() => {
    if (!chartRef.current) return;

    // Remove existing indicator series
    indicatorSeriesRefs.current.forEach(series => {
      try {
        chartRef.current?.removeSeries(series);
      } catch {
        // Series may already be removed
      }
    });
    indicatorSeriesRefs.current = [];

    if (!showIndicators || indicators.length === 0) return;

    try {
      // Add new indicator series
      indicators.forEach(indicator => {
        const lineSeries = chartRef.current!.addLineSeries({
          color: indicator.color,
          lineWidth: 2,
          title: indicator.name,
          priceLineVisible: false,
          lastValueVisible: false,
        });

        const lineData: LineData[] = indicator.data.map(point => ({
          time: toChartTime(point.timestamp),
          value: point.value,
        }));

        lineData.sort((a, b) => (a.time as number) - (b.time as number));
        lineSeries.setData(lineData);
        indicatorSeriesRefs.current.push(lineSeries);
      });
    } catch (err) {
      console.error('Failed to update indicators:', err);
    }
  }, [indicators, showIndicators]);

  // Chart control handlers
  const handleZoomIn = () => {
    if (chartRef.current) {
      const timeScale = chartRef.current.timeScale();
      const visibleRange = timeScale.getVisibleRange();
      if (visibleRange) {
        const center = ((visibleRange.from as number) + (visibleRange.to as number)) / 2;
        const halfRange = ((visibleRange.to as number) - (visibleRange.from as number)) / 4;
        timeScale.setVisibleRange({
          from: (center - halfRange) as UTCTimestamp,
          to: (center + halfRange) as UTCTimestamp,
        });
      }
    }
  };

  const handleZoomOut = () => {
    if (chartRef.current) {
      const timeScale = chartRef.current.timeScale();
      const visibleRange = timeScale.getVisibleRange();
      if (visibleRange) {
        const center = ((visibleRange.from as number) + (visibleRange.to as number)) / 2;
        const halfRange = ((visibleRange.to as number) - (visibleRange.from as number));
        timeScale.setVisibleRange({
          from: (center - halfRange) as UTCTimestamp,
          to: (center + halfRange) as UTCTimestamp,
        });
      }
    }
  };

  const handleReset = () => {
    if (chartRef.current) {
      chartRef.current.timeScale().fitContent();
    }
  };

  // Loading state
  if (isLoading) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <div className="flex items-center justify-center h-[500px]">
          <div className="flex flex-col items-center gap-3">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400"></div>
            <p className="text-sm text-slate-600 dark:text-slate-400">Loading chart data...</p>
          </div>
        </div>
      </div>
    );
  }

  // Error state
  const displayError = error || chartError;
  if (displayError) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-red-200 dark:border-red-800 rounded-lg p-6">
        <div className="flex items-center justify-center h-[500px]">
          <div className="flex flex-col items-center gap-3 text-center max-w-md">
            <div className="w-12 h-12 rounded-full bg-red-100 dark:bg-red-900/50 flex items-center justify-center">
              <svg
                className="w-6 h-6 text-red-600 dark:text-red-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                />
              </svg>
            </div>
            <p className="text-sm font-medium text-red-600 dark:text-red-400">
              Failed to load chart
            </p>
            <p className="text-xs text-slate-600 dark:text-slate-400">{displayError}</p>
          </div>
        </div>
      </div>
    );
  }

  // No data state
  if (candles.length === 0) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <div className="flex items-center justify-center h-[500px]">
          <div className="flex flex-col items-center gap-3 text-center">
            <div className="w-12 h-12 rounded-full bg-slate-100 dark:bg-slate-700 flex items-center justify-center">
              <svg
                className="w-6 h-6 text-slate-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"
                />
              </svg>
            </div>
            <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
              No chart data available
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg overflow-hidden">
      {/* Chart Header */}
      <div className="px-4 py-3 border-b border-slate-200 dark:border-slate-700 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            {instrument}
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Strategy: {strategy.toUpperCase()} | Params:{' '}
            {Object.entries(params)
              .map(([k, v]) => `${k}=${v}`)
              .join(', ')}
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
                <span className="font-mono text-slate-900 dark:text-slate-100">
                  {crosshairData.ohlc.o}
                </span>
              </span>
              <span>
                <span className="text-slate-500 dark:text-slate-400">H:</span>{' '}
                <span className="font-mono text-slate-900 dark:text-slate-100">
                  {crosshairData.ohlc.h}
                </span>
              </span>
              <span>
                <span className="text-slate-500 dark:text-slate-400">L:</span>{' '}
                <span className="font-mono text-slate-900 dark:text-slate-100">
                  {crosshairData.ohlc.l}
                </span>
              </span>
              <span>
                <span className="text-slate-500 dark:text-slate-400">C:</span>{' '}
                <span className="font-mono font-medium text-slate-900 dark:text-slate-100">
                  {crosshairData.ohlc.c}
                </span>
              </span>
            </>
          )}
        </div>
      )}

      {/* Chart Container */}
      <div ref={chartContainerRef} className="w-full" />

      {/* Chart Legend */}
      <div className="px-4 py-3 border-t border-slate-200 dark:border-slate-700 flex flex-wrap items-center gap-4 text-xs">
        {/* Indicator Legend */}
        {showIndicators &&
          indicators.map(indicator => (
            <div key={indicator.name} className="flex items-center gap-1.5">
              <div
                className="w-4 h-0.5 rounded"
                style={{ backgroundColor: indicator.color }}
              />
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
