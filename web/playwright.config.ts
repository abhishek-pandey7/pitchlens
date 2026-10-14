// Browser end-to-end tests: boots the engine and a production build of the web app, then drives Chromium.
import { defineConfig, devices } from "@playwright/test";
import os from "node:os";
import path from "node:path";

export const ENGINE_PORT = 8765;
const WEB_PORT = 3100;
const API_BASE = `http://127.0.0.1:${ENGINE_PORT}/api/v1`;
const venvPython = path
  .resolve(__dirname, "../engine/.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python")
  .replaceAll("\\", "/");
const python = `"${process.env.PITCHLENS_PYTHON ?? venvPython}"`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${WEB_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `${python} -m uvicorn pitchlens.api.main:app --port ${ENGINE_PORT} --log-level warning`,
      cwd: path.resolve(__dirname, "../engine"),
      url: `${API_BASE}/health`,
      env: {
        PITCHLENS_DATA_DIRS: path.join(os.tmpdir(), "pitchlens-web-e2e"),
        CORS_ORIGINS: `http://127.0.0.1:${WEB_PORT},http://localhost:${WEB_PORT}`,
      },
      reuseExistingServer: !!process.env.E2E_REUSE_ENGINE,
      timeout: 60_000,
    },
    {
      command: `npx next build && npx next start -p ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}/watch`,
      // NEXT_PUBLIC_STREAM_BASE from the environment runs the suite with streams through the broadcast
      // edge (start it first, with ENGINE_URL=http://127.0.0.1:8765); unset, streams come from the engine.
      env: { NEXT_PUBLIC_API_BASE: API_BASE, NEXT_PUBLIC_STREAM_BASE: process.env.NEXT_PUBLIC_STREAM_BASE ?? "" },
      reuseExistingServer: false,
      timeout: 240_000,
    },
  ],
});
