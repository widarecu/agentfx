import { test, expect } from '@playwright/test';

// Test configuration
const TEST_TIMEOUT = 120000; // 2 minutes
const NAVIGATION_TIMEOUT = 10000;

// Test credentials
const EMAIL = 'viewer@example.com';
const PASSWORD = 'secretpassword';
const INVALID_PASSWORD = 'wrongpassword';
const NEW_USER_EMAIL = 'testuser@example.com';
const NEW_USER_PASSWORD = 'newuserpassword123';
const NEW_USER_NAME = 'Test User';

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
    
    // Verify error message and URL - expect the actual backend error message
    await expect(page.getByText('Incorrect email or password')).toBeVisible({ timeout: NAVIGATION_TIMEOUT });
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
      response => response.url().includes('/api/v1/login') && response.request().method() === 'POST'
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
    const dashboardTitle = page.getByRole('heading', { name: /Welcome to AgentFX Portal/i }).first();
    const authStatus = page.getByText(/Successfully authenticated/i).first();
    
    try {
      // Wait for either the dashboard title or authentication status
      await Promise.race([
        dashboardTitle.waitFor({ state: 'visible', timeout: 10000 }),
        authStatus.waitFor({ state: 'visible', timeout: 10000 })
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

test.describe('Registration Flows', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(TEST_TIMEOUT);
    await context.clearCookies();
    await context.clearPermissions();
  });

  test('successful registration and login', async ({ page }) => {
    // Navigate to register page
    await page.goto('/register', { waitUntil: 'networkidle' });
    
    // Fill in registration form
    await page.locator('#fullName').fill(NEW_USER_NAME);
    await page.locator('#email').fill(NEW_USER_EMAIL);
    await page.locator('#password').fill(NEW_USER_PASSWORD);
    await page.locator('#confirmPassword').fill(NEW_USER_PASSWORD);
    
    // Submit the form
    await page.getByRole('button', { name: 'Create account' }).click();
    
    // Wait a moment for registration to complete
    await page.waitForTimeout(2000);
    
    // Check current URL - might be dashboard or login page
    const currentUrl = page.url();
    
    if (currentUrl.includes('/register')) {
      // Still on register page - check for error messages
      const errorElement = await page.getByText(/error|failed|invalid/i).first();
      if (await errorElement.isVisible()) {
        console.log('Registration error:', await errorElement.textContent());
        throw new Error('Registration failed');
      }
    }
    
    // If redirected to login, complete the login flow
    if (currentUrl.includes('/login')) {
      await page.locator('#email').fill(NEW_USER_EMAIL);
      await page.locator('#password').fill(NEW_USER_PASSWORD);
      await page.getByRole('button', { name: 'Sign in' }).click();
      
      // Wait for navigation to dashboard
      await expect(page).toHaveURL('/', { timeout: NAVIGATION_TIMEOUT });
    } else if (!currentUrl.includes('/')) {
      // If not on dashboard, navigate there
      await page.goto('/', { waitUntil: 'networkidle' });
    }
    
    // Verify user is logged in (check for logout button)
    await expect(page.getByRole('button', { name: /logout/i })).toBeVisible({ timeout: 5000 });
    
    // Take screenshot for verification
    await page.screenshot({ path: 'registration-success.png', fullPage: true });
  });

  test('registration with existing email shows error', async ({ page }) => {
    // Navigate to register page
    await page.goto('/register', { waitUntil: 'networkidle' });
    
    // Try to register with existing email
    await page.locator('#fullName').fill('Another User');
    await page.locator('#email').fill(EMAIL); // Use existing email
    await page.locator('#password').fill(NEW_USER_PASSWORD);
    await page.locator('#confirmPassword').fill(NEW_USER_PASSWORD);
    
    // Submit the form
    await page.getByRole('button', { name: 'Create account' }).click();
    
    // Should stay on register page and show error
    await expect(page).toHaveURL('/register');
    await expect(page.getByText(/already exists/i)).toBeVisible({ timeout: 5000 });
    
    // Take screenshot for verification
    await page.screenshot({ path: 'registration-existing-email.png', fullPage: true });
  });

  test('registration validation errors', async ({ page }) => {
    // Navigate to register page
    await page.goto('/register', { waitUntil: 'networkidle' });
    
    // Fill minimal data to enable button, then test validation
    await page.locator('#fullName').fill('a');
    await page.locator('#email').fill('a');
    await page.locator('#password').fill('a');
    await page.locator('#confirmPassword').fill('a');
    
    // Try to submit - should show validation errors
    await page.getByRole('button', { name: 'Create account' }).click();
    
    // Wait a moment for validation to trigger
    await page.waitForTimeout(1000);
    
    // Check for any error message
    const errorElement = page.locator('div').filter({ hasText: /error|failed|invalid|required|characters/i }).first();
    await expect(errorElement).toBeVisible({ timeout: 5000 });
    
    // Test password mismatch
    await page.locator('#fullName').fill('Test User');
    await page.locator('#email').fill('test@example.com');
    await page.locator('#password').fill('password123');
    await page.locator('#confirmPassword').fill('different');
    
    await page.getByRole('button', { name: 'Create account' }).click();
    await page.waitForTimeout(1000);
    
    // Check for password mismatch error
    const passwordError = page.locator('div').filter({ hasText: /Passwords do not match/i }).first();
    await expect(passwordError).toBeVisible({ timeout: 5000 });
    
    // Test invalid email
    await page.locator('#email').fill('invalid-email');
    await page.locator('#confirmPassword').fill('password123');
    
    await page.getByRole('button', { name: 'Create account' }).click();
    await page.waitForTimeout(1000);
    
    // Check for email validation error
    const emailError = page.locator('div').filter({ hasText: /valid email/i }).first();
    await expect(emailError).toBeVisible({ timeout: 5000 });
  });
});

