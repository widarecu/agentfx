/**
 * Unit tests for the `selectPoolColumnView` selector that drives the
 * Pool column in `OandaAccountsTable`.
 *
 * Rewritten 2026-11-01 for the AFX-7 collapse: one pool, pool state
 * is written at approve-time and cleared at delete-time, no "fleet
 * vs direct" distinction. These cases pin all four live labels
 * (plus the legacy "Cooling") so a regression on the precedence
 * fails loudly.
 */

import { describe, expect, it } from 'vitest';
import type { UserOandaAccountPublic } from '../../src/types/credentials';
import { selectPoolColumnView } from '../../src/components/OandaAccountsTable';

function _acct(overrides: Partial<UserOandaAccountPublic> = {}): UserOandaAccountPublic {
  return {
    oanda_account_id: '101-004-xxxx-001',
    alias: null,
    currency: 'USD',
    last_seen_at: '2026-10-30T00:00:00Z',
    active_paper_run_id: null,
    pool_status: 'free',
    pool_current_run_id: null,
    pool_in_use_since: null,
    pool_cooling_until: null,
    ...overrides,
  };
}

describe('selectPoolColumnView', () => {
  describe('IN_USE wins over everything', () => {
    it('renders "In use" and names the bound run (via pool_current_run_id)', () => {
      const view = selectPoolColumnView(
        _acct({
          pool_status: 'in_use',
          pool_current_run_id: 'run_abc',
        }),
        true,
      );
      expect(view.kind).toBe('in-use');
      expect(view.label).toBe('In use');
      expect(view.title).toContain('run_abc');
      expect(view.title).toContain('until the run is deleted');
    });

    it('falls back to active_paper_run_id if pool_current_run_id is unset', () => {
      // Defensive — the backend writes both at approve-time, but a
      // legacy row (bound before the collapse landed) might have
      // only active_paper_run_id. Still show "In use" with the run.
      const view = selectPoolColumnView(
        _acct({
          pool_status: 'in_use',
          pool_current_run_id: null,
          active_paper_run_id: 'run_legacy',
        }),
        true,
      );
      expect(view.kind).toBe('in-use');
      expect(view.title).toContain('run_legacy');
    });

    it('renders "In use" regardless of the opt-in flag', () => {
      // The whole point of the collapse: operator may manually pick
      // an account from a non-opted-in credentials row. If that
      // account is bound to a run, the badge truthfully says so.
      const view = selectPoolColumnView(
        _acct({
          pool_status: 'in_use',
          pool_current_run_id: 'run_manual',
        }),
        false, // opt-in off
      );
      expect(view.kind).toBe('in-use');
      expect(view.label).toBe('In use');
    });
  });

  describe('disabled', () => {
    it('renders a dedicated pill, higher precedence than opt-in', () => {
      const viewOptIn = selectPoolColumnView(
        _acct({ pool_status: 'disabled' }),
        true,
      );
      expect(viewOptIn.kind).toBe('disabled');
      expect(viewOptIn.label).toBe('Disabled');

      const viewNoOptIn = selectPoolColumnView(
        _acct({ pool_status: 'disabled' }),
        false,
      );
      expect(viewNoOptIn.kind).toBe('disabled');
    });
  });

  describe('opt-in OFF (idle)', () => {
    it('renders "Not pooled" when pool_status is the default "free"', () => {
      const view = selectPoolColumnView(_acct(), false);
      expect(view.kind).toBe('not-pooled');
      expect(view.label).toBe('Not pooled');
      expect(view.title).toContain('Opt-in to fleet pool is OFF');
    });
  });

  describe('legacy cooling state', () => {
    it('still renders "Cooling" if a pre-collapse row has that status', () => {
      // Allocation reads will sweep this back to FREE on the next
      // call, but until then the pill truthfully reflects the DB.
      const view = selectPoolColumnView(
        _acct({
          pool_status: 'cooling_down',
          pool_cooling_until: '2026-10-30T12:00:30Z',
        }),
        true,
        new Date('2026-10-30T12:00:00Z').getTime(),
      );
      expect(view.kind).toBe('cooling');
      expect(view.label).toBe('Cooling');
      expect(view.title).toContain('30s');
    });
  });

  describe('truly free', () => {
    it('renders "Free" when idle + opt-in ON', () => {
      const view = selectPoolColumnView(_acct(), true);
      expect(view.kind).toBe('free');
      expect(view.label).toBe('Free');
      expect(view.title).toContain('Available for the fleet');
    });
  });

  describe('tone classes stay in sync with kind', () => {
    // Smoke test: each kind maps to a non-empty tailwind class string
    // so the pill can't render invisibly on a Tailwind cache miss.
    it('every kind has a non-empty tone', () => {
      const cases = [
        selectPoolColumnView(_acct({ pool_status: 'in_use' }), true),
        selectPoolColumnView(_acct({ pool_status: 'disabled' }), true),
        selectPoolColumnView(_acct(), false), // not-pooled
        selectPoolColumnView(
          _acct({
            pool_status: 'cooling_down',
            pool_cooling_until: '2026-10-30T12:00:30Z',
          }),
          true,
        ),
        selectPoolColumnView(_acct(), true), // free
      ];
      for (const v of cases) {
        expect(v.tone.trim().length).toBeGreaterThan(0);
      }
    });
  });
});
