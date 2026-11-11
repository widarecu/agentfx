/**
 * Settings page — OANDA Credentials tab (US-019 / PR-H).
 *
 * The other tabs listed in the mockup (Profile, API tokens) land in
 * a later PR; this page ships with just the credentials tab because
 * it's the only one v0.6.0 needs. The tab strip is still rendered so
 * the layout matches the mockup and the future work is discoverable.
 *
 * State machine (per mockup):
 *   loading   → spinner
 *   empty     → CredentialsForm alone
 *   unverified→ verified badge (amber), CredentialsForm + Verify CTA
 *   verified  → verified badge (emerald), OandaAccountsTable, Edit / Delete
 *
 * Every label/branch decision comes from the pure selectors in
 * `./settingsView` (pinned by tests/unit/settingsView.test.ts, AFX-55).
 * Add new state-dependent UI there, not inline in the JSX.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, Loader2, Pencil, Trash2, AlertCircle } from 'lucide-react';

import { CredentialsForm } from '../components/CredentialsForm';
import { OandaAccountsTable } from '../components/OandaAccountsTable';
import {
  deleteMyCredentials,
  getMyCredentials,
  listMyAccounts,
  patchMyCredentialsSettings,
  upsertMyCredentials,
  verifyMyCredentials,
} from '../services/credentialsApi';
import type {
  CredentialsState,
  UserOandaCredentialsUpsert,
} from '../types/credentials';
import {
  selectFleetPoolOptInView,
  selectSettingsView,
  selectVerifyOutcome,
  type SettingsView,
  type VerifyResult,
} from './settingsView';

export default function Settings() {
  const [state, setState] = useState<CredentialsState>({ kind: 'loading' });
  const [editing, setEditing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const creds = await getMyCredentials();
      if (!creds) {
        setState({ kind: 'empty' });
        return;
      }
      if (!creds.verified) {
        setState({ kind: 'unverified', credentials: creds });
        return;
      }
      const list = await listMyAccounts();
      setState({
        kind: 'verified',
        credentials: creds,
        accounts: list.items,
      });
    } catch (err) {
      setPageError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleSave(payload: UserOandaCredentialsUpsert) {
    setSubmitting(true);
    setPageError(null);
    try {
      await upsertMyCredentials(payload);
      setEditing(false);
      await load();
    } catch (err) {
      setPageError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVerify() {
    setVerifying(true);
    setPageError(null);
    let result: VerifyResult;
    try {
      result = { ok: true, response: await verifyMyCredentials() };
    } catch (err) {
      // Verify failures come back as 400 with the sanitised reason.
      result = { ok: false, error: err };
    }
    try {
      const outcome = selectVerifyOutcome(result);
      if (outcome.kind === 'verified') {
        // Re-fetch credentials for the freshest verified_at / masked
        // view; the accounts payload came back on the verify call.
        const creds = await getMyCredentials();
        if (creds) {
          setState({
            kind: 'verified',
            credentials: creds,
            accounts: outcome.accounts,
          });
        }
      } else {
        // Reload so `verify_failure_reason` shows in the badge.
        setPageError(outcome.pageError);
        await load();
      }
    } catch (err) {
      setPageError(err instanceof Error ? err.message : String(err));
    } finally {
      setVerifying(false);
    }
  }

  async function handleDelete() {
    if (
      !confirm(
        'Delete OANDA credentials?\n\n' +
          'The encrypted key is removed from AgentFX. Any active ' +
          'paper runs stay running until they finish naturally — ' +
          'stop them separately if you want them halted.',
      )
    ) {
      return;
    }
    setPageError(null);
    try {
      await deleteMyCredentials();
      await load();
    } catch (err) {
      setPageError(err instanceof Error ? err.message : String(err));
    }
  }

  const view = selectSettingsView(state, { editing, verifying });

  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      <h1 className="text-2xl font-bold mb-1">Settings</h1>
      <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">
        Manage your broker credentials and pick which OANDA sub-account
        each paper run should use.
      </p>

      <TabStrip />

      {pageError && (
        <div
          role="alert"
          className="mb-4 px-4 py-3 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 text-sm text-red-700 dark:text-red-300 flex items-start gap-2"
        >
          <AlertCircle className="w-5 h-5 flex-shrink-0" />
          <div>{pageError}</div>
        </div>
      )}

      {view.showSpinner && (
        <div className="flex items-center justify-center h-40">
          <Loader2 className="w-8 h-8 text-emerald-500 animate-spin" />
        </div>
      )}

      {state.kind === 'empty' && (
        <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
          <div className="text-sm font-semibold mb-1">{view.heading}</div>
          <p className="text-xs text-slate-500 dark:text-slate-400 mb-4">
            {view.subheading}
          </p>
          <CredentialsForm
            onSubmit={handleSave}
            submitting={submitting}
          />
        </div>
      )}

      {state.kind === 'unverified' && (
        <div className="space-y-4">
          <div className="bg-white dark:bg-slate-800 border border-amber-300 dark:border-amber-700 rounded-lg p-6">
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 bg-amber-100 dark:bg-amber-900/40 rounded-full flex items-center justify-center text-amber-600 text-xl">
                  ⏸
                </div>
                <div>
                  <div className="font-semibold">{view.heading}</div>
                  <div className="text-xs text-slate-500 dark:text-slate-400">
                    {view.subheading}
                  </div>
                  {view.lastError && (
                    <div className="mt-2 text-xs text-rose-700 dark:text-rose-400">
                      {view.lastError}
                    </div>
                  )}
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={handleVerify}
                  disabled={view.verifyBusy}
                  className="inline-flex items-center gap-2 text-sm px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded font-semibold disabled:opacity-50"
                >
                  {view.verifyBusy && <Loader2 className="w-4 h-4 animate-spin" />}
                  {view.actions?.verify}
                </button>
                <button
                  onClick={() => setEditing(true)}
                  className="text-sm px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded hover:bg-slate-50 dark:hover:bg-slate-700 inline-flex items-center gap-1.5"
                >
                  <Pencil className="w-3.5 h-3.5" />
                  {view.actions?.edit}
                </button>
                <button
                  onClick={handleDelete}
                  className="text-sm px-3 py-1.5 border border-rose-200 dark:border-rose-800 text-rose-700 dark:text-rose-400 rounded hover:bg-rose-50 dark:hover:bg-rose-900/20 inline-flex items-center gap-1.5"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  {view.actions?.remove}
                </button>
              </div>
            </div>
          </div>
          {view.form && (
            <EditKeyCard
              form={view.form}
              onSubmit={handleSave}
              onCancel={() => setEditing(false)}
              submitting={submitting}
            />
          )}
        </div>
      )}

      {state.kind === 'verified' && (
        <div className="space-y-4">
          <VerifiedBadgeCard
            view={view}
            onEdit={() => setEditing(true)}
            onReverify={handleVerify}
            onDelete={handleDelete}
            reverifying={verifying}
          />

          <FleetPoolOptInCard
            allowFleetPoolUse={state.credentials.allow_fleet_pool_use}
            onToggle={async (next) => {
              try {
                const updated = await patchMyCredentialsSettings({
                  allow_fleet_pool_use: next,
                });
                setState((prev) =>
                  prev.kind === 'verified'
                    ? { ...prev, credentials: updated }
                    : prev,
                );
              } catch (err) {
                setPageError(err instanceof Error ? err.message : String(err));
              }
            }}
          />

          <OandaAccountsTable
            accounts={state.accounts}
            verifiedAt={state.credentials.verified_at}
            onReverify={handleVerify}
            reverifying={verifying}
            allowFleetPoolUse={state.credentials.allow_fleet_pool_use}
          />

          {view.form && (
            <EditKeyCard
              form={view.form}
              onSubmit={handleSave}
              onCancel={() => setEditing(false)}
              submitting={submitting}
            />
          )}
        </div>
      )}
    </div>
  );
}

function TabStrip() {
  return (
    <div className="flex gap-6 border-b border-slate-200 dark:border-slate-700 mb-6 text-sm">
      <div className="pb-3 text-slate-500 dark:text-slate-400" aria-disabled="true">
        Profile
        <span className="ml-1 text-[10px] uppercase tracking-wider text-slate-400">
          soon
        </span>
      </div>
      <div className="pb-3 border-b-2 border-emerald-500 font-semibold">
        OANDA Credentials
      </div>
      <div className="pb-3 text-slate-400 dark:text-slate-500" aria-disabled="true">
        API tokens
        <span className="ml-1 text-[10px] uppercase tracking-wider text-slate-400">
          soon
        </span>
      </div>
    </div>
  );
}

function EditKeyCard({
  form,
  onSubmit,
  onCancel,
  submitting,
}: {
  form: NonNullable<SettingsView['form']>;
  onSubmit: (payload: UserOandaCredentialsUpsert) => Promise<void>;
  onCancel: () => void;
  submitting: boolean;
}) {
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
      <div className="text-sm font-semibold mb-3">{form.title}</div>
      {form.note && (
        <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">
          {form.note}
        </p>
      )}
      <CredentialsForm
        onSubmit={onSubmit}
        onCancel={form.cancellable ? onCancel : undefined}
        submitting={submitting}
      />
    </div>
  );
}

function VerifiedBadgeCard({
  view,
  onEdit,
  onReverify,
  onDelete,
  reverifying,
}: {
  view: SettingsView;
  onEdit: () => void;
  onReverify: () => void;
  onDelete: () => void;
  reverifying: boolean;
}) {
  return (
    <div className="bg-white dark:bg-slate-800 border border-emerald-400 dark:border-emerald-600 rounded-lg p-6">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-emerald-100 dark:bg-emerald-900/40 rounded-full flex items-center justify-center text-emerald-600 text-xl">
            <CheckCircle2 className="w-6 h-6" />
          </div>
          <div>
            <div className="font-semibold">{view.heading}</div>
            <div className="text-xs text-slate-500 dark:text-slate-400">
              {view.subheading}
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={onReverify}
            disabled={reverifying}
            className="text-sm px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50 inline-flex items-center gap-1.5"
          >
            {reverifying && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {view.actions?.verify}
          </button>
          <button
            onClick={onEdit}
            className="text-sm px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded hover:bg-slate-50 dark:hover:bg-slate-700 inline-flex items-center gap-1.5"
          >
            <Pencil className="w-3.5 h-3.5" />
            {view.actions?.edit}
          </button>
          <button
            onClick={onDelete}
            className="text-sm px-3 py-1.5 border border-rose-200 dark:border-rose-800 text-rose-700 dark:text-rose-400 rounded hover:bg-rose-50 dark:hover:bg-rose-900/20 inline-flex items-center gap-1.5"
          >
            <Trash2 className="w-3.5 h-3.5" />
            {view.actions?.remove}
          </button>
        </div>
      </div>
    </div>
  );
}

function FleetPoolOptInCard({
  allowFleetPoolUse,
  onToggle,
}: {
  allowFleetPoolUse: boolean;
  onToggle: (next: boolean) => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const toggle = selectFleetPoolOptInView(allowFleetPoolUse);
  const handleClick = async () => {
    setBusy(true);
    try {
      await onToggle(!allowFleetPoolUse);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
      <div className="flex items-start justify-between gap-6">
        <div>
          <div className="text-sm font-semibold mb-1">
            Allow fleet to use my OANDA accounts for paper experiments
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400 max-w-xl">
            When on, AgentFX's autonomous fleet may lease any of your
            OANDA practice sub-accounts (shown below) for its own paper
            runs. The fleet never uses your live creds — opt-in applies
            to practice only. Each sub-account is held for at most one
            paper run at a time; the "Pool" column below shows what's
            currently in use. Turn off any time to stop new allocations
            (runs already in flight keep their slot until they halt).
          </p>
        </div>
        <button
          type="button"
          onClick={handleClick}
          disabled={busy}
          aria-pressed={toggle.pressed}
          className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 focus-visible:ring-offset-2 disabled:opacity-50 ${toggle.trackTone}`}
        >
          <span className="sr-only">
            {toggle.srLabel}
          </span>
          <span
            aria-hidden="true"
            className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition ${toggle.knobOffset}`}
          />
        </button>
      </div>
    </div>
  );
}
