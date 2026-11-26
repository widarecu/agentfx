import { describe, expect, it } from 'vitest';
import { selectGenomeDsr } from '../../src/pages/genomeDsrView';

const evidence = {
  trial_count: 20,
  threshold: 0.95,
  probability: 0.961,
  raw_sharpe: 3.5,
  sharpe_bar: 3.2,
  passed: true,
  reason: 'Meets threshold',
};

describe('selectGenomeDsr', () => {
  it('does not imply a DSR verdict for legacy genomes', () => {
    expect(selectGenomeDsr(null)).toBeNull();
  });

  it('shows the numbers and reason for a pass', () => {
    expect(selectGenomeDsr(evidence)).toEqual({
      label: 'Passed',
      trials: '20',
      probability: '96.10%',
      threshold: '95.00%',
      sharpe: '3.50',
      bar: '3.20',
      reason: 'Meets threshold',
    });
  });

  it('labels a result below the threshold as failed', () => {
    expect(selectGenomeDsr({ ...evidence, probability: 0.94, passed: false })?.label)
      .toBe('Failed');
  });

  it('distinguishes unavailable evidence from a computed failure', () => {
    expect(selectGenomeDsr({
      ...evidence,
      probability: null,
      raw_sharpe: null,
      sharpe_bar: null,
      passed: false,
    })).toMatchObject({ label: 'Unavailable', probability: '—', sharpe: '—', bar: '—' });
  });
});
