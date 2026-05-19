/**
 * FitnessCurveChart — best-fitness-per-generation line chart.
 *
 * Inline SVG rather than a chart library — the data is small (≤ ~30
 * points), the styling is simple, and adding a chart dep just for one
 * sparkline-style plot isn't worth it.
 *
 * Per-point SVG `<title>` elements give browser-native hover tooltips
 * with no JS — they expose generation index, fitness value, LLM-injection
 * flag, and best-genome origin if known. Vertical markers above LLM
 * injection generations make the "killer feature" of the platform
 * legible at a glance.
 */

import React from 'react';

export interface FitnessCurvePoint {
  /** Generation index (0-indexed, ordered ascending). */
  generation: number;
  /** Best fitness of the population at this generation. Null if not yet evaluated. */
  fitness: number | null;
  /** Did the LLM inject proposals at the *start* of this generation? */
  llmInvoked?: boolean;
  /** Origin of the best genome at this generation (for tooltip context). */
  bestGenomeOrigin?: 'seed' | 'mutation' | 'crossover' | 'llm' | null;
}

interface FitnessCurveChartProps {
  /** One point per generation, oldest-first. */
  points: FitnessCurvePoint[];
  /** Optional target fitness — drawn as a horizontal dashed line. */
  target?: number | null;
  /** Label rendered next to the target line. Defaults to "Gate B target". */
  targetLabel?: string;
  height?: number;
  className?: string;
}

const PADDING = { top: 24, right: 16, bottom: 32, left: 44 };

const ORIGIN_LABELS: Record<NonNullable<FitnessCurvePoint['bestGenomeOrigin']>, string> = {
  seed: 'seed',
  mutation: 'mutation',
  crossover: 'crossover',
  llm: 'LLM-proposed',
};

