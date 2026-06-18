/**
 * Chart verification gallery.
 *
 * Renders StrategyChart with each of the 9 canonical seed strategies
 * on the same fixed candle series. Use this page to sweep every
 * indicator combination at once when verifying:
 *   - scale routing (candles vs oscillator vs off-scale)
 *   - tooltip labels and value formatting
 *   - timestamp alignment between candle and indicator series
 *   - indicator math behaviour on real OANDA data
 *
 * The seed JSONs below are hand-mirrored from
 * src/agentfx/core/dsl/seeds.py. They are the canonical specs the GA
 * starts every run with — verifying these covers the indicator
 * vocabulary the system actually uses at gen 0.
 *
 * Route: /dev/chart-gallery
 * Optional query params:
 *   - instrument (default EUR_USD)
 *   - granularity (default M1)
 *   - bars (default 500)
 */

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import StrategyChart from '../components/StrategyChart';
import { API_BASE_URL } from '../services/config';
import { CandleData } from '../types/optimization';
import { DslSpec } from '../types/evolution';
import { buildIndicatorsFromDsl } from '../utils/dslIndicators';
import { summarizeDsl, inferArchetype } from '../utils/dslSummary';
import { classifyIndicator } from '../utils/indicatorScales';

interface SeedEntry {
  /** Display name shown above the chart. */
  name: string;
  /** Plain-English caption explaining what the seed does. */
  caption: string;
  /** DSL spec — must mirror src/agentfx/core/dsl/seeds.py exactly. */
  spec: DslSpec;
}

const PERMISSIVE_EXIT = {
  stop_pips: 400,
  target_pips: 800,
  max_hold_bars: 2880,
  min_hold_bars: 0,
  opposite_signal: true,
};
const DEFAULT_SIZE = { risk_pct: 0.005 };

