/**
 * Oscillator-routing utility — controls which DSL indicator series are
 * rendered on the bounded [0, 100] secondary panel instead of the candle
 * price scale. The chart uses this to split overlay vs oscillator
 * indicators; getting it wrong means a stoch(14, 3) genome silently
 * tries to draw "67.4" on a EUR_USD price axis around 1.0850 and
 * collapses the candle pane to a flat strip.
 *
 * Pairs with the Python `is_oscillator()` in
 * src/agentfx/core/dsl/indicators.py — any change there must come with a
 * matching update here and a refreshed parity fixture.
 */

import { describe, expect, it } from 'vitest';

import { isOscillatorLabel, OSCILLATOR_NAMES } from '../../src/utils/oscillators';

describe('isOscillatorLabel', () => {
  it.each([
    ['rsi(14)'],
    ['rsi(7)'],
    ['stoch(14, 3)'],
    ['stoch(5, 1)'],
    ['adx(14)'],
    ['adx(7)'],
  ])('routes %s to the oscillator panel', (label) => {
    expect(isOscillatorLabel(label)).toBe(true);
  });

  it.each([
    ['sma(10)'],
    ['ema(26)'],
    ['atr(14)'],
    ['bb_upper(20, 2)'],
    ['bb_lower(20, 2)'],
    ['donchian_high(20)'],
    ['donchian_low(20)'],
    ['macd_line(12, 26)'],
    ['macd_signal(12, 26, 9)'],
    ['obv'],
    ['ichimoku_tenkan(9)'],
    ['ichimoku_kijun(26)'],
  ])('leaves %s on the price-scale overlay panel', (label) => {
    expect(isOscillatorLabel(label)).toBe(false);
  });

  it('is case-insensitive on the indicator name', () => {
    expect(isOscillatorLabel('RSI(14)')).toBe(true);
    expect(isOscillatorLabel('Stoch(14, 3)')).toBe(true);
  });

  it('exports the source-of-truth name list', () => {
    // Asserts the constant the test relies on hasn't silently drifted —
    // adding a new oscillator family means updating both Python's
    // is_oscillator() and this list, and the test catches the second
    // half.
    expect([...OSCILLATOR_NAMES].sort()).toEqual(['adx', 'rsi', 'stoch']);
  });
});
