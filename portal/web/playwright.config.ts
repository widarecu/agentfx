/// <reference types="node" />
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: '../../tests/e2e',
  timeout: 60_000,
  expect: { timeout: 5_000 },
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5174',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  outputDir: '../../test-results/e2e-artifacts',
  reporter: [
    ['html', { outputFolder: '../../test-results/e2e-report' }],
    ['json', { outputFile: '../../test-results/e2e-results.json' }],
  ],
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Configure test runner to resolve modules from portal/web node_modules
  testIgnore: '**/node_modules/**',
})
