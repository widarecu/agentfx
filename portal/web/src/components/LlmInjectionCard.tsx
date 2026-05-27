/**
 * LlmInjectionCard — single LLM-injection event rendered as a card.
 *
 * The "LLM activity" panel on the run-detail page is a list of these.
 * Each card answers, for one injection point:
 *
 *   - Which generation was this for?
 *   - How many proposals did the LLM return (and was it skipped)?
 *   - How much did this call cost (this call + cumulative)?
 *   - What did the LLM "say" — its reasoning_text from the orchestrator's
 *     log_decision write.
 *   - Per-proposal detail: archetype + plain-English strategy summary +
 *     fitness + Δ vs incumbent + first lines of the LLM's per-genome
 *     reasoning. Click a row to drill into the GenomeDetail page.
 *
 * Distinct from the generic DecisionRow — this is the killer-feature
 * surface and gets its own visual treatment (violet accent matching the
 * chart's LLM-injection markers).
 */

import React from 'react';
import { Lightbulb, AlertTriangle, DollarSign, ArrowRight } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { DecisionLogEntry, StrategyGenome } from '../types/evolution';
import {
  inferArchetype,
  summarizeDsl,
  type ArchetypeKind,
} from '../utils/dslSummary';

interface LlmInjectionCardProps {
  /** The `action='inject'` decision-log entry for this injection. */
  decision: DecisionLogEntry;
  /** Run ID — needed to build the link to each proposal's GenomeDetail. */
  runId: string;
  /** Full genomes the LLM produced at this injection (empty if not loaded). */
  proposals: StrategyGenome[];
  /** Best fitness in the previous generation. Null at gen 0 or unknown. */
  incumbentFitness: number | null;
}

function extractGen(decision: DecisionLogEntry): number | null {
  const match = decision.generation_id?.match(/_(\d+)$/);
  if (!match) return null;
  return parseInt(match[1], 10);
}

const ARCHETYPE_LABEL: Record<ArchetypeKind, string> = {
  trend: 'Trend-following',
  'mean-reversion': 'Mean-reverting',
  breakout: 'Breakout',
  mixed: 'Mixed-signal',
  degenerate: 'Degenerate',
};

const ARCHETYPE_TONE: Record<ArchetypeKind, string> = {
  trend:
    'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  'mean-reversion':
    'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  breakout:
    'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
  mixed: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
  degenerate:
    'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300',
};

