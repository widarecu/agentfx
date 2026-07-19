/**
 * ValidatorOverrideModal (US-027).
 *
 * Retires the raw-SQL demo-unblock workaround Wilson used during the
 * v0.6.0 smoke-test. ADMIN-only; the parent gates rendering by role
 * so this component doesn't re-check. Enforces the min-20-char reason
 * client-side; the backend re-validates and returns 422 if bypassed.
 */

import React, { useState } from 'react';
import { AlertTriangle, Loader2, X } from 'lucide-react';

import { overrideValidatorVerdict } from '../services/evolutionApi';
import type { StrategyGenome } from '../types/evolution';

interface ValidatorOverrideModalProps {
  genome: Pick<StrategyGenome, 'id' | 'validator_passed'>;
  onCancel: () => void;
  onOverridden: (updated: StrategyGenome) => void;
  onError: (message: string) => void;
}

export const REASON_MIN_LENGTH = 20;

/**
 * Pure gate: can the submit button be enabled right now?
 *
 * Extracted so Vitest can lock the transition points (below-min,
 * whitespace-only, at-min, above-min, and while a submit is in
 * flight) without a jsdom render pass — the project's Vitest config
 * runs in the `node` environment and has no `@testing-library/react`.
 */
export function _canSubmit(reason: string, submitting: boolean): boolean {
  if (submitting) return false;
  return reason.trim().length >= REASON_MIN_LENGTH;
}

/**
 * Pure payload builder for the OVERRIDE endpoint. Mirrors the
 * backend Pydantic `ValidatorOverrideRequest` shape one-for-one.
 */
export function _buildOverridePayload(
  newValue: 'true' | 'false',
  reason: string,
): { new_value: boolean; reason: string } {
  return {
    new_value: newValue === 'true',
    reason: reason.trim(),
  };
}

export function ValidatorOverrideModal({
  genome,
  onCancel,
  onOverridden,
  onError,
}: ValidatorOverrideModalProps) {
  const [newValue, setNewValue] = useState<'true' | 'false'>(
    genome.validator_passed === true ? 'false' : 'true',
  );
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const reasonLength = reason.trim().length;
  const canSubmit = _canSubmit(reason, submitting);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const updated = await overrideValidatorVerdict(
        genome.id,
        _buildOverridePayload(newValue, reason),
      );
      onOverridden(updated);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="validator-override-title"
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/50 p-4"
    >
      <div className="bg-white dark:bg-slate-800 rounded-lg shadow-xl max-w-lg w-full">
        <div className="flex items-start justify-between p-5 border-b border-slate-200 dark:border-slate-700">
          <div>
            <h2
              id="validator-override-title"
              className="text-lg font-semibold flex items-center gap-2"
            >
              <AlertTriangle className="w-5 h-5 text-amber-500" />
              Override validator verdict
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
              ADMIN action. Writes a{' '}
              <span className="font-mono">USER_VALIDATOR_OVERRIDE</span>{' '}
              audit-log entry.
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          <div className="bg-slate-50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-700 rounded p-3 text-sm space-y-1">
            <div className="flex justify-between">
              <span className="text-slate-500 dark:text-slate-400">
                Genome
              </span>
              <span className="font-mono text-xs truncate max-w-xs">
                {genome.id}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500 dark:text-slate-400">
                Current verdict
              </span>
              <span className="font-semibold">
                {genome.validator_passed === null ||
                genome.validator_passed === undefined
                  ? 'not validated'
                  : String(genome.validator_passed)}
              </span>
            </div>
          </div>

          <div>
            <label
              htmlFor="new-value"
              className="text-sm font-medium block mb-1"
            >
              New verdict
            </label>
            <select
              id="new-value"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value as 'true' | 'false')}
              disabled={submitting}
              className="w-full border border-slate-300 dark:border-slate-600 dark:bg-slate-900 rounded px-3 py-2 text-sm"
            >
              <option value="true">true — force into approval queue</option>
              <option value="false">false — remove from approval queue</option>
            </select>
          </div>

          <div>
            <label htmlFor="reason" className="text-sm font-medium block mb-1">
              Reason{' '}
              <span className="text-xs text-slate-500 dark:text-slate-400 font-normal">
                (min {REASON_MIN_LENGTH} chars — recorded in the audit log)
              </span>
            </label>
            <textarea
              id="reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={submitting}
              rows={3}
              placeholder="e.g. Unblocking paper-trading UI walkthrough — diagnostic agent will re-evaluate this genome in v0.5.0."
              className="w-full border border-slate-300 dark:border-slate-600 dark:bg-slate-900 rounded px-3 py-2 text-sm font-mono"
            />
            <div className="flex justify-between text-xs mt-1">
              <span
                className={
                  reasonLength >= REASON_MIN_LENGTH
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : 'text-slate-500 dark:text-slate-400'
                }
              >
                {reasonLength} / {REASON_MIN_LENGTH} minimum
              </span>
            </div>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={submitting}
              className="px-4 py-2 rounded text-sm font-medium text-slate-700 dark:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-700"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="px-4 py-2 rounded text-sm font-semibold text-white bg-amber-600 hover:bg-amber-700 disabled:bg-slate-300 disabled:dark:bg-slate-700 disabled:cursor-not-allowed inline-flex items-center gap-2"
            >
              {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
              Override verdict
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
