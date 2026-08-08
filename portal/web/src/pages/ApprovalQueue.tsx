/**
 * /paper/approvals — approval queue page (US-021 / PR-I).
 *
 * Approve wires to a placeholder toast for PR-I — the deploy modal
 * with the OANDA account picker lands in PR-J. Reject is fully
 * functional (required reason + quick-picks + backend persists the
 * decision log entry).
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertCircle, CheckCircle2, Loader2, PackageOpen, PlayCircle, Upload } from 'lucide-react';

import { useToast } from '../contexts/ToastContext';
import { ApprovalQueueCard } from '../components/ApprovalQueueCard';
import { DeployPaperModal } from '../components/DeployPaperModal';
import { RejectGenomeModal } from '../components/RejectGenomeModal';
import { listApprovalQueue, rejectGenome } from '../services/paperApi';
import { importGenome } from '../services/evolutionApi';
import type { ApprovalQueueItem, ApprovalQueueMeta } from '../types/paper';
import type { GenomeExportPayload } from '../types/evolution';

export default function ApprovalQueue() {
  const [items, setItems] = useState<ApprovalQueueItem[]>([]);
  const [meta, setMeta] = useState<ApprovalQueueMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rejectTarget, setRejectTarget] = useState<ApprovalQueueItem | null>(
    null,
  );
  const [deployTarget, setDeployTarget] = useState<ApprovalQueueItem | null>(
    null,
  );
  const { showToast } = useToast();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [importing, setImporting] = useState(false);

  // v0.7.x — Import a peer's `.agenome.json`. On success, the
  // imported genome appears as a shadow row in this user's queue
  // (backend creates EvolutionRun/Generation/StrategyGenome shadows
  // scoped to `current_user`). We refresh the queue so the user
  // sees it immediately.
  async function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    // Reset input so re-selecting the same file re-fires onChange.
    e.target.value = '';
    if (!file) return;
    setImporting(true);
    try {
      const text = await file.text();
      let payload: GenomeExportPayload;
      try {
        payload = JSON.parse(text);
      } catch (parseErr) {
        throw new Error(
          `Failed to parse ${file.name} as JSON: ${parseErr instanceof Error ? parseErr.message : String(parseErr)}`,
        );
      }
      const response = await importGenome(payload);
      showToast(
        `Imported ${response.genome_id.slice(-20)} from ${payload.exported_by.email}`,
        'success',
      );
      await fetchQueue();
    } catch (err) {
      showToast(
        `Import failed: ${err instanceof Error ? err.message : String(err)}`,
        'error',
      );
    } finally {
      setImporting(false);
    }
  }

  const fetchQueue = useCallback(async () => {
    try {
      setError(null);
      const response = await listApprovalQueue();
      setItems(response.items);
      setMeta(response.meta);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchQueue();
  }, [fetchQueue]);

  function handleApprove(item: ApprovalQueueItem) {
    // PR-J: open the deploy modal (OANDA account picker + risk knobs).
    setDeployTarget(item);
  }

  function handleDeployed(runId: string) {
    setDeployTarget(null);
    showToast('Paper run deployed — watching from the detail page.', 'success');
    navigate(`/paper/${runId}`);
  }

  function handleRejectClick(item: ApprovalQueueItem) {
    setRejectTarget(item);
  }

  async function handleRejectSubmit(reason: string) {
    if (!rejectTarget) return;
    await rejectGenome(rejectTarget.strategy_genome_id, reason);
    showToast(`Genome rejected — moved off the queue.`, 'success');
    setRejectTarget(null);
    await fetchQueue();
  }

  return (
    <div className="container mx-auto px-4 py-8 max-w-5xl">
      <div className="flex items-start justify-between mb-6 flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold">Approval Queue</h1>
          <div className="text-sm text-slate-500 dark:text-slate-400 mt-1">
            Validated genomes waiting for your sign-off. Approve to deploy
            to paper.
          </div>
        </div>
        <div className="flex items-center gap-3">
          {/* v0.7.x — Import a peer's `.agenome.json`. Hidden file
              input triggered by the visible button. */}
          <input
            ref={fileInputRef}
            type="file"
            accept="application/json,.json,.agenome.json"
            className="hidden"
            onChange={handleImportFile}
            aria-hidden
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-700 text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed"
            title="Import a .agenome.json file a peer shared with you"
          >
            {importing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
            {importing ? 'Importing…' : 'Import genome'}
          </button>
          <div className="flex gap-1 text-xs bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded p-0.5">
            <Link
              to="/paper"
              className="rounded px-3 py-1.5 font-semibold text-slate-500 hover:text-slate-700 dark:hover:text-slate-200"
            >
              Runs
            </Link>
            <span className="bg-emerald-500 text-white rounded px-3 py-1.5 font-semibold">
              Approvals · {items.length}
            </span>
          </div>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="mb-4 px-4 py-3 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-300"
        >
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-40">
          <Loader2 className="w-8 h-8 text-emerald-500 animate-spin" />
        </div>
      ) : items.length === 0 ? (
        <EmptyState meta={meta} />
      ) : (
        <div className="space-y-4">
          {items.map((item) => (
            <ApprovalQueueCard
              key={item.strategy_genome_id}
              item={item}
              onApprove={handleApprove}
              onReject={handleRejectClick}
            />
          ))}
        </div>
      )}

      {rejectTarget && (
        <RejectGenomeModal
          item={rejectTarget}
          onCancel={() => setRejectTarget(null)}
          onSubmit={handleRejectSubmit}
        />
      )}

      {deployTarget && (
        <DeployPaperModal
          item={deployTarget}
          onCancel={() => setDeployTarget(null)}
          onDeployed={handleDeployed}
        />
      )}
    </div>
  );
}

