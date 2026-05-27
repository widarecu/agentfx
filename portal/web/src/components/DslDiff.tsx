/**
 * DslDiff — side-by-side comparison of two DSL specs.
 *
 * Used on GenomeDetail when a genome has parents to show:
 *   - A one-line summary of *what* changed: "perturb_threshold on
 *     long.lhs.args[0]: 20.0 → 24.7 (+24%)"
 *   - The full parent spec on the left, child spec on the right, with
 *     diverged fields highlighted in amber.
 *
 * The diff is computed client-side from the two `DslSpec` JSON objects.
 * No backend support needed (per the v0.3.0 UX-fix plan).
 *
 * Limitations of the v1 diff:
 *   - Recognises *leaf* differences (numbers, strings, booleans, indicator
 *     names) but doesn't try to reason about structural reordering
 *     (e.g. swapping two `args` of an AND). Sufficient for v1's mutation
 *     operators since they all preserve structural shape.
 */

import React from 'react';

import type { DslSpec } from '../types/evolution';

interface DslDiffProps {
  parent: DslSpec;
  child: DslSpec;
  parentLabel?: string;
  childLabel?: string;
  className?: string;
}

interface LeafDiff {
  path: string;
  before: unknown;
  after: unknown;
}

function flatten(
  obj: unknown,
  prefix: string,
  out: Map<string, unknown>,
): void {
  if (obj === null || obj === undefined) {
    out.set(prefix || '$', obj);
    return;
  }
  if (Array.isArray(obj)) {
    obj.forEach((v, i) => flatten(v, `${prefix}[${i}]`, out));
    return;
  }
  if (typeof obj === 'object') {
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
      const next = prefix ? `${prefix}.${k}` : k;
      flatten(v, next, out);
    }
    return;
  }
  out.set(prefix || '$', obj);
}

function computeLeafDiffs(parent: DslSpec, child: DslSpec): LeafDiff[] {
  const parentLeaves = new Map<string, unknown>();
  const childLeaves = new Map<string, unknown>();
  flatten(parent, '', parentLeaves);
  flatten(child, '', childLeaves);

  const allPaths = new Set<string>([
    ...parentLeaves.keys(),
    ...childLeaves.keys(),
  ]);
  const diffs: LeafDiff[] = [];
  for (const path of allPaths) {
    const before = parentLeaves.get(path);
    const after = childLeaves.get(path);
    if (JSON.stringify(before) !== JSON.stringify(after)) {
      diffs.push({ path, before, after });
    }
  }
  return diffs;
}

function formatLeaf(v: unknown): string {
  if (v === null || v === undefined) return '∅';
  if (typeof v === 'number') return v.toFixed(2).replace(/\.00$/, '');
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  return String(v);
}

function pctChange(before: unknown, after: unknown): string | null {
  if (
    typeof before !== 'number' ||
    typeof after !== 'number' ||
    before === 0 ||
    !Number.isFinite(before)
  )
    return null;
  const pct = ((after - before) / Math.abs(before)) * 100;
  return `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`;
}

/** Short human-readable label for a JSON path, e.g. "long entry threshold". */
function humanizePath(path: string): string {
  return path
    .replace(/^entry\./, '')
    .replace(/\.cmp$/, ' comparator')
    .replace(/\.lhs/, ' left side')
    .replace(/\.rhs/, ' right side')
    .replace(/\.ind$/, ' indicator')
    .replace(/\.args\[(\d+)\]/g, ' arg[$1]')
    .replace(/^exit\./, 'exit ')
    .replace(/^size\./, 'sizing ')
    .replace(/_/g, ' ');
}

