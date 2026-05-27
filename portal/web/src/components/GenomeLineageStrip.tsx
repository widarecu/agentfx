/**
 * GenomeLineageStrip — horizontal breadcrumb of a genome's lineage.
 *
 * Shows: [parent(s)] → [this genome] → [child(ren)]
 *
 * Each node is a clickable badge with the short genome id, origin badge,
 * fitness, and (for the current genome) a "you are here" visual. Click a
 * parent or child to navigate to that genome's detail page.
 *
 * Designed for the GenomeDetail page hero region. For the v0.3.0 ship,
 * this is the lighter-weight alternative to the full lineage DAG
 * (Tier 3); if the user finds it sufficient, the DAG stays as a
 * follow-up.
 */

import React from 'react';
import { Link } from 'react-router-dom';
import { ChevronRight } from 'lucide-react';

import type { GenomeOrigin } from '../types/evolution';

interface LineageNode {
  id: string;
  origin?: GenomeOrigin | null;
  fitness?: number | null;
  generationIndex?: number | null;
}

interface GenomeLineageStripProps {
  /** Run id — used to construct navigation links. */
  runId: string;
  /** Parent genomes — 0 (seed), 1 (mutation/llm), or 2 (crossover). */
  parents: LineageNode[];
  /** The currently-displayed genome (rendered as the centre node). */
  current: LineageNode;
  /** Direct descendants of the current genome. */
  children: LineageNode[];
}

const ORIGIN_BADGE_COLORS: Record<GenomeOrigin, string> = {
  seed: 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300',
  warm_start:
    'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  mutation:
    'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
  crossover:
    'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  llm: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300',
};

function shortId(id: string): string {
  // genome_gen_evorun_20260513_..._005_014 → 005/014
  const parts = id.split('_');
  if (parts.length >= 2) {
    return `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
  }
  return id;
}

function GenomeBadge({
  runId,
  node,
  current,
}: {
  runId: string;
  node: LineageNode;
  current?: boolean;
}) {
  const originClass = node.origin
    ? ORIGIN_BADGE_COLORS[node.origin]
    : 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-300';

  const Inner = (
    <div
      className={`inline-flex flex-col items-start gap-0.5 rounded-md border px-2 py-1.5 transition ${
        current
          ? 'border-blue-500 dark:border-blue-400 bg-blue-50 dark:bg-blue-950/40 ring-2 ring-blue-200 dark:ring-blue-900/50'
          : 'border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 hover:border-slate-300 dark:hover:border-slate-600'
      }`}
      title={node.id}
    >
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-xs text-slate-700 dark:text-slate-300">
          {shortId(node.id)}
        </span>
        {node.origin && (
          <span
            className={`inline-flex items-center px-1 py-0 rounded text-[10px] font-medium uppercase tracking-wide ${originClass}`}
          >
            {node.origin}
          </span>
        )}
      </div>
      <div className="text-[10px] text-slate-500 dark:text-slate-400 flex items-center gap-1.5">
        {node.generationIndex != null && (
          <span>gen {node.generationIndex}</span>
        )}
        {node.fitness != null && Number.isFinite(node.fitness) && (
          <span className="font-mono">f={node.fitness.toFixed(2)}</span>
        )}
      </div>
    </div>
  );

  if (current) return Inner;
  return (
    <Link to={`/evolution/${runId}/genomes/${node.id}`}>{Inner}</Link>
  );
}

function Arrow() {
  return (
    <ChevronRight className="w-4 h-4 text-slate-400 dark:text-slate-500 shrink-0" />
  );
}

export default function GenomeLineageStrip({
  runId,
  parents,
  current,
  children,
}: GenomeLineageStripProps) {
  const hasParents = parents.length > 0;
  const hasChildren = children.length > 0;

  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/30 p-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-2">
        Lineage
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {hasParents ? (
          <>
            <div className="flex flex-col gap-1.5">
              {parents.map((p) => (
                <GenomeBadge key={p.id} runId={runId} node={p} />
              ))}
            </div>
            <Arrow />
          </>
        ) : (
          <span className="text-xs text-slate-400 dark:text-slate-500 italic mr-1">
            (no parents — seed or initialised at random)
          </span>
        )}

        <GenomeBadge runId={runId} node={current} current />

        {hasChildren && (
          <>
            <Arrow />
            <div className="flex flex-wrap gap-1.5">
              {children.slice(0, 8).map((c) => (
                <GenomeBadge key={c.id} runId={runId} node={c} />
              ))}
              {children.length > 8 && (
                <span className="text-xs text-slate-500 dark:text-slate-400 self-center">
                  +{children.length - 8} more
                </span>
              )}
            </div>
          </>
        )}
        {!hasChildren && (
          <span className="text-xs text-slate-400 dark:text-slate-500 italic">
            (no children — last generation or did not survive)
          </span>
        )}
      </div>
    </div>
  );
}
