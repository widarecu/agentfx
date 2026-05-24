/**
 * Evolution Runs List Page (v0.3.0).
 *
 * Mirrors the Optimization list page: paginated table + status badges +
 * "Start New Run" modal.
 */

import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Loader2, Plus, X } from 'lucide-react';

import {
  STATUS_BADGE_CLASSES,
  createEvolutionRun,
  formatLlmCost,
  listEvolutionRuns,
} from '../services/evolutionApi';
import type {
  EvolutionRun,
  EvolutionRunStatus,
} from '../types/evolution';

const PAIRS = ['EUR_USD', 'GBP_USD', 'USD_JPY', 'AUD_USD', 'USD_CHF', 'USD_CAD'];
const GRANULARITIES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4'];

export default function EvolutionRuns() {
  const [runs, setRuns] = useState<EvolutionRun[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(10);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const navigate = useNavigate();

  async function fetchRuns(): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      const data = await listEvolutionRuns(page, pageSize);
      setRuns(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load runs');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void fetchRuns();
    // Auto-refresh every 5s while there are running/queued runs in view —
    // gives the UI a live feel without WebSockets.
    const interval = setInterval(() => {
      const live = runs.some(
        (r) => r.status === 'running' || r.status === 'queued',
      );
      if (live) void fetchRuns();
    }, 5000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="container mx-auto px-4 py-8">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
            Autonomous Strategy Evolution
          </h1>
          <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
            Each run discovers, evolves, and validates trading strategies via
            LLM-guided genetic search. The orchestrator runs in a sandboxed
            subprocess; humans approve only the gate to paper trading.
          </p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-sm font-medium"
        >
          <Plus className="w-4 h-4" />
          Start New Run
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div
          role="alert"
          className="mb-4 px-4 py-3 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-300"
        >
          {error}
        </div>
      )}

      {/* Loading state */}
      {loading && runs.length === 0 ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
        </div>
      ) : (
        <RunsTable runs={runs} onRowClick={(id) => navigate(`/evolution/${id}`)} />
      )}

      {/* Pagination */}
      {total > pageSize && (
        <div className="flex items-center justify-between mt-4 text-sm">
          <span className="text-slate-600 dark:text-slate-400">
            Page {page} of {totalPages} · {total} runs total
          </span>
          <div className="flex gap-2">
            <button
              disabled={page === 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="px-3 py-1.5 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Prev
            </button>
            <button
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              className="px-3 py-1.5 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* About blurb */}
      <div className="mt-8 px-4 py-4 rounded-lg bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 text-sm text-slate-600 dark:text-slate-400">
        <h2 className="font-semibold text-slate-900 dark:text-slate-100 mb-1">
          About Autonomous Strategy Evolution
        </h2>
        <p>
          Each run starts from three seed strategies (MA crossover, RSI
          mean-reversion, Donchian breakout), evolves them via genetic crossover
          and mutation across N generations, and periodically asks an LLM to
          inject novel hypotheses. Validation runs walk-forward with a held-out
          out-of-sample fold; survivors must clear Gate B (Sharpe ≥ 1.0,
          MDD ≤ 10%, hit rate ≥ 48%, ≥ 300 trades on OOS).
        </p>
      </div>

      {/* Create modal */}
      {showCreateModal && (
        <CreateRunModal
          onClose={() => {
            setShowCreateModal(false);
            setCreateError(null);
          }}
          onCreate={async (body) => {
            setCreating(true);
            setCreateError(null);
            try {
              const created = await createEvolutionRun(body);
              setShowCreateModal(false);
              await fetchRuns();
              navigate(`/evolution/${created.run_id}`);
            } catch (err) {
              setCreateError(
                err instanceof Error
                  ? err.message
                  : 'Failed to start evolution run',
              );
            } finally {
              setCreating(false);
            }
          }}
          submitting={creating}
          error={createError}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────

function RunsTable({
  runs,
  onRowClick,
}: {
  runs: EvolutionRun[];
  onRowClick: (id: string) => void;
}) {
  if (runs.length === 0) {
    return (
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 px-6 py-12 text-center text-sm text-slate-500 dark:text-slate-400">
        No evolution runs yet. Click <strong>Start New Run</strong> to begin.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-700">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 dark:bg-slate-800">
          <tr className="text-xs uppercase tracking-wide text-slate-600 dark:text-slate-400">
            <th className="px-4 py-3 text-left font-medium">Run ID</th>
            <th className="px-4 py-3 text-left font-medium">Status</th>
            <th className="px-4 py-3 text-left font-medium">Pair</th>
            <th className="px-4 py-3 text-right font-medium">Generation</th>
            <th className="px-4 py-3 text-right font-medium">LLM cost</th>
            <th className="px-4 py-3 text-left font-medium">Stop reason</th>
            <th className="px-4 py-3 text-left font-medium">Created</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200 dark:divide-slate-700">
          {runs.map((r) => (
            <tr
              key={r.id}
              onClick={() => onRowClick(r.id)}
              className="hover:bg-slate-50 dark:hover:bg-slate-800/50 cursor-pointer"
            >
              <td className="px-4 py-3 font-mono text-xs text-blue-600 dark:text-blue-400">
                <Link to={`/evolution/${r.id}`} onClick={(e) => e.stopPropagation()}>
                  {r.id}
                </Link>
              </td>
              <td className="px-4 py-3">
                <StatusBadge status={r.status} />
              </td>
              <td className="px-4 py-3 font-mono text-xs">
                {r.instrument} {r.granularity}
              </td>
              <td className="px-4 py-3 text-right font-mono">
                {r.current_generation}
              </td>
              <td className="px-4 py-3 text-right font-mono">
                {formatLlmCost(r.llm_cost_usd)}
              </td>
              <td className="px-4 py-3 text-slate-500 dark:text-slate-400 text-xs">
                {r.stop_reason ?? '—'}
              </td>
              <td className="px-4 py-3 text-xs text-slate-500 dark:text-slate-400">
                {new Date(r.created_at).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StatusBadge({ status }: { status: EvolutionRunStatus }) {
  return (
    <span
      className={`inline-block px-2 py-0.5 text-xs rounded ${STATUS_BADGE_CLASSES[status] ?? ''}`}
    >
      {status}
    </span>
  );
}

interface CreateRunBody {
  instrument: string;
  granularity: string;
  data_years: number;
  config?: {
    population_size: number;
    rng_seed: number;
    leverage?: number;
    min_cagr_pct?: number;
  };
  stop_conditions?: { max_generations: number; target_fitness: number };
  llm_budget_usd?: number;
}

function CreateRunModal({
  onClose,
  onCreate,
  submitting,
  error,
}: {
  onClose: () => void;
  onCreate: (body: CreateRunBody) => Promise<void>;
  submitting: boolean;
  error: string | null;
}) {
  const [instrument, setInstrument] = useState(PAIRS[0]);
  const [granularity, setGranularity] = useState('M1');
  const [dataYears, setDataYears] = useState(5);
  const [populationSize, setPopulationSize] = useState(30);
  const [maxGenerations, setMaxGenerations] = useState(30);
  const [targetFitness, setTargetFitness] = useState(1.5);
  const [llmBudgetUsd, setLlmBudgetUsd] = useState(5.0);
  const [leverage, setLeverage] = useState<30 | 50>(30);
  const [minCagrPct, setMinCagrPct] = useState(15); // % per year — Gate B floor

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
    >
      <div className="w-full max-w-lg bg-white dark:bg-slate-800 rounded-lg shadow-xl border border-slate-200 dark:border-slate-700">
        <div className="px-5 py-4 border-b border-slate-200 dark:border-slate-700 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
            Start New Evolution Run
          </h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-500"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form
          onSubmit={async (e) => {
            e.preventDefault();
            await onCreate({
              instrument,
              granularity,
              data_years: dataYears,
              config: {
                population_size: populationSize,
                rng_seed: 42,
                leverage,
                min_cagr_pct: minCagrPct / 100,
              },
              stop_conditions: {
                max_generations: maxGenerations,
                target_fitness: targetFitness,
              },
              llm_budget_usd: llmBudgetUsd,
            });
          }}
          className="px-5 py-4 space-y-4 text-sm"
        >
          <div className="grid grid-cols-2 gap-4">
            <Field label="Instrument">
              <select
                value={instrument}
                onChange={(e) => setInstrument(e.target.value)}
                className="form-select w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              >
                {PAIRS.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </Field>
            <Field label="Granularity">
              <select
                value={granularity}
                onChange={(e) => setGranularity(e.target.value)}
                className="form-select w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              >
                {GRANULARITIES.map((g) => (
                  <option key={g}>{g}</option>
                ))}
              </select>
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Data years" hint="History to evolve over">
              <input
                type="number"
                min={1}
                max={20}
                value={dataYears}
                onChange={(e) => setDataYears(Number(e.target.value))}
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
            <Field label="Population size" hint="Genomes per generation">
              <input
                type="number"
                min={4}
                max={200}
                value={populationSize}
                onChange={(e) => setPopulationSize(Number(e.target.value))}
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Max generations" hint="Hard stop after N">
              <input
                type="number"
                min={1}
                max={200}
                value={maxGenerations}
                onChange={(e) => setMaxGenerations(Number(e.target.value))}
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
            <Field label="Target fitness" hint="Early-stop threshold">
              <input
                type="number"
                step="0.1"
                value={targetFitness}
                onChange={(e) => setTargetFitness(Number(e.target.value))}
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="LLM budget (USD)" hint="Hard cap for hypothesis injection cost">
              <input
                type="number"
                step="0.5"
                min={0}
                value={llmBudgetUsd}
                onChange={(e) => setLlmBudgetUsd(Number(e.target.value))}
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
            <Field
              label="Leverage"
              hint="OANDA retail cap (30 or 50)"
            >
              <select
                value={leverage}
                onChange={(e) =>
                  setLeverage(Number(e.target.value) as 30 | 50)
                }
                className="form-select w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              >
                <option value={30}>30:1</option>
                <option value={50}>50:1</option>
              </select>
            </Field>
          </div>

          <Field
            label="Min CAGR (%/year)"
            hint="Gate B floor for net annualised return on the OOS fold"
          >
            <input
              type="number"
              step="1"
              min={-100}
              max={1000}
              value={minCagrPct}
              onChange={(e) => setMinCagrPct(Number(e.target.value))}
              className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
            />
          </Field>

          {error && (
            <div className="px-3 py-2 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-xs text-red-700 dark:text-red-300">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-md text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700 text-sm"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-md text-sm font-medium"
            >
              {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
              Start Run
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="text-xs font-medium text-slate-700 dark:text-slate-300 mb-1">
        {label}
        {hint && (
          <span className="ml-1 text-slate-400 font-normal">— {hint}</span>
        )}
      </div>
      {children}
    </label>
  );
}
