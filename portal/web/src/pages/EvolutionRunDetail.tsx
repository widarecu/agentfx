/**
 * Evolution Run Detail Page (v0.3.0).
 *
 * Three sections:
 * 1. Header — status badge, instrument, generation count, LLM cost,
 *    stop reason, started/completed timestamps + Stop button.
 * 2. Fitness curve — best fitness per generation, with target as a
 *    dashed line.
 * 3. Decision log — paginated, filterable by agent_type.
 */

import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, ChevronLeft, Loader2, Square } from 'lucide-react';

import FitnessCurveChart from '../components/FitnessCurveChart';
import {
  formatLlmCost,
  getEvolutionRun,
  listDecisions,
  listGenerations,
  stopEvolutionRun,
} from '../services/evolutionApi';
import { StatusBadge } from './EvolutionRuns';
import type {
  DecisionLogEntry,
  EvolutionRun,
  Generation,
} from '../types/evolution';

const AGENT_FILTER_OPTIONS = ['all', 'orchestrator', 'ga', 'llm', 'validator'];

export default function EvolutionRunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();

  const [run, setRun] = useState<EvolutionRun | null>(null);
  const [generations, setGenerations] = useState<Generation[]>([]);
  const [decisions, setDecisions] = useState<DecisionLogEntry[]>([]);
  const [decisionTotal, setDecisionTotal] = useState(0);
  const [decisionPage, setDecisionPage] = useState(1);
  const [agentFilter, setAgentFilter] = useState<string>('all');

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function fetchAll() {
    if (!runId) return;
    setError(null);
    try {
      const [runData, gensData, decisionsData] = await Promise.all([
        getEvolutionRun(runId),
        listGenerations(runId),
        listDecisions(
          runId,
          decisionPage,
          50,
          agentFilter === 'all' ? undefined : agentFilter,
        ),
      ]);
      setRun(runData);
      setGenerations(gensData.items);
      setDecisions(decisionsData.items);
      setDecisionTotal(decisionsData.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load run');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setLoading(true);
    void fetchAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, decisionPage, agentFilter]);

  // Auto-refresh while running/queued
  useEffect(() => {
    if (!run || (run.status !== 'running' && run.status !== 'queued')) return;
    const interval = setInterval(() => {
      void fetchAll();
    }, 5000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.status]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
      </div>
    );
  }
  if (error || !run) {
    return (
      <div className="container mx-auto px-4 py-8">
        <div className="px-4 py-3 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-300">
          {error ?? 'Run not found'}
        </div>
        <button
          onClick={() => navigate('/evolution')}
          className="mt-4 inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
        >
          <ChevronLeft className="w-4 h-4" />
          Back to runs
        </button>
      </div>
    );
  }

  const fitnessSeries = generations
    .sort((a, b) => a.index - b.index)
    .map((g) => g.best_fitness);

  const targetFitness =
    typeof run.stop_conditions_json.target_fitness === 'number'
      ? (run.stop_conditions_json.target_fitness as number)
      : null;

  return (
    <div className="container mx-auto px-4 py-8 space-y-6">
      <Link
        to="/evolution"
        className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to runs
      </Link>

      {/* Header card */}
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
        <div className="flex items-start justify-between mb-3">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="font-mono text-base text-slate-900 dark:text-slate-100">
                {run.id}
              </h1>
              <StatusBadge status={run.status} />
            </div>
            <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
              {run.instrument} · {run.granularity} · created{' '}
              {new Date(run.created_at).toLocaleString()}
            </p>
          </div>
          {(run.status === 'running' || run.status === 'queued') && (
            <button
              onClick={async () => {
                if (
                  !confirm(
                    'Stop this run? The orchestrator will finish the current step and exit.',
                  )
                ) {
                  return;
                }
                try {
                  await stopEvolutionRun(run.id);
                  await fetchAll();
                } catch (err) {
                  alert(
                    err instanceof Error
                      ? err.message
                      : 'Failed to stop run',
                  );
                }
              }}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-amber-100 hover:bg-amber-200 dark:bg-amber-900/40 dark:hover:bg-amber-900/60 text-amber-800 dark:text-amber-300 text-sm"
            >
              <Square className="w-4 h-4" />
              Stop
            </button>
          )}
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <Stat label="Generation" value={String(run.current_generation)} />
          <Stat label="Best genome" value={run.best_genome_id ?? '—'} mono />
          <Stat label="LLM cost" value={formatLlmCost(run.llm_cost_usd)} />
          <Stat label="Stop reason" value={run.stop_reason ?? '—'} />
        </div>
      </div>

      {/* Fitness curve */}
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
        <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100 mb-3">
          Best Fitness by Generation
        </h2>
        <FitnessCurveChart values={fitnessSeries} target={targetFitness} />
      </div>

      {/* Best genome shortcut */}
      {run.best_genome_id && (
        <Link
          to={`/evolution/${run.id}/genomes/${run.best_genome_id}`}
          className="block rounded-lg border border-blue-200 dark:border-blue-900 bg-blue-50 dark:bg-blue-950/30 px-5 py-3 text-sm text-blue-800 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-950/50"
        >
          → Inspect best genome <span className="font-mono">{run.best_genome_id}</span>
        </Link>
      )}

      {/* Decision log */}
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Decision Log
          </h2>
          <select
            value={agentFilter}
            onChange={(e) => {
              setAgentFilter(e.target.value);
              setDecisionPage(1);
            }}
            className="text-xs border border-slate-300 dark:border-slate-600 rounded px-2 py-1 bg-white dark:bg-slate-900"
          >
            {AGENT_FILTER_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt === 'all' ? 'all agents' : opt}
              </option>
            ))}
          </select>
        </div>

        {decisions.length === 0 ? (
          <div className="text-sm text-slate-500 dark:text-slate-400 italic py-4">
            No decisions yet.
          </div>
        ) : (
          <div className="space-y-2">
            {decisions.map((d) => (
              <DecisionRow key={d.id} entry={d} />
            ))}
          </div>
        )}

        {decisionTotal > 50 && (
          <div className="flex items-center justify-between mt-4 text-xs">
            <span className="text-slate-500 dark:text-slate-400">
              Page {decisionPage} of{' '}
              {Math.max(1, Math.ceil(decisionTotal / 50))}
            </span>
            <div className="flex gap-2">
              <button
                disabled={decisionPage === 1}
                onClick={() => setDecisionPage((p) => Math.max(1, p - 1))}
                className="px-2 py-1 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-50"
              >
                Prev
              </button>
              <button
                disabled={decisionPage * 50 >= decisionTotal}
                onClick={() => setDecisionPage((p) => p + 1)}
                className="px-2 py-1 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-50"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────

function Stat({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </div>
      <div
        className={`mt-0.5 text-slate-900 dark:text-slate-100 ${mono ? 'font-mono text-xs' : 'text-sm font-medium'}`}
      >
        {value}
      </div>
    </div>
  );
}

const AGENT_BADGE_COLORS: Record<string, string> = {
  orchestrator:
    'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  ga: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  llm: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  validator:
    'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
};

function DecisionRow({ entry }: { entry: DecisionLogEntry }) {
  const badgeClass =
    AGENT_BADGE_COLORS[entry.agent_type] ??
    'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300';

  return (
    <div className="flex gap-3 text-xs py-2 border-b border-slate-100 dark:border-slate-700/50 last:border-0">
      <span className="font-mono text-slate-400 dark:text-slate-500 shrink-0 w-32 truncate">
        {new Date(entry.created_at).toLocaleString()}
      </span>
      <span
        className={`inline-flex items-center px-1.5 py-0.5 rounded ${badgeClass} font-medium uppercase tracking-wide shrink-0 h-min mt-0.5`}
      >
        {entry.agent_type}
      </span>
      <span className="font-mono text-slate-600 dark:text-slate-300 shrink-0 w-32 truncate">
        {entry.action}
      </span>
      <div className="text-slate-700 dark:text-slate-300 flex-1">
        {entry.reasoning_text ?? <em className="text-slate-400">(no reasoning)</em>}
      </div>
    </div>
  );
}
