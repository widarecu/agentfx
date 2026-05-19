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
 *   - A link to drill in: the GenomeDetail page filtered to the
 *     genomes this injection produced (lives in v0.4.0; for v0.3.0 the
 *     card links to the gen-N best genome for context).
 *
 * Distinct from the generic DecisionRow — this is the killer-feature
 * surface and gets its own visual treatment (violet accent matching the
 * chart's LLM-injection markers).
 */

import React from 'react';
import { Lightbulb, AlertTriangle, DollarSign } from 'lucide-react';

import type { DecisionLogEntry } from '../types/evolution';

interface LlmInjectionCardProps {
  /** The `action='inject'` decision-log entry for this injection. */
  decision: DecisionLogEntry;
}

function extractGen(decision: DecisionLogEntry): number | null {
  const match = decision.generation_id?.match(/_(\d+)$/);
  if (!match) return null;
  return parseInt(match[1], 10);
}

export default function LlmInjectionCard({ decision }: LlmInjectionCardProps) {
  const outputs = (decision.outputs_json ?? {}) as Record<string, unknown>;
  const nProposals = typeof outputs.n_proposals === 'number' ? outputs.n_proposals : null;
  const callCost = typeof outputs.call_cost_usd === 'number' ? outputs.call_cost_usd : null;
  const cumulativeCost =
    typeof outputs.cumulative_cost_usd === 'number'
      ? outputs.cumulative_cost_usd
      : null;
  const skipped = typeof outputs.skipped_reason === 'string' ? outputs.skipped_reason : null;
  const generation = extractGen(decision);
  const callId =
    typeof decision.llm_call_id === 'string' ? decision.llm_call_id : null;

  const isSkipped = skipped != null;

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
                <blockquote className="text-sm italic text-slate-700 dark:text-slate-300 border-l-2 border-violet-300 dark:border-violet-700 pl-3 py-0.5">
                  {decision.reasoning_text}
                </blockquote>
              )}

              <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
                Tip: scroll to the generations table below and click the
                target gen's "best genome" link to drill into the proposals
                that landed in this injection.
              </p>

              {callId && (
                <p className="mt-1 text-xs font-mono text-slate-400 dark:text-slate-500">
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
