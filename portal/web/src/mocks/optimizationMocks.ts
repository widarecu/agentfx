/**
 * Mock data for Optimization UI (v0.2.0)
 * This provides realistic sample data for developing the UI before backend integration.
 */

import {
  OptimizationJob,
  OptimizationTrial,
  CandleData,
  TradeSignal,
  IndicatorLine,
  TrialDetailData,
  Trade,
  TrialMetricsExtended,
  evaluateGateB,
} from '../types/optimization';

// ============================================================================
// OPTIMIZATION JOBS
// ============================================================================

export const mockOptimizationJobs: OptimizationJob[] = [
  {
    id: 'optjob_1702900800_a1b2c3',
    strategy: 'ma',
    pair: 'EUR_USD',
    status: 'completed',
    progress: 100,
    estimatedIterations: 55,
    completedIterations: 55,
    bestSharpe: 1.42,
    createdAt: '2024-12-18T10:00:00Z',
    completedAt: '2024-12-18T12:30:00Z',
    paramRanges: {
      fast: { min: 5, max: 30 },
      slow: { min: 20, max: 100 },
    },
    dataRangeStart: '2024-01-01T00:00:00Z',
    dataRangeEnd: '2024-12-15T00:00:00Z',
    accountBalance: 10000,
    positionSize: 10000,
    dataYears: 5,
    timeframe: 'H1',
  },
  {
    id: 'optjob_1702987200_d4e5f6',
    strategy: 'rsi',
    pair: 'GBP_USD',
    status: 'running',
    progress: 68,
    estimatedIterations: 40,
    completedIterations: 27,
    bestSharpe: 1.15,
    createdAt: '2024-12-19T10:00:00Z',
    completedAt: null,
    paramRanges: {
      period: { min: 7, max: 21 },
      oversold: { min: 20, max: 35 },
      overbought: { min: 65, max: 80 },
    },
    dataRangeStart: '2024-06-01T00:00:00Z',
    dataRangeEnd: '2024-12-15T00:00:00Z',
    accountBalance: 25000,
    positionSize: 10000,
    dataYears: 3,
    timeframe: 'M30',
  },
  {
    id: 'optjob_1703073600_g7h8i9',
    strategy: 'donchian',
    pair: 'USD_JPY',
    status: 'queued',
    progress: 0,
    estimatedIterations: 30,
    completedIterations: 0,
    bestSharpe: null,
    createdAt: '2024-12-20T10:00:00Z',
    completedAt: null,
    paramRanges: {
      period: { min: 10, max: 50 },
      atrMultiple: { min: 1.0, max: 3.0 },
    },
    dataRangeStart: '2024-03-01T00:00:00Z',
    dataRangeEnd: '2024-12-15T00:00:00Z',
    accountBalance: 50000,
    positionSize: 100000,
    dataYears: 2,
    timeframe: 'H4',
  },
  {
    id: 'optjob_1702814400_j1k2l3',
    strategy: 'ma',
    pair: 'AUD_USD',
    status: 'failed',
    progress: 23,
    estimatedIterations: 45,
    completedIterations: 10,
    bestSharpe: 0.45,
    createdAt: '2024-12-17T10:00:00Z',
    completedAt: '2024-12-17T11:15:00Z',
    paramRanges: {
      fast: { min: 3, max: 20 },
      slow: { min: 15, max: 80 },
    },
    dataRangeStart: '2024-01-01T00:00:00Z',
    dataRangeEnd: '2024-12-15T00:00:00Z',
    accountBalance: 10000,
    positionSize: 1000,
    dataYears: 5,
    timeframe: 'H1',
  },
];

// ============================================================================
// OPTIMIZATION TRIALS (for completed job)
// ============================================================================

