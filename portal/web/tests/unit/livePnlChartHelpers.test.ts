/**
 * Vitest coverage for the axis + tick helpers in
 * `src/utils/livePnlChart.ts` (PR 1 of the LivePnLChart redesign).
 *
 * Follows the `_buildRiskConfig` / `_pickEmptyBannerScenario` pattern
 * — pure functions, node env, no jsdom, no `@testing-library/react`.
 */

import { describe, expect, it } from 'vitest';
import {
  _containsSaturday,
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

describe('_containsSaturday', () => {
  it('flags a window ending on Saturday', () => {
    // Fri 22:00 UTC → Sat 06:00 UTC (FX close through pre-dawn Saturday)
    expect(
      _containsSaturday(
        new Date('2026-07-17T22:00:00Z').getTime(), // Friday
        new Date('2026-07-18T06:00:00Z').getTime(), // Saturday
      ),
    ).toBe(true);
  });

  it('flags a window that spans a full Saturday', () => {
    expect(
      _containsSaturday(
        new Date('2026-07-17T22:00:00Z').getTime(), // Fri 22:00
        new Date('2026-07-19T22:00:00Z').getTime(), // Sun 22:00 — FX reopen
      ),
    ).toBe(true);
  });

  it('does NOT flag a Sunday-only window (runner outage vs market close)', () => {
    // If the runner recovered from Sunday 06:00 to Sunday 15:00,
    // Saturday was never touched — label the band `GAP`, not
    // `MARKET CLOSED`.
    expect(
      _containsSaturday(
        new Date('2026-07-19T06:00:00Z').getTime(), // Sunday 06:00
        new Date('2026-07-19T15:00:00Z').getTime(), // Sunday 15:00
      ),
    ).toBe(false);
  });

  it('does NOT flag a mid-week gap (ingestion outage on Wednesday)', () => {
    expect(
      _containsSaturday(
        new Date('2026-07-15T10:00:00Z').getTime(), // Wed
        new Date('2026-07-15T20:00:00Z').getTime(), // Wed
      ),
    ).toBe(false);
  });
});

describe('detectGaps', () => {
  it('returns [] for a series with no gap > threshold', () => {
    const series = [
      mkPoint('2026-07-17T12:00:00Z'),
      mkPoint('2026-07-17T12:01:00Z'),
      mkPoint('2026-07-17T12:02:00Z'),
    ];
    expect(detectGaps(series)).toEqual([]);
  });

  it('flags the FX weekend as a MARKET CLOSED band', () => {
    // Fri 22:00 UTC → Sun 22:00 UTC — 48h — well above default 6h.
    const series = [
      mkPoint('2026-07-17T22:00:00Z'),
      mkPoint('2026-07-19T22:00:00Z'),
    ];
    const gaps = detectGaps(series);
    expect(gaps).toHaveLength(1);
    expect(gaps[0].isWeekend).toBe(true);
    expect(gaps[0].startMs).toBe(new Date('2026-07-17T22:00:00Z').getTime());
    expect(gaps[0].endMs).toBe(new Date('2026-07-19T22:00:00Z').getTime());
  });

  it('flags a Wednesday runner outage as a plain GAP (isWeekend=false)', () => {
    const series = [
      mkPoint('2026-07-15T04:00:00Z'), // Wed 04:00
      mkPoint('2026-07-15T20:00:00Z'), // Wed 20:00 — 16h outage
    ];
    const gaps = detectGaps(series);
    expect(gaps).toHaveLength(1);
    expect(gaps[0].isWeekend).toBe(false);
  });

  it('does not flag a gap just under the threshold', () => {
    const series = [
      mkPoint('2026-07-17T12:00:00Z'),
      mkPoint(new Date(
        new Date('2026-07-17T12:00:00Z').getTime() + DEFAULT_MIN_GAP_MS - 60_000,
      ).toISOString()),
    ];
    expect(detectGaps(series)).toEqual([]);
  });

  it('detects multiple gaps in a single series', () => {
    // Weekend + a mid-next-week 8h outage. Pack trading-week samples
    // tightly (every 4h) so adjacent-point deltas stay under the 6h
    // default threshold — only the weekend and the deliberate 8h
    // outage register as gaps.
    const series: EquityPoint[] = [
      mkPoint('2026-07-17T22:00:00Z'), // Fri 22
      mkPoint('2026-07-19T22:00:00Z'), // Sun 22 — first gap here (weekend)
    ];
    // Sun 22 → Wed 22 with samples every 4h keeps every adjacent
    // delta under 6h.
    const trading: string[] = [];
    for (let t = 0; t < 3 * 24; t += 4) {
      const ts = new Date(
        new Date('2026-07-19T22:00:00Z').getTime() + t * 60 * 60 * 1000,
      ).toISOString();
      trading.push(ts);
    }
    trading.forEach((ts) => series.push(mkPoint(ts)));
    // Wed 22 → Thu 06 = 8h outage.
    series.push(mkPoint('2026-07-23T06:00:00Z'));
    const gaps = detectGaps(series);
    expect(gaps).toHaveLength(2);
    expect(gaps[0].isWeekend).toBe(true);
    expect(gaps[1].isWeekend).toBe(false);
  });

  it('handles empty and single-item series without crashing', () => {
    expect(detectGaps([])).toEqual([]);
    expect(detectGaps([mkPoint('2026-07-17T12:00:00Z')])).toEqual([]);
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
