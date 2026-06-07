/**
 * Shared E2E helpers.
 *
 * Keep these minimal. Per-spec inlining is fine for one-off setup; reach
 * here only when the same multi-step flow is used by more than one spec.
 */
import { expect } from '@playwright/test';
import type { Page } from '@playwright/test';

/**
 * Open the "Start New Run" modal, pin the instrument/granularity to the
 * pair that `seed_test_candles.py` always backfills (EUR_USD / M1), and
 * submit. Waits for the navigation to the new run's detail page.
 *
 * The form auto-selects the first instrument returned by
 * `GET /market-data/instruments`, which is alphabetical — that's AUD_USD,
 * not EUR_USD. Granularity options for AUD_USD are empty in the test
 * environment (seed_test_candles only writes EUR_USD candles), which
 * leaves the Start Run button disabled forever. Pinning EUR_USD here
 * makes the test deterministic regardless of seeding order.
 */
export async function startNewEvolutionRunWithDefaults(page: Page): Promise<void> {
  await page.getByRole('button', { name: /Start New Run/i }).click();
  await expect(page.getByRole('dialog')).toBeVisible();

  await page.getByLabel('Instrument').selectOption('EUR_USD');
  // After the instrument change fires, the granularity list is re-fetched.
  // Wait for at least M1 (always seeded) to appear in the dropdown.
  await expect(page.getByLabel('Granularity')).toBeEnabled({ timeout: 5_000 });

  // Pin a data window that fits inside the seeded range. seed_test_candles
  // anchors base_time at `now - 45 days` and writes 3.5 days of M1 candles
  // forward from there, so [now-44d, now-43d] sits safely inside that
  // window. The form's default is 5 years ago → today, which is way
  // outside the seed and triggers `data_window_not_covered` at run start.
  //
  // We target the date inputs by `[type="date"]` index rather than via
  // their label because the Field component wraps the input in a <label>
  // whose accessible name includes the hint ("Data start — UTC midnight
  // of this date"), which makes label-based fill() flaky.
  const toIso = (d: Date): string => d.toISOString().slice(0, 10);
  const now = new Date();
  const start = new Date(now.getTime() - 44 * 24 * 3600 * 1000);
  const end = new Date(now.getTime() - 43 * 24 * 3600 * 1000);
  const dateInputs = page.locator('dialog input[type="date"], [role="dialog"] input[type="date"]');
  await dateInputs.nth(0).fill(toIso(start));
  await dateInputs.nth(1).fill(toIso(end));

  const submit = page.getByRole('button', { name: /^Start Run$/i });
  await expect(submit).toBeEnabled({ timeout: 5_000 });
  await submit.click();

  await page.waitForURL(/\/evolution\/evorun_/, { timeout: 15_000 });
}
