/**
 * Vitest for `formatElapsed` in `src/utils/time.ts` — powers the
 * LivePnLChart header sub-line ("elapsed 6d 23h 00m") and, in PR 2,
 * the hover tooltip's elapsed-since-start row.
 */

import { describe, expect, it } from 'vitest';
import { formatElapsed } from '../../src/utils/time';

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

describe('formatElapsed', () => {
  it('returns 0m for negative or same-instant deltas', () => {
    const now = new Date('2026-07-24T12:00:00Z').getTime();
    expect(formatElapsed(now, now)).toBe('0m');
    expect(formatElapsed(now + MIN, now)).toBe('0m');
  });

  it('sub-hour: minute-only', () => {
    const start = new Date('2026-07-24T12:00:00Z').getTime();
    expect(formatElapsed(start, start + 5 * MIN)).toBe('5m');
    expect(formatElapsed(start, start + 59 * MIN)).toBe('59m');
  });

  it('under a day: hours + zero-padded minutes', () => {
    const start = new Date('2026-07-24T12:00:00Z').getTime();
    expect(formatElapsed(start, start + HOUR + 7 * MIN)).toBe('1h 07m');
    expect(formatElapsed(start, start + 23 * HOUR + 45 * MIN)).toBe(
      '23h 45m',
    );
  });

  it('multi-day: days + hours', () => {
    const start = new Date('2026-07-17T12:38:00Z').getTime();
    expect(formatElapsed(start, start + 6 * DAY + 23 * HOUR)).toBe('6d 23h');
    expect(formatElapsed(start, start + 1 * DAY + 4 * HOUR + 20 * MIN)).toBe(
      '1d 4h',
    );
  });

  it('collapses to days-only past 100 days', () => {
    const start = new Date('2026-01-01T00:00:00Z').getTime();
    expect(formatElapsed(start, start + 100 * DAY + 5 * HOUR)).toBe('100d');
    expect(formatElapsed(start, start + 365 * DAY)).toBe('365d');
  });

  it('accepts Date and ISO string inputs interchangeably', () => {
    const start = '2026-07-17T12:00:00Z';
    const end = new Date('2026-07-17T13:30:00Z');
    expect(formatElapsed(start, end)).toBe('1h 30m');
    expect(formatElapsed(new Date(start), '2026-07-17T13:30:00Z')).toBe(
      '1h 30m',
    );
  });
});
