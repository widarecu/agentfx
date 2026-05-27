/**
 * Optimization API Service (v0.2.0)
 *
 * This service provides API access to optimization jobs and trials.
 * Supports both mock mode (for development) and real API mode.
 *
 * Set USE_MOCK_DATA = false to use real backend API.
 */

import { API_BASE_URL } from './config';
import {
  OptimizationJob,
  OptimizationTrial,
  OptimizationJobListResponse,
  OptimizationTrialListResponse,
  TrialDetailData,
  TrialMetricsExtended,
  Trade,
  TradeSignal,
  CandleData,
  IndicatorLine,
  StrategyType,
} from '../types/optimization';
import {
  mockOptimizationJobs,
  getJobById,
  getTrialsByJobId,
  getTrialById,
  getTop3Trials,
  getTrialDetailData,
} from '../mocks/optimizationMocks';

// Toggle between mock and real API
// Backend is ready - use real API
const USE_MOCK_DATA = false;

// Simulated API delay for realistic UX
const MOCK_DELAY_MS = 300;

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// API request configuration for new job creation
interface CreateJobRequest {
  strategy: StrategyType;
  pair: string;
  param_ranges: Record<string, { min: number; max: number; step?: number }>;
  account_balance: number;
  position_size: number;
  data_years: number;
  timeframe: string;
}

class OptimizationApiService {
  private getAuthHeaders(): HeadersInit {
    const token = localStorage.getItem('access_token');
    return {
      'Content-Type': 'application/json',
      ...(token && { Authorization: `Bearer ${token}` }),
    };
  }

