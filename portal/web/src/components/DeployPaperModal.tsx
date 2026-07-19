/**
 * Deploy Paper modal — OANDA account picker + risk knobs (US-022 / PR-J).
 *
 * Wilson's answer #4 (locked in the v2 UX): the modal never asks for
 * an initial balance. It shows a picker of the user's detected OANDA
 * sub-accounts, and the runner snapshots the balance from the picked
 * account on the first tick. The picker renders cached alias +
 * currency (from `/users/me/credentials/accounts`); live balances
 * refresh on Re-verify from Settings.
 *
 * Confirming triggers POST /paper-runs/approve → 202 → parent
 * navigates to /paper/:id.
 */

import React, { useEffect, useState } from 'react';
import { Loader2, X } from 'lucide-react';

import { listMyAccounts } from '../services/credentialsApi';
import {
  approvePaperRun,
  type ApprovePaperRunRequest,
} from '../services/paperApi';
import type { UserOandaAccountPublic } from '../types/credentials';
import type { ApprovalQueueItem } from '../types/paper';

export interface DeployPaperModalProps {
  item: ApprovalQueueItem;
  onCancel: () => void;
  /** Called after the approve endpoint returns 202. Parent navigates
   *  to /paper/:runId. */
  onDeployed: (runId: string) => void;
}

