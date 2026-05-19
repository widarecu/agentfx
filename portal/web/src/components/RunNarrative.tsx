/**
 * RunNarrative — generates a short story of an evolution run.
 *
 * Takes the raw decision_log + generations and emits 5–10 bullets that
 * tell the user what the loop *did* in plain English. The aim is "open
 * the page and understand the run in under 30 seconds", which means the
 * narrative answers:
 *
 *   - How did the run start (seed population shape)?
 *   - Where did fitness improvements come from (mutation vs crossover
 *     vs LLM)?
 *   - What did LLM injections produce (how many proposals, did they
 *     help)?
 *   - How did the run end (stop reason)?
 *   - Did the validator pass on the run-best genome?
 *
 * This is pure derived state — no API calls, no React state, just
 * derivation from props.
 */

import React from 'react';
import { Sparkles, Dna, FlaskConical, Flag, AlertCircle, Lightbulb } from 'lucide-react';

import type {
  DecisionLogEntry,
  EvolutionRun,
  Generation,
} from '../types/evolution';

interface RunNarrativeProps {
  run: EvolutionRun;
  generations: Generation[];
  decisions: DecisionLogEntry[];
}

type BulletKind = 'init' | 'gen-improvement' | 'llm' | 'validate' | 'stop';

interface NarrativeBullet {
  kind: BulletKind;
  generation?: number;
  text: string;
}

const KIND_ICONS: Record<BulletKind, React.ComponentType<{ className?: string }>> = {
  init: Dna,
  'gen-improvement': Sparkles,
  llm: Lightbulb,
  validate: FlaskConical,
  stop: Flag,
};

const KIND_COLORS: Record<BulletKind, string> = {
  init: 'text-slate-500 dark:text-slate-400',
  'gen-improvement': 'text-emerald-600 dark:text-emerald-400',
  llm: 'text-violet-600 dark:text-violet-400',
  validate: 'text-blue-600 dark:text-blue-400',
  stop: 'text-amber-600 dark:text-amber-400',
};

function findDecisionByAction(
  decisions: DecisionLogEntry[],
  action: string,
): DecisionLogEntry | undefined {
  return decisions.find((d) => d.action === action);
}

function findDecisionsByAction(
  decisions: DecisionLogEntry[],
  action: string,
): DecisionLogEntry[] {
  return decisions.filter((d) => d.action === action);
}

function fmt(n: number, places = 2): string {
  return n.toFixed(places);
}

function pct(a: number, b: number): string {
  if (b === 0 || !Number.isFinite(b)) return '—';
  return `${((a - b) / Math.abs(b) * 100).toFixed(1)}%`;
}

