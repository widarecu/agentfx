/**
 * Read-only OANDA sub-accounts table (US-019 / PR-H).
 *
 * The account list comes from `/users/me/credentials/accounts` (cached
 * from OANDA's `/v3/accounts` at last verify). Balance is NOT cached
 * — this table shows the last-seen metadata only; live balances land
 * in PR-J's deploy modal picker via a live-fetch.
 *
 * AgentFX cannot create OANDA sub-accounts — the caption points at
 * the OANDA dashboard for that.
 */

import React from 'react';
import { RefreshCw } from 'lucide-react';

import type { UserOandaAccountPublic } from '../types/credentials';

interface OandaAccountsTableProps {
  accounts: UserOandaAccountPublic[];
  /** ISO timestamp of the last successful verify — surfaced as
   *  "last verified 3m ago" so users know if the list is stale. */
  verifiedAt: string | null;
  onReverify?: () => void;
  reverifying?: boolean;
  /** AFX-7: whether the parent credentials have opted into the fleet
   *  pool. Controls the Pool column's rendering so a "free" default
   *  pool_status doesn't read as "fleet-available" when the account
   *  isn't actually poolable. */
  allowFleetPoolUse?: boolean;
}

export function OandaAccountsTable({
  accounts,
  verifiedAt,
  onReverify,
  reverifying = false,
  allowFleetPoolUse = false,
}: OandaAccountsTableProps) {
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
      <div className="flex items-center justify-between mb-1">
        <div className="text-sm font-semibold">OANDA accounts on this key</div>
        <div className="flex items-center gap-3">
          {verifiedAt && (
            <div className="text-xs text-slate-500 dark:text-slate-400">
              last verified {formatRelative(verifiedAt)}
            </div>
          )}
          {onReverify && (
            <button
              type="button"
              onClick={onReverify}
              disabled={reverifying}
              className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1 border border-slate-200 dark:border-slate-700 rounded hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50"
            >
              <RefreshCw
                className={`w-3 h-3 ${reverifying ? 'animate-spin' : ''}`}
              />
              Re-verify
            </button>
          )}
        </div>
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
        Read-only. Managed inside OANDA — open your OANDA dashboard to
        create additional sub-accounts. AgentFX stores the account ID
        plus a cached alias / currency; balances are always fetched
        live at deploy time.
      </p>

      {accounts.length === 0 ? (
        <div className="border border-dashed border-slate-200 dark:border-slate-700 rounded p-6 text-center text-xs text-slate-500 dark:text-slate-400">
          No sub-accounts detected yet. Click <strong>Re-verify</strong>{' '}
          after saving your API key.
        </div>
      ) : (
        <div className="border border-slate-200 dark:border-slate-700 rounded overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 dark:bg-slate-900 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="text-left px-4 py-2 font-medium">Account ID</th>
                <th className="text-left px-4 py-2 font-medium">Alias</th>
                <th className="text-left px-4 py-2 font-medium">Currency</th>
                <th className="text-left px-4 py-2 font-medium">Pool</th>
                <th className="text-right px-4 py-2 font-medium">Last seen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700">
              {accounts.map((a) => (
                <tr key={a.oanda_account_id}>
                  <td className="px-4 py-2 font-mono text-xs">
                    {a.oanda_account_id}
                  </td>
                  <td className="px-4 py-2">{a.alias ?? '—'}</td>
                  <td className="px-4 py-2">{a.currency}</td>
                  <td className="px-4 py-2">
                    <PoolStatusPill
                      account={a}
                      allowFleetPoolUse={allowFleetPoolUse}
                    />
                  </td>
                  <td className="px-4 py-2 text-right text-xs text-slate-500">
                    {formatRelative(a.last_seen_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/**
 * "5m ago" / "3h ago" / "2d ago" — same shape the evolution runs
 * list uses. Kept inline (no shared util) because this is the only
 * component so far that renders relative timestamps from the
 * credentials domain; hoist when a second consumer appears.
 */
function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const now = Date.now();
  const sec = Math.max(1, Math.floor((now - then) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

export { formatRelative as _formatRelative };

/**
 * AFX-7: compact badge showing a sub-account's fleet-pool status.
 *
 * Five distinguishable states so the operator sees the real picture:
 *
 * 1. Opt-in OFF (`allowFleetPoolUse=false`) → "Not pooled" (slate).
 *    The row's `pool_status` is "free" by default but the fleet
 *    never considers it — rendering "Free" here would read as
 *    "fleet-available" and lie to the operator.
 * 2. Opt-in ON + fleet is holding the slot (`pool_status=in_use`)
 *    → "In use (fleet)" (blue). Tooltip names the run id.
 * 3. Opt-in ON + a direct-path paper run is live on this account
 *    (`active_paper_run_id` present but `pool_status != in_use`)
 *    → "In use (direct)" (indigo). This covers runs started
 *    before opt-in, or any path that bypassed the pool.
 * 4. Opt-in ON + pool is cooling it down → "Cooling" (amber), with
 *    countdown tooltip.
 * 5. Opt-in ON + genuinely free → "Free" (emerald, fleet-available).
 *
 * Also DISABLED (operator took a sub-account out of rotation) still
 * renders when opt-in is ON — distinct from "not pooled".
 */
export type PoolColumnKind =
  | 'not-pooled'
  | 'fleet-in-use'
  | 'direct-in-use'
  | 'cooling'
  | 'disabled'
  | 'free';

export interface PoolColumnView {
  kind: PoolColumnKind;
  label: string;
  title: string;
  /** Tailwind classes for the pill. Pulled out so tests don't need to
   *  parse DOM to confirm which state rendered. */
  tone: string;
}

/**
 * Pure selector: given an account + the parent credentials' opt-in
 * flag, pick which Pool-column pill to show. Kept pure so tests can
 * assert all 5 states without rendering React / DOM.
 *
 * `now` is injected so cooldown countdowns are deterministic in tests.
 */
export function selectPoolColumnView(
  account: UserOandaAccountPublic,
  allowFleetPoolUse: boolean,
  now: number = Date.now(),
): PoolColumnView {
  if (!allowFleetPoolUse) {
    const extra = account.active_paper_run_id ? ' · paper run active' : '';
    return {
      kind: 'not-pooled',
      label: 'Not pooled',
      title:
        'Opt-in to fleet pool is OFF — this account is not lendable to ' +
        'fleet experiments.' + extra,
      tone:
        'bg-slate-100 dark:bg-slate-800 text-slate-500 dark:text-slate-400 ' +
        'border border-slate-200 dark:border-slate-700',
    };
  }

  if (account.pool_status === 'in_use') {
    return {
      kind: 'fleet-in-use',
      label: 'In use (fleet)',
      title: account.pool_current_run_id
        ? `Held by fleet run ${account.pool_current_run_id}`
        : 'In use by the fleet',
      tone: 'bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300',
    };
  }

  // Opt-in is ON but there's a non-pool paper run running on this
  // account. Common path: run started pre-opt-in and continued on
  // owner creds. The fleet won't allocate this slot while the direct
  // run holds it (OANDA single-run-per-account guard), so the badge
  // tells the operator what's going on.
  if (account.active_paper_run_id) {
    return {
      kind: 'direct-in-use',
      label: 'In use (direct)',
      title:
        `Direct paper run ${account.active_paper_run_id} is active on ` +
        'this account (not via the fleet pool — likely started before ' +
        'opt-in, or deployed directly from the Approval Queue).',
      tone:
        'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300',
    };
  }

  if (account.pool_status === 'cooling_down') {
    const until = account.pool_cooling_until
      ? Math.max(
          0,
          Math.floor(
            (new Date(account.pool_cooling_until).getTime() - now) / 1000,
          ),
        )
      : 0;
    return {
      kind: 'cooling',
      label: 'Cooling',
      title: `Cools down in ${until}s before the fleet can reuse it`,
      tone:
        'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300',
    };
  }

  if (account.pool_status === 'disabled') {
    return {
      kind: 'disabled',
      label: 'Disabled',
      title: 'Operator took this account out of rotation',
      tone:
        'bg-slate-200 dark:bg-slate-700 text-slate-600 dark:text-slate-300',
    };
  }

  return {
    kind: 'free',
    label: 'Free',
    title: 'Available for the fleet to allocate on the next run',
    tone:
      'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300',
  };
}

/**
 * AFX-7: compact badge showing a sub-account's fleet-pool status.
 * Thin wrapper around `selectPoolColumnView`.
 */
function PoolStatusPill({
  account,
  allowFleetPoolUse,
}: {
  account: UserOandaAccountPublic;
  allowFleetPoolUse: boolean;
}) {
  const view = selectPoolColumnView(account, allowFleetPoolUse);
  return (
    <span
      title={view.title}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${view.tone}`}
    >
      {view.label}
    </span>
  );
}

export { PoolStatusPill as _PoolStatusPill };
