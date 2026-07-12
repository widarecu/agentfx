/**
 * /paper/approvals — approval queue page (US-021 / PR-I).
 *
 * Approve wires to a placeholder toast for PR-I — the deploy modal
 * with the OANDA account picker lands in PR-J. Reject is fully
 * functional (required reason + quick-picks + backend persists the
 * decision log entry).
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { AlertCircle, CheckCircle2, Loader2, PackageOpen, PlayCircle } from 'lucide-react';

import { useToast } from '../contexts/ToastContext';
import { ApprovalQueueCard } from '../components/ApprovalQueueCard';
import { DeployPaperModal } from '../components/DeployPaperModal';
import { RejectGenomeModal } from '../components/RejectGenomeModal';
import { listApprovalQueue, rejectGenome } from '../services/paperApi';
import type { ApprovalQueueItem, ApprovalQueueMeta } from '../types/paper';

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
        {meta!.closest_to_passing && (
          <ClosestGenomeCard closest={meta!.closest_to_passing} />
        )}
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

function ClosestGenomeCard({
  closest,
}: {
  closest: NonNullable<ApprovalQueueMeta['closest_to_passing']>;
}) {
  // GenomeDetail only mounts on `/evolution/:runId/genomes/:genomeId`.
  // Fall back to a non-navigating div when the backend couldn't
  // resolve the evolution_run_id (legacy or orphaned genomes) so we
  // don't ship a link that 404s.
  const cardBody = (
    <>
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
        <PackageOpen className="w-4 h-4" /> Closest to passing
      </div>
      <div className="font-mono text-sm mt-1 truncate">{closest.genome_id}</div>
      <div className="text-sm text-slate-600 dark:text-slate-300 mt-2">
        {closest.failure_summary}
      </div>
      {closest.oos_sharpe !== null && (
        <div className="text-xs text-slate-500 dark:text-slate-400 mt-2">
          Median OOS Sharpe {closest.oos_sharpe.toFixed(2)}
          {closest.oos_n_trades !== null &&
            ` · ${closest.oos_n_trades} OOS trades`}
        </div>
      )}
      {closest.evolution_run_id && (
        <div className="text-xs text-emerald-600 dark:text-emerald-400 mt-3">
          View genome detail →
        </div>
      )}
    </>
  );
  if (!closest.evolution_run_id) {
    return (
      <div className="mt-6 block bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-700 rounded p-4 text-left">
        {cardBody}
      </div>
    );
  }
  return (
    <Link
      to={`/evolution/${closest.evolution_run_id}/genomes/${closest.genome_id}`}
      className="mt-6 block bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-700 rounded p-4 text-left hover:border-emerald-400 transition-colors"
    >
      {cardBody}
    </Link>
  );
}
