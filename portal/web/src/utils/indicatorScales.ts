/**
 * Render-scale classification for DSL chart-overlay indicators.
 *
 * The chart has one shared time axis but three families of value axes,
 * and putting an indicator on the wrong axis is destructive to the
 * candle view. Specifically:
 *
 *   - Price-scale indicators (sma, ema, atr, bb_*, donchian_*,
 *     ichimoku_*) return values in the same units as the candle close
 *     — they overlay cleanly on the right (candle) price scale.
 *   - Bounded oscillators (rsi, stoch, adx) return values in [0, 100]
 *     — they go on the left scale at the bottom of the chart with the
 *     0/100 endpoints framing.
 *   - **Off-scale** indicators (macd_line, macd_signal, obv) live on
 *     completely different magnitudes:
 *       * MACD is the difference of two EMAs (typically ±0.001 for
 *         EUR_USD M1) — visible on the price scale but practically a
 *         flat line on a 1.08 axis.
 *       * OBV is cumulative tick volume — typically in the hundreds of
 *         thousands. Plotted on the price scale, the price axis
 *         auto-fits to span [0, 100000] and the candles collapse to a
 *         one-pixel strip at the bottom. This was reported on UAT
 *         2026-06-17 when the OBV_DIRECTIONAL_SEED genome's chart
 *         showed unreadable candles.
 *
 * Off-scale indicators each get their own dedicated overlay scale so
 * their auto-fit can't influence the candle axis.
 *
 * Must stay in sync with `is_oscillator()` in
 * src/agentfx/core/dsl/indicators.py — the JS computations in
 * dslIndicators.ts mirror the same Python semantics, so anything the
 * backend tags as an oscillator routes to the secondary panel here too.
 */

export const OSCILLATOR_NAMES = ['rsi', 'stoch', 'adx'] as const;

/**
 * Indicators that are NOT in price units AND NOT bounded oscillators —
 * each gets its own dedicated overlay scale at the bottom of the chart.
 *
 * MACD line + signal share a single `macd` scale because they're in
 * comparable units (both derived from EMA-of-close arithmetic) and a
 * MACD strategy typically wants to see line and signal together.
 *
 * OBV gets its own `obv` scale (cumulative tick volume, no comparable
 * partner).
 */
export const OFF_SCALE_INDICATORS = {
  macd_line: 'macd',
  macd_signal: 'macd',
  obv: 'obv',
} as const;

export type IndicatorScale = 'price' | 'oscillator' | 'off_scale';

/**
 * Returns the render-scale category for an indicator label like
 * `"rsi(14)"`, `"macd_line(12, 26)"`, `"obv"`, `"sma(20)"`.
 *
 * `startsWith` is used rather than `includes` so a label that happens
 * to contain "rsi" inside an argument list cannot accidentally match.
 */
export function classifyIndicator(label: string): IndicatorScale {
  const lower = label.toLowerCase();
  if (OSCILLATOR_NAMES.some((name) => lower.startsWith(name))) return 'oscillator';
  if (Object.keys(OFF_SCALE_INDICATORS).some((name) => lower.startsWith(name)))
    return 'off_scale';
  return 'price';
}

/**
 * Returns the dedicated lightweight-charts `priceScaleId` for an
 * off-scale indicator (e.g. `"macd"` for macd_line/macd_signal,
 * `"obv"` for obv). Returns null if the label is not off-scale.
 *
 * Indicators sharing a scale ID share an auto-fit range, so MACD line
 * + signal display together correctly. Different scale IDs auto-fit
 * independently so OBV's magnitude can't squash MACD or vice-versa.
 */
export function offScaleId(label: string): string | null {
  const lower = label.toLowerCase();
  for (const [name, scaleId] of Object.entries(OFF_SCALE_INDICATORS)) {
    if (lower.startsWith(name)) return scaleId;
  }
  return null;
}

/**
 * Back-compat shim — the old API exposed `isOscillatorLabel`. Kept so
 * existing call sites compile while StrategyChart migrates to
 * `classifyIndicator`.
 */
export function isOscillatorLabel(label: string): boolean {
  return classifyIndicator(label) === 'oscillator';
}
