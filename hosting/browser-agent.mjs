#!/usr/bin/env node
/**
 * browser-agent.mjs  — deterministic browser executor for Me + Lia
 * ----------------------------------------------------------------
 * Fully isolated from the Gemini render pipeline. It polls its OWN GitHub
 * queue (browser-jobs/) so it never collides with watcher-v8 / the Gemini
 * relay-client, which only watch jobs/ -> ~/gemini-jobs/pending/.
 *
 * FLOW:
 *   GitHub  Alaska-Artemisia/Gemini-relay/browser-jobs/<name>.json
 *      -> this agent pulls it, runs the step list in a real Chromium
 *      -> writes <name>__result.json (+ any screenshots) to the Working Folder
 *      -> deletes the job from browser-jobs/ to dequeue it
 *
 * It uses a PERSISTENT browser profile (~/gemini-jobs/browser-profile) so that
 * authenticated sites work after a one-time manual login into that profile.
 * For public pages (the first test) no login is needed.
 *
 * TOKEN: read from ~/gemini-jobs/.relay_token (one line) or env RELAY_TOKEN.
 *
 * JOB SCHEMA (browser-jobs/<filename>.json):
 * {
 *   "type": "browser",
 *   "filename": "test_slowwardrobe",     // job id; names the result/screenshots
 *   "headful": false,                     // optional; true = visible window
 *   "outputDir": "/abs/path",             // optional; defaults to Working Folder
 *   "steps": [
 *     { "do": "goto",       "url": "https://meandlia.com/slow-wardrobe" },
 *     { "do": "waitFor",    "selector": "h1", "timeoutMs": 15000 },
 *     { "do": "text",       "selector": "h1", "as": "headline" },
 *     { "do": "attr",       "selector": "a.cta", "name": "href", "as": "ctaHref" },
 *     { "do": "count",      "selector": "a", "as": "linkCount" },
 *     { "do": "exists",     "selector": "a[href$='.pdf']", "as": "hasPdf" },
 *     { "do": "eval",       "script": "() => document.title", "as": "pageTitle" },
 *     { "do": "click",      "selector": "#open" },
 *     { "do": "type",       "selector": "#email", "text": "x@y.com" },
 *     { "do": "press",      "key": "Enter" },
 *     { "do": "waitMs",     "ms": 1200 },
 *     { "do": "screenshot", "name": "page", "fullPage": true }
 *   ]
 * }
 * Extracted values land in result.data[<as>]; screenshots in result.screenshots.
 */

import fs from 'fs';
import path from 'path';
import { spawnSync } from 'child_process';
import { createRequire } from 'module';

const HOME = process.env.HOME;
const JOBS_HOME = path.join(HOME, 'gemini-jobs');
const PROFILE_DIR = path.join(JOBS_HOME, 'browser-profile');
const LOG_FILE = path.join(JOBS_HOME, 'browser-agent.log');
const DONE_DIR = path.join(JOBS_HOME, 'browser-done');
const WORKING_FOLDER =
  '/Users/Foongbear/Library/CloudStorage/GoogleDrive-da@heyleyholdings.com/Shared drives/Me + Lia/Content/3. Gemini Working Folder';

const REPO = 'Alaska-Artemisia/Gemini-relay';
const QUEUE = 'browser-jobs';
const POLL_MS = 3000;

fs.mkdirSync(JOBS_HOME, { recursive: true });
fs.mkdirSync(DONE_DIR, { recursive: true });
fs.mkdirSync(PROFILE_DIR, { recursive: true });

function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  try { fs.appendFileSync(LOG_FILE, line + '\n'); } catch {}
}

function readToken() {
  if (process.env.RELAY_TOKEN) return process.env.RELAY_TOKEN.trim();
  const f = path.join(JOBS_HOME, '.relay_token');
  if (fs.existsSync(f)) return fs.readFileSync(f, 'utf8').trim();
  return null;
}
const TOKEN = readToken();
if (!TOKEN) {
  log('FATAL: no relay token. Put it in ~/gemini-jobs/.relay_token or set RELAY_TOKEN. Exiting.');
  process.exit(1);
}

