// Renders docs/architecture.html to docs/architecture.png (2x, full page) with Playwright's Chromium.
//   npm run docs:architecture
import { chromium } from "@playwright/test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const docs = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "docs");
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 2 });
await page.goto(pathToFileURL(path.join(docs, "architecture.html")).href);
await page.locator(".page").screenshot({ path: path.join(docs, "architecture.png") });
await browser.close();
console.log("wrote", path.join(docs, "architecture.png"));
