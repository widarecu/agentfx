/**
 * Evolution Run Detail Page (v0.3.0).
 *
 * Layout, top → bottom:
 *
 *   1. Hero — plain-English run summary + config callout (pop, gens, budget, data window).
 *   2. Outcome banner — ✓ / ✗ / ⚠ headline + one-line diagnostic.
 *   3. Run narrative — derived story of what happened across the loop.
 *   4. Fitness curve — chart with tooltips, LLM-injection markers, Gate B target.
 *   5. Generations table — index, fitness, Δ-vs-prev, LLM-invoked flag, duration.
 *   6. LLM activity panel — one card per LLM injection (or skip event).
 *   7. Best genome shortcut.
 *   8. Decision log (raw, filterable) — for power users who want the audit trail.
 *
 * The goal is "open this page and understand the run in under 30s of
 * scrolling". Sections 1–3 are the headline; the rest is supporting
 * evidence.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  ChevronLeft,
  Loader2,
  Square,
  Trash2,
  CheckCircle2,
  XCircle,
  AlertTriangle,
} from 'lucide-react';

import FitnessCurveChart, {
  type FitnessCurvePoint,
} from '../components/FitnessCurveChart';
import GenomeLineageTree from '../components/GenomeLineageTree';
import RunNarrative from '../components/RunNarrative';
import LlmInjectionCard from '../components/LlmInjectionCard';
import {
  formatLlmCost,
  deleteEvolutionRun,
  getEvolutionRun,
  getGenome,
  listDecisions,
  listGenerations,
  listRunGenomes,
  stopEvolutionRun,
} from '../services/evolutionApi';
import { StatusBadge } from './EvolutionRuns';
import type {
  DecisionLogEntry,
  EvolutionRun,
  Generation,
  StrategyGenome,
} from '../types/evolution';

const AGENT_FILTER_OPTIONS = ['all', 'orchestrator', 'ga', 'llm', 'validator'];

export default function EvolutionRunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();

  const [run, setRun] = useState<EvolutionRun | null>(null);
  const [generations, setGenerations] = useState<Generation[]>([]);
  const [decisions, setDecisions] = useState<DecisionLogEntry[]>([]);
  const [allDecisions, setAllDecisions] = useState<DecisionLogEntry[]>([]);
  const [decisionTotal, setDecisionTotal] = useState(0);
  const [decisionPage, setDecisionPage] = useState(1);
  const [agentFilter, setAgentFilter] = useState<string>('all');
  const [llmGenomes, setLlmGenomes] = useState<StrategyGenome[]>([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function fetchAll() {
    if (!runId) return;
    setError(null);
    try {
      const [runData, gensData, decisionsData, allDecisionsData] = await Promise.all([
        getEvolutionRun(runId),
        listGenerations(runId),
        listDecisions(
          runId,
          decisionPage,
          50,
          agentFilter === 'all' ? undefined : agentFilter,
        ),
        // Pull the full unfiltered decision log once (paginated by 200) so the
        // narrative + LLM panel always see the lifecycle, even if the user
        // has the filter set to something narrow. The decision-log fetch
        // above remains the paginated view shown in the raw log section.
        listDecisions(runId, 1, 200, undefined),
      ]);
      setRun(runData);
      setGenerations(gensData.items);
      setDecisions(decisionsData.items);
      setDecisionTotal(decisionsData.total);
      setAllDecisions(allDecisionsData.items);

      // Fetch full DSL + reasoning for every LLM-origin genome in the run so
      // the LLM injection cards can render each proposal in detail. The
      // summary endpoint omits dsl_json/llm_reasoning to keep the
      // lineage-tree payload light, so we drill in here. Typical run has
      // ≤ 10 LLM-origin genomes so the fan-out is bounded.
      try {
        const llmList = await listRunGenomes(runId, {
          origin: 'llm',
          limit: 200,
        });
        if (llmList.items.length > 0) {
          const detailed = await Promise.all(
            llmList.items.map((g) => getGenome(runId, g.id)),
          );
          setLlmGenomes(detailed);
        } else {
          setLlmGenomes([]);
        }
      } catch {
        // Don't fail the whole page just because we couldn't enrich the
        // LLM cards — they degrade to count-only display.
        setLlmGenomes([]);
      }
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

  const sortedGens = [...generations].sort((a, b) => a.index - b.index);
  const fitnessPoints: FitnessCurvePoint[] = sortedGens.map((g) => ({
    generation: g.index,
    fitness: g.best_fitness,
    llmInvoked: g.llm_invoked,
  }));

  const targetFitness =
    typeof run.stop_conditions_json.target_fitness === 'number'
      ? (run.stop_conditions_json.target_fitness as number)
      : null;

  const injectionDecisions = allDecisions.filter(
    (d) => d.action === 'inject' || (d.agent_type === 'llm' && d.action === 'inject'),
  );

  const validateDecision = allDecisions.find((d) => d.action === 'validate');

  return (
    <div className="container mx-auto px-4 py-8 space-y-6">
      <Link
        to="/evolution"
        className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to runs
      </Link>

      {/* ── 1. Hero ────────────────────────────────────────────────── */}
      <RunHero
        run={run}
        sortedGens={sortedGens}
        onStop={async () => {
          if (
            !confirm(
              'Stop this run? The orchestrator will finish the current step and exit.',
            )
          )
            return;
          try {
            await stopEvolutionRun(run.id);
            await fetchAll();
          } catch (err) {
            alert(err instanceof Error ? err.message : 'Failed to stop run');
          }
        }}
        onDelete={async () => {
          if (
            !confirm(
              `Delete run ${run.id}?\n\nThis permanently removes all generations, ` +
                `genomes, validation folds, and decision-log entries for this run. ` +
                `The cascade cannot be undone.`,
            )
          )
            return;
          try {
            await deleteEvolutionRun(run.id);
            navigate('/evolution');
          } catch (err) {
            alert(err instanceof Error ? err.message : 'Failed to delete run');
          }
        }}
      />

      {/* ── 2. Outcome banner ──────────────────────────────────────── */}
      <OutcomeBanner run={run} validateDecision={validateDecision} />

      {/* ── 2b. Regime chip (v0.4.0) ─────────────────────────────────── */}
      <RegimeChip decisions={allDecisions} />

      {/* ── 3. Narrative ───────────────────────────────────────────── */}
      <Section title="What happened in this run">
        <RunNarrative
          run={run}
          generations={sortedGens}
          decisions={allDecisions}
        />
      </Section>

      {/* ── 4. Fitness chart ───────────────────────────────────────── */}
      <Section title="Best Fitness by Generation">
        <FitnessCurveChart points={fitnessPoints} target={targetFitness} />
      </Section>

      {/* ── 5. Generations table ───────────────────────────────────── */}
      <Section title="Generations">
        <GenerationsTable generations={sortedGens} />
      </Section>

      {/* ── 5b. Lineage tree (all genomes across all gens) ─────────── */}
      <Section
        title="Lineage Tree"
        subtitle="Every genome in the run, one row per generation. Edges connect parent → child. Click any node to drill in."
      >
        {runId && (
          <GenomeLineageTree runId={runId} bestGenomeId={run.best_genome_id} />
        )}
      </Section>

      {/* ── 6. LLM activity ────────────────────────────────────────── */}
      {injectionDecisions.length > 0 && (
        <Section
          title={`LLM Activity (${injectionDecisions.length} injection${
            injectionDecisions.length === 1 ? '' : 's'
          })`}
          subtitle="What the strategy-proposing LLM did at each scheduled injection point."
        >
          <div className="space-y-3">
            {injectionDecisions.map((d) => {
              const proposals = llmGenomes.filter(
                (g) => g.generation_id === d.generation_id,
              );
              // Incumbent = best fitness one generation back (the elite the
              // LLM is trying to beat). Gen 0 has no incumbent.
              const proposalsGenIdx = proposals[0]?.generation_index ?? null;
              const incumbent =
                proposalsGenIdx != null && proposalsGenIdx > 0
                  ? (sortedGens.find((g) => g.index === proposalsGenIdx - 1)
                      ?.best_fitness ?? null)
                  : null;
              return (
                <LlmInjectionCard
                  key={d.id}
                  decision={d}
                  runId={runId ?? ''}
                  proposals={proposals}
                  incumbentFitness={incumbent}
                />
              );
            })}
          </div>
        </Section>
      )}

      {/* ── 7. Best-genome shortcut ────────────────────────────────── */}
      {run.best_genome_id && (
        <Link
          to={`/evolution/${run.id}/genomes/${run.best_genome_id}`}
          className="block rounded-lg border border-blue-200 dark:border-blue-900 bg-blue-50 dark:bg-blue-950/30 px-5 py-3 text-sm text-blue-800 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-950/50"
        >
          → Inspect best genome <span className="font-mono">{run.best_genome_id}</span>
        </Link>
      )}

      {/* ── 8. Decision log (raw) ──────────────────────────────────── */}
      <Section
        title="Raw decision log"
        subtitle="Audit trail of every agent action, in order."
        action={
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
        }
      >
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
      </Section>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Hero — plain-English run summary
