/**
 * Vitest for the pure frontend RBAC primitives (US-018 / PR-B).
 *
 * Locks the invariants:
 *  - unknown / missing roles never satisfy `hasAllowedRole`
 *  - empty allow-lists are refusals, not passes
 *  - `resolveScope` never widens beyond `'my'` for non-admins
 *  - `canSeeScopeToggle` is admin-only
 *
 * The `RequireRole` component itself isn't tested here — jsdom would
 * pull heavy deps into a node-only Vitest suite for no leverage. The
 * component's behaviour is a thin wrapper over these helpers plus a
 * `<Navigate>` redirect.
 */

import { describe, expect, it } from 'vitest';
import {
  canSeeScopeToggle,
  hasAllowedRole,
  isRole,
  normalizeRole,
  resolveScope,
} from '../../src/utils/rbac';

describe('isRole', () => {
  it('accepts known lowercase roles', () => {
    expect(isRole('viewer')).toBe(true);
    expect(isRole('trader')).toBe(true);
    expect(isRole('admin')).toBe(true);
  });

  it('accepts known upper/mixed case roles', () => {
    expect(isRole('VIEWER')).toBe(true);
    expect(isRole('Trader')).toBe(true);
    expect(isRole('ADMIN')).toBe(true);
  });

  it('rejects unknown strings', () => {
    expect(isRole('superadmin')).toBe(false);
    expect(isRole('')).toBe(false);
  });

  it('rejects non-strings', () => {
    expect(isRole(null)).toBe(false);
    expect(isRole(undefined)).toBe(false);
    expect(isRole(1)).toBe(false);
    expect(isRole({})).toBe(false);
  });
});

describe('normalizeRole', () => {
  it('lowers case for known roles', () => {
    expect(normalizeRole('VIEWER')).toBe('viewer');
    expect(normalizeRole('Trader')).toBe('trader');
    expect(normalizeRole('admin')).toBe('admin');
  });

  it('returns null for unknown / missing', () => {
    expect(normalizeRole('root')).toBeNull();
    expect(normalizeRole('')).toBeNull();
    expect(normalizeRole(null)).toBeNull();
    expect(normalizeRole(undefined)).toBeNull();
    expect(normalizeRole(42)).toBeNull();
  });
});

describe('hasAllowedRole', () => {
  it('permits when role is in the allow-list', () => {
    expect(hasAllowedRole('trader', ['trader', 'admin'])).toBe(true);
    expect(hasAllowedRole('admin', ['trader', 'admin'])).toBe(true);
  });

  it('refuses when role is not in the allow-list', () => {
    expect(hasAllowedRole('viewer', ['trader', 'admin'])).toBe(false);
  });

  it('refuses missing / null / undefined roles (fail-closed)', () => {
    expect(hasAllowedRole(null, ['viewer', 'trader', 'admin'])).toBe(false);
    expect(hasAllowedRole(undefined, ['viewer', 'trader', 'admin'])).toBe(false);
  });

  it('refuses when the allow-list is empty (fail-closed)', () => {
    expect(hasAllowedRole('admin', [])).toBe(false);
  });
});

describe('resolveScope', () => {
  it('admin + requested "all" → "all"', () => {
    expect(resolveScope('admin', 'all')).toBe('all');
  });

  it('admin + requested "my" → "my"', () => {
    expect(resolveScope('admin', 'my')).toBe('my');
  });

  it('trader "all" is narrowed to "my"', () => {
    // Defense-in-depth: the toggle isn't rendered for a trader anyway,
    // but if state ever leaks the frontend won't send the widening
    // request to the backend.
    expect(resolveScope('trader', 'all')).toBe('my');
  });

  it('viewer "all" is narrowed to "my"', () => {
    expect(resolveScope('viewer', 'all')).toBe('my');
  });

  it('missing role is narrowed to "my"', () => {
    expect(resolveScope(null, 'all')).toBe('my');
    expect(resolveScope(undefined, 'all')).toBe('my');
  });
});

describe('canSeeScopeToggle', () => {
  it('true only for admin', () => {
    expect(canSeeScopeToggle('admin')).toBe(true);
  });

  it('false for viewer / trader / null / undefined', () => {
    expect(canSeeScopeToggle('viewer')).toBe(false);
    expect(canSeeScopeToggle('trader')).toBe(false);
    expect(canSeeScopeToggle(null)).toBe(false);
    expect(canSeeScopeToggle(undefined)).toBe(false);
  });
});
