/**
 * Mobile drill-down sub-routes for /paper/:runId/{chart,trades,decisions,risk}.
 *
 * Linked from MobilePaperRunDetail's 2×2 grid. Each is a thin
 * full-screen shell over an existing desktop component (or, for
 * risk, a simple readonly render of run.risk_config). Reachable on
 * any viewport — desktop users typing the URL directly get the same
 * scoped view.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronLeft, Loader2 } from 'lucide-react';

import StrategyChart from '../components/StrategyChart';
import { PaperRunDecisionLog } from '../components/PaperRunDecisionLog';
import { PaperRunTradesTable } from '../components/PaperRunTradesTable';
import {
  getDecisionsForRun,
  getPaperRun,
  getTradesForRun,
} from '../services/paperApi';
import type {
  PaperDecisionPublic,
  PaperRunPublic,
  PaperTradePublic,
} from '../types/paper';
import type { CandleData, TradeSignal } from '../types/optimization';
import {
  fetchCandles,
  fetchLatestCandle,
  granularityToSeconds,
} from '../utils/candles';

const POLL_INTERVAL_MS = 5_000;
const CANDLES_POLL_INTERVAL_MS = 60_000;
const CHART_BAR_COUNT = 500;

// ─── shared shell ────────────────────────────────────────────────────

function SubRouteShell({
  runId,
  title,
  subtitle,
  children,
}: {
  runId: string;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 flex flex-col">
      <header
        className="sticky top-0 z-40 flex items-center gap-2 px-3 h-12 border-b border-slate-800 backdrop-blur"
        style={{ background: 'rgba(15,23,42,0.92)' }}
      >
        <Link
          to={`/paper/${runId}`}
          className="w-8 h-8 inline-flex items-center justify-center rounded text-slate-400"
          aria-label="Back to run"
        >
          <ChevronLeft size={18} />
        </Link>
        <div className="flex flex-col min-w-0 flex-1">
          <div className="text-xs font-semibold text-slate-100 truncate">
            {title}
          </div>
          {subtitle && (
            <div className="text-[10px] font-mono text-slate-500 truncate">
              {subtitle}
            </div>
          )}
        </div>
      </header>
      <main className="flex-1 p-3">{children}</main>
    </div>
  );
}

function useRun(runId: string | undefined) {
  const [run, setRun] = useState<PaperRunPublic | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    getPaperRun(runId)
      .then((r) => {
        if (!cancelled) setRun(r);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);
  return { run, error };
}

function Loading() {
  return (
    <div className="flex items-center justify-center h-40 text-sm text-slate-400">
      <Loader2 className="w-5 h-5 animate-spin mr-2" />
      Loading…
    </div>
  );
}

// ─── /paper/:runId/chart ─────────────────────────────────────────────

export function MobilePaperChartRoute(): React.ReactElement {
  const { runId } = useParams<{ runId: string }>();
  const { run, error } = useRun(runId);
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [trades, setTrades] = useState<PaperTradePublic[]>([]);
  const [candlesLoading, setCandlesLoading] = useState(false);

  const loadCandles = useCallback(async () => {
    if (!run) return;
    setCandlesLoading(true);
    try {
      const granSec = granularityToSeconds(run.granularity);
      const to = new Date().toISOString();
      const from = new Date(
        Date.now() - granSec * CHART_BAR_COUNT * 1000,
      ).toISOString();
      const [bars, latest] = await Promise.all([
        fetchCandles(run.instrument, run.granularity, from, to),
        fetchLatestCandle(run.instrument, run.granularity),
      ]);
      const merged =
        latest && (bars.length === 0 || bars[bars.length - 1].timestamp !== latest.timestamp)
          ? [...bars, latest]
          : bars;
      setCandles(merged);
    } finally {
      setCandlesLoading(false);
    }
  }, [run]);

  const loadTrades = useCallback(async () => {
    if (!runId) return;
    try {
      const list = await getTradesForRun(runId, { limit: 500 });
      setTrades(list.items);
    } catch {
      /* keep prior */
    }
  }, [runId]);

  useEffect(() => {
    void loadCandles();
    void loadTrades();
    const c = setInterval(loadCandles, CANDLES_POLL_INTERVAL_MS);
    const t = setInterval(loadTrades, POLL_INTERVAL_MS);
    return () => {
      clearInterval(c);
      clearInterval(t);
    };
  }, [loadCandles, loadTrades]);

  const chartSignals: TradeSignal[] = useMemo(() => {
    const out: TradeSignal[] = [];
    for (const t of trades) {
      const isLong = t.side === 'buy';
      out.push({
        timestamp: t.entry_timestamp,
        signal: isLong ? 1 : -1,
        signalType: isLong ? 'entry_long' : 'entry_short',
        price: t.entry_price,
        indicatorValues: {},
      });
      if (t.exit_timestamp !== null && t.exit_price !== null) {
        const chartReason =
          t.exit_reason === 'stop' ||
          t.exit_reason === 'target' ||
          t.exit_reason === 'max_hold' ||
          t.exit_reason === 'opposite_flip'
            ? t.exit_reason
            : null;
        out.push({
          timestamp: t.exit_timestamp,
          signal: 0,
          signalType: isLong ? 'exit_long' : 'exit_short',
          price: t.exit_price,
          pnl: t.pnl ?? undefined,
          indicatorValues: {},
          exitReason: chartReason,
        });
      }
    }
    return out;
  }, [trades]);

  if (error) {
    return (
      <SubRouteShell runId={runId ?? ''} title="Chart">
        <div className="text-sm text-rose-400">{error}</div>
      </SubRouteShell>
    );
  }
  if (!run) {
    return (
      <SubRouteShell runId={runId ?? ''} title="Chart">
        <Loading />
      </SubRouteShell>
    );
  }

  return (
    <SubRouteShell
      runId={runId ?? ''}
      title={`${run.instrument} · ${run.granularity}`}
      subtitle={`${candles.length.toLocaleString()} bars · ${trades.length} trade markers`}
    >
      {candlesLoading && candles.length === 0 ? (
        <Loading />
      ) : candles.length === 0 ? (
        <div className="text-sm text-slate-500 italic py-8">
          No candles returned for {run.instrument} {run.granularity}.
        </div>
      ) : (
        <StrategyChart
          candles={candles}
          signals={chartSignals}
          indicators={[]}
          instrument={run.instrument}
          strategy="dsl"
          params={{}}
          timeframe={run.granularity}
          defaultVisibleBars={200}
        />
      )}
    </SubRouteShell>
  );
}