// ... (rest of the code remains the same)
test.describe('UI Components and Interactions', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(TEST_TIMEOUT);
    await context.clearCookies();
  });

  test('password visibility toggle', async ({ page }) => {
    // Test on login page
    await page.goto('/login', { waitUntil: 'networkidle' });
    
    const passwordInput = page.locator('#password');
    const toggleButton = page.locator('button').filter({ has: page.locator('svg') }).first();
    
    // Initially password should be hidden
    await expect(passwordInput).toHaveAttribute('type', 'password');
    
    // Click to show password
    await toggleButton.click();
    await expect(passwordInput).toHaveAttribute('type', 'text');
    
    // Click to hide password
    await toggleButton.click();
    await expect(passwordInput).toHaveAttribute('type', 'password');
    
    // Test on register page
    await page.goto('/register', { waitUntil: 'networkidle' });
    
    const regPasswordInput = page.locator('#password');
    const regToggleButtons = page.locator('button').filter({ has: page.locator('svg') });
    
    // Test password field toggle
    await expect(regPasswordInput).toHaveAttribute('type', 'password');
    await regToggleButtons.first().click();
    await expect(regPasswordInput).toHaveAttribute('type', 'text');
    await regToggleButtons.first().click();
    await expect(regPasswordInput).toHaveAttribute('type', 'password');
    
    // Test confirm password field toggle
    const confirmPasswordInput = page.locator('#confirmPassword');
    await expect(confirmPasswordInput).toHaveAttribute('type', 'password');
    await regToggleButtons.nth(1).click();
    await expect(confirmPasswordInput).toHaveAttribute('type', 'text');
  });

  test.skip('theme toggle functionality', async ({ page }) => {
    // Login first
    await page.goto('/login', { waitUntil: 'networkidle' });
    await page.locator('#email').fill(EMAIL);
    await page.locator('#password').fill(PASSWORD);
    await page.getByRole('button', { name: 'Sign in' }).click();
    
    // Wait for dashboard
    await expect(page.getByRole('button', { name: /logout/i })).toBeVisible({ timeout: 5000 });
    
    // Test theme toggle
    const themeButton = page.getByRole('button', { name: 'Light' });
    await expect(themeButton).toBeVisible();
    
    // Click to toggle to dark mode
    await themeButton.click();
    await expect(page.getByRole('button', { name: 'Dark' })).toBeVisible();
    
    // Check if dark class is applied
    const html = page.locator('html');
    await expect(html).toHaveClass(/dark/);
    
    // Click to toggle back to light mode
    await page.getByRole('button', { name: 'Dark' }).click();
    await expect(page.getByRole('button', { name: 'Light' })).toBeVisible();
    await expect(html).not.toHaveClass(/dark/);
  });

  test('navigation between login and register', async ({ page }) => {
    // Start at login page
    await page.goto('/login', { waitUntil: 'networkidle' });
    
    // Click register link
    await page.getByRole('button', { name: 'Sign up' }).click();
    await expect(page).toHaveURL('/register');
    await expect(page.getByRole('heading', { name: 'Create account' })).toBeVisible();
    
    // Click login link
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL('/login');
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
  });
});

