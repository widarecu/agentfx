import { API_BASE_URL } from './config';

export interface Instrument {
  symbol: string;
  name: string;
  type: string;
  pip_location: number;
  display_precision: number;
  trade_units_precision: number;
  minimum_trade_size: number;
  maximum_trade_size: number;
}

export interface Candle {
  exchange: string;
  instrument: string;
  timeframe: string;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  complete?: boolean;
  spread?: number;
}

export interface CandleListResponse {
  items: Candle[];
  total: number;
  from_time: string | null;
  to_time: string | null;
  count: number;
}

export interface MarketDataError {
  message: string;
  status?: number;
  code?: string;
}

export type Timeframe = string;

export interface TimeframeInfo {
  code: string;
  display_name: string;
}

// Simple in-memory cache for market data
class MarketDataCache {
  private cache = new Map<string, { data: any; timestamp: number; ttl: number }>();
  
  set(key: string, data: any, ttlMs: number = 30000): void {
    this.cache.set(key, {
      data,
      timestamp: Date.now(),
      ttl: ttlMs,
    });
  }
  
  get(key: string): any | null {
    const item = this.cache.get(key);
    if (!item) return null;
    
    if (Date.now() - item.timestamp > item.ttl) {
      this.cache.delete(key);
      return null;
    }
    
    return item.data;
  }
  
  clear(): void {
    this.cache.clear();
  }
  
  // Cache size management
  size(): number {
    return this.cache.size;
  }
  
  cleanup(): void {
    const now = Date.now();
    for (const [key, item] of this.cache.entries()) {
      if (now - item.timestamp > item.ttl) {
        this.cache.delete(key);
      }
    }
  }
}

class MarketDataService {
  private cache = new MarketDataCache();
  private readonly CACHE_TTL = {
    INSTRUMENTS: 5 * 60 * 1000, // 5 minutes
    TIMEFRAMES: 5 * 60 * 1000, // 5 minutes
    CANDLES: 30 * 1000, // 30 seconds
    STATUS: 60 * 1000, // 1 minute
  };

  private getAuthHeaders(): HeadersInit {
    const token = localStorage.getItem('access_token');
    return {
      'Content-Type': 'application/json',
      ...(token && { Authorization: `Bearer ${token}` }),
    };
  }

  private async handleResponse<T>(response: Response, errorMessage: string): Promise<T> {
    if (!response.ok) {
      let errorData: MarketDataError = { message: errorMessage };
      
      try {
        const errorJson = await response.json();
        errorData = {
          ...errorData,
          message: errorJson.detail || errorMessage,
          status: response.status,
          code: errorJson.code,
        };
      } catch {
        errorData.message = `${errorMessage} (${response.status})`;
      }
      
      throw new Error(errorData.message);
    }

    try {
      return await response.json();
    } catch (e) {
      throw new Error(`Invalid response format: ${errorMessage}`);
    }
  }

  private getCacheKey(endpoint: string, params: Record<string, any> = {}): string {
    const paramStr = new URLSearchParams(params).toString();
    return `${endpoint}${paramStr ? '?' + paramStr : ''}`;
  }

