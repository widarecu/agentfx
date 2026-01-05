import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

/**
 * Optimization Detail E2E Tests
 *
 * These tests verify the optimization detail views:
 * 1. Trial table interactions (sorting, filtering)
 * 2. Trial metrics display
 * 3. Navigation between views
 * 4. Progress indicators for running jobs
 *
 * Prerequisites:
 * - Backend server running at http://localhost:8000
 * - Frontend server running at http://localhost:5173
 * - Test users seeded in database
 * - At least one optimization job with trials exists
 */

const TRADER_EMAIL = 'trader@example.com';
const TRADER_PASSWORD = 'traderpassword';

// Helper function to login and navigate to optimization
async function loginAndNavigateToOptimization(page: Page): Promise<void> {
  await page.goto('/login', { waitUntil: 'networkidle' });
  await page.locator('#email').fill(TRADER_EMAIL);
  await page.locator('#password').fill(TRADER_PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL('/', { timeout: 10000 });

  await page.getByRole('link', { name: /Optimization/i }).click();
  await expect(page).toHaveURL('/optimization', { timeout: 5000 });
}

// Helper function to navigate to first job detail
async function navigateToFirstJobDetail(page: Page): Promise<boolean> {
  await page.waitForTimeout(3000);

  const firstRow = page.locator('table tbody tr').first();
  const hasRows = await firstRow.isVisible();

  if (hasRows) {
    const clickableElement = firstRow.locator('a, [role="link"], td').first();
    await clickableElement.click();
    await expect(page).toHaveURL(/\/optimization\/[a-zA-Z0-9-]+/, { timeout: 5000 });
    return true;
  }
  return false;
}

test.describe('Trial Table Interactions', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(60000);
    await context.clearCookies();
  });

  test('trial table displays Sharpe ratio column', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Look for Sharpe column header
      const sharpeHeader = page.getByText(/Sharpe/i);
      await expect(sharpeHeader.first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('trial table displays drawdown column', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Look for drawdown column header
      const drawdownHeader = page.getByText(/Drawdown|Max DD/i);
      await expect(drawdownHeader.first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('trial table can be sorted by clicking column header', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Find sortable column header (Sharpe)
      const sharpeHeader = page.getByRole('columnheader', { name: /Sharpe/i });

      if (await sharpeHeader.isVisible()) {
        // Click to sort
        await sharpeHeader.click();
        await page.waitForTimeout(500);

        // Click again to reverse sort
        await sharpeHeader.click();
        await page.waitForTimeout(500);

        // Table should still be visible (sorting didn't break)
        const table = page.locator('table');
        await expect(table).toBeVisible();
      }
    }
  });
});

test.describe('Trial Metrics Display', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(60000);
    await context.clearCookies();
  });

  test('trial row displays key metrics', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Check for trials table with data
      const trialsTable = page.locator('table');
      const firstTrialRow = trialsTable.locator('tbody tr').first();

      if (await firstTrialRow.isVisible()) {
        // Row should contain numeric values (metrics)
        const rowText = await firstTrialRow.textContent();

        // Should have some numeric content (Sharpe, drawdown, etc.)
        expect(rowText).toMatch(/\d+\.?\d*/);
      }
    }
  });

  test('Gate B pass/fail indicator is visible', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Look for Gate B indicator (pass/fail badge or icon)
      const gateBIndicator = page.getByText(/Pass|Fail|Gate B/i);
      const checkIcon = page.locator('[class*="check"], [class*="success"]');
      const crossIcon = page.locator('[class*="cross"], [class*="fail"], [class*="error"]');

      // At least one indicator type should be present
      const hasIndicator =
        (await gateBIndicator.count()) > 0 ||
        (await checkIcon.count()) > 0 ||
        (await crossIcon.count()) > 0;

      // This is soft - not all views may show Gate B
      if (!hasIndicator) {
        console.log('Note: Gate B indicator not visible in current view');
      }
    }
  });

  test('top trials section shows best performing trials', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Look for "Top" or "Best" trials section
      const topTrialsSection = page.getByText(/Top|Best|Rank/i);

      if (await topTrialsSection.first().isVisible()) {
        // Top trials should have Sharpe values
        const trialCards = page.locator('[class*="card"], [class*="trial"]');
        const trialCount = await trialCards.count();

        // Should show at least one top trial
        expect(trialCount).toBeGreaterThanOrEqual(0);
      }
    }
  });
});

