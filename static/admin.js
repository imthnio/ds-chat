/* ds-chat 管理后台：登录、房间列表、立即销毁 / 倒计时销毁 / 取消倒计时。
   登录卡片的交互移植自 Lucian UX/UI Prompt #062（MIT License (c) 2026 Seokwoooo · https://lucianlabs.dev/uxui-prompt/#062）。 */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var rm = function () { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; };

  UI.themeButton($('theme'));

  async function api(path, body) {
    var r = await fetch(path, body === undefined ? { credentials: 'same-origin' } : {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    var data = null;
    try { data = await r.json(); } catch (e) {}
    if (r.status === 401 && path !== '/api/admin/login') { showLogin(); throw new Error('登录已过期，请重新登录'); }
    if (!r.ok && !(data && data.error)) throw new Error((await r.text().catch(function () { return ''; })) || '请求失败（' + r.status + '）');
    return { ok: r.ok, data: data || {} };
  }

  /* ---------- 登录：#062 ---------- */
  var card = $('login-card'), form = $('login-form'), pwF = $('pw-field'), pw = $('pw'), err = $('pw-err'), go = $('login-go');
  var eye = card.querySelector('.u062-eye'), caps = $('caps'), busy = false;
  eye.addEventListener('click', function () {
    var on = eye.getAttribute('aria-pressed') !== 'true';
    eye.setAttribute('aria-pressed', String(on));
    eye.setAttribute('aria-label', on ? '隐藏密码' : '显示密码');
    pw.type = on ? 'text' : 'password';
  });
  pw.addEventListener('keyup', function (e) { if (e.getModifierState) caps.classList.toggle('on', e.getModifierState('CapsLock')); });
  pw.addEventListener('input', function () { pwF.classList.remove('bad'); pw.removeAttribute('aria-invalid'); });
  card.addEventListener('animationend', function (e) { if (e.target === card) card.classList.remove('shake'); });
  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    if (busy) return;
    busy = true;
    go.classList.add('busy'); go.setAttribute('aria-label', '正在验证');
    var res;
    try { res = await api('/api/admin/login', { password: pw.value }); }
    catch (ex) { res = { ok: false, data: { error: ex.message } }; }
    go.classList.remove('busy');
    if (res.ok) {
      go.classList.add('ok'); go.setAttribute('aria-label', '已登录');
      setTimeout(function () { go.classList.remove('ok'); go.removeAttribute('aria-label'); busy = false; pw.value = ''; showRooms(); }, rm() ? 0 : 650);
      return;
    }
    busy = false;
    go.removeAttribute('aria-label');
    err.textContent = res.data.error || '登录失败';
    pwF.classList.add('bad');
    pw.setAttribute('aria-invalid', 'true');
    card.classList.remove('shake'); void card.offsetWidth; card.classList.add('shake');
    pw.select();
  });
  $('logout').addEventListener('click', async function () {
    try { await api('/api/admin/logout', {}); } catch (e) {}
    showLogin();
  });

  function showLogin() {
    clearInterval(poll);
    closeSheet();
    $('view-rooms').hidden = true; $('view-login').hidden = false; $('logout').hidden = true;
    pw.focus({ preventScroll: true });
  }
  function showRooms() {
    $('view-login').hidden = true; $('view-rooms').hidden = false; $('logout').hidden = false;
    load();
    clearInterval(poll); poll = setInterval(load, 10000);
  }

  /* ---------- 房间列表 ---------- */
  var poll = 0, rooms = [], skew = 0;
  function now() { return Date.now() / 1000 + skew; }
  async function load() {
    try {
      var res = await api('/api/admin/rooms');
      skew = res.data.now - Date.now() / 1000;
      rooms = res.data.rooms;
      render();
    } catch (e) {
      if (!$('view-rooms').hidden) UI.toast('err', '加载失败', e.message);
    }
  }
  function render() {
    var online = rooms.reduce(function (n, r) { return n + r.online; }, 0);
    var timed = rooms.filter(function (r) { return r.destroyAt; }).length;
    $('stats').innerHTML = '<span>房间 <b>' + rooms.length + '</b></span><span>在线 <b>' + online + '</b> 人</span><span>倒计时中 <b>' + timed + '</b></span>';
    var box = $('rooms');
    if (!rooms.length) { box.innerHTML = '<p class="rooms-empty">还没有房间</p>'; return; }
    box.innerHTML = rooms.map(function (r) {
      var t = now();
      return '<div class="room"><code>' + UI.esc(r.code) + '</code><div class="room-info">' +
        '<span class="' + (r.online ? 'live' : '') + '">在线 <b>' + r.online + ' / ' + r.limit + '</b></span>' +
        '<span>消息 <b>' + r.messages + '</b></span>' +
        '<span>创建 <b>' + UI.fmtAgo(t - r.created) + '</b></span>' +
        '<span>最近活动 <b>' + UI.fmtAgo(t - r.lastActive) + '</b></span>' +
        (r.destroyAt ? '<span class="warn">销毁倒计时 <b data-left="' + r.destroyAt + '">' + UI.fmtLeft(r.destroyAt - t) + '</b></span>' : '') +
        '</div><button class="btn soft" type="button" data-id="' + UI.esc(r.id) + '">管理</button></div>';
    }).join('');
  }
  $('rooms').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-id]');
    if (b) openSheet(b.getAttribute('data-id'));
  });
  $('refresh').addEventListener('click', load);
  setInterval(function () {
    document.querySelectorAll('[data-left]').forEach(function (el) { el.textContent = UI.fmtLeft(+el.getAttribute('data-left') - now()); });
    if (current) drawTimer();
  }, 1000);

  /* ---------- 管理单个房间 ---------- */
  var sheetBox = $('manage-sheet'), sheet = $('sheet'), current = null;
  var hours = UI.hourSlider($('hours'), function (v) { var b = $('timer-set'); if (b) b.textContent = (current && current.destroyAt ? '改为 ' : '') + v + ' 小时后销毁'; });
  var ask = UI.destroyConfirm(document.body);
  var limitT = 0;
  var limit = UI.stepper($('limit'), 2, 7, function (v) {
    $('limit-now').textContent = '最多 ' + v + ' 人';
    clearTimeout(limitT);
    var id = current && current.id;
    limitT = setTimeout(async function () {
      if (!id) return;
      try {
        var res = await api('/api/admin/rooms/' + id + '/limit', { value: v });
        if (!res.ok) throw new Error(res.data.error || '操作失败');
        UI.toast('info', '人数上限已改为 ' + v + ' 人', '房间 ' + id.slice(0, 6).toUpperCase());
        load();
      } catch (e) { UI.toast('err', '改上限失败', e.message); }
    }, 700);
  });
  sheet.inert = true;
  function find(id) { return rooms.filter(function (r) { return r.id === id; })[0]; }
  function drawTimer() {
    var on = !!current.destroyAt;
    $('timer-now').hidden = !on;
    if (on) $('timer-now-left').textContent = UI.fmtLeft(current.destroyAt - now());
    $('timer-set').textContent = (on ? '改为 ' : '') + hours.value + ' 小时后销毁';
  }
  function openSheet(id) {
    current = find(id);
    if (!current) return;
    $('sheet-code').textContent = current.code;
    $('sheet-meta').textContent = '在线 ' + current.online + ' 人 · ' + current.messages + ' 条消息 · 创建于 ' + UI.fmtAgo(now() - current.created);
    drawTimer();
    limit.set(current.limit);
    $('limit-now').textContent = '最多 ' + current.limit + ' 人';
    $('limit-online').textContent = '现在 ' + current.online + ' 人在线';
    sheetBox.classList.add('on'); sheet.inert = false;
    setTimeout(function () { $('sheet-close').focus({ preventScroll: true }); }, rm() ? 0 : 250);
  }
  function closeSheet() { current = null; sheetBox.classList.remove('on'); sheet.inert = true; }
  $('sheet-dim').addEventListener('click', closeSheet);
  $('sheet-close').addEventListener('click', closeSheet);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && current && !document.querySelector('.u269.is-m')) closeSheet(); });

  async function setTimer(hoursValue, okText) {
    var id = current.id;
    try {
      var res = await api('/api/admin/rooms/' + id + '/timer', { hours: hoursValue });
      if (!res.ok) throw new Error(res.data.error || '操作失败');
      UI.toast(hoursValue === 0 ? 'err' : hoursValue === null ? 'info' : 'warn', okText, '房间 ' + id.slice(0, 6).toUpperCase());
      closeSheet();
      load();
    } catch (e) { UI.toast('err', '操作失败', e.message); }
  }
  $('timer-set').addEventListener('click', function () { setTimer(hours.value, '已设为 ' + hours.value + ' 小时后销毁'); });
  $('timer-cancel').addEventListener('click', function () { setTimer(null, '已取消销毁倒计时'); });
  $('destroy-now').addEventListener('click', async function () {
    if (current && await ask(current.code)) setTimer(0, '房间已销毁');
  });

  api('/api/admin/me').then(function (r) { if (r.data.admin) showRooms(); else showLogin(); }, showLogin);
})();
