/**
 * Vitest for `_buildRiskConfig` in DeployPaperModal (US-028).
 *
 * The critical invariant: empty SL/TP inputs must be OMITTED from
 * the payload — not sent as null/0/etc. — so the backend treats them
 * as "no broker-side SL/TP registered" and the strategy exits on
 * signal reversal (same as backtest). If we shipped `stop_loss_pips:
 * 0` the runner would try to place a zero-pip SL and the OANDA API
 * would reject the order.
 */

import { describe, expect, it } from 'vitest';
import { _buildRiskConfig } from '../../src/components/DeployPaperModal';

describe('_buildRiskConfig', () => {
  const baseline = {
    maxPositionSize: '10000',
    dailyLossPct: '2.0',
    killDdPct: '5.0',
    stopLossPips: '',
    takeProfitPips: '',
  };

  it('omits SL/TP when both fields are empty', () => {
    const cfg = _buildRiskConfig(baseline);
    expect(cfg).toEqual({
      max_position_size_units: 10000,
      daily_loss_pct: 2,
      kill_switch_dd_pct: 5,
    });
    expect(cfg).not.toHaveProperty('stop_loss_pips');
    expect(cfg).not.toHaveProperty('take_profit_pips');
  });

  it('includes SL only when SL is set', () => {
    const cfg = _buildRiskConfig({ ...baseline, stopLossPips: '20' });
    expect(cfg.stop_loss_pips).toBe(20);
    expect(cfg).not.toHaveProperty('take_profit_pips');
  });

  it('includes TP only when TP is set', () => {
    const cfg = _buildRiskConfig({ ...baseline, takeProfitPips: '40' });
    expect(cfg.take_profit_pips).toBe(40);
    expect(cfg).not.toHaveProperty('stop_loss_pips');
  });

  it('includes both when both are set', () => {
    const cfg = _buildRiskConfig({
      ...baseline,
      stopLossPips: '20',
      takeProfitPips: '40',
    });
    expect(cfg.stop_loss_pips).toBe(20);
    expect(cfg.take_profit_pips).toBe(40);
  });

  it('omits SL when input is whitespace-only', () => {
    const cfg = _buildRiskConfig({ ...baseline, stopLossPips: '   ' });
    expect(cfg).not.toHaveProperty('stop_loss_pips');
  });

  it('omits SL when input is zero or negative', () => {
    expect(_buildRiskConfig({ ...baseline, stopLossPips: '0' })).not.toHaveProperty(
      'stop_loss_pips',
    );
    expect(
      _buildRiskConfig({ ...baseline, stopLossPips: '-5' }),
    ).not.toHaveProperty('stop_loss_pips');
  });

  it('omits SL when input is non-numeric', () => {
    const cfg = _buildRiskConfig({ ...baseline, stopLossPips: 'foo' });
    expect(cfg).not.toHaveProperty('stop_loss_pips');
  });

  it('clamps max_position_size_units to at least 1 on truthy sub-1 input', () => {
    // Number('0.5') is truthy so the `|| 10000` fallback doesn't fire —
    // Math.max(1, 0.5) then clamps to 1. Guards against a UX gotcha
    // where a decimal fat-finger would silently reject the order.
    const cfg = _buildRiskConfig({ ...baseline, maxPositionSize: '0.5' });
    expect(cfg.max_position_size_units).toBe(1);
  });

  it('falls back to 10000 for a zero input (Number("0") is falsy)', () => {
    const cfg = _buildRiskConfig({ ...baseline, maxPositionSize: '0' });
    expect(cfg.max_position_size_units).toBe(10000);
  });

  it('falls back to 10000 for empty maxPositionSize', () => {
    const cfg = _buildRiskConfig({ ...baseline, maxPositionSize: '' });
    expect(cfg.max_position_size_units).toBe(10000);
  });
});