/**
 * Empty-state diagnostic banner (US-027).
 *
 * The generic "queue empty" state was actively misleading during the
 * 2026-07-08 UAT smoke-test: Wilson diagnosed a broken validator
 * write-back when the reality (confirmed by a direct DB audit) was
 * that no evolution had ever produced a Gate-B-passing genome on real
 * OANDA data. The four scenarios below are visually distinct so the
 * user can tell "no evolutions" from "evolutions ran but nothing
 * generalised" from "you already deployed everything that passed" —
 * three completely different next-steps.
 */
export type EmptyBannerScenario =
  | 'no_evolutions'
  | 'none_validated'
  | 'none_passed'
  | 'all_deployed';

/**
 * Pure banner-scenario picker — exported so Vitest can lock the
 * switch table without a jsdom render pass. The React component below
 * is a straight visual translation of this classification.
 */
export function _pickEmptyBannerScenario(
  meta: ApprovalQueueMeta | null,
): EmptyBannerScenario {
  if (!meta || meta.evolution_run_count === 0) return 'no_evolutions';
  if (meta.validated_count === 0) return 'none_validated';
  if (meta.passed_count === 0) return 'none_passed';
  return 'all_deployed';
}

function EmptyState({ meta }: { meta: ApprovalQueueMeta | null }) {
  const scenario = _pickEmptyBannerScenario(meta);
  if (scenario === 'no_evolutions') {
    return (
      <BannerFrame icon={<PlayCircle className="w-8 h-8" />} tone="emerald">
        <div className="text-lg font-semibold mt-4">No evolution runs yet</div>
        <div className="text-sm text-slate-500 dark:text-slate-400 mt-1">
          Genomes land on this queue after an evolution run produces at
          least one strategy that clears Gate B on the held-out OOS folds.
        </div>
        <Link
          to="/evolution"
          className="mt-6 inline-flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-sm px-4 py-2 rounded"
        >
          Start an evolution →
        </Link>
      </BannerFrame>
    );
  }

  if (scenario === 'none_validated') {
    return (
      <BannerFrame icon={<AlertCircle className="w-8 h-8" />} tone="amber">
        <div className="text-lg font-semibold mt-4">
          {meta!.evolution_run_count} evolution run
          {meta!.evolution_run_count === 1 ? '' : 's'} · {meta!.total_genomes}{' '}
          genome{meta!.total_genomes === 1 ? '' : 's'} · 0 validated
        </div>
        <div className="text-sm text-slate-500 dark:text-slate-400 mt-1">
          Runs completed but the validator never wrote a verdict — this
          usually means a run crashed mid-loop before reaching validation,
          or the top-K genome pool ended up empty. Check the evolution
          runs page for details.
        </div>
        <Link
          to="/evolution"
          className="mt-6 inline-flex items-center gap-2 bg-slate-600 hover:bg-slate-700 text-white font-semibold text-sm px-4 py-2 rounded"
        >
          View evolution runs →
        </Link>
      </BannerFrame>
    );
  }

  if (scenario === 'none_passed') {
    const rows = meta!.top_close_to_passing ?? [];
    return (
      <BannerFrame icon={<AlertCircle className="w-8 h-8" />} tone="amber">
        <div className="text-lg font-semibold mt-4">
          {meta!.evolution_run_count} evolution run
          {meta!.evolution_run_count === 1 ? '' : 's'} ·{' '}
          {meta!.validated_count} genome
          {meta!.validated_count === 1 ? '' : 's'} validated on OOS folds ·
          0 passed Gate B
        </div>
        <div className="text-sm text-slate-500 dark:text-slate-400 mt-1">
          The evolutions produced strategies but none generalised to the
          held-out out-of-sample window. This isn't a bug in the pipeline —
          it's the training-vs-OOS gap the paper platform exists to
          expose. Consider running a fresh evolution or adjusting Gate B.
        </div>
        {rows.length > 0 && <CloseToPassingTable rows={rows} />}
      </BannerFrame>
    );
  }

  // scenario === 'all_deployed' — passing genomes exist but the user
  // has deployed all of them.
  return (
    <BannerFrame icon={<CheckCircle2 className="w-8 h-8" />} tone="emerald">
      <div className="text-lg font-semibold mt-4">
        {meta!.passed_count} validated genome
        {meta!.passed_count === 1 ? ' has' : 's have'} been deployed
      </div>
      <div className="text-sm text-slate-500 dark:text-slate-400 mt-1">
        Every genome that passed Gate B is already running (or was
        previously run) on paper. Nothing new to approve until the next
        evolution produces a fresh candidate.
      </div>
      <Link
        to="/paper"
        className="mt-6 inline-flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-sm px-4 py-2 rounded"
      >
        View paper runs →
      </Link>
    </BannerFrame>
  );
}