export default function DslDiff({
  parent,
  child,
  parentLabel = 'Parent',
  childLabel = 'This genome',
  className = '',
}: DslDiffProps) {
  const diffs = React.useMemo(
    () => computeLeafDiffs(parent, child),
    [parent, child],
  );

  if (diffs.length === 0) {
    return (
      <div
        className={`rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/30 p-3 text-sm text-slate-600 dark:text-slate-400 italic ${className}`}
      >
        No structural differences between {parentLabel.toLowerCase()} and{' '}
        {childLabel.toLowerCase()}.
      </div>
    );
  }

  return (
    <div className={className}>
      {/* Summary line — most prominent diff first */}
      <div className="rounded-md bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 p-3 mb-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-400 mb-1.5">
          {diffs.length} change{diffs.length === 1 ? '' : 's'} from{' '}
          {parentLabel.toLowerCase()}
        </div>
        <ul className="space-y-1 text-sm">
          {diffs.map((d) => {
            const pct = pctChange(d.before, d.after);
            return (
              <li key={d.path} className="flex items-baseline gap-2 flex-wrap">
                <span className="text-amber-900 dark:text-amber-200">
                  {humanizePath(d.path)}:
                </span>
                <span className="font-mono text-xs">
                  <span className="line-through text-slate-500">
                    {formatLeaf(d.before)}
                  </span>{' '}
                  <span className="text-amber-700 dark:text-amber-300">→</span>{' '}
                  <span className="font-semibold text-amber-900 dark:text-amber-200">
                    {formatLeaf(d.after)}
                  </span>
                  {pct && (
                    <span className="ml-1.5 text-xs text-amber-600 dark:text-amber-400">
                      ({pct})
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      </div>

      {/* Side-by-side JSON view */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono">
        <SpecPane
          label={parentLabel}
          spec={parent}
          diffPaths={new Set(diffs.map((d) => d.path))}
          tone="parent"
        />
        <SpecPane
          label={childLabel}
          spec={child}
          diffPaths={new Set(diffs.map((d) => d.path))}
          tone="child"
        />
      </div>
    </div>
  );
}

function SpecPane({
  label,
  spec,
  diffPaths,
  tone,
}: {
  label: string;
  spec: DslSpec;
  diffPaths: Set<string>;
  tone: 'parent' | 'child';
}) {
  const labelTone =
    tone === 'parent'
      ? 'text-slate-500 dark:text-slate-400'
      : 'text-amber-700 dark:text-amber-400';

  return (
    <div className="rounded-md border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-900/40 overflow-hidden">
      <div
        className={`px-3 py-1.5 text-xs font-semibold uppercase tracking-wide border-b border-slate-200 dark:border-slate-700 ${labelTone}`}
      >
        {label}
      </div>
      <pre className="p-3 overflow-x-auto text-slate-700 dark:text-slate-300">
        {renderHighlighted(spec, '', diffPaths)}
      </pre>
    </div>
  );
}

function renderHighlighted(
  value: unknown,
  path: string,
  diffPaths: Set<string>,
  indent = 0,
): React.ReactNode {
  const pad = '  '.repeat(indent);

  if (value === null) return <span>null</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span>[]</span>;
    return (
      <span>
        {'[\n'}
        {value.map((v, i) => (
          <React.Fragment key={i}>
            {pad}
            {'  '}
            {renderHighlighted(
              v,
              `${path}[${i}]`,
              diffPaths,
              indent + 1,
            )}
            {i < value.length - 1 ? ',' : ''}
            {'\n'}
          </React.Fragment>
        ))}
        {pad}
        {']'}
      </span>
    );
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span>{'{}'}</span>;
    return (
      <span>
        {'{\n'}
        {entries.map(([k, v], i) => (
          <React.Fragment key={k}>
            {pad}
            {'  '}
            <span className="text-blue-600 dark:text-blue-400">
              &quot;{k}&quot;
            </span>
            {': '}
            {renderHighlighted(
              v,
              path ? `${path}.${k}` : k,
              diffPaths,
              indent + 1,
            )}
            {i < entries.length - 1 ? ',' : ''}
            {'\n'}
          </React.Fragment>
        ))}
        {pad}
        {'}'}
      </span>
    );
  }
  // Leaf
  const isDifferent = diffPaths.has(path);
  const leafText =
    typeof value === 'string' ? `"${value}"` : String(value);
  return (
    <span
      className={
        isDifferent
          ? 'bg-amber-200/60 dark:bg-amber-700/40 text-amber-900 dark:text-amber-100 font-semibold px-0.5 rounded'
          : ''
      }
    >
      {leafText}
    </span>
  );
}