function generateTrials(jobId: string, count: number): OptimizationTrial[] {
  const trials: OptimizationTrial[] = [];

  for (let i = 0; i < count; i++) {
    const fast = 5 + Math.floor(Math.random() * 25);
    const slow = Math.max(fast + 10, 20 + Math.floor(Math.random() * 80));

    // Generate realistic metrics with some correlation
    const baseSharpe = 0.3 + Math.random() * 1.5;
    const sharpe = Number(baseSharpe.toFixed(2));
    const maxDrawdown = Number((0.03 + Math.random() * 0.12).toFixed(3));
    const hitRate = Number((0.42 + Math.random() * 0.18).toFixed(3));
    const tradeCount = 250 + Math.floor(Math.random() * 200);
    const totalReturn = Number((sharpe * 0.08 + (Math.random() - 0.5) * 0.1).toFixed(3));
    const profitFactor = Number((1.0 + sharpe * 0.5 + (Math.random() - 0.5) * 0.3).toFixed(2));

    const metrics = {
      sharpe,
      maxDrawdown,
      hitRate,
      tradeCount,
      totalReturn,
      profitFactor,
      avgWin: Number((0.002 + Math.random() * 0.003).toFixed(5)),
      avgLoss: Number((0.001 + Math.random() * 0.002).toFixed(5)),
      maxConsecutiveLosses: 3 + Math.floor(Math.random() * 8),
      avgTradeDuration: Number((2 + Math.random() * 10).toFixed(1)),
    };

    const gateB = evaluateGateB(metrics);

    trials.push({
      id: `trial_${jobId}_${String(i).padStart(3, '0')}`,
      jobId,
      params: { fast, slow },
      metrics,
      passesGateB: gateB.passes,
      gateBFailures: gateB.failures,
      createdAt: new Date(Date.now() - (count - i) * 120000).toISOString(),
    });
  }

  // Sort by sharpe descending
  trials.sort((a, b) => b.metrics.sharpe - a.metrics.sharpe);

  return trials;
}

// Generate 55 trials for the completed job
export const mockTrials: OptimizationTrial[] = generateTrials('optjob_1702900800_a1b2c3', 55);

// Ensure the top 3 have good metrics (for demo purposes)
mockTrials[0].metrics = {
  sharpe: 1.42,
  maxDrawdown: 0.068,
  hitRate: 0.54,
  tradeCount: 412,
  totalReturn: 0.187,
  profitFactor: 1.78,
  avgWin: 0.0035,
  avgLoss: 0.0018,
  maxConsecutiveLosses: 5,
  avgTradeDuration: 4.2,
};
mockTrials[0].params = { fast: 12, slow: 48 };
mockTrials[0].passesGateB = true;
mockTrials[0].gateBFailures = [];

mockTrials[1].metrics = {
  sharpe: 1.31,
  maxDrawdown: 0.072,
  hitRate: 0.52,
  tradeCount: 385,
  totalReturn: 0.156,
  profitFactor: 1.65,
  avgWin: 0.0032,
  avgLoss: 0.0019,
  maxConsecutiveLosses: 6,
  avgTradeDuration: 5.1,
};
mockTrials[1].params = { fast: 10, slow: 45 };
mockTrials[1].passesGateB = true;
mockTrials[1].gateBFailures = [];

mockTrials[2].metrics = {
  sharpe: 1.18,
  maxDrawdown: 0.085,
  hitRate: 0.51,
  tradeCount: 356,
  totalReturn: 0.134,
  profitFactor: 1.52,
  avgWin: 0.0028,
  avgLoss: 0.0017,
  maxConsecutiveLosses: 7,
  avgTradeDuration: 6.3,
};
mockTrials[2].params = { fast: 15, slow: 52 };
mockTrials[2].passesGateB = true;
mockTrials[2].gateBFailures = [];

// ============================================================================
// CANDLESTICK DATA (for chart visualization)
// ============================================================================