test.describe('Error Handling and Edge Cases', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(TEST_TIMEOUT);
    await context.clearCookies();
  });

  test('network error handling', async ({ page }) => {
    // Navigate to login page
    await page.goto('/login', { waitUntil: 'networkidle' });
    
    // Simulate network offline
    await page.context().setOffline(true);
    
    // Try to login
    await page.locator('#email').fill(EMAIL);
    await page.locator('#password').fill(PASSWORD);
    await page.getByRole('button', { name: 'Sign in' }).click();
    
    // Should show network error or loading state
    await page.waitForTimeout(3000); // Wait for network timeout
    
    // Restore network
    await page.context().setOffline(false);
    
    // Try again - should work
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('button', { name: /logout/i })).toBeVisible({ timeout: 10000 });
  });

  test('form state persistence', async ({ page }) => {
    // Navigate to login page
    await page.goto('/login', { waitUntil: 'networkidle' });
    
    // Fill form with invalid credentials
    await page.locator('#email').fill(EMAIL);
    await page.locator('#password').fill(INVALID_PASSWORD);
    
    // Submit form
    await page.getByRole('button', { name: 'Sign in' }).click();
    
    // Wait for error
    await expect(page.getByText(/incorrect email or password/i)).toBeVisible({ timeout: 5000 });
    
    // Check that form values are preserved
    await expect(page.locator('#email')).toHaveValue(EMAIL);
    await expect(page.locator('#password')).toHaveValue(INVALID_PASSWORD);
    
    // Clear error by typing in email field
    await page.locator('#email').fill('modified@example.com');
    
    // Error message should disappear
    await expect(page.getByText(/incorrect email or password/i)).not.toBeVisible({ timeout: 3000 });
  });

  test('protected route redirection', async ({ page }) => {
    // Try to access protected route without authentication
    await page.goto('/', { waitUntil: 'networkidle' });
    
    // Should redirect to login page
    await expect(page).toHaveURL('/login');
    
    // Login and try again
    await page.locator('#email').fill(EMAIL);
    await page.locator('#password').fill(PASSWORD);
    await page.getByRole('button', { name: 'Sign in' }).click();
    
    // Should now access dashboard
    await expect(page).toHaveURL('/');
    await expect(page.getByRole('heading', { name: /runs/i })).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Toast Notifications', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(TEST_TIMEOUT);
    await context.clearCookies();
  });

  test('login error toast notification', async ({ page }) => {
    await page.goto('/login', { waitUntil: 'networkidle' });
    
    // Try invalid login
    await page.locator('#email').fill('nonexistent@example.com');
    await page.locator('#password').fill('wrongpassword');
    await page.getByRole('button', { name: 'Sign in' }).click();
    
    // Check for toast notification with specific error content
    await expect(page.getByText(/Incorrect email or password/)).toBeVisible({ timeout: 5000 });
    
    // Find the toast container that has the error message
    const errorToast = page.locator('div').filter({ hasText: /Incorrect email or password/ }).first();
    await expect(errorToast).toBeVisible({ timeout: 5000 });
    
    // Toast should auto-dismiss after 5 seconds
    await page.waitForTimeout(6000);
    await expect(errorToast).not.toBeVisible({ timeout: 2000 });
  });

  test('registration success toast notification', async ({ page }) => {
    await page.goto('/register', { waitUntil: 'networkidle' });
    
    // Register with new credentials
    const timestamp = Date.now();
    const uniqueEmail = `user${timestamp}@example.com`;
    
    await page.locator('#fullName').fill('Test User');
    await page.locator('#email').fill(uniqueEmail);
    await page.locator('#password').fill('newpassword123');
    await page.locator('#confirmPassword').fill('newpassword123');
    await page.getByRole('button', { name: 'Create account' }).click();
    
    // Check for success toast with specific success content
    await expect(page.getByText(/Account created successfully/)).toBeVisible({ timeout: 5000 });
    
    // Find the toast container that has the success message
    const successToast = page.locator('div').filter({ hasText: /Account created successfully/ }).first();
    await expect(successToast).toBeVisible({ timeout: 5000 });
  });
});
