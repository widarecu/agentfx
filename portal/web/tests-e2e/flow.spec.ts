import { test, expect } from '@playwright/test';

// Test configuration
const TEST_TIMEOUT = 120000; // 2 minutes
const NAVIGATION_TIMEOUT = 10000;

// Test credentials
const EMAIL = 'viewer@example.com';
const PASSWORD = 'secret';
const INVALID_PASSWORD = 'wrongpassword';

test.describe('Authentication Flows', () => {
  test.beforeEach(async ({ page, context }) => {
    // Common setup for each test
    test.setTimeout(TEST_TIMEOUT);
    
    // Clear all cookies and storage before each test
    await context.clearCookies();
    await context.clearPermissions();
  });
  
  test.afterEach(async ({ page }) => {
    // Logout after each test
    try {
      await page.goto('/api/auth/logout', { waitUntil: 'networkidle' });
    } catch (error) {
      console.log('Logout failed, continuing...');
    }
  });

  test('failed login shows error message', async ({ page }) => {
    // Navigate to login page
    await page.goto('/login', { waitUntil: 'networkidle' });
    
    // Fill in the form with invalid credentials
    await page.locator('#email').fill(EMAIL);
    await page.locator('#password').fill(INVALID_PASSWORD);
    
    // Submit the form
    await page.getByRole('button', { name: 'Sign in' }).click();
    
    // Verify error message and URL
    await expect(page.getByText('Login failed')).toBeVisible({ timeout: NAVIGATION_TIMEOUT });
    await expect(page).toHaveURL('/login');
  });

  test('successful login → dashboard → run detail', async ({ page }) => {
    // Navigate to login page
    await page.goto('/login', { waitUntil: 'networkidle' });
    
    // Fill in login form
    await page.locator('#email').fill(EMAIL);
    await page.locator('#password').fill(PASSWORD);
    
    // Set up login response and navigation
    const loginResponsePromise = page.waitForResponse(
      response => response.url().includes('/api/auth/login') && response.request().method() === 'POST'
    );
    
    // Submit the form
    await page.getByRole('button', { name: 'Sign in' }).click();
    
    // Wait for login response and log details
    const loginResponse = await loginResponsePromise;
    console.log('Login response status:', loginResponse.status());
    console.log('Response headers:', JSON.stringify(loginResponse.headers(), null, 2));
    
    try {
      const responseBody = await loginResponse.json();
      console.log('Login response body:', JSON.stringify(responseBody, null, 2));
    } catch (e) {
      console.log('Could not parse login response as JSON');
    }
    
    // Wait for any navigation
    try {
      await page.waitForURL('**/*', { timeout: NAVIGATION_TIMEOUT });
      console.log('Navigated to:', page.url());
    } catch (error) {
      console.log('No navigation occurred after login, current URL:', page.url());
    }
    
    // Check if we're still on the login page
    const currentUrl = page.url();
    console.log('Current URL after login attempt:', currentUrl);
    
    if (currentUrl.includes('/login')) {
      // If we're still on the login page, check for error messages
      const errorElement = await page.getByText(/error|failed|invalid/i).first();
      if (await errorElement.isVisible()) {
        console.log('Error message on login page:', await errorElement.textContent());
      } else {
        console.log('No error message found on login page');
      }
      
      // Take a screenshot for debugging
      await page.screenshot({ path: 'login-failure.png', fullPage: true });
      console.log('Screenshot saved as login-failure.png');
      
      // Check if there are any console errors
      const consoleMessages = await page.evaluate(() => {
        return (window as any).consoleErrors || [];
      });
      
      if (consoleMessages.length > 0) {
        console.log('Console errors:', consoleMessages);
      }
      
      throw new Error('Login failed - still on login page');
    }
    
    // If we got here, we're not on the login page
    await page.waitForLoadState('networkidle');
    
    // Verify we can see the logout button
    try {
      await expect(page.getByRole('button', { name: /logout/i })).toBeVisible({ timeout: 5000 });
    } catch (error) {
      console.log('Could not find logout button, page content:', await page.content().then(c => c.substring(0, 500)));
      throw error;
    }
    
    // Wait for the page to be fully loaded
    await page.waitForLoadState('networkidle');
    
    // Take a screenshot of the current page
    await page.screenshot({ path: 'dashboard-loaded.png', fullPage: true });
    console.log('Screenshot saved as dashboard-loaded.png');
    
    // Check for any loading indicators
    const loadingIndicator = page.locator('.loading, [aria-busy="true"]');
    if (await loadingIndicator.count() > 0) {
      console.log('Waiting for loading indicators to disappear...');
      await expect(loadingIndicator).not.toBeVisible({ timeout: 10000 }).catch(e => {
        console.log('Loading indicators still present after timeout');
      });
    }
    
    // Check for dashboard content with more flexible selectors
    const dashboardTitle = page.getByRole('heading', { name: /runs/i }).first();
    const noRunsMessage = page.getByText(/no runs found/i).first();
    
    try {
      // Wait for either the dashboard title or no runs message
      await Promise.race([
        dashboardTitle.waitFor({ state: 'visible', timeout: 10000 }),
        noRunsMessage.waitFor({ state: 'visible', timeout: 10000 })
      ]);
      console.log('Found dashboard content');
    } catch (error) {
      console.log('Could not find dashboard content, checking page state...');
      const pageContent = await page.content();
      console.log('Page content (first 1000 chars):', pageContent.substring(0, 1000));
      
      // Check for common error messages
      const errorElements = await page.$$eval(
        'div[role="alert"], .error, .error-message, .alert-error', 
        els => els.map(el => el.textContent?.trim())
      );
      if (errorElements.length > 0) {
        console.log('Error messages found on page:', errorElements);
      }
      
      throw new Error('Dashboard content not found after login');
    }
    
    // If we have runs, test the run detail flow
    const viewLinks = page.getByRole('link', { name: 'View' });
    const hasRuns = await viewLinks.count() > 0;
    
    if (hasRuns) {
      // Navigate to run detail
      await Promise.all([
        page.waitForNavigation({ waitUntil: 'networkidle' }),
        viewLinks.first().click()
      ]);
      
      // Verify run detail page
      await expect(page.getByRole('heading', { name: /Run/ })).toBeVisible({ timeout: NAVIGATION_TIMEOUT });
      
      // Verify trades section
      await expect(page.getByRole('heading', { name: 'Trades' })).toBeVisible();
      
      // Test MA overlay toggle if present
      const toggleButton = page.getByRole('button', { name: /Toggle MA Overlay/ }).first();
      if (await toggleButton.isVisible()) {
        await toggleButton.click();
        await expect(toggleButton).toHaveText('Show MA Overlay');
        await toggleButton.click();
        await expect(toggleButton).toHaveText('Hide MA Overlay');
      }
    }
  });
});
