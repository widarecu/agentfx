/**
 * Vitest for `_buildRiskConfig` in DeployPaperModal (US-028).
 *
 * Critical invariants:
 * - Empty SL/TP inputs must be OMITTED (not sent as null/0), so the
 *   runner falls back to the strategy DSL's exit block.
 * - v0.6.1 (Wilson UAT 2026-07-15): `max_position_size_units` is a
 *   SAFETY CAP, not the primary sizing input. Empty/zero input
 *   OMITS the field so the runner treats it as unbounded; the DSL's
 *   risk_pct × balance formula owns actual sizing.
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

  it('emits max_position_size_units when a positive number is set', () => {
    // Any positive input is forwarded verbatim as a safety cap.
    const cfg = _buildRiskConfig({ ...baseline, maxPositionSize: '250000' });
    expect(cfg.max_position_size_units).toBe(250000);
  });

  it('emits sub-1 positive input verbatim (backend clamps if needed)', () => {
    // A decimal fat-finger like '0.5' still passes the >0 test. Backend
    // policy layer decides whether to reject/clamp — the modal doesn't
    // silently rewrite user intent.
    const cfg = _buildRiskConfig({ ...baseline, maxPositionSize: '0.5' });
    expect(cfg.max_position_size_units).toBe(0.5);
  });

  it('omits max_position_size_units when input is zero', () => {
    // 0 is not > 0 → cap intentionally not set → runner runs unbounded.
    const cfg = _buildRiskConfig({ ...baseline, maxPositionSize: '0' });
    expect(cfg).not.toHaveProperty('max_position_size_units');
  });

  it('omits max_position_size_units when input is blank', () => {
    // Blank = "no safety cap set" — runner sizes purely from DSL
    // risk_pct × balance. This is the new default post-v0.6.1.
    const cfg = _buildRiskConfig({ ...baseline, maxPositionSize: '' });
    expect(cfg).not.toHaveProperty('max_position_size_units');
  });

  it('omits max_position_size_units when input is non-numeric', () => {
    const cfg = _buildRiskConfig({ ...baseline, maxPositionSize: 'foo' });
    expect(cfg).not.toHaveProperty('max_position_size_units');
  });
});
