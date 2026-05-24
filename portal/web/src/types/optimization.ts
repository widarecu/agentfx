/**
 * TypeScript interfaces for the Bayesian Parameter Optimizer (v0.2.0)
 * These define the data structures for optimization jobs, trials, and visualization.
 */

// Gate B threshold constants for validation. Mirrors the backend
// defaults in src/agentfx/core/gates.py; per-run overrides flow
// through the run's config_json and are read from the run detail
// rather than this constant.
export const GATE_B_THRESHOLDS = {
  SHARPE_MIN: 1.0,
  MAX_DRAWDOWN: 0.10, // 10%
  HIT_RATE_MIN: 0.48, // 48%
  MIN_TRADES: 300,
  MIN_CAGR: 0.15, // 15% net annualised return per year
} as const;

// Strategy types supported by the optimizer
export type StrategyType = 'ma' | 'rsi' | 'donchian';

// Job status states
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed';

// Trade signal types
export type SignalType = 'entry_long' | 'exit_long' | 'entry_short' | 'exit_short';

// Parameter range definition for optimization
export interface ParamRange {
  min: number;
  max: number;
  step?: number;
}

// Optimization job representing a parameter search
export interface OptimizationJob {
  id: string; // Format: optjob_{timestamp}_{short_uuid}
  strategy: StrategyType;
  pair: string; // EUR_USD, GBP_USD, etc.
  status: JobStatus;
  progress: number; // 0-100
  estimatedIterations: number;
  completedIterations: number;
  bestSharpe: number | null;
  createdAt: string; // ISO timestamp
  completedAt: string | null;
  paramRanges: Record<string, ParamRange>;
  dataRangeStart?: string; // ISO timestamp
  dataRangeEnd?: string; // ISO timestamp
  // Account/position settings
  accountBalance: number; // Starting account balance in dollars
  positionSize: number; // Units (e.g., 10000 = 0.1 lot)
  dataYears: number; // Years of historical data to use
  timeframe: string; // M1, M5, M10, M30, H1, H4, D
}

// Trial metrics from backtest
export interface TrialMetrics {
  sharpe: number;
  maxDrawdown: number; // 0.06 = 6%
  hitRate: number; // 0.54 = 54%
  tradeCount: number;
  totalReturn: number; // 0.15 = 15%
  profitFactor: number;
  avgWin?: number;
  avgLoss?: number;
  maxConsecutiveLosses?: number;
  avgTradeDuration?: number; // in hours
}

// Single optimization trial result
export interface OptimizationTrial {
  id: string; // Format: trial_{job_id}_{index}
  jobId: string;
  params: Record<string, number>; // { fast: 10, slow: 50 }
  metrics: TrialMetrics;
  passesGateB: boolean;
  gateBFailures: string[]; // e.g., ["sharpe < 1.0", "mdd > 10%"]
  createdAt: string;
}

// Trade signal for chart visualization
export interface TradeSignal {
  timestamp: string;
  signal: -1 | 0 | 1; // -1 = short, 0 = flat, 1 = long
  signalType: SignalType;
  price: number;
  pnl?: number; // P&L for exit signals
  indicatorValues: Record<string, number>; // { fast_ma: 1.1220, slow_ma: 1.1200 }
}

// Individual completed trade record
export interface Trade {
  id: string;
  trialId: string;
  direction: 'long' | 'short';
  entryTimestamp: string;
  exitTimestamp: string;
  entryPrice: number;
  exitPrice: number;
  // Results without financing (raw strategy performance)
  pips: number; // P&L in pips (1 pip = 0.0001 for most pairs, 0.01 for JPY pairs)
  // Financing/costs
  spreadCostPips: number; // OANDA spread paid once over the round trip
  slippageCostPips: number; // Execution slippage on entry + exit (2 × per-side)
  swapCostPips: number; // Overnight financing cost in pips (cumulative)
  // Results with financing (real-world P&L)
  netPips: number; // pips - spreadCostPips - slippageCostPips - swapCostPips
  // Dollar values (based on position size)
  grossPnlDollars: number; // P&L in dollars without costs
  spreadCostDollars: number;
  slippageCostDollars: number;
  swapCostDollars: number;
  netPnlDollars: number; // grossPnlDollars - spreadCostDollars - slippageCostDollars - swapCostDollars
  // Metadata
  durationMinutes: number;
  positionSize: number; // Units (e.g., 10000 = 0.1 lot)
}

// View mode for displaying metrics
export type MetricsViewMode = 'pips' | 'dollars';

// Extended trial metrics with financing breakdown
export interface TrialMetricsExtended extends TrialMetrics {
  // Pips-based (without financing)
  totalPips: number;
  avgPipsPerTrade: number;
  // Financing costs
  totalSpreadCostPips: number;
  totalSwapCostPips: number;
  totalCostPips: number;
  // Net (with financing)
  netPips: number;
  avgNetPipsPerTrade: number;
  // Dollar-based (assuming standard position size)
  grossPnlDollars: number;
  totalSpreadCostDollars: number;
  totalSwapCostDollars: number;
  netPnlDollars: number;
}

