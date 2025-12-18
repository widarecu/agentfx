/**
 * Optimization API Service (v0.2.0)
 *
 * This service provides API access to optimization jobs and trials.
 * Currently returns mock data - will be updated to call real backend API.
 */

import { API_BASE_URL } from './config';
import {
  OptimizationJob,
  OptimizationTrial,
  OptimizationJobListResponse,
  OptimizationTrialListResponse,
  TrialDetailData,
} from '../types/optimization';
import {
  mockOptimizationJobs,
  getJobById,
  getTrialsByJobId,
  getTrialById,
  getTop3Trials,
  getTrialDetailData,
} from '../mocks/optimizationMocks';

// Simulated API delay for realistic UX
const MOCK_DELAY_MS = 300;

function delay(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
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
   * Get paginated list of optimization jobs
   */
  async getJobs(
    page: number = 1,
    pageSize: number = 10
  ): Promise<OptimizationJobListResponse> {
    // TODO: Replace with real API call
    // const response = await fetch(
    //   `${API_BASE_URL}/optimization/jobs?page=${page}&page_size=${pageSize}`,
    //   { headers: this.getAuthHeaders() }
    // );
    // return response.json();

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

  /**
   * Get a single optimization job by ID
   */
  async getJob(jobId: string): Promise<OptimizationJob | null> {
    // TODO: Replace with real API call
    // const response = await fetch(
    //   `${API_BASE_URL}/optimization/jobs/${jobId}`,
    //   { headers: this.getAuthHeaders() }
    // );
    // return response.json();

    await delay(MOCK_DELAY_MS);
    return getJobById(jobId) || null;
  }

  /**
   * Get all trials for an optimization job
   */
  async getTrials(jobId: string): Promise<OptimizationTrialListResponse> {
    // TODO: Replace with real API call
    // const response = await fetch(
    //   `${API_BASE_URL}/optimization/jobs/${jobId}/trials`,
    //   { headers: this.getAuthHeaders() }
    // );
    // return response.json();

    await delay(MOCK_DELAY_MS);

    const trials = getTrialsByJobId(jobId);
    return {
      items: trials,
      total: trials.length,
      jobId,
    };
  }

  /**
   * Get top N trials for an optimization job (sorted by Sharpe ratio)
   */
  async getTopTrials(
    jobId: string,
    limit: number = 3
  ): Promise<OptimizationTrial[]> {
    // TODO: Replace with real API call
    // const response = await fetch(
    //   `${API_BASE_URL}/optimization/jobs/${jobId}/trials/top?limit=${limit}`,
    //   { headers: this.getAuthHeaders() }
    // );
    // return response.json();

    await delay(MOCK_DELAY_MS);
    return getTop3Trials(jobId).slice(0, limit);
  }

  /**
   * Get a single trial by ID
   */
  async getTrial(
    jobId: string,
    trialId: string
  ): Promise<OptimizationTrial | null> {
    // TODO: Replace with real API call
    // const response = await fetch(
    //   `${API_BASE_URL}/optimization/jobs/${jobId}/trials/${trialId}`,
    //   { headers: this.getAuthHeaders() }
    // );
    // return response.json();

    await delay(MOCK_DELAY_MS);

    const trial = getTrialById(trialId);
    if (!trial || trial.jobId !== jobId) {
      return null;
    }
    return trial;
  }

  /**
   * Get full trial detail including chart data
   */
  async getTrialDetail(
    jobId: string,
    trialId: string
  ): Promise<TrialDetailData | null> {
    // TODO: Replace with real API call
    // const response = await fetch(
    //   `${API_BASE_URL}/optimization/jobs/${jobId}/trials/${trialId}/detail`,
    //   { headers: this.getAuthHeaders() }
    // );
    // return response.json();

    await delay(MOCK_DELAY_MS * 2); // Longer delay for chart data
    return getTrialDetailData(jobId, trialId);
  }

  /**
   * Create a new optimization job
   */
  async createJob(config: {
    strategy: string;
    pair: string;
    paramRanges: Record<string, { min: number; max: number }>;
    dataRangeStart?: string;
    dataRangeEnd?: string;
  }): Promise<OptimizationJob> {
    // TODO: Replace with real API call
    // const response = await fetch(
    //   `${API_BASE_URL}/optimization/jobs`,
    //   {
    //     method: 'POST',
    //     headers: this.getAuthHeaders(),
    //     body: JSON.stringify(config),
    //   }
    // );
    // return response.json();

    await delay(MOCK_DELAY_MS);

    // Return a mock new job
    const newJob: OptimizationJob = {
      id: `optjob_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`,
      strategy: config.strategy as 'ma' | 'rsi' | 'donchian',
      pair: config.pair,
      status: 'queued',
      progress: 0,
      estimatedIterations: 50,
      completedIterations: 0,
      bestSharpe: null,
      createdAt: new Date().toISOString(),
      completedAt: null,
      paramRanges: config.paramRanges,
      dataRangeStart: config.dataRangeStart,
      dataRangeEnd: config.dataRangeEnd,
    };

    return newJob;
  }

  /**
   * Cancel a running optimization job
   */
  async cancelJob(jobId: string): Promise<{ success: boolean }> {
    // TODO: Replace with real API call
    // const response = await fetch(
    //   `${API_BASE_URL}/optimization/jobs/${jobId}/cancel`,
    //   {
    //     method: 'POST',
    //     headers: this.getAuthHeaders(),
    //   }
    // );
    // return response.json();

    await delay(MOCK_DELAY_MS);
    return { success: true };
  }
}

export const optimizationApi = new OptimizationApiService();