function generateCandleData(count: number, startPrice: number = 1.0850): CandleData[] {
  const candles: CandleData[] = [];
  let currentPrice = startPrice;
  const startTime = new Date('2024-12-01T00:00:00Z').getTime();
  const intervalMs = 5 * 60 * 1000; // 5 minute candles

  for (let i = 0; i < count; i++) {
    const volatility = 0.0003 + Math.random() * 0.0005;
    const drift = (Math.random() - 0.48) * volatility * 2;

    const open = currentPrice;
    const close = open + drift;
    const highExtra = Math.random() * volatility;
    const lowExtra = Math.random() * volatility;
    const high = Math.max(open, close) + highExtra;
    const low = Math.min(open, close) - lowExtra;

    candles.push({
      timestamp: new Date(startTime + i * intervalMs).toISOString(),
      open: Number(open.toFixed(5)),
      high: Number(high.toFixed(5)),
      low: Number(low.toFixed(5)),
      close: Number(close.toFixed(5)),
      volume: Math.floor(1000 + Math.random() * 5000),
    });

    currentPrice = close;
  }

  return candles;
}

export const mockCandleData: CandleData[] = generateCandleData(200);

// ============================================================================
// TRADE SIGNALS (for chart markers)
// ============================================================================

function generateTradeSignals(candles: CandleData[]): TradeSignal[] {
  const signals: TradeSignal[] = [];
  let inPosition: 'long' | 'short' | null = null;
  let entryPrice = 0;

  // Simple MA crossover simulation
  const fastPeriod = 12;
  const slowPeriod = 48;

  const calculateMA = (data: number[], period: number, endIndex: number): number => {
    if (endIndex < period - 1) return data[endIndex];
    let sum = 0;
    for (let i = endIndex - period + 1; i <= endIndex; i++) {
      sum += data[i];
    }
    return sum / period;
  };

  const closes = candles.map(c => c.close);

  for (let i = slowPeriod; i < candles.length; i++) {
    const fastMA = calculateMA(closes, fastPeriod, i);
    const slowMA = calculateMA(closes, slowPeriod, i);
    const prevFastMA = calculateMA(closes, fastPeriod, i - 1);
    const prevSlowMA = calculateMA(closes, slowPeriod, i - 1);

    const candle = candles[i];
    const indicatorValues = {
      fast_ma: Number(fastMA.toFixed(5)),
      slow_ma: Number(slowMA.toFixed(5)),
    };

    // Long entry: fast crosses above slow
    if (!inPosition && prevFastMA <= prevSlowMA && fastMA > slowMA) {
      signals.push({
        timestamp: candle.timestamp,
        signal: 1,
        signalType: 'entry_long',
        price: candle.close,
        indicatorValues,
      });
      inPosition = 'long';
      entryPrice = candle.close;
    }
    // Long exit: fast crosses below slow
    else if (inPosition === 'long' && prevFastMA >= prevSlowMA && fastMA < slowMA) {
      const pnl = candle.close - entryPrice;
      signals.push({
        timestamp: candle.timestamp,
        signal: 0,
        signalType: 'exit_long',
        price: candle.close,
        pnl: Number(pnl.toFixed(5)),
        indicatorValues,
      });
      inPosition = null;
    }
    // Short entry: fast crosses below slow
    else if (!inPosition && prevFastMA >= prevSlowMA && fastMA < slowMA) {
      signals.push({
        timestamp: candle.timestamp,
        signal: -1,
        signalType: 'entry_short',
        price: candle.close,
        indicatorValues,
      });
      inPosition = 'short';
      entryPrice = candle.close;
    }
    // Short exit: fast crosses above slow
    else if (inPosition === 'short' && prevFastMA <= prevSlowMA && fastMA > slowMA) {
      const pnl = entryPrice - candle.close;
      signals.push({
        timestamp: candle.timestamp,
        signal: 0,
        signalType: 'exit_short',
        price: candle.close,
        pnl: Number(pnl.toFixed(5)),
        indicatorValues,
      });
      inPosition = null;
    }
  }

  return signals;
}

export const mockTradeSignals: TradeSignal[] = generateTradeSignals(mockCandleData);

// ============================================================================
// INDICATOR LINES (for chart overlay)
// ============================================================================

