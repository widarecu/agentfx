/**
 * TrialTable Component
 *
 * Displays a sortable table of optimization trials with:
 * - All trials for a job
 * - Sortable columns (Sharpe, Max DD, Hit Rate, etc.)
 * - Gate B pass/fail indicators
 * - Click-through to trial detail
 */

import React, { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  ChevronUp,
  ChevronDown,
  CheckCircle,
  XCircle,
  Eye,
  ChevronsUpDown,
} from 'lucide-react';
import {
  OptimizationTrial,
  formatPercent,
  formatPercentMagnitude,
  formatSharpe,
} from '../types/optimization';

type SortKey =
  | 'sharpe'
  | 'maxDrawdown'
  | 'hitRate'
  | 'tradeCount'
  | 'totalReturn'
  | 'profitFactor';
type SortDirection = 'asc' | 'desc';

interface TrialTableProps {
  trials: OptimizationTrial[];
  jobId: string;
  onTrialClick?: (trial: OptimizationTrial) => void;
}

interface SortConfig {
  key: SortKey;
  direction: SortDirection;
}

export default function TrialTable({ trials, jobId, onTrialClick }: TrialTableProps) {
  const [sortConfig, setSortConfig] = useState<SortConfig>({
    key: 'sharpe',
    direction: 'desc',
  });
  const [expanded, setExpanded] = useState(false);

  // Sort trials based on current sort config
  const sortedTrials = useMemo(() => {
    const sorted = [...trials].sort((a, b) => {
      const aValue = a.metrics[sortConfig.key];
      const bValue = b.metrics[sortConfig.key];

      if (sortConfig.direction === 'asc') {
        return aValue - bValue;
      }
      return bValue - aValue;
    });
    return sorted;
  }, [trials, sortConfig]);

  // Display limited or all trials based on expanded state
  const displayedTrials = expanded ? sortedTrials : sortedTrials.slice(0, 10);

  // Handle sort column click
  const handleSort = (key: SortKey) => {
    setSortConfig(prev => {
      if (prev.key === key) {
        return {
          key,
          direction: prev.direction === 'asc' ? 'desc' : 'asc',
        };
      }
      return { key, direction: 'desc' };
    });
  };

  // Render sort indicator
  const SortIndicator = ({ columnKey }: { columnKey: SortKey }) => {
    if (sortConfig.key !== columnKey) {
      return <ChevronsUpDown className="w-3.5 h-3.5 text-slate-400" />;
    }
    return sortConfig.direction === 'asc' ? (
      <ChevronUp className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
    ) : (
      <ChevronDown className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400" />
    );
  };

  // Sortable column header
  const SortableHeader = ({
    label,
    columnKey,
    align = 'right',
  }: {
    label: string;
    columnKey: SortKey;
    align?: 'left' | 'right';
  }) => (
    <th
      className={`px-4 py-3 text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors ${
        align === 'right' ? 'text-right' : 'text-left'
      }`}
      onClick={() => handleSort(columnKey)}
    >
      <div
        className={`flex items-center gap-1 ${align === 'right' ? 'justify-end' : 'justify-start'}`}
      >
        <span>{label}</span>
        <SortIndicator columnKey={columnKey} />
      </div>
    </th>
  );

  if (trials.length === 0) {
    return (
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-8 text-center">
        <p className="text-slate-500 dark:text-slate-400">No trials found for this job.</p>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
        <h3 className="text-lg font-medium text-slate-900 dark:text-slate-100">
          All Trials ({trials.length})
        </h3>
        <div className="text-xs text-slate-500 dark:text-slate-400">
          Click column headers to sort
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-200 dark:divide-slate-700">
          <thead className="bg-slate-50 dark:bg-slate-900">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                Trial
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                Params
              </th>
              <SortableHeader label="Sharpe" columnKey="sharpe" />
              <SortableHeader label="Max DD" columnKey="maxDrawdown" />
              <SortableHeader label="Hit Rate" columnKey="hitRate" />
              <SortableHeader label="Trades" columnKey="tradeCount" />
              <SortableHeader label="Return" columnKey="totalReturn" />
              <SortableHeader label="PF" columnKey="profitFactor" />
              <th className="px-4 py-3 text-center text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                Gate B
              </th>
              <th className="px-4 py-3 text-center text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                Action
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
            {displayedTrials.map((trial, index) => (
              <tr
                key={trial.id}
                className="hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors"
              >
                {/* Trial ID */}
                <td className="px-4 py-3 whitespace-nowrap">
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-slate-100 dark:bg-slate-700 text-xs font-medium text-slate-600 dark:text-slate-300">
                      {index + 1}
                    </span>
                    <span className="text-sm font-mono text-slate-600 dark:text-slate-400">
                      {trial.id.split('_').pop()}
                    </span>
                  </div>
                </td>

                {/* Parameters */}
                <td className="px-4 py-3 whitespace-nowrap">
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(trial.params).map(([key, value]) => (
                      <span
                        key={key}
                        className="inline-flex items-center px-1.5 py-0.5 rounded text-xs bg-slate-100 dark:bg-slate-700"
                      >
                        <span className="text-slate-500 dark:text-slate-400">{key}:</span>
                        <span className="font-mono font-medium text-slate-700 dark:text-slate-200 ml-0.5">
                          {value}
                        </span>
                      </span>
                    ))}
                  </div>
                </td>

                {/* Sharpe */}
                <td className="px-4 py-3 whitespace-nowrap text-right">
                  <span
                    className={`font-mono font-medium ${
                      trial.metrics.sharpe >= 1.0
                        ? 'text-green-600 dark:text-green-400'
                        : 'text-slate-900 dark:text-slate-100'
                    }`}
                  >
                    {formatSharpe(trial.metrics.sharpe)}
                  </span>
                </td>

                {/* Max Drawdown */}
                <td className="px-4 py-3 whitespace-nowrap text-right">
                  <span
                    className={`font-mono font-medium ${
                      trial.metrics.maxDrawdown <= 0.1
                        ? 'text-green-600 dark:text-green-400'
                        : 'text-red-600 dark:text-red-400'
                    }`}
                  >
                    {formatPercentMagnitude(trial.metrics.maxDrawdown)}
                  </span>
                </td>

                {/* Hit Rate */}
                <td className="px-4 py-3 whitespace-nowrap text-right">
                  <span
                    className={`font-mono font-medium ${
                      trial.metrics.hitRate >= 0.48
                        ? 'text-green-600 dark:text-green-400'
                        : 'text-slate-900 dark:text-slate-100'
                    }`}
                  >
                    {formatPercent(trial.metrics.hitRate)}
                  </span>
                </td>

                {/* Trade Count */}
                <td className="px-4 py-3 whitespace-nowrap text-right">
                  <span
                    className={`font-mono font-medium ${
                      trial.metrics.tradeCount >= 300
                        ? 'text-green-600 dark:text-green-400'
                        : 'text-slate-900 dark:text-slate-100'
                    }`}
                  >
                    {trial.metrics.tradeCount}
                  </span>
                </td>

                {/* Total Return */}
                <td className="px-4 py-3 whitespace-nowrap text-right">
                  <span
                    className={`font-mono font-medium ${
                      trial.metrics.totalReturn >= 0
                        ? 'text-green-600 dark:text-green-400'
                        : 'text-red-600 dark:text-red-400'
                    }`}
                  >
                    {trial.metrics.totalReturn >= 0 ? '+' : ''}
                    {formatPercent(trial.metrics.totalReturn)}
                  </span>
                </td>

                {/* Profit Factor */}
                <td className="px-4 py-3 whitespace-nowrap text-right">
                  <span className="font-mono font-medium text-slate-900 dark:text-slate-100">
                    {trial.metrics.profitFactor.toFixed(2)}
                  </span>
                </td>

                {/* Gate B Status */}
                <td className="px-4 py-3 whitespace-nowrap text-center">
                  {trial.passesGateB ? (
                    <div className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/50">
                      <CheckCircle className="w-3.5 h-3.5 text-green-600 dark:text-green-400" />
                      <span className="text-xs font-medium text-green-700 dark:text-green-400">
                        Pass
                      </span>
                    </div>
                  ) : (
                    <div className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-100 dark:bg-red-900/50">
                      <XCircle className="w-3.5 h-3.5 text-red-600 dark:text-red-400" />
                      <span className="text-xs font-medium text-red-700 dark:text-red-400">
                        Fail
                      </span>
                    </div>
                  )}
                </td>

                {/* Action */}
                <td className="px-4 py-3 whitespace-nowrap text-center">
                  <Link
                    to={`/optimization/${jobId}/trials/${trial.id}`}
                    className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/50 text-xs font-medium transition-colors"
                  >
                    <Eye className="w-3.5 h-3.5" />
                    View
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Show More / Show Less Button */}
      {trials.length > 10 && (
        <div className="px-4 py-3 border-t border-slate-200 dark:border-slate-700 text-center">
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-sm font-medium text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 transition-colors"
          >
            {expanded
              ? `Show Less (10 of ${trials.length})`
              : `Show All (${trials.length} trials)`}
          </button>
        </div>
      )}
    </div>
  );
}
