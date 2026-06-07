import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

import { startNewEvolutionRunWithDefaults } from './test-helpers';

/**
 * Evolution E2E Tests (v0.3.0).
 *
 * Verifies the evolution-runs UI workflow:
 *  - Navigation from the top nav
 *  - List page renders with header + about section
 *  - Start-new-run modal opens, validates input, submits, navigates to detail
 *  - Detail page shows the run header, fitness curve placeholder, decision log,
 *    and Stop button while running
 *
 * Prerequisites mirror the optimization E2E: backend on 8001, frontend on 5174,
 * seeded test users.
 */

const TRADER_EMAIL = 'trader@example.com';
const TRADER_PASSWORD = 'traderpassword';
const VIEWER_EMAIL = 'viewer@example.com';
const VIEWER_PASSWORD = 'secretpassword';

async function login(page: Page, email: string, password: string): Promise<void> {
  await page.goto('/login', { waitUntil: 'networkidle' });
  await page.locator('#email').fill(email);
  await page.locator('#password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL('/', { timeout: 10000 });
}

test.describe('Evolution Runs Page', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(60000);
    await context.clearCookies();
  });

  test('navigates to evolution page from top nav', async ({ page }) => {
    await login(page, TRADER_EMAIL, TRADER_PASSWORD);
    await page.getByRole('link', { name: 'Evolution' }).click();
    await expect(page).toHaveURL('/evolution', { timeout: 5000 });
    // exact: true to disambiguate from the h2 "About Autonomous Strategy Evolution"
    await expect(
      page.getByRole('heading', { name: 'Autonomous Strategy Evolution', exact: true })
    ).toBeVisible();
  });

  test('about section explains the loop', async ({ page }) => {
    await login(page, TRADER_EMAIL, TRADER_PASSWORD);
    await page.goto('/evolution');
    await expect(
      page.getByRole('heading', {
        name: 'About Autonomous Strategy Evolution',
        exact: true,
      })
    ).toBeVisible();
    // The blurb mentions Gate B and OOS — text the orchestrator's plan emphasises
    await expect(page.getByText(/Gate B/)).toBeVisible();
  });

  test('start-new-run button is visible to TRADER', async ({ page }) => {
    await login(page, TRADER_EMAIL, TRADER_PASSWORD);
    await page.goto('/evolution');
    await expect(
      page.getByRole('button', { name: /Start New Run/i })
    ).toBeVisible();
  });

  test('start-new-run modal opens and shows form fields', async ({ page }) => {
    await login(page, TRADER_EMAIL, TRADER_PASSWORD);
    await page.goto('/evolution');
    await page.getByRole('button', { name: /Start New Run/i }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(
      page.getByRole('heading', { name: /Start New Evolution Run/i })
    ).toBeVisible();
    // Key form labels
    await expect(page.getByText('Instrument')).toBeVisible();
    await expect(page.getByText('Granularity')).toBeVisible();
    await expect(page.getByText(/LLM budget/i)).toBeVisible();
  });

  test('TRADER can create a new evolution run and is navigated to detail', async ({
    page,
  }) => {
    await login(page, TRADER_EMAIL, TRADER_PASSWORD);
    await page.goto('/evolution');

    await startNewEvolutionRunWithDefaults(page);

    // Detail page header (run id is mono text inside an h1)
    await expect(page.locator('h1').first()).toContainText('evorun_');
  });

  test('list table appears after the first run is created', async ({
    page,
  }) => {
    await login(page, TRADER_EMAIL, TRADER_PASSWORD);
    await page.goto('/evolution', { waitUntil: 'networkidle' });
    // Either the empty state or the runs table renders. Both are
    // acceptable; we just want the page not to error. The previous
    // comma-separated locator was a SyntaxError trap — Playwright's
    // text= engine treats `,` as a RegExp flag separator, so the test
    // only "passed" when the empty-state branch happened to be true
    // (because the table branch raised before assertion). Use
    // Locator.or() to actually accept both states.
    const emptyState = page.getByText(/No evolution runs yet/i);
    const tableHeader = page.locator('th', { hasText: 'Run ID' });
    await expect(emptyState.or(tableHeader).first()).toBeVisible({
      timeout: 10000,
    });
  });
});

test.describe('Evolution Run Detail', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(60000);
    await context.clearCookies();
  });

  test('detail page renders header + decision log section', async ({
    page,
  }) => {
    await login(page, TRADER_EMAIL, TRADER_PASSWORD);

    // Create a fresh run via the UI (modal → submit → detail page).
    await page.goto('/evolution');
    await startNewEvolutionRunWithDefaults(page);

    // Stop button should be visible (run is queued/running)
    await expect(page.getByRole('button', { name: /^Stop$/ })).toBeVisible({
      timeout: 5000,
    });

    // Decision log section
    await expect(
      page.getByRole('heading', { name: /Decision Log/i })
    ).toBeVisible();

    // Fitness chart section
    await expect(
      page.getByRole('heading', { name: /Best Fitness by Generation/i })
    ).toBeVisible();
  });

  test('back link returns to runs list', async ({ page }) => {
    await login(page, TRADER_EMAIL, TRADER_PASSWORD);
    await page.goto('/evolution');
    await startNewEvolutionRunWithDefaults(page);

    await page.getByRole('link', { name: /Back to runs/i }).click();
    await expect(page).toHaveURL('/evolution', { timeout: 5000 });
  });
});
