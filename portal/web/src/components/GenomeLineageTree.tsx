/**
 * GenomeLineageTree — full-run lineage DAG.
 *
 * One row per generation (gen 0 at the top, final gen at the bottom),
 * with the population spread out horizontally. Edges connect parent →
 * child. Click a node to navigate to that genome's detail page.
 *
 * Visual encoding:
 *   - Node colour = origin (seed / mutation / crossover / llm)
 *   - Node ring   = best of its generation gets a gold ring
 *   - Node radius = ranked by fitness within its gen (capped)
 *   - Edge colour = inherited from parent's origin; the path from gen 0
 *     to the run-best is rendered in amber to draw the eye
 *
 * Implementation notes:
 *   - Pure SVG, no graph layout library. The DSL is small enough
 *     (≤ ~900 nodes at production scale, ≤ 120 in our test runs) that
 *     a simple "grid by generation × index-in-gen" layout works.
 *   - Pulls all genomes via the lightweight `/genomes` endpoint, then
 *     does layout + render client-side.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';

import { listRunGenomes } from '../services/evolutionApi';
import type {
  GenomeOrigin,
  StrategyGenomeSummary,
} from '../types/evolution';

interface GenomeLineageTreeProps {
  runId: string;
  /** The run-best genome id — its ancestry is highlighted. */
  bestGenomeId?: string | null;
}

const ORIGIN_FILL: Record<GenomeOrigin, string> = {
  seed: '#94a3b8', // slate-400
  mutation: '#3b82f6', // blue-500
  crossover: '#10b981', // emerald-500
  llm: '#8b5cf6', // violet-500
};

const ORIGIN_DARK_FILL: Record<GenomeOrigin, string> = {
  seed: '#cbd5e1', // slate-300
  mutation: '#60a5fa', // blue-400
  crossover: '#34d399', // emerald-400
  llm: '#a78bfa', // violet-400
};

const ROW_HEIGHT = 56;
const PADDING = { top: 32, right: 24, bottom: 24, left: 56 };
const MIN_NODE_R = 5;
const MAX_NODE_R = 11;

interface LayoutNode {
  genome: StrategyGenomeSummary;
  x: number;
  y: number;
  r: number;
  isBestInGen: boolean;
}

function rankFitnessRadius(fitness: number | null, allFitnesses: number[]): number {
  if (fitness == null || !Number.isFinite(fitness)) return MIN_NODE_R;
  const sorted = [...allFitnesses].sort((a, b) => a - b);
  if (sorted.length <= 1) return (MIN_NODE_R + MAX_NODE_R) / 2;
  const idx = sorted.indexOf(fitness);
  const rank = idx / (sorted.length - 1); // 0..1
  return MIN_NODE_R + rank * (MAX_NODE_R - MIN_NODE_R);
}

function traceAncestry(
  bestId: string | null | undefined,
  byId: Map<string, StrategyGenomeSummary>,
): Set<string> {
  const path = new Set<string>();
  if (!bestId) return path;
  const queue: string[] = [bestId];
  while (queue.length > 0) {
    const id = queue.shift()!;
    if (path.has(id)) continue;
    path.add(id);
    const g = byId.get(id);
    if (g) {
      for (const p of g.parent_ids) {
        if (!path.has(p)) queue.push(p);
      }
    }
  }
  return path;
}

