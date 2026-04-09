/**
 * Trial Detail Page
 *
 * Displays full details for a single optimization trial:
 * - Metrics summary panel with pips/dollars toggle
 * - Financing breakdown (spread, swap costs)
 * - Individual trades table
 * - Candlestick chart with entry/exit markers and indicators
 */

import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ArrowLeft,
  CheckCircle,
  XCircle,
  AlertCircle,
  TrendingUp,
  TrendingDown,
  BarChart3,
  Target,
  Hash,
  DollarSign,
  Coins,
  Calculator,
  List,
} from 'lucide-react';
import { optimizationApi } from '../services/optimizationApi';
import {
  TrialDetailData,
  MetricsViewMode,
  formatPercent,
  formatSharpe,
  formatPips,
  formatDollars,
  getStrategyDisplayName,
  GATE_B_THRESHOLDS,
} from '../types/optimization';
import StrategyChart from '../components/StrategyChart';
import TradesTable from '../components/TradesTable';

/**
 * Inline tooltip for metric labels.
 * Shows an ⓘ icon that reveals a description on hover — no external library.
 */
function MetricTooltip({ text }: { text: string }) {
  return (
    <span className="relative group ml-1 cursor-help">
      <svg
        className="inline-block w-3.5 h-3.5 text-slate-400"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="16" x2="12" y2="12" />
        <line x1="12" y1="8" x2="12.01" y2="8" />
      </svg>
      <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 text-xs font-normal normal-case text-slate-200 bg-slate-800 rounded-lg shadow-lg opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none w-64 z-50">
        {text}
      </span>
    </span>
  );
}

