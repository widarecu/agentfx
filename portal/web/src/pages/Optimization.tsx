/**
 * Optimization List Page
 *
 * Displays a table of all optimization jobs with:
 * - Status badges (queued, running, completed, failed)
 * - Progress bars for running jobs
 * - Click-through to job detail
 * - Pagination controls
 * - New Optimization modal for creating jobs
 */

import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  Plus,
  RefreshCw,
  ExternalLink,
  Clock,
  AlertCircle,
  TrendingUp,
  X,
  DollarSign,
  Settings,
  BarChart3,
  Calendar,
} from 'lucide-react';
import { optimizationApi } from '../services/optimizationApi';
import {
  OptimizationJob,
  StrategyType,
  getStatusColor,
  getStrategyDisplayName,
  truncateJobId,
  formatSharpe,
} from '../types/optimization';

// Available forex pairs
const FOREX_PAIRS = [
  'EUR_USD',
  'GBP_USD',
  'USD_JPY',
  'USD_CHF',
  'AUD_USD',
  'USD_CAD',
  'NZD_USD',
  'EUR_GBP',
  'EUR_JPY',
  'GBP_JPY',
] as const;

// Strategy configurations with default parameter ranges
const STRATEGY_CONFIGS: Record<StrategyType, {
  name: string;
  description: string;
  params: { key: string; label: string; min: number; max: number; defaultMin: number; defaultMax: number; step: number }[];
}> = {
  ma: {
    name: 'Moving Average Crossover',
    description: 'Buy when fast MA crosses above slow MA, sell when it crosses below',
    params: [
      { key: 'fast', label: 'Fast MA Period', min: 2, max: 100, defaultMin: 5, defaultMax: 50, step: 1 },
      { key: 'slow', label: 'Slow MA Period', min: 10, max: 300, defaultMin: 20, defaultMax: 200, step: 1 },
    ],
  },
  rsi: {
    name: 'RSI Mean Reversion',
    description: 'Buy when RSI is oversold, sell when overbought',
    params: [
      { key: 'period', label: 'RSI Period', min: 2, max: 50, defaultMin: 7, defaultMax: 21, step: 1 },
      { key: 'oversold', label: 'Oversold Level', min: 10, max: 40, defaultMin: 20, defaultMax: 35, step: 1 },
      { key: 'overbought', label: 'Overbought Level', min: 60, max: 90, defaultMin: 65, defaultMax: 80, step: 1 },
    ],
  },
  donchian: {
    name: 'Donchian Breakout',
    description: 'Buy on breakout above channel high, sell on breakout below',
    params: [
      { key: 'period', label: 'Channel Period', min: 5, max: 100, defaultMin: 10, defaultMax: 55, step: 1 },
      { key: 'exit_period', label: 'Exit Period', min: 2, max: 50, defaultMin: 5, defaultMax: 20, step: 1 },
    ],
  },
};

// Position size options
const POSITION_SIZES = [
  { value: 1000, label: '1,000 units (0.01 lot - Micro)' },
  { value: 10000, label: '10,000 units (0.1 lot - Mini)' },
  { value: 100000, label: '100,000 units (1.0 lot - Standard)' },
];

interface NewOptimizationFormData {
  strategy: StrategyType;
  pair: string;
  accountBalance: number;
  positionSize: number;
  dataYears: number;
  paramRanges: Record<string, { min: number; max: number }>;
}

// Get default form data for a strategy
function getDefaultFormData(strategy: StrategyType): NewOptimizationFormData {
  const config = STRATEGY_CONFIGS[strategy];
  const paramRanges: Record<string, { min: number; max: number }> = {};
  config.params.forEach(p => {
    paramRanges[p.key] = { min: p.defaultMin, max: p.defaultMax };
  });
  return {
    strategy,
    pair: 'EUR_USD',
    accountBalance: 10000,
    positionSize: 10000,
    dataYears: 5,
    paramRanges,
  };
}

