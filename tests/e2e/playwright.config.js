import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.js",
  timeout: 120000,
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  outputDir: "../../.local/playwright-results",
  use: {
    baseURL: process.env.APP_PUBLIC_URL || "http://localhost:8000",
    headless: true,
    viewport: { width: 1440, height: 1000 },
  },
});