  /**
   * Handle API response errors
   */
  private async handleResponse<T>(response: Response): Promise<T> {
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `API error: ${response.status}`);
    }
    return response.json();
  }

  /**
   * Get paginated list of optimization jobs
   */
  async getJobs(
    page: number = 1,
    pageSize: number = 10
  ): Promise<OptimizationJobListResponse> {
    if (USE_MOCK_DATA) {
      await delay(MOCK_DELAY_MS);
      const start = (page - 1) * pageSize;
      const end = start + pageSize;
      const items = mockOptimizationJobs.slice(start, end);
      return {
        items,
        total: mockOptimizationJobs.length,
        page,
        pageSize,
      };
    }

    const response = await fetch(
      `${API_BASE_URL}/optimization-jobs?page=${page}&page_size=${pageSize}`,
      { headers: this.getAuthHeaders() }
    );
    const data = await this.handleResponse<{
      items: OptimizationJob[];
      total: number;
      page: number;
      page_size: number;
    }>(response);

    // Map snake_case to camelCase
    return {
      items: data.items.map(this.mapJobResponse),
      total: data.total,
      page: data.page,
      pageSize: data.page_size,
    };
  }

  /**
   * Get a single optimization job by ID
   */
  async getJob(jobId: string): Promise<OptimizationJob | null> {
    if (USE_MOCK_DATA) {
      await delay(MOCK_DELAY_MS);
      return getJobById(jobId) || null;
    }

    const response = await fetch(
      `${API_BASE_URL}/optimization-jobs/${jobId}`,
      { headers: this.getAuthHeaders() }
    );

    if (response.status === 404) {
      return null;
    }

    const data = await this.handleResponse<OptimizationJob>(response);
    return this.mapJobResponse(data);
  }

  /**
   * Get all trials for an optimization job
   */
  async getTrials(jobId: string): Promise<OptimizationTrialListResponse> {
    if (USE_MOCK_DATA) {
      await delay(MOCK_DELAY_MS);
      const trials = getTrialsByJobId(jobId);
      return {
        items: trials,
        total: trials.length,
        jobId,
      };
    }

    const response = await fetch(
      `${API_BASE_URL}/optimization-jobs/${jobId}/trials`,
      { headers: this.getAuthHeaders() }
    );
    const data = await this.handleResponse<{
      items: OptimizationTrial[];
      total: number;
      job_id: string;
    }>(response);

    return {
      items: data.items.map(this.mapTrialResponse),
      total: data.total,
      jobId: data.job_id,
    };
  }

  /**
   * Get top N trials for an optimization job (sorted by Sharpe ratio)
   */
  async getTopTrials(
    jobId: string,
    limit: number = 3
  ): Promise<OptimizationTrial[]> {
    if (USE_MOCK_DATA) {
      await delay(MOCK_DELAY_MS);
      return getTop3Trials(jobId).slice(0, limit);
    }

    const response = await fetch(
      `${API_BASE_URL}/optimization-jobs/${jobId}/trials/top?limit=${limit}`,
      { headers: this.getAuthHeaders() }
    );
    const data = await this.handleResponse<OptimizationTrial[]>(response);
    return data.map(this.mapTrialResponse);
  }

  /**
   * Get a single trial by ID
   */
  async getTrial(
    jobId: string,
    trialId: string
  ): Promise<OptimizationTrial | null> {
    if (USE_MOCK_DATA) {
      await delay(MOCK_DELAY_MS);
      const trial = getTrialById(trialId);
      if (!trial || trial.jobId !== jobId) {
        return null;
      }
      return trial;
    }

    const response = await fetch(
      `${API_BASE_URL}/optimization-jobs/${jobId}/trials/${trialId}`,
      { headers: this.getAuthHeaders() }
    );

    if (response.status === 404) {
      return null;
    }

    const data = await this.handleResponse<OptimizationTrial>(response);
    return this.mapTrialResponse(data);
  }

  /**
   * Get full trial detail including chart data
   */
  async getTrialDetail(
    jobId: string,
    trialId: string
  ): Promise<TrialDetailData | null> {
    if (USE_MOCK_DATA) {
      await delay(MOCK_DELAY_MS * 2); // Longer delay for chart data
      return getTrialDetailData(jobId, trialId);
    }

    // Fetch trial, job, signals, and trades in parallel
    const [trial, job, signals, trades] = await Promise.all([
      this.getTrial(jobId, trialId),
      this.getJob(jobId),
      this.getTrialSignals(jobId, trialId),
      this.getTrialTrades(jobId, trialId),
    ]);

    if (!trial || !job) {
      return null;
    }

    // Fetch candles from market-data API (needs job.pair for instrument)
    let candles: CandleData[] = [];
    try {
      // Date range fallback chain
      let fromTime: string | null = null;
      let toTime: string | null = null;

      if (job.dataRangeStart && job.dataRangeEnd) {
        fromTime = job.dataRangeStart;
        toTime = job.dataRangeEnd;
      } else if (job.dataYears) {
        const end = job.completedAt ? new Date(job.completedAt) : new Date();
        const start = new Date(end.getTime() - job.dataYears * 365 * 24 * 3600 * 1000);
        fromTime = start.toISOString();
        toTime = end.toISOString();
      } else if (trades.length > 0) {
        fromTime = trades[0].entryTimestamp;
        toTime = trades[trades.length - 1].exitTimestamp;
      }

      if (fromTime && toTime) {
        const pageSize = 10000;
        let offset = 0;
        let total = Infinity;

        while (offset < total) {
          const params = new URLSearchParams({
            exchange: 'oanda',
            instrument: job.pair,
            timeframe: job.timeframe || 'H1',
            from_time: typeof fromTime === 'string' ? fromTime : new Date(fromTime).toISOString(),
            to_time: typeof toTime === 'string' ? toTime : new Date(toTime).toISOString(),
            limit: String(pageSize),
            offset: String(offset),
          });
          const candleResponse = await fetch(
            `${API_BASE_URL}/market-data/candles?${params}`,
            { headers: this.getAuthHeaders() }
          );
          if (!candleResponse.ok) break;
          const candleData = await candleResponse.json();
          total = candleData.total ?? 0;
          const page = (candleData.items || []).map(this.mapCandleResponse);
          candles.push(...page);
          offset += pageSize;
          if (page.length < pageSize) break;
        }
      }
    } catch {
      // Candle fetch is best-effort; chart will show empty state
    }

    // Compute extended metrics from trades
    const extendedMetrics = this.computeExtendedMetrics(trial, trades);

    // Build continuous indicator lines from candle data
    const indicators = this.buildIndicatorsFromCandles(candles, job.strategy, trial.params);

    return {
      trial,
      job,
      candles,
      signals,
      indicators,
      trades,
      extendedMetrics,
    };
  }

  /**
   * Get trade signals for a trial
   */
  async getTrialSignals(jobId: string, trialId: string): Promise<TradeSignal[]> {
    if (USE_MOCK_DATA) {
      return [];
    }

    const allSignals: TradeSignal[] = [];
    let skip = 0;
    const pageSize = 1000;
    let total = Infinity;

    while (skip < total) {
      const response = await fetch(
        `${API_BASE_URL}/optimization-jobs/${jobId}/trials/${trialId}/signals?skip=${skip}&limit=${pageSize}`,
        { headers: this.getAuthHeaders() }
      );
      const data = await this.handleResponse<{ items: any[]; total: number }>(response);
      total = data.total;
      allSignals.push(...data.items.map(this.mapSignalResponse));
      skip += pageSize;
      if (data.items.length < pageSize) break;
    }

    return allSignals;
  }

  /**
   * Get trades for a trial
   */
  async getTrialTrades(jobId: string, trialId: string): Promise<Trade[]> {
    if (USE_MOCK_DATA) {
      return [];
    }

    // Fetch all trades with pagination (API defaults to 1000 per page)
    const allTrades: Trade[] = [];
    let skip = 0;
    const pageSize = 1000;
    let total = Infinity;

    while (skip < total) {
      const response = await fetch(
        `${API_BASE_URL}/optimization-jobs/${jobId}/trials/${trialId}/trades?skip=${skip}&limit=${pageSize}`,
        { headers: this.getAuthHeaders() }
      );
      const data = await this.handleResponse<{ items: any[]; total: number }>(response);
      total = data.total;
      allTrades.push(...data.items.map(this.mapTradeResponse));
      skip += pageSize;
    }

    return allTrades;
  }

  /**
   * Create a new optimization job
   */
  async createJob(config: {
    strategy: StrategyType;
    pair: string;
    accountBalance: number;
    positionSize: number;
    dataYears: number;
    timeframe: string;
    paramRanges: Record<string, { min: number; max: number }>;
  }): Promise<OptimizationJob> {
    if (USE_MOCK_DATA) {
      await delay(MOCK_DELAY_MS);
      const paramCount = Object.keys(config.paramRanges).length;
      const newJob: OptimizationJob = {
        id: `optjob_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`,
        strategy: config.strategy,
        pair: config.pair,
        status: 'queued',
        progress: 0,
        estimatedIterations: 10 + paramCount * 15,
        completedIterations: 0,
        bestSharpe: null,
        createdAt: new Date().toISOString(),
        completedAt: null,
        paramRanges: config.paramRanges,
        accountBalance: config.accountBalance,
        positionSize: config.positionSize,
        dataYears: config.dataYears,
        timeframe: config.timeframe,
      };
      return newJob;
    }

    // Map camelCase to snake_case for API
    const requestBody: CreateJobRequest = {
      strategy: config.strategy,
      pair: config.pair,
      param_ranges: config.paramRanges,
      account_balance: config.accountBalance,
      position_size: config.positionSize,
      data_years: config.dataYears,
      timeframe: config.timeframe,
    };

    const response = await fetch(
      `${API_BASE_URL}/optimization-jobs`,
      {
        method: 'POST',
        headers: this.getAuthHeaders(),
        body: JSON.stringify(requestBody),
      }
    );

    const data = await this.handleResponse<{
      job_id: string;
      status: string;
      status_url: string;
      estimated_iterations: number;
      created_at: string;
    }>(response);

    // Return minimal job data from creation response
    return {
      id: data.job_id,
      strategy: config.strategy,
      pair: config.pair,
      status: data.status as 'queued',
      progress: 0,
      estimatedIterations: data.estimated_iterations,
      completedIterations: 0,
      bestSharpe: null,
      createdAt: data.created_at,
      completedAt: null,
      paramRanges: config.paramRanges,
      accountBalance: config.accountBalance,
      positionSize: config.positionSize,
      dataYears: config.dataYears,
      timeframe: config.timeframe,
    };
  }

  /**
   * Delete an optimization job
   */
  async deleteJob(jobId: string): Promise<{ success: boolean }> {
    if (USE_MOCK_DATA) {
      await delay(MOCK_DELAY_MS);
      return { success: true };
    }

    const response = await fetch(
      `${API_BASE_URL}/optimization-jobs/${jobId}`,
      {
        method: 'DELETE',
        headers: this.getAuthHeaders(),
      }
    );

    return this.handleResponse<{ success: boolean }>(response);
  }

  /**
   * Map API response (snake_case) to frontend type (camelCase)
   */
  private mapJobResponse = (job: any): OptimizationJob => ({
    id: job.id,
    strategy: job.strategy,
    pair: job.pair,
    status: job.status,
    progress: job.progress,
    estimatedIterations: job.estimated_iterations ?? job.estimatedIterations,
    completedIterations: job.completed_iterations ?? job.completedIterations,
    bestSharpe: job.best_sharpe ?? job.bestSharpe,
    createdAt: job.created_at ?? job.createdAt,
    completedAt: job.completed_at ?? job.completedAt,
    paramRanges: job.param_ranges ?? job.paramRanges,
    dataRangeStart: job.data_range_start ?? job.dataRangeStart,
    dataRangeEnd: job.data_range_end ?? job.dataRangeEnd,
    accountBalance: job.account_balance ?? job.accountBalance ?? 10000,
    positionSize: job.position_size ?? job.positionSize ?? 10000,
    dataYears: job.data_years ?? job.dataYears ?? 5,
    timeframe: job.timeframe ?? 'H1',
  });

  private mapTrialResponse = (trial: any): OptimizationTrial => ({
    id: trial.id,
    jobId: trial.job_id ?? trial.jobId,
    params: trial.params,
    metrics: {
      sharpe: trial.sharpe ?? trial.metrics?.sharpe,
      maxDrawdown: trial.max_drawdown ?? trial.metrics?.maxDrawdown,
      hitRate: trial.hit_rate ?? trial.metrics?.hitRate,
      tradeCount: trial.trade_count ?? trial.metrics?.tradeCount,
      totalReturn: trial.total_return ?? trial.metrics?.totalReturn,
      profitFactor: trial.profit_factor ?? trial.metrics?.profitFactor,
      avgWin: trial.avg_win ?? trial.metrics?.avgWin,
      avgLoss: trial.avg_loss ?? trial.metrics?.avgLoss,
      maxConsecutiveLosses: trial.max_consecutive_losses ?? trial.metrics?.maxConsecutiveLosses,
      avgTradeDuration: trial.avg_trade_duration ?? trial.metrics?.avgTradeDuration,
    },
    passesGateB: trial.passes_gate_b ?? trial.passesGateB,
    gateBFailures: trial.gate_b_failures ?? trial.gateBFailures,
    createdAt: trial.created_at ?? trial.createdAt,
  });

  private mapTradeResponse = (trade: any): Trade => ({
    id: trade.id,
    trialId: trade.trial_id ?? trade.trialId,
    direction: trade.direction,
    entryTimestamp: trade.entry_timestamp ?? trade.entryTimestamp,
    exitTimestamp: trade.exit_timestamp ?? trade.exitTimestamp,
    entryPrice: trade.entry_price ?? trade.entryPrice,
    exitPrice: trade.exit_price ?? trade.exitPrice,
    pips: trade.pips,
    spreadCostPips: trade.spread_cost_pips ?? trade.spreadCostPips,
    slippageCostPips:
      trade.slippage_cost_pips ?? trade.slippageCostPips ?? 0,
    swapCostPips: trade.swap_cost_pips ?? trade.swapCostPips,
    netPips: trade.net_pips ?? trade.netPips,
    grossPnlDollars: trade.gross_pnl_dollars ?? trade.grossPnlDollars,
    spreadCostDollars: trade.spread_cost_dollars ?? trade.spreadCostDollars,
    slippageCostDollars:
      trade.slippage_cost_dollars ?? trade.slippageCostDollars ?? 0,
    swapCostDollars: trade.swap_cost_dollars ?? trade.swapCostDollars,
    netPnlDollars: trade.net_pnl_dollars ?? trade.netPnlDollars,
    durationMinutes: trade.duration_minutes ?? trade.durationMinutes,
    positionSize: trade.position_size ?? trade.positionSize,
  });

  private mapSignalResponse = (signal: any): TradeSignal => ({
    timestamp: signal.timestamp,
    signal: signal.signal,
    signalType: signal.signal_type ?? signal.signalType,
    price: signal.price,
    pnl: signal.pnl,
    indicatorValues: signal.indicator_values ?? signal.indicatorValues ?? {},
  });

  private mapCandleResponse = (candle: any): CandleData => ({
    timestamp: candle.timestamp,
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
    volume: candle.volume ?? 0,
  });

  private computeExtendedMetrics(trial: OptimizationTrial, trades: Trade[]): TrialMetricsExtended {
    const count = trades.length;
    const totalPips = trades.reduce((sum, t) => sum + (t.pips ?? 0), 0);
    const totalSpreadCostPips = trades.reduce((sum, t) => sum + (t.spreadCostPips ?? 0), 0);
    const totalSwapCostPips = trades.reduce((sum, t) => sum + (t.swapCostPips ?? 0), 0);
    const totalCostPips = totalSpreadCostPips + totalSwapCostPips;
    const netPips = totalPips - totalCostPips;
    const grossPnlDollars = trades.reduce((sum, t) => sum + (t.grossPnlDollars ?? 0), 0);
    const totalSpreadCostDollars = trades.reduce((sum, t) => sum + (t.spreadCostDollars ?? 0), 0);
    const totalSwapCostDollars = trades.reduce((sum, t) => sum + (t.swapCostDollars ?? 0), 0);
    const netPnlDollars = grossPnlDollars - totalSpreadCostDollars - totalSwapCostDollars;

    return {
      ...trial.metrics,
      totalPips,
      avgPipsPerTrade: count > 0 ? totalPips / count : 0,
      totalSpreadCostPips,
      totalSwapCostPips,
      totalCostPips,
      netPips,
      avgNetPipsPerTrade: count > 0 ? netPips / count : 0,
      grossPnlDollars,
      totalSpreadCostDollars,
      totalSwapCostDollars,
      netPnlDollars,
    };
  }

  private buildIndicatorsFromCandles(
    candles: CandleData[],
    strategy: string,
    params: Record<string, number>,
  ): IndicatorLine[] {
    if (candles.length === 0) return [];

    const closes = candles.map(c => c.close);
    const highs = candles.map(c => c.high);
    const lows = candles.map(c => c.low);
    const timestamps = candles.map(c => c.timestamp);
    const colors = ['#3b82f6', '#f59e0b', '#8b5cf6', '#ec4899'];

    if (strategy === 'ma') {
      const fast = Math.round(params.fast ?? 20);
      const slow = Math.round(params.slow ?? 50);
      return [
        { name: 'fast_ma', color: colors[0], data: sma(closes, fast, timestamps) },
        { name: 'slow_ma', color: colors[1], data: sma(closes, slow, timestamps) },
      ];
    }

    if (strategy === 'donchian') {
      const window = Math.round(params.window ?? params.fast ?? 20);
      return [
        { name: 'upper_band', color: colors[0], data: rollingMax(highs, window, timestamps) },
        { name: 'lower_band', color: colors[1], data: rollingMin(lows, window, timestamps) },
      ];
    }

    if (strategy === 'rsi') {
      const window = Math.round(params.period ?? params.window ?? params.fast ?? 14);
      return [
        { name: 'rsi', color: colors[2], data: rsi(closes, window, timestamps) },
      ];
    }

    return [];
  }
}

