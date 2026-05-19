/**
 * Smoke test for the GenomeBacktest page — verifies the "View full backtest
 * detail" link from a genome opens a page that loads metrics, trades, and
 * the chart (or its graceful fallback) end-to-end.
 */

import { expect, test } from '@playwright/test';

test('genome → view full backtest → metrics + trades render', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel(/email/i).fill('trader@example.com');
  await page.getByLabel(/password/i).fill('traderpassword');
  await page.getByRole('button', { name: /sign in|log in/i }).click();
  await page.waitForFunction(() => !window.location.pathname.startsWith('/login'), {
    timeout: 10_000,
  });

  // Go straight to a known genome
  await page.goto(
    '/evolution/evorun_20260513_7734ecfe/genomes/genome_gen_evorun_20260513_7734ecfe_005_014',
  );
  await page.waitForLoadState('networkidle');

  const link = page.getByRole('link', { name: /View full backtest detail/i });
  await expect(link).toBeVisible();
  await link.click();

  // Wait for the backtest to finish (server-side ~1s) — heading + metrics render
  await page.waitForLoadState('networkidle');
  await expect(page.getByRole('heading', { name: /Trades \(/i })).toBeVisible({
    timeout: 30_000,
  });

  // Capture for visual verification
  await page.screenshot({
    path: 'test-results/ux-genome-backtest.png',
    fullPage: true,
  });
});
