/**
 * Role-gated route wrapper (US-018 / PR-B).
 *
 * Composes with `ProtectedRoute` — auth-check runs there, role-check
 * runs here. Example:
 *
 *   <ProtectedRoute>
 *     <RequireRole allowed={['trader', 'admin']}>
 *       <PaperRuns />
 *     </RequireRole>
 *   </ProtectedRoute>
 *
 * When a user with the wrong role hits a guarded route, they land
 * back on `/` with a toast explaining why. Ownership checks on the
 * backend (`src/agentfx/api/core/rbac.py`) are the real guard; this
 * component is UX polish so a VIEWER doesn't watch the app render a
 * page and then blank out on 403 responses.
 *
 * The `RequireRole` mount also emits a `useEffect` toast rather than
 * a render-time one so the redirect isn't blocked by a stray
 * setState during render.
 */

import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { hasAllowedRole, normalizeRole, type Role } from '../utils/rbac';

interface RequireRoleProps {
  /** Roles that may render `children`. Empty = block everyone (fails closed). */
  allowed: readonly Role[];
  /** Where to send an unauthorised caller. Defaults to home. */
  fallbackPath?: string;
  children: React.ReactNode;
}

export function RequireRole({
  allowed,
  fallbackPath = '/',
  children,
}: RequireRoleProps) {
  const { user, isLoading } = useAuth();
  const { showToast } = useToast();
  const location = useLocation();

  const role = normalizeRole(user?.role);
  const authorised = hasAllowedRole(role, allowed);

  // Emit the toast in an effect so the render itself stays pure. The
  // dependency list is stable-enough that re-navigations don't
  // duplicate the toast (React skips the re-run when deps are equal).
  React.useEffect(() => {
    if (!isLoading && !authorised && user) {
      showToast(
        `This page requires ${allowed.join(' or ').toUpperCase()} access.`,
        'error',
      );
    }
    // `showToast` and `allowed` are re-created per render; `authorised`,
    // `isLoading`, `user` are the meaningful triggers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading, authorised, user?.id]);

  if (isLoading) {
    return (
      <div className="min-h-[50vh] flex items-center justify-center">
        <div className="flex flex-col items-center gap-2">
          <Loader2 className="h-8 w-8 animate-spin text-sky-600" />
          <span className="text-sm text-slate-600 dark:text-slate-400">
            Loading…
          </span>
        </div>
      </div>
    );
  }

  if (!authorised) {
    return (
      <Navigate to={fallbackPath} state={{ from: location }} replace />
    );
  }

  return <>{children}</>;
}