export function DeployPaperModal({
  item,
  onCancel,
  onDeployed,
}: DeployPaperModalProps) {
  const [accounts, setAccounts] = useState<UserOandaAccountPublic[] | null>(
    null,
  );
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState<string>('');

  // Risk knobs. Defaults match the wireframe values.
  const [name, setName] = useState<string>(
    `${item.instrument} ${item.granularity} — ${new Date().toISOString().slice(0, 10)}`,
  );
  // v0.6.2 (Wilson UAT 2026-07-19): `max_position_size_units` used to
  // be an editable "safety cap" input here. Removed entirely — dynamic
  // sizing (`balance × risk_pct ÷ stop_pips`) is the only ceiling the
  // runner respects, and OANDA's own margin/leverage checks reject
  // orders that exceed real capacity. The extra knob confused the UX
  // (users set numbers that under-sized compounding strategies) and
  // added no safety over the broker's own controls.
  const [dailyLossPct, setDailyLossPct] = useState<string>('2.0');
  const [killDdPct, setKillDdPct] = useState<string>('5.0');
  // Broker-side SL/TP. Pre-populated from the strategy's DSL exit
  // block so paper trades always ship with the strategy's designed
  // stop/target. User can override; the fields are always visible
  // and required so nothing ever leaves without protection.
  // (v0.6.1 UAT feedback 2026-07-13 — previously these were empty
  // strings under an "Advanced" collapsible, so orders shipped
  // without SL/TP unless the user explicitly filled them in.)
  const [stopLossPips, setStopLossPips] = useState<string>(
    item.dsl_exit ? String(item.dsl_exit.stop_pips) : '',
  );
  const [takeProfitPips, setTakeProfitPips] = useState<string>(
    item.dsl_exit ? String(item.dsl_exit.target_pips) : '',
  );
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const list = await listMyAccounts();
        setAccounts(list.items);
        if (list.items.length && !selectedAccountId) {
          setSelectedAccountId(list.items[0].oanda_account_id);
        }
      } catch (err) {
        setLoadError(err instanceof Error ? err.message : String(err));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedAccountId) return;

    // Both SL and TP must be present. v0.6.1 UAT rule: no orders
    // leave without broker-side stop/target protection.
    if (!stopLossPips.trim() || !takeProfitPips.trim()) {
      setSubmitError('Stop loss and take profit are required.');
      return;
    }

    const riskConfig = _buildRiskConfig({
      dailyLossPct,
      killDdPct,
      stopLossPips,
      takeProfitPips,
    });

    const payload: ApprovePaperRunRequest = {
      strategy_genome_id: item.strategy_genome_id,
      instrument: item.instrument,
      granularity: item.granularity,
      oanda_account_id: selectedAccountId,
      name: name.trim() || undefined,
      risk_config: riskConfig as ApprovePaperRunRequest['risk_config'],
    };

    setSubmitting(true);
    setSubmitError(null);
    try {
      const response = await approvePaperRun(payload);
      onDeployed(response.run_id);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  const noAccountsError =
    accounts !== null && accounts.length === 0
      ? 'No OANDA sub-accounts detected — head to Settings and re-verify your credentials before deploying.'
      : null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="deploy-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4"
    >
      <div className="w-full max-w-lg bg-white dark:bg-slate-800 rounded-xl shadow-2xl max-h-[calc(100vh-2rem)] overflow-y-auto">
        <form onSubmit={handleSubmit}>
          <div className="p-6 border-b border-slate-200 dark:border-slate-700 flex items-start justify-between">
            <div>
              <div id="deploy-modal-title" className="text-lg font-semibold">
                Deploy to paper
              </div>
              <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                {item.instrument} · {item.granularity} · genome{' '}
                <span className="font-mono">{item.strategy_genome_id}</span>
              </div>
            </div>
            <button
              type="button"
              onClick={onCancel}
              className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-500"
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="p-6 space-y-4">
            <div>
              <label
                htmlFor="deploy-name"
                className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400"
              >
                Deployment name
              </label>
              <input
                id="deploy-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-1 w-full px-3 py-2 border border-slate-300 dark:border-slate-600 dark:bg-slate-900 rounded text-sm"
              />
            </div>

            <div>
              <div className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400 flex items-center justify-between">
                <span>
                  OANDA account <span className="text-rose-600">*</span>
                </span>
                <span className="text-[10px] normal-case tracking-normal text-slate-400">
                  balance snapshotted at run start
                </span>
              </div>
              {loadError && (
                <div className="mt-2 text-xs text-rose-700 dark:text-rose-400">
                  {loadError}
                </div>
              )}
              {noAccountsError && (
                <div className="mt-2 text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded p-3">
                  {noAccountsError}
                </div>
              )}
              {accounts === null && !loadError && (
                <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  Loading accounts…
                </div>
              )}
              {accounts && accounts.length > 0 && (
                <div
                  role="radiogroup"
                  aria-label="OANDA sub-account"
                  className="mt-1 divide-y divide-slate-200 dark:divide-slate-700 border border-slate-300 dark:border-slate-600 rounded overflow-hidden"
                >
                  {accounts.map((account) => {
                    const selected =
                      selectedAccountId === account.oanda_account_id;
                    return (
                      <label
                        key={account.oanda_account_id}
                        className={`flex items-center gap-3 p-3 cursor-pointer ${
                          selected
                            ? 'bg-emerald-50 dark:bg-emerald-900/20'
                            : 'hover:bg-slate-50 dark:hover:bg-slate-700'
                        }`}
                      >
                        <input
                          type="radio"
                          name="oanda-account"
                          role="radio"
                          checked={selected}
                          onChange={() =>
                            setSelectedAccountId(account.oanda_account_id)
                          }
                          aria-checked={selected}
                          className="accent-emerald-500"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-sm truncate">
                              {account.oanda_account_id}
                            </span>
                            {account.alias && (
                              <span className="text-xs bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300 px-1.5 py-0.5 rounded shrink-0">
                                {account.alias}
                              </span>
                            )}
                          </div>
                          <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                            {account.currency}
                          </div>
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-4">
              <NumericField
                label="Daily loss threshold"
                hint="Auto-halt at this % below snapshot"
                suffix="% of balance"
                value={dailyLossPct}
                onChange={setDailyLossPct}
                step="0.1"
              />
              <NumericField
                label="Kill-switch DD threshold"
                hint="Halt if drawdown from snapshot exceeds this"
                suffix="%"
                value={killDdPct}
                onChange={setKillDdPct}
                step="0.1"
              />
            </div>

            {/* v0.6.1 UAT fix (2026-07-13): SL/TP are always visible
                and pre-populated from the strategy's DSL exit block.
                Nothing ships to OANDA without stop-loss + take-profit
                protection. User can override the pre-filled values.
                Both are required. */}
            <div className="grid grid-cols-2 gap-4">
              <NumericField
                label="Stop loss (pips)"
                hint={
                  item.dsl_exit
                    ? `From strategy DSL (${item.dsl_exit.stop_pips}); override if you want`
                    : 'DSL exit not readable — enter manually'
                }
                value={stopLossPips}
                onChange={setStopLossPips}
                step="1"
                placeholder="e.g. 20"
              />
              <NumericField
                label="Take profit (pips)"
                hint={
                  item.dsl_exit
                    ? `From strategy DSL (${item.dsl_exit.target_pips}); override if you want`
                    : 'DSL exit not readable — enter manually'
                }
                value={takeProfitPips}
                onChange={setTakeProfitPips}
                step="1"
                placeholder="e.g. 40"
              />
            </div>

            {submitError && (
              <div
                role="alert"
                className="text-xs text-rose-700 dark:text-rose-400 bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-800 rounded px-3 py-2"
              >
                {submitError}
              </div>
            )}

            <div className="text-xs text-slate-500 dark:text-slate-400">
              Snapshot balance is captured from the picked OANDA account on
              the first tick — that's your P&amp;L % denominator. Live
              balances refresh from OANDA every 5s on the run detail page.
            </div>
          </div>

          <div className="p-6 border-t border-slate-200 dark:border-slate-700 flex flex-wrap items-center justify-between gap-3">
            <div className="text-xs text-slate-500 dark:text-slate-400 min-w-0 flex-1 basis-full sm:basis-auto">
              The paper runner picks this up within one loop cycle.
            </div>
            <div className="flex gap-2 shrink-0 ml-auto">
              <button
                type="button"
                onClick={onCancel}
                disabled={submitting}
                className="px-4 py-2 border border-slate-200 dark:border-slate-700 rounded text-sm hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50 whitespace-nowrap"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={
                  submitting || !selectedAccountId || accounts?.length === 0
                }
                className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-sm font-semibold shadow disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
              >
                {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
                Approve &amp; deploy →
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

function NumericField({
  label,
  hint,
  suffix,
  value,
  onChange,
  step = '1',
  placeholder,
}: {
  label: string;
  hint?: string;
  suffix?: string;
  value: string;
  onChange: (next: string) => void;
  step?: string;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </label>
      <div className="mt-1 flex">
        <input
          type="number"
          step={step}
          min={0}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={`flex-1 min-w-0 px-3 py-2 border border-slate-300 dark:border-slate-600 dark:bg-slate-900 font-mono text-sm ${
            suffix ? 'rounded-l' : 'rounded'
          }`}
        />
        {suffix && (
          <span className="shrink-0 px-3 py-2 border border-slate-300 dark:border-slate-600 border-l-0 rounded-r bg-slate-50 dark:bg-slate-700 text-slate-500 text-sm whitespace-nowrap">
            {suffix}
          </span>
        )}
      </div>
      {hint && (
        <div className="text-xs text-slate-500 mt-1">{hint}</div>
      )}
    </div>
  );
}

/**
 * Pure helper that builds the `risk_config` payload from the raw
 * text inputs. Exported for Vitest so the SL/TP inclusion logic and
 * default-clamping behaviour lock without a jsdom render pass. See
 * US-028 for the spec: empty SL/TP fields must NOT ship as null/0 —
 * they must be absent from the payload entirely so the backend
 * treats them as "no broker-side SL/TP registered".
 */
export function _buildRiskConfig(inputs: {
  dailyLossPct: string;
  killDdPct: string;
  stopLossPips: string;
  takeProfitPips: string;
}): Record<string, number> {
  const cfg: Record<string, number> = {
    daily_loss_pct: Math.max(0.01, Number(inputs.dailyLossPct) || 2),
    kill_switch_dd_pct: Math.max(0.01, Number(inputs.killDdPct) || 5),
  };
  // v0.6.2 (Wilson UAT 2026-07-19): `max_position_size_units` was
  // dropped as an editable input — sizing is `balance × risk_pct ÷
  // stop_pips` and OANDA's own margin/leverage checks are the real
  // ceiling. Legacy risk_config JSON blobs may still carry the key on
  // pre-2026-07-19 runs; the runner treats a stored 10000 as "no
  // cap set" so those runs keep working unchanged.
  const sl = Number(inputs.stopLossPips);
  if (inputs.stopLossPips.trim() !== '' && Number.isFinite(sl) && sl > 0) {
    cfg.stop_loss_pips = sl;
  }
  const tp = Number(inputs.takeProfitPips);
  if (inputs.takeProfitPips.trim() !== '' && Number.isFinite(tp) && tp > 0) {
    cfg.take_profit_pips = tp;
  }
  return cfg;
}
