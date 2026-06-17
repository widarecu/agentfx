/**
 * Render-scale classification for DSL chart-overlay indicators.
 *
 * The chart has three families of value axes (price / oscillator /
 * off-scale). Putting an indicator on the wrong axis is destructive:
 * an OBV genome on the price scale auto-fits the candle axis to span
 * cumulative-volume magnitudes and collapses the candles to a flat
 * strip — exactly the bug reported on UAT 2026-06-17.
 *
 * Must stay in sync with `is_oscillator()` in
 * src/agentfx/core/dsl/indicators.py.
 */

import { describe, expect, it } from 'vitest';

import {
  classifyIndicator,
  isOscillatorLabel,
  offScaleId,
  OFF_SCALE_INDICATORS,
  OSCILLATOR_NAMES,
} from '../../src/utils/indicatorScales';

describe('classifyIndicator', () => {
  it.each([
    ['rsi(14)'],
    ['rsi(7)'],
    ['stoch(14, 3)'],
    ['stoch(5, 1)'],
    ['adx(14)'],
    ['adx(7)'],
  ])('routes %s to the bounded-oscillator panel', (label) => {
    expect(classifyIndicator(label)).toBe('oscillator');
  });

  it.each([
    ['macd_line(12, 26)'],
    ['macd_signal(12, 26, 9)'],
    ['obv'],
  ])('routes %s to its own off-scale overlay', (label) => {
    expect(classifyIndicator(label)).toBe('off_scale');
  });

  it.each([
    ['sma(10)'],
    ['ema(26)'],
    ['atr(14)'],
    ['bb_upper(20, 2)'],
    ['bb_lower(20, 2)'],
    ['donchian_high(20)'],
    ['donchian_low(20)'],
    ['ichimoku_tenkan(9)'],
    ['ichimoku_kijun(26)'],
  ])('leaves %s on the candle price scale', (label) => {
    expect(classifyIndicator(label)).toBe('price');
  });

  it('is case-insensitive', () => {
    expect(classifyIndicator('RSI(14)')).toBe('oscillator');
    expect(classifyIndicator('Stoch(14, 3)')).toBe('oscillator');
    expect(classifyIndicator('OBV')).toBe('off_scale');
    expect(classifyIndicator('MACD_line(12, 26)')).toBe('off_scale');
  });
});

describe('offScaleId', () => {
  it('groups MACD line and signal on the same scale', () => {
    // MACD line and signal are in comparable units (signal is an EMA
    // of the line); they need to share an auto-fit range so the
    // signal cross is visible.
    expect(offScaleId('macd_line(12, 26)')).toBe('macd');
    expect(offScaleId('macd_signal(12, 26, 9)')).toBe('macd');
  });

  it('gives OBV its own dedicated scale', () => {
    // OBV is cumulative volume — orders of magnitude different from
    // MACD's price-delta scale. Sharing would defeat the purpose.
    expect(offScaleId('obv')).toBe('obv');
  });

  it('returns null for price-scale and oscillator indicators', () => {
    expect(offScaleId('sma(20)')).toBeNull();
    expect(offScaleId('rsi(14)')).toBeNull();
    expect(offScaleId('bb_upper(20, 2)')).toBeNull();
  });
});

describe('Source-of-truth registries', () => {
  it('exports the oscillator name list', () => {
    expect([...OSCILLATOR_NAMES].sort()).toEqual(['adx', 'rsi', 'stoch']);
  });

  it('exports the off-scale registry', () => {
    expect(OFF_SCALE_INDICATORS).toEqual({
      macd_line: 'macd',
      macd_signal: 'macd',
      obv: 'obv',
    });
  });
});

describe('isOscillatorLabel (back-compat shim)', () => {
  it('still returns true only for bounded oscillators', () => {
    expect(isOscillatorLabel('rsi(14)')).toBe(true);
    expect(isOscillatorLabel('stoch(14, 3)')).toBe(true);
    expect(isOscillatorLabel('adx(14)')).toBe(true);
    // Off-scale indicators must NOT be classified as oscillators —
    // they live on their own scale, not the [0, 100] panel.
    expect(isOscillatorLabel('obv')).toBe(false);
    expect(isOscillatorLabel('macd_line(12, 26)')).toBe(false);
  });
});
