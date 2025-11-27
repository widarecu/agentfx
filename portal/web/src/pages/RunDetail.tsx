import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Skeleton, ChartSkeleton } from '@/components/Skeleton'
import type { ISeriesApi, Time } from 'lightweight-charts'
import { createChart } from 'lightweight-charts'

type Candle = { ts: string; o: number; h: number; l: number; c: number; v: number }

type Trade = {
  trade_id: string
  side: 'long' | 'short'
  entry_ts: string
  entry_price: number
  exit_ts: string
  exit_price: number
  net_pnl: number
}

export default function RunDetail() {
  const { runId } = useParams()
  const [pair, setPair] = useState<string>('')
  const [candles, setCandles] = useState<Candle[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [showMA, setShowMA] = useState<boolean>(true)
  const [tf, setTf] = useState<'M1' | 'M5' | 'M15' | 'H1'>('M5')
  const [range, setRange] = useState<'1d' | '7d' | '30d'>('1d')
  const [signals, setSignals] = useState<{ ma_fast: { ts: string; value: number }[]; ma_slow: { ts: string; value: number }[] }>({ ma_fast: [], ma_slow: [] })
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const chartRef = useRef<HTMLDivElement>(null)
  const chartHandle = useRef<ReturnType<typeof createChart> | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const maFastRef = useRef<ISeriesApi<'Line'> | null>(null)
  const maSlowRef = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!runId) {
      setIsLoading(false)
      return
    }
    ;(async () => {
      try {
        const run = await fetch(`/api/runs/${runId}`, { credentials: 'include' }).then((r) => r.json())
        setPair(run.pair)
        const cbody = await fetch(`/api/pairs/${run.pair}/candles?tf=M5&limit=500`, {
          credentials: 'include',
        }).then((r) => r.json())
        setCandles(cbody.items ?? [])
        const tbody = await fetch(`/api/runs/${runId}/trades?limit=200`, { credentials: 'include' }).then((r) => r.json())
        setTrades(tbody.items ?? [])
        const sbody = await fetch(`/api/runs/${runId}/signals?series=ma_fast,ma_slow&limit=5000`, { credentials: 'include' }).then((r) => r.json())
        const mf = (sbody.series?.find((s: any) => s.name === 'ma_fast')?.points ?? []).map((p: any) => ({ ts: p.ts, value: p.value }))
        const ms = (sbody.series?.find((s: any) => s.name === 'ma_slow')?.points ?? []).map((p: any) => ({ ts: p.ts, value: p.value }))
        setSignals({ ma_fast: mf, ma_slow: ms })
      } catch (e) {
        setError(String(e))
      } finally {
        setIsLoading(false)
      }
    })()
  }, [runId])

  const lwcData = useMemo(
    () =>
      candles.map((k) => ({
        time: (new Date(k.ts).getTime() / 1000) as Time,
        open: k.o,
        high: k.h,
        low: k.l,
        close: k.c,
      })),
    [candles]
  )

  useEffect(() => {
    if (!chartRef.current) return
    if (!chartHandle.current) {
      chartHandle.current = createChart(chartRef.current, { height: 480 })
      seriesRef.current = chartHandle.current.addCandlestickSeries()
      maFastRef.current = chartHandle.current.addLineSeries({ color: '#0ea5e9', lineWidth: 2 })
      maSlowRef.current = chartHandle.current.addLineSeries({ color: '#f97316', lineWidth: 2 })
    }
    seriesRef.current?.setData(lwcData)
    // Map signals to lightweight-charts format and set
    const toTime = (ts: string) => (new Date(ts).getTime() / 1000) as Time
    const fastData = signals.ma_fast.map((p) => ({ time: toTime(p.ts), value: p.value }))
    const slowData = signals.ma_slow.map((p) => ({ time: toTime(p.ts), value: p.value }))
    if (showMA) {
      maFastRef.current?.setData(fastData)
      maSlowRef.current?.setData(slowData)
      maFastRef.current?.applyOptions({ visible: true })
      maSlowRef.current?.applyOptions({ visible: true })
    } else {
      maFastRef.current?.applyOptions({ visible: false })
      maSlowRef.current?.applyOptions({ visible: false })
    }
    const handle = chartHandle.current
    const onResize = () => handle.applyOptions({ width: chartRef.current!.clientWidth })
    window.addEventListener('resize', onResize)
    onResize()
    return () => window.removeEventListener('resize', onResize)
  }, [lwcData, showMA, signals])

  if (!runId) {
    return (
      <div className="p-6 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg">
        <div className="flex items-center gap-3 text-yellow-700 dark:text-yellow-300">
          <svg className="w-5 h-5 flex-none" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
          </svg>
          <div>
            <h3 className="font-medium">No run selected</h3>
            <p className="text-sm mt-1">Please select a run from the dashboard to view details.</p>
          </div>
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <ChartSkeleton />
        <div className="p-6 bg-white dark:bg-slate-800 rounded-lg border border-slate-200 dark:border-slate-700">
          <Skeleton className="h-6 w-32 mb-4" />
          <Skeleton className="h-4 w-full mb-2" count={5} />
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
        <div className="flex items-center gap-3 text-red-700 dark:text-red-300">
          <svg className="w-5 h-5 flex-none" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clipRule="evenodd" />
          </svg>
          <div>
            <h3 className="font-medium">Failed to load run data</h3>
            <p className="text-sm mt-1">{error}</p>
            <button
              onClick={() => window.location.reload()}
              className="mt-2 text-sm text-red-700 dark:text-red-300 hover:underline"
            >
              Try again
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-xl font-semibold">Run {runId}</h2>
        <div className="text-sm text-slate-600">Pair: {pair}</div>
      </div>

      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white p-3">
        <div className="flex items-center gap-2">
          <div className="text-xs font-medium text-slate-600">TF</div>
          {(['M1', 'M5', 'M15', 'H1'] as const).map((k) => (
            <button
              key={k}
              onClick={() => setTf(k)}
              className={
                'rounded-md border px-2.5 py-1 text-xs ' +
                (tf === k
                  ? 'border-sky-500 bg-sky-50 text-sky-700'
                  : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50')
              }
              aria-pressed={tf === k}
            >
              {k}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <div className="text-xs font-medium text-slate-600">Range</div>
          {(['1d', '7d', '30d'] as const).map((k) => (
            <button
              key={k}
              onClick={() => setRange(k)}
              className={
                'rounded-md border px-2.5 py-1 text-xs ' +
                (range === k
                  ? 'border-sky-500 bg-sky-50 text-sky-700'
                  : 'border-slate-300 bg-white text-slate-700 hover:bg-slate-50')
              }
              aria-pressed={range === k}
            >
              {k}
            </button>
          ))}
        </div>
        <div className="ml-auto">
          <button
            aria-pressed={showMA}
            onClick={() => setShowMA((v) => !v)}
            className="rounded-md border border-slate-300 bg-white px-3 py-1 text-xs hover:bg-slate-50"
          >
            {showMA ? 'Hide MA overlays' : 'Show MA overlays'}
          </button>
        </div>
      </div>

      {/* Chart */}
      <div className="rounded-lg border border-slate-200 bg-white p-2">
        <div ref={chartRef} className="w-full h-[480px] rounded-md border border-slate-100" />
      </div>

      {/* Trades */}
      <div className="space-y-2">
        <h3 className="text-lg font-medium">Trades</h3>
        {trades.length === 0 ? (
          <div className="text-slate-600">No trades.</div>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-700">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">Trade ID</th>
                  <th className="px-4 py-2 font-medium">Side</th>
                  <th className="px-4 py-2 font-medium">Entry</th>
                  <th className="px-4 py-2 font-medium">Exit</th>
                  <th className="px-4 py-2 font-medium">Net PnL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {trades.map((t) => (
                  <tr key={t.trade_id} className="hover:bg-slate-50">
                    <td className="px-4 py-2 text-left font-medium text-slate-800">{t.trade_id}</td>
                    <td className="px-4 py-2 text-center uppercase">{t.side}</td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {t.entry_price} @ {new Date(t.entry_ts).toISOString()}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {t.exit_price} @ {new Date(t.exit_ts).toISOString()}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">{t.net_pnl}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
