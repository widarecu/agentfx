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
            // spread — off by ~1 pip on majors). For closed trades we
            // show NET pnl (pnl + financing - commission); the tooltip
            // breaks it down. For open trades we still only have the
            // price-derived unrealised (financing on an open trade is
            // exposed via t.financing if the reconciler saw a
            // DAILY_FINANCING event, but the display keeps it in the
            // tooltip only to avoid a confusing "unrealised net" mix).
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
              // Prefer server-computed net_pnl (includes financing +
              // commission); fall back to raw pnl for legacy rows
              // where net_pnl might be null.
              pnl = t.net_pnl != null ? t.net_pnl : t.pnl;
              if (t.exit_price != null) {
                pipMove = ((t.exit_price - t.entry_price) * sign) / pipSize;
              }
            }
            // Tooltip breakdown: only interesting when at least one
            // fee/financing component is non-zero. Format as a
            // multi-line title string (Tailwind doesn't need a
            // headless Tooltip for this — native browser tooltip
            // suffices for now).
            const financing = t.financing;
            const commission = t.commission;
            // For OPEN trades `t.pnl` is null (only set on close); the
            // price component we can show is the mark-to-market `pnl`
            // computed above. For CLOSED trades `t.pnl` is the raw
            // realised price P&L; `pnl` above is `t.net_pnl` (already
            // net of financing + commission).
            const priceComponent = open ? pnl : t.pnl;
            const hasFinancingData = financing != null || commission != null;
            const tooltipLines: string[] = [];
            if (hasFinancingData && priceComponent != null) {
              tooltipLines.push(
                open
                  ? `Mark-to-market: ${priceComponent.toFixed(2)}`
                  : `Price P&L:      ${priceComponent.toFixed(2)}`,
              );
              const fin = financing ?? 0;
              tooltipLines.push(
                `Financing:      ${fin >= 0 ? '+' : ''}${fin.toFixed(2)}`,
              );
              const com = commission ?? 0;
              tooltipLines.push(
                `Commission:    -${Math.abs(com).toFixed(2)}`,
              );
              tooltipLines.push('─────────────────');
              if (open) {
                const netIfClosedNow = priceComponent + fin - com;
                tooltipLines.push(
                  `Net if closed:  ${netIfClosedNow.toFixed(2)}`,
                );
              } else {
                tooltipLines.push(`Net:            ${(pnl ?? 0).toFixed(2)}`);
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
                data-trade-id={t.id}
                // `data-trade-id` is the anchor the LivePnLChart's
                // trade-marker click handler scrolls to (PR3 of the
                // v0.6.3 chart redesign). Also lets the ring-highlight
                // stick to a table row for a moment so the user's eye
                // catches which row was jumped to.
                className={`border-b border-slate-100 dark:border-slate-800 transition-shadow duration-500 ${
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
                <td
                  className={`py-1.5 pr-3 text-right font-mono text-xs ${pnlClass}`}
                  title={tooltipLines.length ? tooltipLines.join('\n') : undefined}
                >
                  {pnl != null ? (
                    <>
                      <div className="flex items-center justify-end gap-1">
                        <span>{pnl.toFixed(2)}</span>
                        {tooltipLines.length > 0 && (
                          <span
                            className="text-[10px] text-slate-400 dark:text-slate-500 cursor-help"
                            aria-label="Includes financing / commission — hover for breakdown"
                          >
                            *
                          </span>
                        )}
                      </div>
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
                  ) : !open ? (
                    // Wilson UAT 2026-07-28 (v0.6.5): trade shows as
                    // closed (exit_timestamp is set) but pnl is null →
                    // OANDA's SL/TP fired between our ticks, the
                    // runner marked it closed with an estimated exit
                    // price, and the reconciler will fill in the real
                    // realizedPL on its next tick (up to ~12 min).
                    // Distinct from open trades (which show a
                    // mark-to-market above).
                    <div
                      className="text-[10px] uppercase italic font-sans text-amber-600 dark:text-amber-400"
                      title="Broker-side close (SL/TP fired between ticks). Reconciler will populate the authoritative OANDA realized P&L within ~12 min."
                    >
                      pending
                    </div>
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
                <td
                  className="py-1.5 text-xs text-slate-600 dark:text-slate-300 font-mono truncate max-w-xs"
                  title={t.signal_reason ?? undefined}
                >
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
