/**
 * DslTree — recursively renders a `StrategySpec` (DSL) as readable text
 * rather than raw JSON.
 *
 * Shape:
 *   Long entry: SMA(close, 20) > SMA(close, 50)
 *   Short entry: SMA(close, 20) < SMA(close, 50)
 *   Exit: stop=15 pips · target=30 pips · max-hold=240 bars · opposite-signal
 *   Sizing: risk 0.5%
 *
 * Indicator function calls render as `NAME(args)` rather than the raw
 * `{ind: "sma", args: [20]}` form. AND/OR conditions are bracketed and
 * indented for legibility.
 */

import React from 'react';
import type {
  DslComparison,
  DslCondition,
  DslExpr,
  DslLogical,
  DslSpec,
} from '../types/evolution';

function isIndicatorCall(
  e: DslExpr,
): e is { ind: string; args: number[] } {
  return typeof e === 'object' && e !== null && 'ind' in e;
}

function isLogical(c: DslCondition): c is DslLogical {
  return typeof c === 'object' && c !== null && 'op' in c;
}

function renderExpr(e: DslExpr): string {
  if (isIndicatorCall(e)) {
    const args = e.args && e.args.length > 0 ? e.args.join(', ') : '';
    return `${e.ind.toUpperCase()}(${args})`;
  }
  return String(e);
}

function renderComparison(c: DslComparison): string {
  return `${renderExpr(c.lhs)} ${c.cmp} ${renderExpr(c.rhs)}`;
}

function ConditionNode({
  cond,
  depth = 0,
}: {
  cond: DslCondition | null | undefined;
  depth?: number;
}) {
  if (!cond) {
    return <span className="text-slate-400 italic">(none)</span>;
  }
  if (isLogical(cond)) {
    const sep = (
      <span className="text-blue-600 dark:text-blue-400 font-medium px-1">
        {cond.op}
      </span>
    );
    return (
      <span>
        <span className="text-slate-400">(</span>
        {cond.args.map((sub, i) => (
          <React.Fragment key={i}>
            {i > 0 && sep}
            <ConditionNode cond={sub} depth={depth + 1} />
          </React.Fragment>
        ))}
        <span className="text-slate-400">)</span>
      </span>
    );
  }
  // Comparison — colour the comparator
  return (
    <span className="font-mono text-slate-800 dark:text-slate-200">
      <span>{renderExpr(cond.lhs)}</span>
      <span className="text-purple-600 dark:text-purple-400 px-1.5 font-semibold">
        {cond.cmp}
      </span>
      <span>{renderExpr(cond.rhs)}</span>
    </span>
  );
}

interface DslTreeProps {
  spec: DslSpec;
  className?: string;
}

export default function DslTree({ spec, className = '' }: DslTreeProps) {
  const exit = spec.exit ?? {};
  const size = spec.size ?? {};

  return (
    <div
      className={`rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/50 p-4 space-y-3 text-sm ${className}`}
    >
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">
          Long Entry
        </div>
        <ConditionNode cond={spec.entry?.long} />
      </div>

      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">
          Short Entry
        </div>
        <ConditionNode cond={spec.entry?.short} />
      </div>

      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">
          Exit
        </div>
        <div className="font-mono text-slate-700 dark:text-slate-300 flex flex-wrap gap-x-3 gap-y-1">
          {exit.stop_pips !== undefined && (
            <span>
              stop=<span className="font-semibold">{exit.stop_pips}</span> pips
            </span>
          )}
          {exit.target_pips !== undefined && (
            <span>
              target=
              <span className="font-semibold">{exit.target_pips}</span> pips
            </span>
          )}
          {exit.max_hold_bars !== undefined && (
            <span>
              max-hold=
              <span className="font-semibold">{exit.max_hold_bars}</span> bars
            </span>
          )}
          {exit.opposite_signal !== undefined && (
            <span
              className={
                exit.opposite_signal
                  ? 'text-blue-600 dark:text-blue-400'
                  : 'text-slate-400'
              }
            >
              {exit.opposite_signal ? 'opposite-signal ✓' : 'opposite-signal ✗'}
            </span>
          )}
        </div>
      </div>

      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">
          Sizing
        </div>
        <div className="font-mono text-slate-700 dark:text-slate-300">
          risk{' '}
          <span className="font-semibold">
            {size.risk_pct !== undefined
              ? `${(size.risk_pct * 100).toFixed(2)}%`
              : '—'}
          </span>{' '}
          per trade
        </div>
      </div>
    </div>
  );
}
