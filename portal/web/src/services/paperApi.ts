/**
 * Frontend client for /api/v1/paper-runs/* (US-021 / US-023 / PR-F).
 *
 * `approve` intentionally isn't shipped here yet — the deploy modal
 * lands in PR-J with the account picker. The rest of the surface
 * (list, approval queue, reject, kill, delete, detail) is available
 * so PR-I's list + approvals pages can render end-to-end.
 */

import { API_BASE_URL } from './config';
import type {
  ApprovalQueueList,
  BacktestOverlayResponse,
  EquityList,
  KillPaperRunResponse,
  PaperDecisionList,
  PaperRunList,
  PaperRunPublic,
  PaperRunStatus,
  PaperTradeList,
} from '../types/paper';
import type { Scope } from '../utils/rbac';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: string = res.statusText;
    try {
      const body = await res.json();
      const raw = body?.detail;
      if (Array.isArray(raw)) {
        detail = raw
          .map((e) => {
            const loc = Array.isArray(e?.loc) ? e.loc.join('.') : '';
            const msg = e?.msg ?? JSON.stringify(e);
            return loc ? `${loc}: ${msg}` : msg;
          })
          .join('; ');
      } else if (typeof raw === 'string') {
        detail = raw;
      } else if (raw != null) {
        detail = JSON.stringify(raw);
      }
    } catch {
      /* fall through */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

export async function listPaperRuns(
  page = 1,
  pageSize = 10,
  filters: {
    status?: PaperRunStatus;
    instrument?: string;
    scope?: Scope;
  } = {},
): Promise<PaperRunList> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (filters.status) params.set('status_filter', filters.status);
  if (filters.instrument) params.set('instrument', filters.instrument);
  if (filters.scope) params.set('scope', filters.scope);

  const res = await fetch(`${API_BASE_URL}/paper-runs?${params}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function getPaperRun(runId: string): Promise<PaperRunPublic> {
  const res = await fetch(`${API_BASE_URL}/paper-runs/${runId}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export interface ApprovePaperRunRequest {
  strategy_genome_id: string;
  instrument: string;
  granularity: string;
  oanda_account_id: string;
  name?: string;
  risk_config: {
    max_position_size_units: number;
    daily_loss_pct: number;
    kill_switch_dd_pct: number;
    /** v0.6.1 (US-028): optional broker-side SL. When set, passed
     *  through to `client.place_market_order` on the runner's next
     *  signal tick. Absent → no broker-side SL registered. */
    stop_loss_pips?: number;
    /** v0.6.1 (US-028): same as above, for TP. */
    take_profit_pips?: number;
  };
}

export interface ApprovePaperRunResponse {
  run_id: string;
  status: string;
  approval_status: string;
  status_url: string;
}

export async function approvePaperRun(
  body: ApprovePaperRunRequest,
): Promise<ApprovePaperRunResponse> {
  const res = await fetch(`${API_BASE_URL}/paper-runs/approve`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  return handleResponse(res);
}

export async function listApprovalQueue(): Promise<ApprovalQueueList> {
  const res = await fetch(`${API_BASE_URL}/paper-runs/approval-queue`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function rejectGenome(
  strategyGenomeId: string,
  reason: string,
): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE_URL}/paper-runs/reject`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({
      strategy_genome_id: strategyGenomeId,
      reason,
    }),
  });
  return handleResponse(res);
}

export async function killPaperRun(
  runId: string,
): Promise<KillPaperRunResponse> {
  const res = await fetch(`${API_BASE_URL}/paper-runs/${runId}/kill`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse(res);
}

/**
 * Un-halt a HALTED_MANUAL run. Backend clears the kill flag, resets
 * status to APPROVED, and re-schedules the runner subprocess. Snapshot
 * balance is preserved so P&L % stays anchored to the original start.
 */
export async function resumePaperRun(
  runId: string,
): Promise<PaperRunPublic> {
  const res = await fetch(`${API_BASE_URL}/paper-runs/${runId}/resume`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function deletePaperRun(
  runId: string,
): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE_URL}/paper-runs/${runId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  return handleResponse(res);
}

// ─────────────────────────────────────────────────────────────────────
// US-025 + US-026 read endpoints (PR-D backend / PR-F frontend).
// ─────────────────────────────────────────────────────────────────────

/**
 * Trade rows for the /paper/:id Trades table. Open trades sort ahead
 * of closed. `origin='live'` for every v0.6.1 row; `signal_reason` is
 * the DSL entry condition summary (e.g. "sma > sma") rendered in the
 * "Reason" column.
 */
export async function getTradesForRun(
  runId: string,
  { limit = 100, offset = 0 }: { limit?: number; offset?: number } = {},
): Promise<PaperTradeList> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  const res = await fetch(
    `${API_BASE_URL}/paper-runs/${runId}/trades?${params}`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}

/**
 * DecisionLog rows for the /paper/:id Decision log stream. Filtered
 * server-side to paper-specific actions (order placed / position
 * closed / signal skipped / kill / resume / etc.). `since` supports
 * incremental polling — pass the newest `timestamp` you've already
 * displayed and the server returns only newer rows.
 */
export async function getDecisionsForRun(
  runId: string,
  {
    limit = 100,
    offset = 0,
    since,
  }: { limit?: number; offset?: number; since?: string } = {},
): Promise<PaperDecisionList> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (since) params.set('since', since);
  const res = await fetch(
    `${API_BASE_URL}/paper-runs/${runId}/decisions?${params}`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}

/**
 * Persisted equity snapshots for `LivePnLChart` — one per signal tick
 * (not per health tick). Response includes `run_started_at` +
 * `snapshot_balance` so the chart can anchor without a second request.
 */
export async function getEquityForRun(
  runId: string,
  {
    fromTs,
    toTs,
    limit = 5000,
  }: { fromTs?: string; toTs?: string; limit?: number } = {},
): Promise<EquityList> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (fromTs) params.set('from_ts', fromTs);
  if (toTs) params.set('to_ts', toTs);
  const res = await fetch(
    `${API_BASE_URL}/paper-runs/${runId}/equity?${params}`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}

/**
 * Walk-forward OOS equity series from the paper run's underlying
 * evolution. `timestamp_relative_seconds` = seconds since OOS start,
 * so the frontend renders it directly against LivePnLChart's
 * hours-since-run-start axis. Returns `{source: 'unavailable', ...}`
 * when the chain breaks (pre-v0.4.0 genome, no genome linked, etc.).
 */
export async function getBacktestOverlayForRun(
  runId: string,
): Promise<BacktestOverlayResponse> {
  const res = await fetch(
    `${API_BASE_URL}/paper-runs/${runId}/backtest-overlay`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}
