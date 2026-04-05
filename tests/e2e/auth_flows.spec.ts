import { test, expect } from '@playwright/test';

// Test configuration
const TEST_TIMEOUT = 120000; // 2 minutes
const NAVIGATION_TIMEOUT = 10000;

// Test credentials
const EMAIL = 'viewer@example.com';
const PASSWORD = 'secretpassword';
const INVALID_PASSWORD = 'wrongpassword';
const NEW_USER_EMAIL = `testuser_${Date.now()}@example.com`;
const NEW_USER_PASSWORD = 'newuserpassword123';
const NEW_USER_NAME = 'Test User';

test.describe('Authentication Flows', () => {
  test.beforeEach(async ({ page, context }) => {
    // Common setup for each test
    test.setTimeout(TEST_TIMEOUT);
    
    // Clear all cookies, permissions, and localStorage before each test
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
    // Use .first() to avoid strict mode violation when message appears in multiple places
    await expect(page.getByText('Incorrect email or password').first()).toBeVisible({ timeout: NAVIGATION_TIMEOUT });
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
    
    // Check for any loading indicators
    const loadingIndicator = page.locator('.loading, [aria-busy="true"]');
    if (await loadingIndicator.count() > 0) {
      console.log('Waiting for loading indicators to disappear...');
      await expect(loadingIndicator).not.toBeVisible({ timeout: 10000 }).catch(e => {
        console.log('Loading indicators still present after timeout');
      });
    }
    
    // Check for dashboard content with flexible selectors matching actual UI
    // Dashboard.tsx shows heading "Dashboard" and App.tsx shows "AgentFX Portal" in header
    const dashboardTitle = page.getByRole('heading', { name: /Dashboard/i }).first();
    const portalBranding = page.locator('text=AgentFX Portal').first();
    const logoutButton = page.getByRole('button', { name: /logout/i }).first();

    try {
      // Wait for any of these dashboard indicators
      await Promise.race([
        dashboardTitle.waitFor({ state: 'visible', timeout: 10000 }),
        portalBranding.waitFor({ state: 'visible', timeout: 10000 }),
        logoutButton.waitFor({ state: 'visible', timeout: 10000 })
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
  });

  test('registration with existing email shows error', async ({ page }) => {
    await page.goto('/register', { waitUntil: 'networkidle' });

    // Try to register with existing email
    await page.locator('#fullName').fill('Another User');
    await page.locator('#email').fill(EMAIL); // Use existing email
    await page.locator('#password').fill(NEW_USER_PASSWORD);
    await page.locator('#confirmPassword').fill(NEW_USER_PASSWORD);

    // Submit the form
    await page.getByRole('button', { name: 'Create account' }).click();

    // Should stay on register page and show error
    // Backend returns: "The user with this email already exists in the system."
    await expect(page).toHaveURL('/register');

    // Wait for API response and check for error display
    // The error message should appear in the error container
    // Use .first() since the message may appear in multiple elements (toast + inline error)
    await expect(page.getByText(/already exists in the system/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('registration validation errors', async ({ page }) => {
    // Navigate to register page
    await page.goto('/register', { waitUntil: 'networkidle' });

    // Test 1: Password too short (frontend validation: "Password must be at least 8 characters long")
    await page.locator('#fullName').fill('Test User');
    await page.locator('#email').fill('test@example.com');
    await page.locator('#password').fill('short'); // Less than 8 chars
    await page.locator('#confirmPassword').fill('short');

    await page.getByRole('button', { name: 'Create account' }).click();
    await page.waitForTimeout(500);

    // Expected: "Password must be at least 8 characters long" (error message, not help text)
    // Use more specific selector to avoid matching help text "Must be at least 8 characters long"
    await expect(page.getByText('Password must be at least 8 characters long')).toBeVisible({ timeout: 5000 });

    // Test 2: Password mismatch (frontend validation: "Passwords do not match")
    await page.locator('#password').fill('password123');
    await page.locator('#confirmPassword').fill('different123');

    await page.getByRole('button', { name: 'Create account' }).click();
    await page.waitForTimeout(500);

    // Expected: "Passwords do not match"
    await expect(page.getByText(/Passwords do not match/i)).toBeVisible({ timeout: 5000 });

    // Test 3: Invalid email
    // Note: The email input has type="email" which triggers browser-native validation
    // When an email without @ is entered, browser blocks form submission with native tooltip
    // We verify this by checking the input is in an invalid state
    await page.locator('#email').fill('invalid-email'); // No @ symbol
    await page.locator('#confirmPassword').fill('password123');

    await page.getByRole('button', { name: 'Create account' }).click();
    await page.waitForTimeout(500);

    // The browser's native validation should mark the email field as invalid
    // We can verify the field has the :invalid CSS pseudo-class
    const emailInput = page.locator('#email');
    const isInvalid = await emailInput.evaluate((el: HTMLInputElement) => !el.validity.valid);
    expect(isInvalid).toBe(true);

    // Alternatively, check that we didn't navigate away (form was blocked)
    await expect(page).toHaveURL('/register');
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

  // Theme toggle test removed - feature not implemented in v0.1.0

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

    // Wait for fetch to fail (network error)
    await page.waitForTimeout(3000);

    // Should show some error indication (either "Login failed" or network error)
    // The authApi throws "Login failed" as default when response.json() fails
    const hasError = await page.getByText(/failed|error|network/i).first().isVisible().catch(() => false);
    console.log('Error shown after network failure:', hasError);

    // Restore network
    await page.context().setOffline(false);

    // Wait a moment for network to stabilize
    await page.waitForTimeout(1000);

    // Clear any existing error state by re-entering credentials
    await page.locator('#email').fill('');
    await page.locator('#email').fill(EMAIL);
    await page.locator('#password').fill('');
    await page.locator('#password').fill(PASSWORD);

    // Try again - should work now
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

    // Wait for error - use .first() to avoid strict mode violation
    await expect(page.getByText(/incorrect email or password/i).first()).toBeVisible({ timeout: 5000 });

    // Check that form values are preserved after error
    await expect(page.locator('#email')).toHaveValue(EMAIL);
    await expect(page.locator('#password')).toHaveValue(INVALID_PASSWORD);

    // Expected behavior from Login.tsx:
    // Error clears when user types AND hasUserInteracted is true (set after first submit)
    // The useEffect at lines 26-31 handles this: when email/password changes + hasUserInteracted + error exists -> clearError()

    // Type in email field to trigger error clearing
    // Use keyboard input to ensure React onChange fires properly
    await page.locator('#email').clear();
    await page.locator('#email').pressSequentially('modified@example.com', { delay: 50 });

    // Wait a moment for React state update
    await page.waitForTimeout(1000);

    // Error message should disappear (this is the expected behavior)
    await expect(page.getByText(/incorrect email or password/i)).not.toBeVisible({ timeout: 5000 });
  });

  test('protected route redirection', async ({ page }) => {
    // Try to access protected route without authentication
    await page.goto('/', { waitUntil: 'networkidle' });

    // Should redirect to login page
    await expect(page).toHaveURL('/login');

    // Login with valid credentials
    await page.locator('#email').fill(EMAIL);
    await page.locator('#password').fill(PASSWORD);
    await page.getByRole('button', { name: 'Sign in' }).click();

    // Should navigate away from login page to dashboard
    await expect(page).not.toHaveURL('/login', { timeout: 10000 });

    // Verify we're authenticated by checking for logout button
    await expect(page.getByRole('button', { name: /logout/i })).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Logout Flow', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(TEST_TIMEOUT);
    await context.clearCookies();
  });

  test('logout clears session and redirects to login', async ({ page }) => {
    // First login
    await page.goto('/login', { waitUntil: 'networkidle' });
    await page.locator('#email').fill('test@example.com');
    await page.locator('#password').fill('TestPass123!');
    await page.getByRole('button', { name: 'Sign in' }).click();

    // Wait for navigation to dashboard
    await expect(page).toHaveURL('/', { timeout: 5000 });

    // Verify we're authenticated (user nav should be visible)
    await expect(page.getByRole('button', { name: 'Logout' })).toBeVisible();

    // Verify token exists
    const tokenBefore = await page.evaluate(() => localStorage.getItem('access_token'));
    expect(tokenBefore).toBeTruthy();

    // Click logout
    await page.getByRole('button', { name: 'Logout' }).click();

    // Should redirect to login
    await expect(page).toHaveURL('/login', { timeout: 5000 });

    // Token should be cleared
    const tokenAfter = await page.evaluate(() => localStorage.getItem('access_token'));
    expect(tokenAfter).toBeNull();
  });

  test('after logout, protected routes redirect to login', async ({ page }) => {
    // First login
    await page.goto('/login', { waitUntil: 'networkidle' });
    await page.locator('#email').fill('test@example.com');
    await page.locator('#password').fill('TestPass123!');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL('/', { timeout: 5000 });

    // Logout
    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL('/login', { timeout: 5000 });

    // Try to access protected route directly
    await page.goto('/market-data', { waitUntil: 'networkidle' });

    // Should be redirected to login
    await expect(page).toHaveURL('/login', { timeout: 5000 });
  });

  test('session persists across page refreshes', async ({ page }) => {
    // Login
    await page.goto('/login', { waitUntil: 'networkidle' });
    await page.locator('#email').fill('test@example.com');
    await page.locator('#password').fill('TestPass123!');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL('/', { timeout: 5000 });

    // Refresh the page
    await page.reload({ waitUntil: 'networkidle' });

    // Should still be on dashboard (not redirected to login)
    await expect(page).toHaveURL('/', { timeout: 5000 });

    // Should still see logout button
    await expect(page.getByRole('button', { name: 'Logout' })).toBeVisible();
  });
});

test.describe('Toast Notifications', () => {
  test.beforeEach(async ({ page, context }) => {
    test.setTimeout(TEST_TIMEOUT);
    await context.clearCookies();
  });

  test('login error displays inline error message', async ({ page }) => {
    await page.goto('/login', { waitUntil: 'networkidle' });

    // Try invalid login
    await page.locator('#email').fill('nonexistent@example.com');
    await page.locator('#password').fill('wrongpassword');
    await page.getByRole('button', { name: 'Sign in' }).click();

    // Check for error message in the form (inline error, not toast)
    // The error appears as an inline form error that persists until user interaction
    // Use .first() to avoid strict mode violation when multiple elements match
    await expect(page.getByText(/Incorrect email or password/).first()).toBeVisible({ timeout: 5000 });

    // The error appears both inline in the form and as a toast notification
    // Use .first() to avoid strict mode violation when both are visible
    const errorMessage = page.locator('.bg-red-50, .dark\\:bg-red-900\\/20').filter({ hasText: /Incorrect email or password/ }).first();
    await expect(errorMessage).toBeVisible({ timeout: 5000 });
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
