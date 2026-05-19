/**
 * LineageExplainer — explains how a genome came to be, in plain English.
 *
 * Different origin types render different content:
 *
 *   - seed     → one-paragraph explainer
 *   - llm      → explainer + the LLM's own reasoning blockquote
 *   - crossover→ inheritance map (long/short/exit/size from which parent),
 *                with the swapped slot called out + per-parent diffs available
 *                on demand for the curious
 *   - mutation → operator name detected from the diff shape, plus the
 *                one-line description + intent + the field-level diff
 *
 * Designed as the *first* thing the user sees on GenomeDetail after the
 * hero. Replaces the old "Mutation diff" / "Crossover diff" section
 * which showed JSON deltas without context.
 */

import React from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, BookOpen } from 'lucide-react';

import DslDiff from './DslDiff';
import type { GenomeOrigin, StrategyGenome } from '../types/evolution';
import {
  detectCrossoverInheritance,
  detectMutationOperator,
  diffSpecs,
  findSwappedSlot,
  getOperatorInfo,
  type SlotProvenance,
} from '../utils/lineageExplain';

interface LineageExplainerProps {
  runId: string;
  genome: StrategyGenome;
  parents: StrategyGenome[];
}

const SLOT_LABELS: Record<SlotProvenance['slot'], string> = {
  long: 'Long entry rules',
  short: 'Short entry rules',
  exit: 'Exit rules (stop / target / hold / opposite-signal)',
  size: 'Sizing (risk per trade)',
};

