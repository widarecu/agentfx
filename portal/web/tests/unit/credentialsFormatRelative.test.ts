/**
 * Unit tests for the `formatRelative` helper in `OandaAccountsTable`.
 *
 * Kept in a separate file so the pure logic can be exercised in the
 * node Vitest env without pulling React / DOM. The helper is
 * re-exported as `_formatRelative` specifically for tests — a public
 * export tag would encourage callers to reach into it as an unstable
 * util. If a second consumer appears, hoist to a shared `src/utils/`.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { _formatRelative } from '../../src/components/OandaAccountsTable';

describe('formatRelative', () => {
  beforeEach(() => {
    // Pin "now" to a stable clock so the seconds bucket doesn't flap.
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-06T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns seconds for < 60s ago', () => {
    expect(_formatRelative('2026-07-06T11:59:53Z')).toBe('7s ago');
  });

  it('returns minutes for < 60m ago', () => {
    expect(_formatRelative('2026-07-06T11:55:00Z')).toBe('5m ago');
  });

  it('returns hours for < 24h ago', () => {
    expect(_formatRelative('2026-07-06T09:00:00Z')).toBe('3h ago');
  });

  it('returns days for >= 24h', () => {
    expect(_formatRelative('2026-07-04T12:00:00Z')).toBe('2d ago');
  });

  it('clamps to 1s ago for future timestamps (clock skew)', () => {
    // A verified_at that's slightly ahead of the client clock (skew)
    // should NOT render as "-2s ago". The floor + max clamp yields
    // "1s ago".
    expect(_formatRelative('2026-07-06T12:00:05Z')).toBe('1s ago');
  });
});
