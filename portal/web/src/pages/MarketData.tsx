import React, { useState, useEffect, useCallback, useRef } from 'react';
import { marketDataService, Instrument, Candle, Timeframe, TimeframeInfo } from '../services/marketData';
import { RefreshCw, AlertCircle, Clock } from 'lucide-react';
import CandlestickChart from '../components/CandlestickChart';

const CHART_PAGE_SIZE = 10000;
const TABLE_PAGE_SIZE = 50;

const MarketData: React.FC = () => {
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [selectedInstrument, setSelectedInstrument] = useState<string>('');
  const [timeframes, setTimeframes] = useState<TimeframeInfo[]>([]);
  const [selectedTimeframe, setSelectedTimeframe] = useState<Timeframe>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);

  // Chart state — accumulated candles, grows as user scrolls left
  const [chartCandles, setChartCandles] = useState<Candle[]>([]);
  const [chartTotal, setChartTotal] = useState<number>(0);
  const [chartLoading, setChartLoading] = useState<boolean>(false);
  const chartOffsetRef = useRef<number>(0);

  // Table state — server-side pagination
  const [tableCandles, setTableCandles] = useState<Candle[]>([]);
  const [tableTotal, setTableTotal] = useState<number>(0);
  const [tablePage, setTablePage] = useState<number>(1);
  const [tableLoading, setTableLoading] = useState<boolean>(false);

  const pollingInterval = useRef<NodeJS.Timeout | null>(null);

  // Fetch chart candles (initial load or load-more)
  const fetchChartCandles = useCallback(async (offset: number, append: boolean = false) => {
    if (!selectedInstrument || !selectedTimeframe) return;

    try {
      setChartLoading(true);
      const response = await marketDataService.getCandles(
        'oanda',
        selectedInstrument,
        selectedTimeframe,
        { limit: CHART_PAGE_SIZE, offset }
      );

      setChartTotal(response.total);

      if (append && offset > 0) {
        // Prepend older candles (API returns DESC, so new batch is older)
        setChartCandles(prev => [...prev, ...response.items]);
      } else {
        setChartCandles(response.items);
      }

      chartOffsetRef.current = offset + response.items.length;
    } catch (err) {
      console.error('Failed to fetch chart candles:', err);
    } finally {
      setChartLoading(false);
    }
  }, [selectedInstrument, selectedTimeframe]);

  // Fetch a specific page of table candles
  const fetchTablePage = useCallback(async (page: number) => {
    if (!selectedInstrument || !selectedTimeframe) return;

    try {
      setTableLoading(true);
      const offset = (page - 1) * TABLE_PAGE_SIZE;
      const response = await marketDataService.getCandles(
        'oanda',
        selectedInstrument,
        selectedTimeframe,
        { limit: TABLE_PAGE_SIZE, offset }
      );

      setTableCandles(response.items);
      setTableTotal(response.total);
      setLastRefresh(new Date());
    } catch (err) {
      setError('Failed to fetch candle data. Please try again.');
      console.error(err);
    } finally {
      setTableLoading(false);
    }
  }, [selectedInstrument, selectedTimeframe]);

  // Refresh both chart and table
  const refreshAll = useCallback(async () => {
    if (!selectedInstrument || !selectedTimeframe) return;
    setLoading(true);
    setError('');
    try {
      await Promise.all([
        fetchChartCandles(0),
        fetchTablePage(tablePage),
      ]);
    } finally {
      setLoading(false);
    }
  }, [selectedInstrument, selectedTimeframe, tablePage, fetchChartCandles, fetchTablePage]);

  // Chart load-more callback
  const handleChartLoadMore = useCallback(() => {
    if (chartLoading || chartOffsetRef.current >= chartTotal) return;
    fetchChartCandles(chartOffsetRef.current, true);
  }, [chartLoading, chartTotal, fetchChartCandles]);

  // Polling
  const startPolling = useCallback(() => {
    if (pollingInterval.current) {
      clearInterval(pollingInterval.current);
    }
    if (autoRefresh && selectedInstrument && selectedTimeframe) {
      pollingInterval.current = setInterval(() => {
        refreshAll();
      }, 60000);
    }
  }, [autoRefresh, selectedInstrument, selectedTimeframe, refreshAll]);

  const stopPolling = useCallback(() => {
    if (pollingInterval.current) {
      clearInterval(pollingInterval.current);
      pollingInterval.current = null;
    }
  }, []);

  // Fetch instruments and timeframes on mount
  useEffect(() => {
    const fetchInitialData = async () => {
      try {
        setLoading(true);
        const [instrumentsData, timeframesData] = await Promise.all([
          marketDataService.getInstruments(),
          marketDataService.getTimeframes(),
        ]);
        setInstruments(instrumentsData);
        setTimeframes(timeframesData);
        if (instrumentsData.length > 0 && !selectedInstrument) {
          setSelectedInstrument(instrumentsData[0].symbol);
        }
        if (timeframesData.length > 0 && !selectedTimeframe) {
          const m1 = timeframesData.find(tf => tf.code === 'M1');
          setSelectedTimeframe(m1 ? m1.code : timeframesData[0].code);
        }
      } catch (err) {
        setError('Failed to fetch instruments. Please check your connection.');
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchInitialData();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch data when instrument or timeframe changes
  useEffect(() => {
    if (selectedInstrument && selectedTimeframe) {
      setTablePage(1);
      chartOffsetRef.current = 0;
      setChartCandles([]);
      setTableCandles([]);
      fetchChartCandles(0);
      fetchTablePage(1);
      startPolling();
    }
    return () => stopPolling();
  }, [selectedInstrument, selectedTimeframe]); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch table page when tablePage changes (user clicks prev/next)
  useEffect(() => {
    if (selectedInstrument && selectedTimeframe && tablePage > 0) {
      fetchTablePage(tablePage);
    }
  }, [tablePage]); // eslint-disable-line react-hooks/exhaustive-deps

  // Toggle auto-refresh
  useEffect(() => {
    if (autoRefresh) {
      startPolling();
    } else {
      stopPolling();
    }
  }, [autoRefresh]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleInstrumentChange = (instrument: string) => {
    setSelectedInstrument(instrument);
  };

  const handleTimeframeChange = (timeframe: Timeframe) => {
    setSelectedTimeframe(timeframe);
  };

  const formatPrice = (price: number, precision: number = 5): string => {
    return price.toFixed(precision);
  };

  const formatTimestamp = (timestamp: string): string => {
    return new Date(timestamp).toLocaleString();
  };

  const getPriceChange = (candle: Candle): number => {
    return candle.close - candle.open;
  };

  const getPriceChangePercent = (candle: Candle): number => {
    return ((candle.close - candle.open) / candle.open) * 100;
  };

  // Latest candle from chart data (first item = most recent, DESC order)
  const latestCandle = chartCandles.length > 0 ? chartCandles[0] : null;

  // Table pagination
  const tableTotalPages = Math.max(1, Math.ceil(tableTotal / TABLE_PAGE_SIZE));
  const chartHasMore = chartOffsetRef.current < chartTotal;

  return (
    <div className="p-6 dark:bg-slate-900 min-h-screen">
      <div className="mb-6">
        <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2">Market Data</h1>
        <p className="text-gray-600 dark:text-gray-400">Historical market data for trading instruments</p>
      </div>

      {/* Status Banner */}
      <div className="mb-4 p-4 rounded-lg border bg-blue-50 border-blue-200 dark:bg-blue-900/20 dark:border-blue-800">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Clock className="h-5 w-5 text-blue-600 dark:text-blue-400" />
            <div>
              <span className="text-blue-800 dark:text-blue-300 font-medium">
                {autoRefresh ? 'Auto-refresh enabled' : 'Auto-refresh disabled'}
              </span>
              <p className="text-xs text-blue-600 dark:text-blue-400 mt-0.5">
                {autoRefresh ? 'Data refreshes every 60 seconds' : 'Click refresh to update data'}
              </p>
            </div>
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="w-4 h-4 text-blue-600 border-gray-300 rounded focus:ring-blue-500"
            />
            <span className="text-sm text-blue-700 dark:text-blue-300">Auto-refresh</span>
          </label>
        </div>
      </div>

      {/* Current Price Display */}
      {latestCandle && (
        <div className="mb-6 p-4 bg-green-50 dark:bg-green-900/20 rounded-lg border border-green-200 dark:border-green-800">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-lg font-semibold text-green-900 dark:text-green-100">
              Latest Candle - {selectedInstrument}
            </h3>
            <span className="text-xs text-gray-500 dark:text-gray-400">
              {formatTimestamp(latestCandle.timestamp)}
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div>
              <span className="text-sm text-gray-600 dark:text-gray-400">Open:</span>
              <div className="text-lg font-mono dark:text-white">{formatPrice(latestCandle.open)}</div>
            </div>
            <div>
              <span className="text-sm text-gray-600 dark:text-gray-400">High:</span>
              <div className="text-lg font-mono dark:text-white">{formatPrice(latestCandle.high)}</div>
            </div>
            <div>
              <span className="text-sm text-gray-600 dark:text-gray-400">Low:</span>
              <div className="text-lg font-mono dark:text-white">{formatPrice(latestCandle.low)}</div>
            </div>
            <div>
              <span className="text-sm text-gray-600 dark:text-gray-400">Close:</span>
              <div className="text-lg font-mono font-bold dark:text-white">{formatPrice(latestCandle.close)}</div>
            </div>
            <div>
              <span className="text-sm text-gray-600 dark:text-gray-400">Volume:</span>
              <div className="text-lg font-mono dark:text-white">{latestCandle.volume.toLocaleString()}</div>
            </div>
          </div>
        </div>
      )}

      {/* Controls */}
      <div className="mb-6 grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <label htmlFor="instrument-select" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            Instrument
          </label>
          <select
            id="instrument-select"
            value={selectedInstrument}
            onChange={(e) => handleInstrumentChange(e.target.value)}
            className="w-full p-2 border border-gray-300 dark:border-slate-600 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white dark:bg-slate-800 text-gray-900 dark:text-white"
            disabled={loading}
          >
            <option value="">Select an instrument</option>
            {instruments.map((instrument) => (
              <option key={instrument.symbol} value={instrument.symbol}>
                {instrument.symbol} - {instrument.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="timeframe-select" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            Timeframe
          </label>
          <select
            id="timeframe-select"
            value={selectedTimeframe}
            onChange={(e) => handleTimeframeChange(e.target.value as Timeframe)}
            className="w-full p-2 border border-gray-300 dark:border-slate-600 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white dark:bg-slate-800 text-gray-900 dark:text-white"
            disabled={loading || timeframes.length === 0}
          >
            {timeframes.length === 0 && <option value="">Loading...</option>}
            {timeframes.map((tf) => (
              <option key={tf.code} value={tf.code}>
                {tf.display_name}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-end">
          <button
            onClick={refreshAll}
            disabled={loading || !selectedInstrument}
            className="w-full p-2 flex items-center justify-center gap-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition-colors"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh Data
          </button>
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <div className="mb-4 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-md flex items-start gap-3">
          <AlertCircle className="h-5 w-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-red-800 dark:text-red-300">{error}</p>
            <button
              onClick={refreshAll}
              className="mt-2 text-sm text-red-600 dark:text-red-400 hover:underline"
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {/* Candlestick Chart */}
      {chartCandles.length > 0 && (
        <div className="mb-6">
          <CandlestickChart
            candles={chartCandles}
            instrument={selectedInstrument}
            timeframe={selectedTimeframe}
            isLoading={loading && chartCandles.length === 0}
            error={error || null}
            onLoadMore={handleChartLoadMore}
            hasMore={chartHasMore}
            loadingMore={chartLoading}
          />
        </div>
      )}

      {/* Loading State (initial load only) */}
      {loading && chartCandles.length === 0 && tableCandles.length === 0 && (
        <div className="flex justify-center py-8">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        </div>
      )}

      {/* Candles Table */}
      {(tableCandles.length > 0 || tableTotal > 0) && (
        <div className="bg-white dark:bg-slate-800 shadow rounded-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-200 dark:border-slate-700 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-medium text-gray-900 dark:text-white">
                Historical Candles - {selectedInstrument} ({selectedTimeframe})
              </h2>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                {tableTotal.toLocaleString()} candles available
              </p>
            </div>
            {lastRefresh && (
              <span className="text-xs text-gray-500 dark:text-gray-400">
                Updated: {lastRefresh.toLocaleTimeString()}
              </span>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 dark:divide-slate-700">
              <thead className="bg-gray-50 dark:bg-slate-900">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Timestamp
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Open
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    High
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Low
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Close
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Change
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Change %
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    Volume
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white dark:bg-slate-800 divide-y divide-gray-200 dark:divide-slate-700">
                {tableLoading ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-gray-500 dark:text-gray-400">
                      <div className="flex items-center justify-center gap-2">
                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600"></div>
                        Loading page...
                      </div>
                    </td>
                  </tr>
                ) : (
                  tableCandles.map((candle, index) => {
                    const priceChange = getPriceChange(candle);
                    const priceChangePercent = getPriceChangePercent(candle);
                    const isPositive = priceChange >= 0;

                    return (
                      <tr key={index} className="hover:bg-gray-50 dark:hover:bg-slate-700">
                        <td className="px-4 py-2 text-sm text-gray-900 dark:text-gray-100">
                          {formatTimestamp(candle.timestamp)}
                        </td>
                        <td className="px-4 py-2 text-sm font-mono text-gray-900 dark:text-gray-100">
                          {formatPrice(candle.open)}
                        </td>
                        <td className="px-4 py-2 text-sm font-mono text-gray-900 dark:text-gray-100">
                          {formatPrice(candle.high)}
                        </td>
                        <td className="px-4 py-2 text-sm font-mono text-gray-900 dark:text-gray-100">
                          {formatPrice(candle.low)}
                        </td>
                        <td className="px-4 py-2 text-sm font-mono font-medium text-gray-900 dark:text-gray-100">
                          {formatPrice(candle.close)}
                        </td>
                        <td className={`px-4 py-2 text-sm font-mono font-medium ${
                          isPositive ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'
                        }`}>
                          {isPositive ? '+' : ''}{formatPrice(priceChange)}
                        </td>
                        <td className={`px-4 py-2 text-sm font-mono font-medium ${
                          isPositive ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'
                        }`}>
                          {isPositive ? '+' : ''}{priceChangePercent.toFixed(2)}%
                        </td>
                        <td className="px-4 py-2 text-sm font-mono text-gray-900 dark:text-gray-100">
                          {candle.volume.toLocaleString()}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          {tableTotalPages > 1 && (
            <div className="px-4 py-3 border-t border-gray-200 dark:border-slate-700 flex items-center justify-between">
              <div className="text-sm text-gray-500 dark:text-gray-400">
                Page {tablePage} of {tableTotalPages.toLocaleString()}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setTablePage(p => Math.max(1, p - 1))}
                  disabled={tablePage === 1 || tableLoading}
                  className="px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-slate-800 border border-gray-300 dark:border-slate-600 rounded-md hover:bg-gray-50 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  Previous
                </button>
                <button
                  onClick={() => setTablePage(p => Math.min(tableTotalPages, p + 1))}
                  disabled={tablePage === tableTotalPages || tableLoading}
                  className="px-3 py-1.5 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-slate-800 border border-gray-300 dark:border-slate-600 rounded-md hover:bg-gray-50 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* No Data State */}
      {!loading && !error && chartCandles.length === 0 && tableCandles.length === 0 && selectedInstrument && (
        <div className="text-center py-8 bg-white dark:bg-slate-800 rounded-lg border border-gray-200 dark:border-slate-700">
          <p className="text-gray-500 dark:text-gray-400">No candle data available for {selectedInstrument}</p>
          <button
            onClick={refreshAll}
            className="mt-4 text-blue-600 dark:text-blue-400 hover:underline text-sm"
          >
            Try fetching again
          </button>
        </div>
      )}
    </div>
  );
};

export default MarketData;
