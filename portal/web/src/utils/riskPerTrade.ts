const riskPercent = new Intl.NumberFormat(undefined, {
  style: 'percent',
  maximumFractionDigits: 3,
});

export function formatRiskPerTrade(riskPct: number | null | undefined): string {
  return riskPct != null && Number.isFinite(riskPct) ? riskPercent.format(riskPct) : '—';
}