// OHLCV candle data
export interface CandleData {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// Indicator line data for chart overlay
export interface IndicatorLine {
  name: string;
  color: string;
  data: { timestamp: string; value: number }[];
}

// Complete trial detail with chart data
export interface TrialDetailData {
  trial: OptimizationTrial;
  job: OptimizationJob;
  candles: CandleData[];
  signals: TradeSignal[];
  indicators: IndicatorLine[];
  trades: Trade[];
  extendedMetrics: TrialMetricsExtended;
}

// API response types
export interface OptimizationJobListResponse {
  items: OptimizationJob[];
  total: number;
  page: number;
  pageSize: number;
}

export interface OptimizationTrialListResponse {
  items: OptimizationTrial[];
  total: number;
  jobId: string;
}

// Helper function to evaluate Gate B pass/fail
export function evaluateGateB(metrics: TrialMetrics): { passes: boolean; failures: string[] } {
  const failures: string[] = [];

  if (metrics.sharpe < GATE_B_THRESHOLDS.SHARPE_MIN) {
    failures.push(`Sharpe ${metrics.sharpe.toFixed(2)} < ${GATE_B_THRESHOLDS.SHARPE_MIN}`);
  }

  if (metrics.maxDrawdown > GATE_B_THRESHOLDS.MAX_DRAWDOWN) {
    failures.push(
      `Max DD ${(Math.abs(metrics.maxDrawdown) * 100).toFixed(1)}% > ${GATE_B_THRESHOLDS.MAX_DRAWDOWN * 100}%`,
    );
  }

  if (metrics.hitRate < GATE_B_THRESHOLDS.HIT_RATE_MIN) {
    failures.push(`Hit Rate ${(metrics.hitRate * 100).toFixed(1)}% < ${GATE_B_THRESHOLDS.HIT_RATE_MIN * 100}%`);
  }

  if (metrics.tradeCount < GATE_B_THRESHOLDS.MIN_TRADES) {
    failures.push(`Trade Count ${metrics.tradeCount} < ${GATE_B_THRESHOLDS.MIN_TRADES}`);
  }

  return {
    passes: failures.length === 0,
    failures,
  };
}

// Format helpers
export function formatPercent(value: number, decimals: number = 1): string {
  return `${(value * 100).toFixed(decimals)}%`;
}

/**
 * Render a metric as a positive-magnitude percentage. Used for inherently
 * negative metrics like Max Drawdown where the minus sign carries no
 * information (a drawdown is a loss by definition) and the negative-vs-
 * positive-threshold comparison reads as a contradiction. "MaxDD 3.8%,
 * cap 10%" matches how traders describe these values verbally.
 */
export function formatPercentMagnitude(value: number, decimals: number = 1): string {
  return `${(Math.abs(value) * 100).toFixed(decimals)}%`;
}

export function formatSharpe(value: number | null | undefined): string {
  if (value == null) return '-';
  return value.toFixed(2);
}

export function formatPrice(price: number | null | undefined, decimals: number = 5): string {
  if (price == null) return '-';
  return price.toFixed(decimals);
}

export function truncateJobId(id: string): string {
  // optjob_1234567890_abc123 -> optjob_...abc123
  if (id.length <= 16) return id;
  return `${id.slice(0, 7)}...${id.slice(-6)}`;
}

export function getStrategyDisplayName(strategy: StrategyType): string {
  const names: Record<StrategyType, string> = {
    ma: 'Moving Average Crossover',
    rsi: 'RSI Mean Reversion',
    donchian: 'Donchian Breakout',
  };
  return names[strategy] || strategy.toUpperCase();
}

export function getStatusColor(status: JobStatus): string {
  const colors: Record<JobStatus, string> = {
    queued: 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
    running: 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-400',
    completed: 'bg-green-100 text-green-700 dark:bg-green-900/50 dark:text-green-400',
    failed: 'bg-red-100 text-red-700 dark:bg-red-900/50 dark:text-red-400',
  };
  return colors[status];
}

export function formatPips(pips: number | null | undefined, showSign: boolean = true): string {
  if (pips == null) return '-';
  const formatted = pips.toFixed(1);
  if (showSign && pips > 0) return `+${formatted}`;
  return formatted;
}

export function formatDollars(dollars: number | null | undefined, showSign: boolean = true): string {
  if (dollars == null) return '-';
  const formatted = Math.abs(dollars).toFixed(2);
  if (dollars < 0) return `-$${formatted}`;
  if (showSign && dollars > 0) return `+$${formatted}`;
  return `$${formatted}`;
}

export function formatDuration(minutes: number | null | undefined): string {
  if (minutes == null) return '-';
  if (minutes < 60) {
    return `${Math.round(minutes)}m`;
  }
  const hours = Math.floor(minutes / 60);
  const mins = Math.round(minutes % 60);
  if (hours < 24) {
    return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const remainingHours = hours % 24;
  return remainingHours > 0 ? `${days}d ${remainingHours}h` : `${days}d`;
}

export function formatDateTime(isoString: string): string {
  return new Date(isoString).toLocaleString('en-US', {
    timeZone: 'UTC',
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
