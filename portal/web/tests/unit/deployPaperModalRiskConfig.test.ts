/**
 * Vitest for `_buildRiskConfig` in DeployPaperModal.
 *
 * Critical invariants:
 * - Empty SL/TP inputs must be OMITTED (not sent as null/0), so the
 *   runner falls back to the strategy DSL's exit block (US-028).
 * - v0.6.2 (Wilson UAT 2026-07-19): `max_position_size_units` has been
 *   removed as a user-facing input entirely. Sizing is dynamic
 *   (`balance × risk_pct ÷ stop_pips`) and OANDA's own margin/leverage
 *   checks are the real ceiling. The regression test at the bottom
 *   locks that `_buildRiskConfig` no longer emits the key under any
 *   input path.
 */

import { describe, expect, it } from 'vitest';
import { _buildRiskConfig } from '../../src/components/DeployPaperModal';

describe('_buildRiskConfig', () => {
  const baseline = {
    lossCapPct: '2.0',
    killDdPct: '5.0',
    stopLossPips: '',
    takeProfitPips: '',
  };

  it('omits SL/TP when both fields are empty', () => {
    const cfg = _buildRiskConfig(baseline);
    expect(cfg).toEqual({
      loss_cap_pct: 2,
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

  it('regression: never emits max_position_size_units — the input was removed in v0.6.2', () => {
    // The field was deleted from the modal; the runner sizes each
    // trade off risk_pct × balance and OANDA enforces the real
    // ceiling. Any risk_config payload built by this helper must NOT
    // carry the deprecated key, regardless of what other inputs are
    // set.
    const combos = [
      baseline,
      { ...baseline, lossCapPct: '5', killDdPct: '10' },
      { ...baseline, stopLossPips: '20', takeProfitPips: '40' },
      { ...baseline, stopLossPips: '20' },
    ];
    for (const inputs of combos) {
      const cfg = _buildRiskConfig(inputs);
      expect(cfg).not.toHaveProperty('max_position_size_units');
    }
  });
});
