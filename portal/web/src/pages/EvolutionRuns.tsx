/**
 * Evolution Runs List Page (v0.3.0).
 *
 * Mirrors the Optimization list page: paginated table + status badges +
 * "Start New Run" modal.
 */

import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Loader2, Plus, Trash2, X } from 'lucide-react';

import {
  STATUS_BADGE_CLASSES,
  createEvolutionRun,
  deleteEvolutionRun,
  formatLlmCost,
  listEvolutionRuns,
} from '../services/evolutionApi';
import { marketDataService } from '../services/marketData';
import type {
  EvolutionRun,
  EvolutionRunStatus,
} from '../types/evolution';

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
        <RunsTable
          runs={runs}
          onRowClick={(id) => navigate(`/evolution/${id}`)}
          onDelete={async (id) => {
            if (
              !confirm(
                `Delete run ${id}?\n\nThis permanently removes all generations, ` +
                  `genomes, validation folds, and decision-log entries for this ` +
                  `run. The cascade cannot be undone.`,
              )
            )
              return;
            try {
              await deleteEvolutionRun(id);
              await fetchRuns();
            } catch (err) {
              alert(err instanceof Error ? err.message : 'Failed to delete run');
            }
          }}
        />
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
  onDelete,
}: {
  runs: EvolutionRun[];
  onRowClick: (id: string) => void;
  onDelete: (id: string) => void | Promise<void>;
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
            <th className="px-4 py-3 text-right font-medium w-12"></th>
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
              <td className="px-4 py-3 text-right">
                {r.status !== 'running' && r.status !== 'queued' && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      void onDelete(r.id);
                    }}
                    title="Delete this run and all its lineage (cannot be undone)"
                    className="inline-flex items-center justify-center p-1.5 rounded text-slate-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/30"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
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
  /** Explicit window (UTC midnight ISO). Always sent by the create
   * form — the worker uses these verbatim and the run is reproducible
   * regardless of when it's relaunched. */
  data_window_start: string;
  data_window_end: string;
  config?: {
    population_size: number;
    /** Optional: pin a seed for reproducibility. When omitted, the worker
     * generates a fresh 32-bit random seed at run-start and persists it
     * onto config_json so the run can be reproduced later. */
    rng_seed?: number;
    leverage?: number;
    min_cagr_pct?: number;
    spread_pips?: number | null;
  };
  stop_conditions?: {
    max_generations: number;
    target_fitness: number;
    enable_target_fitness_stop?: boolean;
  };
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
  // Instrument + granularity options are pulled from the API so the form
  // reflects what's actually been ingested on this environment. The
  // granularities list is re-fetched whenever the user picks a different
  // instrument so it never shows a timeframe that has no data for the
  // selected pair — what used to be hardcoded
  // `['M1','M5','M15','M30','H1','H4']` silently hid M10 on environments
  // (like staging/UAT) where M10 had been backfilled but the constant
  // didn't list it.
  const [instrumentList, setInstrumentList] = useState<string[] | null>(null);
  const [granularityList, setGranularityList] = useState<string[] | null>(null);
  const [instrument, setInstrument] = useState<string>('');
  const [granularity, setGranularity] = useState<string>('');

  // Initial load: get instruments. Default to first available so the form
  // is usable on first open without an extra click.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const items = await marketDataService.getInstruments();
        if (cancelled) return;
        const symbols = items.map((i) => i.symbol);
        setInstrumentList(symbols);
        if (symbols.length > 0 && !instrument) {
          setInstrument(symbols[0]);
        }
      } catch {
        if (cancelled) return;
        // Fail-safe: surface an empty list, the user will see no options
        // and can refresh. We deliberately don't fall back to a hardcoded
        // list because that's the bug we're fixing.
        setInstrumentList([]);
      }
    })();
    return () => {
      cancelled = true;
    };
    // intentionally empty deps — fetch once on mount
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Whenever instrument changes, re-fetch the timeframes that have
  // candles for THAT instrument. Pick a sensible default (M1 if present,
  // else the shortest-tag candidate the API returns).
  useEffect(() => {
    if (!instrument) return;
    let cancelled = false;
    void (async () => {
      try {
        const tfs = await marketDataService.getTimeframes(instrument);
        if (cancelled) return;
        const codes = tfs.map((t) => t.code);
        setGranularityList(codes);
        if (codes.length === 0) {
          setGranularity('');
        } else if (codes.includes('M1')) {
          setGranularity('M1');
        } else if (!codes.includes(granularity)) {
          setGranularity(codes[0]);
        }
      } catch {
        if (cancelled) return;
        setGranularityList([]);
      }
    })();
    return () => {
      cancelled = true;
    };
    // granularity is intentionally NOT in the deps — we only want to
    // re-derive when the instrument changes, not when the user picks a
    // granularity from the current list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument]);
  // Numeric inputs are stored as strings so editing behaves naturally
  // (clear → type, no leading-zero stickiness, no jump-back-to-min). They
  // are parsed and validated at submit time. Defaults match the
  // OrchestratorConfigSchema defaults.
  const [populationSizeStr, setPopulationSizeStr] = useState('30');
  const [maxGenerationsStr, setMaxGenerationsStr] = useState('30');
  const [targetFitnessStr, setTargetFitnessStr] = useState('1.5');
  const [enableTargetFitnessStop, setEnableTargetFitnessStop] = useState(false);
  const [llmBudgetUsdStr, setLlmBudgetUsdStr] = useState('5.0');
  const [leverage, setLeverage] = useState<30 | 50>(30);
  const [minCagrPctStr, setMinCagrPctStr] = useState('15'); // % per year — Gate B floor
  // Empty string = "use live OANDA snapshot from instruments table"; a
  // number = pin that exact spread for the whole run. Pinning makes
  // results reproducible across container rebuilds (which re-fetch the
  // live spread and would otherwise silently shift the cost model).
  const [spreadPipsStr, setSpreadPipsStr] = useState('');
  // Optional RNG seed for the GA. Blank = "let the worker pick a fresh
  // random seed and persist it on the row" (the recommended default —
  // hardcoded seeds were causing independent runs to deterministically
  // converge on the same winner). Pin a numeric value to reproduce a
  // specific past run.
  const [rngSeedStr, setRngSeedStr] = useState('');
  // Data window is always explicit start + end (UTC midnight). Removed
  // the legacy "Data years" rolling-window mode in favour of a single,
  // unambiguous control: by always pinning the window the run is
  // reproducible weeks later (no `now()` reference in the math) and the
  // form has one less moving part.
  const isoToday = new Date().toISOString().slice(0, 10);
  const isoFiveYearsAgo = new Date(
    Date.now() - 5 * 365 * 24 * 3600 * 1000,
  )
    .toISOString()
    .slice(0, 10);
  const [dataStartStr, setDataStartStr] = useState(isoFiveYearsAgo);
  const [dataEndStr, setDataEndStr] = useState(isoToday);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
    >
      <div className="w-full max-w-lg max-h-[calc(100vh-2rem)] overflow-y-auto bg-white dark:bg-slate-800 rounded-lg shadow-xl border border-slate-200 dark:border-slate-700">
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
            // Parse + validate numeric strings at submit time. HTML5
            // `required` + `type="date"` already covers the date fields.
            const populationSize = Number(populationSizeStr);
            const maxGenerations = Number(maxGenerationsStr);
            const targetFitness = Number(targetFitnessStr);
            const llmBudgetUsd = Number(llmBudgetUsdStr);
            const minCagrPct = Number(minCagrPctStr);
            await onCreate({
              instrument,
              granularity,
              // Send as ISO at UTC midnight — the backend parses both
              // `YYYY-MM-DD` and full ISO, but being explicit avoids
              // local-tz drift.
              data_window_start: `${dataStartStr}T00:00:00Z`,
              data_window_end: `${dataEndStr}T00:00:00Z`,
              config: {
                population_size: populationSize,
                // Only forward when user pinned an explicit seed; blank
                // = let the worker pick a fresh random seed.
                ...(rngSeedStr.trim() !== '' &&
                Number.isFinite(Number(rngSeedStr))
                  ? { rng_seed: Number(rngSeedStr) }
                  : {}),
                leverage,
                min_cagr_pct: minCagrPct / 100,
                // Only forward when the user explicitly pinned a value;
                // otherwise let the orchestrator resolve the live DB
                // snapshot for the instrument.
                ...(spreadPipsStr.trim() !== '' &&
                Number.isFinite(Number(spreadPipsStr))
                  ? { spread_pips: Number(spreadPipsStr) }
                  : {}),
              },
              stop_conditions: {
                max_generations: maxGenerations,
                target_fitness: targetFitness,
                enable_target_fitness_stop: enableTargetFitnessStop,
              },
              llm_budget_usd: llmBudgetUsd,
            });
          }}
          className="px-5 py-4 space-y-4 text-sm"
        >
          <div className="grid grid-cols-2 gap-4">
            <Field
              label="Instrument"
              hint={
                instrumentList === null
                  ? 'loading…'
                  : instrumentList.length === 0
                    ? 'no instruments configured — seed_instruments.py needs to run'
                    : undefined
              }
            >
              <select
                value={instrument}
                onChange={(e) => setInstrument(e.target.value)}
                disabled={instrumentList === null || instrumentList.length === 0}
                className="form-select w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 disabled:opacity-50"
              >
                {(instrumentList ?? []).map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </Field>
            <Field
              label="Granularity"
              hint={
                granularityList === null
                  ? 'loading…'
                  : granularityList.length === 0
                    ? `no candles ingested for ${instrument} yet`
                    : 'only timeframes with backfilled candles are listed'
              }
            >
              <select
                value={granularity}
                onChange={(e) => setGranularity(e.target.value)}
                disabled={granularityList === null || granularityList.length === 0}
                className="form-select w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 disabled:opacity-50"
              >
                {(granularityList ?? []).map((g) => (
                  <option key={g}>{g}</option>
                ))}
              </select>
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Data start" hint="UTC midnight of this date">
              <input
                type="date"
                value={dataStartStr}
                onChange={(e) => setDataStartStr(e.target.value)}
                max={dataEndStr || undefined}
                required
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
            <Field label="Data end" hint="UTC midnight of this date">
              <input
                type="date"
                value={dataEndStr}
                onChange={(e) => setDataEndStr(e.target.value)}
                min={dataStartStr || undefined}
                required
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field
              label="Population size"
              hint="Genomes per generation (15–200; must fit all seed strategies)"
            >
              <input
                type="number"
                inputMode="numeric"
                min={15}
                max={200}
                value={populationSizeStr}
                onChange={(e) => setPopulationSizeStr(e.target.value)}
                required
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
            <Field label="Max generations" hint="Hard stop after N">
              <input
                type="number"
                inputMode="numeric"
                min={1}
                max={200}
                value={maxGenerationsStr}
                onChange={(e) => setMaxGenerationsStr(e.target.value)}
                required
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field
              label="Target fitness"
              hint="Threshold for opt-in early stop (see checkbox below)"
            >
              <input
                type="number"
                inputMode="decimal"
                step="0.1"
                value={targetFitnessStr}
                onChange={(e) => setTargetFitnessStr(e.target.value)}
                disabled={!enableTargetFitnessStop}
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900 disabled:opacity-50 disabled:cursor-not-allowed"
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

          <label className="flex items-start gap-2 text-xs cursor-pointer select-none">
            <input
              type="checkbox"
              checked={enableTargetFitnessStop}
              onChange={(e) => setEnableTargetFitnessStop(e.target.checked)}
              className="mt-0.5"
            />
            <span className="text-slate-700 dark:text-slate-300">
              <span className="font-semibold">
                Stop early when target fitness is reached
              </span>
              <span className="block text-slate-500 dark:text-slate-400 mt-0.5">
                Off by default. Training fitness is biased toward overfit
                shapes, so stopping early on it often sacrifices a
                later-generation candidate that would have OOS-passed.
                Leave unchecked to exhaust the generation budget and let
                top-K validation pick the survivor.
              </span>
            </span>
          </label>

          <div className="grid grid-cols-2 gap-4">
            <Field label="LLM budget (USD)" hint="Hard cap for hypothesis injection cost">
              <input
                type="number"
                inputMode="decimal"
                step="0.5"
                min={0}
                value={llmBudgetUsdStr}
                onChange={(e) => setLlmBudgetUsdStr(e.target.value)}
                required
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
            <Field
              label="Min CAGR (%/year)"
              hint="Gate B floor for net annualised return on the OOS fold"
            >
              <input
                type="number"
                inputMode="decimal"
                step="1"
                min={-100}
                max={1000}
                value={minCagrPctStr}
                onChange={(e) => setMinCagrPctStr(e.target.value)}
                required
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field
              label="Spread (pips)"
              hint="Blank = live OANDA snapshot. Pin a number to make this run reproducible across rebuilds."
            >
              <input
                type="number"
                inputMode="decimal"
                step="0.1"
                min={0}
                placeholder="(use live snapshot)"
                value={spreadPipsStr}
                onChange={(e) => setSpreadPipsStr(e.target.value)}
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
            <Field
              label="RNG seed (optional)"
              hint="Blank = fresh random seed every run (recommended). Pin a value to reproduce a specific past run — the seed actually used is persisted on the run row."
            >
              <input
                type="number"
                inputMode="numeric"
                min={0}
                max={4294967295}
                placeholder="(random)"
                value={rngSeedStr}
                onChange={(e) => setRngSeedStr(e.target.value)}
                className="form-input w-full px-3 py-2 rounded-md border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-900"
              />
            </Field>
          </div>

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
              disabled={submitting || !instrument || !granularity}
              title={
                !instrument || !granularity
                  ? 'Pick an instrument + granularity that have candles ingested.'
                  : undefined
              }
              className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-md text-sm font-medium"
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
  // Reserve enough vertical space for the label + hint to span up to two
  // lines without pushing the input below it. Without this, fields in
  // the same grid row whose labels wrap differently (long hint vs short)
  // end up with their inputs at different y-positions, which looks
  // misaligned. `min-h-[2.75rem]` (~44px) fits two lines of the 12px /
  // leading-tight label across typical column widths in the modal.
  return (
    <label className="flex flex-col">
      <div className="text-xs font-medium text-slate-700 dark:text-slate-300 mb-1 leading-tight min-h-[2.75rem]">
        <span className="text-slate-700 dark:text-slate-300">{label}</span>
        {hint && (
          <span className="text-slate-400 font-normal"> — {hint}</span>
        )}
      </div>
      <div className="mt-auto">{children}</div>
    </label>
  );
}
