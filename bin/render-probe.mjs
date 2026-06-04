#!/usr/bin/env node
/**
 * render-probe.mjs — F51 headless render probe + F219 idle-rAF canary
 *
 * Navigates Chart / Live Trading / Discovery via clicking tab buttons,
 * captures screenshots, asserts DOM anchors, checks for console errors,
 * and runs an idle rAF canary + canvas MutationObserver on the Chart view.
 *
 * Tool choice: playwright with channel:'chrome' — uses the locally installed
 * Google Chrome binary, zero extra browser download.
 *
 * Prerequisites: backend on :8000 and frontend on :5173 (or --url <base>)
 * must already be running. This script does NOT start servers.
 *
 * Usage:
 *   node bin/render-probe.mjs [--url http://localhost:4173]
 *
 * Exit codes:
 *   0  all assertions passed
 *   1  one or more assertions failed
 *   2  environment unreachable (servers not running)
 */

import { mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

// playwright is a devDependency of frontend/ — resolve it from there, since
// bin/ has no node_modules of its own (plain `import 'playwright'` fails).
const requireFromFrontend = createRequire(
  new URL('../frontend/package.json', import.meta.url)
);
const { chromium } = requireFromFrontend('playwright');

// ── Config ────────────────────────────────────────────────────────────────────

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..');
const SCREENSHOT_DIR = path.join(REPO_ROOT, '.run', 'render-probe');

const DEFAULT_URL = 'http://localhost:5173';
const BACKEND_URL = 'http://localhost:8000';

// Parse --url flag
const urlFlagIdx = process.argv.indexOf('--url');
const BASE_URL = urlFlagIdx !== -1 ? process.argv[urlFlagIdx + 1] : DEFAULT_URL;

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Probe result accumulator. Each probe call pushes { view, check, pass, detail }.
 */
const results = [];

function record(view, check, pass, detail = '') {
  results.push({ view, check, pass, detail });
}

function printTable() {
  const width = { view: 12, check: 36, status: 6 };
  const header = `${'View'.padEnd(width.view)}  ${'Check'.padEnd(width.check)}  Status`;
  const sep = '-'.repeat(header.length);
  console.log('\n' + sep);
  console.log(header);
  console.log(sep);
  for (const r of results) {
    const status = r.pass ? 'PASS' : 'FAIL';
    const detail = r.detail ? `  (${r.detail})` : '';
    console.log(`${r.view.padEnd(width.view)}  ${r.check.padEnd(width.check)}  ${status}${detail}`);
  }
  console.log(sep);
  const failed = results.filter(r => !r.pass).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed.\n`);
}

/** Check server reachability before launching browser. */
async function checkReachable(url) {
  try {
    const resp = await fetch(url, { signal: AbortSignal.timeout(5000) });
    return resp.ok || resp.status < 500;
  } catch {
    return false;
  }
}

// ── F219: rAF canary ──────────────────────────────────────────────────────────

/**
 * Count APP-initiated requestAnimationFrame() calls over durationMs with NO
 * user input. A healthy idle page schedules 0 frames (nothing to repaint).
 * The F218 perpetual-repaint bug scheduled ~60/s.
 *
 * Implementation: rafHookInitScript (registered via addInitScript BEFORE any
 * app code runs, so module-load-time captures of window.requestAnimationFrame
 * get the wrapper) wraps rAF and counts calls only while __rafSampling is on.
 * NOTE: a naive self-scheduling tick loop (rAF(tick) → count++ → rAF(tick))
 * measures the display refresh rate (~60/s on ANY healthy page), not app
 * activity — that was the original, broken implementation of this canary.
 * Threshold: < 5 over 500ms.
 */
function rafHookInitScript() {
  window.__rafCount = 0;
  window.__rafSampling = false;
  const orig = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (cb) => {
    if (window.__rafSampling) window.__rafCount++;
    return orig(cb);
  };
}

async function measureRAFCalls(page, durationMs = 500) {
  return page.evaluate(async (ms) => {
    return new Promise((resolve) => {
      window.__rafCount = 0;
      window.__rafSampling = true;
      setTimeout(() => {
        window.__rafSampling = false;
        resolve(window.__rafCount);
      }, ms);
    });
  }, durationMs);
}

// ── F219: canvas MutationObserver ─────────────────────────────────────────────

/**
 * Count attribute mutations on the first canvas over durationMs.
 * lightweight-charts should not thrash canvas attributes at idle.
 * Threshold: 0 mutations.
 */
async function measureCanvasMutations(page, durationMs = 1000) {
  return page.evaluate(async (ms) => {
    const canvas = document.querySelector('canvas');
    if (!canvas) return { found: false, count: 0 };

    return new Promise((resolve) => {
      let mutationCount = 0;
      const observer = new MutationObserver(() => mutationCount++);
      observer.observe(canvas, { attributes: true, subtree: false });
      setTimeout(() => {
        observer.disconnect();
        resolve({ found: true, count: mutationCount });
      }, ms);
    });
  }, durationMs);
}

// ── Per-view probes ───────────────────────────────────────────────────────────

async function probeChart(page, errors) {
  const view = 'Chart';
  console.log(`  Probing ${view} view…`);

  // Wait for canvas (lightweight-charts renders asynchronously)
  try {
    await page.waitForSelector('canvas', { timeout: 8000 });
    record(view, 'canvas present', true);
  } catch {
    record(view, 'canvas present', false, 'waitForSelector timed out');
  }

  // "Disable Chart" or "Enable Chart" button
  const toggleBtn = page.getByRole('button', { name: /^(Disable|Enable) Chart$/ });
  const toggleVisible = await toggleBtn.first().isVisible().catch(() => false);
  record(view, '"Disable/Enable Chart" button', toggleVisible);

  // Ticker input with default value AAPL
  const tickerInput = page.locator('input[value="AAPL"], input[placeholder*="AAPL"], input[placeholder*="ticker"], input[placeholder*="Ticker"]').first();
  const tickerVisible = await tickerInput.isVisible().catch(() => false);
  record(view, 'ticker input visible', tickerVisible);

  // Console errors accumulated so far
  const errCount = errors.length;
  record(view, 'no console errors', errCount === 0,
    errCount > 0 ? errors.slice(0, 3).join(' | ') : '');

  // Screenshot
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'chart.png'), fullPage: false });
  console.log(`    screenshot → .run/render-probe/chart.png`);

  // F219 — rAF canary (settle 1s first, then measure 500ms idle)
  console.log(`    rAF canary: settling 1s…`);
  await page.waitForTimeout(1000);
  const rafCalls = await measureRAFCalls(page, 500);
  const rafOk = rafCalls < 5;
  record(view, `idle rAF < 5 in 500ms (F219)`, rafOk, `got ${rafCalls}`);
  console.log(`    rAF calls: ${rafCalls} → ${rafOk ? 'ok' : 'FAIL'}`);

  // F219 — canvas mutation observer (1s)
  console.log(`    canvas MutationObserver: 1s…`);
  const mut = await measureCanvasMutations(page, 1000);
  if (!mut.found) {
    record(view, 'canvas mutations = 0 (F219)', false, 'canvas not found for MutationObserver');
  } else {
    record(view, 'canvas mutations = 0 (F219)', mut.count === 0, `got ${mut.count}`);
    console.log(`    canvas mutations: ${mut.count} → ${mut.count === 0 ? 'ok' : 'FAIL'}`);
  }
}

async function probeTrading(page, errsBefore) {
  const view = 'Trading';
  console.log(`  Probing ${view} view…`);

  // Click the "Live Trading" tab button
  const tabBtn = page.getByRole('button', { name: /Live Trading/ });
  await tabBtn.click();
  await page.waitForTimeout(1500); // allow React Query to settle

  // Screenshot
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'trading.png'), fullPage: false });
  console.log(`    screenshot → .run/render-probe/trading.png`);

  // Check for at least one VISIBLE trading-related element. `.first()` alone
  // can land on a hidden DOM match (display:none keeps inactive tabs mounted),
  // so scope the locator to visible matches before counting.
  const tradingAnchor = page
    .locator('text=/Add Bot|New Bot|broker|Alpaca|Paper Trading|Bots/i')
    .locator('visible=true');
  const anchorVisible = (await tradingAnchor.count().catch(() => 0)) > 0;
  record(view, 'trading content visible', anchorVisible);

  // Errors introduced specifically during this view
  const newErrors = page.context()._errors ?? [];  // fallback; real tracking is via closure
  void newErrors; // we track globally below
  record(view, 'no new console errors', true); // will be overridden below if errors exist
  // (actual error check done at end with snapshot of error count)
}

async function probeDiscovery(page) {
  const view = 'Discovery';
  console.log(`  Probing ${view} view…`);

  const tabBtn = page.getByRole('button', { name: /Discovery/ });
  await tabBtn.click();
  await page.waitForTimeout(1500);

  await page.screenshot({ path: path.join(SCREENSHOT_DIR, 'discovery.png'), fullPage: false });
  console.log(`    screenshot → .run/render-probe/discovery.png`);

  // Any content that signals Discovery rendered
  const discoveryAnchor = page.locator('text=/Discover|Screener|Scan|Scanner|Strategy|Watchlist/i').first();
  const anchorVisible = await discoveryAnchor.isVisible().catch(() => false);
  record(view, 'discovery content visible', anchorVisible);
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  // Ensure screenshot output directory exists
  await mkdir(SCREENSHOT_DIR, { recursive: true });

  // ── 1. Reachability check ─────────────────────────────────────────────────
  console.log(`Checking frontend: ${BASE_URL}`);
  const feOk = await checkReachable(BASE_URL);
  if (!feOk) {
    console.error(`ERROR: Frontend unreachable at ${BASE_URL}`);
    console.error('Start the frontend first: npm run dev  OR  npm run preview -- --port 4173');
    process.exit(2);
  }

  console.log(`Checking backend: ${BACKEND_URL}`);
  const beOk = await checkReachable(`${BACKEND_URL}/api/providers`);
  if (!beOk) {
    console.error(`ERROR: Backend unreachable at ${BACKEND_URL}`);
    console.error('Start the backend first: cd backend && venv/bin/uvicorn main:app --port 8000');
    process.exit(2);
  }

  console.log('Both servers reachable. Launching browser…\n');

  // ── 2. Launch with system Chrome (zero download) ──────────────────────────
  // R-08: wrap launch() so a missing Chrome binary gives an actionable message
  // instead of a raw playwright stack trace, and exits with code 2 (environment).
  let browser;
  try {
    browser = await chromium.launch({
      channel: 'chrome',   // uses /Applications/Google Chrome.app on macOS
      headless: true,
      args: ['--disable-gpu'],
    });
  } catch (err) {
    const msg = err?.message ?? String(err);
    if (/Executable doesn't exist|No usable sandbox|cannot find Chrome|failed to launch/i.test(msg)) {
      console.error('ERROR: Chrome executable not found or failed to launch.');
      console.error('  Option A: Install Google Chrome from https://www.google.com/chrome/');
      console.error('  Option B: Use a Playwright-managed browser instead:');
      console.error('            npx playwright install chromium');
      console.error('            Then remove the `channel: "chrome"` line from render-probe.mjs');
      console.error(`\nOriginal error: ${msg}`);
    } else {
      console.error(`ERROR: Browser launch failed — ${msg}`);
    }
    process.exit(2);
  }

  // R-09: hard global deadline — a hung page.goto() must not leave a zombie Chrome.
  // 120 s covers the ~10 s goto timeout + all view probes + settle waits with headroom.
  const GLOBAL_TIMEOUT_MS = 120_000;
  const timeoutPromise = new Promise((_, reject) =>
    setTimeout(() => reject(new Error(`Global probe deadline exceeded (${GLOBAL_TIMEOUT_MS / 1000}s)`)), GLOBAL_TIMEOUT_MS)
  );

  // R-09: browser.close() is guaranteed via finally on every path after launch succeeds.
  try {
    await Promise.race([timeoutPromise, runProbe(browser)]);
  } finally {
    await browser.close();
  }
}

/** Core probe sequence, separated so the outer finally always closes the browser. */
async function runProbe(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  // F219: install the rAF counting hook before any app code loads
  await page.addInitScript(rafHookInitScript);

  // ── 3. Collect console errors globally ───────────────────────────────────
  const consoleErrors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text());
    }
  });
  page.on('pageerror', (err) => {
    consoleErrors.push(`[uncaught] ${err.message}`);
  });

  // ── 4. Navigate to app ────────────────────────────────────────────────
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
  // Wait for the React tree to hydrate and initial API calls to settle
  await page.waitForTimeout(2000);

  // ── 5. Chart view (default on load) ──────────────────────────────────
  const errsBefore = consoleErrors.length;
  await probeChart(page, consoleErrors);

  // ── 6. Live Trading view ──────────────────────────────────────────────
  const tradingErrsBefore = consoleErrors.length;
  await probeTrading(page, tradingErrsBefore);
  // Patch the placeholder Trading error-check result with real data
  const tradingIdx = results.findLastIndex(r => r.view === 'Trading' && r.check === 'no new console errors');
  const tradingErrs = consoleErrors.slice(tradingErrsBefore);
  if (tradingIdx !== -1) {
    results[tradingIdx].pass = tradingErrs.length === 0;
    results[tradingIdx].detail = tradingErrs.length > 0
      ? tradingErrs.slice(0, 2).join(' | ')
      : '';
  }

  // ── 7. Discovery view ─────────────────────────────────────────────────
  const discoveryErrsBefore = consoleErrors.length;
  await probeDiscovery(page);
  const discoveryErrs = consoleErrors.slice(discoveryErrsBefore);
  record('Discovery', 'no new console errors', discoveryErrs.length === 0,
    discoveryErrs.length > 0 ? discoveryErrs.slice(0, 2).join(' | ') : '');

  // ── 8. Print summary table ────────────────────────────────────────────────
  printTable();

  const anyFailed = results.some(r => !r.pass);
  if (anyFailed) {
    console.error('One or more checks failed.');
    process.exit(1);
  }

  console.log('All checks passed.');
  process.exit(0);
}

main().catch((err) => {
  console.error('Unexpected error:', err);
  process.exit(1);
});
