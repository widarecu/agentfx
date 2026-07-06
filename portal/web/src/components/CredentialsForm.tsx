/**
 * OANDA API-key form (US-019 / PR-H).
 *
 * The form collects only the API key + environment — the account IDs
 * are discovered on verify (Wilson's UX answer #4). Live is
 * tooltip-disabled until v1.0.0. The API key input is
 * password-styled with a visibility toggle; the visible-toggle state
 * is local-only and never leaves the component.
 */

import React, { useState } from 'react';
import { Eye, EyeOff, Loader2 } from 'lucide-react';

import type { OandaEnvironment } from '../types/credentials';

interface CredentialsFormProps {
  /**
   * When rendered from the "Edit" flow, this is prefilled with the
   * environment the user previously used so a user re-verifying with
   * the same key + env combo doesn't have to re-pick.
   */
  initialEnvironment?: OandaEnvironment;
  /**
   * Called on submit. The parent handles the actual API call so it
   * can also refresh the surrounding state cards (verified badge,
   * accounts table).
   */
  onSubmit: (payload: {
    oanda_api_key: string;
    environment: OandaEnvironment;
  }) => Promise<void>;
  /** Called when the user clicks Cancel (only shown in Edit mode). */
  onCancel?: () => void;
  /** Set by the parent when a submit is in flight. */
  submitting?: boolean;
  /** Server-side error message to render inline. */
  error?: string | null;
}

export function CredentialsForm({
  initialEnvironment = 'practice',
  onSubmit,
  onCancel,
  submitting = false,
  error,
}: CredentialsFormProps) {
  const [apiKey, setApiKey] = useState('');
  const [environment] = useState<OandaEnvironment>(initialEnvironment);
  const [reveal, setReveal] = useState(false);

  const canSubmit = apiKey.trim().length >= 16 && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    await onSubmit({ oanda_api_key: apiKey.trim(), environment });
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-4"
      aria-label="OANDA credentials form"
    >
      <div>
        <label
          htmlFor="oanda-api-key"
          className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400 block"
        >
          OANDA API Key{' '}
          <span className="text-slate-400">(masked · v20 REST token)</span>
        </label>
        <div className="mt-1 flex">
          <input
            id="oanda-api-key"
            type={reveal ? 'text' : 'password'}
            autoComplete="off"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="e.g. abcdef1234567890abcdef..."
            className="flex-1 px-3 py-2 border border-slate-300 dark:border-slate-600 dark:bg-slate-900 rounded-l font-mono text-sm"
            aria-required="true"
            aria-invalid={apiKey.length > 0 && apiKey.trim().length < 16}
          />
          <button
            type="button"
            onClick={() => setReveal((r) => !r)}
            aria-label={reveal ? 'Hide API key' : 'Show API key'}
            className="px-3 border border-slate-300 dark:border-slate-600 border-l-0 rounded-r text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-700"
          >
            {reveal ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
        <div className="text-xs text-slate-500 mt-1">
          One key covers all your OANDA sub-accounts. Generate it in the
          OANDA dashboard → Manage API Access.
        </div>
      </div>

      <div>
        <div className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400 mb-1">
          Environment
        </div>
        <div className="flex gap-6 text-sm">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="env"
              value="practice"
              checked={environment === 'practice'}
              readOnly
              className="accent-emerald-500"
            />
            PRACTICE
          </label>
          <label
            className="flex items-center gap-2 text-slate-400 cursor-not-allowed"
            title="Live trading requires ADMIN approval and enables in v1.0.0"
          >
            <input type="radio" name="env" disabled />
            LIVE <span className="text-xs">(v1.0.0)</span>
          </label>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="px-3 py-2 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-xs text-red-700 dark:text-red-300"
        >
          {error}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="px-4 py-2 border border-slate-200 dark:border-slate-700 rounded text-sm hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50"
          >
            Cancel
          </button>
        )}
        <button
          type="submit"
          disabled={!canSubmit}
          className="inline-flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
          Save credentials
        </button>
      </div>

      <div className="text-xs text-slate-500 dark:text-slate-400">
        The API key is encrypted at rest using Fernet (AES-128-CBC +
        HMAC-SHA256). Only the paper runner subprocess decrypts it in
        memory; the plaintext never appears in any API response or
        log line.
      </div>
    </form>
  );
}