// Indicator calculation helpers

function sma(
  values: number[], period: number, timestamps: string[]
): { timestamp: string; value: number }[] {
  const result: { timestamp: string; value: number }[] = [];
  for (let i = period - 1; i < values.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += values[j];
    result.push({ timestamp: timestamps[i], value: sum / period });
  }
  return result;
}

function rollingMax(
  values: number[], period: number, timestamps: string[]
): { timestamp: string; value: number }[] {
  const result: { timestamp: string; value: number }[] = [];
  for (let i = period - 1; i < values.length; i++) {
    let max = -Infinity;
    for (let j = i - period + 1; j <= i; j++) {
      if (values[j] > max) max = values[j];
    }
    result.push({ timestamp: timestamps[i], value: max });
  }
  return result;
}

function rollingMin(
  values: number[], period: number, timestamps: string[]
): { timestamp: string; value: number }[] {
  const result: { timestamp: string; value: number }[] = [];
  for (let i = period - 1; i < values.length; i++) {
    let min = Infinity;
    for (let j = i - period + 1; j <= i; j++) {
      if (values[j] < min) min = values[j];
    }
    result.push({ timestamp: timestamps[i], value: min });
  }
  return result;
}

function rsi(
  closes: number[], period: number, timestamps: string[]
): { timestamp: string; value: number }[] {
  // SMA-based RSI matching the backend (strategy.py RSIMeanReversion).
  // Uses simple rolling mean of gains/losses, NOT Wilder's exponential smoothing.
  if (closes.length < period + 1) return [];
  const result: { timestamp: string; value: number }[] = [];

  // Compute per-bar changes
  const changes: number[] = [0]; // first bar has no change
  for (let i = 1; i < closes.length; i++) {
    changes.push(closes[i] - closes[i - 1]);
  }

  // Rolling SMA of gains and losses
  for (let i = period; i < closes.length; i++) {
    let sumGain = 0;
    let sumLoss = 0;
    for (let j = i - period + 1; j <= i; j++) {
      if (changes[j] > 0) sumGain += changes[j];
      else sumLoss += Math.abs(changes[j]);
    }
    const avgGain = sumGain / period;
    const avgLoss = sumLoss / period;
    const rs = avgLoss === 0 ? Infinity : avgGain / avgLoss;
    const value = avgLoss === 0 ? 100 : 100 - 100 / (1 + rs);
    result.push({ timestamp: timestamps[i], value });
  }

  return result;
}

export const optimizationApi = new OptimizationApiService();
