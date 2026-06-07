/**
 * Smoke test for the GenomeBacktest page — verifies the "View full backtest
 * detail" link from a genome opens a page that loads metrics, trades, and
 * the chart (or its graceful fallback) end-to-end.
 *
 * Discovers a real genome at test time by navigating from the runs list
 * rather than hard-coding an ID. Robust against the test DB getting
 * wiped or seeded with different runs across environments. If no run
 * exists yet (clean CI DB), the test creates one and waits for a
 * genome to be persisted before navigating.
 */

import { expect, test } from '@playwright/test';

import { startNewEvolutionRunWithDefaults } from './test-helpers';

test('genome → view full backtest → metrics + trades render', async ({
  page,
}) => {
  test.setTimeout(120_000);

  // Login via UI
  await page.goto('/login');
  await page.getByLabel(/email/i).fill('trader@example.com');
  await page.getByLabel(/password/i).fill('traderpassword');
  await page.getByRole('button', { name: /sign in|log in/i }).click();
  await page.waitForFunction(
    () => !window.location.pathname.startsWith('/login'),
    { timeout: 10_000 },
  );

  // Land on the runs list. If empty, create a run and wait until at
  // least one row exists.
  await page.goto('/evolution');
  await page.waitForLoadState('networkidle');

  let firstRunLink = page
    .getByRole('link')
    .filter({ hasText: /^evorun_/ })
    .first();
  if ((await firstRunLink.count()) === 0) {
    await startNewEvolutionRunWithDefaults(page);
    // We're now on the detail page. Pop back to the list so the rest
    // of the flow is symmetric.
    await page.goto('/evolution');
    await page.waitForLoadState('networkidle');
    firstRunLink = page
      .getByRole('link')
      .filter({ hasText: /^evorun_/ })
      .first();
    await expect(firstRunLink).toBeVisible({ timeout: 15_000 });
  }

  await firstRunLink.click();
  await page.waitForURL(/\/evolution\/evorun_/, { timeout: 15_000 });
  await page.waitForLoadState('networkidle');

  // Find a link to ANY genome detail page. The portal renders these
  // both as text links (best-genome shortcuts) and as href-only anchors
  // (lineage strip nodes), so we select by URL pattern rather than
  // link text. Poll briefly because a freshly-started run needs a few
  // seconds for gen 0 to evaluate and persist its first genome rows.
  let genomeLink = page.locator('a[href*="/genomes/genome_gen_"]').first();
  for (let i = 0; i < 30; i++) {
    if ((await genomeLink.count()) > 0) break;
    await page.waitForTimeout(1000);
    await page.reload();
    await page.waitForLoadState('networkidle');
    genomeLink = page.locator('a[href*="/genomes/genome_gen_"]').first();
  }
  await expect(genomeLink).toBeVisible({ timeout: 5_000 });
  // Navigate via href to avoid hit-testing under SVG overlays (lineage
  // tree nodes are <a> elements positioned absolutely and can be
  // covered by other panels at certain viewport sizes).
  const href = await genomeLink.getAttribute('href');
  expect(href).toMatch(/\/genomes\/genome_gen_/);
  await page.goto(href!);
  await page.waitForURL(/\/genomes\/genome_gen_/, { timeout: 15_000 });
  await page.waitForLoadState('networkidle');

  // From the genome detail page, click "View full backtest detail"
  const link = page.getByRole('link', { name: /View full backtest detail/i });
  await expect(link).toBeVisible({ timeout: 10_000 });
  await link.click();

  // Wait for the backtest to finish (server-side ~1s) — heading + metrics render
  await page.waitForLoadState('networkidle');
  await expect(page.getByRole('heading', { name: /Trades \(/i })).toBeVisible({
    timeout: 30_000,
  });

  await page.screenshot({
    path: 'test-results/ux-genome-backtest.png',
    fullPage: true,
  });
});