// IMPORTANT: keep this list in 1:1 sync with SEED_SPECS in
// src/agentfx/core/dsl/seeds.py. Order matches the Python file so the
// indices map to the seed gen-0 IDs (genome_gen_<run>_000_<idx>).
const SEED_GALLERY: SeedEntry[] = [
  {
    name: 'MA crossover (trend)',
    caption: 'Long when sma(20) > sma(50); short on opposite.',
    spec: {
      version: 1,
      entry: {
        long: { lhs: { ind: 'sma', args: [20] }, cmp: '>', rhs: { ind: 'sma', args: [50] } },
        short: { lhs: { ind: 'sma', args: [20] }, cmp: '<', rhs: { ind: 'sma', args: [50] } },
      },
      exit: PERMISSIVE_EXIT,
      size: DEFAULT_SIZE,
    } as DslSpec,
  },
  {
    name: 'MACD crossover (trend)',
    caption: 'Classic 12/26/9 — long on macd_line crossing above signal.',
    spec: {
      version: 1,
      entry: {
        long: {
          lhs: { ind: 'macd_line', args: [12, 26] },
          cmp: 'crosses_above',
          rhs: { ind: 'macd_signal', args: [12, 26, 9] },
        },
        short: {
          lhs: { ind: 'macd_line', args: [12, 26] },
          cmp: 'crosses_below',
          rhs: { ind: 'macd_signal', args: [12, 26, 9] },
        },
      },
      exit: PERMISSIVE_EXIT,
      size: DEFAULT_SIZE,
    } as DslSpec,
  },
  {
    name: 'RSI mean-reversion',
    caption: 'Long when rsi(14) < 30 (oversold); short when > 70.',
    spec: {
      version: 1,
      entry: {
        long: { lhs: { ind: 'rsi', args: [14] }, cmp: '<', rhs: 30 },
        short: { lhs: { ind: 'rsi', args: [14] }, cmp: '>', rhs: 70 },
      },
      exit: PERMISSIVE_EXIT,
      size: DEFAULT_SIZE,
    } as DslSpec,
  },
  {
    name: 'Stochastic mean-reversion',
    caption: 'Long when stoch(14, 3) < 20; short when > 80.',
    spec: {
      version: 1,
      entry: {
        long: { lhs: { ind: 'stoch', args: [14, 3] }, cmp: '<', rhs: 20 },
        short: { lhs: { ind: 'stoch', args: [14, 3] }, cmp: '>', rhs: 80 },
      },
      exit: PERMISSIVE_EXIT,
      size: DEFAULT_SIZE,
    } as DslSpec,
  },
  {
    name: 'Bollinger band mean-reversion',
    caption: 'Long when close < bb_lower(20, 2); short above bb_upper.',
    spec: {
      version: 1,
      entry: {
        long: {
          lhs: { ind: 'close', args: [] },
          cmp: '<',
          rhs: { ind: 'bb_lower', args: [20, 2.0] },
        },
        short: {
          lhs: { ind: 'close', args: [] },
          cmp: '>',
          rhs: { ind: 'bb_upper', args: [20, 2.0] },
        },
      },
      exit: PERMISSIVE_EXIT,
      size: DEFAULT_SIZE,
    } as DslSpec,
  },
  {
    name: 'Donchian breakout',
    caption: 'Long when close > donchian_high(20); short below donchian_low.',
    spec: {
      version: 1,
      entry: {
        long: {
          lhs: { ind: 'close', args: [] },
          cmp: '>',
          rhs: { ind: 'donchian_high', args: [20] },
        },
        short: {
          lhs: { ind: 'close', args: [] },
          cmp: '<',
          rhs: { ind: 'donchian_low', args: [20] },
        },
      },
      exit: PERMISSIVE_EXIT,
      size: DEFAULT_SIZE,
    } as DslSpec,
  },
  {
    name: 'Ichimoku tenkan/kijun cross',
    caption: 'Long when tenkan(9) crosses above kijun(26); short on opposite.',
    spec: {
      version: 1,
      entry: {
        long: {
          lhs: { ind: 'ichimoku_tenkan', args: [9] },
          cmp: 'crosses_above',
          rhs: { ind: 'ichimoku_kijun', args: [26] },
        },
        short: {
          lhs: { ind: 'ichimoku_tenkan', args: [9] },
          cmp: 'crosses_below',
          rhs: { ind: 'ichimoku_kijun', args: [26] },
        },
      },
      exit: PERMISSIVE_EXIT,
      size: DEFAULT_SIZE,
    } as DslSpec,
  },
  {
    name: 'ADX threshold (lineage seed)',
    caption: 'Long when adx(14) > 25; short < 20. Weak standalone — exists for mutation lineage.',
    spec: {
      version: 1,
      entry: {
        long: { lhs: { ind: 'adx', args: [14] }, cmp: '>', rhs: 25 },
        short: { lhs: { ind: 'adx', args: [14] }, cmp: '<', rhs: 20 },
      },
      exit: PERMISSIVE_EXIT,
      size: DEFAULT_SIZE,
    } as DslSpec,
  },
  {
    name: 'OBV directional (lineage seed)',
    caption: 'Long when obv > 0; short < 0. Weak standalone — exists for mutation lineage.',
    spec: {
      version: 1,
      entry: {
        long: { lhs: { ind: 'obv', args: [] }, cmp: '>', rhs: 0 },
        short: { lhs: { ind: 'obv', args: [] }, cmp: '<', rhs: 0 },
      },
      exit: PERMISSIVE_EXIT,
      size: DEFAULT_SIZE,
    } as DslSpec,
  },
];

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function fetchRecentCandles(
  instrument: string,
  granularity: string,
  bars: number,
): Promise<CandleData[]> {
  // Pull the last `bars` available candles for the instrument. Walks
  // backward in time from now to find a window that has data.
  const params = new URLSearchParams({
    exchange: 'oanda',
    instrument,
    timeframe: granularity,
    limit: String(bars),
    offset: '0',
  });
  const res = await fetch(`${API_BASE_URL}/market-data/candles?${params}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`market-data ${res.status}`);
  const payload = await res.json();
  return ((payload.items ?? []) as Record<string, unknown>[]).map((c) => ({
    timestamp: c.timestamp as string,
    open: c.open as number,
    high: c.high as number,
    low: c.low as number,
    close: c.close as number,
    volume: (c.volume as number) ?? 0,
  }));
}

export default function ChartGallery() {
  const [search] = useSearchParams();
  const instrument = search.get('instrument') ?? 'EUR_USD';
  const granularity = search.get('granularity') ?? 'M1';
  const bars = Number(search.get('bars') ?? 500);

  const [candles, setCandles] = useState<CandleData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchRecentCandles(instrument, granularity, bars)
      .then((cs) => {
        if (cs.length === 0) {
          setError(
            `No candles returned for ${instrument} ${granularity}. ` +
              `Has the backend backfilled this instrument? Check ` +
              `market-data first.`,
          );
        } else {
          setCandles(cs);
        }
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [instrument, granularity, bars]);

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          Chart verification gallery
        </h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
          All {SEED_GALLERY.length} canonical seed strategies rendered on the
          same {instrument} {granularity} candle window ({candles.length} bars).
          Use to verify scale routing, tooltip labels, timestamp alignment, and
          indicator behaviour across every family the GA starts with at gen 0.
          Override via{' '}
          <code className="text-xs bg-slate-100 dark:bg-slate-800 px-1 py-0.5 rounded">
            ?instrument=GBP_USD&granularity=H1&bars=300
          </code>
          .
        </p>
      </div>

      {loading && (
        <div className="rounded-lg border border-slate-200 dark:border-slate-700 p-8 text-center text-slate-500">
          Loading candles…
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 p-4 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {!loading && !error && candles.length > 0 && (
        <div className="space-y-12">
          {SEED_GALLERY.map((entry, idx) => (
            <SeedSection
              key={entry.name}
              entry={entry}
              candles={candles}
              instrument={instrument}
              granularity={granularity}
              seedIdx={idx}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function SeedSection({
  entry,
  candles,
  instrument,
  granularity,
  seedIdx,
}: {
  entry: SeedEntry;
  candles: CandleData[];
  instrument: string;
  granularity: string;
  seedIdx: number;
}) {
  const indicators = useMemo(
    () => buildIndicatorsFromDsl(entry.spec, candles),
    [entry.spec, candles],
  );
  const archetype = useMemo(() => inferArchetype(entry.spec), [entry.spec]);
  const summary = useMemo(() => summarizeDsl(entry.spec), [entry.spec]);

  // Classify each indicator so the section header shows which render
  // scales the chart should be exercising — surfaces routing bugs at a
  // glance (an OBV genome whose row says "1 off-scale" but renders no
  // bottom panel means the routing regressed).
  const scaleCounts = useMemo(() => {
    const counts = { price: 0, oscillator: 0, off_scale: 0 } as Record<string, number>;
    for (const i of indicators) counts[classifyIndicator(i.name)] += 1;
    return counts;
  }, [indicators]);

  return (
    <section>
      <div className="mb-3 flex flex-wrap items-baseline gap-3">
        <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
          [{seedIdx}] {entry.name}
        </h2>
        {archetype && (
          <span className="px-2 py-0.5 text-xs font-medium rounded-md bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300">
            {archetype}
          </span>
        )}
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {scaleCounts.price} price · {scaleCounts.oscillator} osc ·{' '}
          {scaleCounts.off_scale} off-scale
        </span>
      </div>
      <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">
        {entry.caption}
      </p>
      <p className="mb-3 text-xs italic text-slate-500 dark:text-slate-500">
        {summary}
      </p>
      <StrategyChart
        candles={candles}
        signals={[]}
        indicators={indicators}
        instrument={instrument}
        strategy="DSL seed"
        params={{}}
        timeframe={granularity}
      />
    </section>
  );
}
