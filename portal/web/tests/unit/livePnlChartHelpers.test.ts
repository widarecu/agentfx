/**
 * Vitest coverage for the axis + tick helpers in
 * `src/utils/livePnlChart.ts` (PR 1 of the LivePnLChart redesign).
 *
 * Follows the `_buildRiskConfig` / `_pickEmptyBannerScenario` pattern
 * — pure functions, node env, no jsdom, no `@testing-library/react`.
 */

import { describe, expect, it } from 'vitest';
import {
  _weekendBandsInRange,
  detectGaps,
  DEFAULT_MIN_GAP_MS,
  pickAxisTicks,
  toEquityPoints,
  type EquityPoint,
} from '../../src/utils/livePnlChart';

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

// ─────────────────────────────────────────────────────────────────────
// toEquityPoints
// ─────────────────────────────────────────────────────────────────────

describe('toEquityPoints', () => {
  it('converts ISO timestamps to millisecond epochs', () => {
    const points = toEquityPoints([
      { timestamp: '2026-07-17T12:38:00.000Z', balance: 100, unrealized_pnl: 5 },
    ]);
    expect(points[0].timestampMs).toBe(
      new Date('2026-07-17T12:38:00.000Z').getTime(),
    );
    expect(points[0].balance).toBe(100);
    expect(points[0].unrealizedPnl).toBe(5);
  });

  it('defaults unrealized_pnl to 0 when missing', () => {
    const points = toEquityPoints([
      {
        timestamp: '2026-07-17T12:38:00.000Z',
        balance: 100,
        unrealized_pnl: undefined as unknown as number,
      },
    ]);
    expect(points[0].unrealizedPnl).toBe(0);
  });
});

// ─────────────────────────────────────────────────────────────────────
// detectGaps + _containsSaturday
// ─────────────────────────────────────────────────────────────────────

function mkPoint(ts: string, balance = 100): EquityPoint {
  return {
    timestampMs: new Date(ts).getTime(),
    balance,
    unrealizedPnl: 0,
  };
}

describe('_weekendBandsInRange', () => {
  it('emits one weekend band per Fri22→Sun22 UTC window in the range', () => {
    // Range Wed Jul 15 → Wed Jul 29 contains TWO Fri→Sun weekends:
    // Fri Jul 17 22:00 → Sun Jul 19 22:00, and
    // Fri Jul 24 22:00 → Sun Jul 26 22:00.
    const bands = _weekendBandsInRange(
      new Date('2026-07-15T00:00:00Z').getTime(),
      new Date('2026-07-29T00:00:00Z').getTime(),
    );
    expect(bands).toHaveLength(2);
    expect(new Date(bands[0].startMs).toISOString()).toBe(
      '2026-07-17T22:00:00.000Z',
    );
    expect(new Date(bands[0].endMs).toISOString()).toBe(
      '2026-07-19T22:00:00.000Z',
    );
    expect(bands[0].isWeekend).toBe(true);
    expect(new Date(bands[1].startMs).toISOString()).toBe(
      '2026-07-24T22:00:00.000Z',
    );
    expect(new Date(bands[1].endMs).toISOString()).toBe(
      '2026-07-26T22:00:00.000Z',
    );
  });

  it('clips the LEFT edge to rangeStart when the weekend started before the visible window', () => {
    // 7-day range starting Sun Jul 19 12:00 — first weekend
    // (Fri 17 22:00 → Sun 19 22:00) starts BEFORE the range but its
    // right half is visible → band should start at rangeStart.
    const rangeStart = new Date('2026-07-19T12:00:00Z').getTime();
    const rangeEnd = new Date('2026-07-26T00:00:00Z').getTime();
    const bands = _weekendBandsInRange(rangeStart, rangeEnd);
    // First visible weekend is the tail end of Fri-Sun (clipped
    // left) + the next weekend Fri Jul 24 22:00.
    expect(bands).toHaveLength(2);
    expect(bands[0].startMs).toBe(rangeStart);
    expect(new Date(bands[0].endMs).toISOString()).toBe(
      '2026-07-19T22:00:00.000Z',
    );
  });

  it('clips the RIGHT edge to rangeEnd when the weekend continues past the visible window', () => {
    // Range ends Sat Jul 25 12:00 UTC — mid-weekend. Band should
    // start at Fri 22:00 and end at rangeEnd (mid-Sat).
    const rangeStart = new Date('2026-07-20T00:00:00Z').getTime();
    const rangeEnd = new Date('2026-07-25T12:00:00Z').getTime();
    const bands = _weekendBandsInRange(rangeStart, rangeEnd);
    expect(bands).toHaveLength(1);
    expect(new Date(bands[0].startMs).toISOString()).toBe(
      '2026-07-24T22:00:00.000Z',
    );
    expect(bands[0].endMs).toBe(rangeEnd);
  });

  it('emits nothing when the range is entirely mid-week', () => {
    const bands = _weekendBandsInRange(
      new Date('2026-07-20T00:00:00Z').getTime(), // Mon
      new Date('2026-07-24T00:00:00Z').getTime(), // Fri BEFORE 22:00
    );
    expect(bands).toEqual([]);
  });

  it('handles reversed / empty ranges defensively', () => {
    expect(
      _weekendBandsInRange(
        new Date('2026-07-25T00:00:00Z').getTime(),
        new Date('2026-07-20T00:00:00Z').getTime(),
      ),
    ).toEqual([]);
  });
});

