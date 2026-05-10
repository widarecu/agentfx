/**
 * FitnessCurveChart — best-fitness-per-generation line chart.
 *
 * Inline SVG rather than a chart library — the data is small (≤ ~30
 * points), the styling is simple, and adding a chart dep just for one
 * sparkline-style plot isn't worth it.
 */

import React from 'react';

interface FitnessCurveChartProps {
  /** Best fitness per generation, oldest-first. `null` for missing values. */
  values: (number | null)[];
  /** Optional target fitness — drawn as a horizontal dashed line. */
  target?: number | null;
  height?: number;
  className?: string;
}

const PADDING = { top: 16, right: 16, bottom: 28, left: 40 };

export default function FitnessCurveChart({
  values,
  target = null,
  height = 200,
  className = '',
}: FitnessCurveChartProps) {
  if (values.length === 0) {
    return (
      <div
        className={`flex items-center justify-center text-sm text-slate-500 ${className}`}
        style={{ height }}
      >
        No fitness data yet — generation 0 is still evaluating.
      </div>
    );
  }

  const numeric = values.map((v) => (v == null || !Number.isFinite(v) ? null : v));
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
  // Add small padding so the line doesn't touch the edges
  const range = Math.max(maxRaw - minRaw, 0.5);
  const yMin = minRaw - range * 0.1;
  const yMax = maxRaw + range * 0.1;

  // SVG dimensions — width is fluid (set via CSS); we use viewBox to scale.
  const VIEW_W = 600;
  const VIEW_H = height;
  const plotW = VIEW_W - PADDING.left - PADDING.right;
  const plotH = VIEW_H - PADDING.top - PADDING.bottom;

  const xFor = (i: number) =>
    PADDING.left + (i / Math.max(values.length - 1, 1)) * plotW;
  const yFor = (v: number) =>
    PADDING.top + (1 - (v - yMin) / (yMax - yMin)) * plotH;

  // Build polyline points only for the defined values; gaps render as
  // separate segments.
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

  // Y-axis ticks — 4 evenly-spaced values
  const yTicks = Array.from({ length: 4 }, (_, i) => {
    const v = yMin + (i / 3) * (yMax - yMin);
    return { v, y: yFor(v) };
  });

  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="none"
      className={`w-full ${className}`}
      style={{ height }}
      role="img"
      aria-label="Best fitness per generation"
    >
      {/* Grid + Y-axis labels */}
      {yTicks.map((t, i) => (
        <g key={i}>
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

      {/* X-axis label (generation count) */}
      <text
        x={VIEW_W / 2}
        y={VIEW_H - 6}
        textAnchor="middle"
        className="fill-slate-500 dark:fill-slate-400"
        fontSize="10"
      >
        Generation
      </text>

      {/* X-axis ticks — first, mid, last */}
      {[0, Math.floor((values.length - 1) / 2), values.length - 1].map((i) => (
        <text
          key={i}
          x={xFor(i)}
          y={VIEW_H - PADDING.bottom + 14}
          textAnchor="middle"
          className="fill-slate-500 dark:fill-slate-400"
          fontSize="10"
        >
          {i}
        </text>
      ))}

      {/* Target line */}
      {target !== null && target !== undefined && Number.isFinite(target) && (
        <line
          x1={PADDING.left}
          x2={VIEW_W - PADDING.right}
          y1={yFor(target)}
          y2={yFor(target)}
          className="stroke-amber-500"
          strokeWidth={1.5}
          strokeDasharray="4 3"
        />
      )}

      {/* Fitness line(s) — one polyline per defined segment */}
      {segments.map((points, i) => (
        <polyline
          key={i}
          points={points}
          fill="none"
          className="stroke-blue-600 dark:stroke-blue-400"
          strokeWidth={2}
        />
      ))}

      {/* Data points */}
      {numeric.map((v, i) =>
        v == null ? null : (
          <circle
            key={i}
            cx={xFor(i)}
            cy={yFor(v)}
            r={3}
            className="fill-blue-600 dark:fill-blue-400"
          />
        ),
      )}
    </svg>
  );
}