function generateIndicatorLines(candles: CandleData[]): IndicatorLine[] {
  const fastPeriod = 12;
  const slowPeriod = 48;
  const closes = candles.map(c => c.close);

  const calculateMA = (data: number[], period: number, endIndex: number): number => {
    if (endIndex < period - 1) return data[endIndex];
    let sum = 0;
    for (let i = endIndex - period + 1; i <= endIndex; i++) {
      sum += data[i];
    }
    return sum / period;
  };

  const fastData: { timestamp: string; value: number }[] = [];
  const slowData: { timestamp: string; value: number }[] = [];

  for (let i = 0; i < candles.length; i++) {
    if (i >= fastPeriod - 1) {
      fastData.push({
        timestamp: candles[i].timestamp,
        value: Number(calculateMA(closes, fastPeriod, i).toFixed(5)),
      });
    }
    if (i >= slowPeriod - 1) {
      slowData.push({
        timestamp: candles[i].timestamp,
        value: Number(calculateMA(closes, slowPeriod, i).toFixed(5)),
      });
    }
  }

  return [
    { name: 'Fast MA (12)', color: '#3b82f6', data: fastData },
    { name: 'Slow MA (48)', color: '#f59e0b', data: slowData },
  ];
}

export const mockIndicatorLines: IndicatorLine[] = generateIndicatorLines(mockCandleData);

// ============================================================================
// INDIVIDUAL TRADES (derived from signals with financing)
// ============================================================================

// Configuration for financing calculations
const FINANCING_CONFIG = {
  spreadPips: 0.8, // Average spread for EUR/USD in pips
  swapLongPerDay: -0.5, // Daily swap rate for long positions (in pips)
  swapShortPerDay: 0.3, // Daily swap rate for short positions (in pips)
  pipValuePerLot: 10, // Dollar value per pip per standard lot (100,000 units)
  defaultPositionSize: 10000, // 0.1 lot (mini lot)
};

function generateTrades(signals: TradeSignal[], trialId: string): Trade[] {
  const trades: Trade[] = [];
  let entrySignal: TradeSignal | null = null;
  let tradeIndex = 0;

  for (const signal of signals) {
    if (signal.signalType === 'entry_long' || signal.signalType === 'entry_short') {
      entrySignal = signal;
    } else if (
      entrySignal &&
      (signal.signalType === 'exit_long' || signal.signalType === 'exit_short')
    ) {
      const direction = entrySignal.signalType === 'entry_long' ? 'long' : 'short';
      const entryTime = new Date(entrySignal.timestamp).getTime();
      const exitTime = new Date(signal.timestamp).getTime();
      const durationMinutes = (exitTime - entryTime) / (1000 * 60);
      const daysHeld = durationMinutes / (60 * 24);

      // Calculate pips (1 pip = 0.0001 for EUR/USD)
      const priceDiff = signal.price - entrySignal.price;
      const pips = direction === 'long' ? priceDiff * 10000 : -priceDiff * 10000;

      // Calculate financing costs
      const spreadCostPips = FINANCING_CONFIG.spreadPips;
      const swapRate =
        direction === 'long'
          ? FINANCING_CONFIG.swapLongPerDay
          : FINANCING_CONFIG.swapShortPerDay;
      const swapCostPips = Math.max(0, Math.floor(daysHeld)) * Math.abs(swapRate);

      // Net pips after costs
      const netPips = pips - spreadCostPips - swapCostPips;

      // Dollar calculations (based on position size)
      const positionSize = FINANCING_CONFIG.defaultPositionSize;
      const lotSize = positionSize / 100000;
      const pipValue = FINANCING_CONFIG.pipValuePerLot * lotSize;

      const grossPnlDollars = pips * pipValue;
      const spreadCostDollars = spreadCostPips * pipValue;
      const swapCostDollars = swapCostPips * pipValue;
      const netPnlDollars = grossPnlDollars - spreadCostDollars - swapCostDollars;

      trades.push({
        id: `trade_${trialId}_${String(tradeIndex).padStart(3, '0')}`,
        trialId,
        direction,
        entryTimestamp: entrySignal.timestamp,
        exitTimestamp: signal.timestamp,
        entryPrice: entrySignal.price,
        exitPrice: signal.price,
        pips: Number(pips.toFixed(1)),
        spreadCostPips: Number(spreadCostPips.toFixed(1)),
        swapCostPips: Number(swapCostPips.toFixed(1)),
        netPips: Number(netPips.toFixed(1)),
        grossPnlDollars: Number(grossPnlDollars.toFixed(2)),
        spreadCostDollars: Number(spreadCostDollars.toFixed(2)),
        swapCostDollars: Number(swapCostDollars.toFixed(2)),
        netPnlDollars: Number(netPnlDollars.toFixed(2)),
        durationMinutes: Math.round(durationMinutes),
        positionSize,
      });

      tradeIndex++;
      entrySignal = null;
    }
  }

  return trades;
}