export default function FitnessCurveChart({
  points,
  target = null,
  targetLabel = 'Gate B target',
  height = 220,
  className = '',
}: FitnessCurveChartProps) {
  if (points.length === 0) {
    return (
      <div
        className={`flex items-center justify-center text-sm text-slate-500 ${className}`}
        style={{ height }}
      >
        No fitness data yet — generation 0 is still evaluating.
      </div>
    );
  }

  const numeric = points.map((p) =>
    p.fitness == null || !Number.isFinite(p.fitness) ? null : p.fitness,
  );
  const definedValues = numeric.filter((v): v is number => v !== null);

  if (definedValues.length === 0) {
    return (
      <div
        className={`flex items-center justify-center text-sm text-slate-500 ${className}`}
        style={{ height }}
      >
        No finite fitness values yet.
      </div>
    );
  }

  const minRaw = Math.min(...definedValues, target ?? Infinity);
  const maxRaw = Math.max(...definedValues, target ?? -Infinity);
  const range = Math.max(maxRaw - minRaw, 0.5);
  const yMin = minRaw - range * 0.1;
  const yMax = maxRaw + range * 0.1;

  const VIEW_W = 600;
  const VIEW_H = height;
  const plotW = VIEW_W - PADDING.left - PADDING.right;
  const plotH = VIEW_H - PADDING.top - PADDING.bottom;

  const xFor = (i: number) =>
    PADDING.left + (i / Math.max(points.length - 1, 1)) * plotW;
  const yFor = (v: number) =>
    PADDING.top + (1 - (v - yMin) / (yMax - yMin)) * plotH;

  // Build polyline segments, splitting on gaps (null values).
  const segments: string[] = [];
  let currentSegment: string[] = [];
  numeric.forEach((v, i) => {
    if (v == null) {
      if (currentSegment.length > 0) {
        segments.push(currentSegment.join(' '));
        currentSegment = [];
      }
    } else {
      currentSegment.push(`${xFor(i)},${yFor(v)}`);
    }
  });
  if (currentSegment.length > 0) segments.push(currentSegment.join(' '));

  const yTicks = Array.from({ length: 4 }, (_, i) => {
    const v = yMin + (i / 3) * (yMax - yMin);
    return { v, y: yFor(v) };
  });

  const llmInjectionIndices = points
    .map((p, i) => (p.llmInvoked ? i : -1))
    .filter((i) => i >= 0);

  const hasTarget =
    target !== null && target !== undefined && Number.isFinite(target);

  return (
    <div className={className}>
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        preserveAspectRatio="none"
        className="w-full"
        style={{ height }}
        role="img"
        aria-label="Best fitness per generation"
      >
        {/* Grid + Y-axis labels */}
        {yTicks.map((t, i) => (
          <g key={`tick-${i}`}>
            <line
              x1={PADDING.left}
              x2={VIEW_W - PADDING.right}
              y1={t.y}
              y2={t.y}
              className="stroke-slate-200 dark:stroke-slate-700"
              strokeWidth={1}
            />
            <text
              x={PADDING.left - 6}
              y={t.y}
              textAnchor="end"
              dominantBaseline="middle"
              className="fill-slate-500 dark:fill-slate-400"
              fontSize="10"
            >
              {t.v.toFixed(2)}
            </text>
          </g>
        ))}

        {/* X-axis ticks — first, mid, last */}
        {[0, Math.floor((points.length - 1) / 2), points.length - 1]
          .filter((i, idx, arr) => arr.indexOf(i) === idx)
          .map((i) => (
            <text
              key={`xtick-${i}`}
              x={xFor(i)}
              y={VIEW_H - PADDING.bottom + 14}
              textAnchor="middle"
              className="fill-slate-500 dark:fill-slate-400"
              fontSize="10"
            >
              {points[i].generation}
            </text>
          ))}

        <text
          x={VIEW_W / 2}
          y={VIEW_H - 4}
          textAnchor="middle"
          className="fill-slate-500 dark:fill-slate-400"
          fontSize="10"
        >
          Generation
        </text>

        {/* LLM injection markers — vertical bands behind the line */}
        {llmInjectionIndices.map((i) => {
          const x = xFor(i);
          return (
            <g key={`llm-${i}`}>
              <line
                x1={x}
                x2={x}
                y1={PADDING.top}
                y2={VIEW_H - PADDING.bottom}
                className="stroke-violet-400 dark:stroke-violet-500"
                strokeWidth={1.5}
                strokeDasharray="2 3"
                opacity={0.7}
              >
                <title>{`LLM injection at gen ${points[i].generation}`}</title>
              </line>
              <text
                x={x}
                y={PADDING.top - 6}
                textAnchor="middle"
                className="fill-violet-600 dark:fill-violet-400"
                fontSize="9"
                fontWeight={600}
              >
                LLM
              </text>
            </g>
          );
        })}

        {/* Target line with label */}
        {hasTarget && (
          <g>
            <line
              x1={PADDING.left}
              x2={VIEW_W - PADDING.right}
              y1={yFor(target as number)}
              y2={yFor(target as number)}
              className="stroke-amber-500"
              strokeWidth={1.5}
              strokeDasharray="4 3"
            />
            <text
              x={VIEW_W - PADDING.right - 4}
              y={yFor(target as number) - 4}
              textAnchor="end"
              className="fill-amber-700 dark:fill-amber-400"
              fontSize="10"
              fontWeight={600}
            >
              {targetLabel} ({(target as number).toFixed(2)})
            </text>
          </g>
        )}

        {/* Fitness polyline(s) */}
        {segments.map((segPoints, i) => (
          <polyline
            key={`seg-${i}`}
            points={segPoints}
            fill="none"
            className="stroke-blue-600 dark:stroke-blue-400"
            strokeWidth={2}
          />
        ))}

        {/* Data points with hover tooltips */}
        {numeric.map((v, i) => {
          if (v == null) return null;
          const point = points[i];
          const originLabel = point.bestGenomeOrigin
            ? ORIGIN_LABELS[point.bestGenomeOrigin]
            : 'unknown origin';
          const tipParts = [
            `Gen ${point.generation}`,
            `best fitness ${v.toFixed(3)}`,
            `best genome: ${originLabel}`,
          ];
          if (point.llmInvoked) tipParts.push('LLM injected here');
          return (
            <circle
              key={`pt-${i}`}
              cx={xFor(i)}
              cy={yFor(v)}
              r={point.llmInvoked ? 4.5 : 3.5}
              className={
                point.llmInvoked
                  ? 'fill-violet-600 dark:fill-violet-400 stroke-white dark:stroke-slate-800'
                  : 'fill-blue-600 dark:fill-blue-400'
              }
              strokeWidth={point.llmInvoked ? 1.5 : 0}
            >
              <title>{tipParts.join(' · ')}</title>
            </circle>
          );
        })}
      </svg>

      {/* Legend + caption */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600 dark:text-slate-400">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block w-3 h-0.5 bg-blue-600 dark:bg-blue-400" />
          Best fitness per gen
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block w-2.5 h-2.5 rounded-full bg-violet-600 dark:bg-violet-400 border border-white dark:border-slate-800" />
          LLM injection gen
        </span>
        {hasTarget && (
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block w-3 h-0"
              style={{ borderTop: '1.5px dashed rgb(245 158 11)' }}
            />
            {targetLabel}
          </span>
        )}
        <span className="ml-auto italic">
          Higher is better. Hover any point for details.
        </span>
      </div>
    </div>
  );
}