  async getInstruments(forceRefresh: boolean = false): Promise<Instrument[]> {
    const cacheKey = this.getCacheKey('instruments');
    
    if (!forceRefresh) {
      const cached = this.cache.get(cacheKey);
      if (cached) return cached;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/market-data/instruments`, {
        headers: this.getAuthHeaders(),
      });

      const data = await this.handleResponse<Instrument[]>(response, 'Failed to fetch instruments');
      this.cache.set(cacheKey, data, this.CACHE_TTL.INSTRUMENTS);
      return data;
    } catch (error) {
      // Return cached data if available on error
      const cached = this.cache.get(cacheKey);
      if (cached) {
        console.warn('Using cached instruments due to error:', error);
        return cached;
      }
      throw error;
    }
  }

  async getTimeframes(instrument?: string, forceRefresh: boolean = false): Promise<TimeframeInfo[]> {
    const params: Record<string, string> = { exchange: 'oanda' };
    if (instrument) params.instrument = instrument;

    const cacheKey = this.getCacheKey('timeframes', params);

    if (!forceRefresh) {
      const cached = this.cache.get(cacheKey);
      if (cached) return cached;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/market-data/timeframes?${new URLSearchParams(params)}`, {
        headers: this.getAuthHeaders(),
      });

      const data = await this.handleResponse<TimeframeInfo[]>(response, 'Failed to fetch timeframes');
      this.cache.set(cacheKey, data, this.CACHE_TTL.TIMEFRAMES);
      return data;
    } catch (error) {
      const cached = this.cache.get(cacheKey);
      if (cached) {
        console.warn('Using cached timeframes due to error:', error);
        return cached;
      }
      throw error;
    }
  }

  async getCandles(
    exchange: string,
    instrument: string,
    timeframe: Timeframe,
    options?: {
      from_time?: string;
      to_time?: string;
      limit?: number;
      offset?: number;
      forceRefresh?: boolean;
    }
  ): Promise<CandleListResponse> {
    const params = {
      exchange,
      instrument,
      timeframe,
      limit: (options?.limit || 100).toString(),
      ...(options?.offset !== undefined && { offset: options.offset.toString() }),
      ...(options?.from_time && { from_time: options.from_time }),
      ...(options?.to_time && { to_time: options.to_time }),
    };

    const cacheKey = this.getCacheKey('candles', params);
    
    if (!options?.forceRefresh) {
      const cached = this.cache.get(cacheKey);
      if (cached) return cached;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/market-data/candles?${new URLSearchParams(params)}`, {
        headers: this.getAuthHeaders(),
      });

      const data = await this.handleResponse<CandleListResponse>(response, 'Failed to fetch candles');
      this.cache.set(cacheKey, data, this.CACHE_TTL.CANDLES);
      return data;
    } catch (error) {
      // Don't return cached candles on error - data should be fresh
      throw error;
    }
  }

  async getLatestCandle(
    exchange: string,
    instrument: string,
    timeframe: Timeframe
  ): Promise<Candle | null> {
    try {
      const params = { exchange, instrument, timeframe };
      const response = await fetch(`${API_BASE_URL}/market-data/candles/latest?${new URLSearchParams(params)}`, {
        headers: this.getAuthHeaders(),
      });

      return await this.handleResponse<Candle>(response, 'Failed to fetch latest candle');
    } catch (error) {
      // Return null for latest candle errors (common when no data exists)
      console.warn('Failed to fetch latest candle:', error);
      return null;
    }
  }

  async getMarketDataStatus(forceRefresh: boolean = false): Promise<any[]> {
    const cacheKey = this.getCacheKey('status');
    
    if (!forceRefresh) {
      const cached = this.cache.get(cacheKey);
      if (cached) return cached;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/market-data/status`, {
        headers: this.getAuthHeaders(),
      });

      const data = await this.handleResponse<any[]>(response, 'Failed to fetch market data status');
      this.cache.set(cacheKey, data, this.CACHE_TTL.STATUS);
      return data;
    } catch (error) {
      // Return cached status if available on error
      const cached = this.cache.get(cacheKey);
      if (cached) {
        console.warn('Using cached status due to error:', error);
        return cached;
      }
      throw error;
    }
  }

  // Utility methods
  clearCache(): void {
    this.cache.clear();
  }

  getCacheInfo(): { size: number; keys: string[] } {
    return {
      size: this.cache.size(),
      keys: Array.from((this.cache as any).cache.keys()),
    };
  }

  // Validate candle data before sending to API
  validateCandleData(candle: Partial<Candle>): string[] {
    const errors: string[] = [];
    
    if (!candle.timestamp) errors.push('Timestamp is required');
    if (!candle.open || candle.open <= 0) errors.push('Open price must be positive');
    if (!candle.high || candle.high <= 0) errors.push('High price must be positive');
    if (!candle.low || candle.low <= 0) errors.push('Low price must be positive');
    if (!candle.close || candle.close <= 0) errors.push('Close price must be positive');
    
    if (candle.high && candle.low && candle.high < candle.low) {
      errors.push('High price cannot be lower than low price');
    }
    
    if (candle.open && candle.high && candle.high < candle.open) {
      errors.push('High price cannot be lower than open price');
    }
    
    if (candle.close && candle.high && candle.high < candle.close) {
      errors.push('High price cannot be lower than close price');
    }
    
    if (candle.open && candle.low && candle.low > candle.open) {
      errors.push('Low price cannot be higher than open price');
    }
    
    if (candle.close && candle.low && candle.low > candle.close) {
      errors.push('Low price cannot be higher than close price');
    }
    
    if (candle.volume && candle.volume < 0) {
      errors.push('Volume cannot be negative');
    }
    
    return errors;
  }
}

export const marketDataService = new MarketDataService();
