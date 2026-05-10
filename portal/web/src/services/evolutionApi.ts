/**
 * Evolution API client (v0.3.0).
 *
 * Mirrors the structure of `optimizationApi.ts` but minus the mock-data
 * machinery — v0.3.0 ships against the real backend from day one. Field
 * names match the API's snake_case so JSON deserializes verbatim into
 * the types in `../types/evolution.ts`.
 */

import { API_BASE_URL } from './config';
import type {
  DecisionLogListResponse,
  EvolutionRun,
  EvolutionRunCreateRequest,
  EvolutionRunCreated,
  EvolutionRunListResponse,
  EvolutionRunStatus,
  Generation,
  GenerationListResponse,
  StrategyGenome,
} from '../types/evolution';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      // ignore parse failure — fall back to statusText
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

// ─────────────────────────────────────────────────────────────────────
// Run CRUD
// ─────────────────────────────────────────────────────────────────────

export async function listEvolutionRuns(
  page = 1,
  pageSize = 10,
  filters: { status?: EvolutionRunStatus; instrument?: string } = {},
): Promise<EvolutionRunListResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (filters.status) params.set('status_filter', filters.status);
  if (filters.instrument) params.set('instrument', filters.instrument);

  const res = await fetch(`${API_BASE_URL}/evolution-runs?${params}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function getEvolutionRun(runId: string): Promise<EvolutionRun> {
  const res = await fetch(`${API_BASE_URL}/evolution-runs/${runId}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function createEvolutionRun(
  body: EvolutionRunCreateRequest,
): Promise<EvolutionRunCreated> {
  const res = await fetch(`${API_BASE_URL}/evolution-runs`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  return handleResponse(res);
}

export async function stopEvolutionRun(runId: string): Promise<EvolutionRun> {
  const res = await fetch(`${API_BASE_URL}/evolution-runs/${runId}/stop`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function deleteEvolutionRun(
  runId: string,
): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${API_BASE_URL}/evolution-runs/${runId}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  return handleResponse(res);
}

// ─────────────────────────────────────────────────────────────────────
// Sub-resources
// ─────────────────────────────────────────────────────────────────────

export async function listGenerations(
  runId: string,
): Promise<GenerationListResponse> {
  const res = await fetch(
    `${API_BASE_URL}/evolution-runs/${runId}/generations`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}

export async function getGenome(
  runId: string,
  genomeId: string,
): Promise<StrategyGenome> {
  const res = await fetch(
    `${API_BASE_URL}/evolution-runs/${runId}/genomes/${genomeId}`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}

export async function listDecisions(
  runId: string,
  page = 1,
  pageSize = 50,
  agentType?: string,
): Promise<DecisionLogListResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (agentType) params.set('agent_type', agentType);
  const res = await fetch(
    `${API_BASE_URL}/evolution-runs/${runId}/decisions?${params}`,
    { headers: authHeaders() },
  );
  return handleResponse(res);
}

// ─────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────

export const STATUS_BADGE_CLASSES: Record<EvolutionRunStatus, string> = {
  queued:
    'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  running:
    'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-200 animate-pulse',
  completed:
    'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200',
  failed: 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200',
  stopped:
    'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-200',
};

export function formatLlmCost(decimalString: string): string {
  const n = Number(decimalString);
  if (!Number.isFinite(n)) return '—';
  return `$${n.toFixed(4)}`;
}
