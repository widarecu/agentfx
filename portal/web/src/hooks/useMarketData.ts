import { useState, useEffect, useCallback } from 'react';
import { marketDataService, Candle, Timeframe } from '../services/marketData';

interface UseMarketDataOptions {
  exchange: string;
  instrument: string;
  timeframe: Timeframe;
  limit?: number;
  pollInterval?: number; // Polling interval in milliseconds
}

interface UseMarketDataReturn {
  candles: Candle[];
  latestPrice: number | null;
  isLoading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

/**
 * Hook for fetching market data via REST API with optional polling.
 * This is a REST-only implementation - no WebSocket support.
 */
export function useMarketData({
  exchange,
  instrument,
  timeframe,
  limit = 100,
  pollInterval = 60000, // Default to 60 seconds
}: UseMarketDataOptions): UseMarketDataReturn {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [latestPrice, setLatestPrice] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const response = await marketDataService.getCandles(
        exchange,
        instrument,
        timeframe,
        { limit, forceRefresh: true }
      );

      setCandles(response.items);

      // Set latest price from most recent candle
      if (response.items.length > 0) {
        const latestCandle = response.items[response.items.length - 1];
        setLatestPrice(latestCandle.close);
      }

      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch market data';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [exchange, instrument, timeframe, limit]);

  // Initial fetch
  useEffect(() => {
    setIsLoading(true);
    fetchData();
  }, [fetchData]);

  // Set up polling
  useEffect(() => {
    if (pollInterval <= 0) return;

    const intervalId = setInterval(() => {
      fetchData();
    }, pollInterval);

    return () => clearInterval(intervalId);
  }, [fetchData, pollInterval]);

  const refetch = useCallback(async () => {
    setIsLoading(true);
    await fetchData();
  }, [fetchData]);

  return {
    candles,
    latestPrice,
    isLoading,
    error,
    refetch,
  };
}
