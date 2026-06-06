/**
 * Trades Table Component
 *
 * Displays a sortable table of all trades for a trial with:
 * - Entry/exit timestamps and prices
 * - Duration
 * - P&L in pips (raw) and dollars (with financing)
 * - Spread and swap costs
 */

import React, { useState, useMemo } from 'react';
import { ArrowUpDown, ArrowUp, ArrowDown, TrendingUp, TrendingDown, ChevronLeft, ChevronRight } from 'lucide-react';
import {
  Trade,
  MetricsViewMode,
  formatPips,
  formatDollars,
  formatDuration,
  formatDateTime,
  formatPrice,
} from '../types/optimization';

interface TradesTableProps {
  trades: Trade[];
  viewMode: MetricsViewMode;
  pair: string;
}

type SortField = 'entryTimestamp' | 'direction' | 'pips' | 'netPips' | 'durationMinutes' | 'spreadCostPips' | 'slippageCostPips' | 'swapCostPips' | 'positionSize';
type SortDirection = 'asc' | 'desc';

const PAGE_SIZE = 50;

export default function TradesTable({ trades, viewMode, pair }: TradesTableProps) {
  const [sortField, setSortField] = useState<SortField>('entryTimestamp');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');
  const [currentPage, setCurrentPage] = useState(1);

  // Sort trades
  const sortedTrades = useMemo(() => {
    return [...trades].sort((a, b) => {
      let aVal: number | string = a[sortField];
      let bVal: number | string = b[sortField];

      if (sortField === 'direction') {
        aVal = a.direction;
        bVal = b.direction;
      }

      if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortDirection === 'asc'
          ? aVal.localeCompare(bVal)
          : bVal.localeCompare(aVal);
      }

      return sortDirection === 'asc'
        ? (aVal as number) - (bVal as number)
        : (bVal as number) - (aVal as number);
    });
  }, [trades, sortField, sortDirection]);

  // Pagination
  const totalPages = Math.ceil(sortedTrades.length / PAGE_SIZE);
  const paginatedTrades = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE;
    return sortedTrades.slice(start, start + PAGE_SIZE);
  }, [sortedTrades, currentPage]);

  // Toggle sort
  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
  };

  // Sort icon
  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) {
      return <ArrowUpDown className="w-3.5 h-3.5 text-slate-400" />;
    }
    return sortDirection === 'asc' ? (
      <ArrowUp className="w-3.5 h-3.5 text-blue-500" />
    ) : (
      <ArrowDown className="w-3.5 h-3.5 text-blue-500" />
    );
  };

  // Summary stats
  const totalTrades = trades.length;
  const winningTrades = trades.filter(t => t.pips > 0).length;
  const losingTrades = trades.filter(t => t.pips < 0).length;
  const totalPips = trades.reduce((sum, t) => sum + t.pips, 0);
  const totalNetPips = trades.reduce((sum, t) => sum + t.netPips, 0);
  const totalGrossDollars = trades.reduce((sum, t) => sum + (t.grossPnlDollars ?? 0), 0);
  const totalNetDollars = trades.reduce((sum, t) => sum + (t.netPnlDollars ?? 0), 0);
  const totalSpreadPips = trades.reduce((s, t) => s + (t.spreadCostPips ?? 0), 0);
  const totalSpreadDollars = trades.reduce((s, t) => s + (t.spreadCostDollars ?? 0), 0);
  const totalSlippagePips = trades.reduce((s, t) => s + (t.slippageCostPips ?? 0), 0);
  const totalSlippageDollars = trades.reduce((s, t) => s + (t.slippageCostDollars ?? 0), 0);
  const totalSwapPips = trades.reduce((s, t) => s + (t.swapCostPips ?? 0), 0);
  const totalSwapDollars = trades.reduce((s, t) => s + (t.swapCostDollars ?? 0), 0);

  const fmtCost = (pips: number, dollars: number): string =>
    viewMode === 'pips'
      ? `${pips.toFixed(1)}p`
      : formatDollars(dollars);
  const fmtPnl = (pips: number, dollars: number): string =>
    viewMode === 'pips'
      ? `${formatPips(pips)}p`
      : formatDollars(dollars);

  return (
    <div className="space-y-4">
      {/* Summary Row */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400">Total Trades:</span>
          <span className="font-semibold text-slate-900 dark:text-slate-100">{totalTrades}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400">Winners:</span>
          <span className="font-semibold text-green-600 dark:text-green-400">{winningTrades}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400">Losers:</span>
          <span className="font-semibold text-red-600 dark:text-red-400">{losingTrades}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400">Gross:</span>
          <span
            className={`font-semibold font-mono ${
              totalPips >= 0
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {fmtPnl(totalPips, totalGrossDollars)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400" title="Total OANDA spread paid across all trades">
            Spread:
          </span>
          <span className="font-semibold font-mono text-amber-600 dark:text-amber-400">
            -{fmtCost(totalSpreadPips, totalSpreadDollars)}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400" title="Total slippage buffer applied (0 by default)">
            Slippage:
          </span>
          <span
            className={`font-semibold font-mono ${
              totalSlippagePips > 0
                ? 'text-amber-600 dark:text-amber-400'
                : 'text-slate-400 dark:text-slate-500'
            }`}
          >
            {totalSlippagePips > 0
              ? `-${fmtCost(totalSlippagePips, totalSlippageDollars)}`
              : '—'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400" title="Total overnight financing charged across all trades">
            Swap:
          </span>
          <span
            className={`font-semibold font-mono ${
              totalSwapPips !== 0
                ? 'text-amber-600 dark:text-amber-400'
                : 'text-slate-400 dark:text-slate-500'
            }`}
          >
            {totalSwapPips !== 0
              ? `${totalSwapPips > 0 ? '-' : '+'}${fmtCost(
                  Math.abs(totalSwapPips),
                  Math.abs(totalSwapDollars)
                )}`
              : '—'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-slate-500 dark:text-slate-400">Net:</span>
          <span
            className={`font-semibold font-mono ${
              (viewMode === 'pips' ? totalNetPips : totalNetDollars) >= 0
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {fmtPnl(totalNetPips, totalNetDollars)}
          </span>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto border border-slate-200 dark:border-slate-700 rounded-lg">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-slate-50 dark:bg-slate-800/50 border-b border-slate-200 dark:border-slate-700">
              <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-400">#</th>
              <th
                className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                onClick={() => handleSort('direction')}
              >
                <div className="flex items-center gap-1">
                  Direction
                  <SortIcon field="direction" />
                </div>
              </th>
              <th
                className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                onClick={() => handleSort('entryTimestamp')}
              >
                <div className="flex items-center gap-1">
                  Entry
                  <SortIcon field="entryTimestamp" />
                </div>
              </th>
              <th className="px-3 py-2 text-left font-medium text-slate-600 dark:text-slate-400">
                Exit
              </th>
              <th
                className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                onClick={() => handleSort('durationMinutes')}
              >
                <div className="flex items-center justify-end gap-1">
                  Duration
                  <SortIcon field="durationMinutes" />
                </div>
              </th>
              <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400">
                Entry Price
              </th>
              <th className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400">
                Exit Price
              </th>
              <th
                className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                onClick={() => handleSort('positionSize')}
                title="Order size for this trade, in units of base currency"
              >
                <div className="flex items-center justify-end gap-1">
                  Size
                  <SortIcon field="positionSize" />
                </div>
              </th>
              <th
                className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                onClick={() => handleSort('pips')}
                title="Raw price movement from entry to exit, before any costs"
              >
                <div className="flex items-center justify-end gap-1">
                  Gross
                  <SortIcon field="pips" />
                </div>
              </th>
              <th
                className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                onClick={() => handleSort('spreadCostPips')}
                title="OANDA spread charged on this round trip (half on open, half on close = advertised spread per execution)"
              >
                <div className="flex items-center justify-end gap-1">
                  Spread
                  <SortIcon field="spreadCostPips" />
                </div>
              </th>
              <th
                className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                onClick={() => handleSort('slippageCostPips')}
                title="Execution slippage buffer. OANDA does not bill slippage as a line item; default is 0 pips/side. Override via BACKTEST_SLIPPAGE_PIPS for stress tests."
              >
                <div className="flex items-center justify-end gap-1">
                  Slippage
                  <SortIcon field="slippageCostPips" />
                </div>
              </th>
              <th
                className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                onClick={() => handleSort('swapCostPips')}
                title="OANDA overnight financing — one charge per 21:00 UTC mark crossed, Sat/Sun (UTC) skipped, no 3× Wednesday multiplier"
              >
                <div className="flex items-center justify-end gap-1">
                  Swap
                  <SortIcon field="swapCostPips" />
                </div>
              </th>
              <th
                className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                onClick={() => handleSort('netPips')}
                title="Gross − spread − slippage − swap. The number that actually hits the account."
              >
                <div className="flex items-center justify-end gap-1">
                  Net
                  <SortIcon field="netPips" />
                </div>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
            {paginatedTrades.map((trade, index) => (
              <tr
                key={trade.id}
                className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors"
              >
                <td className="px-3 py-2 text-slate-500 dark:text-slate-400 font-mono text-xs">
                  {(currentPage - 1) * PAGE_SIZE + index + 1}
                </td>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-1.5">
                    {trade.direction === 'long' ? (
                      <TrendingUp className="w-4 h-4 text-green-500" />
                    ) : (
                      <TrendingDown className="w-4 h-4 text-red-500" />
                    )}
                    <span
                      className={`text-xs font-medium uppercase ${
                        trade.direction === 'long'
                          ? 'text-green-600 dark:text-green-400'
                          : 'text-red-600 dark:text-red-400'
                      }`}
                    >
                      {trade.direction}
                    </span>
                  </div>
                </td>
                <td className="px-3 py-2 text-slate-700 dark:text-slate-300 text-xs">
                  {formatDateTime(trade.entryTimestamp)}
                </td>
                <td className="px-3 py-2 text-slate-700 dark:text-slate-300 text-xs">
                  {formatDateTime(trade.exitTimestamp)}
                </td>
                <td className="px-3 py-2 text-right text-slate-700 dark:text-slate-300 font-mono text-xs">
                  {formatDuration(trade.durationMinutes)}
                </td>
                <td className="px-3 py-2 text-right text-slate-700 dark:text-slate-300 font-mono text-xs">
                  {formatPrice(trade.entryPrice)}
                </td>
                <td className="px-3 py-2 text-right text-slate-700 dark:text-slate-300 font-mono text-xs">
                  {formatPrice(trade.exitPrice)}
                </td>
                <td className="px-3 py-2 text-right text-slate-700 dark:text-slate-300 font-mono text-xs">
                  {(trade.positionSize ?? 0).toLocaleString()}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  <span
                    className={
                      trade.pips >= 0
                        ? 'text-green-600 dark:text-green-400'
                        : 'text-red-600 dark:text-red-400'
                    }
                  >
                    {viewMode === 'pips'
                      ? `${formatPips(trade.pips)}p`
                      : formatDollars(trade.grossPnlDollars)}
                  </span>
                </td>
                <td className="px-3 py-2 text-right text-amber-600 dark:text-amber-400 font-mono text-xs">
                  {viewMode === 'pips'
                    ? `-${(trade.spreadCostPips ?? 0).toFixed(1)}p`
                    : formatDollars(-(trade.spreadCostDollars ?? 0))}
                </td>
                <td
                  className={`px-3 py-2 text-right font-mono text-xs ${
                    (trade.slippageCostPips ?? 0) > 0
                      ? 'text-amber-600 dark:text-amber-400'
                      : 'text-slate-400 dark:text-slate-500'
                  }`}
                >
                  {(trade.slippageCostPips ?? 0) > 0
                    ? viewMode === 'pips'
                      ? `-${(trade.slippageCostPips ?? 0).toFixed(1)}p`
                      : formatDollars(-(trade.slippageCostDollars ?? 0))
                    : '—'}
                </td>
                <td
                  className={`px-3 py-2 text-right font-mono text-xs ${
                    (trade.swapCostPips ?? 0) !== 0
                      ? 'text-amber-600 dark:text-amber-400'
                      : 'text-slate-400 dark:text-slate-500'
                  }`}
                >
                  {(trade.swapCostPips ?? 0) !== 0
                    ? viewMode === 'pips'
                      ? `${(trade.swapCostPips ?? 0) > 0 ? '-' : '+'}${Math.abs(
                          trade.swapCostPips ?? 0
                        ).toFixed(2)}p`
                      : formatDollars(-(trade.swapCostDollars ?? 0))
                    : '—'}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  <span
                    className={
                      (viewMode === 'pips'
                        ? (trade.netPips ?? 0)
                        : (trade.netPnlDollars ?? 0)) >= 0
                        ? 'text-green-600 dark:text-green-400'
                        : 'text-red-600 dark:text-red-400'
                    }
                  >
                    {viewMode === 'pips'
                      ? `${formatPips(trade.netPips ?? 0)}p`
                      : formatDollars(trade.netPnlDollars)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-slate-500 dark:text-slate-400">
            Showing {(currentPage - 1) * PAGE_SIZE + 1}-{Math.min(currentPage * PAGE_SIZE, sortedTrades.length)} of {sortedTrades.length} trades
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
              disabled={currentPage === 1}
              className="p-1.5 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-30 hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-slate-700 dark:text-slate-300 font-mono">
              {currentPage} / {totalPages}
            </span>
            <button
              onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
              disabled={currentPage === totalPages}
              className="p-1.5 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-30 hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Footer note */}
      {(() => {
        const sizes = trades
          .map((t) => t.positionSize ?? 0)
          .filter((s) => s > 0);
        const minSize = sizes.length ? Math.min(...sizes) : 0;
        const maxSize = sizes.length ? Math.max(...sizes) : 0;
        const sizeLabel =
          minSize === maxSize
            ? `${minSize.toLocaleString()} units`
            : `${minSize.toLocaleString()}–${maxSize.toLocaleString()} units (varies with entry close, sized to risk per trade)`;
        const perTradeSpread =
          totalTrades > 0 ? totalSpreadPips / totalTrades : 0;
        return (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Costs per trade: spread ≈ {perTradeSpread.toFixed(2)} pips round-trip
            (half on open + half on close, snapshotted at evolution-run start),
            slippage {totalSlippagePips > 0 ? `≈ ${(totalSlippagePips / totalTrades).toFixed(2)} pips` : '0 (OANDA does not bill slippage)'},
            swap fires once per 21:00 UTC mark crossed (Sat/Sun UTC skipped).
            Order size: {sizeLabel}.
          </p>
        );
      })()}
    </div>
  );
}
