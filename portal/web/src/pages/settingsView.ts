/**
 * Pure selectors that drive every state-dependent branch of the
 * /settings page (AFX-55). `Settings.tsx` renders whatever these
 * return; `tests/unit/settingsView.test.ts` pins each branch in node
 * Vitest without a DOM. Same pattern as `selectPoolColumnView` in
 * `OandaAccountsTable`.
 */

import type {
  CredentialsState,
  UserOandaAccountPublic,
  VerifyCredentialsResponse,
} from '../types/credentials';

export interface SettingsFormView {
  /** Card title above the form; null when the card has its own heading. */
  title: string | null;
  note: string | null;
  cancellable: boolean;
}

export interface SettingsActionLabels {
  verify: string;
  edit: string;
  remove: string;
}

export interface SettingsView {
  kind: CredentialsState['kind'];
  showSpinner: boolean;
  tone: 'none' | 'amber' | 'emerald';
  heading: string | null;
  subheading: string | null;
  lastError: string | null;
  /** Button labels on the status card; null when there is no card. */
  actions: SettingsActionLabels | null;
  verifyBusy: boolean;
  form: SettingsFormView | null;
  showOptIn: boolean;
  showAccountsTable: boolean;
}

export interface SettingsUiFlags {
  editing: boolean;
  verifying: boolean;
}

const _BASE: Omit<SettingsView, 'kind'> = {
  showSpinner: false,
  tone: 'none',
  heading: null,
  subheading: null,
  lastError: null,
  actions: null,
  verifyBusy: false,
  form: null,
  showOptIn: false,
  showAccountsTable: false,
};

export function selectSettingsView(
  state: CredentialsState,
  { editing, verifying }: SettingsUiFlags,
): SettingsView {
  switch (state.kind) {
    case 'loading':
      return { ..._BASE, kind: 'loading', showSpinner: true };

    case 'empty':
      return {
        ..._BASE,
        kind: 'empty',
        heading: 'Add your OANDA API key',
        subheading:
          "You haven't set OANDA credentials yet. Paper runs execute " +
          'against your own OANDA Practice account — add your API key ' +
          'below to continue.',
        form: { title: null, note: null, cancellable: false },
      };

    case 'unverified': {
      const reason = state.credentials.verify_failure_reason;
      return {
        ..._BASE,
        kind: 'unverified',
        tone: 'amber',
        heading: 'OANDA credentials set — verification pending',
        subheading:
          'Click Test connection to fetch your sub-accounts from OANDA.',
        lastError: reason ? `Last error: ${reason}` : null,
        actions: { verify: 'Test connection', edit: 'Edit', remove: 'Delete' },
        verifyBusy: verifying,
        form: editing
          ? { title: 'Edit API key', note: null, cancellable: true }
          : null,
      };
    }

    case 'verified': {
      const { verified_at, key_version } = state.credentials;
      const when = verified_at
        ? `Last verified ${new Date(verified_at).toLocaleString()}`
        : 'Verified';
      return {
        ..._BASE,
        kind: 'verified',
        tone: 'emerald',
        heading: 'OANDA API key verified · PRACTICE',
        subheading: `${when} · key v${key_version}`,
        actions: { verify: 'Re-verify', edit: 'Rotate key', remove: 'Delete' },
        verifyBusy: verifying,
        form: editing
          ? {
              title: 'Rotate API key',
              note:
                'Saving a new key invalidates the verified badge — ' +
                "you'll need to re-verify after saving.",
              cancellable: true,
            }
          : null,
        showOptIn: true,
        showAccountsTable: true,
      };
    }
  }
}

export interface FleetPoolOptInView {
  pressed: boolean;
  srLabel: string;
  trackTone: string;
  knobOffset: string;
}

export function selectFleetPoolOptInView(
  allowFleetPoolUse: boolean,
): FleetPoolOptInView {
  return allowFleetPoolUse
    ? {
        pressed: true,
        srLabel: 'Disable fleet pool use',
        trackTone: 'bg-emerald-500',
        knobOffset: 'translate-x-5',
      }
    : {
        pressed: false,
        srLabel: 'Enable fleet pool use',
        trackTone: 'bg-slate-300 dark:bg-slate-600',
        knobOffset: 'translate-x-0',
      };
}

export type VerifyResult =
  | { ok: true; response: VerifyCredentialsResponse }
  | { ok: false; error: unknown };

export type VerifyOutcome =
  | { kind: 'verified'; accounts: UserOandaAccountPublic[] }
  | { kind: 'failed'; pageError: string };

/**
 * What the page does after Test connection / Re-verify. On `failed`
 * the page shows `pageError` and reloads, so the unverified card picks
 * up the stored `verify_failure_reason`.
 */
export function selectVerifyOutcome(result: VerifyResult): VerifyOutcome {
  if (!result.ok) {
    const { error } = result;
    return {
      kind: 'failed',
      pageError: error instanceof Error ? error.message : String(error),
    };
  }
  if (!result.response.verified) {
    return {
      kind: 'failed',
      pageError: result.response.verify_failure_reason ?? 'Verification failed',
    };
  }
  return { kind: 'verified', accounts: result.response.accounts };
}