// ─── /paper/:runId/trades ────────────────────────────────────────────

export function MobilePaperTradesRoute(): React.ReactElement {
  const { runId } = useParams<{ runId: string }>();
  const { run, error } = useRun(runId);
  const [trades, setTrades] = useState<PaperTradePublic[]>([]);
  const [latestClose, setLatestClose] = useState<number | null>(null);

  useEffect(() => {
    if (!runId) return;
    const load = async () => {
      try {
        const list = await getTradesForRun(runId, { limit: 500 });
        setTrades(list.items);
      } catch {
        /* keep prior */
      }
    };
    void load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [runId]);

  useEffect(() => {
    if (!run) return;
    let cancelled = false;
    fetchLatestCandle(run.instrument, run.granularity).then((c) => {
      if (!cancelled && c) setLatestClose(c.close);
    });
    return () => {
      cancelled = true;
    };
  }, [run?.instrument, run?.granularity]);

  if (error) {
    return (
      <SubRouteShell runId={runId ?? ''} title="Trades">
        <div className="text-sm text-rose-400">{error}</div>
      </SubRouteShell>
    );
  }
  if (!run) {
    return (
      <SubRouteShell runId={runId ?? ''} title="Trades">
        <Loading />
      </SubRouteShell>
    );
  }
  return (
    <SubRouteShell
      runId={runId ?? ''}
      title="All trades"
      subtitle={`${trades.length} · ${run.instrument} · ${run.granularity}`}
    >
      <div className="bg-slate-800 border border-slate-700 rounded-lg p-3">
        <PaperRunTradesTable
          trades={trades}
          latestClose={latestClose}
          instrument={run.instrument}
        />
      </div>
    </SubRouteShell>
  );
}

// ─── /paper/:runId/decisions ─────────────────────────────────────────

export function MobilePaperDecisionsRoute(): React.ReactElement {
  const { runId } = useParams<{ runId: string }>();
  const { run, error } = useRun(runId);
  const [decisions, setDecisions] = useState<PaperDecisionPublic[]>([]);

  useEffect(() => {
    if (!runId) return;
    const load = async () => {
      try {
        const list = await getDecisionsForRun(runId, { limit: 200 });
        setDecisions(list.items);
      } catch {
        /* keep prior */
      }
    };
    void load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [runId]);

  if (error) {
    return (
      <SubRouteShell runId={runId ?? ''} title="Decisions">
        <div className="text-sm text-rose-400">{error}</div>
      </SubRouteShell>
    );
  }
  if (!run) {
    return (
      <SubRouteShell runId={runId ?? ''} title="Decisions">
        <Loading />
      </SubRouteShell>
    );
  }
  return (
    <SubRouteShell
      runId={runId ?? ''}
      title="Decision log"
      subtitle={`${decisions.length} entries`}
    >
      <div className="bg-slate-800 border border-slate-700 rounded-lg p-3">
        <PaperRunDecisionLog decisions={decisions} />
      </div>
    </SubRouteShell>
  );
}

// ─── /paper/:runId/risk ──────────────────────────────────────────────

export function MobilePaperRiskRoute(): React.ReactElement {
  const { runId } = useParams<{ runId: string }>();
  const { run, error } = useRun(runId);

  if (error) {
    return (
      <SubRouteShell runId={runId ?? ''} title="Risk config">
        <div className="text-sm text-rose-400">{error}</div>
      </SubRouteShell>
    );
  }
  if (!run) {
    return (
      <SubRouteShell runId={runId ?? ''} title="Risk config">
        <Loading />
      </SubRouteShell>
    );
  }

  const risk = run.risk_config;
  const rows: Array<{ label: string; value: string }> = [
    {
      label: 'Stop loss',
      value: risk?.stop_loss_pips != null ? `${risk.stop_loss_pips} pips` : '—',
    },
    {
      label: 'Take profit',
      value: risk?.take_profit_pips != null ? `${risk.take_profit_pips} pips` : '—',
    },
    {
      label: 'Daily loss cap',
      value: risk?.daily_loss_pct != null ? `${risk.daily_loss_pct.toFixed(2)}%` : '—',
    },
    {
      label: 'Kill-switch drawdown',
      value:
        risk?.kill_switch_dd_pct != null
          ? `${risk.kill_switch_dd_pct.toFixed(2)}%`
          : '—',
    },
    {
      label: 'Max position size',
      value:
        risk?.max_position_size_units != null
          ? `${risk.max_position_size_units.toLocaleString()} units`
          : 'dynamic (balance × risk_pct ÷ stop_pips)',
    },
  ];

  return (
    <SubRouteShell runId={runId ?? ''} title="Risk config" subtitle="Read-only">
      <div className="bg-slate-800 border border-slate-700 rounded-lg divide-y divide-slate-700">
        {rows.map((r) => (
          <div
            key={r.label}
            className="flex items-center justify-between px-4 py-3 text-sm"
          >
            <span className="text-slate-400">{r.label}</span>
            <span className="font-mono text-slate-100 text-right">{r.value}</span>
          </div>
        ))}
      </div>
      <p className="text-[11px] text-slate-500 mt-3 px-1">
        Edit risk config on desktop — the runner reads these values at every
        signal tick.
      </p>
    </SubRouteShell>
  );
}
