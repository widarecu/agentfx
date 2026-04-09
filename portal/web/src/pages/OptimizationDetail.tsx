/**
 * Optimization Detail Page
 *
 * Displays details of a single optimization job:
 * - Header with job metadata
 * - Top 3 results cards (most important visual)
 * - Full trials table with sorting
 */

import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ArrowLeft,
  Calendar,
  Clock,
  Settings,
  AlertCircle,
  CheckCircle,
  XCircle,
  DollarSign,
  Layers,
  Database,
  BarChart3,
} from 'lucide-react';
import { optimizationApi } from '../services/optimizationApi';
import {
  OptimizationJob,
  OptimizationTrial,
  getStatusColor,
  getStrategyDisplayName,
} from '../types/optimization';
import MetricsCard from '../components/MetricsCard';
import TrialTable from '../components/TrialTable';

export default function OptimizationDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<OptimizationJob | null>(null);
  const [topTrials, setTopTrials] = useState<OptimizationTrial[]>([]);
  const [allTrials, setAllTrials] = useState<OptimizationTrial[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch job and trial data
  useEffect(() => {
    const fetchData = async () => {
      if (!jobId) return;

      try {
        setIsLoading(true);
        setError(null);

        // Fetch job details and trials in parallel
        const [jobData, trialsData, topData] = await Promise.all([
          optimizationApi.getJob(jobId),
          optimizationApi.getTrials(jobId),
          optimizationApi.getTopTrials(jobId, 3),
        ]);

        if (!jobData) {
          setError('Optimization job not found');
          return;
        }

        setJob(jobData);
        setAllTrials(trialsData.items);
        setTopTrials(topData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load optimization details');
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, [jobId]);

  // Format date for display
  const formatDate = (dateStr: string | null | undefined) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // Calculate duration
  const getDuration = () => {
    if (!job?.completedAt) return null;
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

  // Count Gate B passes
  const gateBPassCount = allTrials.filter(t => t.passesGateB).length;

  // Format position size to lot description
  const formatPositionSize = (units: number) => {
    if (units >= 100000) {
      return `${(units / 100000).toFixed(1)} Standard Lot${units > 100000 ? 's' : ''}`;
    } else if (units >= 10000) {
      return `${(units / 10000).toFixed(1)} Mini Lot${units > 10000 ? 's' : ''}`;
    } else {
      return `${(units / 1000).toFixed(1)} Micro Lot${units > 1000 ? 's' : ''}`;
    }
  };

  // Format currency
  const formatCurrency = (amount: number) => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(amount);
  };

  // Loading state
  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Link
            to="/optimization"
            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-slate-600 dark:text-slate-400" />
          </Link>
          <div className="h-8 w-64 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
        </div>
        <div className="flex justify-center py-16">
          <div className="flex flex-col items-center gap-3">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400"></div>
            <p className="text-sm text-slate-600 dark:text-slate-400">Loading optimization details...</p>
          </div>
        </div>
      </div>
    );
  }

  // Error state
  if (error || !job) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Link
            to="/optimization"
            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-slate-600 dark:text-slate-400" />
          </Link>
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
            Optimization Details
          </h1>
        </div>
        <div className="p-6 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <h3 className="font-medium text-red-800 dark:text-red-300">
              {error || 'Job not found'}
            </h3>
            <p className="text-sm text-red-600 dark:text-red-400 mt-1">
              The optimization job could not be loaded. It may have been deleted or the ID is invalid.
            </p>
            <Link
              to="/optimization"
              className="mt-3 inline-flex items-center gap-1 text-sm text-red-600 dark:text-red-400 hover:underline"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to Optimization Jobs
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const statusColorClasses = getStatusColor(job.status);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <Link
            to="/optimization"
            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            title="Back to list"
          >
            <ArrowLeft className="w-5 h-5 text-slate-600 dark:text-slate-400" />
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
                {job.strategy.toUpperCase()} - {job.pair}
              </h1>
              <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${statusColorClasses}`}>
                {job.status.charAt(0).toUpperCase() + job.status.slice(1)}
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
              {getStrategyDisplayName(job.strategy)}
            </p>
          </div>
        </div>

        {/* Job Metadata */}
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <div className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <Calendar className="w-4 h-4" />
            <span>{formatDate(job.createdAt)}</span>
          </div>
          {getDuration() && (
            <div className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
              <Clock className="w-4 h-4" />
              <span>{getDuration()}</span>
            </div>
          )}
          <div className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <Settings className="w-4 h-4" />
            <span>{job.completedIterations}/{job.estimatedIterations} iterations</span>
          </div>
          <div className="flex items-center gap-1.5 text-slate-600 dark:text-slate-400">
            <BarChart3 className="w-4 h-4" />
            <span>Timeframe: {job.timeframe}</span>
          </div>
        </div>
      </div>

      {/* Job Configuration */}
      <div className="bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg p-4 space-y-4">
        {/* Account Settings Row */}
        <div className="flex flex-wrap items-center gap-4">
          <span className="text-sm font-medium text-slate-700 dark:text-slate-300 min-w-[120px]">
            Account Settings:
          </span>
          <div className="flex flex-wrap gap-3">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 text-sm">
              <DollarSign className="w-3.5 h-3.5 text-green-600 dark:text-green-400" />
              <span className="text-slate-500 dark:text-slate-400">Balance:</span>
              <span className="font-mono font-medium text-slate-900 dark:text-slate-100">
                {formatCurrency(job.accountBalance)}
              </span>
            </span>
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 text-sm">
              <Layers className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
              <span className="text-slate-500 dark:text-slate-400">Position:</span>
              <span className="font-mono font-medium text-slate-900 dark:text-slate-100">
                {job.positionSize.toLocaleString()} units
              </span>
              <span className="text-slate-400 dark:text-slate-500">
                ({formatPositionSize(job.positionSize)})
              </span>
            </span>
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 text-sm">
              <Database className="w-3.5 h-3.5 text-purple-600 dark:text-purple-400" />
              <span className="text-slate-500 dark:text-slate-400">Historical Data:</span>
              <span className="font-mono font-medium text-slate-900 dark:text-slate-100">
                {job.dataYears} year{job.dataYears > 1 ? 's' : ''}
              </span>
            </span>
          </div>
        </div>

        {/* Parameter Ranges Row */}
        <div className="flex flex-wrap items-center gap-4">
          <span className="text-sm font-medium text-slate-700 dark:text-slate-300 min-w-[120px]">
            Parameter Ranges:
          </span>
          <div className="flex flex-wrap gap-2">
            {Object.entries(job.paramRanges).map(([param, range]) => (
              <span
                key={param}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-white dark:bg-slate-700 border border-slate-200 dark:border-slate-600 text-sm"
              >
                <span className="text-slate-500 dark:text-slate-400">{param}:</span>
                <span className="font-mono text-slate-900 dark:text-slate-100">
                  {range.min} - {range.max}
                </span>
              </span>
            ))}
          </div>
        </div>

        {/* Data Range Row */}
        {job.dataRangeStart && job.dataRangeEnd && (
          <div className="flex flex-wrap items-center gap-4">
            <span className="text-sm font-medium text-slate-700 dark:text-slate-300 min-w-[120px]">
              Data Range:
            </span>
            <span className="text-sm text-slate-600 dark:text-slate-400">
              {new Date(job.dataRangeStart).toLocaleDateString()} -{' '}
              {new Date(job.dataRangeEnd).toLocaleDateString()}
            </span>
          </div>
        )}
      </div>

      {/* Gate B Summary */}
      {allTrials.length > 0 && (
        <div className="flex items-center gap-6 p-4 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg">
          <div className="flex items-center gap-2">
            {gateBPassCount > 0 ? (
              <CheckCircle className="w-5 h-5 text-green-600 dark:text-green-400" />
            ) : (
              <XCircle className="w-5 h-5 text-amber-600 dark:text-amber-400" />
            )}
            <span className="text-sm font-medium text-slate-900 dark:text-slate-100">
              Gate B Results:
            </span>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <div>
              <span className="text-green-600 dark:text-green-400 font-semibold">
                {gateBPassCount}
              </span>
              <span className="text-slate-600 dark:text-slate-400 ml-1">passed</span>
            </div>
            <div>
              <span className="text-red-600 dark:text-red-400 font-semibold">
                {allTrials.length - gateBPassCount}
              </span>
              <span className="text-slate-600 dark:text-slate-400 ml-1">failed</span>
            </div>
            <div className="text-slate-400 dark:text-slate-500">
              ({((gateBPassCount / allTrials.length) * 100).toFixed(1)}% pass rate)
            </div>
          </div>
        </div>
      )}

      {/* Top 3 Results */}
      {topTrials.length > 0 && (
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-4">
            Top Results
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {topTrials.map((trial, index) => (
              <MetricsCard key={trial.id} trial={trial} rank={index + 1} jobId={job.id} />
            ))}
          </div>
        </div>
      )}

      {/* All Trials Table */}
      {allTrials.length > 0 && (
        <div>
          <TrialTable trials={allTrials} jobId={job.id} />
        </div>
      )}

      {/* No trials message */}
      {allTrials.length === 0 && job.status !== 'queued' && (
        <div className="text-center py-12 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg">
          <AlertCircle className="w-12 h-12 text-slate-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-slate-900 dark:text-slate-100 mb-2">
            No trials found
          </h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {job.status === 'running'
              ? 'Trials will appear here as they complete.'
              : 'This job did not produce any trial results.'}
          </p>
        </div>
      )}

      {/* Queued job message */}
      {job.status === 'queued' && (
        <div className="text-center py-12 bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg">
          <Clock className="w-12 h-12 text-slate-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-slate-900 dark:text-slate-100 mb-2">
            Job is queued
          </h3>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            This optimization job is waiting to start. Results will appear once processing begins.
          </p>
        </div>
      )}
    </div>
  );
}