function BannerFrame({
  icon,
  tone,
  children,
}: {
  icon: React.ReactNode;
  tone: 'emerald' | 'amber';
  children: React.ReactNode;
}) {
  const iconBg =
    tone === 'emerald'
      ? 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600'
      : 'bg-amber-100 dark:bg-amber-900/30 text-amber-600';
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-12">
      <div className="max-w-xl mx-auto text-center">
        <div
          className={`w-16 h-16 rounded-full mx-auto flex items-center justify-center text-3xl ${iconBg}`}
        >
          {icon}
        </div>
        {children}
      </div>
    </div>
  );
}

/**
 * Comparison table of up to 5 near-miss genomes ordered by
 * distance-to-passing (US-027). Wilson called out on 2026-07-13
 * that a single "closest" recommendation was:
 *   (a) picked by a noisy heuristic (fluky-high-Sharpe folds beat
 *       solid multi-fold performance), and
 *   (b) hides better candidates from an ADMIN override decision.
 * A table sorted by an aggregate distance score lets him eyeball
 * per-gate metrics and pick with context rather than trusting one
 * heuristic-driven pick.
 */
function CloseToPassingTable({
  rows,
}: {
  rows: NonNullable<ApprovalQueueMeta['top_close_to_passing']>;
}) {
  return (
    <div className="mt-6 text-left">
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-2">
        <PackageOpen className="w-4 h-4" /> Top {rows.length} closest to
        passing — ordered by aggregate distance from Gate B thresholds
      </div>
      <div className="overflow-x-auto bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-700 rounded">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs uppercase text-slate-500 dark:text-slate-400 text-left border-b border-slate-200 dark:border-slate-700">
              <th className="px-3 py-2">Genome</th>
              <th className="px-3 py-2 text-right">Sharpe</th>
              <th className="px-3 py-2 text-right">MDD</th>
              <th className="px-3 py-2 text-right">Hit</th>
              <th className="px-3 py-2 text-right">Trades</th>
              <th className="px-3 py-2 text-center">Gates ✓</th>
              <th className="px-3 py-2 text-right">Distance</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr
                key={r.genome_id}
                className={
                  i === 0
                    ? 'bg-amber-50/40 dark:bg-amber-900/10 border-b border-slate-200 dark:border-slate-700'
                    : 'border-b border-slate-100 dark:border-slate-800'
                }
              >
                <td className="px-3 py-1.5 font-mono text-xs truncate max-w-[14ch]">
                  <span title={r.genome_id}>
                    {r.genome_id.split('_').slice(-3).join('_')}
                  </span>
                </td>
                <td
                  className={`px-3 py-1.5 text-right font-mono text-xs ${
                    (r.oos_sharpe ?? 0) >= 1 ? 'text-emerald-600' : ''
                  }`}
                >
                  {r.oos_sharpe !== null ? r.oos_sharpe.toFixed(2) : '—'}
                </td>
                <td
                  className={`px-3 py-1.5 text-right font-mono text-xs ${
                    Math.abs(r.oos_max_drawdown ?? 1) <= 0.1
                      ? 'text-emerald-600'
                      : ''
                  }`}
                >
                  {r.oos_max_drawdown !== null
                    ? `${(r.oos_max_drawdown * 100).toFixed(1)}%`
                    : '—'}
                </td>
                <td
                  className={`px-3 py-1.5 text-right font-mono text-xs ${
                    (r.oos_hit_rate ?? 0) >= 0.48 ? 'text-emerald-600' : ''
                  }`}
                >
                  {r.oos_hit_rate !== null
                    ? `${(r.oos_hit_rate * 100).toFixed(1)}%`
                    : '—'}
                </td>
                <td
                  className={`px-3 py-1.5 text-right font-mono text-xs ${
                    (r.oos_n_trades ?? 0) >= 300 ? 'text-emerald-600' : ''
                  }`}
                >
                  {r.oos_n_trades !== null
                    ? r.oos_n_trades.toLocaleString()
                    : '—'}
                </td>
                <td className="px-3 py-1.5 text-center font-mono text-xs">
                  {r.gates_cleared}/4
                </td>
                <td className="px-3 py-1.5 text-right font-mono text-xs text-slate-600 dark:text-slate-400">
                  {r.distance_score.toFixed(2)}
                </td>
                <td className="px-3 py-1.5 text-right">
                  {r.evolution_run_id ? (
                    <Link
                      to={`/evolution/${r.evolution_run_id}/genomes/${r.genome_id}`}
                      className="text-xs text-emerald-600 dark:text-emerald-400 hover:underline whitespace-nowrap"
                    >
                      View →
                    </Link>
                  ) : (
                    <span className="text-xs text-slate-400">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-slate-500 dark:text-slate-400 mt-2 italic">
        Ranked by aggregate distance from Gate B thresholds (Sharpe ≥ 1.0,
        |MDD| ≤ 10%, Hit ≥ 48%, Trades ≥ 300). Highlighted row = smallest
        distance. Green cells = gate already cleared. Use the ADMIN
        override on the genome detail page to promote any row to
        approvable.
      </div>
    </div>
  );
}
