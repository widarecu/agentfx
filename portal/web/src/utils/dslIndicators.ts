/**
 * Build chart-overlay indicator lines from a Strategy DSL spec.
 *
 * Walks the genome's DSL to find every distinct indicator call and
 * computes its full series from the candle data — mirroring the Python
 * semantics in `src/agentfx/core/dsl/indicators.py` so the chart overlay
 * matches what the backtest engine evaluated.
 *
 * Supported indicators (must be kept in sync with the Python catalog):
 *   - Price-scale (drawn on main panel): sma, ema, atr, bb_upper, bb_lower,
 *     donchian_high, donchian_low, macd_line, macd_signal, ichimoku_tenkan,
 *     ichimoku_kijun, obv.
 *   - Oscillator-scale (drawn on the [0,100] secondary panel — see
 *     `StrategyChart.isOscillator`): rsi, stoch, adx.
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
  // SMA-style RSI with pandas semantics:
  //   delta = close.diff()                           # NaN at i=0
  //   gain  = delta.where(delta > 0, 0.0)            # gain[0] = 0
  //   loss  = -delta.where(delta < 0, 0.0)           # loss[0] = 0
  //   avg_gain = gain.rolling(n, min_periods=n).mean()
  //   avg_loss = loss.rolling(n, min_periods=n).mean()
  //   rs = avg_gain / avg_loss.replace(0, NaN)       # NaN when no losses
  //   rsi = 100 - 100 / (1 + rs)
  //
  // The first valid output is at i = n-1: pandas's rolling window then
  // covers indices [0..n-1] (n bars). gain[0]=0 / loss[0]=0 are non-NaN
  // so they count toward min_periods=n. Numerically equivalent to summing
  // gains[1..n-1] / divided by n.
  const out: Series = [];
  if (closes.length < n) return out;
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
    if (i >= n - 1) {
      const avgGain = sumGain / n;
      const avgLoss = sumLoss / n;
      // pandas replaces 0 avg_loss with NaN → RSI is NaN there. We skip
      // the bar so the output series matches what the fixture sees
      // (NaN-dropped) and what the chart should plot (a gap, not a 100
      // spike that pretends the indicator initialised cleanly).
      if (avgLoss === 0) continue;
      const rs = avgGain / avgLoss;
      out.push({ timestamp: ts[i], value: 100 - 100 / (1 + rs) });
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
// v0.4.0 indicator expansion — JS mirrors of Python implementations
// ─────────────────────────────────────────────────────────────────────

/** EMA computed as a raw value array (no timestamps) — used as a helper
 * by MACD which needs to chain EMAs. Matches the ewm(span=n, adjust=False,
 * min_periods=1) semantics used by Python's `ema()`. */
function emaValues(values: number[], n: number): number[] {
  if (values.length === 0) return [];
  const alpha = 2 / (n + 1);
  const out: number[] = new Array(values.length);
  out[0] = values[0];
  for (let i = 1; i < values.length; i++) {
    out[i] = alpha * values[i] + (1 - alpha) * out[i - 1];
  }
  return out;
}

function macdLine(
  closes: number[],
  ts: string[],
  nFast: number,
  nSlow: number,
): Series {
  const fast = emaValues(closes, nFast);
  const slow = emaValues(closes, nSlow);
  return closes.map((_, i) => ({ timestamp: ts[i], value: fast[i] - slow[i] }));
}

function macdSignal(
  closes: number[],
  ts: string[],
  nFast: number,
  nSlow: number,
  nSignal: number,
): Series {
  const fast = emaValues(closes, nFast);
  const slow = emaValues(closes, nSlow);
  const line = closes.map((_, i) => fast[i] - slow[i]);
  const signal = emaValues(line, nSignal);
  return signal.map((v, i) => ({ timestamp: ts[i], value: v }));
}

function stochSeries(
  highs: number[],
  lows: number[],
  closes: number[],
  ts: string[],
  n: number,
  kSmooth: number,
): Series {
  // %K = 100 * (close - lowest_low(n)) / (highest_high(n) - lowest_low(n)),
  // then SMA-smoothed over kSmooth bars. min_periods=n for the rolling
  // max/min step; min_periods=1 on the smoothing step (mirrors pandas
  // `.rolling(k_smooth, min_periods=1).mean()` which averages over only
  // the non-NaN values inside each window).
  const len = closes.length;
  const rawK: number[] = new Array(len).fill(NaN);
  for (let i = n - 1; i < len; i++) {
    let lo = Infinity;
    let hi = -Infinity;
    for (let j = i - n + 1; j <= i; j++) {
      if (lows[j] < lo) lo = lows[j];
      if (highs[j] > hi) hi = highs[j];
    }
    const range = hi - lo;
    rawK[i] = range === 0 ? NaN : 100 * (closes[i] - lo) / range;
  }
  const out: Series = [];
  for (let i = 0; i < len; i++) {
    const start = Math.max(0, i - kSmooth + 1);
    let sum = 0;
    let count = 0;
    for (let j = start; j <= i; j++) {
      if (!Number.isNaN(rawK[j])) {
        sum += rawK[j];
        count++;
      }
    }
    if (count > 0) {
      out.push({ timestamp: ts[i], value: sum / count });
    }
  }
  return out;
}

