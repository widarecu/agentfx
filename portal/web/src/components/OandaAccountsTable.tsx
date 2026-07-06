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
}

export function OandaAccountsTable({
  accounts,
  verifiedAt,
  onReverify,
  reverifying = false,
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
