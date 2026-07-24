// UTC is the display convention across paper-run views so timestamps line up
// with the chart axis (lightweight-charts renders UTC) and with server logs.
export function formatUTC(input: string | number | Date): string {
  const d = input instanceof Date ? input : new Date(input);
  return d.toLocaleString('en-GB', {
    timeZone: 'UTC',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }) + ' UTC';
}

/**
 * Human-readable elapsed time — used by the LivePnLChart header
 * ("elapsed 6d 23h 00m") and the hover tooltip's "elapsed since start"
 * row (added in PR 2). Returns `0m` for negative or same-instant deltas
 * so the caller never sees empty output.
 *
 * Format rules — always emit the two largest applicable units:
 * - < 1h: "45m"
 * - < 24h: "23h 45m"
 * - < 100d: "6d 23h"
 * - >= 100d: "123d"
 */
export function formatElapsed(
  fromMs: number | Date | string,
  toMs: number | Date | string,
): string {
  const from = fromMs instanceof Date ? fromMs.getTime()
    : typeof fromMs === 'string' ? new Date(fromMs).getTime()
    : fromMs;
  const to = toMs instanceof Date ? toMs.getTime()
    : typeof toMs === 'string' ? new Date(toMs).getTime()
    : toMs;
  const deltaSec = Math.max(0, Math.floor((to - from) / 1000));
  const days = Math.floor(deltaSec / 86_400);
  const hours = Math.floor((deltaSec % 86_400) / 3600);
  const mins = Math.floor((deltaSec % 3600) / 60);
  if (days >= 100) return `${days}d`;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins.toString().padStart(2, '0')}m`;
  return `${mins}m`;
}