export default function Optimization() {
  const [jobs, setJobs] = useState<OptimizationJob[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const pageSize = 10;

  // New Optimization Modal State
  const [showNewModal, setShowNewModal] = useState(false);
  const [formData, setFormData] = useState<NewOptimizationFormData>(getDefaultFormData('ma'));
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Fetch jobs
  const fetchJobs = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const response = await optimizationApi.getJobs(page, pageSize);
      setJobs(response.items);
      setTotalPages(Math.ceil(response.total / pageSize));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch optimization jobs');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();
  }, [page]);

  // Handle strategy change - reset param ranges to defaults for new strategy
  const handleStrategyChange = (strategy: StrategyType) => {
    const config = STRATEGY_CONFIGS[strategy];
    const paramRanges: Record<string, { min: number; max: number }> = {};
    config.params.forEach(p => {
      paramRanges[p.key] = { min: p.defaultMin, max: p.defaultMax };
    });
    setFormData(prev => ({ ...prev, strategy, paramRanges }));
  };

  // Handle param range change
  const handleParamRangeChange = (key: string, field: 'min' | 'max', value: number) => {
    setFormData(prev => ({
      ...prev,
      paramRanges: {
        ...prev.paramRanges,
        [key]: { ...prev.paramRanges[key], [field]: value },
      },
    }));
  };

  // Handle form submission
  const handleSubmitOptimization = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);

    try {
      // Call the real API to create a new optimization job
      const newJob = await optimizationApi.createJob({
        strategy: formData.strategy,
        pair: formData.pair,
        accountBalance: formData.accountBalance,
        positionSize: formData.positionSize,
        dataYears: formData.dataYears,
        paramRanges: formData.paramRanges,
      });

      console.log('Created optimization job:', newJob);

      // Close modal and refresh list
      setShowNewModal(false);
      setFormData(getDefaultFormData('ma'));
      fetchJobs();

      // Show success message
      alert(`Optimization job ${newJob.id} created successfully!`);
    } catch (err) {
      console.error('Failed to create optimization job:', err);
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      alert(`Failed to create optimization job: ${errorMessage}`);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Open modal
  const openNewModal = () => {
    setFormData(getDefaultFormData('ma'));
    setShowNewModal(true);
  };

  // Format date for display
  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // Calculate duration for completed jobs
  const getDuration = (job: OptimizationJob) => {
    if (!job.completedAt) return null;
    const start = new Date(job.createdAt).getTime();
    const end = new Date(job.completedAt).getTime();
    const durationMs = end - start;
    const minutes = Math.floor(durationMs / 60000);
    const hours = Math.floor(minutes / 60);
    if (hours > 0) {
      return `${hours}h ${minutes % 60}m`;
    }
    return `${minutes}m`;
  };

  // Status badge component
  const StatusBadge = ({ status }: { status: OptimizationJob['status'] }) => {
    const colorClasses = getStatusColor(status);
    const isRunning = status === 'running';

    return (
      <span
        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${colorClasses}`}
      >
        {isRunning && (
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500"></span>
          </span>
        )}
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </span>
    );
  };

  // Progress bar component
  const ProgressBar = ({ progress, completed, total }: { progress: number; completed: number; total: number }) => (
    <div className="w-full">
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-slate-500 dark:text-slate-400">{completed}/{total}</span>
        <span className="text-slate-500 dark:text-slate-400">{progress}%</span>
      </div>
      <div className="w-full h-2 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-600 dark:bg-blue-500 rounded-full transition-all duration-500"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-200 dark:border-slate-800 pb-5">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
            Parameter Optimization
          </h1>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
            Bayesian optimization jobs for strategy parameter discovery
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchJobs}
            disabled={isLoading}
            className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium text-slate-700 dark:text-slate-300 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600 rounded-lg transition-colors"
            onClick={openNewModal}
          >
            <Plus className="w-4 h-4" />
            New Optimization
          </button>
        </div>
      </div>

      {/* Error State */}
      {error && (
        <div className="p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-red-800 dark:text-red-300">{error}</p>
            <button
              onClick={fetchJobs}
              className="mt-2 text-sm text-red-600 dark:text-red-400 hover:underline"
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {/* Loading State */}
      {isLoading && !error && (
        <div className="flex justify-center py-12">
          <div className="flex flex-col items-center gap-3">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400"></div>
            <p className="text-sm text-slate-600 dark:text-slate-400">Loading optimization jobs...</p>
          </div>
        </div>
      )}

      {/* Jobs Table */}
      {!isLoading && !error && (
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
          {jobs.length === 0 ? (
            <div className="text-center py-12">
              <TrendingUp className="w-12 h-12 text-slate-400 mx-auto mb-4" />
              <h3 className="text-lg font-medium text-slate-900 dark:text-slate-100 mb-2">
                No optimization jobs yet
              </h3>
              <p className="text-sm text-slate-500 dark:text-slate-400 mb-4">
                Start your first parameter optimization to discover profitable strategies.
              </p>
              <button
                className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
                onClick={openNewModal}
              >
                <Plus className="w-4 h-4" />
                New Optimization
              </button>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
                  <thead className="bg-slate-50 dark:bg-slate-900">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        Job ID
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        Strategy
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        Pair
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        Status
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider min-w-[160px]">
                        Progress
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        Best Sharpe
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        Created
                      </th>
                      <th className="px-4 py-3 text-center text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        Action
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
                    {jobs.map(job => (
                      <tr
                        key={job.id}
                        className="hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors"
                      >
                        {/* Job ID */}
                        <td className="px-4 py-4 whitespace-nowrap">
                          <span className="text-sm font-mono text-slate-900 dark:text-slate-100">
                            {truncateJobId(job.id)}
                          </span>
                        </td>

                        {/* Strategy */}
                        <td className="px-4 py-4 whitespace-nowrap">
                          <div>
                            <span className="text-sm font-medium text-slate-900 dark:text-slate-100">
                              {job.strategy.toUpperCase()}
                            </span>
                            <p className="text-xs text-slate-500 dark:text-slate-400">
                              {getStrategyDisplayName(job.strategy)}
                            </p>
                          </div>
                        </td>

                        {/* Pair */}
                        <td className="px-4 py-4 whitespace-nowrap">
                          <span className="text-sm font-mono text-slate-900 dark:text-slate-100">
                            {job.pair}
                          </span>
                        </td>

                        {/* Status */}
                        <td className="px-4 py-4 whitespace-nowrap">
                          <StatusBadge status={job.status} />
                        </td>

                        {/* Progress */}
                        <td className="px-4 py-4">
                          {job.status === 'running' ? (
                            <ProgressBar
                              progress={job.progress}
                              completed={job.completedIterations}
                              total={job.estimatedIterations}
                            />
                          ) : job.status === 'completed' ? (
                            <div className="text-sm text-slate-600 dark:text-slate-400">
                              {job.completedIterations} iterations
                              {getDuration(job) && (
                                <span className="text-xs text-slate-400 dark:text-slate-500 ml-2">
                                  ({getDuration(job)})
                                </span>
                              )}
                            </div>
                          ) : job.status === 'failed' ? (
                            <div className="text-sm text-red-600 dark:text-red-400">
                              Failed at {job.completedIterations}/{job.estimatedIterations}
                            </div>
                          ) : (
                            <div className="text-sm text-slate-500 dark:text-slate-400 flex items-center gap-1">
                              <Clock className="w-3.5 h-3.5" />
                              Waiting...
                            </div>
                          )}
                        </td>

                        {/* Best Sharpe */}
                        <td className="px-4 py-4 whitespace-nowrap text-right">
                          {job.bestSharpe !== null ? (
                            <span
                              className={`text-sm font-mono font-medium ${
                                job.bestSharpe >= 1.0
                                  ? 'text-green-600 dark:text-green-400'
                                  : 'text-slate-900 dark:text-slate-100'
                              }`}
                            >
                              {formatSharpe(job.bestSharpe)}
                            </span>
                          ) : (
                            <span className="text-sm text-slate-400 dark:text-slate-500">-</span>
                          )}
                        </td>

                        {/* Created */}
                        <td className="px-4 py-4 whitespace-nowrap text-right">
                          <span className="text-sm text-slate-600 dark:text-slate-400">
                            {formatDate(job.createdAt)}
                          </span>
                        </td>

                        {/* Action */}
                        <td className="px-4 py-4 whitespace-nowrap text-center">
                          <Link
                            to={`/optimization/${job.id}`}
                            className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded-md transition-colors"
                          >
                            View
                            <ExternalLink className="w-3.5 h-3.5" />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="px-4 py-3 border-t border-slate-200 dark:border-slate-700 flex items-center justify-between">
                  <div className="text-sm text-slate-500 dark:text-slate-400">
                    Page {page} of {totalPages}
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setPage(p => Math.max(1, p - 1))}
                      disabled={page === 1}
                      className="px-3 py-1.5 text-sm font-medium text-slate-700 dark:text-slate-300 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded-md hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      Previous
                    </button>
                    <button
                      onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                      disabled={page === totalPages}
                      className="px-3 py-1.5 text-sm font-medium text-slate-700 dark:text-slate-300 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-600 rounded-md hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      Next
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Info Section */}
      <div className="bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <h3 className="text-lg font-medium text-slate-900 dark:text-slate-100 mb-3">
          About Parameter Optimization
        </h3>
        <div className="text-sm text-slate-600 dark:text-slate-400 space-y-2">
          <p>
            The Bayesian Parameter Optimizer uses probabilistic modeling to efficiently search
            for optimal strategy configurations. It evaluates each parameter combination through
            backtesting and uses the results to guide future exploration.
          </p>
          <p>
            <strong>Gate B Thresholds:</strong> Strategies must achieve Sharpe {'>='} 1.0,
            Max Drawdown {'<='} 10%, Hit Rate {'>='} 48%, and at least 300 trades to pass
            validation and proceed to paper trading.
          </p>
        </div>
      </div>

      {/* New Optimization Modal */}
      {showNewModal && (
        <div className="fixed inset-0 z-50 overflow-y-auto" role="dialog" aria-modal="true" aria-labelledby="modal-title">
          {/* Backdrop */}
          <div
            className="fixed inset-0 bg-black/50 transition-opacity"
            onClick={() => setShowNewModal(false)}
            aria-hidden="true"
          />

          {/* Modal */}
          <div className="flex min-h-full items-center justify-center p-4">
            <div className="relative w-full max-w-2xl bg-white dark:bg-slate-800 rounded-xl shadow-2xl">
              {/* Modal Header */}
              <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 dark:border-slate-700">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-blue-100 dark:bg-blue-900/50 rounded-lg">
                    <BarChart3 className="w-5 h-5 text-blue-600 dark:text-blue-400" />
                  </div>
                  <div>
                    <h2 id="modal-title" className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                      New Optimization Job
                    </h2>
                    <p className="text-sm text-slate-500 dark:text-slate-400">
                      Configure strategy parameters for Bayesian optimization
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setShowNewModal(false)}
                  className="p-2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              {/* Modal Body */}
              <form onSubmit={handleSubmitOptimization}>
                <div className="px-6 py-4 space-y-6 max-h-[60vh] overflow-y-auto">
                  {/* Strategy Selection */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                      <Settings className="w-4 h-4 inline mr-1" />
                      Strategy
                    </label>
                    <div className="grid grid-cols-3 gap-3">
                      {(Object.keys(STRATEGY_CONFIGS) as StrategyType[]).map(strategy => (
                        <button
                          key={strategy}
                          type="button"
                          onClick={() => handleStrategyChange(strategy)}
                          className={`p-3 rounded-lg border-2 text-left transition-colors ${
                            formData.strategy === strategy
                              ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
                              : 'border-slate-200 dark:border-slate-600 hover:border-slate-300 dark:hover:border-slate-500'
                          }`}
                        >
                          <div className="text-sm font-medium text-slate-900 dark:text-slate-100">
                            {strategy.toUpperCase()}
                          </div>
                          <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                            {STRATEGY_CONFIGS[strategy].name}
                          </div>
                        </button>
                      ))}
                    </div>
                    <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                      {STRATEGY_CONFIGS[formData.strategy].description}
                    </p>
                  </div>

                  {/* Forex Pair */}
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                        Forex Pair
                      </label>
                      <select
                        value={formData.pair}
                        onChange={e => setFormData(prev => ({ ...prev, pair: e.target.value }))}
                        className="w-full px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                      >
                        {FOREX_PAIRS.map(pair => (
                          <option key={pair} value={pair}>
                            {pair.replace('_', '/')}
                          </option>
                        ))}
                      </select>
                    </div>

                    {/* Data Years */}
                    <div>
                      <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                        <Calendar className="w-4 h-4 inline mr-1" />
                        Historical Data
                      </label>
                      <select
                        value={formData.dataYears}
                        onChange={e => setFormData(prev => ({ ...prev, dataYears: Number(e.target.value) }))}
                        className="w-full px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                      >
                        <option value={1}>1 Year</option>
                        <option value={2}>2 Years</option>
                        <option value={3}>3 Years</option>
                        <option value={5}>5 Years</option>
                      </select>
                    </div>
                  </div>

                  {/* Account Settings */}
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                        <DollarSign className="w-4 h-4 inline mr-1" />
                        Starting Account Balance
                      </label>
                      <div className="relative">
                        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">$</span>
                        <input
                          type="number"
                          value={formData.accountBalance}
                          onChange={e => setFormData(prev => ({ ...prev, accountBalance: Number(e.target.value) }))}
                          min={1000}
                          max={10000000}
                          step={1000}
                          className="w-full pl-8 pr-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                        />
                      </div>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-2">
                        Position Size
                      </label>
                      <select
                        value={formData.positionSize}
                        onChange={e => setFormData(prev => ({ ...prev, positionSize: Number(e.target.value) }))}
                        className="w-full px-3 py-2 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                      >
                        {POSITION_SIZES.map(size => (
                          <option key={size.value} value={size.value}>
                            {size.label}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {/* Parameter Ranges */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 dark:text-slate-300 mb-3">
                      Parameter Search Ranges
                    </label>
                    <div className="bg-slate-50 dark:bg-slate-700/50 rounded-lg p-4 space-y-4">
                      {STRATEGY_CONFIGS[formData.strategy].params.map(param => (
                        <div key={param.key} className="grid grid-cols-3 gap-3 items-center">
                          <label className="text-sm text-slate-600 dark:text-slate-300">
                            {param.label}
                          </label>
                          <div>
                            <label className="block text-xs text-slate-400 mb-1">Min</label>
                            <input
                              type="number"
                              value={formData.paramRanges[param.key]?.min ?? param.defaultMin}
                              onChange={e => handleParamRangeChange(param.key, 'min', Number(e.target.value))}
                              min={param.min}
                              max={param.max}
                              step={param.step}
                              className="w-full px-3 py-1.5 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                            />
                          </div>
                          <div>
                            <label className="block text-xs text-slate-400 mb-1">Max</label>
                            <input
                              type="number"
                              value={formData.paramRanges[param.key]?.max ?? param.defaultMax}
                              onChange={e => handleParamRangeChange(param.key, 'max', Number(e.target.value))}
                              min={param.min}
                              max={param.max}
                              step={param.step}
                              className="w-full px-3 py-1.5 text-sm rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                    <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                      The optimizer will search for optimal values within these ranges using Bayesian optimization.
                    </p>
                  </div>

                  {/* Estimated Iterations Info */}
                  <div className="p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
                    <div className="flex items-start gap-2">
                      <TrendingUp className="w-4 h-4 text-blue-600 dark:text-blue-400 mt-0.5" />
                      <div className="text-sm">
                        <span className="font-medium text-blue-800 dark:text-blue-300">
                          Estimated iterations:
                        </span>
                        <span className="text-blue-600 dark:text-blue-400 ml-1">
                          ~{10 + STRATEGY_CONFIGS[formData.strategy].params.length * 15} trials
                        </span>
                        <p className="text-xs text-blue-600 dark:text-blue-400 mt-1">
                          Bayesian optimization adapts based on results to find optimal parameters efficiently.
                        </p>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Modal Footer */}
                <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/50 rounded-b-xl">
                  <button
                    type="button"
                    onClick={() => setShowNewModal(false)}
                    className="px-4 py-2 text-sm font-medium text-slate-700 dark:text-slate-300 bg-white dark:bg-slate-700 border border-slate-300 dark:border-slate-600 rounded-lg hover:bg-slate-50 dark:hover:bg-slate-600 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed rounded-lg transition-colors"
                  >
                    {isSubmitting ? (
                      <>
                        <RefreshCw className="w-4 h-4 animate-spin" />
                        Creating...
                      </>
                    ) : (
                      <>
                        <Plus className="w-4 h-4" />
                        Start Optimization
                      </>
                    )}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
