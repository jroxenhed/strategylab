#!/usr/bin/env node
/**
 * probe-graph-split.mjs: F435 W4 (spec S28) browser probe for the graph/chart split.
 *
 * What it does, on a fresh browser profile:
 *   1. Runs the default rule backtest on the Chart view, so graph view has a
 *      strategy to show, then opens graph view, "Edit this graph", and runs
 *      the graph backtest.
 *   2. Opens the chart panel and checks the ONE chart moved into it: every
 *      lightweight-charts canvas on the page sits inside the split's chart
 *      panel, and the chart bar shows the run summary.
 *   3. Toggles graph view / chart view 20 times. After each toggle the number
 *      of lightweight-charts instances on the page must equal the number one
 *      Chart makes (no leaked or doubled chart).
 *   4. Drags the chart handle 10 times and double-clicks it (reset to 35%).
 *   5. Idle canary: with the chart open and nothing happening, the page must
 *      schedule fewer than 10 requestAnimationFrame calls in 2 s (F218/F219).
 *   6. Closes the chart from its bar; no chart canvas may stay in the split.
 *   7. Fails on any console error or uncaught page error.
 *
 * Same style and rAF hook as bin/render-probe.mjs. It does NOT start servers.
 *
 * Usage:
 *   node bin/probe-graph-split.mjs [--url http://localhost:4173]
 *
 * Exit codes: 0 pass, 1 a check failed, 2 servers not reachable.
 */

import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

// playwright is a devDependency of frontend/ (bin/ has no node_modules).
const requireFromFrontend = createRequire(new URL('../frontend/package.json', import.meta.url));
const { chromium } = requireFromFrontend('playwright');

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..');
const OUT_DIR = path.join(REPO_ROOT, '.run', 'render-probe');

const urlFlagIdx = process.argv.indexOf('--url');
const BASE_URL = urlFlagIdx !== -1 ? process.argv[urlFlagIdx + 1] : 'http://localhost:4173';
const BACKEND_URL = 'http://localhost:8000';
const GLOBAL_TIMEOUT_MS = 150_000;

const TOGGLES = 20;
const DRAGS = 10;
const IDLE_MS = 2000;
const IDLE_RAF_MAX = 10;

const results = [];
function record(check, pass, detail = '') {
  results.push({ check, pass, detail });
  console.log(`  ${pass ? 'PASS' : 'FAIL'}  ${check}${detail ? `  (${detail})` : ''}`);
}

async function reachable(url) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(5000) });
    return r.status < 500;
  } catch {
    return false;
  }
}

/** Counts app requestAnimationFrame calls while __rafSampling is on (render-probe F219 hook). */
function rafHookInitScript() {
  window.__rafCount = 0;
  window.__rafSampling = false;
  const orig = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (cb) => {
    if (window.__rafSampling) window.__rafCount++;
    return orig(cb);
  };
}

async function idleRaf(page, ms) {
  return page.evaluate(async (d) => new Promise((resolve) => {
    window.__rafCount = 0;
    window.__rafSampling = true;
    setTimeout(() => { window.__rafSampling = false; resolve(window.__rafCount); }, d);
  }), ms);
}

/** Where the lightweight-charts instances are: one `.tv-lightweight-charts` root per IChartApi. */
async function chartCensus(page) {
  return page.evaluate(() => {
    const roots = [...document.querySelectorAll('.tv-lightweight-charts')];
    const body = document.querySelector('[data-testid="nb-chart-body"]');
    const resultsPanel = document.querySelector('[data-testid="graph-results-panel"]');
    const inSplit = body ? roots.filter(r => body.contains(r)).length : 0;
    // The Results panel's own equity chart is not the price chart.
    const inResults = resultsPanel ? roots.filter(r => resultsPanel.contains(r)).length : 0;
    return { total: roots.length, inSplit, inResults };
  });
}

async function graphViewActive(page) {
  return page.evaluate(() => localStorage.getItem('nodebuilder-graph-view-active') === 'true');
}

