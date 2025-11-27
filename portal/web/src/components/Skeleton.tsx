import React from 'react';

type SkeletonProps = {
  className?: string;
  count?: number;
};

export function Skeleton({ className = '', count = 1 }: SkeletonProps) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className={`animate-pulse bg-slate-200 dark:bg-slate-700 rounded ${className}`}
          aria-hidden="true"
        />
      ))}
    </>
  );
}

export function TableSkeleton({ rows = 5, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="w-full">
      <div className="grid grid-cols-5 gap-4 mb-4">
        {Array.from({ length: columns }).map((_, i) => (
          <div key={`header-${i}`} className="h-6 bg-slate-200 dark:bg-slate-700 rounded" />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="grid grid-cols-5 gap-4 mb-3">
          {Array.from({ length: columns }).map((_, colIndex) => (
            <Skeleton key={`${rowIndex}-${colIndex}`} className="h-4" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function ChartSkeleton() {
  return (
    <div className="w-full h-[480px] bg-slate-100 dark:bg-slate-800 rounded-lg p-4">
      <div className="flex justify-between mb-4">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-6 w-24" />
      </div>
      <div className="h-[400px] bg-white dark:bg-slate-900 rounded border border-slate-200 dark:border-slate-700 p-4">
        <div className="h-full w-full flex items-center justify-center">
          <Skeleton className="h-8 w-32" />
        </div>
      </div>
    </div>
  );
}
