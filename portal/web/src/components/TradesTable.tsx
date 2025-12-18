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
import { ArrowUpDown, ArrowUp, ArrowDown, TrendingUp, TrendingDown } from 'lucide-react';
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

type SortField = 'entryTimestamp' | 'direction' | 'pips' | 'netPips' | 'durationMinutes' | 'spreadCostPips' | 'swapCostPips';
type SortDirection = 'asc' | 'desc';

export default function TradesTable({ trades, viewMode, pair }: TradesTableProps) {
  const [sortField, setSortField] = useState<SortField>('entryTimestamp');
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

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
  const totalNetDollars = trades.reduce((sum, t) => sum + t.netPnlDollars, 0);

  return (
    <div className="space-y-4">
      {/* Summary Row */}
      <div className="flex flex-wrap items-center gap-4 text-sm">
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
          <span className="text-slate-500 dark:text-slate-400">
            {viewMode === 'pips' ? 'Total P&L:' : 'Net P&L:'}
          </span>
          <span
            className={`font-semibold font-mono ${
              (viewMode === 'pips' ? totalPips : totalNetDollars) >= 0
                ? 'text-green-600 dark:text-green-400'
                : 'text-red-600 dark:text-red-400'
            }`}
          >
            {viewMode === 'pips'
              ? `${formatPips(totalPips)} pips`
              : formatDollars(totalNetDollars)}
          </span>
        </div>
        {viewMode === 'dollars' && (
          <div className="flex items-center gap-2">
            <span className="text-slate-500 dark:text-slate-400">Gross:</span>
            <span
              className={`font-semibold font-mono ${
                totalPips >= 0
                  ? 'text-green-600 dark:text-green-400'
                  : 'text-red-600 dark:text-red-400'
              }`}
            >
              {formatPips(totalPips)} pips
            </span>
          </div>
        )}
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
                onClick={() => handleSort('pips')}
              >
                <div className="flex items-center justify-end gap-1">
                  {viewMode === 'pips' ? 'P&L (pips)' : 'Gross'}
                  <SortIcon field="pips" />
                </div>
              </th>
              {viewMode === 'dollars' && (
                <>
                  <th
                    className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                    onClick={() => handleSort('spreadCostPips')}
                  >
                    <div className="flex items-center justify-end gap-1">
                      Spread
                      <SortIcon field="spreadCostPips" />
                    </div>
                  </th>
                  <th
                    className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                    onClick={() => handleSort('swapCostPips')}
                  >
                    <div className="flex items-center justify-end gap-1">
                      Swap
                      <SortIcon field="swapCostPips" />
                    </div>
                  </th>
                  <th
                    className="px-3 py-2 text-right font-medium text-slate-600 dark:text-slate-400 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-700/50"
                    onClick={() => handleSort('netPips')}
                  >
                    <div className="flex items-center justify-end gap-1">
                      Net P&L
                      <SortIcon field="netPips" />
                    </div>
                  </th>
                </>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
            {sortedTrades.map((trade, index) => (
              <tr
                key={trade.id}
                className="hover:bg-slate-50 dark:hover:bg-slate-800/30 transition-colors"
              >
                <td className="px-3 py-2 text-slate-500 dark:text-slate-400 font-mono text-xs">
                  {index + 1}
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
                <td className="px-3 py-2 text-right font-mono text-xs">
                  <span
                    className={
                      trade.pips >= 0
                        ? 'text-green-600 dark:text-green-400'
                        : 'text-red-600 dark:text-red-400'
                    }
                  >
                    {viewMode === 'pips'
                      ? `${formatPips(trade.pips)} pips`
                      : formatDollars(trade.grossPnlDollars)}
                  </span>
                </td>
                {viewMode === 'dollars' && (
                  <>
                    <td className="px-3 py-2 text-right text-amber-600 dark:text-amber-400 font-mono text-xs">
                      -{trade.spreadCostPips.toFixed(1)}p / {formatDollars(trade.spreadCostDollars, false)}
                    </td>
                    <td className="px-3 py-2 text-right text-amber-600 dark:text-amber-400 font-mono text-xs">
                      {trade.swapCostPips > 0 ? `-${trade.swapCostPips.toFixed(1)}p` : '0'}
                      {trade.swapCostDollars > 0 ? ` / ${formatDollars(trade.swapCostDollars, false)}` : ''}
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-xs">
                      <span
                        className={
                          trade.netPnlDollars >= 0
                            ? 'text-green-600 dark:text-green-400'
                            : 'text-red-600 dark:text-red-400'
                        }
                      >
                        {formatDollars(trade.netPnlDollars)}
                      </span>
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer note */}
      <p className="text-xs text-slate-500 dark:text-slate-400">
        {viewMode === 'pips' ? (
          <>Showing raw P&L in pips without transaction costs. Switch to "With Financing" to see net results.</>
        ) : (
          <>
            Net P&L includes spread ({trades[0]?.spreadCostPips || 0.8} pips) and overnight swap costs.
            Position size: {(trades[0]?.positionSize || 10000).toLocaleString()} units (0.1 lot).
          </>
        )}
      </p>
    </div>
  );
}
