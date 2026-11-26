import type { ValidationHoldout } from '../types/evolution';

const LABELS: Record<ValidationHoldout['status'], string> = {
  passed: 'Passed',
  failed: 'Failed',
  blocked: 'Not run (already used)',
  unavailable: 'Not run (window too short)',
  error: 'Not completed (holdout spent)',
};

/** Everything the genome page shows for the sealed final holdout
 * (AFX-151). Null means the genome never reached it: it failed an
 * earlier check, or pre-dates the holdout. */
export function selectGenomeHoldout(
  evidence: ValidationHoldout | null | undefined,
) {
  if (!evidence) return null;

  const day = (value: string | null | undefined) => value?.slice(0, 10) ?? '—';
  const pct = (value: number | null | undefined) =>
    value == null ? '—' : `${(100 * value).toFixed(1)}%`;
  const m = evidence.metrics ?? {};
  return {
    label: LABELS[evidence.status] ?? 'Unknown',
    window:
      evidence.window_start && evidence.window_end
        ? `${day(evidence.window_start)} → ${day(evidence.window_end)}`
        : '—',
    sharpe: m.sharpe?.toFixed(2) ?? '—',
    maxDrawdown: pct(m.max_drawdown == null ? null : Math.abs(m.max_drawdown)),
    cagr: pct(m.cagr),
    trades:
      m.n_trades == null
        ? '—'
        : evidence.min_trades == null
          ? String(m.n_trades)
          : `${m.n_trades} (need ${evidence.min_trades})`,
    reason: evidence.reason,
  };
}
