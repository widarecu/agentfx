/**
 * Frontend RBAC primitives (US-018 / PR-B).
 *
 * Pure — no React, no fetch. Kept in `utils/` so the Vitest suite can
 * exercise every branch in the node environment without pulling in
 * jsdom. The `RequireRole` component in `components/` wraps these with
 * the routing + toast side effects.
 *
 * The canonical role vocabulary matches the backend `UserRole` enum
 * (see `src/agentfx/api/models/user.py`). Backend stores lowercase
 * strings; we normalise here defensively in case a token payload ever
 * ships them upper-cased.
 */

export type Role = 'viewer' | 'trader' | 'admin';

export type Scope = 'my' | 'all';

const ALL_ROLES: readonly Role[] = ['viewer', 'trader', 'admin'] as const;

/** True iff `raw` is a known role string (case-insensitive). */
export function isRole(raw: unknown): raw is Role {
  if (typeof raw !== 'string') return false;
  return (ALL_ROLES as readonly string[]).includes(raw.toLowerCase());
}

/**
 * Lower-case a role string, or return `null` for unknown / missing.
 *
 * Every downstream check should route through here so an unexpected
 * role value can never be silently accepted by a case-sensitive
 * comparison against the allow-list.
 */
export function normalizeRole(raw: unknown): Role | null {
  if (typeof raw !== 'string') return null;
  const lower = raw.toLowerCase();
  return isRole(lower) ? (lower as Role) : null;
}

/**
 * Membership check for `<RequireRole allowed=[…]>`.
 *
 * Fails closed: unknown roles, missing users, and empty allow-lists
 * all return `false`. That mirrors the backend's `require_owner_or_admin`
 * fail-closed policy for NULL owners.
 */
export function hasAllowedRole(
  role: Role | null | undefined,
  allowed: readonly Role[],
): boolean {
  if (!role) return false;
  if (allowed.length === 0) return false;
  return allowed.includes(role);
}

/**
 * Decide the scope value to send on a list request.
 *
 * ADMIN + user picked "All runs" → `"all"` (backend widens the query).
 * Everyone else, or ADMIN with the toggle in "My runs" → `"my"`.
 *
 * Kept explicit rather than "just forward whatever the toggle says" so
 * the caller can't accidentally send `scope=all` for a TRADER (the
 * backend would 403, but a client-side gate avoids the round-trip).
 */
export function resolveScope(
  role: Role | null | undefined,
  requested: Scope,
): Scope {
  if (role !== 'admin') return 'my';
  return requested;
}

/**
 * True iff the caller should see the "My runs / All runs" toggle at
 * all. Only ADMIN. Kept as a helper so the two list pages
 * (`EvolutionRuns`, `Optimization`, later `PaperRuns`) render the
 * control from a single condition.
 */
export function canSeeScopeToggle(role: Role | null | undefined): boolean {
  return role === 'admin';
}
