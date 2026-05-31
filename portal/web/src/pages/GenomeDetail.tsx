/**
 * Genome Detail Page (v0.3.0).
 *
 * Top-to-bottom layout, designed to answer "why did this specific
 * genome win/lose?" in a single scroll:
 *
 *   1. Hero — id, origin badge, validator verdict + plain-English DSL
 *      summary so the user immediately knows what the strategy *does*.
 *   2. Lineage strip — clickable horizontal chain of parents → this →
 *      children. The lighter alternative to the full lineage tree (Tier 3).
 *   3. Mutation diff — for each parent, fetched on-demand and diffed
 *      against this genome. Highlights what changed and (if origin='llm')
 *      surfaces the LLM's reasoning.
 *   4. Walk-forward validation — per-fold metrics with per-threshold
 *      Gate B coloring.
 *   5. Strategy DSL — full tree (the original DslTree component).
 *
 * Parent specs are fetched client-side via the existing
 * `getGenome(runId, parentId)` call (no backend change).
 */

import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  BarChart3,
  ChevronLeft,
  ChevronRight,
  Loader2,
  AlertTriangle,
} from 'lucide-react';

import DslTree from '../components/DslTree';
import GenomeLineageStrip from '../components/GenomeLineageStrip';
import LineageExplainer from '../components/LineageExplainer';
import WalkForwardFoldTable from '../components/WalkForwardFoldTable';
import { getEvolutionRun, getGenome } from '../services/evolutionApi';
import {
  archetypeTooltip,
  inferArchetype,
  summarizeDsl,
} from '../utils/dslSummary';
import type {
  EvolutionRun,
  GenomeOrigin,
  StrategyGenome,
} from '../types/evolution';

const ORIGIN_BADGE_COLORS: Record<GenomeOrigin, string> = {
  seed: 'bg-slate-200 text-slate-800 dark:bg-slate-600 dark:text-slate-100',
  warm_start:
    'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  crossover:
    'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  mutation:
    'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  llm: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
};

const ARCHETYPE_LABELS: Record<string, string> = {
  trend: 'Trend-following',
  'mean-reversion': 'Mean-reverting',
  breakout: 'Breakout',
  mixed: 'Mixed-signal',
  degenerate: 'Degenerate (no trades)',
};

