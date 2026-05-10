/**
 * Genome Detail Page (v0.3.0).
 *
 * Sections:
 * - Header (id, origin badge, fitness, validator pass/fail badge)
 * - DSL spec (rendered as a tree, not raw JSON)
 * - LLM reasoning (only shown if origin='llm')
 * - Walk-forward fold table (with OOS row highlighted)
 * - Lineage (parent IDs as links + children as links)
 */

import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, ChevronLeft, Loader2 } from 'lucide-react';

import DslTree from '../components/DslTree';
import WalkForwardFoldTable from '../components/WalkForwardFoldTable';
import { getGenome } from '../services/evolutionApi';
import type {
  GenomeOrigin,
  StrategyGenome,
} from '../types/evolution';

const ORIGIN_BADGE_COLORS: Record<GenomeOrigin, string> = {
  seed: 'bg-slate-200 text-slate-800 dark:bg-slate-600 dark:text-slate-100',
  crossover:
    'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  mutation:
    'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
  llm: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
};

export default function GenomeDetail() {
  const { runId, genomeId } = useParams<{ runId: string; genomeId: string }>();
  const navigate = useNavigate();

  const [genome, setGenome] = useState<StrategyGenome | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!runId || !genomeId) return;
      setLoading(true);
      setError(null);
      try {
        const data = await getGenome(runId, genomeId);
        if (!cancelled) setGenome(data);
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

  const fitnessText =
    genome.fitness != null && Number.isFinite(genome.fitness)
      ? genome.fitness.toFixed(3)
      : '—';

  return (
    <div className="container mx-auto px-4 py-8 space-y-6">
      <Link
        to={`/evolution/${runId}`}
        className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to run
      </Link>

      {/* Header */}
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
        <div className="flex items-start justify-between mb-3">
          <div>
            <h1 className="font-mono text-base text-slate-900 dark:text-slate-100">
              {genome.id}
            </h1>
            <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
              Generation {genome.generation_index} ·{' '}
              {new Date(genome.created_at).toLocaleString()}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`px-2 py-0.5 text-xs uppercase rounded ${ORIGIN_BADGE_COLORS[genome.origin]}`}
            >
              {genome.origin}
            </span>
            {genome.validator_passed === true && (
              <span className="px-2 py-0.5 text-xs uppercase rounded bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300">
                validated
              </span>
            )}
            {genome.validator_passed === false && (
              <span
                className="px-2 py-0.5 text-xs uppercase rounded bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300"
                title={genome.validation_failures.join('\n')}
              >
                gate B fail
              </span>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm">
          <Stat label="Training Sharpe" value={fitnessText} />
          <Stat label="Parents" value={String(genome.parent_ids.length)} />
          <Stat label="Children" value={String(genome.children.length)} />
        </div>

        {genome.validation_failures.length > 0 && (
          <div className="mt-4 px-3 py-2 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800">
            <div className="text-xs font-semibold uppercase tracking-wide text-red-700 dark:text-red-300 mb-1">
              Gate B failures
            </div>
            <ul className="text-sm text-red-700 dark:text-red-300 list-disc list-inside space-y-0.5">
              {genome.validation_failures.map((f, i) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* DSL spec */}
      <Section title="Strategy DSL">
        <DslTree spec={genome.dsl_json} />
      </Section>

      {/* LLM reasoning */}
      {genome.origin === 'llm' && genome.llm_reasoning && (
        <Section title="LLM Reasoning">
          <div className="rounded-lg border border-amber-200 dark:border-amber-900 bg-amber-50 dark:bg-amber-950/20 px-4 py-3 text-sm text-slate-700 dark:text-slate-300 italic">
            "{genome.llm_reasoning}"
            {genome.llm_call_id && (
              <div className="mt-2 not-italic font-mono text-xs text-slate-500">
                call: {genome.llm_call_id}
              </div>
            )}
          </div>
        </Section>
      )}

      {/* Walk-forward folds */}
      <Section title="Walk-Forward Validation">
        <WalkForwardFoldTable folds={genome.validation_folds} />
      </Section>

      {/* Lineage */}
      <Section title="Lineage">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div>
            <div className="text-xs uppercase tracking-wide text-slate-500 mb-2">
              Parents
            </div>
            {genome.parent_ids.length === 0 ? (
              <span className="italic text-slate-400">
                None (seed or direct LLM proposal)
              </span>
            ) : (
              <ul className="space-y-1">
                {genome.parent_ids.map((pid) => (
                  <li key={pid}>
                    <Link
                      to={`/evolution/${runId}/genomes/${pid}`}
                      className="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline break-all"
                    >
                      {pid}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-slate-500 mb-2">
              Children
            </div>
            {genome.children.length === 0 ? (
              <span className="italic text-slate-400">
                None — this genome is a leaf or the run is still ongoing
              </span>
            ) : (
              <ul className="space-y-1">
                {genome.children.map((c) => (
                  <li key={c.id}>
                    <Link
                      to={`/evolution/${runId}/genomes/${c.id}`}
                      className="font-mono text-xs text-blue-600 dark:text-blue-400 hover:underline break-all"
                    >
                      gen {c.generation_index} · {c.id}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
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
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
      <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100 mb-3">
        {title}
      </h2>
      {children}
    </div>
  );
}