function buildBullets(
  run: EvolutionRun,
  generations: Generation[],
  decisions: DecisionLogEntry[],
): NarrativeBullet[] {
  const bullets: NarrativeBullet[] = [];
  const sortedGens = [...generations].sort((a, b) => a.index - b.index);

  // 1. Initialisation — from init_population decision if present
  const init = findDecisionByAction(decisions, 'init_population');
  if (init?.reasoning_text) {
    bullets.push({
      kind: 'init',
      generation: 0,
      text: init.reasoning_text,
    });
  } else if (sortedGens.length > 0) {
    const popSize =
      (run.config_json as Record<string, unknown>)?.population_size ?? '?';
    bullets.push({
      kind: 'init',
      generation: 0,
      text: `Initialised generation 0 with a population of ${popSize} genomes.`,
    });
  }

  // 2. Per-generation improvements — only call out generations where
  //    best_fitness moved meaningfully. Stack 4+ gens of "no change"
  //    into a single "plateau" bullet to keep the narrative short.
  const fitnesses = sortedGens
    .map((g) => g.best_fitness)
    .map((f) => (f == null || !Number.isFinite(f) ? null : f));

  let plateauStart: number | null = null;
  let lastBest: number | null = null;
  sortedGens.forEach((g, i) => {
    const f = fitnesses[i];
    if (f == null) return;

    if (lastBest == null) {
      lastBest = f;
      return;
    }

    const delta = f - lastBest;
    const meaningful = Math.abs(delta) >= 0.5; // 0.5 sharpe units is a real move

    if (meaningful) {
      // Close any open plateau before reporting the improvement
      if (plateauStart !== null) {
        const plateauEnd = i - 1;
        if (plateauEnd - plateauStart >= 2) {
          bullets.push({
            kind: 'gen-improvement',
            generation: plateauStart,
            text: `Gens ${plateauStart}–${plateauEnd}: fitness plateaued around ${fmt(lastBest)}.`,
          });
        }
        plateauStart = null;
      }

      const trigger = g.llm_invoked ? 'LLM injection at this gen' : 'GA mutation/crossover';
      const direction = delta > 0 ? 'improved' : 'regressed';
      bullets.push({
        kind: g.llm_invoked ? 'llm' : 'gen-improvement',
        generation: g.index,
        text: `Gen ${g.index}: best fitness ${direction} from ${fmt(lastBest)} → ${fmt(f)} (${pct(f, lastBest)}) via ${trigger}.`,
      });
      lastBest = f;
    } else {
      // No meaningful change — start/continue a plateau
      if (plateauStart === null) plateauStart = g.index;
    }
  });
  if (plateauStart !== null && plateauStart < (sortedGens[sortedGens.length - 1]?.index ?? 0)) {
    bullets.push({
      kind: 'gen-improvement',
      generation: plateauStart,
      text: `Gens ${plateauStart}–${sortedGens[sortedGens.length - 1].index}: fitness plateaued around ${fmt(lastBest ?? 0)}.`,
    });
  }

  // 3. LLM activity summary — one bullet per injection that we haven't
  //    already credited with an improvement.
  const injections = findDecisionsByAction(decisions, 'inject');
  injections.forEach((injection) => {
    const outputs = (injection.outputs_json ?? {}) as Record<string, unknown>;
    const nProposals = typeof outputs.n_proposals === 'number' ? outputs.n_proposals : null;
    const cost = typeof outputs.call_cost_usd === 'number' ? outputs.call_cost_usd : null;
    const skipped = typeof outputs.skipped_reason === 'string' ? outputs.skipped_reason : null;
    // Find which gen this injection was for via the generation_id
    const genIdMatch = injection.generation_id?.match(/_(\d+)$/);
    const gen = genIdMatch ? parseInt(genIdMatch[1], 10) : undefined;

    // Skip if we already recorded an LLM improvement for this gen.
    const alreadyHasLlmImprovement = bullets.some(
      (b) => b.kind === 'llm' && b.generation === gen,
    );
    if (alreadyHasLlmImprovement) return;

    if (skipped) {
      bullets.push({
        kind: 'llm',
        generation: gen,
        text: `Gen ${gen}: LLM injection skipped (${skipped}).`,
      });
    } else if (nProposals != null) {
      bullets.push({
        kind: 'llm',
        generation: gen,
        text: `Gen ${gen}: LLM proposed ${nProposals} new genome${nProposals === 1 ? '' : 's'}${
          cost != null ? ` ($${cost.toFixed(4)} this call)` : ''
        }.`,
      });
    }
  });

  // 4. Validator outcome — terminal verdict on the run-best
  const validate = findDecisionByAction(decisions, 'validate');
  if (validate) {
    const outputs = (validate.outputs_json ?? {}) as Record<string, unknown>;
    const passed = Boolean(outputs.validator_passed);
    const failures = Array.isArray(outputs.validation_failures)
      ? (outputs.validation_failures as string[])
      : [];
    if (passed) {
      bullets.push({
        kind: 'validate',
        text: `Walk-forward validation PASSED on the run-best genome — ready for human review.`,
      });
    } else {
      const failureSummary = failures.length > 0 ? ` (${failures[0]}${failures.length > 1 ? ` +${failures.length - 1} more` : ''})` : '';
      bullets.push({
        kind: 'validate',
        text: `Walk-forward validation FAILED on the run-best genome${failureSummary}. No genome promoted.`,
      });
    }
  }

  // 5. Stop reason
  if (run.stop_reason) {
    const stopLabels: Record<string, string> = {
      max_generations: 'Run completed all configured generations.',
      max_wall_clock: 'Run hit its wall-clock budget.',
      target_reached: 'Run reached the target fitness — early stop.',
      no_improvement: 'Run stopped after no improvement for several generations.',
      llm_budget_exhausted: 'Run stopped — LLM cost cap reached.',
      manual_stop: 'Run was stopped manually.',
    };
    bullets.push({
      kind: 'stop',
      text: stopLabels[run.stop_reason] ?? `Run ended: ${run.stop_reason}.`,
    });
  }

  return bullets;
}

export default function RunNarrative({
  run,
  generations,
  decisions,
}: RunNarrativeProps) {
  const bullets = React.useMemo(
    () => buildBullets(run, generations, decisions),
    [run, generations, decisions],
  );

  if (bullets.length === 0) {
    return (
      <p className="text-sm text-slate-500 dark:text-slate-400 italic">
        Run hasn't produced enough decisions yet for a narrative.
      </p>
    );
  }

  return (
    <ol className="space-y-2">
      {bullets.map((b, i) => {
        const Icon = KIND_ICONS[b.kind];
        return (
          <li key={i} className="flex items-start gap-2.5 text-sm">
            <Icon
              className={`w-4 h-4 mt-0.5 shrink-0 ${KIND_COLORS[b.kind]}`}
              aria-hidden
            />
            <span className="text-slate-700 dark:text-slate-300">
              {b.text}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
