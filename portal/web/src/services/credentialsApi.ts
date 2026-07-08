/**
 * Frontend client for /api/v1/users/me/credentials/* (US-019 / PR-C).
 *
 * Every response is passed straight through to `handleResponse` so a
 * 4xx surfaces as `Error(\`status: detail\`)` — the /settings page
 * shows that string verbatim in an inline banner. The plaintext API
 * key is never echoed by the backend on GET/PUT — the frontend never
 * has to redact.
 */

import { API_BASE_URL } from './config';
import type {
  UserOandaAccountsList,
  UserOandaCredentialsPublic,
  UserOandaCredentialsUpsert,
  VerifyCredentialsResponse,
} from '../types/credentials';

function authHeaders(): HeadersInit {
  const token = localStorage.getItem('access_token');
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (res.status === 204) {
    // DELETE endpoint returns 204 with no body.
    return undefined as unknown as T;
  }
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
      // fall through to statusText
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

/**
 * Returns null if the user has no credentials set yet (backend 404).
 * Any other error propagates — the caller renders it as a banner.
 */
export async function getMyCredentials(): Promise<UserOandaCredentialsPublic | null> {
  const res = await fetch(`${API_BASE_URL}/users/me/credentials`, {
    headers: authHeaders(),
  });
  if (res.status === 404) return null;
  return handleResponse(res);
}

export async function upsertMyCredentials(
  body: UserOandaCredentialsUpsert,
): Promise<UserOandaCredentialsPublic> {
  const res = await fetch(`${API_BASE_URL}/users/me/credentials`, {
    method: 'PUT',
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  return handleResponse(res);
}

export async function deleteMyCredentials(): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/users/me/credentials`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  await handleResponse<void>(res);
}

export async function verifyMyCredentials(): Promise<VerifyCredentialsResponse> {
  const res = await fetch(`${API_BASE_URL}/users/me/credentials/verify`, {
    method: 'POST',
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function listMyAccounts(): Promise<UserOandaAccountsList> {
  const res = await fetch(`${API_BASE_URL}/users/me/credentials/accounts`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}
