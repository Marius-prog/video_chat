/*
 * Stand-in for the Agora Web SDK, used by the E2E run so the app can be exercised in a real browser without
 * Agora's network. It implements just the calls assets/js/room.js makes and exposes window.__agoraStub so a
 * test can fire SDK events (e.g. a remote user publishing).
 */
(function () {
  var handlers = {};
  function fire(event) {
    var args = Array.prototype.slice.call(arguments, 1);
    (handlers[event] || []).forEach(function (fn) { fn.apply(null, args); });
  }
  function track(kind) {
    return {
      kind: kind,
      muted: false,
      play: function (el) { el.textContent = kind + ' playing'; },
      setMuted: function (muted) { this.muted = muted; return Promise.resolve(); },
      stop: function () {},
      close: function () {}
    };
  }
  var client = {
    on: function (event, fn) { (handlers[event] = handlers[event] || []).push(fn); },
    join: function (appId, channel, token, uid) {
      client.joined = { appId: appId, channel: channel, token: token, uid: uid };
      client.uid = uid;
      fire('connection-state-change', 'CONNECTED');
      return Promise.resolve(uid);
    },
    publish: function (tracks) { client.published = tracks.map(function (t) { return t.kind; }); return Promise.resolve(); },
    subscribe: function () { return Promise.resolve(); },
    renewToken: function (token) { client.renewals = (client.renewals || 0) + 1; client.lastToken = token; return Promise.resolve(); },
    leave: function () { client.joined = null; client.left = true; return Promise.resolve(); }
  };
  window.AgoraRTC = {
    createClient: function (config) { client.config = config; return client; },
    createMicrophoneAudioTrack: function () { return Promise.resolve(track('audio')); },
    createCameraVideoTrack: function () { return Promise.resolve(track('video')); }
  };
  window.__agoraStub = { client: client, emit: fire };
})();
