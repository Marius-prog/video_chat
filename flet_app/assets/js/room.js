/*
 * Room page: joins an Agora channel using credentials from this server.
 *
 * Security notes
 *  - The page is opened as /room.html#p=<room ticket>. The ticket is read from the URL fragment (never
 *    sent to a server in a request line) and immediately removed from the address bar/history.
 *  - It lives only in this closure. Every API call sends it as `Authorization: Bearer`, with no cookies.
 *  - The Agora App Certificate never reaches the browser; only short-lived tokens bound to channel + uid.
 *  - All remote text (display names) is inserted with textContent, never as HTML.
 *  - No inline script/handlers: the page runs under `script-src 'self'`.
 */
(function () {
  'use strict';

  var TICKET_RE = /^#p=([A-Za-z0-9._-]{20,2048})$/;

  function create(env) {
    var doc = env.document;
    var win = env.window;
    var AgoraRTC = env.AgoraRTC;
    var fetchFn = env.fetch.bind(win);

    var $ = function (id) { return doc.getElementById(id); };
    var grid = $('grid');
    var notice = $('notice');
    var statusEl = $('status');
    var btnMic = $('btn-mic');
    var btnCam = $('btn-cam');
    var btnLeave = $('btn-leave');
    var btnRejoin = $('btn-rejoin');

    var ticket = null;
    var client = null;
    var creds = null;
    var localMic = null;
    var localCam = null;
    var tiles = Object.create(null);
    var directory = Object.create(null);
    var active = false;
    var shownViolations = Object.create(null);

    // ---- small helpers -------------------------------------------------------------------------

    function setStatus(text) { statusEl.textContent = text; }

    function showNotice(text) {
      notice.textContent = text;
      notice.hidden = false;
    }

    function clearNotice() {
      notice.textContent = '';
      notice.hidden = true;
    }

    function takeTicket() {
      var match = TICKET_RE.exec(win.location.hash);
      if (!match) { return null; }
      // Remove the credential from the address bar and session history right away.
      win.history.replaceState(null, '', win.location.pathname + win.location.search);
      return match[1];
    }

    function reason(err) {
      var code = err && (err.code || err.name);
      var map = {
        PERMISSION_DENIED: 'permission was denied',
        NotAllowedError: 'permission was denied',
        DEVICE_NOT_FOUND: 'no device was found',
        NotFoundError: 'no device was found',
        NotReadableError: 'the device is in use by another application'
      };
      return map[code] || (err && err.message) || 'unknown error';
    }

    function describe(err) {
      if (err && err.status === 401) {
        return 'Your session has expired. Close this room and join again.';
      }
      if (err && err.status === 503) {
        return 'Video is not configured on this server.';
      }
      if (err && err.status === 429) {
        return 'Too many attempts. Wait a moment and try again.';
      }
      return (err && err.message) || 'Something went wrong.';
    }

    async function api(path, options) {
      options = options || {};
      var response = await fetchFn(path, {
        method: options.method || 'GET',
        headers: { Authorization: 'Bearer ' + ticket, Accept: 'application/json' },
        cache: 'no-store',
        credentials: 'omit',
        referrerPolicy: 'no-referrer',
        keepalive: Boolean(options.keepalive)
      });
      var body = null;
      try { body = await response.json(); } catch (ignored) { /* non-JSON error body */ }
      if (!response.ok) {
        var error = new Error((body && body.error) || ('Request failed (' + response.status + ')'));
        error.status = response.status;
        throw error;
      }
      return body;
    }

    // ---- participant tiles ---------------------------------------------------------------------

    function ensureTile(uid, local) {
      var key = String(uid);
      if (tiles[key]) { return tiles[key]; }
      var tile = doc.createElement('div');
      tile.className = local ? 'tile local' : 'tile';
      tile.dataset.uid = key;
      var video = doc.createElement('div');
      video.className = 'video';
      var label = doc.createElement('div');
      label.className = 'label';
      var name = doc.createElement('span');
      name.className = 'name';
      name.textContent = local ? 'You' : 'Guest ' + key;
      var handle = doc.createElement('span');
      handle.className = 'handle';
      label.append(name, handle);
      tile.append(video, label);
      grid.append(tile);
      tiles[key] = { tile: tile, video: video, name: name, handle: handle };
      return tiles[key];
    }

    function removeTile(uid) {
      var key = String(uid);
      if (tiles[key]) {
        tiles[key].tile.remove();
        delete tiles[key];
      }
    }

    async function labelTile(uid) {
      var key = String(uid);
      if (!(key in directory)) {
        try { directory[key] = await api('/api/rtc/members/' + encodeURIComponent(key)); }
        catch (ignored) { directory[key] = null; }
      }
      var entry = directory[key];
      var tile = tiles[key];
      if (entry && tile) {
        tile.name.textContent = entry.name;
        tile.handle.textContent = '@' + entry.username;
      }
    }

    // ---- Agora events --------------------------------------------------------------------------

    async function onPublished(user, mediaType) {
      await client.subscribe(user, mediaType);
      var tile = ensureTile(user.uid, false);
      labelTile(user.uid);
      if (mediaType === 'video') { user.videoTrack.play(tile.video); }
      if (mediaType === 'audio') { user.audioTrack.play(); }
    }

    function onUnpublished(user, mediaType) {
      if (mediaType === 'video' && tiles[String(user.uid)]) {
        tiles[String(user.uid)].video.textContent = '';
      }
    }

    function onLeft(user) { removeTile(user.uid); }

    async function renewToken() {
      try {
        var renewed = await api('/api/rtc/renew', { method: 'POST' });
        await client.renewToken(renewed.token);
      } catch (err) {
        showNotice(describe(err));
        setStatus('Session expiring');
      }
    }

    function wire() {
      client.on('user-published', function (user, type) {
        onPublished(user, type).catch(function (err) { showNotice('Could not show a participant: ' + reason(err)); });
      });
      client.on('user-unpublished', onUnpublished);
      client.on('user-left', onLeft);
      client.on('token-privilege-will-expire', renewToken);
      client.on('token-privilege-did-expire', renewToken);
      client.on('connection-state-change', function (current) {
        var labels = { CONNECTED: 'Connected', CONNECTING: 'Connecting…', RECONNECTING: 'Reconnecting…', DISCONNECTED: 'Disconnected', DISCONNECTING: 'Disconnecting…' };
        if (active) { setStatus(labels[current] || current); }
      });
    }

    // ---- local media ---------------------------------------------------------------------------

    async function openLocalTracks() {
      var tracks = [];
      try { localMic = await AgoraRTC.createMicrophoneAudioTrack(); tracks.push(localMic); }
      catch (err) { showNotice('Microphone unavailable: ' + reason(err) + '.'); }
      try { localCam = await AgoraRTC.createCameraVideoTrack(); tracks.push(localCam); }
      catch (err) { showNotice('Camera unavailable: ' + reason(err) + '.'); }
      btnMic.disabled = !localMic;
      btnCam.disabled = !localCam;
      return tracks;
    }

    function closeLocalTracks() {
      [localMic, localCam].forEach(function (track) {
        if (!track) { return; }
        try { track.stop(); track.close(); } catch (ignored) { /* already closed */ }
      });
      localMic = null;
      localCam = null;
      btnMic.disabled = true;
      btnCam.disabled = true;
    }

    async function toggle(track, button, onLabel, offLabel) {
      if (!track) { return; }
      var muted = !track.muted;
      await track.setMuted(muted);
      button.setAttribute('aria-pressed', String(muted));
      button.textContent = muted ? offLabel : onLabel;
    }

    // ---- lifecycle -----------------------------------------------------------------------------

    async function join() {
      clearNotice();
      btnRejoin.hidden = true;
      setStatus('Connecting…');
      try {
        creds = await api('/api/rtc/credentials', { method: 'POST' });
        $('room-name').textContent = creds.channel;
        doc.title = 'Room ' + creds.channel;

        client = AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });
        wire();
        await client.join(creds.app_id, creds.channel, creds.token, creds.uid);
        active = true;

        var tracks = await openLocalTracks();
        var self = ensureTile(creds.uid, true);
        self.name.textContent = creds.display_name + ' (you)';
        self.handle.textContent = '@' + creds.username;
        if (localCam) { localCam.play(self.video); }
        if (tracks.length) { await client.publish(tracks); }

        btnLeave.disabled = false;
        setStatus('Connected');
      } catch (err) {
        await teardown();
        showNotice(describe(err));
        setStatus('Not connected');
        if (!(err && (err.status === 401 || err.status === 503))) { btnRejoin.hidden = false; }
      }
    }

    async function teardown() {
      active = false;
      closeLocalTracks();
      if (client) {
        try { await client.leave(); } catch (ignored) { /* not joined */ }
        client = null;
      }
      Object.keys(tiles).forEach(removeTile);
      btnLeave.disabled = true;
    }

    async function leave() {
      await teardown();
      try { await api('/api/rtc/leave', { method: 'POST', keepalive: true }); } catch (ignored) { /* best effort */ }
      setStatus('You left the room.');
      btnRejoin.hidden = false;
    }

    function onPageHide() {
      if (!active) { return; }
      active = false;
      closeLocalTracks();
      try { client.leave(); } catch (ignored) { /* page is going away */ }
      api('/api/rtc/leave', { method: 'POST', keepalive: true }).catch(function () { /* best effort */ });
    }

    function reportPolicyViolation(event) {
      var key = event.violatedDirective + '|' + event.blockedURI;
      if (shownViolations[key]) { return; }
      shownViolations[key] = true;
      showNotice('Blocked by the Content-Security-Policy (' + event.violatedDirective + '): ' + (event.blockedURI || 'inline') +
        '. If this host is legitimate, add it to CSP_CONNECT_SRC_EXTRA on the server.');
    }

    async function start() {
      doc.addEventListener('securitypolicyviolation', reportPolicyViolation);
      win.addEventListener('pagehide', onPageHide);
      btnMic.addEventListener('click', function () { toggle(localMic, btnMic, 'Mute', 'Unmute'); });
      btnCam.addEventListener('click', function () { toggle(localCam, btnCam, 'Camera off', 'Camera on'); });
      btnLeave.addEventListener('click', leave);
      btnRejoin.addEventListener('click', join);

      if (AgoraRTC && 'onAutoplayFailed' in AgoraRTC) {
        AgoraRTC.onAutoplayFailed = function () { showNotice('Click anywhere on this page to enable sound.'); };
      }
      if (!AgoraRTC || typeof AgoraRTC.createClient !== 'function') {
        showNotice('The video library failed to load.');
        setStatus('Not connected');
        return;
      }
      ticket = takeTicket();
      if (!ticket) {
        showNotice('This room link is missing its access ticket. Go back to the app and join again.');
        setStatus('Not connected');
        return;
      }
      await join();
    }

    return { start: start };
  }

  var root = typeof window !== 'undefined' ? window : null;
  if (root) {
    root.VideoRoom = { create: create };
    if (!root.__VIDEO_ROOM_MANUAL__) {
      create({ document: root.document, window: root, AgoraRTC: root.AgoraRTC, fetch: root.fetch }).start();
    }
  }
})();
