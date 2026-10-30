// End-to-end tests: real server, real SQLite database, real Chrome. Only the Agora network is replaced
// (by agora_stub.js) so calls can be exercised without Agora; one step also checks the real vendored SDK
// (loaded under the production CSP, driving Chrome's fake camera, no network needed).
//
// No real Agora project is needed: any 32-character values work, because tokens are only minted, never
// presented to Agora. Use a throwaway database and restart the server between runs: registration and
// sign-in throttles live in the server's memory, so several runs against one process will trip them.
//
//   cd flet_app
//   SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))") \
//   AGORA_APP_ID=0123456789abcdef0123456789abcdef AGORA_APP_CERTIFICATE=fedcba9876543210fedcba9876543210 \
//   DATABASE_URL=sqlite:////tmp/videochat-e2e.sqlite3 SCRYPT_N=16384 PORT=8560 python main.py &
//   cd tests/e2e && npm install && BASE=http://localhost:8560 SHOTS=./shots npm test
//
// Puppeteer downloads Chrome on install; set PUPPETEER_EXECUTABLE_PATH to use one you already have.
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');
const { sleep, openApp, pageText, waitForText, clickButton, clickCheckbox, fill, roomFrame } = require('./lib');

const BASE = (process.env.BASE || 'http://localhost:8550').replace(/\/$/, '');
const SHOTS = process.env.SHOTS || '';
const suffix = Date.now().toString(36).slice(-5);
const alice = { user: `alice_${suffix}`, email: `alice_${suffix}@example.com`, name: 'Alice E2E', pw: `Correct-Horse-${suffix}-battery`, pw2: `Another-Fine-${suffix}-phrase` };
const bob = { user: `bob_${suffix}`, email: `bob_${suffix}@example.com`, name: 'Bob E2E', pw: `Staple-Horse-${suffix}-battery` };
const ROOM = `e2e-${suffix}`;
const ROOM_UPPER = ROOM.toUpperCase();

const results = [];
const consoleErrors = [];
let browser;

async function shot(page, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, name + '.png') });
}

async function step(name, fn) {
  const started = Date.now();
  try {
    await fn();
    results.push({ name, ok: true });
    console.log(`  PASS  ${name} (${Date.now() - started} ms)`);
  } catch (error) {
    results.push({ name, ok: false, error });
    console.log(`  FAIL  ${name}\n        ${String(error.message).split('\n').join('\n        ')}`);
  }
}

function assert(condition, message) {
  if (!condition) { throw new Error(message); }
}

async function register(page, who) {
  await clickButton(page, 'Create an account');
  await waitForText(page, 'Create your account');
  await fill(page, 'Username', who.user);
  await fill(page, 'Email', who.email);
  await fill(page, 'Password', who.pw);
  await fill(page, 'Confirm password', who.pw);
  await clickButton(page, 'Create account');
  await waitForText(page, `Welcome, ${who.user}`);
}

async function join(page, who, room) {
  await fill(page, 'Room name', room);
  await fill(page, 'Display name', who.name);
  await clickButton(page, 'Join room');
  const frame = await roomFrame(page);
  await frame.waitForFunction(() => document.getElementById('status').textContent === 'Connected', { timeout: 20000 });
  return frame;
}

