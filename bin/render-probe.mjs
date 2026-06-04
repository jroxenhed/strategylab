#!/usr/bin/env node
/**
 * render-probe.mjs — F51 headless render probe + F219 idle-rAF canary + F298 manifest/seed
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
 *   node bin/render-probe.mjs --url http://localhost:4173 --seed seed.json
 *   node bin/render-probe.mjs --url http://localhost:4173 --manifest manifest.json
 *   node bin/render-probe.mjs --url http://localhost:4173 --seed seed.json --manifest manifest.json
 *
 * Exit codes:
 *   0  all assertions passed
 *   1  one or more assertions failed
 *   2  environment unreachable (servers not running)
 *
 * Manifest trigger types (F298/F301):
 *   click      — { type: "click", selector: "<css>" }
 *   input      — { type: "input", selector: "<css>", value: "<text>" }
 *   navigate   — { type: "navigate", value: "<button name regex>" }
 *   drag       — { type: "drag",
 *                  from: { selector: "<css>", offset?: { x, y } },
 *                  to:   { selector: "<css>", offset?: { x, y } } }
 *                Mouse-based multi-step drag: mousedown → intermediate moves → mouseup.
 *                offset values are added to the element's top-left corner.
 *
 * DEADLINE COUPLING (F300):
 *   render-probe.mjs GLOBAL_TIMEOUT_MS must be ≤ verify-batch.sh watchdog − 15s margin.
 *   Node: 120s | Bash: 135s | Margin: 15s
 *   PER_VIEW_TIMEOUT_MS limits how long any single manifest view can take;
 *   a timed-out view records a failure and the run continues with the next view.
 *   If increasing PER_VIEW_TIMEOUT_MS, recalculate:
 *     new_global = per_view * max_views + overhead (≤ bash_watchdog − 15s)
 */

import { mkdir, readFile } from 'node:fs/promises';
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

// Parse --seed flag (F298)
const seedFlagIdx = process.argv.indexOf('--seed');
const SEED_FILE = seedFlagIdx !== -1 ? process.argv[seedFlagIdx + 1] : null;

// Parse --manifest flag (F298)
const manifestFlagIdx = process.argv.indexOf('--manifest');
const MANIFEST_FILE = manifestFlagIdx !== -1 ? process.argv[manifestFlagIdx + 1] : null;

// Parse --per-view-timeout flag (F300): per-view deadline in ms (default 20000)
const perViewTimeoutFlagIdx = process.argv.indexOf('--per-view-timeout');
const PER_VIEW_TIMEOUT_MS = perViewTimeoutFlagIdx !== -1
  ? parseInt(process.argv[perViewTimeoutFlagIdx + 1], 10)
  : 20_000;

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

// ── F298: Seed loading and application ────────────────────────────────────────

/**
 * Load and parse the seed JSON file.
 * Returns null if no seed file was specified.
 * REL-03: hard-exits (code 2) if --seed was given but the file cannot be read or parsed,
 * preventing a silent fallback that would run the probe against un-seeded state.
 */
async function loadSeed(seedFile) {
  if (!seedFile) return null;
  const absPath = path.resolve(process.cwd(), seedFile);
  try {
    const raw = await readFile(absPath, 'utf8');
    const seed = JSON.parse(raw);
    console.log(`[seed] Loaded: ${absPath}`);
    if (seed.description) console.log(`[seed] Description: ${seed.description}`);
    return seed;
  } catch (err) {
    console.error(`[seed] ERROR: Cannot load seed file '${seedFile}': ${err.message}`);
    console.error('[seed] Hard-failing — probe would run against un-seeded state (use --seed only with a valid file).');
    process.exit(2);
  }
}

/**
 * Apply seed: make backend API calls and inject localStorage.
 * - Backend calls are made before the browser navigates (no page context needed).
 * - localStorage injection is done via page.addInitScript so it runs before
 *   any app code on first page load.
 *
 * @param {object|null} seed  Parsed seed JSON (may be null — no-op).
 * @param {object} page       Playwright Page (used for addInitScript).
 */
