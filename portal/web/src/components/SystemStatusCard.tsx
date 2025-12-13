import React, { useEffect, useState } from 'react';
import { Activity, Database, AlertCircle } from 'lucide-react';
import { marketDataService } from '../services/marketData';

interface MarketDataStatus {
  exchange: string;
  status: string;
  instruments_count: number;
  last_update?: string;
}

export default function SystemStatusCard() {
  const [status, setStatus] = useState<MarketDataStatus[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    loadStatus();
    // Refresh status every 30 seconds
    const interval = setInterval(loadStatus, 30000);
    return () => clearInterval(interval);
  }, []);

  const loadStatus = async () => {
    try {
      setError(null);
      const data = await marketDataService.getMarketDataStatus(true);
      setStatus(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load status');
    } finally {
      setIsLoading(false);
    }
  };

  const totalInstruments = status.reduce((sum, s) => sum + (s.instruments_count || 0), 0);

  if (isLoading) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <div className="animate-pulse">
          <div className="h-4 bg-slate-200 dark:bg-slate-700 rounded w-3/4 mb-4"></div>
          <div className="h-3 bg-slate-200 dark:bg-slate-700 rounded w-1/2 mb-2"></div>
          <div className="h-3 bg-slate-200 dark:bg-slate-700 rounded w-2/3"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-purple-100 dark:bg-purple-900/30 rounded-lg">
            <Database className="h-6 w-6 text-purple-600 dark:text-purple-400" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
              System Status
            </h3>
            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium mt-1 bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400">
              <Activity className="h-3 w-3" />
              REST API
            </span>
          </div>
        </div>
      </div>

      {error ? (
        <div className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400">
          <AlertCircle className="h-4 w-4" />
          <span>{error}</span>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-600 dark:text-slate-400">Exchanges</span>
            <span className="font-semibold text-slate-900 dark:text-slate-100">
              {status.length}
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-600 dark:text-slate-400">Instruments</span>
            <span className="font-semibold text-slate-900 dark:text-slate-100">
              {totalInstruments}
            </span>
          </div>
          {status.length > 0 && (
            <div className="pt-2 border-t border-slate-200 dark:border-slate-700">
              {status.map((s, idx) => (
                <div key={idx} className="flex items-center justify-between text-xs py-1">
                  <span className="text-slate-500 dark:text-slate-400 capitalize">
                    {s.exchange}
                  </span>
                  <span className={`px-2 py-0.5 rounded-full font-medium ${
                    s.status === 'active' || s.status === 'connected'
                      ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400'
                  }`}>
                    {s.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
