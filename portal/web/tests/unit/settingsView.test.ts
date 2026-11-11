/**
 * AFX-55: pins every visible-label branch of the /settings page.
 *
 * The page's rendering is driven by three pure selectors in
 * `src/pages/settingsView.ts` (the `selectPoolColumnView` pattern):
 *
 *   - `selectSettingsView`        loading / empty / unverified / verified
 *   - `selectFleetPoolOptInView`  opt-in on / off
 *   - `selectVerifyOutcome`       verify success / failure
 *
 * Each `it` asserts the label delta a user would see, so a component
 * that stops branching (the AFX-7 "always Free" bug) fails here.
 */

import { describe, expect, it } from 'vitest';
import type {
  CredentialsState,
  UserOandaAccountPublic,
  UserOandaCredentialsPublic,
  VerifyCredentialsResponse,
} from '../../src/types/credentials';
import {
  selectFleetPoolOptInView,
  selectSettingsView,
  selectVerifyOutcome,
} from '../../src/pages/settingsView';

function _creds(
  overrides: Partial<UserOandaCredentialsPublic> = {},
): UserOandaCredentialsPublic {
  return {
    environment: 'practice',
    verified: true,
    verified_at: '2026-11-10T09:00:00Z',
    verify_failure_reason: null,
    key_version: 2,
    allow_fleet_pool_use: false,
    updated_at: '2026-11-10T09:00:00Z',
    ...overrides,
  };
}

function _acct(): UserOandaAccountPublic {
  return {
    oanda_account_id: '101-004-xxxx-001',
    alias: null,
    currency: 'USD',
    last_seen_at: '2026-11-10T09:00:00Z',
    active_paper_run_id: null,
    pool_status: 'free',
    pool_current_run_id: null,
    pool_in_use_since: null,
    pool_cooling_until: null,
  };
}

const IDLE = { editing: false, verifying: false };

describe('selectSettingsView', () => {
  it('loading: spinner only, no card, no form', () => {
    const view = selectSettingsView({ kind: 'loading' }, IDLE);
    expect(view.kind).toBe('loading');
    expect(view.showSpinner).toBe(true);
    expect(view.heading).toBeNull();
    expect(view.form).toBeNull();
    expect(view.actions).toBeNull();
    expect(view.showOptIn).toBe(false);
    expect(view.showAccountsTable).toBe(false);
  });

  it('empty: "Add your OANDA API key" with the form always open', () => {
    const view = selectSettingsView({ kind: 'empty' }, IDLE);
    expect(view.kind).toBe('empty');
    expect(view.showSpinner).toBe(false);
    expect(view.heading).toBe('Add your OANDA API key');
    expect(view.subheading).toContain("haven't set OANDA credentials");
    expect(view.form).toEqual({ title: null, note: null, cancellable: false });
    expect(view.actions).toBeNull();
    expect(view.showOptIn).toBe(false);
    expect(view.showAccountsTable).toBe(false);
  });

  it('empty: ignores a stale `editing` flag (form is not cancellable)', () => {
    const view = selectSettingsView({ kind: 'empty' }, { ...IDLE, editing: true });
    expect(view.form?.cancellable).toBe(false);
  });

  describe('unverified', () => {
    const state: CredentialsState = {
      kind: 'unverified',
      credentials: _creds({ verified: false, verified_at: null }),
    };

    it('amber "verification pending" card with Test connection / Edit / Delete', () => {
      const view = selectSettingsView(state, IDLE);
      expect(view.kind).toBe('unverified');
      expect(view.tone).toBe('amber');
      expect(view.heading).toBe('OANDA credentials set — verification pending');
      expect(view.subheading).toBe(
        'Click Test connection to fetch your sub-accounts from OANDA.',
      );
      expect(view.actions).toEqual({
        verify: 'Test connection',
        edit: 'Edit',
        remove: 'Delete',
      });
      expect(view.lastError).toBeNull();
      expect(view.showOptIn).toBe(false);
      expect(view.showAccountsTable).toBe(false);
    });

    it('surfaces the last verify failure reason', () => {
      const view = selectSettingsView(
        {
          kind: 'unverified',
          credentials: _creds({
            verified: false,
            verified_at: null,
            verify_failure_reason: 'OANDA 401: Insufficient authorization',
          }),
        },
        IDLE,
      );
      expect(view.lastError).toBe(
        'Last error: OANDA 401: Insufficient authorization',
      );
    });

    it('form hidden until Edit, then "Edit API key" with cancel', () => {
      expect(selectSettingsView(state, IDLE).form).toBeNull();
      expect(selectSettingsView(state, { ...IDLE, editing: true }).form).toEqual({
        title: 'Edit API key',
        note: null,
        cancellable: true,
      });
    });

    it('disables the verify action while verifying', () => {
      expect(selectSettingsView(state, IDLE).verifyBusy).toBe(false);
      expect(
        selectSettingsView(state, { ...IDLE, verifying: true }).verifyBusy,
      ).toBe(true);
    });
  });

  describe('verified', () => {
    const state: CredentialsState = {
      kind: 'verified',
      credentials: _creds(),
      accounts: [_acct()],
    };

    it('emerald badge, opt-in card + accounts table, Re-verify / Rotate key / Delete', () => {
      const view = selectSettingsView(state, IDLE);
      expect(view.kind).toBe('verified');
      expect(view.tone).toBe('emerald');
      expect(view.heading).toBe('OANDA API key verified · PRACTICE');
      expect(view.subheading).toMatch(/^Last verified .+ · key v2$/);
      expect(view.actions).toEqual({
        verify: 'Re-verify',
        edit: 'Rotate key',
        remove: 'Delete',
      });
      expect(view.showOptIn).toBe(true);
      expect(view.showAccountsTable).toBe(true);
      expect(view.form).toBeNull();
    });

    it('falls back to "Verified" when verified_at is missing', () => {
      const view = selectSettingsView(
        { ...state, credentials: _creds({ verified_at: null }) },
        IDLE,
      );
      expect(view.subheading).toBe('Verified · key v2');
    });

    it('Rotate key opens "Rotate API key" with the re-verify warning', () => {
      const view = selectSettingsView(state, { ...IDLE, editing: true });
      expect(view.form?.title).toBe('Rotate API key');
      expect(view.form?.note).toContain('re-verify after saving');
      expect(view.form?.cancellable).toBe(true);
    });
  });
});

