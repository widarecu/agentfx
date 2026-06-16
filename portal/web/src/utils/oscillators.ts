/**
 * Routing helpers for the DSL chart overlay.
 *
 * Some DSL indicators (rsi, stoch, adx) return values bounded to [0, 100]
 * and have to render on a secondary "oscillator" panel rather than on the
 * same price scale as the candles — otherwise their tiny dollar values
 * relative to the FX price (e.g. RSI of 67 vs EUR_USD of 1.0850) collapse
 * the candle pane to a flat strip.
 *
 * Must stay in sync with `is_oscillator()` in
 * src/agentfx/core/dsl/indicators.py — the JS computations in
 * dslIndicators.ts mirror the same Python semantics, so anything the
 * backend tags as an oscillator routes to the secondary panel here too.
 */

export const OSCILLATOR_NAMES = ['rsi', 'stoch', 'adx'] as const;

/**
 * Returns true if the indicator label (e.g. `"rsi(14)"`, `"stoch(14, 3)"`,
 * `"adx(14)"`) belongs to the oscillator family — used by StrategyChart
 * to split overlays from oscillator-panel series.
 *
 * `startsWith` is used rather than `includes` so e.g. an indicator
 * label that happened to contain "rsi" inside an argument list cannot
 * accidentally match.
 */
export function isOscillatorLabel(label: string): boolean {
  const lower = label.toLowerCase();
  return OSCILLATOR_NAMES.some((name) => lower.startsWith(name));
}
