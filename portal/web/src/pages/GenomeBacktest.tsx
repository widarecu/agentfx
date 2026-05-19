/**
 * GenomeBacktest — full trade-level detail for a single genome.
 *
 * The orchestrator only persists each genome's fitness + fold summary; we
 * re-execute the backtest on demand from the genome's DSL spec + the run's
 * data window via `GET /evolution-runs/{id}/genomes/{id}/backtest`. The
 * result is deterministic (same DSL + same candles = same trades), so
 * the user effectively gets the same drill-in experience as for an
 * optimisation trial without bloating the DB with per-genome trade rows.
 *
 * Layout mirrors `TrialDetail.tsx`:
 *   - Hero (genome id, instrument/window, fitness, link back to genome page)
 *   - Metrics summary (Sharpe, max DD, hit rate, trade count, total pips)
 *   - Candlestick chart with trade markers (reuses `StrategyChart`)
 *   - Trades table (reuses `TradesTable`)
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Loader2, AlertTriangle, BarChart3 } from 'lucide-react';

import StrategyChart from '../components/StrategyChart';
import TradesTable from '../components/TradesTable';
import { API_BASE_URL } from '../services/config';
import { getEvolutionRun, getGenomeBacktest } from '../services/evolutionApi';
import type {
  EvolutionRun,
  GenomeBacktestResult,
} from '../types/evolution';
import type {
  CandleData,
  MetricsViewMode,
  Trade,
  TradeSignal,
} from '../types/optimization';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function mapTrade(t: Record<string, unknown>): Trade {
  return {
    id: (t.id as string) ?? '',
    trialId: '',
    direction: t.direction as 'long' | 'short',
    entryTimestamp: t.entry_timestamp as string,
    exitTimestamp: t.exit_timestamp as string,
    entryPrice: t.entry_price as number,
    exitPrice: t.exit_price as number,
    pips: (t.pips as number) ?? 0,
    spreadCostPips: (t.spread_cost_pips as number) ?? 0,
    swapCostPips: (t.swap_cost_pips as number) ?? 0,
    netPips: (t.net_pips as number) ?? 0,
    grossPnlDollars: (t.gross_pnl_dollars as number) ?? 0,
    spreadCostDollars: (t.spread_cost_dollars as number) ?? 0,
    swapCostDollars: (t.swap_cost_dollars as number) ?? 0,
    netPnlDollars: (t.net_pnl_dollars as number) ?? 0,
    durationMinutes: (t.duration_minutes as number) ?? 0,
    positionSize: (t.position_size as number) ?? 0,
  };
}

function mapSignal(s: Record<string, unknown>): TradeSignal {
  return {
    timestamp: s.timestamp as string,
    signal: s.signal as -1 | 0 | 1,
    signalType: (s.signal_type as TradeSignal['signalType']) ?? 'entry_long',
    price: s.price as number,
    pnl: s.pnl as number | undefined,
    indicatorValues: (s.indicator_values as Record<string, number>) ?? {},
  };
}

async function fetchCandles(
  instrument: string,
  granularity: string,
  fromTime: string,
  toTime: string,
): Promise<CandleData[]> {
  // Mirror TrialDetail's paginated fetch from market-data API.
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

export default function GenomeBacktest() {
  const { runId, genomeId } = useParams<{ runId: string; genomeId: string }>();
  const navigate = useNavigate();

  const [backtest, setBacktest] = useState<GenomeBacktestResult | null>(null);
  const [run, setRun] = useState<EvolutionRun | null>(null);
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [loading, setLoading] = useState(true);
  const [candlesLoading, setCandlesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<MetricsViewMode>('pips');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!runId || !genomeId) return;
      setLoading(true);
      setError(null);
      try {
        const [bt, runData] = await Promise.all([
          getGenomeBacktest(runId, genomeId),
          getEvolutionRun(runId),
        ]);
        if (cancelled) return;
        setBacktest(bt);
        setRun(runData);
        setLoading(false);

        // Fetch candles in the background — chart can render
        // progressively as they arrive
        setCandlesLoading(true);
        try {
          const c = await fetchCandles(
            bt.instrument,
            bt.granularity,
            bt.data_range_start,
            bt.data_range_end,
          );
          if (!cancelled) setCandles(c);
        } finally {
          if (!cancelled) setCandlesLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : 'Failed to load backtest',
          );
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [runId, genomeId]);

  const trades = useMemo<Trade[]>(
    () => (backtest ? backtest.trades.map(mapTrade) : []),
    [backtest],
  );
  const signals = useMemo<TradeSignal[]>(
    () => (backtest ? backtest.signals.map(mapSignal) : []),
    [backtest],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
      </div>
    );
  }

  if (error || !backtest) {
    return (
      <div className="container mx-auto px-4 py-8 space-y-4">
        <div className="px-4 py-3 rounded-md bg-rose-50 dark:bg-rose-900/20 border border-rose-200 dark:border-rose-800 text-sm text-rose-700 dark:text-rose-300">
          {error ?? 'Backtest result not available'}
        </div>
        <button
          onClick={() => navigate(`/evolution/${runId}/genomes/${genomeId}`)}
          className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to genome
        </button>
      </div>
    );
  }

  const sharpe = backtest.metrics.sharpe;
  const maxDd = backtest.metrics.max_drawdown ?? backtest.metrics.mdd;
  const hitRate = backtest.metrics.hit_rate;
  const totalReturn = backtest.metrics.total_return;

  return (
    <div className="container mx-auto px-4 py-8 space-y-5">
      <Link
        to={`/evolution/${runId}/genomes/${genomeId}`}
        className="inline-flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700"
      >
        <ArrowLeft className="w-4 h-4" />
        Back to genome
      </Link>

      {/* Hero */}
      <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-5">
        <div className="flex items-start justify-between gap-3 mb-3 flex-wrap">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400 text-xs uppercase tracking-wide mb-1">
              <BarChart3 className="w-3.5 h-3.5" />
              Genome Backtest
            </div>
            <h1 className="font-mono text-base text-slate-900 dark:text-slate-100 break-all">
              {genomeId}
            </h1>
            <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
              Backtested on{' '}
              <span className="font-semibold text-slate-700 dark:text-slate-300">
                {backtest.instrument} {backtest.granularity}
              </span>{' '}
              from{' '}
              <span className="font-mono">
                {backtest.data_range_start.slice(0, 10)}
              </span>{' '}
              to{' '}
              <span className="font-mono">
                {backtest.data_range_end.slice(0, 10)}
              </span>
              {' · '}
              <span className="italic">training window only (folds 0..K-2)</span>
            </p>
          </div>
          <div className="flex items-center gap-1.5 text-xs">
            <button
              onClick={() => setViewMode('pips')}
              className={`px-2.5 py-1 rounded ${
                viewMode === 'pips'
                  ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 font-semibold'
                  : 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400'
              }`}
            >
              Pips
            </button>
            <button
              onClick={() => setViewMode('dollars')}
              className={`px-2.5 py-1 rounded ${
                viewMode === 'dollars'
                  ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 font-semibold'
                  : 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-400'
              }`}
            >
              Dollars
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-sm">
          <Metric
            label="Sharpe"
            value={fmtNum(sharpe, 3)}
            tone={sharpe != null && sharpe >= 1 ? 'good' : 'bad'}
          />
          <Metric
            label="Max DD"
            value={fmtPct(maxDd)}
            tone={maxDd != null && maxDd <= 0.1 ? 'good' : 'bad'}
          />
          <Metric
            label="Hit rate"
            value={fmtPct(hitRate)}
            tone={hitRate != null && hitRate >= 0.48 ? 'good' : 'bad'}
          />
          <Metric label="Trades" value={String(trades.length)} />
          <Metric
            label="Total return"
            value={fmtPct(totalReturn)}
            tone={
              totalReturn == null
                ? 'neutral'
                : totalReturn > 0
                  ? 'good'
                  : 'bad'
            }
          />
        </div>
      </div>

      {/* Chart */}
      <section className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
        <div className="flex items-center justify-between mb-2 gap-3 flex-wrap">
          <div>
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
              Price + trade markers
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
              {backtest.granularity} candles — backtest's native timeframe.
              Default view shows the last ~1500 bars so individual entries +
              exits are legible. Pan/scroll the chart or hit the maximise
              icon to see the full window.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {!candlesLoading && candles.length > 0 && (
              <span className="text-xs text-slate-500 dark:text-slate-400">
                {candles.length.toLocaleString()} bars loaded
              </span>
            )}
            {candlesLoading && (
              <span className="text-xs text-slate-500 dark:text-slate-400 inline-flex items-center gap-1">
                <Loader2 className="w-3 h-3 animate-spin" />
                loading candles…
              </span>
            )}
          </div>
        </div>
        {candlesLoading && candles.length === 0 ? (
          // Don't mount StrategyChart while candles are loading.
          // StrategyChart's isLoading branch returns a placeholder *before*
          // its chart container div is rendered, so its useRef never gets
          // populated and the init useEffect bails — the chart is never
          // created. Render our own spinner here and only mount the chart
          // once candles are present.
          <div className="flex items-center justify-center h-[500px] border border-slate-200 dark:border-slate-700 rounded-lg">
            <div className="flex flex-col items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
              <Loader2 className="w-6 h-6 animate-spin" />
              Loading {backtest.granularity} candles for the chart…
            </div>
          </div>
        ) : candles.length === 0 ? (
          <div className="text-sm text-slate-500 dark:text-slate-400 italic py-8 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 text-amber-500 shrink-0" />
            Couldn't load candles for the backtest window — the chart needs
            them but the trades + metrics above are still accurate. (The
            market-data endpoint may have rejected the date range; check
            the network tab.)
          </div>
        ) : (
          <StrategyChart
            candles={candles}
            signals={signals}
            indicators={[]}
            instrument={backtest.instrument}
            strategy="dsl"
            params={{}}
            timeframe={backtest.granularity}
            defaultVisibleBars={1500}
          />
        )}
      </section>

      {/* Trades */}
      <section className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 p-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Trades ({trades.length})
          </h2>
        </div>
        <TradesTable
          trades={trades}
          viewMode={viewMode}
          pair={backtest.instrument}
        />
      </section>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sub-components
// ─────────────────────────────────────────────────────────────────────

function Metric({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  tone?: 'good' | 'bad' | 'neutral';
}) {
  const valueClass =
    tone === 'good'
      ? 'text-emerald-700 dark:text-emerald-400'
      : tone === 'bad'
        ? 'text-rose-700 dark:text-rose-400'
        : 'text-slate-900 dark:text-slate-100';
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </div>
      <div className={`mt-0.5 text-sm font-mono font-semibold ${valueClass}`}>
        {value}
      </div>
    </div>
  );
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${(v * 100).toFixed(2)}%`;
}