export const mockTrades: Trade[] = generateTrades(mockTradeSignals, 'trial_optjob_1702900800_a1b2c3_000');

// ============================================================================
// EXTENDED METRICS (with financing breakdown)
// ============================================================================

function calculateExtendedMetrics(trades: Trade[], baseMetrics: typeof mockTrials[0]['metrics']): TrialMetricsExtended {
  const totalPips = trades.reduce((sum, t) => sum + t.pips, 0);
  const totalSpreadCostPips = trades.reduce((sum, t) => sum + t.spreadCostPips, 0);
  const totalSwapCostPips = trades.reduce((sum, t) => sum + t.swapCostPips, 0);
  const totalCostPips = totalSpreadCostPips + totalSwapCostPips;
  const netPips = totalPips - totalCostPips;

  const grossPnlDollars = trades.reduce((sum, t) => sum + t.grossPnlDollars, 0);
  const totalSpreadCostDollars = trades.reduce((sum, t) => sum + t.spreadCostDollars, 0);
  const totalSwapCostDollars = trades.reduce((sum, t) => sum + t.swapCostDollars, 0);
  const netPnlDollars = grossPnlDollars - totalSpreadCostDollars - totalSwapCostDollars;

  const tradeCount = trades.length || 1;

  return {
    ...baseMetrics,
    totalPips: Number(totalPips.toFixed(1)),
    avgPipsPerTrade: Number((totalPips / tradeCount).toFixed(1)),
    totalSpreadCostPips: Number(totalSpreadCostPips.toFixed(1)),
    totalSwapCostPips: Number(totalSwapCostPips.toFixed(1)),
    totalCostPips: Number(totalCostPips.toFixed(1)),
    netPips: Number(netPips.toFixed(1)),
    avgNetPipsPerTrade: Number((netPips / tradeCount).toFixed(1)),
    grossPnlDollars: Number(grossPnlDollars.toFixed(2)),
    totalSpreadCostDollars: Number(totalSpreadCostDollars.toFixed(2)),
    totalSwapCostDollars: Number(totalSwapCostDollars.toFixed(2)),
    netPnlDollars: Number(netPnlDollars.toFixed(2)),
  };
}

export const mockExtendedMetrics: TrialMetricsExtended = calculateExtendedMetrics(mockTrades, mockTrials[0].metrics);

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

export function getJobById(jobId: string): OptimizationJob | undefined {
  return mockOptimizationJobs.find(job => job.id === jobId);
}

export function getTrialsByJobId(jobId: string): OptimizationTrial[] {
  return mockTrials.filter(trial => trial.jobId === jobId);
}

export function getTrialById(trialId: string): OptimizationTrial | undefined {
  return mockTrials.find(trial => trial.id === trialId);
}

export function getTop3Trials(jobId: string): OptimizationTrial[] {
  return getTrialsByJobId(jobId)
    .sort((a, b) => b.metrics.sharpe - a.metrics.sharpe)
    .slice(0, 3);
}

export function getTrialDetailData(jobId: string, trialId: string): TrialDetailData | null {
  const trial = getTrialById(trialId);
  const job = getJobById(jobId);

  if (!trial || !job || trial.jobId !== jobId) {
    return null;
  }

  // Generate trades specific to this trial
  const trades = generateTrades(mockTradeSignals, trialId);
  const extendedMetrics = calculateExtendedMetrics(trades, trial.metrics);

  return {
    trial,
    job,
    candles: mockCandleData,
    signals: mockTradeSignals,
    indicators: mockIndicatorLines,
    trades,
    extendedMetrics,
  };
}
