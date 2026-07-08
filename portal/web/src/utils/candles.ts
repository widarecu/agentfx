/**
 * Shared candle-fetching helper.
 *
 * Extracted verbatim from `pages/GenomeBacktest.tsx` so the paper
 * run detail page can render the same StrategyChart with the same
 * candle-shape guarantees — Wilson's ask: "leverage the end product
 * to avoid reintroducing defects" from the backtest chart work.
 *
 * Paginates the /market-data/candles endpoint until the requested
 * window is fully covered. The endpoint caps per-page at 10k, so
 * long windows (e.g. 1500 M1 bars = 25 hours) may take two round
 * trips; the caller sees the full array.
 */

import { API_BASE_URL } from '../services/config';
import type { CandleData } from '../types/optimization';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export async function fetchCandles(
  instrument: string,
  granularity: string,
  fromTime: string,
  toTime: string,
): Promise<CandleData[]> {
  const out: CandleData[] = [];
  const pageSize = 10000;
  let offset = 0;
  let total = Infinity;
  while (offset < total) {
    const params = new URLSearchParams({
      exchange: 'oanda',
      instrument,
      timeframe: granularity,
      from_time: fromTime,
      to_time: toTime,
      limit: String(pageSize),
      offset: String(offset),
    });
    const res = await fetch(`${API_BASE_URL}/market-data/candles?${params}`, {
      headers: authHeaders(),
    });
    if (!res.ok) break;
    const payload = await res.json();
    total = payload.total ?? 0;
    const items: CandleData[] = (payload.items ?? []).map(
      (c: Record<string, unknown>) => ({
        timestamp: c.timestamp as string,
        open: c.open as number,
        high: c.high as number,
        low: c.low as number,
        close: c.close as number,
        volume: (c.volume as number) ?? 0,
      }),
    );
    out.push(...items);
    offset += pageSize;
    if (items.length < pageSize) break;
  }
  return out;
}

/**
 * OANDA-standard granularity → seconds. Used to figure out how far
 * back to reach when asking for "the last N bars up to now" on a
 * paper run's live chart.
 *
 * Falls back to 60s (M1) on unknown granularities so the caller
 * still gets a chart even if the run row has an unexpected value.
 */
export function granularityToSeconds(granularity: string): number {
  const table: Record<string, number> = {
    M1: 60,
    M5: 300,
    M10: 600,
    M15: 900,
    M30: 1800,
    H1: 3600,
    H4: 14400,
    D: 86400,
  };
  return table[granularity] ?? 60;
}

/**
 * "Give me the last N bars up to now" — computes the `from_time` /
 * `to_time` params and calls `fetchCandles`. The paper run detail
 * page uses this on a 60s cadence so a fresh candle rolls into the
 * chart as it closes.
 */
export async function fetchRecentCandles(
  instrument: string,
  granularity: string,
  bars: number = 1500,
): Promise<CandleData[]> {
  const now = new Date();
  const secondsBack = bars * granularityToSeconds(granularity);
  const from = new Date(now.getTime() - secondsBack * 1000);
  return fetchCandles(
    instrument,
    granularity,
    from.toISOString(),
    now.toISOString(),
  );
}