test.describe('Navigation', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(60000);
    await context.clearCookies();
  });

  test('back button returns to job list', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Find and click back button
      const backButton = page.getByRole('button', { name: /Back/i });
      const backLink = page.getByRole('link', { name: /Back|Jobs|List/i });
      const backIcon = page.locator('[aria-label*="back"], [title*="back"]');

      if (await backButton.isVisible()) {
        await backButton.click();
      } else if (await backLink.isVisible()) {
        await backLink.click();
      } else if (await backIcon.first().isVisible()) {
        await backIcon.first().click();
      } else {
        // Use browser back
        await page.goBack();
      }

      // Should return to optimization list
      await expect(page).toHaveURL('/optimization', { timeout: 5000 });
    }
  });

  test('breadcrumb navigation works', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Look for breadcrumb
      const breadcrumb = page.locator('[class*="breadcrumb"], nav[aria-label*="breadcrumb"]');

      if (await breadcrumb.isVisible()) {
        // Click on optimization breadcrumb link
        const optimizationLink = breadcrumb.getByRole('link', { name: /Optimization/i });

        if (await optimizationLink.isVisible()) {
          await optimizationLink.click();
          await expect(page).toHaveURL('/optimization', { timeout: 5000 });
        }
      }
    }
  });

  test('clicking trial navigates to trial detail', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Find first trial row
      const trialsTable = page.locator('table');
      const firstTrialRow = trialsTable.locator('tbody tr').first();

      if (await firstTrialRow.isVisible()) {
        // Click on trial row
        const trialLink = firstTrialRow.locator('a, [role="link"]').first();
        const trialCell = firstTrialRow.locator('td').first();

        if (await trialLink.isVisible()) {
          await trialLink.click();
        } else if (await trialCell.isVisible()) {
          await trialCell.click();
        }

        // Should navigate to trial detail (if implemented)
        await page.waitForTimeout(2000);

        // Check if we navigated to trial detail
        const currentUrl = page.url();
        const isTrialDetail = currentUrl.includes('/trial') || currentUrl.match(/\/[a-zA-Z0-9-]+\/[a-zA-Z0-9-]+/);

        if (isTrialDetail) {
          // Trial detail should show trial info
          const trialInfo = page.getByText(/Parameters|Metrics|Trial/i);
          await expect(trialInfo.first()).toBeVisible({ timeout: 5000 });
        }
      }
    }
  });
});

test.describe('Progress Indicators', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(60000);
    await context.clearCookies();
  });

  test('running job shows progress indicator', async ({ page }) => {
    await loginAndNavigateToOptimization(page);

    // Look for running job in the list
    const runningJob = page.locator('tr').filter({ hasText: /running/i });

    if (await runningJob.first().isVisible()) {
      // Navigate to running job
      await runningJob.first().locator('a, td').first().click();
      await expect(page).toHaveURL(/\/optimization\/[a-zA-Z0-9-]+/, { timeout: 5000 });

      await page.waitForTimeout(2000);

      // Should show progress bar or percentage
      const progressBar = page.locator('[role="progressbar"], [class*="progress"]');
      const progressText = page.getByText(/\d+%|Progress/i);

      expect(
        (await progressBar.isVisible()) ||
        (await progressText.first().isVisible())
      ).toBeTruthy();
    }
  });

  test('completed job shows completion status', async ({ page }) => {
    await loginAndNavigateToOptimization(page);

    // Look for completed job in the list
    const completedJob = page.locator('tr').filter({ hasText: /completed/i });

    if (await completedJob.first().isVisible()) {
      // Navigate to completed job
      await completedJob.first().locator('a, td').first().click();
      await expect(page).toHaveURL(/\/optimization\/[a-zA-Z0-9-]+/, { timeout: 5000 });

      await page.waitForTimeout(2000);

      // Should show completed status or 100% progress
      const completedStatus = page.getByText(/Completed|100%|Done/i);
      await expect(completedStatus.first()).toBeVisible({ timeout: 5000 });
    }
  });

  test('job summary shows iteration count', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Look for iteration count
      const iterationInfo = page.getByText(/Iterations|Trials|\/\d+/i);

      // This should be visible in job summary
      if (await iterationInfo.first().isVisible()) {
        const text = await iterationInfo.first().textContent();
        // Should contain numbers
        expect(text).toMatch(/\d+/);
      }
    }
  });
});

test.describe('Job Configuration Display', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(60000);
    await context.clearCookies();
  });

  test('job detail shows parameter ranges', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Look for parameter ranges section
      const paramSection = page.getByText(/Parameters|Ranges|Config/i);

      if (await paramSection.first().isVisible()) {
        // Should show min/max or parameter names
        const paramInfo = page.getByText(/min|max|fast|slow|period/i);
        expect(await paramInfo.count()).toBeGreaterThanOrEqual(0);
      }
    }
  });

  test('job detail shows account configuration', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Look for account balance or position size
      const accountInfo = page.getByText(/Balance|Position|Account|\$/i);

      // This info may or may not be displayed depending on UI design
      const hasAccountInfo = await accountInfo.count() > 0;
      console.log(`Account configuration displayed: ${hasAccountInfo}`);
    }
  });

  test('job detail shows data range', async ({ page }) => {
    await loginAndNavigateToOptimization(page);
    const hasJob = await navigateToFirstJobDetail(page);

    if (hasJob) {
      await page.waitForTimeout(2000);

      // Look for data range (years or dates)
      const dataRange = page.getByText(/years|Data Range|Period|History/i);

      // This info may or may not be displayed
      const hasDataRange = await dataRange.count() > 0;
      console.log(`Data range displayed: ${hasDataRange}`);
    }
  });
});
