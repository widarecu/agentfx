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