export default function TrialDetail() {
  const { jobId, trialId } = useParams<{ jobId: string; trialId: string }>();
  const [data, setData] = useState<TrialDetailData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<MetricsViewMode>('pips');
  const [showTrades, setShowTrades] = useState(true);

  // Fetch trial detail data
  useEffect(() => {
    const fetchData = async () => {
      if (!jobId || !trialId) return;

      try {
        setIsLoading(true);
        setError(null);

        const detailData = await optimizationApi.getTrialDetail(jobId, trialId);

        if (!detailData) {
          setError('Trial not found');
          return;
        }

        setData(detailData);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load trial details');
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, [jobId, trialId]);

  // Loading state
  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Link
            to={`/optimization/${jobId}`}
            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-slate-600 dark:text-slate-400" />
          </Link>
          <div className="h-8 w-64 bg-slate-200 dark:bg-slate-700 rounded animate-pulse" />
        </div>
        <div className="flex justify-center py-16">
          <div className="flex flex-col items-center gap-3">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-blue-600 dark:border-blue-400"></div>
            <p className="text-sm text-slate-600 dark:text-slate-400">Loading trial details...</p>
          </div>
        </div>
      </div>
    );
  }

  // Error state
  if (error || !data) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Link
            to={`/optimization/${jobId}`}
            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
          >
            <ArrowLeft className="w-5 h-5 text-slate-600 dark:text-slate-400" />
          </Link>
          <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
            Trial Details
          </h1>
        </div>
        <div className="p-6 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <h3 className="font-medium text-red-800 dark:text-red-300">
              {error || 'Trial not found'}
            </h3>
            <p className="text-sm text-red-600 dark:text-red-400 mt-1">
              The trial could not be loaded. It may have been deleted or the ID is invalid.
            </p>
            <Link
              to={`/optimization/${jobId}`}
              className="mt-3 inline-flex items-center gap-1 text-sm text-red-600 dark:text-red-400 hover:underline"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to Optimization Job
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const { trial, job, candles, signals, indicators, trades, extendedMetrics } = data;
  const { metrics, params, passesGateB, gateBFailures } = trial;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <Link
            to={`/optimization/${jobId}`}
            className="p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            title="Back to job details"
          >
            <ArrowLeft className="w-5 h-5 text-slate-600 dark:text-slate-400" />
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
                Trial Results
              </h1>
              {passesGateB ? (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-green-100 dark:bg-green-900/50 text-xs font-medium text-green-700 dark:text-green-400">
                  <CheckCircle className="w-3.5 h-3.5" />
                  Gate B Pass
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-red-100 dark:bg-red-900/50 text-xs font-medium text-red-700 dark:text-red-400">
                  <XCircle className="w-3.5 h-3.5" />
                  Gate B Fail
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
              {getStrategyDisplayName(job.strategy)} | {job.pair}
            </p>
          </div>
        </div>

        {/* View Mode Toggle + Parameters */}
        <div className="flex items-center gap-3">
          {/* View Mode Toggle */}
          <div className="inline-flex items-center rounded-lg bg-slate-100 dark:bg-slate-800 p-1">
            <button
              onClick={() => setViewMode('pips')}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                viewMode === 'pips'
                  ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100'
              }`}
            >
              <Coins className="w-4 h-4" />
              Pips
            </button>
            <button
              onClick={() => setViewMode('dollars')}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                viewMode === 'dollars'
                  ? 'bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100 shadow-sm'
                  : 'text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-100'
              }`}
            >
              <DollarSign className="w-4 h-4" />
              With Financing
            </button>
          </div>

          {/* Parameters */}
          {Object.entries(params).map(([key, value]) => (
            <div
              key={key}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700"
            >
              <span className="text-sm text-slate-500 dark:text-slate-400">{key}:</span>
              <span className="text-sm font-mono font-semibold text-slate-900 dark:text-slate-100">
                {value}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Performance Summary - Key Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        {/* Sharpe Ratio */}
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="w-4 h-4 text-slate-400" />
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase">
              Sharpe
              <MetricTooltip text="Risk-adjusted return. Measures excess return per unit of risk. Values above 1.0 are considered good, above 2.0 excellent. Calculated by annualizing daily returns (√252)." />
            </span>
          </div>
          <div
            className={`text-2xl font-bold font-mono ${
              metrics.sharpe >= GATE_B_THRESHOLDS.SHARPE_MIN
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {formatSharpe(metrics.sharpe)}
          </div>
          <div className="text-xs text-slate-400 mt-1">
            Threshold: {'>='} {GATE_B_THRESHOLDS.SHARPE_MIN}
          </div>
        </div>

        {/* Total P&L (pips or dollars based on view mode) */}
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-2">
            {viewMode === 'pips' ? (
              <Coins className="w-4 h-4 text-slate-400" />
            ) : (
              <DollarSign className="w-4 h-4 text-slate-400" />
            )}
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase">
              {viewMode === 'pips' ? 'Total P&L' : 'Net P&L'}
              {viewMode === 'pips' ? (
                <MetricTooltip text="Total profit or loss in pips from all closed trades, before financing costs (spread and swap)." />
              ) : (
                <MetricTooltip text="Profit or loss after deducting spread and swap costs. This is the realistic return you would have achieved." />
              )}
            </span>
          </div>
          <div
            className={`text-2xl font-bold font-mono ${
              (viewMode === 'pips' ? extendedMetrics.totalPips : extendedMetrics.netPnlDollars) >= 0
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {viewMode === 'pips'
              ? `${formatPips(extendedMetrics.totalPips)} pips`
              : formatDollars(extendedMetrics.netPnlDollars)}
          </div>
          <div className="text-xs text-slate-400 mt-1">
            {viewMode === 'pips'
              ? 'Raw strategy performance'
              : 'After spread & swap costs'}
          </div>
        </div>

        {/* Max Drawdown */}
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-2">
            <TrendingDown className="w-4 h-4 text-slate-400" />
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase">
              Max DD
              <MetricTooltip text="Maximum Drawdown — the largest peak-to-trough decline in account equity during the backtest period. Lower is better; above 10% is considered high risk." />
            </span>
          </div>
          <div
            className={`text-2xl font-bold font-mono ${
              metrics.maxDrawdown <= GATE_B_THRESHOLDS.MAX_DRAWDOWN
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {formatPercent(metrics.maxDrawdown)}
          </div>
          <div className="text-xs text-slate-400 mt-1">
            Threshold: {'<='} {formatPercent(GATE_B_THRESHOLDS.MAX_DRAWDOWN)}
          </div>
        </div>

        {/* Hit Rate */}
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Target className="w-4 h-4 text-slate-400" />
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase">
              Hit Rate
              <MetricTooltip text="Percentage of winning trades out of total closed trades. A strategy can be profitable with a hit rate below 50% if average wins exceed average losses." />
            </span>
          </div>
          <div
            className={`text-2xl font-bold font-mono ${
              metrics.hitRate >= GATE_B_THRESHOLDS.HIT_RATE_MIN
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {formatPercent(metrics.hitRate)}
          </div>
          <div className="text-xs text-slate-400 mt-1">
            Threshold: {'>='} {formatPercent(GATE_B_THRESHOLDS.HIT_RATE_MIN)}
          </div>
        </div>

        {/* Trade Count */}
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Hash className="w-4 h-4 text-slate-400" />
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase">
              Trades
              <MetricTooltip text="Total number of closed trades during the backtest. More trades provide higher statistical confidence. Minimum 300 trades recommended for reliable results." />
            </span>
          </div>
          <div
            className={`text-2xl font-bold font-mono ${
              metrics.tradeCount >= GATE_B_THRESHOLDS.MIN_TRADES
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {metrics.tradeCount}
          </div>
          <div className="text-xs text-slate-400 mt-1">
            Threshold: {'>='} {GATE_B_THRESHOLDS.MIN_TRADES}
          </div>
        </div>

        {/* Profit Factor */}
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-2">
            <BarChart3 className="w-4 h-4 text-slate-400" />
            <span className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase">
              Profit Factor
              <MetricTooltip text="Ratio of gross profit to gross loss. Values above 1.0 mean the strategy is profitable. Above 1.5 is good, above 2.0 is excellent." />
            </span>
          </div>
          <div
            className={`text-2xl font-bold font-mono ${
              metrics.profitFactor >= 1.5
                ? 'text-green-600 dark:text-green-400'
                : metrics.profitFactor >= 1.0
                ? 'text-amber-600 dark:text-amber-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {metrics.profitFactor.toFixed(2)}
          </div>
          <div className="text-xs text-slate-400 mt-1">Gross Profit / Loss</div>
        </div>
      </div>

      {/* Financing Breakdown (only shown in dollars mode) */}
      {viewMode === 'dollars' && (
        <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
          <div className="flex items-center gap-2 mb-4">
            <Calculator className="w-5 h-5 text-slate-400" />
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Financing Breakdown
            </h3>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-sm">
            <div>
              <div className="text-slate-500 dark:text-slate-400">
                Gross P&L
                <MetricTooltip text="Total profit or loss before any financing costs are deducted. Represents raw strategy performance." />
              </div>
              <div
                className={`text-lg font-mono font-semibold ${
                  extendedMetrics.grossPnlDollars >= 0
                    ? 'text-green-600 dark:text-green-400'
                    : 'text-red-600 dark:text-red-400'
                }`}
              >
                {formatDollars(extendedMetrics.grossPnlDollars)}
              </div>
              <div className="text-xs text-slate-400">{formatPips(extendedMetrics.totalPips)} pips</div>
            </div>
            <div>
              <div className="text-slate-500 dark:text-slate-400">
                Spread Cost
                <MetricTooltip text="Cumulative cost of bid-ask spreads across all trades. Each trade incurs a spread cost upon entry." />
              </div>
              <div className="text-lg font-mono font-semibold text-amber-600 dark:text-amber-400">
                -{formatDollars(extendedMetrics.totalSpreadCostDollars, false)}
              </div>
              <div className="text-xs text-slate-400">-{extendedMetrics.totalSpreadCostPips.toFixed(1)} pips</div>
            </div>
            <div>
              <div className="text-slate-500 dark:text-slate-400">
                Swap/Interest
                <MetricTooltip text="Overnight financing charges for holding positions past the daily rollover time. Can be positive or negative depending on the interest rate differential." />
              </div>
              <div className="text-lg font-mono font-semibold text-amber-600 dark:text-amber-400">
                -{formatDollars(extendedMetrics.totalSwapCostDollars, false)}
              </div>
              <div className="text-xs text-slate-400">-{extendedMetrics.totalSwapCostPips.toFixed(1)} pips</div>
            </div>
            <div>
              <div className="text-slate-500 dark:text-slate-400">Total Costs</div>
              <div className="text-lg font-mono font-semibold text-amber-600 dark:text-amber-400">
                -{formatDollars(extendedMetrics.totalSpreadCostDollars + extendedMetrics.totalSwapCostDollars, false)}
              </div>
              <div className="text-xs text-slate-400">-{extendedMetrics.totalCostPips.toFixed(1)} pips</div>
            </div>
            <div className="bg-slate-50 dark:bg-slate-700/50 rounded-lg p-2 -m-2">
              <div className="text-slate-500 dark:text-slate-400">
                Net P&L
                <MetricTooltip text="Profit or loss after deducting spread and swap costs. This is the realistic return you would have achieved." />
              </div>
              <div
                className={`text-lg font-mono font-bold ${
                  extendedMetrics.netPnlDollars >= 0
                    ? 'text-green-600 dark:text-green-400'
                    : 'text-red-600 dark:text-red-400'
                }`}
              >
                {formatDollars(extendedMetrics.netPnlDollars)}
              </div>
              <div className="text-xs text-slate-400">{formatPips(extendedMetrics.netPips)} pips</div>
            </div>
          </div>
        </div>
      )}

      {/* Additional Metrics Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
          <div className="text-xs text-slate-500 dark:text-slate-400">
            Avg Win
            <MetricTooltip text="Average profit per winning trade in pips or dollars. Higher values indicate the strategy captures larger moves." />
          </div>
          <div className="text-lg font-mono font-semibold text-green-600 dark:text-green-400">
            {viewMode === 'pips'
              ? `+${((metrics.avgWin || 0) * 10000).toFixed(1)} pips`
              : formatDollars((metrics.avgWin || 0) * 10000 * 0.1)}
          </div>
        </div>
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
          <div className="text-xs text-slate-500 dark:text-slate-400">
            Avg Loss
            <MetricTooltip text="Average loss per losing trade in pips or dollars. Smaller values indicate tighter risk management." />
          </div>
          <div className="text-lg font-mono font-semibold text-red-600 dark:text-red-400">
            {viewMode === 'pips'
              ? `${((metrics.avgLoss || 0) * 10000).toFixed(1)} pips`
              : formatDollars((metrics.avgLoss || 0) * 10000 * 0.1)}
          </div>
        </div>
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
          <div className="text-xs text-slate-500 dark:text-slate-400">
            Avg P&L per Trade
            <MetricTooltip text="Expected value per trade — the average profit or loss across all trades. Positive values indicate a profitable edge." />
          </div>
          <div
            className={`text-lg font-mono font-semibold ${
              extendedMetrics.avgPipsPerTrade >= 0
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {viewMode === 'pips'
              ? `${formatPips(extendedMetrics.avgPipsPerTrade)} pips`
              : formatDollars(extendedMetrics.avgPipsPerTrade * 0.1)}
          </div>
        </div>
        <div className="bg-slate-50 dark:bg-slate-800/50 rounded-lg p-3">
          <div className="text-xs text-slate-500 dark:text-slate-400">
            Max Consec. Losses
            <MetricTooltip text="Longest streak of consecutive losing trades. Important for sizing positions and managing psychological risk during drawdowns." />
          </div>
          <div className="text-lg font-mono font-semibold text-slate-900 dark:text-slate-100">
            {metrics.maxConsecutiveLosses || 'N/A'}
          </div>
        </div>
      </div>

      {/* Gate B Failures (if any) */}
      {!passesGateB && gateBFailures.length > 0 && (
        <div className="p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
          <div className="flex items-start gap-3">
            <XCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
            <div>
              <h3 className="font-medium text-red-800 dark:text-red-300">
                Gate B Validation Failed
              </h3>
              <ul className="mt-2 space-y-1">
                {gateBFailures.map((failure, idx) => (
                  <li key={idx} className="text-sm text-red-600 dark:text-red-400">
                    {failure}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      {/* Strategy Chart */}
      <div>
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100 mb-4">
          Backtest Visualization
        </h2>
        <StrategyChart
          candles={candles}
          signals={signals}
          indicators={indicators}
          instrument={job.pair}
          strategy={job.strategy}
          params={params}
          timeframe={job.timeframe}
        />
      </div>

      {/* Chart Legend Info */}
      <div className="bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg p-4">
        <h3 className="text-sm font-medium text-slate-900 dark:text-slate-100 mb-2">
          Chart Guide
        </h3>
        <div className="text-sm text-slate-600 dark:text-slate-400 space-y-1">
          <p>
            <strong>Green arrow up:</strong> Long entry signal | <strong>Red arrow down:</strong>{' '}
            Short entry signal
          </p>
          <p>
            <strong>Green circle:</strong> Profitable exit | <strong>Red circle:</strong> Losing
            exit
          </p>
          <p>
            <strong>Blue line:</strong> Fast MA | <strong>Orange line:</strong> Slow MA
          </p>
          <p>
            The numbers on exit markers show P&L in pips (multiply by 0.0001 for price difference).
          </p>
        </div>
      </div>

      {/* Trades Table Section */}
      <div className="bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700 p-4">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <List className="w-5 h-5 text-slate-400" />
            <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Individual Trades ({trades.length})
            </h3>
          </div>
          <button
            onClick={() => setShowTrades(!showTrades)}
            className="text-sm text-blue-600 dark:text-blue-400 hover:underline"
          >
            {showTrades ? 'Hide' : 'Show'}
          </button>
        </div>
        {showTrades && (
          <TradesTable trades={trades} viewMode={viewMode} pair={job.pair} />
        )}
      </div>
    </div>
  );
}
