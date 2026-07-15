/**
 * Trades table for /paper/:id (US-025).
 *
 * Renders the /paper-runs/:id/trades payload as one row per trade
 * with Origin + Signal reason columns per Wilson's UX answer #1.
 * Open trades sort ahead of closed ones (backend ordering); we don't
 * re-sort client-side.
 */

import React from 'react';
import type { PaperTradePublic } from '../types/paper';
import { formatUTC } from '../utils/time';

interface PaperRunTradesTableProps {
  trades: PaperTradePublic[];
  /** Latest closed-bar price for the run's instrument. When provided,
   *  open trades render an approximate unrealized PnL in the P&L
   *  column with an "unrealized" badge. Approximate because it uses
   *  the latest bar close (mid) rather than the broker's live bid/ask
   *  spread — off by ~1 pip for majors. */
  latestClose?: number | null;
  /** Instrument symbol (e.g. "EUR_USD" or "USD_JPY") — used to pick
   *  pip size for the pip-move display (0.01 for JPY-quoted pairs,
   *  0.0001 otherwise). Falls back to 0.0001 (majors) when absent. */
  instrument?: string | null;
}

/** Pip size in the instrument's quote currency. Mirrors
 *  `pip_size_for_pair` on the runner side; kept small + duplicated so
 *  the table has no cross-module dep. */
function pipSizeFor(instrument: string | null | undefined): number {
  if (!instrument) return 0.0001;
  const parts = instrument.toUpperCase().replace('/', '_').split('_');
  if (parts.length !== 2) return 0.0001;
  return parts[1] === 'JPY' ? 0.01 : 0.0001;
}

export function PaperRunTradesTable({
  trades,
  latestClose,
  instrument,
}: PaperRunTradesTableProps) {
  const pipSize = pipSizeFor(instrument);
  if (trades.length === 0) {
    return (
      <div className="text-xs text-slate-500 dark:text-slate-400 italic">
        No trades yet. The runner writes a row on every order placement;
        exit rows fill in on close.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-xs text-slate-500 dark:text-slate-400 uppercase text-left">
          <tr className="border-b border-slate-200 dark:border-slate-700">
            <th className="py-2 pr-3">Entry</th>
            <th className="py-2 pr-3">Exit</th>
            <th className="py-2 pr-3">Side</th>
            <th className="py-2 pr-3 text-right">Qty</th>
            <th className="py-2 pr-3 text-right">Entry px</th>
            <th className="py-2 pr-3 text-right">Exit px</th>
            <th className="py-2 pr-3 text-right">P&amp;L</th>
            <th className="py-2 pr-3">Origin</th>
            <th className="py-2">Reason</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => {
            const open = t.exit_timestamp === null;
            const sign = t.side === 'buy' ? 1 : -1;
            // Realised PnL for closed trades; approximate unrealised
            // for open trades (latest bar close vs entry, ignoring
            // spread — off by ~1 pip on majors).
            let pnl: number | null;
            let pnlIsUnrealized = false;
            // Pip move: (exit - entry) × side, in pip units. For open
            // trades derived from latestClose the same way pnl$ is.
            // Nulls out cleanly when we don't have a closing price.
            let pipMove: number | null = null;
            if (open && latestClose != null) {
              pnl = (latestClose - t.entry_price) * t.quantity * sign;
              pipMove = ((latestClose - t.entry_price) * sign) / pipSize;
              pnlIsUnrealized = true;
            } else {
              pnl = t.pnl;
              if (t.exit_price != null) {
                pipMove = ((t.exit_price - t.entry_price) * sign) / pipSize;
              }
            }
            const pnlClass =
              pnl == null
                ? 'text-slate-500'
                : pnl > 0
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : pnl < 0
                    ? 'text-rose-600 dark:text-rose-400'
                    : 'text-slate-500';
            return (
              <tr
                key={t.id}
                className={`border-b border-slate-100 dark:border-slate-800 ${
                  open ? 'bg-amber-50/40 dark:bg-amber-900/10' : ''
                }`}
              >
                <td className="py-1.5 pr-3 whitespace-nowrap text-xs text-slate-600 dark:text-slate-300">
                  {formatUTC(t.entry_timestamp)}
                </td>
                <td className="py-1.5 pr-3 whitespace-nowrap text-xs text-slate-600 dark:text-slate-300">
                  {t.exit_timestamp
                    ? formatUTC(t.exit_timestamp)
                    : <span className="text-amber-600 font-medium">open</span>}
                </td>
                <td className="py-1.5 pr-3">
                  <span
                    className={`px-1.5 py-0.5 rounded text-xs uppercase font-medium ${
                      t.side === 'buy'
                        ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                        : 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300'
                    }`}
                  >
                    {t.side === 'buy' ? 'LONG' : 'SHORT'}
                  </span>
                </td>
                <td className="py-1.5 pr-3 text-right font-mono text-xs">
                  {t.quantity.toLocaleString()}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono text-xs">
                  {t.entry_price.toFixed(5)}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono text-xs">
                  {t.exit_price != null ? t.exit_price.toFixed(5) : '—'}
                </td>
                <td className={`py-1.5 pr-3 text-right font-mono text-xs ${pnlClass}`}>
                  {pnl != null ? (
                    <>
                      <div>{pnl.toFixed(2)}</div>
                      {pipMove != null && (
                        <div className="text-[10px] opacity-70">
                          {pipMove >= 0 ? '+' : ''}
                          {pipMove.toFixed(1)} pips
                        </div>
                      )}
                      {pnlIsUnrealized && (
                        <div
                          className="text-[10px] uppercase italic font-sans text-amber-600 dark:text-amber-400"
                          title="Approximate — latest bar close vs entry, ignoring spread"
                        >
                          unrealised
                        </div>
                      )}
                    </>
                  ) : (
                    '—'
                  )}
                </td>
                <td className="py-1.5 pr-3">
                  {t.origin && (
                    <span className="px-1.5 py-0.5 rounded text-xs uppercase bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                      {t.origin}
                    </span>
                  )}
                </td>
                <td className="py-1.5 text-xs text-slate-600 dark:text-slate-300 font-mono truncate max-w-xs">
                  {t.signal_reason ?? '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