async function applySeed(seed, page) {
  if (!seed) return;

  // ── Backend API calls ────────────────────────────────────────────────────────
  if (Array.isArray(seed.backend) && seed.backend.length > 0) {
    console.log(`[seed] Running ${seed.backend.length} backend API call(s)…`);
    let prevResponse = null;  // for {{ prev.id }} substitution

    for (const call of seed.backend) {
      // Simple {{ prev.id }} / {{ prev.data.id }} template substitution in body
      let body = call.body ? JSON.parse(
        JSON.stringify(call.body).replace(
          /\{\{\s*prev\.data\.id\s*\}\}/g,
          () => prevResponse?.data?.id ?? prevResponse?.id ?? ''
        ).replace(
          /\{\{\s*prev\.id\s*\}\}/g,
          () => prevResponse?.id ?? ''
        )
      ) : undefined;

      const url = `${BACKEND_URL}${call.path}`;
      const method = (call.method || 'GET').toUpperCase();
      // REL-04: bail_on_error (default false for backward compat) — if true, a failed
      // call hard-fails the probe rather than continuing with empty prevResponse.
      const bailOnError = call.bail_on_error === true;
      try {
        const resp = await fetch(url, {
          method,
          headers: body ? { 'Content-Type': 'application/json' } : undefined,
          body: body ? JSON.stringify(body) : undefined,
          signal: AbortSignal.timeout(10000),
        });
        const status = resp.status;
        const ok = call.expect ? status === call.expect : resp.ok;
        console.log(`[seed]   ${method} ${call.path} → ${status} ${ok ? 'OK' : 'UNEXPECTED'}`);
        if (!ok && bailOnError) {
          console.error(`[seed] FAIL: ${method} ${call.path} returned ${status} and bail_on_error is true.`);
          console.error('[seed] Aborting seed — subsequent calls would use empty prevResponse, producing inconsistent state.');
          process.exit(1);
        }
        if (resp.headers.get('content-type')?.includes('application/json')) {
          try { prevResponse = await resp.json(); } catch { prevResponse = null; }
        } else {
          prevResponse = null;
        }
      } catch (err) {
        console.log(`[seed]   ${method} ${call.path} → ERROR: ${err.message}${bailOnError ? ' (bail_on_error=true, aborting)' : ' (continuing)'}`);
        if (bailOnError) {
          console.error('[seed] Aborting seed — network/timeout failure with bail_on_error=true.');
          process.exit(1);
        }
        prevResponse = null;
      }
    }
  }

  // ── localStorage injection via addInitScript ─────────────────────────────────
  if (Array.isArray(seed.localStorage) && seed.localStorage.length > 0) {
    console.log(`[seed] Injecting ${seed.localStorage.length} localStorage key(s)…`);
    const items = seed.localStorage;
    await page.addInitScript((lsItems) => {
      lsItems.forEach(({ key, value }) => {
        localStorage.setItem(key, value);
      });
    }, items);
    for (const { key } of items) {
      console.log(`[seed]   localStorage.${key} set`);
    }
  }
}

// ── F298: Manifest loading and assertion execution ────────────────────────────

/**
 * Load and parse the manifest JSON file.
 * Returns null if no manifest file was specified.
 * REL-03: hard-exits (code 2) if --manifest was given but the file cannot be read or parsed,
 * preventing a misleading PASS from the legacy hardcoded probe path running instead.
 */
async function loadManifest(manifestFile) {
  if (!manifestFile) return null;
  const absPath = path.resolve(process.cwd(), manifestFile);
  try {
    const raw = await readFile(absPath, 'utf8');
    const manifest = JSON.parse(raw);
    console.log(`[manifest] Loaded: ${absPath}`);
    if (manifest.description) console.log(`[manifest] Description: ${manifest.description}`);
    return manifest;
  } catch (err) {
    console.error(`[manifest] ERROR: Cannot load manifest file '${manifestFile}': ${err.message}`);
    console.error('[manifest] Hard-failing — would fall through to legacy probes, producing a misleading PASS.');
    process.exit(2);
  }
}

/**
 * Execute a trigger action on the page.
 *
 * Trigger types:
 *   click      — { type: "click", selector: "<css>" }
 *   input      — { type: "input", selector: "<css>", value: "<text>" }
 *   navigate   — { type: "navigate", value: "<button name regex>" }
 *   drag (F301)— { type: "drag",
 *                  from: { selector: "<css>", offset?: { x, y } },
 *                  to:   { selector: "<css>", offset?: { x, y } } }
 *                Performs mousedown → intermediate moves → mouseup via the page
 *                mouse API so apps tracking mousemove events see a real drag.
 *                offset is added to each element's bounding-box top-left corner
 *                (default: centre of the element, i.e. offset not required).
 */