export default function LineageExplainer({
  runId,
  genome,
  parents,
}: LineageExplainerProps) {
  const { origin } = genome;

  if (origin === 'seed') {
    const info = getOperatorInfo('seed');
    return (
      <ExplainerCard tone="slate" label={info.label}>
        <Description info={info} />
      </ExplainerCard>
    );
  }

  if (origin === 'llm') {
    const info = getOperatorInfo('llm');
    return (
      <ExplainerCard tone="violet" label={info.label}>
        <Description info={info} />
        {genome.llm_reasoning && (
          <blockquote className="mt-3 italic text-sm text-slate-700 dark:text-slate-300 border-l-2 border-violet-300 dark:border-violet-700 pl-3 py-0.5">
            <span className="not-italic font-semibold text-violet-700 dark:text-violet-400 mr-1">
              LLM said:
            </span>
            "{genome.llm_reasoning}"
            {genome.llm_call_id && (
              <div className="not-italic mt-1.5 font-mono text-[10px] text-slate-500 dark:text-slate-500">
                call_id: {genome.llm_call_id}
              </div>
            )}
          </blockquote>
        )}
      </ExplainerCard>
    );
  }

  if (origin === 'crossover') {
    return <CrossoverExplainer runId={runId} genome={genome} parents={parents} />;
  }

  // mutation
  return <MutationExplainer runId={runId} genome={genome} parents={parents} />;
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────

function CrossoverExplainer({
  runId,
  genome,
  parents,
}: LineageExplainerProps) {
  const info = getOperatorInfo('crossover');

  if (parents.length < 2) {
    return (
      <ExplainerCard tone="emerald" label={info.label}>
        <Description info={info} />
        <p className="text-sm text-slate-500 dark:text-slate-400 mt-2 italic">
          Couldn't load both parents — inheritance map unavailable.
        </p>
      </ExplainerCard>
    );
  }

  const parentIds = parents.map((p) => p.id);
  const inheritance = detectCrossoverInheritance(
    genome.dsl_json,
    parents.map((p) => p.dsl_json),
    parentIds,
  );
  const swapped = findSwappedSlot(inheritance);

  return (
    <ExplainerCard tone="emerald" label={info.label}>
      <Description info={info} />

      <div className="mt-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-2">
          Inheritance map
        </p>
        <ul className="space-y-1.5">
          {inheritance.map((row) => (
            <InheritanceRow
              key={row.slot}
              row={row}
              isSwapped={swapped?.slot === row.slot}
              runId={runId}
            />
          ))}
        </ul>
        {swapped ? (
          <p className="mt-3 text-sm text-slate-700 dark:text-slate-300">
            <span className="font-semibold">Swapped slot:</span>{' '}
            <span className="capitalize">{SLOT_LABELS[swapped.slot].toLowerCase()}</span>{' '}
            was inherited from{' '}
            <Link
              to={`/evolution/${runId}/genomes/${swapped.from}`}
              className="font-mono text-emerald-700 dark:text-emerald-400 hover:underline"
            >
              {shortId(swapped.from)}
            </Link>
            ; everything else came from the other parent.
          </p>
        ) : (
          <p className="mt-3 text-sm text-slate-500 dark:text-slate-400 italic">
            Couldn't determine which slot was swapped — both parents may share
            the same shape for the swapped slot.
          </p>
        )}
      </div>

      <ExpandableJsonDiffs label="View raw JSON diff against each parent">
        {parents.map((p) => (
          <div key={p.id} className="mt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1.5">
              Parent <span className="font-mono normal-case">{shortId(p.id)}</span>
            </p>
            <DslDiff
              parent={p.dsl_json}
              child={genome.dsl_json}
              parentLabel={`Parent ${p.generation_index != null ? `(gen ${p.generation_index})` : ''}`}
              childLabel={`This genome (gen ${genome.generation_index})`}
            />
          </div>
        ))}
      </ExpandableJsonDiffs>
    </ExplainerCard>
  );
}

function MutationExplainer({
  runId,
  genome,
  parents,
}: LineageExplainerProps) {
  if (parents.length === 0) {
    const info = getOperatorInfo('mutation-unknown');
    return (
      <ExplainerCard tone="blue" label={info.label}>
        <Description info={info} />
        <p className="mt-2 text-sm text-slate-500 dark:text-slate-400 italic">
          Couldn't load the parent — diff unavailable.
        </p>
      </ExplainerCard>
    );
  }

  const parent = parents[0];
  const diffs = diffSpecs(parent.dsl_json, genome.dsl_json);
  const opKind = detectMutationOperator(diffs);
  const info = getOperatorInfo(opKind);

  return (
    <ExplainerCard tone="blue" label={info.label}>
      <Description info={info} />

      <div className="mt-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-2">
          What changed from{' '}
          <Link
            to={`/evolution/${runId}/genomes/${parent.id}`}
            className="font-mono normal-case text-blue-700 dark:text-blue-400 hover:underline"
          >
            {shortId(parent.id)}
          </Link>
        </p>
        {diffs.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400 italic">
            No structural differences — the mutation operator may have been a
            no-op on this particular spec.
          </p>
        ) : (
          <ul className="space-y-1 text-sm">
            {diffs.slice(0, 8).map((d) => (
              <li key={d.path} className="flex items-baseline gap-2 flex-wrap">
                <span className="text-slate-700 dark:text-slate-300">
                  {humanizePath(d.path)}:
                </span>
                <span className="font-mono text-xs">
                  <span className="line-through text-slate-400">
                    {fmt(d.before)}
                  </span>
                  <ArrowRight className="inline-block w-3 h-3 mx-1 text-slate-400" />
                  <span className="font-semibold text-blue-700 dark:text-blue-400">
                    {fmt(d.after)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ExpandableJsonDiffs label="View raw JSON diff">
        <div className="mt-3">
          <DslDiff
            parent={parent.dsl_json}
            child={genome.dsl_json}
            parentLabel={`Parent ${parent.generation_index != null ? `(gen ${parent.generation_index})` : ''}`}
            childLabel={`This genome (gen ${genome.generation_index})`}
          />
        </div>
      </ExpandableJsonDiffs>
    </ExplainerCard>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Building blocks
// ─────────────────────────────────────────────────────────────────────

type Tone = 'slate' | 'violet' | 'emerald' | 'blue';

const TONE_CLASSES: Record<Tone, { bg: string; border: string; label: string }> = {
  slate: {
    bg: 'bg-slate-50 dark:bg-slate-900/40',
    border: 'border-slate-200 dark:border-slate-700',
    label: 'text-slate-700 dark:text-slate-300',
  },
  violet: {
    bg: 'bg-violet-50 dark:bg-violet-900/15',
    border: 'border-violet-200 dark:border-violet-900',
    label: 'text-violet-700 dark:text-violet-400',
  },
  emerald: {
    bg: 'bg-emerald-50 dark:bg-emerald-900/15',
    border: 'border-emerald-200 dark:border-emerald-900',
    label: 'text-emerald-700 dark:text-emerald-400',
  },
  blue: {
    bg: 'bg-blue-50 dark:bg-blue-900/15',
    border: 'border-blue-200 dark:border-blue-900',
    label: 'text-blue-700 dark:text-blue-400',
  },
};

function ExplainerCard({
  tone,
  label,
  children,
}: {
  tone: Tone;
  label: string;
  children: React.ReactNode;
}) {
  const t = TONE_CLASSES[tone];
  return (
    <section className={`rounded-lg border p-5 ${t.border} ${t.bg}`}>
      <div className="flex items-center gap-2 mb-3">
        <BookOpen className={`w-4 h-4 ${t.label}`} />
        <h2 className={`text-sm font-semibold uppercase tracking-wide ${t.label}`}>
          How this genome came to be · {label}
        </h2>
      </div>
      {children}
    </section>
  );
}

function Description({ info }: { info: { description: string; intent: string } }) {
  return (
    <div className="space-y-1.5 text-sm">
      <p className="text-slate-700 dark:text-slate-300">{info.description}</p>
      <p className="text-slate-600 dark:text-slate-400 italic">
        <span className="font-semibold not-italic">Why the GA tries this: </span>
        {info.intent}
      </p>
    </div>
  );
}

function InheritanceRow({
  row,
  isSwapped,
  runId,
}: {
  row: SlotProvenance;
  isSwapped: boolean;
  runId: string;
}) {
  return (
    <li
      className={`flex items-center gap-3 text-sm rounded-md px-2 py-1 ${
        isSwapped
          ? 'bg-amber-100 dark:bg-amber-900/30 border border-amber-300 dark:border-amber-800'
          : ''
      }`}
    >
      <span
        className={`shrink-0 w-2 h-2 rounded-full ${
          isSwapped
            ? 'bg-amber-500 dark:bg-amber-400'
            : 'bg-slate-300 dark:bg-slate-600'
        }`}
      />
      <span className="text-slate-700 dark:text-slate-300 flex-1">
        {SLOT_LABELS[row.slot]}
      </span>
      <ArrowRight className="w-3 h-3 text-slate-400" />
      <Link
        to={`/evolution/${runId}/genomes/${row.from}`}
        className="font-mono text-xs text-emerald-700 dark:text-emerald-400 hover:underline"
        title={row.from}
      >
        {shortId(row.from)}
      </Link>
      {isSwapped && (
        <span className="text-[10px] font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400">
          swapped
        </span>
      )}
    </li>
  );
}

function ExpandableJsonDiffs({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className="mt-4 pt-3 border-t border-slate-200 dark:border-slate-700">
      <button
        onClick={() => setOpen((o) => !o)}
        className="text-xs text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 underline"
      >
        {open ? 'Hide' : label}
      </button>
      {open && <div>{children}</div>}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────

function shortId(id: string): string {
  const parts = id.split('_');
  if (parts.length >= 2) {
    return `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
  }
  return id;
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return '∅';
  if (typeof v === 'number') {
    return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return String(v);
}

function humanizePath(path: string): string {
  return path
    .replace(/^entry\.long\./, 'long entry → ')
    .replace(/^entry\.short\./, 'short entry → ')
    .replace(/^entry\.long$/, 'long entry rules')
    .replace(/^entry\.short$/, 'short entry rules')
    .replace(/^exit\./, 'exit ')
    .replace(/^size\./, 'sizing ')
    .replace(/\.cmp$/, 'comparator')
    .replace(/\.lhs$/, 'left side')
    .replace(/\.rhs$/, 'right side')
    .replace(/\.ind$/, 'indicator')
    .replace(/\.args\.\[(\d+)\]/g, ' arg[$1]')
    .replace(/_/g, ' ');
}