(async () => {
  browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'],
  });
  const ctxA = await browser.createBrowserContext();
  const ctxB = await browser.createBrowserContext();
  const requests = [];
  let pageA, pageB, frameA, frameB;
  console.log(`E2E against ${BASE}`);

  // ---------------------------------------------------------------- sign-up / sign-in
  await step('an anonymous visit lands on the sign-in page', async () => {
    pageA = await openApp(ctxA, BASE + '/', { log: consoleErrors });
    pageA.on('request', (r) => requests.push({ url: r.url(), headers: r.headers() }));
    await waitForText(pageA, 'Sign in to start or join a room');
    assert(new URL(pageA.url()).pathname === '/login', 'expected /login, got ' + pageA.url());
    await shot(pageA, '01-login');
  });

  await step('registration rejects a weak password with a clear message', async () => {
    await clickButton(pageA, 'Create an account');
    await waitForText(pageA, 'Create your account');
    await fill(pageA, 'Username', alice.user);
    await fill(pageA, 'Email', alice.email);
    await fill(pageA, 'Password', 'short');
    await fill(pageA, 'Confirm password', 'short');
    await clickButton(pageA, 'Create account');
    await waitForText(pageA, /Password must be at least 12 characters/);
    await shot(pageA, '02-register-weak-password');
  });

  await step('registration with a strong password creates the account and signs in', async () => {
    await fill(pageA, 'Password', alice.pw);
    await fill(pageA, 'Confirm password', alice.pw);
    await clickButton(pageA, 'Create account');
    await waitForText(pageA, `Welcome, ${alice.user}`);
    await shot(pageA, '03-lobby');
  });

  await step('joining an invalid room name is rejected before any request is made', async () => {
    await fill(pageA, 'Room name', 'bad room!');
    await clickButton(pageA, 'Join room');
    await waitForText(pageA, /Room name must be 1-64 characters/);
  });

  // ---------------------------------------------------------------- the room
  await step('joining opens the embedded room page, which connects and strips its ticket from the URL', async () => {
    frameA = await join(pageA, alice, ROOM);
    const info = await frameA.evaluate(() => ({
      url: location.href,
      room: document.getElementById('room-name').textContent,
      tiles: Array.from(document.querySelectorAll('.tile')).map((t) => ({ uid: t.dataset.uid, name: t.querySelector('.name').textContent, handle: t.querySelector('.handle').textContent })),
      joined: window.__agoraStub.client.joined,
      published: window.__agoraStub.client.published,
    }));
    assert(!/p=/.test(info.url) && !info.url.includes('#'), 'ticket still in the address bar: ' + info.url);
    assert(info.room === ROOM_UPPER, `room title was ${info.room}`);
    assert(info.joined.channel === ROOM_UPPER && /^006[0-9a-f]{32}/.test(info.joined.token), 'Agora join call is wrong: ' + JSON.stringify(info.joined).slice(0, 200));
    assert(info.published.join() === 'audio,video', 'tracks published: ' + info.published);
    assert(info.tiles.length === 1 && info.tiles[0].name === `${alice.name} (you)` && info.tiles[0].handle === '@' + alice.user, 'local tile: ' + JSON.stringify(info.tiles));
    await sleep(500);
    await shot(pageA, '04-room');
  });

  await step('a second user joins the same room and the first user sees their verified name', async () => {
    pageB = await openApp(ctxB, BASE + '/', { log: consoleErrors });
    await register(pageB, bob);
    frameB = await join(pageB, bob, ROOM);
    const bobUid = await frameB.evaluate(() => window.__agoraStub.client.uid);
    assert(Number.isInteger(bobUid) && bobUid > 0, 'bad uid ' + bobUid);
    // pretend Agora tells Alice's browser that Bob published video and audio
    await frameA.evaluate((uid) => {
      const user = { uid, videoTrack: { play(el) { el.textContent = 'remote video'; } }, audioTrack: { play() {} } };
      window.__agoraStub.emit('user-published', user, 'video');
      window.__agoraStub.emit('user-published', user, 'audio');
    }, bobUid);
    await frameA.waitForFunction((uid) => {
      const tile = document.querySelector(`.tile[data-uid="${uid}"]`);
      return tile && tile.querySelector('.name').textContent === 'Bob E2E';
    }, { timeout: 10000 }, bobUid);
    const handle = await frameA.evaluate((uid) => document.querySelector(`.tile[data-uid="${uid}"] .handle`).textContent, bobUid);
    assert(handle === '@' + bob.user, 'handle was ' + handle);
    await sleep(400);
    await shot(pageA, '05-room-two-participants');
    // and when he leaves the tile disappears
    await frameA.evaluate((uid) => window.__agoraStub.emit('user-left', { uid }), bobUid);
    await frameA.waitForFunction((uid) => !document.querySelector(`.tile[data-uid="${uid}"]`), { timeout: 5000 }, bobUid);
  });

  await step('the Agora token is renewed before it expires', async () => {
    await frameA.evaluate(() => window.__agoraStub.emit('token-privilege-will-expire'));
    await frameA.waitForFunction(() => window.__agoraStub.client.renewals === 1 && /^006/.test(window.__agoraStub.client.lastToken), { timeout: 10000 });
  });

  await step('mute and camera buttons toggle the local tracks', async () => {
    await frameA.click('#btn-mic');
    await frameA.waitForFunction(() => document.getElementById('btn-mic').textContent === 'Unmute' && document.getElementById('btn-mic').getAttribute('aria-pressed') === 'true');
    await frameA.click('#btn-cam');
    await frameA.waitForFunction(() => document.getElementById('btn-cam').textContent === 'Camera on');
  });

  await step('the ticket and credentials only ever travel in an Authorization header', async () => {
    const api = requests.filter((r) => r.url.includes('/api/rtc/'));
    assert(api.length >= 2, 'expected API calls, saw ' + api.length);
    for (const r of api) {
      assert(/^Bearer v1\.[\w-]+\.[\w-]+$/.test(r.headers.authorization || ''), 'missing bearer ticket on ' + r.url);
      assert(!/v1\.[\w-]{10,}\.[\w-]{20,}/.test(r.url), 'ticket leaked into URL ' + r.url);
      assert(!r.headers.cookie, 'cookie sent to ' + r.url);
    }
    // Puppeteer reports a navigation's fragment as part of its URL, but browsers never put a fragment on the
    // wire (the server log is checked for this separately). Everything else must be free of the ticket.
    const wire = (u) => u.split('#')[0];
    assert(requests.some((r) => r.url.includes('/room.html#p=v1.')), 'expected the room page to be opened with the ticket in the fragment');
    assert(requests.every((r) => !/v1\.[\w-]{10,}\.[\w-]{20,}/.test(wire(r.url)) && !/v1\.[\w-]{10,}\./.test(r.headers.referer || '')), 'ticket found in a request URL or Referer');
  });

  await step('leaving the room returns to the lobby', async () => {
    await clickButton(pageA, 'Leave');
    await waitForText(pageA, `Welcome, ${alice.user}`);
    await waitForText(pageA, new RegExp(ROOM_UPPER)); // now listed under "Recent rooms"
    await shot(pageA, '06-lobby-recent-room');
  });

  // ---------------------------------------------------------------- profile & sessions
  await step('the profile page shows the account and lists created rooms', async () => {
    await clickButton(pageA, 'Profile');
    await waitForText(pageA, alice.email);
    await waitForText(pageA, 'Rooms you created');
    await waitForText(pageA, new RegExp(ROOM_UPPER));
    await shot(pageA, '07-profile');
  });

  await step('the profile page links back to the lobby', async () => {
    await clickButton(pageA, 'Lobby');
    await waitForText(pageA, `Welcome, ${alice.user}`);
    await clickButton(pageA, 'Profile');
    await waitForText(pageA, 'Rooms you created');
  });

  await step('changing the password requires the current one', async () => {
    await fill(pageA, 'Current password', 'definitely not it');
    await fill(pageA, 'New password', alice.pw2);
    await fill(pageA, 'Confirm new password', alice.pw2);
    await clickButton(pageA, 'Change password');
    await waitForText(pageA, 'Current password is incorrect.');
    await fill(pageA, 'Current password', alice.pw);
    await clickButton(pageA, 'Change password');
    await waitForText(pageA, /Password changed/);
  });

  await step('after signing out the old password fails and the new one works', async () => {
    await clickButton(pageA, 'Sign out');
    await waitForText(pageA, 'Sign in to start or join a room');
    await fill(pageA, 'Username or email', alice.user);
    await fill(pageA, 'Password', alice.pw);
    await clickButton(pageA, 'Sign in');
    await waitForText(pageA, 'Invalid username or password.');
    await fill(pageA, 'Password', alice.pw2);
    await clickButton(pageA, 'Sign in');
    await waitForText(pageA, `Welcome, ${alice.user}`);
  });

  await step('repeated failed sign-ins are throttled', async () => {
    const page = await openApp(await browser.createBrowserContext(), BASE + '/', { log: consoleErrors });
    for (let i = 0; i < 5; i += 1) {
      await fill(page, 'Username or email', `ghost_${suffix}`);
      await fill(page, 'Password', `wrong password ${i}`);
      await clickButton(page, 'Sign in');
      await waitForText(page, 'Invalid username or password.');
    }
    await fill(page, 'Username or email', `ghost_${suffix}`);
    await fill(page, 'Password', 'wrong password again');
    await clickButton(page, 'Sign in');
    await waitForText(page, /Too many attempts\. Try again in \d+ seconds\./);
    await shot(page, '08-throttled');
    await page.close();
  });

  await step('"sign out of all devices" ends the other browser\'s login at its next action', async () => {
    // Bob is still signed in on pageB; sign him out everywhere from a second session of his own account.
    const second = await openApp(await browser.createBrowserContext(), BASE + '/', { log: consoleErrors });
    await fill(second, 'Username or email', bob.user);
    await fill(second, 'Password', bob.pw);
    await clickButton(second, 'Sign in');
    await waitForText(second, `Welcome, ${bob.user}`);
    await clickButton(second, 'Profile');
    await waitForText(second, 'Sign out of all devices');
    await clickButton(second, 'Sign out of all devices');
    await waitForText(second, 'Sign in to start or join a room');
    await clickButton(pageB, 'Leave'); // pageB was still inside the room
    await waitForText(pageB, 'Sign in to start or join a room');
    await second.close();
  });

  await step('"keep me signed in" survives a page reload', async () => {
    await clickButton(pageA, 'Sign out');
    await waitForText(pageA, 'Sign in to start or join a room');
    await fill(pageA, 'Username or email', alice.user);
    await fill(pageA, 'Password', alice.pw2);
    await clickCheckbox(pageA, 'Keep me signed in on this device');
    await clickButton(pageA, 'Sign in');
    await waitForText(pageA, `Welcome, ${alice.user}`);
    await pageA.reload({ waitUntil: 'networkidle2' });
    await waitForText(pageA, 'Sign in to start or join a room', 1).catch(() => {}); // semantics may need re-enabling
    await require('./lib').enableSemantics(pageA);
    await waitForText(pageA, `Welcome, ${alice.user}`);
  });

  // ---------------------------------------------------------------- the real SDK and the browser policy
  await step('the real Agora SDK loads under the strict CSP and drives a (fake) camera without violations', async () => {
    const page = await (await browser.createBrowserContext()).newPage();
    await page.evaluateOnNewDocument(() => {
      window.__violations = [];
      document.addEventListener('securitypolicyviolation', (e) => window.__violations.push(`${e.violatedDirective} ${e.blockedURI}`));
    });
    await page.goto(BASE + '/room.html', { waitUntil: 'load' }); // no ticket: the page must not try to join
    const outcome = await page.evaluate(async () => {
      const notice = document.getElementById('notice').textContent;
      const version = window.AgoraRTC && window.AgoraRTC.VERSION;
      const tracks = await window.AgoraRTC.createMicrophoneAndCameraTracks();
      const cam = tracks[1];
      cam.play(document.getElementById('grid'));
      await new Promise((r) => setTimeout(r, 1500));
      const video = document.querySelector('#grid video');
      const result = { notice, version, hasVideo: Boolean(video), width: video ? video.videoWidth : 0, violations: window.__violations.slice() };
      tracks.forEach((t) => { t.stop(); t.close(); });
      return result;
    });
    assert(/missing its access ticket/.test(outcome.notice), 'notice was: ' + outcome.notice);
    assert(outcome.version === '4.8.0', 'SDK version ' + outcome.version);
    assert(outcome.hasVideo && outcome.width > 0, 'no playing video: ' + JSON.stringify(outcome));
    assert(outcome.violations.length === 0, 'CSP violations: ' + outcome.violations.join(' | '));
    await page.close();
  });

  await step('other origins cannot embed the room page', async () => {
    const page = await (await browser.createBrowserContext()).newPage();
    const blocked = [];
    page.on('requestfailed', (r) => blocked.push(`${r.url()} ${r.failure() && r.failure().errorText}`));
    await page.setContent(`<iframe src="${BASE}/room.html" width="300" height="200"></iframe>`);
    await sleep(2000);
    const frames = page.frames().filter((f) => f !== page.mainFrame());
    const framed = frames.some((f) => f.url().includes('/room.html'));
    assert(!framed || blocked.some((b) => /BLOCKED_BY_RESPONSE/.test(b)), 'room page was framed by another origin: ' + frames.map((f) => f.url()));
    await page.close();
  });

  await step('the browser console shows no unexpected errors', async () => {
    // Known, harmless noise from Flet 1.0.5's web client (neither comes from this app):
    //  - it tries to fetch the Rive runtime from jsDelivr; our CSP blocks that and no Rive content is used;
    //  - typing a "-" key in any text field makes its WASM engine throw a bare "Exception: Exception"
    //    (reproducible on the stock login field); the character is still inserted.
    const benign = [/rive_native\.js/, /Failed to load resource/, /^pageerror: Exception: Exception$/];
    const unexpected = consoleErrors.filter((e) => !benign.some((re) => re.test(e)));
    const noise = consoleErrors.length - unexpected.length;
    if (noise) { console.log(`        (ignored ${noise} known Flet-client messages)`); }
    assert(unexpected.length === 0, unexpected.slice(0, 5).join('\n'));
  });

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  process.exit(failed.length ? 1 : 0);
})().catch(async (error) => {
  console.error('E2E aborted:', error);
  if (browser) { await browser.close().catch(() => {}); }
  process.exit(2);
});
