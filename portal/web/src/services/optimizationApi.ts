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

    // For real API, we need to fetch multiple endpoints
    const [trial, job, signals, trades] = await Promise.all([
      this.getTrial(jobId, trialId),
      this.getJob(jobId),
      this.getTrialSignals(jobId, trialId),
      this.getTrialTrades(jobId, trialId),
    ]);

    if (!trial || !job) {
      return null;
    }

    // Note: Candles come from market-data API, indicators are calculated client-side
    return {
      trial,
      job,
      candles: [], // Would fetch from /api/v1/market-data/candles
      signals,
      indicators: [], // Calculated from candles + strategy params
      trades,
      extendedMetrics: {
        ...trial.metrics,
        totalPips: 0,
        avgPipsPerTrade: 0,
        totalSpreadCostPips: 0,
        totalSwapCostPips: 0,
        totalCostPips: 0,
        netPips: 0,
        avgNetPipsPerTrade: 0,
        grossPnlDollars: 0,
        totalSpreadCostDollars: 0,
        totalSwapCostDollars: 0,
        netPnlDollars: 0,
      },
    };
  }

  /**
   * Get trade signals for a trial
   */
  async getTrialSignals(jobId: string, trialId: string): Promise<any[]> {
    if (USE_MOCK_DATA) {
      return [];
    }

    const response = await fetch(
      `${API_BASE_URL}/optimization-jobs/${jobId}/trials/${trialId}/signals`,
      { headers: this.getAuthHeaders() }
    );
    const data = await this.handleResponse<{ items: any[] }>(response);
    return data.items;
  }

  /**
   * Get trades for a trial
   */
  async getTrialTrades(jobId: string, trialId: string): Promise<any[]> {
    if (USE_MOCK_DATA) {
      return [];
    }

    const response = await fetch(
      `${API_BASE_URL}/optimization-jobs/${jobId}/trials/${trialId}/trades`,
      { headers: this.getAuthHeaders() }
    );
    const data = await this.handleResponse<{ items: any[] }>(response);
    return data.items;
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
}

export const optimizationApi = new OptimizationApiService();
