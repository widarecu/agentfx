import React, { useEffect, useRef, useState } from 'react';
import { createChart, IChartApi, ISeriesApi, CandlestickData, UTCTimestamp } from 'lightweight-charts';
import { Candle } from '../services/marketData';
import { AlertCircle, TrendingUp } from 'lucide-react';

interface CandlestickChartProps {
  candles: Candle[];
  instrument: string;
  timeframe: string;
  isLoading?: boolean;
  error?: string | null;
}

export default function CandlestickChart({
  candles,
  instrument,
  timeframe,
  isLoading = false,
  error = null,
}: CandlestickChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    try {
      // Create chart
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

      // Create candlestick series
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
      // Convert candles to chart format
      const chartData: CandlestickData[] = candles.map((candle) => ({
        time: (new Date(candle.timestamp).getTime() / 1000) as UTCTimestamp,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      }));

      // Sort by time
      chartData.sort((a, b) => (a.time as number) - (b.time as number));

      // Set data
      candlestickSeriesRef.current.setData(chartData);

      // Fit content to view
      if (chartRef.current) {
        chartRef.current.timeScale().fitContent();
      }

      setChartError(null);
    } catch (err) {
      console.error('Failed to update chart data:', err);
      setChartError(err instanceof Error ? err.message : 'Failed to update chart data');
    }
  }, [candles]);

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
            Timeframe: {timeframe} • {candles.length} candles
          </p>
        </div>
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
      <div ref={chartContainerRef} className="w-full" />
    </div>
  );
}