function adxSeries(
  highs: number[],
  lows: number[],
  closes: number[],
  ts: string[],
  n: number,
): Series {
  // Wilder smoothing with alpha=1/n. Matches Python ewm(alpha=alpha,
  // adjust=False, min_periods=1).
  //
  // Boundary handling: at i=0, plus_dm[0]=minus_dm[0]=0 (no prior bar to
  // diff against), so plus_di[0] = minus_di[0] = 0, di_sum = 0. Pandas
  // replaces the zero divisor with NaN, so DX[0] is NaN; the ewm of DX
  // then propagates that NaN and re-initialises at the first non-NaN
  // value (i=1). Output bar 0 must be dropped to match this.
  const alpha = 1 / n;
  const len = closes.length;
  if (len < 2) return [];

  // True range
  const tr: number[] = new Array(len);
  tr[0] = highs[0] - lows[0];
  // Directional movement
  const plusDm: number[] = new Array(len).fill(0);
  const minusDm: number[] = new Array(len).fill(0);
  for (let i = 1; i < len; i++) {
    const prevClose = closes[i - 1];
    tr[i] = Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - prevClose),
      Math.abs(lows[i] - prevClose),
    );
    const upMove = highs[i] - highs[i - 1];
    const downMove = lows[i - 1] - lows[i];
    if (upMove > downMove && upMove > 0) plusDm[i] = upMove;
    if (downMove > upMove && downMove > 0) minusDm[i] = downMove;
  }

  // Wilder smooth — no NaN in any input, so the simple recurrence is fine.
  const wilderSmooth = (vals: number[]): number[] => {
    const out: number[] = new Array(vals.length);
    out[0] = vals[0];
    for (let i = 1; i < vals.length; i++) {
      out[i] = alpha * vals[i] + (1 - alpha) * out[i - 1];
    }
    return out;
  };

  const trSm = wilderSmooth(tr);
  const plusDmSm = wilderSmooth(plusDm);
  const minusDmSm = wilderSmooth(minusDm);

  // DX with NaN sentinel where Python would yield NaN (di_sum == 0).
  const dx: number[] = new Array(len).fill(NaN);
  for (let i = 0; i < len; i++) {
    if (trSm[i] === 0) continue;
    const plusDi = (100 * plusDmSm[i]) / trSm[i];
    const minusDi = (100 * minusDmSm[i]) / trSm[i];
    const sum = plusDi + minusDi;
    if (sum === 0) continue;
    dx[i] = (100 * Math.abs(plusDi - minusDi)) / sum;
  }
  // Final EWM of DX, mirroring pandas's NaN handling: stay NaN until the
  // first valid input value, then initialise the recurrence at that value.
  const adxArr: number[] = new Array(len).fill(NaN);
  let prev = NaN;
  let started = false;
  for (let i = 0; i < len; i++) {
    if (Number.isNaN(dx[i])) continue;
    if (!started) {
      adxArr[i] = dx[i];
      prev = dx[i];
      started = true;
    } else {
      adxArr[i] = alpha * dx[i] + (1 - alpha) * prev;
      prev = adxArr[i];
    }
  }
  const out: Series = [];
  for (let i = 0; i < len; i++) {
    if (!Number.isNaN(adxArr[i])) {
      out.push({ timestamp: ts[i], value: adxArr[i] });
    }
  }
  return out;
}

function obvSeries(closes: number[], volumes: number[], ts: string[]): Series {
  const out: Series = [];
  let cum = 0;
  for (let i = 0; i < closes.length; i++) {
    if (i > 0) {
      if (closes[i] > closes[i - 1]) cum += volumes[i] ?? 0;
      else if (closes[i] < closes[i - 1]) cum -= volumes[i] ?? 0;
    }
    out.push({ timestamp: ts[i], value: cum });
  }
  return out;
}

function ichimokuMidpoint(
  highs: number[],
  lows: number[],
  ts: string[],
  n: number,
): Series {
  // (highest_high(n) + lowest_low(n)) / 2, min_periods=n.
  const out: Series = [];
  for (let i = n - 1; i < highs.length; i++) {
    let lo = Infinity;
    let hi = -Infinity;
    for (let j = i - n + 1; j <= i; j++) {
      if (lows[j] < lo) lo = lows[j];
      if (highs[j] > hi) hi = highs[j];
    }
    out.push({ timestamp: ts[i], value: (hi + lo) / 2 });
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
  volumes: number[],
): Series {
  const [a, b, c] = call.args;
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
    case 'macd_line':
      // args: (n_fast, n_slow) — defaults track Python's typical 12/26.
      return macdLine(closes, ts, Math.round(a ?? 12), Math.round(b ?? 26));
    case 'macd_signal':
      // args: (n_fast, n_slow, n_signal) — defaults 12/26/9.
      return macdSignal(
        closes,
        ts,
        Math.round(a ?? 12),
        Math.round(b ?? 26),
        Math.round(c ?? 9),
      );
    case 'stoch':
      // args: (n, k_smooth) — defaults 14/3.
      return stochSeries(highs, lows, closes, ts, n, Math.round(b ?? 3));
    case 'adx':
      return adxSeries(highs, lows, closes, ts, n);
    case 'obv':
      return obvSeries(closes, volumes, ts);
    case 'ichimoku_tenkan':
      return ichimokuMidpoint(highs, lows, ts, n);
    case 'ichimoku_kijun':
      return ichimokuMidpoint(highs, lows, ts, n);
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
  // OBV is the only volume-aware indicator; absent volume → treated as 0.
  const volumes = candles.map((c) => c.volume ?? 0);

  const lines: IndicatorLine[] = [];
  calls.forEach((call, i) => {
    const data = computeOne(call, candles, ts, closes, highs, lows, volumes);
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