async function toggleView(page) {
  await page.getByRole('button', { name: /^(View as Graph|Back to Chart)$/ }).first().click();
}

async function main() {
  if (!(await reachable(BASE_URL)) || !(await reachable(`${BACKEND_URL}/api/cache`))) {
    console.error(`Servers not reachable (frontend ${BASE_URL}, backend ${BACKEND_URL}).`);
    process.exit(2);
  }
  await mkdir(OUT_DIR, { recursive: true });

  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const deadline = new Promise((_, reject) =>
    setTimeout(() => reject(new Error(`deadline ${GLOBAL_TIMEOUT_MS / 1000}s exceeded`)), GLOBAL_TIMEOUT_MS));
  try {
    await Promise.race([deadline, run(browser)]);
  } finally {
    await browser.close();
  }
}

async function run(browser) {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  await page.addInitScript(rafHookInitScript);
  // A rule strategy that trades (RSI 45 / 60 on daily AAPL), so the graph
  // run has markers to draw. Set once, before the app first loads.
  await page.addInitScript(() => {
    if (localStorage.getItem('strategylab-strategy')) return;
    localStorage.setItem('strategylab-strategy', JSON.stringify({
      buyRules: [{ indicator: 'rsi', condition: 'below', value: 45, params: { period: 14 } }],
      sellRules: [{ indicator: 'rsi', condition: 'above', value: 60, params: { period: 14 } }],
      buyLogic: 'AND', sellLogic: 'AND', capital: 10000, posSize: 100, direction: 'long',
    }));
  });

  const consoleErrors = [];
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return;
    const text = msg.text();
    if (/favicon/i.test(text)) return;
    consoleErrors.push(text);
  });
  page.on('pageerror', (err) => consoleErrors.push(`[uncaught] ${err.message}`));

  console.log(`[probe] ${BASE_URL}`);
  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForSelector('canvas', { timeout: 15000 });

  // 1. A rule backtest gives graph view a strategy; then a graph run.
  await page.getByRole('button', { name: /Run Backtest/ }).first().click();
  await page.getByRole('button', { name: /^Summary$/ }).first().waitFor({ timeout: 20000 });
  await toggleView(page);
  await page.waitForSelector('[data-testid="nb-split"]', { timeout: 15000 });
  record('graph view shows the split', true);
  // The toolbar's button (the read-only Data Sheet has its own, UX-08).
  await page.getByTestId('nb-btn-edit').click();
  await page.click('[data-testid="nb-btn-run"]');
  await page.waitForFunction(
    () => /trades/.test(document.querySelector('[data-testid="nb-chart-bar-summary"]')?.textContent ?? ''),
    null, { timeout: 30000 });
  const summary = await page.textContent('[data-testid="nb-chart-bar-summary"]');
  record('chart bar shows the run summary, with trades',
    /^\S+ \S+ · [1-9]\d* trades? · [+-]\d+\.\d% · Sharpe /.test(summary ?? ''), summary ?? '');
  const header = await page.textContent('[data-testid="results-graph-header"]').catch(() => null);
  record('Results shows the graph header', /^GRAPH.+cooked \d{2}:\d{2}:\d{2}/.test(header ?? ''), header ?? 'missing');

  // 2. Open the chart panel: the one chart moves in.
  const bar = page.locator('[data-testid="nb-chart-bar"]');
  if ((await bar.getAttribute('aria-expanded')) !== 'true') await bar.click();
  await page.waitForSelector('[data-testid="nb-chart-body"] canvas', { timeout: 15000 });
  await page.waitForTimeout(800);
  const open = await chartCensus(page);
  record('chart panel open (aria-expanded=true)', (await bar.getAttribute('aria-expanded')) === 'true');
  record('every price-chart instance is inside the split', open.inSplit > 0 && open.total - open.inResults === open.inSplit,
    `total ${open.total}, in split ${open.inSplit}, in results ${open.inResults}`);
  const perChart = open.inSplit;
  // The run must still be on screen (a late layout tidy once cleared it).
  const stillThere = await page.textContent('[data-testid="nb-chart-bar-summary"]');
  record('the run is still shown after the chart opens', stillThere === summary, stillThere ?? '');
  await page.screenshot({ path: path.join(OUT_DIR, 'graph-split-open.png') });
  console.log('  screenshot → .run/render-probe/graph-split-open.png');

  // 3. Toggle graph view / chart view 20 times: never a leaked or doubled chart.
  let leak = null;
  for (let i = 0; i < TOGGLES; i++) {
    await toggleView(page);
    await page.waitForTimeout(350);
    const gv = await graphViewActive(page);
    const c = await chartCensus(page);
    const priceCharts = c.total - c.inResults;
    if (gv ? c.inSplit !== perChart : (c.inSplit !== 0 || priceCharts !== perChart)) {
      leak = `toggle ${i + 1} (${gv ? 'graph' : 'chart'} view): ${JSON.stringify(c)}, expected ${perChart}`;
      break;
    }
  }
  record(`toggle views ${TOGGLES}x keeps exactly one chart`, leak === null, leak ?? `${perChart} charts each time`);
  if (!(await graphViewActive(page))) await toggleView(page);
  await page.waitForSelector('[data-testid="nb-chart-body"] canvas', { timeout: 15000 });

  // 4. Drag the chart handle 10 times, then double-click it.
  const handle = page.locator('[data-testid="nb-split-handle-chart"]');
  const panel = page.locator('[data-testid="nb-chart-panel"]');
  const heights = [];
  for (let i = 0; i < DRAGS; i++) {
    const box = await handle.boundingBox();
    if (!box) break;
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    const dy = i % 2 === 0 ? -80 : 60;
    await page.mouse.move(x, y);
    await page.mouse.down();
    for (let s = 1; s <= 6; s++) await page.mouse.move(x, y + (dy * s) / 6);
    await page.mouse.up();
    await page.waitForTimeout(120);
    heights.push(Math.round((await panel.boundingBox())?.height ?? 0));
  }
  const moved = new Set(heights).size > 1;
  record(`drag the chart handle ${DRAGS}x resizes the panel`, moved, heights.join(','));
  const group = await page.locator('.nb-split__group').boundingBox();
  await handle.dblclick();
  await page.waitForTimeout(300);
  const afterReset = (await panel.boundingBox())?.height ?? 0;
  const share = group ? afterReset / group.height : 0;
  record('double-click the handle resets the chart to 35%', Math.abs(share - 0.35) < 0.03, `${(share * 100).toFixed(1)}%`);

  // 5. Idle canary with the chart open.
  await page.mouse.move(5, 5);
  await page.waitForTimeout(1000);
  const raf = await idleRaf(page, IDLE_MS);
  record(`idle rAF < ${IDLE_RAF_MAX} in ${IDLE_MS / 1000}s with the chart open`, raf < IDLE_RAF_MAX, `got ${raf}`);

  // 6. Close the chart from its bar.
  await bar.click();
  await page.waitForTimeout(400);
  const closed = await chartCensus(page);
  record('closing the chart unmounts it', (await bar.getAttribute('aria-expanded')) === 'false' && closed.inSplit === 0
    && (await page.locator('[data-testid="nb-chart-body"]').count()) === 0, JSON.stringify(closed));
  const rafClosed = await idleRaf(page, IDLE_MS);
  record(`idle rAF < ${IDLE_RAF_MAX} in ${IDLE_MS / 1000}s with the chart closed`, rafClosed < IDLE_RAF_MAX, `got ${rafClosed}`);

  // 7. Console.
  record('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '));

  const failed = results.filter(r => !r.pass).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed.`);
  process.exit(failed ? 1 : 0);
}

main().catch((err) => {
  console.error('Probe error:', err.message);
  process.exit(1);
});
