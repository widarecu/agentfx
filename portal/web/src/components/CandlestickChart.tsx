import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createChart, IChartApi, ISeriesApi, CandlestickData, UTCTimestamp } from 'lightweight-charts';
import { Candle } from '../services/marketData';
import { AlertCircle, TrendingUp, Loader2 } from 'lucide-react';

interface CandlestickChartProps {
  candles: Candle[];
  instrument: string;
  timeframe: string;
  isLoading?: boolean;
  error?: string | null;
  onLoadMore?: () => void;
  hasMore?: boolean;
  loadingMore?: boolean;
}

export default function CandlestickChart({
  candles,
  instrument,
  timeframe,
  isLoading = false,
  error = null,
  onLoadMore,
  hasMore = false,
  loadingMore = false,
}: CandlestickChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);
  const earliestTimeRef = useRef<number | null>(null);
  const loadMoreCalledRef = useRef<boolean>(false);
  const candleMapRef = useRef<Map<number, Candle>>(new Map());
  const [tooltipData, setTooltipData] = useState<{
    candle: Candle;
    x: number;
    y: number;
  } | null>(null);

  // Reset load-more guard when loadingMore becomes false (fetch completed)
  useEffect(() => {
    if (!loadingMore) {
      loadMoreCalledRef.current = false;
    }
  }, [loadingMore]);

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    try {
      const chart = createChart(chartContainerRef.current, {
        layout: {
          background: { color: 'transparent' },
          textColor: '#9ca3af',
        },
        grid: {
          vertLines: { color: '#1f2937' },
          horzLines: { color: '#1f2937' },
        },
        width: chartContainerRef.current.clientWidth,
        height: 400,
        timeScale: {
          timeVisible: true,
          secondsVisible: false,
          borderColor: '#374151',
        },
        rightPriceScale: {
          borderColor: '#374151',
        },
        crosshair: {
          vertLine: {
            color: '#6b7280',
            width: 1,
            style: 1,
            labelBackgroundColor: '#374151',
          },
          horzLine: {
            color: '#6b7280',
            width: 1,
            style: 1,
            labelBackgroundColor: '#374151',
          },
        },
      });

      const candlestickSeries = chart.addCandlestickSeries({
        upColor: '#10b981',
        downColor: '#ef4444',
        borderUpColor: '#10b981',
        borderDownColor: '#ef4444',
        wickUpColor: '#10b981',
        wickDownColor: '#ef4444',
      });

      chartRef.current = chart;
      candlestickSeriesRef.current = candlestickSeries;

      // Subscribe to crosshair move for tooltip
      chart.subscribeCrosshairMove((param) => {
        if (
          !param.time ||
          !param.point ||
          param.point.x < 0 ||
          param.point.y < 0
        ) {
          setTooltipData(null);
          return;
        }

        const ts = param.time as number;
        const candle = candleMapRef.current.get(ts);
        if (candle) {
          setTooltipData({ candle, x: param.point.x, y: param.point.y });
        } else {
          setTooltipData(null);
        }
      });

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
      };
    } catch (err) {
      console.error('Failed to initialize chart:', err);
      setChartError(err instanceof Error ? err.message : 'Failed to initialize chart');
    }
  }, []);

  // Update chart data when candles change
  useEffect(() => {
    if (!candlestickSeriesRef.current || candles.length === 0) return;

    try {
      // Build lookup map from UTC timestamp to candle data
      const newMap = new Map<number, Candle>();
      const chartData: CandlestickData[] = candles.map((candle) => {
        const ts = (new Date(candle.timestamp).getTime() / 1000) as UTCTimestamp;
        newMap.set(ts as number, candle);
        return {
          time: ts,
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        };
      });
      candleMapRef.current = newMap;

      chartData.sort((a, b) => (a.time as number) - (b.time as number));

      // Track earliest loaded time for scroll detection
      if (chartData.length > 0) {
        earliestTimeRef.current = chartData[0].time as number;
      }

      candlestickSeriesRef.current.setData(chartData);

      // Only fit content on initial load (not on load-more to preserve scroll position)
      if (chartRef.current && !loadingMore) {
        chartRef.current.timeScale().fitContent();
      }

      setChartError(null);
    } catch (err) {
      console.error('Failed to update chart data:', err);
      setChartError(err instanceof Error ? err.message : 'Failed to update chart data');
    }
  }, [candles, loadingMore]);

  // Subscribe to visible range changes for scroll-to-load-more
  useEffect(() => {
    if (!chartRef.current || !onLoadMore) return;

    const chart = chartRef.current;
    const handler = () => {
      if (!hasMore || loadingMore || loadMoreCalledRef.current) return;
      if (earliestTimeRef.current === null) return;

      const visibleRange = chart.timeScale().getVisibleRange();
      if (!visibleRange) return;

      const visibleFrom = visibleRange.from as number;
      if (visibleFrom <= earliestTimeRef.current) {
        loadMoreCalledRef.current = true;
        onLoadMore();
      }
    };

    chart.timeScale().subscribeVisibleTimeRangeChange(handler);

    return () => {
      chart.timeScale().unsubscribeVisibleTimeRangeChange(handler);
    };
  }, [onLoadMore, hasMore, loadingMore]);

  // Update chart theme on dark mode changes
  useEffect(() => {
    if (!chartRef.current) return;

    const isDarkMode = document.documentElement.classList.contains('dark');

    chartRef.current.applyOptions({
      layout: {
        background: { color: 'transparent' },
        textColor: isDarkMode ? '#9ca3af' : '#6b7280',
      },
      grid: {
        vertLines: { color: isDarkMode ? '#1f2937' : '#e5e7eb' },
        horzLines: { color: isDarkMode ? '#1f2937' : '#e5e7eb' },
      },
      timeScale: {
        borderColor: isDarkMode ? '#374151' : '#d1d5db',
      },
      rightPriceScale: {
        borderColor: isDarkMode ? '#374151' : '#d1d5db',
      },
    });
  }, []);

  const displayError = error || chartError;

  if (isLoading) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <div className="flex items-center justify-center h-96">
          <div className="flex flex-col items-center gap-3">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 dark:border-blue-400"></div>
            <p className="text-sm text-slate-600 dark:text-slate-400">Loading chart data...</p>
          </div>
        </div>
      </div>
    );
  }

  if (displayError) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-red-200 dark:border-red-800 rounded-lg p-6">
        <div className="flex items-center justify-center h-96">
          <div className="flex flex-col items-center gap-3 text-center max-w-md">
            <AlertCircle className="h-12 w-12 text-red-600 dark:text-red-400" />
            <p className="text-sm font-medium text-red-600 dark:text-red-400">
              Failed to load chart
            </p>
            <p className="text-xs text-slate-600 dark:text-slate-400">
              {displayError}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (candles.length === 0) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <div className="flex items-center justify-center h-96">
          <div className="flex flex-col items-center gap-3 text-center">
            <TrendingUp className="h-12 w-12 text-slate-400 dark:text-slate-500" />
            <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
              No data available
            </p>
            <p className="text-xs text-slate-500 dark:text-slate-500">
              Select an instrument to view chart data
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            {instrument}
          </h3>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Timeframe: {timeframe} • {candles.length.toLocaleString()} candles loaded
            {hasMore && ' • Scroll left to load more'}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {loadingMore && (
            <div className="flex items-center gap-1.5 text-xs text-blue-600 dark:text-blue-400">
              <Loader2 className="h-3 w-3 animate-spin" />
              Loading more...
            </div>
          )}
          {candles.length > 0 && (
            <div className="text-right">
              <div className="text-xs text-slate-500 dark:text-slate-400">Latest</div>
              <div className={`text-sm font-semibold ${
                candles[candles.length - 1].close >= candles[candles.length - 1].open
                  ? 'text-green-600 dark:text-green-400'
                  : 'text-red-600 dark:text-red-400'
              }`}>
                {candles[candles.length - 1].close.toFixed(5)}
              </div>
            </div>
          )}
        </div>
      </div>
      <div ref={chartContainerRef} className="relative w-full">
        {tooltipData && (
          <div
            className="absolute z-10 pointer-events-none bg-slate-900/95 dark:bg-slate-800/95 border border-slate-600 dark:border-slate-500 rounded px-3 py-2 text-xs text-slate-100 shadow-lg"
            style={{
              left: tooltipData.x + 16,
              top: tooltipData.y - 8,
              transform: tooltipData.x > (chartContainerRef.current?.clientWidth ?? 0) - 200
                ? 'translateX(calc(-100% - 32px))'
                : undefined,
            }}
          >
            <div className="font-medium text-slate-300 mb-1">
              {new Date(tooltipData.candle.timestamp).toLocaleString()}
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
              <span className="text-slate-400">Open</span>
              <span className="text-right font-mono">{tooltipData.candle.open.toFixed(5)}</span>
              <span className="text-slate-400">High</span>
              <span className="text-right font-mono text-green-400">{tooltipData.candle.high.toFixed(5)}</span>
              <span className="text-slate-400">Low</span>
              <span className="text-right font-mono text-red-400">{tooltipData.candle.low.toFixed(5)}</span>
              <span className="text-slate-400">Close</span>
              <span className={`text-right font-mono ${
                tooltipData.candle.close >= tooltipData.candle.open
                  ? 'text-green-400'
                  : 'text-red-400'
              }`}>{tooltipData.candle.close.toFixed(5)}</span>
              <span className="text-slate-400">Volume</span>
              <span className="text-right font-mono">{tooltipData.candle.volume.toLocaleString()}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
