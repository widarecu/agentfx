/**
 * Build chart-overlay indicator lines from a Strategy DSL spec.
 *
 * Walks the genome's DSL to find every distinct indicator call
 * (sma/ema/rsi/atr/bb_upper/bb_lower/donchian_high/donchian_low) and
 * computes its full series from the candle data — mirroring the Python
 * semantics in `src/agentfx/core/dsl/indicators.py` so the chart overlay
 * matches what the backtest engine evaluated.
 *
 * Used by the GenomeBacktest page; trials use a separate strategy-keyed
 * builder in optimizationApi.ts (the optimisation grammar is fixed to
 * MA/RSI/Donchian, while DSL specs can mix arbitrary indicators).
 */

import type {
  CandleData,
  IndicatorLine,
} from '../types/optimization';
import type {
  DslComparison,
  DslCondition,
  DslSpec,
} from '../types/evolution';

// ─────────────────────────────────────────────────────────────────────
// Walk the DSL to find unique indicator calls
// ─────────────────────────────────────────────────────────────────────

interface IndicatorCall {
  name: string;
  args: number[];
}

function isComparison(cond: DslCondition): cond is DslComparison {
  return typeof cond === 'object' && cond !== null && 'cmp' in cond;
}

function visitExpr(
  expr: unknown,
  out: Map<string, IndicatorCall>,
): void {
  if (expr == null || typeof expr !== 'object') return;
  const node = expr as { ind?: string; args?: number[] };
  if (typeof node.ind === 'string') {
    const args = Array.isArray(node.args) ? node.args.map(Number) : [];
    const key = `${node.ind}(${args.join(',')})`;
    if (!out.has(key)) out.set(key, { name: node.ind, args });
  }
}

function visitCondition(
  cond: DslCondition | null | undefined,
  out: Map<string, IndicatorCall>,
): void {
  if (!cond) return;
  if (isComparison(cond)) {
    visitExpr(cond.lhs, out);
    visitExpr(cond.rhs, out);
    return;
  }
  // Logical AND/OR
  if ('args' in cond && Array.isArray((cond as { args: unknown[] }).args)) {
    for (const sub of (cond as { args: DslCondition[] }).args) {
      visitCondition(sub, out);
    }
  }
}

function collectIndicatorCalls(spec: DslSpec): IndicatorCall[] {
  const map = new Map<string, IndicatorCall>();
  visitCondition(spec.entry?.long, map);
  visitCondition(spec.entry?.short, map);
  // Filter out the `close` pseudo-indicator — it's just the candle close,
  // not something we'd render as an overlay line.
  return Array.from(map.values()).filter((c) => c.name !== 'close');
}

// ─────────────────────────────────────────────────────────────────────
// Indicator computations (mirror Python semantics)
// ─────────────────────────────────────────────────────────────────────

type Series = { timestamp: string; value: number }[];

function sma(closes: number[], ts: string[], n: number): Series {
  // min_periods=1 — first bar is its own mean, then grows to a true SMA.
  const out: Series = [];
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    if (i >= n) sum -= closes[i - n];
    const denom = Math.min(i + 1, n);
    out.push({ timestamp: ts[i], value: sum / denom });
  }
  return out;
}

function ema(closes: number[], ts: string[], n: number): Series {
  // adjust=False, min_periods=1: EWM with alpha = 2/(n+1).
  const alpha = 2 / (n + 1);
  const out: Series = [];
  let prev = closes[0] ?? 0;
  for (let i = 0; i < closes.length; i++) {
    if (i === 0) {
      prev = closes[0];
    } else {
      prev = alpha * closes[i] + (1 - alpha) * prev;
    }
    out.push({ timestamp: ts[i], value: prev });
  }
  return out;
}

function rsiSeries(closes: number[], ts: string[], n: number): Series {
  // SMA-style RSI with min_periods=n.
  const out: Series = [];
  if (closes.length < n + 1) return out;
  const gains: number[] = [0];
  const losses: number[] = [0];
  for (let i = 1; i < closes.length; i++) {
    const delta = closes[i] - closes[i - 1];
    gains.push(Math.max(delta, 0));
    losses.push(Math.max(-delta, 0));
  }
  let sumGain = 0;
  let sumLoss = 0;
  for (let i = 1; i < closes.length; i++) {
    sumGain += gains[i];
    sumLoss += losses[i];
    if (i > n) {
      sumGain -= gains[i - n];
      sumLoss -= losses[i - n];
    }
    if (i >= n) {
      const avgGain = sumGain / n;
      const avgLoss = sumLoss / n;
      if (avgLoss === 0) {
        out.push({ timestamp: ts[i], value: 100 });
      } else {
        const rs = avgGain / avgLoss;
        out.push({ timestamp: ts[i], value: 100 - 100 / (1 + rs) });
      }
    }
  }
  return out;
}

