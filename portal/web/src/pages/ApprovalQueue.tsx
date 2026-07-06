/**
 * /paper/approvals — approval queue page (US-021 / PR-I).
 *
 * Approve wires to a placeholder toast for PR-I — the deploy modal
 * with the OANDA account picker lands in PR-J. Reject is fully
 * functional (required reason + quick-picks + backend persists the
 * decision log entry).
 */

import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle2, Loader2 } from 'lucide-react';

import { useToast } from '../contexts/ToastContext';
import { ApprovalQueueCard } from '../components/ApprovalQueueCard';
import { RejectGenomeModal } from '../components/RejectGenomeModal';
import { listApprovalQueue, rejectGenome } from '../services/paperApi';
import type { ApprovalQueueItem } from '../types/paper';

export default function ApprovalQueue() {
  const [items, setItems] = useState<ApprovalQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rejectTarget, setRejectTarget] = useState<ApprovalQueueItem | null>(
    null,
  );
  const { showToast } = useToast();

  const fetchQueue = useCallback(async () => {
    try {
      setError(null);
      const response = await listApprovalQueue();
      setItems(response.items);
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
    // PR-J owns the deploy modal (OANDA account picker + risk knobs).
    // For PR-I we surface an explanatory toast so users see the
    // button exists but understand the flow is incomplete.
    showToast(
      `Deploy configuration modal ships in the next update. Genome ${item.strategy_genome_id} stays on the queue.`,
      'info',
    );
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
        <EmptyState />
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
    </div>
  );
}

function EmptyState() {
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-12">
      <div className="max-w-md mx-auto text-center">
        <div className="w-16 h-16 bg-emerald-100 dark:bg-emerald-900/30 rounded-full mx-auto flex items-center justify-center text-3xl text-emerald-600">
          <CheckCircle2 className="w-8 h-8" />
        </div>
        <div className="text-lg font-semibold mt-4">Your queue is empty</div>
        <div className="text-sm text-slate-500 dark:text-slate-400 mt-1">
          Nothing waiting for approval. Genomes land here after passing
          Gate B (Sharpe ≥ 1.0, MDD ≤ 10%, hit ≥ 48%, ≥ 300 trades) and
          being explicitly sent to your queue.
        </div>
        <Link
          to="/evolution"
          className="mt-6 inline-flex items-center gap-2 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-sm px-4 py-2 rounded"
        >
          Browse validated genomes in Evolution →
        </Link>
      </div>
    </div>
  );
}