export default function GenomeLineageTree({
  runId,
  bestGenomeId,
}: GenomeLineageTreeProps) {
  const navigate = useNavigate();
  const [genomes, setGenomes] = useState<StrategyGenomeSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await listRunGenomes(runId, { limit: 2000 });
        if (!cancelled) setGenomes(data.items);
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Failed to load lineage');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const { layout, edges, bounds, ancestryIds, byId, generations } = useMemo(() => {
    const byId = new Map<string, StrategyGenomeSummary>();
    for (const g of genomes) byId.set(g.id, g);

    // Group genomes by generation
    const genMap = new Map<number, StrategyGenomeSummary[]>();
    for (const g of genomes) {
      const list = genMap.get(g.generation_index) ?? [];
      list.push(g);
      genMap.set(g.generation_index, list);
    }
    const generations = Array.from(genMap.keys()).sort((a, b) => a - b);

    // Determine column width — driven by the largest generation
    const maxPopInGen = Math.max(
      ...Array.from(genMap.values()).map((g) => g.length),
      1,
    );
    const COL_WIDTH = 42;
    const plotW = COL_WIDTH * maxPopInGen;

    // Find best per generation
    const bestPerGen = new Map<number, string>(); // gen -> genome id
    for (const [gen, list] of genMap.entries()) {
      let bestId: string | null = null;
      let bestF = -Infinity;
      for (const g of list) {
        if (
          g.fitness != null &&
          Number.isFinite(g.fitness) &&
          g.fitness > bestF
        ) {
          bestF = g.fitness;
          bestId = g.id;
        }
      }
      if (bestId) bestPerGen.set(gen, bestId);
    }

    // Layout: each generation gets a row; within a row, sort by fitness
    // descending so the best genomes cluster on the left for legibility.
    const layout: LayoutNode[] = [];
    for (const gen of generations) {
      const list = (genMap.get(gen) ?? []).slice().sort((a, b) => {
        const af = a.fitness ?? -Infinity;
        const bf = b.fitness ?? -Infinity;
        return bf - af;
      });
      const allFitnesses = list
        .map((g) => g.fitness)
        .filter((f): f is number => f != null && Number.isFinite(f));
      list.forEach((g, i) => {
        layout.push({
          genome: g,
          x: PADDING.left + i * COL_WIDTH + COL_WIDTH / 2,
          y: PADDING.top + gen * ROW_HEIGHT,
          r: rankFitnessRadius(g.fitness, allFitnesses),
          isBestInGen: bestPerGen.get(gen) === g.id,
        });
      });
    }

    const layoutById = new Map<string, LayoutNode>();
    for (const n of layout) layoutById.set(n.genome.id, n);

    const edges: Array<{
      from: LayoutNode;
      to: LayoutNode;
      ancestry: boolean;
    }> = [];
    const ancestryIds = traceAncestry(bestGenomeId, byId);
    for (const node of layout) {
      for (const pid of node.genome.parent_ids) {
        const parentNode = layoutById.get(pid);
        if (parentNode) {
          edges.push({
            from: parentNode,
            to: node,
            ancestry: ancestryIds.has(pid) && ancestryIds.has(node.genome.id),
          });
        }
      }
    }

    const totalH = PADDING.top + (generations.length - 1) * ROW_HEIGHT + PADDING.bottom + MAX_NODE_R;
    const totalW = PADDING.left + plotW + PADDING.right;

    return {
      layout,
      edges,
      bounds: { w: totalW, h: totalH },
      ancestryIds,
      byId,
      generations,
    };
  }, [genomes, bestGenomeId]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400 py-6">
        <Loader2 className="w-4 h-4 animate-spin" />
        Loading lineage…
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-sm text-rose-700 dark:text-rose-300 italic py-2">
        {error}
      </div>
    );
  }

  if (genomes.length === 0) {
    return (
      <div className="text-sm text-slate-500 dark:text-slate-400 italic py-2">
        No genomes in this run yet.
      </div>
    );
  }

  return (
    <div>
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex items-center gap-1 text-xs text-slate-600 dark:text-slate-300 hover:text-slate-900 dark:hover:text-slate-100 mb-2"
      >
        {expanded ? (
          <ChevronDown className="w-3.5 h-3.5" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5" />
        )}
        {expanded ? 'Hide' : 'Show'} {genomes.length} nodes across{' '}
        {generations.length} generations
      </button>

      {expanded && (
        <>
          <Legend hasBest={!!bestGenomeId} />
          <div className="overflow-x-auto border border-slate-200 dark:border-slate-700 rounded-md bg-slate-50/40 dark:bg-slate-900/30">
            <svg
              width={bounds.w}
              height={bounds.h}
              viewBox={`0 0 ${bounds.w} ${bounds.h}`}
              className="block"
            >
              {/* Generation labels on the left gutter */}
              {generations.map((gen) => (
                <text
                  key={`label-${gen}`}
                  x={PADDING.left - 12}
                  y={PADDING.top + gen * ROW_HEIGHT + 4}
                  textAnchor="end"
                  fontSize={11}
                  className="fill-slate-500 dark:fill-slate-400 font-mono"
                >
                  gen {gen}
                </text>
              ))}

              {/* Edges first so they sit underneath nodes */}
              {edges.map((e, i) => (
                <line
                  key={i}
                  x1={e.from.x}
                  y1={e.from.y}
                  x2={e.to.x}
                  y2={e.to.y}
                  className={
                    e.ancestry
                      ? 'stroke-amber-500 dark:stroke-amber-400'
                      : 'stroke-slate-300 dark:stroke-slate-600'
                  }
                  strokeWidth={e.ancestry ? 1.5 : 0.6}
                  opacity={e.ancestry ? 0.95 : 0.5}
                />
              ))}

              {/* Nodes */}
              {layout.map((n) => {
                const fill = ORIGIN_FILL[n.genome.origin] ?? '#94a3b8';
                const isInAncestry = ancestryIds.has(n.genome.id);
                const isRunBest = n.genome.id === bestGenomeId;
                const titleParts = [
                  n.genome.id,
                  `gen ${n.genome.generation_index}`,
                  `origin: ${n.genome.origin}`,
                  n.genome.fitness != null && Number.isFinite(n.genome.fitness)
                    ? `fitness ${n.genome.fitness.toFixed(3)}`
                    : 'no fitness',
                ];
                if (n.isBestInGen) titleParts.push('best in generation');
                if (isRunBest) titleParts.push('★ RUN BEST');
                return (
                  <g
                    key={n.genome.id}
                    style={{ cursor: 'pointer' }}
                    onClick={() =>
                      navigate(`/evolution/${runId}/genomes/${n.genome.id}`)
                    }
                  >
                    {n.isBestInGen && (
                      <circle
                        cx={n.x}
                        cy={n.y}
                        r={n.r + 3}
                        fill="none"
                        className="stroke-amber-500 dark:stroke-amber-400"
                        strokeWidth={1.5}
                      />
                    )}
                    {isRunBest && (
                      <circle
                        cx={n.x}
                        cy={n.y}
                        r={n.r + 6}
                        fill="none"
                        className="stroke-amber-600 dark:stroke-amber-300"
                        strokeWidth={2}
                      />
                    )}
                    <circle
                      cx={n.x}
                      cy={n.y}
                      r={n.r}
                      fill={fill}
                      className="hover:opacity-100"
                      opacity={isInAncestry || !bestGenomeId ? 1 : 0.55}
                    >
                      <title>{titleParts.join(' · ')}</title>
                    </circle>
                  </g>
                );
              })}
            </svg>
          </div>
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400 italic">
            Hover any node for genome id + fitness + origin. Click to drill in.
            {bestGenomeId &&
              ' Amber edges + ring trace the lineage of the run-best genome back to its gen-0 ancestors.'}
          </p>
        </>
      )}
    </div>
  );
}

function Legend({ hasBest }: { hasBest: boolean }) {
  const items: Array<{ color: string; label: string }> = [
    { color: ORIGIN_FILL.seed, label: 'seed' },
    { color: ORIGIN_FILL.mutation, label: 'mutation' },
    { color: ORIGIN_FILL.crossover, label: 'crossover' },
    { color: ORIGIN_FILL.llm, label: 'LLM-proposed' },
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600 dark:text-slate-400 mb-2">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block w-2.5 h-2.5 rounded-full"
            style={{ backgroundColor: it.color }}
          />
          {it.label}
        </span>
      ))}
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-block w-3 h-3 rounded-full border-2 border-amber-500"
          style={{ backgroundColor: 'transparent' }}
        />
        best of generation
      </span>
      {hasBest && (
        <>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block w-4 h-4 rounded-full border-2 border-amber-600"
              style={{ backgroundColor: 'transparent' }}
            />
            run-best
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block w-3 h-0.5"
              style={{ backgroundColor: '#f59e0b' }}
            />
            ancestry of run-best
          </span>
        </>
      )}
      <span className="ml-auto italic">
        Larger nodes = higher fitness within their generation.
      </span>
    </div>
  );
}
