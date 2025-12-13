import React, { useState } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { useMarketData } from '../hooks/useMarketData';
import { Timeframe } from '../services/marketData';
import UserInfoCard from '../components/UserInfoCard';
import SystemStatusCard from '../components/SystemStatusCard';
import InstrumentSelector from '../components/InstrumentSelector';
import CandlestickChart from '../components/CandlestickChart';
import { Clock, Activity, RefreshCw } from 'lucide-react';

const TIMEFRAMES: { value: Timeframe; label: string }[] = [
  { value: 'M1', label: '1 Minute' },
  { value: 'M5', label: '5 Minutes' },
  { value: 'M15', label: '15 Minutes' },
  { value: 'M30', label: '30 Minutes' },
  { value: 'H1', label: '1 Hour' },
  { value: 'H4', label: '4 Hours' },
  { value: 'D1', label: '1 Day' },
];

// Polling interval in milliseconds (60 seconds)
const POLL_INTERVAL = 60000;

export default function Dashboard() {
  const { user, isLoading: authLoading } = useAuth();
  const [selectedInstrument, setSelectedInstrument] = useState<string>('EUR_USD');
  const [selectedTimeframe, setSelectedTimeframe] = useState<Timeframe>('M1');

  const {
    candles,
    latestPrice,
    isLoading: chartLoading,
    error: chartError,
    refetch,
  } = useMarketData({
    exchange: 'oanda',
    instrument: selectedInstrument,
    timeframe: selectedTimeframe,
    limit: 100,
    pollInterval: POLL_INTERVAL,
  });

  const handleRefresh = async () => {
    await refetch();
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="border-b border-slate-200 pb-5 dark:border-slate-800">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          Dashboard
        </h1>
        <p className="mt-2 text-slate-600 dark:text-slate-400">
          Monitor market data and system status
        </p>
      </div>

      {/* Top Row: User Info, System Status, Current Price */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <UserInfoCard user={user} isLoading={authLoading} />
        <SystemStatusCard />

        {/* Current Price Card */}
        <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
          <div className="flex items-start justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-green-100 dark:bg-green-900/30 rounded-lg">
                <Activity className="h-6 w-6 text-green-600 dark:text-green-400" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                  Current Price
                </h3>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {selectedInstrument}
                </p>
              </div>
            </div>
          </div>

          {chartLoading ? (
            <div className="flex items-center justify-center py-4">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600 dark:border-blue-400"></div>
            </div>
          ) : latestPrice !== null ? (
            <div>
              <div className="text-3xl font-bold text-slate-900 dark:text-slate-100 mb-2">
                {latestPrice.toFixed(5)}
              </div>
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400">
                  <div className="h-1.5 w-1.5 rounded-full bg-blue-600" />
                  Auto-refresh (60s)
                </span>
              </div>
            </div>
          ) : (
            <div className="text-sm text-slate-500 dark:text-slate-400">
              No price data available
            </div>
          )}
        </div>
      </div>

      {/* Controls Row: Instrument Selector, Timeframe Selector, Refresh Button */}
      <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-start">
        {/* Instrument Selector */}
        <div className="md:col-span-5">
          <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
            Instrument
          </label>
          <InstrumentSelector
            value={selectedInstrument}
            onChange={setSelectedInstrument}
            disabled={chartLoading}
          />
        </div>

        {/* Timeframe Selector */}
        <div className="md:col-span-4">
          <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
            Timeframe
          </label>
          <div className="relative">
            <select
              value={selectedTimeframe}
              onChange={(e) => setSelectedTimeframe(e.target.value as Timeframe)}
              disabled={chartLoading}
              className={`
                w-full bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700
                rounded-lg px-4 py-2.5 text-sm text-slate-900 dark:text-slate-100
                appearance-none cursor-pointer
                transition-colors hover:bg-slate-50 dark:hover:bg-slate-700
                ${chartLoading ? 'opacity-50 cursor-not-allowed' : ''}
              `}
            >
              {TIMEFRAMES.map((tf) => (
                <option key={tf.value} value={tf.value}>
                  {tf.label}
                </option>
              ))}
            </select>
            <Clock className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400 dark:text-slate-500 pointer-events-none" />
          </div>
        </div>

        {/* Refresh Button */}
        <div className="md:col-span-3">
          <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
            Actions
          </label>
          <button
            onClick={handleRefresh}
            disabled={chartLoading}
            className={`
              w-full bg-blue-600 hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600
              text-white rounded-lg px-4 py-2.5 text-sm font-medium
              flex items-center justify-center gap-2
              transition-colors
              ${chartLoading ? 'opacity-50 cursor-not-allowed' : ''}
            `}
          >
            <RefreshCw className={`h-4 w-4 ${chartLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        </div>
      </div>

      {/* Chart Row */}
      <div>
        <CandlestickChart
          candles={candles}
          instrument={selectedInstrument}
          timeframe={selectedTimeframe}
          isLoading={chartLoading}
          error={chartError}
        />
      </div>

      {/* Info Section */}
      <div className="bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <h3 className="text-lg font-medium text-slate-900 dark:text-slate-100 mb-4">
          About Market Data
        </h3>
        <div className="text-sm text-slate-600 dark:text-slate-400">
          <p>
            Market data is fetched from the backend API and automatically refreshes every 60 seconds.
            Click the Refresh button to manually update the data.
          </p>
          <p className="mt-2">
            Historical candle data is ingested from OANDA by a background worker and stored in the database
            for fast retrieval.
          </p>
        </div>
      </div>
    </div>
  );
}
