/**
 * Unit tests for the `selectPoolColumnView` selector that drives the
 * Pool column in `OandaAccountsTable` (AFX-7 UI follow-up 2026-10-30).
 *
 * The gap this closes: the first cut of the column always rendered
 * the DB `pool_status` ("free" by default), which read as
 * "fleet-available" even when the parent credentials had
 * `allow_fleet_pool_use=false` — misleading in exactly the state
 * every operator starts in. These tests pin all five labels so a
 * future regression on the opt-in gate fails loudly.
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
  describe('opt-in OFF', () => {
    it('renders "Not pooled" even when pool_status is the default "free"', () => {
      // The regression Wilson surfaced on 2026-10-30: default
      // `pool_status="free"` was reading as "fleet-available" when
      // the account wasn't actually poolable.
      const view = selectPoolColumnView(_acct(), false);
      expect(view.kind).toBe('not-pooled');
      expect(view.label).toBe('Not pooled');
      expect(view.title).toContain('Opt-in to fleet pool is OFF');
    });

    it('still renders "Not pooled" when a direct paper run is active', () => {
      // Wilson's actual state 2026-10-30: 2 active paper runs on
      // owner creds, opt-in off → both must render "Not pooled",
      // with a hint that a direct run is live.
      const view = selectPoolColumnView(
        _acct({ active_paper_run_id: 'run_1788294076495_paper_EUR_USD_M1' }),
        false,
      );
      expect(view.kind).toBe('not-pooled');
      expect(view.label).toBe('Not pooled');
      expect(view.title).toContain('paper run active');
    });
  });

  describe('opt-in ON', () => {
    it('fleet-held slot renders "In use (fleet)" and names the run', () => {
      const view = selectPoolColumnView(
        _acct({
          pool_status: 'in_use',
          pool_current_run_id: 'run_abc',
        }),
        true,
      );
      expect(view.kind).toBe('fleet-in-use');
      expect(view.label).toBe('In use (fleet)');
      expect(view.title).toContain('run_abc');
    });

    it('direct-path paper run renders "In use (direct)" distinctly', () => {
      // The subtle case: opt-in is ON but a run started pre-opt-in
      // is still live on the account via owner-creds. Must NOT render
      // as "Free" — that would misrepresent availability.
      const view = selectPoolColumnView(
        _acct({
          active_paper_run_id: 'run_direct_xyz',
          pool_status: 'free', // not pool-held
        }),
        true,
      );
      expect(view.kind).toBe('direct-in-use');
      expect(view.label).toBe('In use (direct)');
      expect(view.title).toContain('run_direct_xyz');
      expect(view.title).toContain('not via the fleet pool');
    });

    it('fleet-held takes precedence over active_paper_run_id', () => {
      // If BOTH are set (shouldn't normally — same run) prefer the
      // explicit fleet state so the operator sees it's leased.
      const view = selectPoolColumnView(
        _acct({
          pool_status: 'in_use',
          pool_current_run_id: 'run_fleet',
          active_paper_run_id: 'run_fleet',
        }),
        true,
      );
      expect(view.kind).toBe('fleet-in-use');
    });

    it('cooling renders countdown in title relative to injected now', () => {
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

    it('cooling with expired cooling_until shows 0s (clamped)', () => {
      const view = selectPoolColumnView(
        _acct({
          pool_status: 'cooling_down',
          pool_cooling_until: '2026-10-30T11:50:00Z',
        }),
        true,
        new Date('2026-10-30T12:00:00Z').getTime(),
      );
      expect(view.kind).toBe('cooling');
      expect(view.title).toContain('0s');
    });

    it('disabled renders a dedicated pill, not "Free"', () => {
      const view = selectPoolColumnView(
        _acct({ pool_status: 'disabled' }),
        true,
      );
      expect(view.kind).toBe('disabled');
      expect(view.label).toBe('Disabled');
    });

    it('truly free renders "Free" (fleet-available)', () => {
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
        selectPoolColumnView(_acct(), false),
        selectPoolColumnView(_acct({ pool_status: 'in_use' }), true),
        selectPoolColumnView(
          _acct({ active_paper_run_id: 'r' }),
          true,
        ),
        selectPoolColumnView(
          _acct({
            pool_status: 'cooling_down',
            pool_cooling_until: '2026-10-30T12:00:30Z',
          }),
          true,
        ),
        selectPoolColumnView(_acct({ pool_status: 'disabled' }), true),
        selectPoolColumnView(_acct(), true),
      ];
      for (const v of cases) {
        expect(v.tone.trim().length).toBeGreaterThan(0);
      }
    });
  });
});
