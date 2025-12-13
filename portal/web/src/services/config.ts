/**
 * Application configuration loaded from environment variables.
 *
 * Vite loads .env.{mode} files based on the --mode flag:
 * - npm run dev             -> .env.development (default)
 * - npm run dev --mode test -> .env.test
 * - npm run build           -> .env.production (default)
 * - npm run build --mode staging -> .env.staging
 */

// API Base URL - configured per environment
// Default uses relative URL so requests go through Vite's dev server proxy (avoids CORS)
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

// Current environment
export const ENVIRONMENT = import.meta.env.VITE_ENVIRONMENT || 'development';

// Helper to check environments
export const isDevelopment = ENVIRONMENT === 'development';
export const isTest = ENVIRONMENT === 'test';
export const isStaging = ENVIRONMENT === 'staging';
export const isProduction = ENVIRONMENT === 'production';

// Config object for convenience
export const config = {
  apiBaseUrl: API_BASE_URL,
  environment: ENVIRONMENT,
  isDevelopment,
  isTest,
  isStaging,
  isProduction,
} as const;
