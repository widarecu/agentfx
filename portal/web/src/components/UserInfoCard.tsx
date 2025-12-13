import React from 'react';
import { User } from '../contexts/AuthContext';
import { User as UserIcon, Mail, Shield, CheckCircle, XCircle } from 'lucide-react';

interface UserInfoCardProps {
  user: User | null;
  isLoading?: boolean;
}

export default function UserInfoCard({ user, isLoading }: UserInfoCardProps) {
  if (isLoading) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <div className="animate-pulse">
          <div className="h-4 bg-slate-200 dark:bg-slate-700 rounded w-3/4 mb-4"></div>
          <div className="h-3 bg-slate-200 dark:bg-slate-700 rounded w-1/2 mb-2"></div>
          <div className="h-3 bg-slate-200 dark:bg-slate-700 rounded w-2/3"></div>
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
        <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
          <UserIcon className="h-5 w-5" />
          <p className="text-sm">No user information available</p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-6">
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-blue-100 dark:bg-blue-900/30 rounded-lg">
            <UserIcon className="h-6 w-6 text-blue-600 dark:text-blue-400" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
              {user.full_name || 'User'}
            </h3>
            <div className="flex items-center gap-2 mt-1">
              {user.is_active ? (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400">
                  <CheckCircle className="h-3 w-3" />
                  Active
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400">
                  <XCircle className="h-3 w-3" />
                  Inactive
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
          <Mail className="h-4 w-4" />
          <span className="truncate">{user.email}</span>
        </div>
        <div className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
          <Shield className="h-4 w-4" />
          <span className="capitalize">{user.role || 'user'}</span>
        </div>
      </div>
    </div>
  );
}