// ---------- GitHub helpers (queue lives in the repo) ----------
async function gh(method, p, body) {
  const res = await fetch(`https://api.github.com/repos/${REPO}/${p}`, {
    method,
    headers: {
      Authorization: `token ${TOKEN}`,
      'User-Agent': 'melia-browser-agent',
      Accept: 'application/vnd.github+json',
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 404) return { _404: true };
  const text = await res.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch { json = { _raw: text }; }
  if (!res.ok) { json = json || {}; json._status = res.status; }
  return json;
}

async function listQueue() {
  const r = await gh('GET', `contents/${QUEUE}`);
  if (r._404 || !Array.isArray(r)) return [];
  return r.filter((f) => f.type === 'file' && f.name.endsWith('.json'));
}

async function fetchJob(file) {
  const r = await gh('GET', `contents/${QUEUE}/${file.name}`);
  if (!r || !r.content) return null;
  const raw = Buffer.from(r.content, 'base64').toString('utf8');
  return { sha: r.sha, job: JSON.parse(raw) };
}

async function deleteJob(name, sha) {
  for (let i = 0; i < 4; i++) {
    const r = await gh('DELETE', `contents/${QUEUE}/${name}`, {
      message: `browser-agent: dequeue ${name}`,
      sha,
    });
    if (!r._status) return true;
    if (r._status === 409) { await sleep(1500); // re-read sha then retry
      const re = await gh('GET', `contents/${QUEUE}/${name}`);
      if (re._404) return true;
      if (re.sha) sha = re.sha;
      continue;
    }
    log(`delete ${name} failed status=${r._status} ${JSON.stringify(r).slice(0,160)}`);
    return false;
  }
  return false;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- Playwright bootstrap (self-installing on first run) ----------
let chromium = null;
async function ensurePlaywright() {
  if (chromium) return chromium;
  try {
    ({ chromium } = await import('playwright'));
  } catch {
    log('Playwright not found — installing (one-time, downloads Chromium)...');
    spawnSync('npm', ['install', 'playwright'], { cwd: JOBS_HOME, stdio: 'inherit' });
    spawnSync('npx', ['playwright', 'install', 'chromium'], { cwd: JOBS_HOME, stdio: 'inherit' });
    const require = createRequire(path.join(JOBS_HOME, 'noop.js'));
    ({ chromium } = require(require.resolve('playwright', { paths: [JOBS_HOME] })));
    log('Playwright installed.');
  }
  return chromium;
}

// ---------- Step executor (deterministic) ----------
async function runStep(page, step, result, outDir, jobName) {
  const sel = step.selector;
  const to = step.timeoutMs ?? 15000;
  switch (step.do) {
    case 'goto':
      await page.goto(step.url, { waitUntil: step.waitUntil || 'load', timeout: step.timeoutMs ?? 30000 });
      return { do: 'goto', url: step.url, ok: true };
    case 'waitFor':
      await page.waitForSelector(sel, { timeout: to, state: step.state || 'visible' });
      return { do: 'waitFor', selector: sel, ok: true };
    case 'waitMs':
      await sleep(step.ms ?? 500);
      return { do: 'waitMs', ms: step.ms ?? 500, ok: true };
    case 'click':
      await page.click(sel, { timeout: to });
      return { do: 'click', selector: sel, ok: true };
    case 'type':
      await page.fill(sel, step.text ?? '', { timeout: to });
      return { do: 'type', selector: sel, ok: true };
    case 'press':
      await page.keyboard.press(step.key);
      return { do: 'press', key: step.key, ok: true };
    case 'text': {
      const el = await page.waitForSelector(sel, { timeout: to });
      const v = (await el.textContent() || '').trim();
      if (step.as) result.data[step.as] = v;
      return { do: 'text', selector: sel, as: step.as, value: v, ok: true };
    }
    case 'attr': {
      const el = await page.waitForSelector(sel, { timeout: to });
      const v = await el.getAttribute(step.name);
      if (step.as) result.data[step.as] = v;
      return { do: 'attr', selector: sel, name: step.name, as: step.as, value: v, ok: true };
    }
    case 'count': {
      const v = await page.locator(sel).count();
      if (step.as) result.data[step.as] = v;
      return { do: 'count', selector: sel, as: step.as, value: v, ok: true };
    }
    case 'exists': {
      const v = (await page.locator(sel).count()) > 0;
      if (step.as) result.data[step.as] = v;
      return { do: 'exists', selector: sel, as: step.as, value: v, ok: true };
    }
    case 'eval': {
      const v = await page.evaluate(step.script);
      if (step.as) result.data[step.as] = v;
      return { do: 'eval', as: step.as, value: v, ok: true };
    }
    case 'screenshot': {
      const name = step.name || `shot${result.screenshots.length + 1}`;
      const file = path.join(outDir, `${jobName}__${name}.png`);
      await page.screenshot({ path: file, fullPage: step.fullPage !== false });
      result.screenshots.push(file);
      return { do: 'screenshot', name, file, ok: true };
    }
    default:
      return { do: step.do, ok: false, error: 'unknown step' };
  }
}

async function runJob(job) {
  const jobName = job.filename || `job_${Date.now()}`;
  const outDir = job.outputDir || WORKING_FOLDER;
  fs.mkdirSync(outDir, { recursive: true });
  const result = {
    job: jobName, ok: false, startedAt: new Date().toISOString(),
    finishedAt: null, data: {}, screenshots: [], steps: [], error: null,
  };
  const cm = await ensurePlaywright();
  const context = await cm.launchPersistentContext(PROFILE_DIR, {
    headless: job.headful ? false : true,
    viewport: { width: job.width || 1280, height: job.height || 1600 },
  });
  try {
    const page = context.pages()[0] || (await context.newPage());
    for (const step of (job.steps || [])) {
      try {
        const r = await runStep(page, step, result, outDir, jobName);
        result.steps.push(r);
        if (r.ok === false) { result.error = `step failed: ${step.do}`; break; }
      } catch (e) {
        result.steps.push({ do: step.do, ok: false, error: String(e.message || e) });
        result.error = `step threw on '${step.do}': ${String(e.message || e)}`;
        break;
      }
    }
    result.ok = !result.error;
  } finally {
    await context.close();
    result.finishedAt = new Date().toISOString();
  }
  const rfile = path.join(outDir, `${jobName}__result.json`);
  fs.writeFileSync(rfile, JSON.stringify(result, null, 2));
  log(`job ${jobName} -> ok=${result.ok} steps=${result.steps.length} shots=${result.screenshots.length} -> ${rfile}`);
  return result;
}

// ---------- Main loop ----------
log(`browser-agent up. queue=${QUEUE} profile=${PROFILE_DIR}`);
let busy = false;
async function tick() {
  if (busy) return;
  busy = true;
  try {
    const files = await listQueue();
    for (const f of files) {
      const fetched = await fetchJob(f);
      if (!fetched) continue;
      const { sha, job } = fetched;
      if (job.type && job.type !== 'browser') { continue; } // not ours
      log(`picked ${f.name}`);
      try { await runJob(job); }
      catch (e) { log(`runJob error ${f.name}: ${String(e.message || e)}`); }
      await deleteJob(f.name, sha);
      try { fs.writeFileSync(path.join(DONE_DIR, f.name), '1'); } catch {}
    }
  } catch (e) {
    log(`tick error: ${String(e.message || e)}`);
  } finally {
    busy = false;
  }
}
setInterval(tick, POLL_MS);
tick();
