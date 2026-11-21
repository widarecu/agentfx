import type { RiskConfig } from '../types/paper';

/**
 * AFX-104: paper runs never auto-halt on loss or drawdown. OANDA's
 * margin closeout is the only loss guardrail. Runs deployed before
 * AFX-104 may still carry `loss_cap_pct` / `kill_switch_dd_pct` in
 * their stored `risk_config`; the runner ignores them.
 */
export const PAPER_NO_LOSS_HALT_NOTE =
  "Paper runs have no loss cap: they keep trading until you stop them, the strategy's own stop-loss closes trades, or OANDA's margin call closes the account.";

export const PAPER_LOSS_HALT_LABEL = 'None (OANDA margin call only)';

export function describePaperLossHalt(
  cfg: RiskConfig | null | undefined,
): string {
  const legacy = [cfg?.loss_cap_pct, cfg?.kill_switch_dd_pct].filter(
    (v): v is number => typeof v === 'number',
  );
  if (legacy.length === 0) return PAPER_LOSS_HALT_LABEL;
  const caps = legacy.map((v) => `${v}%`).join(' / ');
  return `${PAPER_LOSS_HALT_LABEL}; old ${caps} cap ignored`;
}