describe('detectGaps', () => {
  it('returns [] for a series with no gap > threshold and no weekends in range', () => {
    // Mid-Wed → mid-Wed, no weekend anywhere.
    const series = [
      mkPoint('2026-07-15T12:00:00Z'),
      mkPoint('2026-07-15T12:01:00Z'),
      mkPoint('2026-07-15T12:02:00Z'),
    ];
    expect(
      detectGaps(
        series,
        undefined,
        new Date('2026-07-15T00:00:00Z').getTime(),
        new Date('2026-07-15T23:59:59Z').getTime(),
      ),
    ).toEqual([]);
  });

  it('emits a calendar-anchored weekend band even when the data spans it', () => {
    // Data samples at Fri 22 and Sun 22 (exact FX close/open times).
    // The weekend band comes from the calendar, not the data gap —
    // so the band's edges match Fri 22:00 / Sun 22:00 exactly.
    const series = [
      mkPoint('2026-07-17T22:00:00Z'),
      mkPoint('2026-07-19T22:00:00Z'),
    ];
    const rangeStart = new Date('2026-07-17T00:00:00Z').getTime();
    const rangeEnd = new Date('2026-07-20T00:00:00Z').getTime();
    const gaps = detectGaps(series, undefined, rangeStart, rangeEnd);
    expect(gaps).toHaveLength(1);
    expect(gaps[0].isWeekend).toBe(true);
    expect(new Date(gaps[0].startMs).toISOString()).toBe(
      '2026-07-17T22:00:00.000Z',
    );
    expect(new Date(gaps[0].endMs).toISOString()).toBe(
      '2026-07-19T22:00:00.000Z',
    );
  });

  it('flags a Wednesday runner outage as a plain GAP (isWeekend=false)', () => {
    const series = [
      mkPoint('2026-07-15T04:00:00Z'), // Wed 04:00
      mkPoint('2026-07-15T20:00:00Z'), // Wed 20:00 — 16h outage
    ];
    const gaps = detectGaps(
      series,
      undefined,
      new Date('2026-07-15T00:00:00Z').getTime(),
      new Date('2026-07-15T23:59:59Z').getTime(),
    );
    expect(gaps).toHaveLength(1);
    expect(gaps[0].isWeekend).toBe(false);
  });

  it('does not flag a data gap just under the threshold', () => {
    const series = [
      mkPoint('2026-07-15T12:00:00Z'),
      mkPoint(
        new Date(
          new Date('2026-07-15T12:00:00Z').getTime() +
            DEFAULT_MIN_GAP_MS -
            60_000,
        ).toISOString(),
      ),
    ];
    // Range is entirely mid-week, so no weekend band either.
    expect(
      detectGaps(
        series,
        undefined,
        new Date('2026-07-15T00:00:00Z').getTime(),
        new Date('2026-07-16T00:00:00Z').getTime(),
      ),
    ).toEqual([]);
  });

  it('suppresses outages that mostly overlap a weekend band', () => {
    // Wilson UAT 2026-07-25: the previous "fully contained" check
    // let outages that straddled the weekend by a few minutes escape
    // and render a redundant GAP band on top of the weekend. The new
    // rule: if the outage's overlap with any weekend > 50% of the
    // outage duration, suppress it.

    const rangeStart = new Date('2026-07-17T00:00:00Z').getTime();
    const rangeEnd = new Date('2026-07-20T00:00:00Z').getTime();

    // Case 1: outage entirely inside the weekend — clearly suppressed.
    const gapsWithin = detectGaps(
      [
        mkPoint('2026-07-17T22:30:00Z'),
        mkPoint('2026-07-19T21:30:00Z'),
      ],
      undefined,
      rangeStart,
      rangeEnd,
    );
    expect(gapsWithin.filter((g) => !g.isWeekend)).toEqual([]);

    // Case 2: outage straddles the weekend by an hour on each side —
    // overlap is ~98% of the outage duration → still suppressed.
    const gapsStraddling = detectGaps(
      [
        mkPoint('2026-07-17T21:00:00Z'), // 1h before weekend start
        mkPoint('2026-07-19T23:00:00Z'), // 1h after weekend end
      ],
      undefined,
      rangeStart,
      rangeEnd,
    );
    expect(gapsStraddling.some((g) => g.isWeekend)).toBe(true);
    expect(gapsStraddling.filter((g) => !g.isWeekend)).toEqual([]);

    // Case 3: an outage that only clips the tail of a weekend but
    // extends deep into the trading week — < 50% overlap → survives.
    const gapsPartial = detectGaps(
      [
        mkPoint('2026-07-19T21:00:00Z'), // 1h before weekend end
        mkPoint('2026-07-20T18:00:00Z'), // 20h into Monday
      ],
      undefined,
      rangeStart,
      new Date('2026-07-21T00:00:00Z').getTime(),
    );
    // Overlap = 1h out of 21h ≈ 4.7% → not suppressed.
    expect(gapsPartial.some((g) => !g.isWeekend)).toBe(true);
  });

  it('returns weekend bands even when the equity series is empty', () => {
    const rangeStart = new Date('2026-07-15T00:00:00Z').getTime();
    const rangeEnd = new Date('2026-07-22T00:00:00Z').getTime();
    // One weekend Fri Jul 17 22 → Sun Jul 19 22 in this range.
    const gaps = detectGaps([], undefined, rangeStart, rangeEnd);
    expect(gaps).toHaveLength(1);
    expect(gaps[0].isWeekend).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────
// pickAxisTicks
// ─────────────────────────────────────────────────────────────────────

describe('pickAxisTicks', () => {
  it('returns [] on empty / invalid input', () => {
    expect(pickAxisTicks(NaN, 1, '7d')).toEqual([]);
    expect(pickAxisTicks(1, NaN, '7d')).toEqual([]);
    expect(pickAxisTicks(2, 1, '7d')).toEqual([]);
    expect(pickAxisTicks(1, 1, '7d')).toEqual([]);
  });

  it('7d range: emits daily ticks with weekday labels', () => {
    // Mon 13 Jul 00:00 UTC → Sun 19 Jul 00:00 UTC — spans 6 days.
    const start = new Date('2026-07-13T00:00:00Z').getTime();
    const end = new Date('2026-07-19T00:00:00Z').getTime();
    const ticks = pickAxisTicks(start, end, '7d');
    // Major ticks (labelled): Mon, Tue, Wed, Thu, Fri, Sat, Sun
    // (7 major + 6 minor noon ticks in between)
    const majors = ticks.filter((t) => !t.minor);
    expect(majors).toHaveLength(7);
    expect(majors[0].label).toBe('Mon 13');
    expect(majors[6].label).toBe('Sun 19');
    // Minors sit at noon between the majors — should be one fewer.
    const minors = ticks.filter((t) => t.minor);
    expect(minors.length).toBeGreaterThan(0);
    // First tick lands exactly on midnight UTC.
    expect(new Date(majors[0].timestampMs).getUTCHours()).toBe(0);
  });

  it('7d range: snaps to the NEXT UTC midnight when start is mid-day', () => {
    // 12:37:24 → first tick should be 00:00 the next day, not the current one.
    const start = new Date('2026-07-13T12:37:24Z').getTime();
    const end = new Date('2026-07-14T18:00:00Z').getTime();
    const ticks = pickAxisTicks(start, end, '7d');
    const firstMajor = ticks.find((t) => !t.minor);
    expect(firstMajor?.timestampMs).toBe(new Date('2026-07-14T00:00:00Z').getTime());
    expect(firstMajor?.label).toBe('Tue 14');
  });

  it('24h range: emits 3-hourly ticks with HH:00 labels', () => {
    const start = new Date('2026-07-17T00:00:00Z').getTime();
    const end = new Date('2026-07-17T24:00:00Z').getTime();
    const ticks = pickAxisTicks(start, end, '24h');
    // 00, 03, 06, 09, 12, 15, 18, 21, 24 — 9 ticks in a 24h window
    expect(ticks).toHaveLength(9);
    expect(ticks[0].label).toBe('00:00');
    expect(ticks[1].label).toBe('03:00');
    expect(ticks[8].label).toBe('00:00');
  });

  it('24h range: attaches weekday sub-label on midnight-crossing tick', () => {
    // Wilson opens the chart mid-Monday; the 24h window crosses into Tuesday.
    const start = new Date('2026-07-13T12:00:00Z').getTime();
    const end = new Date('2026-07-14T12:00:00Z').getTime();
    const ticks = pickAxisTicks(start, end, '24h');
    const midnightTick = ticks.find(
      (t) => t.label === '00:00' && t.timestampMs >= start,
    );
    expect(midnightTick?.sublabel).toBe('Tue 14');
  });

  it('all range: uses daily stride on short spans, wider stride on longer ones', () => {
    // Short span (~7 days) → daily.
    const shortTicks = pickAxisTicks(
      new Date('2026-07-13T00:00:00Z').getTime(),
      new Date('2026-07-19T00:00:00Z').getTime(),
      'all',
    );
    expect(shortTicks.length).toBeGreaterThanOrEqual(6);

    // Medium span (30 days) → every 3 days = 10 ticks.
    const medStart = new Date('2026-07-01T00:00:00Z').getTime();
    const medEnd = medStart + 30 * DAY;
    const medTicks = pickAxisTicks(medStart, medEnd, 'all');
    expect(medTicks.length).toBeLessThan(shortTicks.length + 10);

    // Long span (180 days) → weekly.
    const longStart = new Date('2026-01-01T00:00:00Z').getTime();
    const longEnd = longStart + 180 * DAY;
    const longTicks = pickAxisTicks(longStart, longEnd, 'all');
    // Weekly stride → roughly 26 ticks.
    expect(longTicks.length).toBeLessThan(40);
    expect(longTicks.length).toBeGreaterThan(15);
  });

  it('all range: adds month sub-label on the first tick of each month', () => {
    // 6-week window straddling July → August.
    const start = new Date('2026-07-20T00:00:00Z').getTime();
    const end = new Date('2026-08-31T00:00:00Z').getTime();
    const ticks = pickAxisTicks(start, end, 'all');
    // The first tick after Aug 1 should carry `Aug` as sublabel.
    const augTick = ticks.find(
      (t) => new Date(t.timestampMs).getUTCMonth() === 7, // Aug
    );
    expect(augTick?.sublabel).toBe('Aug');
  });
});
