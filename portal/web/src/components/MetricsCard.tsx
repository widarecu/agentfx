/**
 * MetricsCard Component
 *
 * Displays a single optimization trial result as a card with:
 * - Rank indicator (#1, #2, #3)
 * - Parameter values
 * - Key metrics (Sharpe, Max DD, Hit Rate, Trade Count)
 * - Gate B pass/fail indicator
 * - View Chart button
 */

import React from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle, XCircle, TrendingUp, BarChart3, Target, Hash, Eye } from 'lucide-react';
import { OptimizationTrial, formatPercent, formatSharpe } from '../types/optimization';

interface MetricsCardProps {
  trial: OptimizationTrial;
  rank: number;
  jobId: string;
}

export default function MetricsCard({ trial, rank, jobId }: MetricsCardProps) {
  const { metrics, params, passesGateB, gateBFailures } = trial;

  // Determine rank badge styling
  const getRankBadge = () => {
    const baseClasses = 'flex items-center justify-center w-10 h-10 rounded-full font-bold text-lg';
    switch (rank) {
      case 1:
        return `${baseClasses} bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400`;
      case 2:
        return `${baseClasses} bg-slate-200 text-slate-600 dark:bg-slate-700 dark:text-slate-300`;
      case 3:
        return `${baseClasses} bg-orange-100 text-orange-700 dark:bg-orange-900/50 dark:text-orange-400`;
      default:
        return `${baseClasses} bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400`;
    }
  };

  // Get card border color based on Gate B status
  const getCardBorder = () => {
    if (passesGateB) {
      return 'border-green-200 dark:border-green-800';
    }
    return 'border-slate-200 dark:border-slate-700';
  };

  return (
    <div
      className={`bg-white dark:bg-slate-800 rounded-lg border-2 ${getCardBorder()} p-5 transition-shadow hover:shadow-md`}
    >
      {/* Header: Rank + Gate B Status */}
      <div className="flex items-start justify-between mb-4">
        <div className={getRankBadge()}>#{rank}</div>
        <div className="flex items-center gap-2">
          {passesGateB ? (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-green-100 dark:bg-green-900/50">
              <CheckCircle className="w-4 h-4 text-green-600 dark:text-green-400" />
              <span className="text-xs font-medium text-green-700 dark:text-green-400">
                Gate B Pass
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-red-100 dark:bg-red-900/50">
              <XCircle className="w-4 h-4 text-red-600 dark:text-red-400" />
              <span className="text-xs font-medium text-red-700 dark:text-red-400">
                Gate B Fail
              </span>
            </div>
          )}
        </div>
      </div>

      {/* Parameters */}
      <div className="mb-4 p-3 rounded-lg bg-slate-50 dark:bg-slate-900/50">
        <div className="text-xs font-medium text-slate-500 dark:text-slate-400 mb-2">
          Parameters
        </div>
        <div className="flex flex-wrap gap-2">
          {Object.entries(params).map(([key, value]) => (
            <div
              key={key}
              className="inline-flex items-center gap-1 px-2 py-1 rounded bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700"
            >
              <span className="text-xs text-slate-500 dark:text-slate-400">{key}:</span>
              <span className="text-sm font-mono font-semibold text-slate-900 dark:text-slate-100">
                {value}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Metrics Grid */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        {/* Sharpe Ratio */}
        <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-900/50">
          <div className="flex items-center gap-1.5 mb-1">
            <TrendingUp className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Sharpe</span>
          </div>
          <div
            className={`text-xl font-bold font-mono ${
              metrics.sharpe >= 1.0
                ? 'text-green-600 dark:text-green-400'
                : 'text-slate-900 dark:text-slate-100'
            }`}
          >
            {formatSharpe(metrics.sharpe)}
          </div>
        </div>

        {/* Max Drawdown */}
        <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-900/50">
          <div className="flex items-center gap-1.5 mb-1">
            <BarChart3 className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Max DD</span>
          </div>
          <div
            className={`text-xl font-bold font-mono ${
              metrics.maxDrawdown <= 0.10
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {formatPercent(metrics.maxDrawdown)}
          </div>
        </div>

        {/* Hit Rate */}
        <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-900/50">
          <div className="flex items-center gap-1.5 mb-1">
            <Target className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Hit Rate</span>
          </div>
          <div
            className={`text-xl font-bold font-mono ${
              metrics.hitRate >= 0.48
                ? 'text-green-600 dark:text-green-400'
                : 'text-slate-900 dark:text-slate-100'
            }`}
          >
            {formatPercent(metrics.hitRate)}
          </div>
        </div>

        {/* Trade Count */}
        <div className="p-3 rounded-lg bg-slate-50 dark:bg-slate-900/50">
          <div className="flex items-center gap-1.5 mb-1">
            <Hash className="w-3.5 h-3.5 text-slate-400" />
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400">Trades</span>
          </div>
          <div
            className={`text-xl font-bold font-mono ${
              metrics.tradeCount >= 300
                ? 'text-green-600 dark:text-green-400'
                : 'text-slate-900 dark:text-slate-100'
            }`}
          >
            {metrics.tradeCount}
          </div>
        </div>
      </div>

      {/* Additional Metrics */}
      <div className="flex items-center gap-4 mb-4 text-sm">
        <div>
          <span className="text-slate-500 dark:text-slate-400">Return:</span>{' '}
          <span
            className={`font-mono font-medium ${
              metrics.totalReturn >= 0
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {metrics.totalReturn >= 0 ? '+' : ''}
            {formatPercent(metrics.totalReturn)}
          </span>
        </div>
        <div>
          <span className="text-slate-500 dark:text-slate-400">PF:</span>{' '}
          <span className="font-mono font-medium text-slate-900 dark:text-slate-100">
            {metrics.profitFactor.toFixed(2)}
          </span>
        </div>
      </div>

      {/* Gate B Failures (if any) */}
      {!passesGateB && gateBFailures.length > 0 && (
        <div className="mb-4 p-2 rounded bg-red-50 dark:bg-red-900/20 border border-red-100 dark:border-red-800">
          <div className="text-xs text-red-700 dark:text-red-400">
            <span className="font-medium">Gate B Failures:</span>
            <ul className="mt-1 ml-3 list-disc">
              {gateBFailures.map((failure, idx) => (
                <li key={idx}>{failure}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* View Chart Button */}
      <Link
        to={`/optimization/${jobId}/trials/${trial.id}`}
        className="flex items-center justify-center gap-2 w-full py-2.5 px-4 rounded-lg bg-blue-600 hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-600 text-white font-medium text-sm transition-colors"
      >
        <Eye className="w-4 h-4" />
        View Chart
      </Link>
    </div>
  );
}