async function executeTrigger(trigger, page) {
  if (!trigger) return;
  const { type, selector, value } = trigger;
  try {
    if (type === 'click') {
      await page.locator(selector).first().click({ timeout: 5000 });
      console.log(`  [trigger] click: ${selector}`);
    } else if (type === 'input') {
      await page.locator(selector).first().fill(value ?? '', { timeout: 5000 });
      console.log(`  [trigger] input: ${selector} = ${value}`);
    } else if (type === 'navigate') {
      // Navigate to a tab by clicking a button matching the value
      const tabBtn = page.getByRole('button', { name: new RegExp(value, 'i') });
      await tabBtn.first().click({ timeout: 5000 });
      console.log(`  [trigger] navigate: ${value}`);
    } else if (type === 'drag') {
      // F301: mouse-based drag — from element (+ optional offset) to target element
      // (+ optional offset) with intermediate move steps so mousemove listeners fire.
      const { from, to } = trigger;
      // Resolve element positions
      const fromEl = page.locator(from.selector).first();
      const toEl   = page.locator(to.selector).first();
      const fromBox = await fromEl.boundingBox({ timeout: 5000 });
      const toBox   = await toEl.boundingBox({ timeout: 5000 });
      if (!fromBox || !toBox) {
        throw new Error(`drag: bounding box not found for "${!fromBox ? from.selector : to.selector}"`);
      }
      // Default drag origin: element centre; apply optional offset from top-left.
      // Guard each coordinate independently so a partial offset object {x} doesn't
      // silently produce NaN for the missing axis (fromBox.y + undefined → NaN).
      const fx = fromBox.x + (from.offset?.x != null ? from.offset.x : fromBox.width  / 2);
      const fy = fromBox.y + (from.offset?.y != null ? from.offset.y : fromBox.height / 2);
      const tx = toBox.x   + (to.offset?.x   != null ? to.offset.x   : toBox.width    / 2);
      const ty = toBox.y   + (to.offset?.y   != null ? to.offset.y   : toBox.height   / 2);
      // Perform drag: move to start → mousedown → intermediate moves → mouseup
      await page.mouse.move(fx, fy);
      await page.mouse.down();
      // Intermediate steps so applications tracking mousemove see a smooth drag
      const STEPS = 5;
      for (let i = 1; i <= STEPS; i++) {
        const progress = i / STEPS;
        await page.mouse.move(fx + (tx - fx) * progress, fy + (ty - fy) * progress);
      }
      await page.mouse.up();
      console.log(`  [trigger] drag: ${from.selector} → ${to.selector}`);
    } else {
      console.log(`  [trigger] unknown type: ${type} (skipped)`);
    }
  } catch (err) {
    console.log(`  [trigger] ERROR: ${err.message} (continuing)`);
  }
}

/**
 * Evaluate a single assertion against the page and record result.
 *
 * Assertion types:
 *   selector  — page.locator + operator (visible|hidden|count)
 *   console   — check accumulated console messages
 *   eval      — page.evaluate(expression) == expected
 *   canvas-mutation — measureCanvasMutations()
 *   raf-idle  — measureRAFCalls()
 */
