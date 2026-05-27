/**
 * Smoke test for the v0.3.0 Evolution UX fix (Tier 1 + Tier 2).
 *
 * Not a full E2E — just a load-the-pages-and-screenshot pass against
 * whatever runs already exist in the test DB. Used to capture visual
 * verification before user review.
 *
 *   docker compose --profile test up -d
 *   cd portal/web && PLAYWRIGHT_BASE_URL=http://localhost:5174 \
 *     npx playwright test smoke-evolution-ux.spec.ts --project=chromium
 */

import { expect, test } from '@playwright/test';

test.describe('Evolution UX smoke', () => {
  test('runs list → detail → genome detail (real run)', async ({ page }) => {
    // Login as trader
    await page.goto('/login');
    await page.getByLabel(/email/i).fill('trader@example.com');
    await page.getByLabel(/password/i).fill('traderpassword');
    await page.getByRole('button', { name: /sign in|log in/i }).click();
    // After login the app redirects to either the dashboard or the
    // landing route — accept anything that isn't /login.
    await page.waitForFunction(() => !window.location.pathname.startsWith('/login'), {
      timeout: 10_000,
    });

    // Navigate to evolution list
    await page.goto('/evolution');
    await page.waitForLoadState('networkidle');
    await page.screenshot({
      path: 'test-results/ux-runs-list.png',
      fullPage: true,
    });

    // Click the most recent completed run
    const firstRunLink = page
      .getByRole('link')
      .filter({ hasText: /evorun_/ })
      .first();
    await firstRunLink.click();
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(800); // Let the chart render
    await page.screenshot({
      path: 'test-results/ux-run-detail.png',
      fullPage: true,
    });

    // Click the "Inspect best genome" shortcut
    const inspectLink = page.getByRole('link', { name: /Inspect best genome/i });
    if (await inspectLink.count()) {
      await inspectLink.click();
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(800);
      await page.screenshot({
        path: 'test-results/ux-genome-detail.png',
        fullPage: true,
      });
    }
  });
});
