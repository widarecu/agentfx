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
  KillPaperRunResponse,
  PaperRunList,
  PaperRunPublic,
  PaperRunStatus,
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

export async function deletePaperRun(
  runId: string,
): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE_URL}/paper-runs/${runId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  return handleResponse(res);
}
