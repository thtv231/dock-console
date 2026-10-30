// Captures the 10 screens in Replay mode into docs/screens/ (dev only: `playwright` is a devDependency).
// Start uvicorn (port 8000) and `npx next dev` first, then: node scripts/screenshots.mjs
import { chromium } from "playwright";

const base = process.env.DOCK_WEB_URL ?? "http://localhost:3000";
const api = process.env.DOCK_API_URL ?? "http://127.0.0.1:8000";
const screens = [
  ["overview", "/"], ["inbox", "/inbox"], ["documents", "/documents"], ["handbook", "/handbook"],
  ["agents", "/agents"], ["approvals", "/approvals"], ["guardrails", "/guardrails"],
  ["voice", "/voice"], ["scorecard", "/scorecard"], ["operations", "/operations"],
];

const browser = await chromium.launch();
for (const [name, path] of screens) {
  // A fresh page per screen, with no key: every result comes from replays/.
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });
  // Send /api straight to uvicorn: the Voice desk's aborted audio range requests can jam the dev proxy.
  await page.route("**/api/**", (route) => route.continue({ url: route.request().url().replace(base, api) }));
  await page.goto(base + path, { waitUntil: "networkidle" });
  await page.waitForFunction(() => !/Loading/.test(document.body.innerText), null, { timeout: 60_000 });
  // Fill the screen from replays: press its "... all" button (or start the recorded approvals run) and wait until it finishes.
  const run = page.getByRole("button", { name: /^(Triage|Read|Answer|Route|Screen|Run) all|^Start HF-/ }).first();
  if (await run.count()) {
    await run.click();
    await page.waitForFunction(() => !/running|at a time/.test(document.body.innerText), null, { timeout: 120_000 });
    await page.waitForLoadState("networkidle");
  }
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `docs/screens/${name}.png`, fullPage: true });
  console.log(`docs/screens/${name}.png`);
  await page.close();
}
await browser.close();
