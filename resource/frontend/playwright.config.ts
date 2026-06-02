import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  retries: 0,
  use: {
    headless: true,
    baseURL: 'http://localhost:4173',
    viewport: { width: 1600, height: 1000 },
    screenshot: 'only-on-failure',
  },
  reporter: [['list']],
  webServer: {
    command: 'npm run preview',
    port: 4173,
    reuseExistingServer: true,
  },
});
