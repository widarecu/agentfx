import React, { useState, useEffect, useCallback, useRef } from 'react';
import { marketDataService, Instrument, Candle, Timeframe } from '../services/marketData';
import { RefreshCw, AlertCircle, Clock } from 'lucide-react';

const MarketData: React.FC = () => {
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [selectedInstrument, setSelectedInstrument] = useState<string>('');
  const [selectedTimeframe, setSelectedTimeframe] = useState<Timeframe>('M1');
  const [candles, setCandles] = useState<Candle[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string>('');
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);

  const pollingInterval = useRef<NodeJS.Timeout | null>(null);
  const timeframes: Timeframe[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN1'];

  // Fetch candles
  const fetchCandles = useCallback(async () => {
    if (!selectedInstrument) return;

    try {
      setLoading(true);
      setError('');
      const response = await marketDataService.getCandles(
        'oanda',
        selectedInstrument,
        selectedTimeframe,
        { limit: 100 }
      );
      setCandles(response.items);
      setLastRefresh(new Date());
    } catch (err) {
      setError('Failed to fetch candle data. Please try again.');
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [selectedInstrument, selectedTimeframe]);

  // Start polling for updates
  const startPolling = useCallback(() => {
    if (pollingInterval.current) {
      clearInterval(pollingInterval.current);
    }

    if (autoRefresh && selectedInstrument && selectedTimeframe) {
      pollingInterval.current = setInterval(() => {
        fetchCandles();
      }, 60000); // Refresh every 60 seconds
    }
  }, [autoRefresh, selectedInstrument, selectedTimeframe, fetchCandles]);

  // Stop polling
  const stopPolling = useCallback(() => {
    if (pollingInterval.current) {
      clearInterval(pollingInterval.current);
      pollingInterval.current = null;
    }
  }, []);

  // Fetch instruments on component mount
  useEffect(() => {
    const fetchInstruments = async () => {
      try {
        setLoading(true);
        const data = await marketDataService.getInstruments();
        setInstruments(data);
        if (data.length > 0 && !selectedInstrument) {
          setSelectedInstrument(data[0].symbol);
        }
      } catch (err) {
        setError('Failed to fetch instruments. Please check your connection.');
        console.error(err);
      } finally {
        setLoading(false);
      }
    };

    fetchInstruments();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch candles when instrument or timeframe changes
  useEffect(() => {
    if (selectedInstrument && selectedTimeframe) {
      fetchCandles();
      startPolling();
    }

    return () => stopPolling();
  }, [selectedInstrument, selectedTimeframe]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // Get the most recent candle as "current price"
  const latestCandle = candles.length > 0 ? candles[0] : null;

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
            disabled={loading}
          >
            {timeframes.map((timeframe) => (
              <option key={timeframe} value={timeframe}>
                {timeframe}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-end">
          <button
            onClick={fetchCandles}
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
              onClick={fetchCandles}
              className="mt-2 text-sm text-red-600 dark:text-red-400 hover:underline"
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {/* Loading State */}
      {loading && (
        <div className="flex justify-center py-8">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        </div>
      )}

      {/* Candles Table */}
      {!loading && candles.length > 0 && (
        <div className="bg-white dark:bg-slate-800 shadow rounded-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-200 dark:border-slate-700 flex items-center justify-between">
            <h2 className="text-lg font-medium text-gray-900 dark:text-white">
              Historical Candles - {selectedInstrument} ({selectedTimeframe})
            </h2>
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
                {candles.map((candle, index) => {
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
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* No Data State */}
      {!loading && !error && candles.length === 0 && selectedInstrument && (
        <div className="text-center py-8 bg-white dark:bg-slate-800 rounded-lg border border-gray-200 dark:border-slate-700">
          <p className="text-gray-500 dark:text-gray-400">No candle data available for {selectedInstrument}</p>
          <button
            onClick={fetchCandles}
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
