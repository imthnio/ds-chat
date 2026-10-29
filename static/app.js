/* ds-chat 聊天页。
   密钥只留在这个页面里：用它推导出房间号（发给服务器）和 AES-GCM 钥匙（只在本地加解密）。
   界面交互移植自 Lucian UX/UI Prompt #062 #036 #078（MIT License (c) 2026 Seokwoooo · https://lucianlabs.dev/uxui-prompt/）。 */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var rm = function () { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; };
  var MIN_KEY = 16;
  var enc = new TextEncoder(), dec = new TextDecoder();

  UI.themeButton($('theme'));

  /* ---------- 本地小存储（隐私模式下可能用不了，用不了就只放内存） ---------- */
  var mem = {};
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return mem[k] || null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { mem[k] = v; } },
    del: function (k) { try { localStorage.removeItem(k); } catch (e) {} delete mem[k]; }
  };

  /* ---------- 加密 ---------- */
  function hex(buf) { return Array.prototype.map.call(new Uint8Array(buf), function (b) { return (b < 16 ? '0' : '') + b.toString(16); }).join(''); }
  function b64(bytes) { var s = ''; for (var i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000)); return btoa(s); }
  function unb64(s) { var bin = atob(s), out = new Uint8Array(bin.length); for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out; }

  async function deriveRoom(key) {
    var base = await crypto.subtle.importKey('raw', enc.encode(key.normalize('NFC')), 'PBKDF2', false, ['deriveBits']);
    var bits = new Uint8Array(await crypto.subtle.deriveBits({ name: 'PBKDF2', salt: enc.encode('ds-chat/room/v1'), iterations: 310000, hash: 'SHA-256' }, base, 512));
    var aes = await crypto.subtle.importKey('raw', bits.slice(0, 32), 'AES-GCM', false, ['encrypt', 'decrypt']);
    var id = hex(await crypto.subtle.digest('SHA-256', bits.slice(32)));
    return { id: id, code: id.slice(0, 6).toUpperCase(), aes: aes };
  }
  async function seal(obj) {
    var iv = crypto.getRandomValues(new Uint8Array(12));
    var ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: iv, additionalData: enc.encode(room.id) }, room.aes, enc.encode(JSON.stringify(obj))));
    var all = new Uint8Array(12 + ct.length); all.set(iv); all.set(ct, 12);
    return b64(all);
  }
  async function open(data) {
    try {
      var all = unb64(data);
      var pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: all.slice(0, 12), additionalData: enc.encode(room.id) }, room.aes, all.slice(12));
      var obj = JSON.parse(dec.decode(pt));
      return obj && typeof obj.x === 'string' ? obj : null;
    } catch (e) { return null; }
  }
  function randomId(n) {
    var a = crypto.getRandomValues(new Uint8Array(n)), cs = 'abcdefghijkmnpqrstuvwxyz23456789', s = '';
    for (var i = 0; i < n; i++) s += cs[a[i] % cs.length];
    return s;
  }

  /* ---------- 密钥输入框：#036 强度条 + #062 卡片反馈 ---------- */
  var card = $('join-card'), form = $('join-form'), keyIn = $('key'), meter = $('key-meter'), field = $('key-field');
  var err = $('key-err'), go = $('join-go'), caps = $('caps');
  var eye = meter.querySelector('.u036-eye'), segs = meter.querySelectorAll('.u036-meter i'), word = meter.querySelector('.u036-word');
  var rules = Array.prototype.slice.call(meter.querySelectorAll('.u036-r'));
  var TEST = {
    len: function (v) { return v.length >= MIN_KEY; },
    abc: function (v) { return /[A-Za-zÀ-ɏ一-鿿]/.test(v); },
    num: function (v) { return /\d/.test(v); },
    sym: function (v) { return /[^A-Za-z0-9À-ɏ一-鿿\s]/.test(v); }
  };
  var LV = [['输入密钥', 'var(--ux-line-2)'], ['弱', '#E5484D'], ['一般', '#F07A3E'], ['较强', '#E8B417'], ['很强', 'var(--ux-mint)']];
  function meterUpdate() {
    var v = keyIn.value.trim(), n = 0;
    rules.forEach(function (r) { var ok = TEST[r.dataset.r](v); r.classList.toggle('ok', ok); if (ok) n++; });
    var lv = v ? Math.max(1, n) : 0;
    if (lv && !TEST.len(v)) lv = Math.min(lv, 1);  // 不够长就算其他都满足也是“弱”
    meter.style.setProperty('--lv', LV[lv][1]);
    Array.prototype.forEach.call(segs, function (s, i) { s.classList.toggle('on', i < lv); });
    word.textContent = LV[lv][0];
  }
  function setShow(show) {
    keyIn.type = show ? 'text' : 'password';
    meter.classList.toggle('is-show', show);
    eye.setAttribute('aria-pressed', String(show));
    eye.setAttribute('aria-label', show ? '隐藏密钥' : '显示密钥');
  }
  function clearBad() { field.classList.remove('bad'); meter.classList.remove('bad'); keyIn.removeAttribute('aria-invalid'); }
  function fail(text) {
    err.textContent = text;
    field.classList.add('bad'); meter.classList.add('bad');
    keyIn.setAttribute('aria-invalid', 'true');
    card.classList.remove('shake'); void card.offsetWidth; card.classList.add('shake');
  }
  card.addEventListener('animationend', function (e) { if (e.target === card) card.classList.remove('shake'); });
  keyIn.addEventListener('input', function () { clearBad(); meterUpdate(); });
  keyIn.addEventListener('keyup', function (e) { if (e.getModifierState) caps.classList.toggle('on', e.getModifierState('CapsLock')); });
  eye.addEventListener('click', function () { setShow(keyIn.type === 'password'); });

  $('gen').addEventListener('click', function () {
    var cs = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789', v;
    do {
      var a = crypto.getRandomValues(new Uint8Array(28)), s = '';
      for (var i = 0; i < a.length; i++) { s += cs[a[i] % cs.length]; if (i % 7 === 6 && i < a.length - 1) s += '-'; }
      v = s;
    } while (!/\d/.test(v) || !/[A-Za-z]/.test(v));
    keyIn.value = v;
    setShow(true); clearBad(); meterUpdate();
    keyIn.focus({ preventScroll: true });
  });
  $('copy').addEventListener('click', function () {
    var btn = this, label = btn.querySelector('span'), v = keyIn.value.trim();
    if (!v) { fail('先输入或生成一段密钥'); return; }
    var ok = function () { btn.classList.add('done'); label.textContent = '已复制'; setTimeout(function () { btn.classList.remove('done'); label.textContent = '复制密钥'; }, 1600); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(v).then(ok, legacy); else legacy();
    function legacy() {
      var was = keyIn.type; keyIn.type = 'text'; keyIn.select();
      try { document.execCommand('copy'); ok(); } catch (e) { UI.toast('err', '复制失败', '请手动选中密钥复制'); }
      keyIn.type = was; keyIn.setSelectionRange(v.length, v.length);
    }
  });

  var joining = false;
  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    if (joining) return;
    var key = keyIn.value.trim();
    if (key.length < MIN_KEY) { fail(key ? '密钥太短：至少 ' + MIN_KEY + ' 个字符，现在 ' + key.length + ' 个' : '请输入密钥'); keyIn.focus(); return; }
    if (!window.crypto || !crypto.subtle) { fail('这个网址不能加密：请用 https:// 打开（本机测试可用 localhost）'); return; }
    joining = true;
    go.classList.add('busy'); go.setAttribute('aria-label', '正在进入');
    try {
      var r = await deriveRoom(key);
      await enterRoom(r);
      go.classList.remove('busy'); go.classList.add('ok'); go.setAttribute('aria-label', '已进入');
      await new Promise(function (res) { setTimeout(res, rm() ? 0 : 550); });
      keyIn.value = ''; setShow(false); meterUpdate();
      showChat();
    } catch (ex) {
      go.classList.remove('busy');
      leaveQuiet();
      fail(ex && ex.message ? ex.message : '进不去房间，请稍后再试');
    } finally {
      go.classList.remove('ok'); go.removeAttribute('aria-label');
      joining = false;
    }
  });

  /* ---------- 房间连接 ---------- */
  var room = null, ws = null, me = null, info = { creator: false, admin: false, destroyAt: null, own: null, policyAt: null, online: 0, limit: 7 };
  var skew = 0, retry = 0, retryT = 0, leaving = false, seen = {}, lastDay = '', firstJoin = null;

  function wsUrl() { return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws'; }

  function enterRoom(r) {
    room = r; leaving = false; retry = 0; seen = {}; lastDay = '';
    me = store.get('dschat:me:' + r.id);
    if (!me) { me = randomId(12); }
    return new Promise(function (resolve, reject) { firstJoin = { resolve: resolve, reject: reject }; connect(); });
  }

  function connect() {
    clearTimeout(retryT);
    var sock = ws = new WebSocket(wsUrl());
    var timeout = setTimeout(function () { if (sock.readyState !== 1) sock.close(); }, 10000);
    sock.onopen = function () {
      clearTimeout(timeout);
      sock.send(JSON.stringify({ t: 'join', room: room.id, token: store.get('dschat:tok:' + room.id) || undefined, create: !!firstJoin }));
    };
    sock.onmessage = function (ev) {
      var m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (sock !== ws) return;
      handle(m);
    };
    sock.onclose = function (ev) {
      clearTimeout(timeout);
      if (sock !== ws) return;
      ws = null;
      if (firstJoin) { var f = firstJoin; firstJoin = null; f.reject(new Error(ev.code === 1008 || ev.code === 403 ? '连接被服务器拒绝' : '连不上服务器，请检查网络后再试')); return; }
      if (leaving || !room) return;
      setPresence();
      retry = Math.min(retry + 1, 6);
      retryT = setTimeout(connect, Math.min(10000, 600 * Math.pow(2, retry - 1)));
    };
  }

  async function handle(m) {
    if (m.t === 'joined') {
      retry = 0;
      skew = m.now - Date.now() / 1000;
      info.creator = m.creator; info.admin = m.admin; info.destroyAt = m.destroyAt; info.own = m.own; info.policyAt = m.policyAt; info.online = m.online; info.limit = m.limit;
      if (m.token) store.set('dschat:tok:' + room.id, m.token);
      var wasHere = store.get('dschat:seen:' + room.id);
      store.set('dschat:me:' + room.id, me);
      store.set('dschat:seen:' + room.id, '1');
      var msgs = await Promise.all(m.history.map(function (h) { return open(h.data).then(function (o) { return { h: h, o: o }; }); }));
      if (!room) return;
      var fresh = !Object.keys(seen).length;
      if (fresh) { $('log').innerHTML = ''; lastDay = ''; }
      msgs.forEach(function (x) { addMessage(x.h, x.o, false); });
      if (fresh) {
        if (m.created && wasHere) note('这个房间之前已被销毁，现在是一个新的空房间。');
        else if (m.created) note('房间已创建，你是创建者。把同一段密钥告诉对方，对方输入后就能进来。');
        else if (!m.history.length) note('已进入房间。还没有消息。');
      }
      scrollDown(true);
      renderRole(); setPresence(); renderTimer(); renderLimit();
      if (firstJoin) { var f = firstJoin; firstJoin = null; f.resolve(); }
    } else if (m.t === 'error') {
      if (firstJoin) { var f2 = firstJoin; firstJoin = null; leaving = true; if (ws) ws.close(); f2.reject(new Error(m.error)); return; }
      UI.toast('err', m.error);
    } else if (m.t === 'msg') {
      var o = await open(m.data);
      if (!room) return;
      hideTyping(o && o.s !== me);
      addMessage(m, o, true);
    } else if (m.t === 'full') {
      var full = '房间已满：最多 ' + m.limit + ' 人，等有人退出后再试';
      if (firstJoin) { var f3 = firstJoin; firstJoin = null; leaving = true; if (ws) ws.close(); f3.reject(new Error(full)); return; }
      leaveQuiet(); showJoin();
      UI.toast('err', '重连失败', full, 7000);
    } else if (m.t === 'limit') {
      info.limit = m.value; renderLimit(); setPresence();
      UI.toast('info', (m.by === 'admin' ? '管理员' : '创建者') + '把人数上限改为 ' + m.value + ' 人');
    } else if (m.t === 'presence') {
      var before = info.online;
      info.online = m.online; setPresence();
      if (before && m.online > before) UI.toast('ok', '有人进入了房间', '现在 ' + m.online + ' 人在线', 3000);
    } else if (m.t === 'typing') {
      showTyping();
    } else if (m.t === 'timer') {
      info.destroyAt = m.destroyAt; info.own = m.own; info.policyAt = m.policyAt; renderTimer();
      var left = m.destroyAt ? '房间将在 ' + UI.fmtLeft(m.destroyAt - now()) + ' 后销毁' : '';
      if (m.by === 'policy') {
        if (m.policyAt) UI.toast('warn', '管理员开启了全局自动销毁', left);
        else UI.toast('info', '管理员关闭了全局自动销毁', left);
      } else {
        var who = m.by === 'admin' ? '管理员' : '创建者';
        if (m.own) UI.toast('warn', who + '设置了销毁倒计时', left);
        else UI.toast('info', who + '取消了销毁倒计时', left);
      }
    } else if (m.t === 'destroyed' || m.t === 'gone') {
      var why = m.t === 'gone' ? '断线期间房间被销毁了' : { creator: '创建者销毁了房间', admin: '管理员销毁了房间', timer: '倒计时结束，房间已自动销毁', idle: '房间太久没人使用，已清理' }[m.by] || '';
      forget(room.id);
      leaveQuiet();
      showJoin();
      UI.toast('err', '房间已销毁', why, 7000);
    }
  }

  function now() { return Date.now() / 1000 + skew; }
  function forget(id) { ['tok', 'me', 'seen'].forEach(function (k) { store.del('dschat:' + k + ':' + id); }); }
  function leaveQuiet() {
    leaving = true; clearTimeout(retryT);
    if (ws) { var s = ws; ws = null; try { s.close(); } catch (e) {} }
    room = null; seen = {};
    closeSheet(); clearReply(); hideTyping(true); dropFiles();
  }
  function send(obj) {
    if (!ws || ws.readyState !== 1) return false;
    ws.send(JSON.stringify(obj));
    return true;
  }

  /* ---------- 页面切换 ---------- */
  function showChat() {
    $('view-join').hidden = true; $('view-chat').hidden = false;
    document.body.classList.add('in-chat');
    $('room-code').textContent = room.code; $('sheet-code').textContent = room.code;
    document.title = '房间 ' + room.code + ' · ds-chat';
    scrollDown(true);
    $('msg').focus({ preventScroll: true });
  }
  function showJoin() {
    $('view-chat').hidden = true; $('view-join').hidden = false;
    document.body.classList.remove('in-chat');
    document.title = 'ds-chat 密钥聊天室';
    $('log').innerHTML = ''; $('msg').value = ''; sendState();
    info.destroyAt = info.own = info.policyAt = null; renderTimer();
    keyIn.focus({ preventScroll: true });
  }

  /* ---------- 状态 ---------- */
  function setPresence() {
    var p = $('presence');
    p.className = '';
    if (!ws || ws.readyState !== 1) { p.textContent = '连接断开，正在重连…'; p.className = 'wait'; return; }
    if (info.online > 2) { p.textContent = info.online + ' 人在线 · 上限 ' + info.limit + ' 人'; p.className = 'on'; }
    else if (info.online === 2) { p.textContent = '对方在线' + (info.limit > 2 ? ' · 上限 ' + info.limit + ' 人' : ''); p.className = 'on'; }
    else { p.textContent = '等待对方加入'; p.className = 'off'; }
    $('limit-online').textContent = '现在 ' + info.online + ' 人在线';
  }
  function renderLimit() {
    limit.set(info.limit);
    $('limit-now').textContent = '最多 ' + info.limit + ' 人';
    $('sheet-limit').textContent = '最多 ' + info.limit + ' 人';
  }
  function renderRole() {
    var can = info.creator || info.admin;
    $('manage').hidden = !can; $('no-manage').hidden = can;
    $('sheet-role').textContent = info.creator ? '你是创建者' : info.admin ? '你是管理员' : '你是成员';
  }
  var tick = 0;
  function renderTimer() {
    clearInterval(tick);
    var on = !!info.destroyAt, byPolicy = on && info.policyAt && info.destroyAt === info.policyAt;
    $('timer-strip').classList.toggle('on', on);
    $('timer-strip-why').textContent = byPolicy ? '（管理员设置的全局自动销毁）' : '';
    $('timer-now').hidden = !on;
    $('timer-cancel').hidden = !info.own;  // 全局自动销毁取消不了
    $('policy-note').hidden = !info.policyAt;
    $('timer-set').textContent = (info.own ? '改为 ' : '') + hours.value + ' 小时后销毁';
    if (!on) return;
    var draw = function () {
      var left = info.destroyAt - now();
      var text = left > 0 ? UI.fmtLeft(left) : '片刻';
      $('timer-left').textContent = text; $('timer-now-left').textContent = text;
      if (info.policyAt) $('policy-note').textContent = '管理员开启了全局自动销毁：这个房间最晚 ' + UI.fmtLeft(Math.max(0, info.policyAt - now())) + ' 后销毁，这里取消不了。';
    };
    draw(); tick = setInterval(draw, 1000);
  }

  /* ---------- 聊天记录：#078 ---------- */
  var log = $('log'), reply = $('reply'), rb = reply.querySelector('b'), rt = reply.querySelector('em'), rx = reply.querySelector('.u078-rx');
  var composer = $('composer'), input = $('msg'), sendBtn = $('send');
  var target = null;
  var CHECK = '<svg class="u078-ck" viewBox="0 0 16 11" aria-label="已发送"><path d="M1.5 6l3.5 3.5L14.5 1"/></svg>';
  var lastWho = null, lastTs = 0;
  var ARROW = '<svg viewBox="0 0 14 14"><path d="M5.5 3.5L2 7l3.5 3.5M2 7h6.5a3.5 3.5 0 0 1 3.5 3.5"/></svg>';

  function nearBottom() { return log.scrollHeight - log.scrollTop - log.clientHeight < 90; }
  function scrollDown(force) { if (force || nearBottom()) log.scrollTop = log.scrollHeight; }
  function note(text) {
    var n = document.createElement('p'); n.className = 'u078-note'; n.textContent = text;
    log.appendChild(n);
  }
  function addMessage(h, o, animate) {
    if (seen[h.id]) return;
    seen[h.id] = 1;
    var day = UI.fmtDay(h.ts);
    if (day !== lastDay) { lastDay = day; lastWho = null; var d = document.createElement('p'); d.className = 'u078-day'; d.textContent = day; log.appendChild(d); }
    var mine = !!(o && o.s === me), stick = mine || nearBottom();
    var text = o ? o.x : '无法解密这条消息';
    var f = o && fileMeta(o.f);
    // 同一个人 5 分钟内连着发的算一组，只有第一条带气泡尾巴
    var who = o ? o.s : '?', head = who !== lastWho || h.ts - lastTs > 300;
    lastWho = who; lastTs = h.ts;
    var m = document.createElement('div');
    m.className = 'u078-m' + (animate ? ' in' : '') + (mine ? ' me' : '') + (o ? '' : ' fail') + (head ? ' tail' : '');
    m.innerHTML = '<span class="u078-rep" aria-hidden="true">' + ARROW + '</span><span class="u078-bub">' +
      (o && o.r ? '<span class="u078-q"><b>' + (o.rw === 'me' ? (mine ? '你' : '对方') : (mine ? '对方' : '你')) + '</b>' + UI.esc(o.r) + '</span>' : '') +
      (f ? attHTML(f) : '') + UI.esc(text) +
      '<span class="u078-t"><time>' + UI.fmtClock(h.ts) + '</time>' + (mine ? CHECK : '') + '</span></span>' +
      (mine || !o ? '' : '<button class="u078-rb" type="button" aria-label="回复这条消息">' + ARROW + '</button>');
    m.setAttribute('data-t', f ? fileLabel(f) + (text ? ' ' + text : '') : text); m.setAttribute('data-w', mine ? '自己' : '对方');
    if (f) {
      m.classList.add('has-att');
      if (uploads[f.id]) { uploads[f.id].el.remove(); delete uploads[f.id]; }  // 自己正在上传的那个占位气泡换成正式消息
    }
    var ty = log.querySelector('.u078-ty');
    log.insertBefore(m, ty || null);
    m.addEventListener('animationend', function () { m.classList.remove('in'); });
    if (!mine && o) wire(m);
    if (f) hydrate(m, f);
    if (stick) scrollDown(true);
  }
  function setReply(m) {
    target = m;
    rb.textContent = '回复' + m.getAttribute('data-w');
    rt.textContent = m.getAttribute('data-t');
    reply.classList.add('on'); rx.tabIndex = 0;
    input.focus({ preventScroll: true });
  }
  function clearReply() { target = null; reply.classList.remove('on'); rx.tabIndex = -1; }
  function wire(m) {
    var bub = m.querySelector('.u078-bub'), rep = m.querySelector('.u078-rep'), sx = 0, sy = 0, dx = 0, down = false, sideways = false;
    bub.addEventListener('pointerdown', function (e) { if (e.button !== 0 || e.pointerType === 'mouse') return; down = true; sideways = false; sx = e.clientX; sy = e.clientY; dx = 0; });
    bub.addEventListener('pointermove', function (e) {
      if (!down) return;
      if (!sideways) {
        if (Math.abs(e.clientY - sy) > 10) { down = false; return; }
        if (e.clientX - sx < 8) return;
        sideways = true; bub.setPointerCapture(e.pointerId); m.classList.add('drag');
      }
      dx = Math.max(0, e.clientX - sx); var d = dx > 60 ? 60 + (dx - 60) * 0.3 : dx;
      bub.style.setProperty('--dx', d + 'px'); m.style.setProperty('--ro', Math.min(1, dx / 60).toFixed(2));
      rep.classList.toggle('full', dx >= 60);
    });
    var end = function () { if (!down) return; down = false; if (!sideways) return; m.classList.remove('drag'); bub.style.setProperty('--dx', '0px'); m.style.setProperty('--ro', 0); rep.classList.remove('full'); if (dx >= 60) setReply(m); };
    bub.addEventListener('pointerup', end); bub.addEventListener('pointercancel', end);
    m.querySelector('.u078-rb').addEventListener('click', function () { setReply(m); });
  }

  var typingT = 0;
  function showTyping() {
    var ty = log.querySelector('.u078-ty');
    if (!ty) {
      var stick = nearBottom();
      ty = document.createElement('div'); ty.className = 'u078-ty u078-m in'; ty.setAttribute('aria-label', '对方正在输入'); ty.innerHTML = '<i></i><i></i><i></i>';
      log.appendChild(ty);
      if (stick) scrollDown(true);
    }
    clearTimeout(typingT); typingT = setTimeout(function () { hideTyping(true); }, 4000);
  }
  function hideTyping(yes) { if (!yes) return; clearTimeout(typingT); var ty = log.querySelector('.u078-ty'); if (ty) ty.remove(); }

  /* ---------- 图片、视频、文件 ----------
     文件在浏览器里切成 1 MB 一块，每块用房间钥匙单独加密（AES-GCM，附带文件号和块号防止被调换），
     一块一块传上去；服务器只存密文。下载时按块取回、解密、拼起来。不限制文件大小。 */
  var CHUNK = 1024 * 1024, OVER = 28, AUTO = 20 * 1024 * 1024;
  var blobs = {}, loading = {}, uploads = {}, upQueue = Promise.resolve(), autoQueue = [], autoBusy = 0;
  var ICON = {
    file: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
    vid: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l10.5-6.5z"/></svg>',
    aud: '<svg viewBox="0 0 24 24"><path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></svg>',
    down: '<svg viewBox="0 0 24 24"><path d="M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 20h14"/></svg>',
    x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>'
  };
  function fileMeta(f) {
    if (!f || typeof f.id !== 'string' || !/^[0-9a-f]{32}$/.test(f.id) || typeof f.z !== 'number' || f.z < 0) return null;
    return { id: f.id, n: String(f.n || '未命名').slice(0, 200), t: String(f.t || ''), z: f.z };
  }
  function kindOf(f) {
    var t = f.t;
    return /^image\/(png|jpe?g|gif|webp|avif|bmp)$/.test(t) ? 'img' : t.indexOf('video/') === 0 ? 'vid' : t.indexOf('audio/') === 0 ? 'aud' : 'file';
  }
  function fileLabel(f) { return { img: '[图片] ', vid: '[视频] ', aud: '[音频] ', file: '[文件] ' }[kindOf(f)] + f.n; }
  function aadFor(rid, fid, i) { return enc.encode(rid + ':' + fid + ':' + i); }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function attHTML(f) {
    var k = kindOf(f);
    if (k === 'img') return '<span class="att img"><span class="att-ph"><b class="att-pct"></b></span></span>';
    return '<span class="att card ' + k + '"><span class="att-ic">' + ICON[k === 'file' ? 'file' : k] + '</span>' +
      '<span class="att-tx"><b>' + UI.esc(f.n) + '</b><small>' + UI.fmtBytes(f.z) + '</small></span>' +
      '<button class="att-go" type="button" aria-label="' + (k === 'file' ? '下载' : '播放') + ' ' + UI.esc(f.n) + '">' + (k === 'file' ? ICON.down : ICON.vid) + '</button>' +
      '<i class="att-bar" aria-hidden="true"></i></span>';
  }
  function setBar(box, p) {
    var bar = box.querySelector('.att-bar'), pct = box.querySelector('.att-pct');
    if (bar) { bar.style.width = (p * 100).toFixed(1) + '%'; bar.classList.toggle('on', p > 0 && p < 1); }
    if (pct) pct.textContent = p > 0 && p < 1 ? Math.round(p * 100) + '%' : '';
  }
  function hydrate(m, f) {
    var k = kindOf(f), box = m.querySelector('.att');
    if (k === 'img') {
      var show = function (url) {
        var img = document.createElement('img');
        img.src = url; img.alt = f.n; img.loading = 'lazy';
        img.addEventListener('load', function () { if (nearBottom()) scrollDown(true); });
        img.addEventListener('click', function () { lightbox(url, f); });
        box.innerHTML = ''; box.appendChild(img);
      };
      var fail = function (e) { box.querySelector('.att-ph').innerHTML = '<b>' + UI.esc(e.message || '加载失败') + '</b>'; };
      if (blobs[f.id]) { show(blobs[f.id]); return; }
      if (f.z <= AUTO) { queueAuto(function () { return download(f, function (p) { setBar(box, p); }).then(show, fail); }); return; }
      var ph = box.querySelector('.att-ph');
      ph.innerHTML = '<button class="att-load" type="button">点击加载图片（' + UI.fmtBytes(f.z) + '）</button>';
      ph.querySelector('button').addEventListener('click', function () {
        ph.innerHTML = '<b class="att-pct"></b>';
        download(f, function (p) { setBar(box, p); }).then(show, fail);
      });
      return;
    }
    var go = box.querySelector('.att-go');
    go.addEventListener('click', function () {
      if (go.disabled) return;
      go.disabled = true;
      download(f, function (p) { setBar(box, p); }).then(function (url) {
        go.disabled = false; setBar(box, 1);
        if (k === 'file') { saveAs(url, f.n); return; }
        var media = document.createElement(k === 'vid' ? 'video' : 'audio');
        media.controls = true; media.src = url; media.className = 'att-media'; media.preload = 'metadata';
        var save = document.createElement('button');
        save.type = 'button'; save.className = 'att-save'; save.innerHTML = ICON.down + '<span>保存' + (k === 'vid' ? '视频' : '音频') + '</span>';
        save.addEventListener('click', function () { saveAs(url, f.n); });
        box.replaceWith(media); media.after(save);
        media.play().catch(function () {});
      }, function (e) { go.disabled = false; UI.toast('err', '下载失败', e.message); });
    });
  }
  function queueAuto(job) { autoQueue.push(job); pumpAuto(); }
  function pumpAuto() {
    while (autoBusy < 2 && autoQueue.length) {
      autoBusy++;
      autoQueue.shift()().catch(function () {}).then(function () { autoBusy--; pumpAuto(); });
    }
  }
  function saveAs(url, name) {
    var a = document.createElement('a');
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  }
  function lightbox(url, f) {
    var lb = document.createElement('div');
    lb.className = 'lightbox'; lb.setAttribute('role', 'dialog'); lb.setAttribute('aria-label', f.n);
    lb.innerHTML = '<div class="lb-bar"><span>' + UI.esc(f.n) + ' · ' + UI.fmtBytes(f.z) + '</span><button type="button" class="lb-save">' + ICON.down + '保存</button><button type="button" class="lb-x" aria-label="关闭">' + ICON.x + '</button></div><img alt="">';
    lb.querySelector('img').src = url;
    var close = function () { lb.remove(); document.removeEventListener('keydown', onKey); };
    var onKey = function (e) { if (e.key === 'Escape') close(); };
    lb.addEventListener('click', function (e) { if (e.target === lb || e.target.tagName === 'IMG' || e.target.closest('.lb-x')) close(); });
    lb.querySelector('.lb-save').addEventListener('click', function (e) { e.stopPropagation(); saveAs(url, f.n); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(lb);
    lb.querySelector('.lb-x').focus();
  }

  function download(f, onProgress) {
    if (blobs[f.id]) return Promise.resolve(blobs[f.id]);
    var job = loading[f.id];
    if (job) { job.listeners.push(onProgress); return job.p; }
    var rid = room.id, key = room.aes, n = Math.max(1, Math.ceil(f.z / CHUNK)), parts = new Array(n), done = 0, next = 0;
    job = loading[f.id] = { listeners: [onProgress] };
    async function worker() {
      while (next < n) {
        var i = next++, start = i * (CHUNK + OVER), len = Math.min(CHUNK, f.z - i * CHUNK) + OVER, buf = null;
        for (var t = 0; t < 4 && !buf; t++) {
          try {
            var r = await fetch('/api/files/' + rid + '/' + f.id, { headers: { Range: 'bytes=' + start + '-' + (start + len - 1) } });
            if (r.status === 404) throw Object.assign(new Error('文件已被清理'), { fatal: true });
            if (!r.ok) throw new Error('下载失败（' + r.status + '）');
            buf = new Uint8Array(await r.arrayBuffer());
          } catch (e) { if (e.fatal || t === 3) throw e; await sleep(800 * (t + 1)); }
        }
        try {
          parts[i] = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: buf.slice(0, 12), additionalData: aadFor(rid, f.id, i) }, key, buf.slice(12)));
        } catch (e) { throw new Error('解密失败'); }
        done++;
        job.listeners.forEach(function (cb) { if (cb) cb(done / n); });
      }
    }
    job.p = Promise.all([worker(), worker(), worker()]).then(function () {
      var url = URL.createObjectURL(new Blob(parts, { type: f.t || 'application/octet-stream' }));
      if (room && room.id === rid) blobs[f.id] = url;
      delete loading[f.id];
      return url;
    }, function (e) { delete loading[f.id]; throw e; });
    return job.p;
  }

  function pending(file) {
    var el = document.createElement('div');
    el.className = 'u078-m me tail in has-att uploading';
    el.innerHTML = '<span class="u078-bub"><span class="att card file"><span class="att-ic">' + ICON.file + '</span>' +
      '<span class="att-tx"><b></b><small>' + UI.fmtBytes(file.size) + ' · 等待上传</small></span>' +
      '<button class="att-go" type="button" aria-label="取消上传">' + ICON.x + '</button><i class="att-bar on" aria-hidden="true"></i></span></span>';
    el.querySelector('b').textContent = file.name || '未命名';
    var small = el.querySelector('small'), box = el.querySelector('.att');
    var up = { el: el, cancelled: false };
    up.status = function (t) { small.textContent = UI.fmtBytes(file.size) + ' · ' + t; };
    up.progress = function (p) { setBar(box, p); up.status('上传中 ' + Math.round(p * 100) + '%'); };
    up.fail = function (msg) {
      el.classList.add('failed'); up.status(msg || '上传失败');
      var b = el.querySelector('.att-go'); b.setAttribute('aria-label', '移除'); b.onclick = function () { el.remove(); };
    };
    el.querySelector('.att-go').onclick = function () { up.cancelled = true; el.remove(); };
    var ty = log.querySelector('.u078-ty');
    log.insertBefore(el, ty || null);
    scrollDown(true);
    return up;
  }
  async function putChunk(base, offset, body) {
    for (var t = 0; ; t++) {
      var r;
      try { r = await fetch(base + '?offset=' + offset, { method: 'PUT', body: body }); }
      catch (e) { if (t >= 5) throw new Error('网络断了，上传失败'); await sleep(1000 * (t + 1)); continue; }
      if (r.ok) return (await r.json()).size;
      if (r.status === 409) {
        var s = -1; try { s = (await r.json()).size; } catch (e) {}
        if (s === offset + body.length) return s;  // 这一块其实已经传上去了，只是回应丢了
      }
      throw new Error((await r.text().catch(function () { return ''; })) || '上传失败（' + r.status + '）');
    }
  }
  async function uploadOne(file, up, owner) {
    if (up.cancelled || room !== owner) return;  // 已经离开了发文件时所在的房间
    var rid = owner.id, key = owner.aes, fid = null;
    try {
      var r = await fetch('/api/files/' + rid, { method: 'POST' });
      if (!r.ok) throw new Error(await r.text() || '上传失败');
      fid = (await r.json()).id;
      var base = '/api/files/' + rid + '/' + fid, n = Math.max(1, Math.ceil(file.size / CHUNK)), offset = 0;
      up.progress(0);
      for (var i = 0; i < n; i++) {
        if (up.cancelled || room !== owner) { fetch(base + '/cancel', { method: 'POST' }).catch(function () {}); return; }
        var plain = await file.slice(i * CHUNK, Math.min(file.size, (i + 1) * CHUNK)).arrayBuffer();
        var iv = crypto.getRandomValues(new Uint8Array(12));
        var ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: iv, additionalData: aadFor(rid, fid, i) }, key, plain));
        var body = new Uint8Array(12 + ct.length); body.set(iv); body.set(ct, 12);
        offset = await putChunk(base, offset, body);
        up.progress((i + 1) / n);
      }
      r = await fetch(base + '/done', { method: 'POST' });
      if (!r.ok) throw new Error('上传失败');
      if (up.cancelled || room !== owner) return;
      blobs[fid] = URL.createObjectURL(file);  // 自己发的不用再下载一遍
      uploads[fid] = up;
      up.status('发送中');
      var data = await seal({ s: me, x: '', f: { id: fid, n: file.name || '未命名', t: file.type || 'application/octet-stream', z: file.size } });
      if (!send({ t: 'msg', data: data, file: fid })) throw new Error('还没连上，没发出去');
    } catch (e) {
      if (fid) delete uploads[fid];
      up.fail(e.message);
    }
  }
  function sendFiles(list) {
    if (!room || !ws || ws.readyState !== 1) { UI.toast('err', '还没连上，文件没发出去'); return; }
    var owner = room;
    Array.prototype.forEach.call(list, function (file) {
      var up = pending(file);
      upQueue = upQueue.then(function () { return uploadOne(file, up, owner); });  // 一个一个传，不抢网速也不占内存
    });
  }
  function dropFiles() {
    Object.keys(uploads).forEach(function (k) { uploads[k].cancelled = true; });
    log.querySelectorAll('.uploading').forEach(function (el) { el.remove(); });
    Object.keys(blobs).forEach(function (k) { URL.revokeObjectURL(blobs[k]); });
    blobs = {}; uploads = {}; autoQueue = [];
    upQueue = Promise.resolve();
    document.querySelectorAll('.lightbox').forEach(function (el) { el.remove(); });
  }
  $('attach').addEventListener('click', function () { $('file-in').click(); });
  $('file-in').addEventListener('change', function () { if (this.files.length) sendFiles(this.files); this.value = ''; });
  input.addEventListener('paste', function (e) {
    var files = e.clipboardData && e.clipboardData.files;
    if (files && files.length) { e.preventDefault(); sendFiles(files); }
  });
  var dragDepth = 0, chatEl = $('chat');
  chatEl.addEventListener('dragenter', function (e) { if (e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, 'Files') >= 0) { dragDepth++; chatEl.classList.add('dropping'); } });
  chatEl.addEventListener('dragleave', function () { if (--dragDepth <= 0) { dragDepth = 0; chatEl.classList.remove('dropping'); } });
  chatEl.addEventListener('dragover', function (e) { if (chatEl.classList.contains('dropping')) e.preventDefault(); });
  chatEl.addEventListener('drop', function (e) {
    dragDepth = 0; chatEl.classList.remove('dropping');
    if (e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); sendFiles(e.dataTransfer.files); }
  });

  function sendState() {
    var on = !!input.value.trim();
    sendBtn.classList.toggle('go', on);
  }
  var lastTyping = 0;
  input.addEventListener('input', function () {
    sendState();
    if (input.value.trim() && Date.now() - lastTyping > 2500) { lastTyping = Date.now(); send({ t: 'typing' }); }
  });
  rx.addEventListener('click', function () { clearReply(); input.focus(); });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && target) { e.preventDefault(); clearReply(); }
  });
  composer.addEventListener('submit', async function (e) {
    e.preventDefault();
    var v = input.value.trim();
    if (!v) { if (!rm()) sendBtn.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.15)' }, { transform: 'scale(1)' }], { duration: 300 }); return; }
    if (!ws || ws.readyState !== 1) { UI.toast('err', '还没连上，消息没发出去', '连上后再发一次'); return; }
    var payload = { s: me, x: v };
    if (target) { payload.r = target.getAttribute('data-t').slice(0, 140); payload.rw = target.classList.contains('me') ? 'me' : 'them'; }
    var data = await seal(payload);
    if (!send({ t: 'msg', data: data })) { UI.toast('err', '还没连上，消息没发出去'); return; }
    input.value = ''; sendState(); lastTyping = 0;
    clearReply();
  });

  /* ---------- 房间设置面板 ---------- */
  var chat = $('chat'), sheet = $('sheet');
  var hours = UI.hourSlider($('hours'), function (v) { if ($('timer-set')) $('timer-set').textContent = (info.own ? '改为 ' : '') + v + ' 小时后销毁'; });
  var ask = UI.destroyConfirm(chat);
  var limitT = 0;
  var limit = UI.stepper($('limit'), 2, 7, function (v) {
    $('limit-now').textContent = '最多 ' + v + ' 人';
    clearTimeout(limitT);
    limitT = setTimeout(function () { if (v !== info.limit && !send({ t: 'limit', value: v })) UI.toast('err', '还没连上，上限没改'); }, 700);
  });
  sheet.inert = true;
  function openSheet() { chat.classList.add('sheet-on'); sheet.inert = false; setTimeout(function () { $('leave').focus({ preventScroll: true }); }, rm() ? 0 : 250); }
  function closeSheet() { if (!chat.classList.contains('sheet-on')) return; chat.classList.remove('sheet-on'); sheet.inert = true; $('open-sheet').focus({ preventScroll: true }); }
  $('open-sheet').addEventListener('click', openSheet);
  $('sheet-dim').addEventListener('click', closeSheet);
  chat.addEventListener('keydown', function (e) { if (e.key === 'Escape' && chat.classList.contains('sheet-on')) closeSheet(); });

  $('timer-set').addEventListener('click', function () {
    if (send({ t: 'timer', hours: hours.value })) closeSheet();
    else UI.toast('err', '还没连上，稍后再试');
  });
  $('timer-cancel').addEventListener('click', function () { if (!send({ t: 'timer', hours: null })) UI.toast('err', '还没连上，稍后再试'); });
  $('destroy-now').addEventListener('click', async function () {
    if (!room) return;
    if (await ask(room.code)) { if (!send({ t: 'timer', hours: 0 })) UI.toast('err', '还没连上，房间没有销毁'); }
  });
  $('leave').addEventListener('click', function () {
    leaveQuiet();
    showJoin();
    UI.toast('info', '已退出房间', '输入同一段密钥可以随时回来');
  });

  meterUpdate();
  keyIn.focus({ preventScroll: true });
})();
