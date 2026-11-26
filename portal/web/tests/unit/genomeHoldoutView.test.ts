import { describe, expect, it } from 'vitest';
import { selectGenomeHoldout } from '../../src/pages/genomeHoldoutView';
import type { ValidationHoldout } from '../../src/types/evolution';

const evidence: ValidationHoldout = {
  status: 'passed',
  reason: 'Sealed holdout passed',
  window_start: '2026-05-27T00:00:00+00:00',
  window_end: '2026-11-26T00:00:00+00:00',
  metrics: {
    sharpe: 1.84,
    max_drawdown: -0.0412,
    hit_rate: 0.53,
    n_trades: 140,
    cagr: 0.221,
  },
  min_trades: 112,
  failures: [],
  blocked_by: null,
};

describe('selectGenomeHoldout', () => {
  it('shows nothing for a genome that never reached the holdout', () => {
    expect(selectGenomeHoldout(null)).toBeNull();
    expect(selectGenomeHoldout(undefined)).toBeNull();
  });

  it('shows the window, numbers and reason for a pass', () => {
    expect(selectGenomeHoldout(evidence)).toEqual({
      label: 'Passed',
      window: '2026-05-27 → 2026-11-26',
      sharpe: '1.84',
      maxDrawdown: '4.1%',
      cagr: '22.1%',
      trades: '140 (need 112)',
      reason: 'Sealed holdout passed',
    });
  });

  it('labels a holdout failure as failed', () => {
    expect(
      selectGenomeHoldout({
        ...evidence,
        status: 'failed',
        reason: 'Sealed holdout failed: Sharpe 0.40 < 1.0',
      }),
    ).toMatchObject({
      label: 'Failed',
      reason: 'Sealed holdout failed: Sharpe 0.40 < 1.0',
    });
  });

  it('says the holdout was not run when the lineage already used it', () => {
    expect(
      selectGenomeHoldout({
        ...evidence,
        status: 'blocked',
        metrics: {},
        min_trades: null,
        blocked_by: 'ancestor genome_x',
      }),
    ).toMatchObject({
      label: 'Not run (already used)',
      sharpe: '—',
      maxDrawdown: '—',
      cagr: '—',
      trades: '—',
    });
  });

  it('says the holdout was not run when the run reserved none', () => {
    expect(
      selectGenomeHoldout({
        ...evidence,
        status: 'unavailable',
        window_start: null,
        window_end: null,
        metrics: {},
        min_trades: null,
      }),
    ).toMatchObject({ label: 'Not run (window too short)', window: '—' });
  });

  it('says the holdout is spent when its backtest crashed', () => {
    expect(
      selectGenomeHoldout({ ...evidence, status: 'error', metrics: {} }),
    ).toMatchObject({
      label: 'Not completed (holdout spent)',
      window: '2026-05-27 → 2026-11-26',
    });
  });
});
