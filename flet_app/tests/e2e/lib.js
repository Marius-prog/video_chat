// Helpers for driving the Flet web client (Flutter canvas) through its accessibility tree.
// After enableSemantics() every widget has a DOM twin: text fields are <input aria-label="Label">,
// buttons are flt-semantics[role=button] with their text, so tests never depend on pixel positions.
const fs = require('fs');
const path = require('path');

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const STUB = fs.readFileSync(path.join(__dirname, 'agora_stub.js'), 'utf8');
const SDK_URL_SUFFIX = '/vendor/AgoraRTC_N-4.8.0.js';

async function openApp(context, url, { stubSdk = true, log = [] } = {}) {
  const page = await context.newPage();
  await page.setViewport({ width: 1100, height: 800 });
  page.on('console', (m) => { if (m.type() === 'error') log.push(m.text()); });
  page.on('pageerror', (e) => log.push('pageerror: ' + String(e)));
  if (stubSdk) {
    await page.setRequestInterception(true);
    page.on('request', (req) => {
      if (req.url().endsWith(SDK_URL_SUFFIX)) {
        req.respond({ status: 200, contentType: 'application/javascript', body: STUB });
      } else {
        req.continue();
      }
    });
  }
  await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
  await enableSemantics(page);
  return page;
}

async function enableSemantics(page) {
  await page.waitForSelector('flt-semantics-placeholder', { timeout: 30000 });
  await page.evaluate(() => document.querySelector('flt-semantics-placeholder').click());
  await sleep(700);
}

// All text currently exposed by the accessibility tree: the text of every leaf node, aria labels, and
// descriptions (Flutter puts a text field's error message in aria-description).
async function pageText(page) {
  return page.evaluate(() => {
    const out = [];
    for (const e of document.querySelectorAll('flt-semantics')) {
      if (!e.querySelector('flt-semantics')) {
        const text = (e.innerText || '').trim();
        if (text) out.push(text);
      }
      for (const attr of ['aria-label', 'aria-description', 'aria-errormessage']) {
        const value = e.getAttribute(attr);
        if (value) out.push(value);
      }
    }
    return out.join('\n');
  });
}

async function waitForText(page, pattern, timeout = 20000) {
  const re = pattern instanceof RegExp ? pattern : new RegExp(pattern.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const deadline = Date.now() + timeout;
  let text = '';
  while (Date.now() < deadline) {
    text = await pageText(page);
    if (re.test(text)) return text;
    await sleep(200);
  }
  throw new Error(`timed out waiting for ${re} - page text was:\n${text.slice(0, 600)}`);
}

// Flutter scrolls with its own physics, so bring a node on screen with the mouse wheel (like a user would).
// Anything inside the viewport counts as visible: the fixed AppBar buttons live at y < 60 and never scroll.
async function scrollInto(page, getRect) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    const rect = await page.evaluate(`(${getRect})()`); // getRect is the source of an arrow function
    const height = await page.evaluate(() => window.innerHeight);
    if (rect && rect.y > 4 && rect.y < height - 40) { return rect; }
    await page.mouse.move(550, 400);
    await page.mouse.wheel({ deltaY: rect && rect.y <= 4 ? -400 : 400 });
    await sleep(350);
  }
  throw new Error('could not scroll the element into view');
}

async function center(page, fn, arg, timeout = 15000) {
  const handle = await page.waitForFunction(fn, { timeout }, arg);
  return handle.jsonValue();
}

async function clickButton(page, text) {
  await page.waitForFunction((wanted) => Array.from(document.querySelectorAll('flt-semantics[role="button"]')).some((e) => e.innerText.trim() === wanted || e.getAttribute('aria-label') === wanted), { timeout: 15000 }, text);
  const point = await scrollInto(page, `() => {
    const wanted = ${JSON.stringify(text)};
    for (const e of document.querySelectorAll('flt-semantics[role="button"]')) {
      if (e.innerText.trim() === wanted || e.getAttribute('aria-label') === wanted) {
        const r = e.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && e.getAttribute('aria-disabled') !== 'true') return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
      }
    }
    return null;
  }`);
  await page.mouse.click(point.x, point.y);
  await sleep(250);
}

async function clickCheckbox(page, label) {
  await page.waitForSelector(`flt-semantics[role="checkbox"][aria-label="${label}"]`, { timeout: 15000 });
  const point = await scrollInto(page, `() => {
    const e = document.querySelector('flt-semantics[role="checkbox"][aria-label=${JSON.stringify(label)}]');
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return { x: r.x + 20, y: r.y + r.height / 2 };
  }`);
  await page.mouse.click(point.x, point.y);
  await sleep(250);
}

async function fill(page, label, value) {
  // The accessible name is "<label>" or "<label>\n<hint>", so match on the prefix.
  const selector = `input[aria-label^="${label}"], textarea[aria-label^="${label}"]`;
  await page.waitForSelector(selector, { timeout: 15000 });
  const box = await scrollInto(page, `() => {
    const e = document.querySelector(${JSON.stringify(selector)});
    if (!e) return null;
    const r = e.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }`);
  await page.mouse.click(box.x, box.y);
  await sleep(200);
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await page.keyboard.press('Backspace');
  await page.keyboard.type(value, { delay: 4 });
  await sleep(100);
}

async function roomFrame(page, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const frame = page.frames().find((f) => f.url().includes('/room.html'));
    if (frame) return frame;
    await sleep(200);
  }
  throw new Error('room iframe never appeared; frames: ' + page.frames().map((f) => f.url()).join(', '));
}

module.exports = { sleep, openApp, enableSemantics, pageText, waitForText, clickButton, clickCheckbox, fill, roomFrame };