// ─────────────────────────────────────────────────────────────────────

function RunHero({
  run,
  sortedGens,
  onStop,
  onDelete,
}: {
  run: EvolutionRun;
  sortedGens: Generation[];
  onStop: () => void | Promise<void>;
  onDelete: () => void | Promise<void>;
}) {
  const cfg = (run.config_json ?? {}) as Record<string, unknown>;
  const popSize = typeof cfg.population_size === 'number' ? cfg.population_size : null;
  const llmBudget = typeof cfg.llm_budget_usd === 'number' ? cfg.llm_budget_usd : null;
  const stopConds = (run.stop_conditions_json ?? {}) as Record<string, unknown>;
  const maxGens =
    typeof stopConds.max_generations === 'number' ? stopConds.max_generations : null;
  // Effective values pinned by the worker at run-start. These are what
  // the GA + validator actually paid; user inputs above show what was
  // requested. The two can differ (rolling-window resolution at
  // run-start, live spread lookup, clamp to available data).
  const effSpread =
    typeof cfg.effective_spread_pips === 'number' ? cfg.effective_spread_pips : null;
  // v0.4.0+ per-pair dict. Prefer this when present; fall back to
  // legacy scalar `effective_spread_pips` for older runs.
  const effSpreadPerPair: Record<string, number> | null =
    cfg.effective_spread_pips_per_pair &&
    typeof cfg.effective_spread_pips_per_pair === 'object'
      ? (cfg.effective_spread_pips_per_pair as Record<string, number>)
      : null;
  // The seed the worker actually used to drive the GA. For runs that
  // pin a seed via the form, this equals the user's input. For runs
  // that left it blank, this is a fresh 32-bit value the worker
  // generated at run-start and persisted onto config_json — copy it
  // forward to a new run to reproduce the same lineage exactly.
  const effRngSeed = typeof cfg.rng_seed === 'number' ? cfg.rng_seed : null;
  const effWindowStart =
    typeof cfg.effective_data_window_start === 'string'
      ? cfg.effective_data_window_start
      : null;
  const effWindowEnd =
    typeof cfg.effective_data_window_end === 'string'
      ? cfg.effective_data_window_end
      : null;
  const minCagrPct =
    typeof cfg.min_cagr_pct === 'number' ? cfg.min_cagr_pct : null;

  const duration = useMemo(() => {
    if (!run.started_at) return null;
    const start = new Date(run.started_at).getTime();
    const end = run.completed_at ? new Date(run.completed_at).getTime() : Date.now();
    const secs = Math.floor((end - start) / 1000);
    if (secs < 60) return `${secs}s`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
    return `${Math.floor(secs / 3600)}h ${Math.floor((secs % 3600) / 60)}m`;
  }, [run.started_at, run.completed_at]);

  // Prefer the *effective* window pinned by the worker at run-start
  // (always set on v0.3.0+ runs that completed snapshotting). Fall back
  // to the user-requested explicit dates, then to the legacy
  // `data_years` field for ancient runs that pre-date the dates-first
  // form.
  const historySpan = useMemo(() => {
    const startStr =
      effWindowStart ??
      (typeof cfg.data_window_start === 'string'
        ? (cfg.data_window_start as string)
        : null);
    const endStr =
      effWindowEnd ??
      (typeof cfg.data_window_end === 'string'
        ? (cfg.data_window_end as string)
        : null);
    if (startStr && endStr) {
      const start = new Date(startStr);
      const end = new Date(endStr);
      const days = Math.max(
        1,
        Math.round((end.getTime() - start.getTime()) / (24 * 3600 * 1000)),
      );
      if (days >= 365) {
        const years = days / 365;
        return `${years.toFixed(1).replace(/\.0$/, '')}y of history`;
      }
      return `${days}d of history`;
    }
    // Ancient-run fallback (no window dates in config_json at all).
    if (typeof cfg.data_years === 'number') {
      return `${cfg.data_years}y of history`;
    }
    return null;
  }, [cfg, effWindowStart, effWindowEnd]);

  const summarySentence = useMemo(() => {
    const parts: string[] = [];
    parts.push(`${run.instrument} ${run.granularity}`);
    if (historySpan) parts.push(historySpan);
    const popPart = popSize ? `${popSize}-genome population` : null;
    const genPart = maxGens ? `${maxGens} generation budget` : null;
    if (popPart && genPart) parts.push(`${popPart} × ${genPart}`);
    else if (popPart) parts.push(popPart);
    else if (genPart) parts.push(genPart);
    if (llmBudget != null) parts.push(`$${llmBudget.toFixed(2)} LLM budget`);
    return parts.join(' · ');
  }, [run, historySpan, popSize, maxGens, llmBudget]);

  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
      <div className="flex items-start justify-between gap-4 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="font-mono text-base text-slate-900 dark:text-slate-100 truncate">
              {run.id}
            </h1>
            <StatusBadge status={run.status} />
          </div>
          <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
            {summarySentence}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {(run.status === 'running' || run.status === 'queued') && (
            <button
              onClick={onStop}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-amber-100 hover:bg-amber-200 dark:bg-amber-900/40 dark:hover:bg-amber-900/60 text-amber-800 dark:text-amber-300 text-sm"
            >
              <Square className="w-4 h-4" />
              Stop
            </button>
          )}
          {run.status !== 'running' && run.status !== 'queued' && (
            <button
              onClick={onDelete}
              title="Delete this run and all its lineage (cannot be undone)"
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-red-100 hover:bg-red-200 dark:bg-red-900/40 dark:hover:bg-red-900/60 text-red-700 dark:text-red-300 text-sm"
            >
              <Trash2 className="w-4 h-4" />
              Delete
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <Stat
          label="Current gen"
          value={
            sortedGens.length > 0
              ? `${sortedGens.length}${maxGens ? ` / ${maxGens}` : ''}`
              : '0'
          }
        />
        <Stat label="Duration" value={duration ?? '—'} />
        <Stat label="LLM cost" value={formatLlmCost(run.llm_cost_usd)} />
        <Stat label="Stop reason" value={run.stop_reason ?? '—'} />
      </div>

      {/* Effective inputs strip — what the GA + validator ACTUALLY paid /
        evaluated over. Reproducibility-critical: surfacing these here
        means a reader can tell at a glance why two runs of the "same"
        spec produced different fitness (spread drift, window shift,
        threshold change). Hidden when the row was created before the
        worker started snapshotting these. */}
      {(effWindowStart ||
        effSpread != null ||
        (effSpreadPerPair != null && Object.keys(effSpreadPerPair).length > 0) ||
        minCagrPct != null ||
        effRngSeed != null) && (
        <div className="mt-3 pt-3 border-t border-slate-200 dark:border-slate-700 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500 dark:text-slate-400 font-semibold uppercase tracking-wide">
            Effective inputs:
          </span>
          {effWindowStart && effWindowEnd && (
            <span
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-800 dark:text-blue-300 font-mono"
              title="The post-clamp data window the GA actually trained on and the validator actually OOS-tested against. Frozen at run-start so re-running won't drift."
            >
              data: {effWindowStart.slice(0, 10)} → {effWindowEnd.slice(0, 10)}
            </span>
          )}
          {effSpreadPerPair != null && Object.keys(effSpreadPerPair).length > 0 ? (
            // v0.4.0+ per-pair shape. Render one chip per pair so a
            // reader can see (and audit) exactly what cost model each
            // backtest paid. Today this is one chip; v0.5.0+ multi-pair
            // runs will line up several side by side.
            Object.entries(effSpreadPerPair).map(([pair, spread]) => (
              <span
                key={pair}
                className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-violet-50 dark:bg-violet-900/30 text-violet-800 dark:text-violet-300 font-mono"
                title={`Spread (in pips) every backtest on ${pair} in this run paid. Pinned at run-start from either the user-provided value or the live instruments snapshot. Does not drift mid-run.`}
              >
                {pair} spread: {spread.toFixed(2)} pips
              </span>
            ))
          ) : effSpread != null ? (
            // Legacy v0.3.0 row without per-pair dict.
            <span
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-violet-50 dark:bg-violet-900/30 text-violet-800 dark:text-violet-300 font-mono"
              title="Spread (in pips) every backtest in this run paid. Pinned at run-start from either the user-provided value or the live instruments snapshot. Does not drift mid-run."
            >
              spread: {effSpread.toFixed(2)} pips
            </span>
          ) : null}
          {minCagrPct != null && (
            <span
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-900/30 text-emerald-800 dark:text-emerald-300 font-mono"
              title="Gate B CAGR floor applied to OOS validation in this run."
            >
              min CAGR: {(minCagrPct * 100).toFixed(0)}%/yr
            </span>
          )}
          {effRngSeed != null && (
            <span
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-700/60 text-slate-700 dark:text-slate-300 font-mono"
              title="The 32-bit RNG seed the GA actually used to drive mutation/crossover/tournament selection. Pin this on a new run's seed field to reproduce the same lineage."
            >
              rng_seed: {effRngSeed.toLocaleString()}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Outcome banner — ✓/✗/⚠ + diagnostic
// ─────────────────────────────────────────────────────────────────────

function OutcomeBanner({
  run,
  validateDecision,
}: {
  run: EvolutionRun;
  validateDecision: DecisionLogEntry | undefined;
}) {
  let icon: React.ReactNode;
  let title: string;
  let detail: React.ReactNode;
  let toneClass: string;

  if (run.status === 'running' || run.status === 'queued') {
    icon = <Loader2 className="w-5 h-5 animate-spin" />;
    title = run.status === 'queued' ? 'Queued — waiting to start' : 'Run in progress';
    detail = (
      <span className="text-slate-500 dark:text-slate-400">
        Polling for updates every 5s.
      </span>
    );
    toneClass =
      'border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40 text-slate-700 dark:text-slate-300';
  } else if (run.status === 'failed' || run.status === 'stopped') {
    icon = <AlertTriangle className="w-5 h-5" />;
    // Use the structured stop_reason to write a meaningful title where
    // possible; otherwise fall back to the generic phrasing.
    const reasonTitles: Record<string, string> = {
      data_window_not_covered:
        'Pinned data window is not covered by available candles',
      process_crashed: 'Run crashed in the worker subprocess',
    };
    title =
      (run.stop_reason && reasonTitles[run.stop_reason]) ||
      (run.status === 'failed'
        ? 'Run failed before completion'
        : 'Run was stopped');
    const failureMessage =
      typeof (run.config_json as { failure_message?: unknown })?.failure_message ===
      'string'
        ? ((run.config_json as { failure_message: string }).failure_message)
        : null;
    detail = (
      <span>
        {run.stop_reason ? (
          <>
            Reason: <span className="font-mono">{run.stop_reason}</span>
            {failureMessage && (
              <>
                <br />
                <span className="text-xs mt-1 inline-block">{failureMessage}</span>
              </>
            )}
          </>
        ) : (
          'No stop reason recorded.'
        )}
      </span>
    );
    toneClass =
      'border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-300';
  } else {
    // status === 'completed'
    const outputs = (validateDecision?.outputs_json ?? {}) as Record<string, unknown>;
    const passed = Boolean(outputs.validator_passed);
    const failures = Array.isArray(outputs.validation_failures)
      ? (outputs.validation_failures as string[])
      : [];

    if (passed) {
      icon = <CheckCircle2 className="w-5 h-5" />;
      title = 'Found a validated genome — ready for human review';
      detail = (
        <span>
          The run-best genome passed Gate B on the held-out OOS fold.{' '}
          {run.best_genome_id && (
            <Link
              to={`/evolution/${run.id}/genomes/${run.best_genome_id}`}
              className="underline font-semibold"
            >
              Inspect →
            </Link>
          )}
        </span>
      );
      toneClass =
        'border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-800 dark:text-emerald-300';
    } else if (validateDecision) {
      icon = <XCircle className="w-5 h-5" />;
      title = 'No genome cleared Gate B';
      detail = (
        <span>
          {failures.length > 0 ? (
            <>
              Best genome failed validation:{' '}
              <span className="font-mono text-xs">
                {failures.slice(0, 2).join(' · ')}
              </span>
              {failures.length > 2 && (
                <span className="text-slate-500"> · +{failures.length - 2} more</span>
              )}
            </>
          ) : (
            'No genome passed the walk-forward validator.'
          )}
        </span>
      );
      toneClass =
        'border-rose-200 dark:border-rose-800 bg-rose-50 dark:bg-rose-900/20 text-rose-800 dark:text-rose-300';
    } else {
      icon = <AlertTriangle className="w-5 h-5" />;
      title = 'Run completed but no validator decision recorded';
      detail = (
        <span>
          The orchestrator finished without running walk-forward validation —
          this usually means the best-genome lookup returned nothing.
        </span>
      );
      toneClass =
        'border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-300';
    }
  }

  return (
    <div className={`rounded-lg border p-4 flex items-start gap-3 ${toneClass}`}>
      <div className="shrink-0 mt-0.5">{icon}</div>
      <div className="min-w-0">
        <p className="font-semibold text-sm">{title}</p>
        <p className="text-sm mt-0.5">{detail}</p>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Generations table — index + fitness + Δ + LLM badge + duration
// ─────────────────────────────────────────────────────────────────────

function GenerationsTable({ generations }: { generations: Generation[] }) {
  if (generations.length === 0) {
    return (
      <p className="text-sm text-slate-500 dark:text-slate-400 italic">
        No generations completed yet.
      </p>
    );
  }
  let lastFitness: number | null = null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400 border-b border-slate-200 dark:border-slate-700">
            <th className="py-2 pr-3">Gen</th>
            <th className="py-2 pr-3">Best fitness</th>
            <th className="py-2 pr-3">Δ vs prev</th>
            <th className="py-2 pr-3">LLM</th>
            <th className="py-2 pr-3">Duration</th>
            <th className="py-2 pr-3">Completed</th>
          </tr>
        </thead>
        <tbody>
          {generations.map((g) => {
            const fitness = g.best_fitness;
            const delta =
              fitness != null && lastFitness != null
                ? fitness - lastFitness
                : null;
            const deltaTone =
              delta == null
                ? 'text-slate-400'
                : delta > 0.01
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : delta < -0.01
                    ? 'text-rose-600 dark:text-rose-400'
                    : 'text-slate-500';
            const durationSecs =
              g.started_at && g.completed_at
                ? Math.max(
                    0,
                    Math.round(
                      (new Date(g.completed_at).getTime() -
                        new Date(g.started_at).getTime()) /
                        1000,
                    ),
                  )
                : null;
            const row = (
              <tr
                key={g.id}
                className="border-b border-slate-100 dark:border-slate-700/50 last:border-0"
              >
                <td className="py-2 pr-3 font-mono text-slate-700 dark:text-slate-300">
                  {g.index}
                </td>
                <td className="py-2 pr-3 font-mono">
                  {fitness != null ? fitness.toFixed(4) : '—'}
                </td>
                <td className={`py-2 pr-3 font-mono ${deltaTone}`}>
                  {delta == null
                    ? '—'
                    : `${delta > 0 ? '+' : ''}${delta.toFixed(4)}`}
                </td>
                <td className="py-2 pr-3">
                  {g.llm_invoked ? (
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300 text-xs font-medium uppercase tracking-wide">
                      LLM
                    </span>
                  ) : (
                    <span className="text-slate-300 dark:text-slate-600">—</span>
                  )}
                </td>
                <td className="py-2 pr-3 text-slate-600 dark:text-slate-400 font-mono text-xs">
                  {durationSecs != null ? `${durationSecs}s` : '—'}
                </td>
                <td className="py-2 pr-3 text-slate-500 dark:text-slate-400 font-mono text-xs">
                  {g.completed_at
                    ? new Date(g.completed_at).toLocaleTimeString()
                    : '—'}
                </td>
              </tr>
            );
            if (fitness != null) lastFitness = fitness;
            return row;
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Generic section wrapper
// ─────────────────────────────────────────────────────────────────────

function Section({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
      <div className="flex items-start justify-between gap-2 mb-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            {title}
          </h2>
          {subtitle && (
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              {subtitle}
            </p>
          )}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components (carryover from previous page)
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
  llm: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
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

// ─────────────────────────────────────────────────────────────────────
// Regime chip (v0.4.0) — surfaces train/OOS regime tags + agreement
// ─────────────────────────────────────────────────────────────────────

function RegimeChip({ decisions }: { decisions: DecisionLogEntry[] }) {
  // The orchestrator logs a `regime_check` decision at run start with the
  // train/OOS region tags + whether they match. The UI surfaces this as a
  // single chip so reviewers can spot regime mismatches without drilling
  // into the decision log.
  const entry = decisions.find((d) => d.action === 'regime_check');
  if (!entry) return null;
  const inputs = (entry.inputs_json ?? {}) as {
    train_region_regime?: string | null;
    oos_region_regime?: string | null;
    regimes_match?: boolean | null;
    auto_stop_enabled?: boolean;
  };
  const train = inputs.train_region_regime;
  const oos = inputs.oos_region_regime;
  if (!train || !oos) return null;

  const match = inputs.regimes_match === true;
  const tone = match
    ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-800 dark:text-emerald-200 border-emerald-200 dark:border-emerald-800'
    : 'bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200 border-amber-200 dark:border-amber-800';
  const icon = match ? '✓' : '⚠';

  return (
    <div
      className={`rounded-lg border px-4 py-3 text-sm flex items-start gap-3 ${tone}`}
    >
      <span className="font-bold text-base leading-none mt-0.5">{icon}</span>
      <div className="flex-1">
        <div className="font-medium">
          {match
            ? 'Training and OOS regimes match'
            : "Training and OOS regimes don't match"}
        </div>
        <div className="text-xs mt-0.5 opacity-80">
          Training region:{' '}
          <span className="font-mono">{train}</span>; OOS region:{' '}
          <span className="font-mono">{oos}</span>.
          {!match && (
            <>
              {' '}
              {inputs.auto_stop_enabled
                ? 'Auto-stop was enabled — the run aborted before generation 0.'
                : 'Auto-stop was off — the run will complete but OOS results are likely brittle. Consider re-running with a data window where the regimes align, or enable auto-stop to skip doomed runs.'}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