function ProposalRow({
  runId,
  genome,
  incumbentFitness,
}: {
  runId: string;
  genome: StrategyGenome;
  incumbentFitness: number | null;
}) {
  const archetype = inferArchetype(genome.dsl_json);
  const summary = summarizeDsl(genome.dsl_json);
  const fitness = genome.fitness;
  const delta =
    fitness != null && incumbentFitness != null
      ? fitness - incumbentFitness
      : null;
  const beatsIncumbent = delta != null && delta > 0;

  return (
    <Link
      to={`/evolution/${runId}/genomes/${genome.id}`}
      className="block rounded-md border border-violet-100 dark:border-violet-900/40 bg-white dark:bg-slate-900/40 hover:bg-violet-50/60 dark:hover:bg-violet-950/30 px-3 py-2.5 transition-colors"
    >
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center flex-wrap gap-1.5 mb-1">
            {archetype && (
              <span
                className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${ARCHETYPE_TONE[archetype]}`}
              >
                {ARCHETYPE_LABEL[archetype]}
              </span>
            )}
            <span className="text-xs font-mono text-slate-500 dark:text-slate-400 truncate">
              {genome.id.split('_').slice(-2).join('_')}
            </span>
          </div>
          <p className="text-sm text-slate-700 dark:text-slate-200 mb-1">
            {summary}
          </p>
          {genome.llm_reasoning && (
            <p className="text-xs text-slate-500 dark:text-slate-400 italic line-clamp-2">
              {genome.llm_reasoning}
            </p>
          )}
        </div>
        <div className="text-right shrink-0">
          <div
            className={`text-sm font-mono font-semibold ${
              fitness == null
                ? 'text-slate-400'
                : beatsIncumbent
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : 'text-rose-600 dark:text-rose-400'
            }`}
          >
            {fitness == null ? '—' : fitness.toFixed(2)}
          </div>
          {delta != null && (
            <div
              className={`text-[11px] font-mono ${
                beatsIncumbent
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : 'text-rose-500 dark:text-rose-400'
              }`}
            >
              {delta >= 0 ? '+' : ''}
              {delta.toFixed(2)} vs elite
            </div>
          )}
          <ArrowRight className="w-3 h-3 mt-1 ml-auto text-slate-400" />
        </div>
      </div>
    </Link>
  );
}

export default function LlmInjectionCard({
  decision,
  runId,
  proposals,
  incumbentFitness,
}: LlmInjectionCardProps) {
  const outputs = (decision.outputs_json ?? {}) as Record<string, unknown>;
  const nProposals =
    typeof outputs.n_proposals === 'number' ? outputs.n_proposals : null;
  const callCost =
    typeof outputs.call_cost_usd === 'number' ? outputs.call_cost_usd : null;
  const cumulativeCost =
    typeof outputs.cumulative_cost_usd === 'number'
      ? outputs.cumulative_cost_usd
      : null;
  const skipped =
    typeof outputs.skipped_reason === 'string' ? outputs.skipped_reason : null;
  const generation = extractGen(decision);
  const callId =
    typeof decision.llm_call_id === 'string' ? decision.llm_call_id : null;

  const isSkipped = skipped != null;

  // Order proposals by fitness desc — best LLM attempt first.
  const orderedProposals = [...proposals].sort((a, b) => {
    if (a.fitness == null) return 1;
    if (b.fitness == null) return -1;
    return b.fitness - a.fitness;
  });

  const beatCount =
    incumbentFitness != null
      ? orderedProposals.filter(
          (g) => g.fitness != null && g.fitness > incumbentFitness,
        ).length
      : 0;

  return (
    <div
      className={`rounded-lg border p-4 ${
        isSkipped
          ? 'border-amber-200 dark:border-amber-900/50 bg-amber-50/50 dark:bg-amber-900/10'
          : 'border-violet-200 dark:border-violet-900/50 bg-violet-50/50 dark:bg-violet-900/10'
      }`}
    >
      <div className="flex items-start gap-3">
        <div
          className={`p-2 rounded-md shrink-0 ${
            isSkipped
              ? 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300'
              : 'bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300'
          }`}
        >
          {isSkipped ? (
            <AlertTriangle className="w-4 h-4" />
          ) : (
            <Lightbulb className="w-4 h-4" />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-baseline gap-2 mb-1.5">
            <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              {isSkipped
                ? `Injection skipped — gen ${generation ?? '?'}`
                : `LLM injection — gen ${generation ?? '?'}`}
            </span>
            <span className="text-xs text-slate-500 dark:text-slate-400">
              {new Date(decision.created_at).toLocaleString()}
            </span>
          </div>

          {isSkipped ? (
            <p className="text-sm text-amber-700 dark:text-amber-300">
              Skipped reason:{' '}
              <span className="font-mono text-xs">{skipped}</span>
              {skipped === 'budget_exhausted' && (
                <span className="block mt-1 text-xs text-amber-600 dark:text-amber-400 italic">
                  The cost cap was reached; the orchestrator continued without
                  injection.
                </span>
              )}
            </p>
          ) : (
            <>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600 dark:text-slate-400 mb-2">
                <span>
                  <span className="font-semibold text-slate-900 dark:text-slate-100">
                    {nProposals ?? '?'}
                  </span>{' '}
                  proposal{nProposals === 1 ? '' : 's'}
                </span>
                {incumbentFitness != null && orderedProposals.length > 0 && (
                  <span
                    className={
                      beatCount > 0
                        ? 'text-emerald-700 dark:text-emerald-400 font-semibold'
                        : 'text-rose-700 dark:text-rose-400'
                    }
                  >
                    {beatCount}/{orderedProposals.length} beat the elite (
                    {incumbentFitness.toFixed(2)})
                  </span>
                )}
                {callCost != null && (
                  <span className="inline-flex items-center gap-0.5">
                    <DollarSign className="w-3 h-3 opacity-60" />
                    <span className="font-semibold text-slate-900 dark:text-slate-100">
                      {callCost.toFixed(4)}
                    </span>
                    <span className="ml-1 text-slate-500">this call</span>
                  </span>
                )}
                {cumulativeCost != null && (
                  <span className="inline-flex items-center gap-0.5">
                    <span className="font-semibold text-slate-900 dark:text-slate-100">
                      ${cumulativeCost.toFixed(4)}
                    </span>
                    <span className="ml-1 text-slate-500">cumulative</span>
                  </span>
                )}
              </div>

              {decision.reasoning_text && (
                <blockquote className="text-sm italic text-slate-700 dark:text-slate-300 border-l-2 border-violet-300 dark:border-violet-700 pl-3 py-0.5 mb-2">
                  {decision.reasoning_text}
                </blockquote>
              )}

              {orderedProposals.length > 0 ? (
                <div className="mt-3 space-y-2">
                  {orderedProposals.map((g) => (
                    <ProposalRow
                      key={g.id}
                      runId={runId}
                      genome={g}
                      incumbentFitness={incumbentFitness}
                    />
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-xs text-slate-500 dark:text-slate-400 italic">
                  Proposal detail not loaded yet. Refresh the page if this
                  persists.
                </p>
              )}

              {callId && (
                <p className="mt-2 text-xs font-mono text-slate-400 dark:text-slate-500">
                  call_id: {callId}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