export default function GenomeDetail() {
  const { runId, genomeId } = useParams<{ runId: string; genomeId: string }>();
  const navigate = useNavigate();

  const [genome, setGenome] = useState<StrategyGenome | null>(null);
  const [run, setRun] = useState<EvolutionRun | null>(null);
  const [parents, setParents] = useState<StrategyGenome[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [parentError, setParentError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!runId || !genomeId) return;
      setLoading(true);
      setError(null);
      setParents([]);
      setParentError(null);
      try {
        // Fetch run + genome in parallel — run gives us instrument/granularity
        // + data window for the page's "what data was this evaluated on" line.
        const [genomeData, runData] = await Promise.all([
          getGenome(runId, genomeId),
          getEvolutionRun(runId),
        ]);
        if (cancelled) return;
        setGenome(genomeData);
        setRun(runData);
        const data = genomeData;

        // Fetch parents in parallel for the diff section. Failures are
        // non-fatal — diff just doesn't render. The genome page itself
        // is still usable.
        if (data.parent_ids.length > 0) {
          try {
            const fetched = await Promise.all(
              data.parent_ids.map((pid) => getGenome(runId, pid)),
            );
            if (!cancelled) setParents(fetched);
          } catch (perr) {
            if (!cancelled)
              setParentError(
                perr instanceof Error ? perr.message : 'Failed to load parents',
              );
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : 'Failed to load genome',
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [runId, genomeId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
      </div>
    );
  }

  if (error || !genome) {
    return (
      <div className="container mx-auto px-4 py-8">
        <div className="px-4 py-3 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-300">
          {error ?? 'Genome not found'}
        </div>
        <button
          onClick={() => navigate(`/evolution/${runId}`)}
          className="mt-4 inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
        >
          <ChevronLeft className="w-4 h-4" />
          Back to run
        </button>
      </div>
    );
  }

  const summary = summarizeDsl(genome.dsl_json);
  const archetype = inferArchetype(genome.dsl_json);
  const fitnessText =
    genome.fitness != null && Number.isFinite(genome.fitness)
      ? genome.fitness.toFixed(3)
      : '—';

  // Build the "evaluated on …" line from the run config. The fitness on
  // this page is computed against this exact instrument+granularity+window.
  // Prefer the effective window dates (set by the worker at run-start);
  // fall back to the user-pinned window dates; fall back to legacy
  // `data_years` only for ancient runs.
  const evaluatedOnLine = (() => {
    if (!run) return null;
    const cfg = (run.config_json ?? {}) as Record<string, unknown>;
    const startStr =
      (typeof cfg.effective_data_window_start === 'string'
        ? (cfg.effective_data_window_start as string)
        : null) ??
      (typeof cfg.data_window_start === 'string'
        ? (cfg.data_window_start as string)
        : null);
    const endStr =
      (typeof cfg.effective_data_window_end === 'string'
        ? (cfg.effective_data_window_end as string)
        : null) ??
      (typeof cfg.data_window_end === 'string'
        ? (cfg.data_window_end as string)
        : null);
    let windowPart = '';
    if (startStr && endStr) {
      const days = Math.max(
        1,
        Math.round(
          (new Date(endStr).getTime() - new Date(startStr).getTime()) /
            (24 * 3600 * 1000),
        ),
      );
      windowPart =
        days >= 365
          ? ` · ${(days / 365).toFixed(1).replace(/\.0$/, '')}y window`
          : ` · ${days}d window`;
    } else if (typeof cfg.data_years === 'number') {
      windowPart = ` · ${cfg.data_years as number}y window`;
    }
    return `${run.instrument} ${run.granularity}${windowPart}`;
  })();

  return (
    <div className="container mx-auto px-4 py-8 space-y-6">
      <Link
        to={`/evolution/${runId}`}
        className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to run
      </Link>

      {/* ── 1. Hero ─────────────────────────────────────────────── */}
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
        <div className="flex items-start justify-between mb-3 gap-3">
          <div className="min-w-0">
            <h1 className="font-mono text-base text-slate-900 dark:text-slate-100 break-all">
              {genome.id}
            </h1>
            <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
              Generation {genome.generation_index} ·{' '}
              {new Date(genome.created_at).toLocaleString()}
            </p>
            {evaluatedOnLine && (
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                Backtested on{' '}
                <span className="font-semibold text-slate-700 dark:text-slate-300">
                  {evaluatedOnLine}
                </span>
              </p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2 shrink-0">
            <span
              className={`px-2 py-0.5 text-xs uppercase rounded ${ORIGIN_BADGE_COLORS[genome.origin]}`}
            >
              {genome.origin}
            </span>
            {archetype && (
              <span
                className="px-2 py-0.5 text-xs uppercase rounded bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300 cursor-help"
                title={archetypeTooltip(genome.dsl_json, archetype)}
              >
                {ARCHETYPE_LABELS[archetype] ?? archetype}
              </span>
            )}
            {genome.validator_passed === true && (
              <span className="px-2 py-0.5 text-xs uppercase rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
                validated
              </span>
            )}
            {genome.validator_passed === false && (
              <span
                className="px-2 py-0.5 text-xs uppercase rounded bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300"
                title={genome.validation_failures.join('\n')}
              >
                gate B fail
              </span>
            )}
          </div>
        </div>

        {/* Plain-English summary */}
        {summary && (
          <p className="text-sm text-slate-700 dark:text-slate-300 italic border-l-2 border-blue-300 dark:border-blue-700 pl-3 mb-4">
            {summary}
          </p>
        )}

        <div className="mb-4">
          <Link
            to={`/evolution/${runId}/genomes/${genomeId}/backtest`}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium"
          >
            <BarChart3 className="w-4 h-4" />
            View full backtest detail
            <ChevronRight className="w-4 h-4" />
          </Link>
          <span className="ml-3 text-xs text-slate-500 dark:text-slate-400">
            Re-runs the backtest on demand to show every trade, signal, and
            the price chart with markers.
          </span>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm">
          <Stat label="Training fitness" value={fitnessText} />
          <Stat label="Parents" value={String(genome.parent_ids.length)} />
          <Stat label="Children" value={String(genome.children.length)} />
        </div>

        {genome.validation_failures.length > 0 && (
          <div className="mt-4 px-3 py-2 rounded-md bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-800">
            <div className="text-xs font-semibold uppercase tracking-wide text-rose-700 dark:text-rose-300 mb-1">
              Gate B failures
            </div>
            <ul className="text-sm text-rose-700 dark:text-rose-300 list-disc list-inside space-y-0.5">
              {genome.validation_failures.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* ── 2. Lineage strip ─────────────────────────────────────── */}
      {runId && (
        <GenomeLineageStrip
          runId={runId}
          parents={parents.map((p) => ({
            id: p.id,
            origin: p.origin,
            fitness: p.fitness,
            generationIndex: p.generation_index,
          }))}
          current={{
            id: genome.id,
            origin: genome.origin,
            fitness: genome.fitness,
            generationIndex: genome.generation_index,
          }}
          children={genome.children.map((c) => ({
            id: c.id,
            generationIndex: c.generation_index,
          }))}
        />
      )}

      {/* ── 3. Lineage explainer (origin-aware plain-English) ────── */}
      {runId && (genome.origin !== 'mutation' || parents.length > 0) && (
        <LineageExplainer runId={runId} genome={genome} parents={parents} />
      )}

      {parentError && parents.length === 0 && genome.parent_ids.length > 0 && (
        <div className="rounded-md border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 p-3 text-sm text-amber-700 dark:text-amber-300 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>
            Couldn't fetch parent genomes for the lineage explainer ({parentError}).
            The rest of the page is still usable.
          </span>
        </div>
      )}

      {/* ── 4. Walk-forward validation ──────────────────────────── */}
      <Section
        title="Walk-Forward Validation"
        subtitle="Each fold is backtested independently from $10,000 starting balance — values won't sum or average to the Backtest page's tile metrics (which run one continuous backtest with the balance compounding across folds). Cells are colored by whether each metric passes its Gate B threshold independently. Hover any cell for the delta vs threshold."
      >
        <WalkForwardFoldTable folds={genome.validation_folds} />
      </Section>

      {/* ── 5. Full DSL tree ────────────────────────────────────── */}
      <Section title="Strategy DSL (full spec)">
        <DslTree spec={genome.dsl_json} />
      </Section>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </div>
      <div className="mt-0.5 text-slate-900 dark:text-slate-100 font-medium">
        {value}
      </div>
    </div>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
      <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
        {title}
      </h2>
      {subtitle && (
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5 mb-3">
          {subtitle}
        </p>
      )}
      {!subtitle && <div className="mb-3" />}
      {children}
    </section>
  );
}