describe('selectFleetPoolOptInView', () => {
  it('opt-in OFF: unpressed, grey track, offers "Enable"', () => {
    const view = selectFleetPoolOptInView(false);
    expect(view.pressed).toBe(false);
    expect(view.srLabel).toBe('Enable fleet pool use');
    expect(view.trackTone).toContain('bg-slate-300');
    expect(view.knobOffset).toBe('translate-x-0');
  });

  it('opt-in ON: pressed, emerald track, offers "Disable"', () => {
    const view = selectFleetPoolOptInView(true);
    expect(view.pressed).toBe(true);
    expect(view.srLabel).toBe('Disable fleet pool use');
    expect(view.trackTone).toContain('bg-emerald-500');
    expect(view.knobOffset).toBe('translate-x-5');
  });
});

describe('selectVerifyOutcome', () => {
  function _response(
    overrides: Partial<VerifyCredentialsResponse> = {},
  ): VerifyCredentialsResponse {
    return {
      verified: true,
      verified_at: '2026-11-10T09:00:00Z',
      verify_failure_reason: null,
      accounts: [_acct()],
      ...overrides,
    };
  }

  it('success: lands on verified with the accounts from the verify call', () => {
    const outcome = selectVerifyOutcome({ ok: true, response: _response() });
    expect(outcome.kind).toBe('verified');
    if (outcome.kind === 'verified') {
      expect(outcome.accounts).toHaveLength(1);
    }
  });

  it('failure (400 from OANDA): banner carries the sanitised reason and reloads', () => {
    const outcome = selectVerifyOutcome({
      ok: false,
      error: new Error('OANDA 401: Insufficient authorization'),
    });
    expect(outcome).toEqual({
      kind: 'failed',
      pageError: 'OANDA 401: Insufficient authorization',
    });
  });

  it('failure with a non-Error rejection still yields a readable banner', () => {
    const outcome = selectVerifyOutcome({ ok: false, error: 'network down' });
    expect(outcome).toEqual({ kind: 'failed', pageError: 'network down' });
  });

  it('a 200 with verified=false is treated as failure, not silently ignored', () => {
    const outcome = selectVerifyOutcome({
      ok: true,
      response: _response({
        verified: false,
        verified_at: null,
        verify_failure_reason: 'OANDA 403: account locked',
        accounts: [],
      }),
    });
    expect(outcome).toEqual({
      kind: 'failed',
      pageError: 'OANDA 403: account locked',
    });
  });

  it('a 200 with verified=false and no reason uses a generic banner', () => {
    const outcome = selectVerifyOutcome({
      ok: true,
      response: _response({ verified: false, accounts: [] }),
    });
    expect(outcome).toEqual({
      kind: 'failed',
      pageError: 'Verification failed',
    });
  });
});
