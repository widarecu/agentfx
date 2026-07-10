/**
 * Decision log stream for /paper/:id (US-025).
 *
 * Renders the /paper-runs/:id/decisions payload as a scrollable list
 * newest-first. Every gate refusal, order placement, kill request,
 * or resume shows up in real time. Server does incremental polling
 * via `?since=` so a long-running run's log doesn't refetch history.
 */

import React from 'react';
import type { PaperDecisionPublic } from '../types/paper';

interface PaperRunDecisionLogProps {
  decisions: PaperDecisionPublic[];
}

const ACTION_STYLES: Record<string, string> = {
  USER_ORDER_PLACED:
    'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  USER_POSITION_CLOSED:
    'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  USER_SIGNAL_SKIPPED_STALE:
    'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  USER_KILL_PAPER:
    'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
  USER_RESUME_PAPER:
    'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
  USER_DEPLOY_PAPER:
    'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  USER_APPROVE_GENOME:
    'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  USER_REJECT_GENOME:
    'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  USER_VALIDATOR_OVERRIDE:
    'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
};

const DEFAULT_ACTION_STYLE =
  'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200';

export function PaperRunDecisionLog({ decisions }: PaperRunDecisionLogProps) {
  if (decisions.length === 0) {
    return (
      <div className="text-xs text-slate-500 dark:text-slate-400 italic">
        No decision-log entries yet. Every gate refusal, order placement,
        and kill request lands here in real time.
      </div>
    );
  }

  return (
    <ul className="divide-y divide-slate-100 dark:divide-slate-800 max-h-96 overflow-y-auto">
      {decisions.map((d) => {
        const style = ACTION_STYLES[d.action] ?? DEFAULT_ACTION_STYLE;
        return (
          <li key={d.id} className="py-2 flex items-start gap-3">
            <span
              className={`shrink-0 px-2 py-0.5 rounded text-xs uppercase font-mono ${style}`}
              title={d.agent_type}
            >
              {d.action}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-sm text-slate-700 dark:text-slate-200">
                {d.reason ?? d.action}
              </div>
              <div className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                {new Date(d.timestamp).toLocaleString()}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