async function evaluateAssertion(assertion, page, consoleErrors, viewName) {
  const desc = assertion.description || assertion.type;
  try {
    switch (assertion.type) {
      case 'selector': {
        const loc = page.locator(assertion.selector);
        const op = assertion.operator || 'visible';
        const timeout = assertion.timeout || 5000;
        if (op === 'visible') {
          try {
            await loc.first().waitFor({ state: 'visible', timeout });
            record(viewName, desc, true);
          } catch {
            record(viewName, desc, false, 'element not visible within timeout');
          }
        } else if (op === 'hidden') {
          try {
            await loc.first().waitFor({ state: 'hidden', timeout });
            record(viewName, desc, true);
          } catch {
            record(viewName, desc, false, 'element still visible');
          }
        } else if (op === 'count') {
          const count = await loc.count();
          const pass = count === assertion.expected;
          record(viewName, desc, pass, `got ${count}, expected ${assertion.expected}`);
        } else {
          record(viewName, desc, false, `unknown operator: ${op}`);
        }
        break;
      }

      case 'console': {
        // REL-05: only 'error' level is captured. 'warn' is not yet supported —
        // reject it with a clear FAIL rather than silently checking the error array.
        const level = assertion.level || 'error';
        if (level !== 'error') {
          record(viewName, desc, false,
            `console level '${level}' not yet supported (only 'error' is tracked); assertion is a no-op`);
          break;
        }
        const op = assertion.operator || 'count';
        if (op === 'count') {
          const count = consoleErrors.length;
          const pass = count === assertion.expected;
          record(viewName, desc, pass,
            pass ? '' : `got ${count} ${level}(s): ${consoleErrors.slice(0, 2).join(' | ')}`);
        } else if (op === 'includes') {
          const found = consoleErrors.some(m => m.includes(assertion.expected));
          record(viewName, desc, found, found ? '' : `"${assertion.expected}" not found in console`);
        } else {
          record(viewName, desc, false, `unknown console operator: ${op}`);
        }
        break;
      }

      case 'eval': {
        let result;
        try {
          result = await page.evaluate(assertion.expression);
        } catch (err) {
          record(viewName, desc, false, `eval error: ${err.message}`);
          break;
        }
        const pass = result === assertion.expected;
        record(viewName, desc, pass, pass ? '' : `got ${JSON.stringify(result)}, expected ${JSON.stringify(assertion.expected)}`);
        break;
      }

      case 'canvas-mutation': {
        const duration = assertion.durationMs || 1000;
        const mut = await measureCanvasMutations(page, duration);
        if (!mut.found) {
          record(viewName, desc, false, 'canvas not found for MutationObserver');
          break;
        }
        const op = assertion.operator || 'count';
        if (op === 'count') {
          const pass = mut.count === assertion.expected;
          record(viewName, desc, pass, `got ${mut.count}, expected ${assertion.expected}`);
        } else if (op === 'max') {
          const pass = mut.count <= assertion.expected;
          record(viewName, desc, pass, `got ${mut.count}, max ${assertion.expected}`);
        } else {
          record(viewName, desc, false, `unknown canvas-mutation operator: ${op}`);
        }
        break;
      }

      case 'raf-idle': {
        const duration = assertion.durationMs || 500;
        const threshold = assertion.threshold ?? 5;
        const rafCalls = await measureRAFCalls(page, duration);
        const pass = rafCalls < threshold;
        record(viewName, desc, pass, `got ${rafCalls} rAF calls in ${duration}ms, threshold <${threshold}`);
        break;
      }

      default:
        record(viewName, desc, false, `unknown assertion type: ${assertion.type}`);
    }
  } catch (err) {
    record(viewName, desc, false, `assertion error: ${err.message}`);
  }
}

/**
 * Run all views in the manifest against the live page.
 * Replaces the hardcoded probeChart/probeTrading/probeDiscovery flow.
 */
