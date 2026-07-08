/**
 * Reject-with-reason modal (US-023 / PR-I).
 *
 * Wilson's answer #5 (locked in the v2 UX PR): reject reason is
 * REQUIRED, ≥ 10 characters. Quick-pick chips are advisory — they
 * inject the chip text into the reason field but the user can still
 * edit. The chips are the vocabulary the Evolution agent (v0.7.0)
 * will learn from.
 */

import React, { useState } from 'react';
import { X, Loader2 } from 'lucide-react';

import type { ApprovalQueueItem } from '../types/paper';
import { REJECT_QUICK_PICKS } from '../types/paper';

interface RejectGenomeModalProps {
  item: ApprovalQueueItem;
  onCancel: () => void;
  onSubmit: (reason: string) => Promise<void>;
}

export function RejectGenomeModal({
  item,
  onCancel,
  onSubmit,
}: RejectGenomeModalProps) {
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmedLength = reason.trim().length;
  const canSubmit = trimmedLength >= 10 && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(reason.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="reject-modal-title"
    >
      <div className="w-full max-w-md bg-white dark:bg-slate-800 rounded-xl shadow-2xl">
        <form onSubmit={handleSubmit}>
          <div className="p-6 border-b border-slate-200 dark:border-slate-700 flex items-start justify-between">
            <div>
              <div id="reject-modal-title" className="text-lg font-semibold">
                Reject genome
              </div>
              <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                {item.instrument} · {item.granularity} ·{' '}
                <span className="font-mono">{item.strategy_genome_id}</span>
              </div>
            </div>
            <button
              type="button"
              onClick={onCancel}
              className="p-1 rounded hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-500"
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="p-6 space-y-4">
            <div>
              <label
                htmlFor="reject-reason"
                className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400"
              >
                Reason <span className="text-rose-600">*</span>{' '}
                <span className="normal-case text-slate-400 tracking-normal">
                  required (≥ 10 chars)
                </span>
              </label>
              <textarea
                id="reject-reason"
                rows={4}
                minLength={10}
                maxLength={500}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why isn't this genome fit for paper trading?"
                required
                className="mt-1 w-full px-3 py-2 border border-slate-300 dark:border-slate-600 dark:bg-slate-900 rounded text-sm"
              />
              <div className="flex items-center justify-between mt-1">
                <div className="text-xs text-slate-500">
                  Persisted to the decision log; the Evolution agent uses these
                  when generating future variations.
                </div>
                <div className="text-xs text-slate-400 font-mono">
                  {trimmedLength} / 500
                </div>
              </div>
            </div>

            <div className="flex flex-wrap gap-1.5 text-xs">
              <span className="text-slate-500 mr-1">Quick-pick:</span>
              {REJECT_QUICK_PICKS.map((chip) => (
                <button
                  key={chip}
                  type="button"
                  onClick={() =>
                    setReason((prev) => (prev.trim() ? prev : chip))
                  }
                  className="border border-slate-200 dark:border-slate-700 rounded-full px-2 py-0.5 hover:bg-slate-100 dark:hover:bg-slate-700"
                >
                  {chip}
                </button>
              ))}
            </div>

            <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded p-3 text-xs">
              <div className="font-semibold text-amber-800 dark:text-amber-300">
                Rejecting removes this genome from the queue.
              </div>
              <div className="text-amber-700 dark:text-amber-400 mt-1">
                It stays visible under /evolution/… — you can send it back
                to the queue anytime from GenomeDetail.
              </div>
            </div>

            {error && (
              <div
                role="alert"
                className="text-xs text-rose-700 dark:text-rose-400 bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-800 rounded px-3 py-2"
              >
                {error}
              </div>
            )}
          </div>

          <div className="p-6 border-t border-slate-200 dark:border-slate-700 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={submitting}
              className="px-4 py-2 border border-slate-200 dark:border-slate-700 rounded text-sm hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="inline-flex items-center gap-2 px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded text-sm font-semibold shadow disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
              Reject with reason
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
