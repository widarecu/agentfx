import type { ValidationDsr } from '../types/evolution';

export function selectGenomeDsr(evidence: ValidationDsr | null | undefined) {
  if (!evidence) return null;

  const probability = (value: number) => `${(100 * value).toFixed(2)}%`;
  return {
    label:
      evidence.probability === null
        ? 'Unavailable'
        : evidence.passed
          ? 'Passed'
          : 'Failed',
    trials: evidence.trial_count.toLocaleString(),
    probability:
      evidence.probability === null
        ? '—'
        : probability(evidence.probability),
    threshold: probability(evidence.threshold),
    sharpe: evidence.raw_sharpe?.toFixed(2) ?? '—',
    bar: evidence.sharpe_bar?.toFixed(2) ?? '—',
    reason: evidence.reason,
  };
}
