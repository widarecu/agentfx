import { describe, expect, it } from 'vitest';

import { formatRiskPerTrade } from '../../src/utils/riskPerTrade';

describe('formatRiskPerTrade', () => {
  it.each([
    [0.0025, '0.25%'],
    [0.005, '0.5%'],
    [0.01, '1%'],
    [0.02, '2%'],
    [0.0024, '0.24%'],
  ])('displays %s as %s of balance', (riskPct, label) => {
    expect(formatRiskPerTrade(riskPct)).toBe(label);
  });

  it.each([null, undefined, NaN])('does not invent a value for %s', (riskPct) => {
    expect(formatRiskPerTrade(riskPct)).toBe('—');
  });
});