async function runManifestProbe(manifest, initialPage, context, consoleErrors) {
  if (!manifest || !Array.isArray(manifest.views)) {
    console.error('[manifest] No views defined; skipping manifest probe.');
    return;
  }

  // Use a local `let` so a post-timeout page reset can be reflected within the loop.
  let page = initialPage;

  for (const view of manifest.views) {
    const viewName = view.name || 'Unknown';
    console.log(`\n  [manifest] View: ${viewName}`);

    // F300: per-view timeout envelope — one stuck view must not eat the global budget.
    // Wrap the entire view (trigger + settle + assertions + screenshot) in a race.
    // On timeout: record a failure for this view, close+recreate the page so the
    // stale runView() can no longer interleave with subsequent views' interactions,
    // then continue to the next view.
    let viewTimedOut = false;
    let timeoutId;
    const viewTimeoutPromise = new Promise((resolve) => {
      timeoutId = setTimeout(() => {
        viewTimedOut = true;
        resolve(); // resolve (not reject) so the race winner is our sentinel
      }, PER_VIEW_TIMEOUT_MS);
    });

    const runView = async () => {
      // COR-01: snapshot the console-error count at view start so console
      // assertions see only THIS view's errors (same errsBefore pattern as
      // the legacy probes) — not errors accumulated by earlier views.
      const viewErrsBefore = consoleErrors.length;

      // Execute trigger (navigate/click to reach this view)
      if (view.trigger) {
        await executeTrigger(view.trigger, page);
      }

      // Settle wait
      const settleMs = view.before?.settleMs ?? 500;
      if (settleMs > 0) {
        console.log(`  [manifest] Settling ${settleMs}ms…`);
        await page.waitForTimeout(settleMs);
      }

      // Run assertions
      if (Array.isArray(view.assertions)) {
        const viewErrors = consoleErrors.slice(viewErrsBefore);
        for (const assertion of view.assertions) {
          await evaluateAssertion(assertion, page, viewErrors, viewName);
        }
      }

      // Screenshot
      const screenshotName = view.screenshot || `${viewName.toLowerCase().replace(/\s+/g, '-')}.png`;
      const screenshotPath = path.join(SCREENSHOT_DIR, screenshotName);
      try {
        await page.screenshot({ path: screenshotPath, fullPage: false });
        console.log(`  [manifest] screenshot → .run/render-probe/${screenshotName}`);
      } catch (err) {
        console.log(`  [manifest] screenshot failed: ${err.message}`);
      }
    };

    await Promise.race([viewTimeoutPromise, runView().finally(() => clearTimeout(timeoutId))]);

    if (viewTimedOut) {
      console.log(`  [manifest] TIMEOUT: view "${viewName}" exceeded ${PER_VIEW_TIMEOUT_MS / 1000}s budget — skipping remaining assertions.`);
      record(viewName, 'per-view timeout', false,
        `view did not complete within ${PER_VIEW_TIMEOUT_MS / 1000}s`);
      // Neutralize the stale runView(): close the timed-out page and open a fresh
      // one so any in-flight CDP operations cannot interleave with the next view.
      try {
        await page.close();
        const newPage = await context.newPage();
        await newPage.addInitScript(rafHookInitScript);
        newPage.on('console', (msg) => {
          if (msg.type() === 'error') consoleErrors.push(msg.text());
        });
        newPage.on('pageerror', (err) => {
          consoleErrors.push(`[uncaught] ${err.message}`);
        });
        await newPage.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
        // Reassign local `page` so subsequent views use the fresh page.
        page = newPage;
      } catch (resetErr) {
        console.log(`  [manifest] WARNING: page reset after timeout failed: ${resetErr.message}`);
      }
    }
  }
}

// ── Per-view probes (legacy / hardcoded mode) ─────────────────────────────────

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

  // ── F298: Load seed + manifest early (file errors surfaced before browser launch) ──
  const seed = await loadSeed(SEED_FILE);
  const manifest = await loadManifest(MANIFEST_FILE);

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
  // DEADLINE COUPLING (F300): GLOBAL_TIMEOUT_MS (120s) must be ≤ verify-batch.sh watchdog (135s) − 15s margin.
  // See matching comment in bin/verify-batch.sh near the 135-iteration watchdog loop.
  // If you change GLOBAL_TIMEOUT_MS, update the bash watchdog to GLOBAL_TIMEOUT_MS/1000 + 15.
  const GLOBAL_TIMEOUT_MS = 120_000;
  const timeoutPromise = new Promise((_, reject) =>
    setTimeout(() => reject(new Error(`Global probe deadline exceeded (${GLOBAL_TIMEOUT_MS / 1000}s)`)), GLOBAL_TIMEOUT_MS)
  );

  // F300: Per-view timeout envelope. Each manifest view must complete within this
  // budget. A stuck view records a timeout failure and the run moves on to the next
  // view instead of consuming the entire global deadline.
  // Tune PER_VIEW_TIMEOUT_MS via --per-view-timeout <ms>. Default: 20s.
  // Invariant: PER_VIEW_TIMEOUT_MS * max_views + setup_overhead ≤ GLOBAL_TIMEOUT_MS

  // R-09: browser.close() is guaranteed via finally on every path after launch succeeds.
  try {
    await Promise.race([timeoutPromise, runProbe(browser, seed, manifest)]);
  } finally {
    await browser.close();
  }
}

/** Core probe sequence, separated so the outer finally always closes the browser. */
async function runProbe(browser, seed, manifest) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  // F219: install the rAF counting hook before any app code loads
  await page.addInitScript(rafHookInitScript);

  // ── F298: Apply seed (backend API calls + localStorage injection) ──────────
  // applySeed uses page.addInitScript for localStorage — must be called before goto()
  await applySeed(seed, page);

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

  // ── F298: Manifest mode — run dynamic assertions from manifest ────────────
  if (manifest) {
    console.log('\n[manifest] Running manifest-driven probe…');
    await runManifestProbe(manifest, page, context, consoleErrors);
  } else {
    // ── Legacy hardcoded probe mode ─────────────────────────────────────────

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
  }

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