function rollingMaxShifted(
  highs: number[],
  ts: string[],
  n: number,
): Series {
  // matches donchian_high: high.shift(1).rolling(n).max()
  const out: Series = [];
  // shift(1) means at bar i we look at bars [i-n .. i-1]. Need n complete prior bars.
  for (let i = n; i < highs.length; i++) {
    let max = -Infinity;
    for (let j = i - n; j < i; j++) {
      if (highs[j] > max) max = highs[j];
    }
    out.push({ timestamp: ts[i], value: max });
  }
  return out;
}

function rollingMinShifted(
  lows: number[],
  ts: string[],
  n: number,
): Series {
  const out: Series = [];
  for (let i = n; i < lows.length; i++) {
    let min = Infinity;
    for (let j = i - n; j < i; j++) {
      if (lows[j] < min) min = lows[j];
    }
    out.push({ timestamp: ts[i], value: min });
  }
  return out;
}

function atrSeries(candles: CandleData[], ts: string[], n: number): Series {
  // True range, then SMA(n) with min_periods=1
  const trs: number[] = [];
  for (let i = 0; i < candles.length; i++) {
    const high = candles[i].high;
    const low = candles[i].low;
    const prevClose = i > 0 ? candles[i - 1].close : candles[i].close;
    const tr = Math.max(
      high - low,
      Math.abs(high - prevClose),
      Math.abs(low - prevClose),
    );
    trs.push(tr);
  }
  return sma(trs, ts, n);
}

function bollinger(
  closes: number[],
  ts: string[],
  n: number,
  k: number,
  side: 'upper' | 'lower',
): Series {
  // SMA(n) +/- k * std(n) with ddof=0, min_periods=n
  const out: Series = [];
  if (closes.length < n) return out;
  let sum = 0;
  let sumSq = 0;
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i];
    sumSq += closes[i] * closes[i];
    if (i >= n) {
      sum -= closes[i - n];
      sumSq -= closes[i - n] * closes[i - n];
    }
    if (i >= n - 1) {
      const mean = sum / n;
      const variance = sumSq / n - mean * mean;
      const stdev = Math.sqrt(Math.max(variance, 0));
      const offset = k * stdev;
      out.push({
        timestamp: ts[i],
        value: side === 'upper' ? mean + offset : mean - offset,
      });
    }
  }
  return out;
}

// ─────────────────────────────────────────────────────────────────────
// Dispatch
// ─────────────────────────────────────────────────────────────────────

const COLORS = [
  '#3b82f6', // blue
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#10b981', // emerald
  '#ef4444', // red
];

function computeOne(
  call: IndicatorCall,
  candles: CandleData[],
  ts: string[],
  closes: number[],
  highs: number[],
  lows: number[],
): Series {
  const [a, b] = call.args;
  const n = Math.round(a ?? 14);
  switch (call.name) {
    case 'sma':
      return sma(closes, ts, n);
    case 'ema':
      return ema(closes, ts, n);
    case 'rsi':
      return rsiSeries(closes, ts, n);
    case 'atr':
      return atrSeries(candles, ts, n);
    case 'bb_upper':
      return bollinger(closes, ts, n, b ?? 2.0, 'upper');
    case 'bb_lower':
      return bollinger(closes, ts, n, b ?? 2.0, 'lower');
    case 'donchian_high':
      return rollingMaxShifted(highs, ts, n);
    case 'donchian_low':
      return rollingMinShifted(lows, ts, n);
    default:
      return [];
  }
}

function labelFor(call: IndicatorCall): string {
  return `${call.name}(${call.args.join(', ')})`;
}

/**
 * Build IndicatorLines for every unique indicator call in the DSL spec.
 * Returns an empty list if the spec has no entry conditions or the
 * candles array is empty.
 */
export function buildIndicatorsFromDsl(
  spec: DslSpec | null | undefined,
  candles: CandleData[],
): IndicatorLine[] {
  if (!spec || candles.length === 0) return [];
  const calls = collectIndicatorCalls(spec);
  if (calls.length === 0) return [];

  const ts = candles.map((c) => c.timestamp);
  const closes = candles.map((c) => c.close);
  const highs = candles.map((c) => c.high);
  const lows = candles.map((c) => c.low);

  const lines: IndicatorLine[] = [];
  calls.forEach((call, i) => {
    const data = computeOne(call, candles, ts, closes, highs, lows);
    if (data.length > 0) {
      lines.push({
        name: labelFor(call),
        color: COLORS[i % COLORS.length],
        data,
      });
    }
  });
  return lines;
}
