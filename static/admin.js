/* ds-chat 管理后台：登录、房间列表（可多选批量操作）、单个房间管理、全局自动销毁。
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
    var data = null, text = '';
    try { text = await r.text(); data = JSON.parse(text); } catch (e) {}
    if (r.status === 401 && path !== '/api/admin/login') { showLogin(); throw new Error('登录已过期，请重新登录'); }
    if (!r.ok && !(data && data.error)) throw new Error(text || '请求失败（' + r.status + '）');
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
    closeAll();
    $('view-rooms').hidden = true; $('view-login').hidden = false; $('logout').hidden = true;
    pw.focus({ preventScroll: true });
  }
  function showRooms() {
    $('view-login').hidden = true; $('view-rooms').hidden = false; $('logout').hidden = false;
    load();
    clearInterval(poll); poll = setInterval(load, 10000);
  }

  /* ---------- 底部弹出的面板 ---------- */
  var openBox = null;
  function openPanel(box) {
    closeAll();
    openBox = box;
    box.classList.add('on'); box.querySelector('.sheet').inert = false;
    setTimeout(function () { var b = box.querySelector('[data-close], #sheet-close'); if (b) b.focus({ preventScroll: true }); }, rm() ? 0 : 250);
  }
  function closeAll() {
    document.querySelectorAll('.admin-sheet.on').forEach(function (b) { b.classList.remove('on'); b.querySelector('.sheet').inert = true; });
    openBox = null; current = null;
  }
  document.querySelectorAll('.admin-sheet').forEach(function (box) {
    box.querySelector('.sheet').inert = true;
    box.querySelector('.sheet-dim').addEventListener('click', closeAll);
    box.querySelectorAll('[data-close], #sheet-close').forEach(function (b) { b.addEventListener('click', closeAll); });
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && openBox && !document.querySelector('.u269.is-m')) closeAll(); });

  /* ---------- 房间列表 ---------- */
  var poll = 0, rooms = [], policy = { hours: null, since: null }, skew = 0, selected = {};
  function now() { return Date.now() / 1000 + skew; }
  async function load() {
    try {
      var res = await api('/api/admin/rooms');
      skew = res.data.now - Date.now() / 1000;
      rooms = res.data.rooms;
      policy = res.data.policy || policy;
      var alive = {};
      rooms.forEach(function (r) { if (selected[r.id]) alive[r.id] = 1; });
      selected = alive;
      render(res.data.storage);
      if (current) { current = find(current.id) || current; drawTimer(); }
    } catch (e) {
      if (!$('view-rooms').hidden) UI.toast('err', '加载失败', e.message);
    }
  }
  function timerLabel(r) {
    if (!r.destroyAt) return '';
    var byPolicy = r.policyAt && r.destroyAt === r.policyAt;
    return '<span class="warn">' + (byPolicy ? '全局销毁' : '销毁倒计时') + ' <b data-left="' + r.destroyAt + '">' + UI.fmtLeft(r.destroyAt - now()) + '</b></span>';
  }
  function render(storage) {
    var online = rooms.reduce(function (n, r) { return n + r.online; }, 0);
    var timed = rooms.filter(function (r) { return r.destroyAt; }).length;
    $('stats').innerHTML = '<span>房间 <b>' + rooms.length + '</b></span><span>在线 <b>' + online + '</b> 人</span><span>倒计时中 <b>' + timed + '</b></span>' +
      (storage ? '<span>文件占用 <b>' + UI.fmtBytes(storage.files) + '</b></span><span>数据库 <b>' + UI.fmtBytes(storage.db) + '</b></span><span>硬盘剩余 <b>' + UI.fmtBytes(storage.freeMb * 1048576) + '</b></span>' : '');
    renderPolicy();
    var box = $('rooms');
    if (!rooms.length) { box.innerHTML = '<p class="rooms-empty">还没有房间</p>'; renderBatch(); return; }
    box.innerHTML = rooms.map(function (r) {
      var t = now(), id = UI.esc(r.id);
      return '<div class="room' + (selected[r.id] ? ' sel' : '') + '"><label class="chk"><input type="checkbox" data-sel="' + id + '"' + (selected[r.id] ? ' checked' : '') + ' aria-label="选择房间 ' + UI.esc(r.code) + '"></label>' +
        '<code>' + UI.esc(r.code) + '</code><div class="room-info">' +
        '<span class="' + (r.online ? 'live' : '') + '">在线 <b>' + r.online + ' / ' + r.limit + '</b></span>' +
        '<span>消息 <b>' + r.messages + '</b></span>' +
        (r.files ? '<span>文件 <b>' + r.files + ' 个 · ' + UI.fmtBytes(r.fileBytes) + '</b></span>' : '') +
        '<span>创建 <b>' + UI.fmtAgo(t - r.created) + '</b></span>' +
        '<span>最近活动 <b>' + UI.fmtAgo(t - r.lastActive) + '</b></span>' +
        timerLabel(r) +
        '</div><button class="btn soft" type="button" data-id="' + id + '">管理</button></div>';
    }).join('');
    renderBatch();
  }
  $('rooms').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-id]');
    if (b) openRoom(b.getAttribute('data-id'));
  });
  $('rooms').addEventListener('change', function (e) {
    var c = e.target.closest('input[data-sel]');
    if (!c) return;
    var id = c.getAttribute('data-sel');
    if (c.checked) selected[id] = 1; else delete selected[id];
    c.closest('.room').classList.toggle('sel', c.checked);
    renderBatch();
  });
  $('select-all').addEventListener('change', function () {
    var on = this.checked;
    selected = {};
    if (on) rooms.forEach(function (r) { selected[r.id] = 1; });
    document.querySelectorAll('input[data-sel]').forEach(function (c) { c.checked = on; c.closest('.room').classList.toggle('sel', on); });
    renderBatch();
  });
  $('refresh').addEventListener('click', load);
  setInterval(function () {
    document.querySelectorAll('[data-left]').forEach(function (el) { el.textContent = UI.fmtLeft(+el.getAttribute('data-left') - now()); });
    if (current) drawTimer();
  }, 1000);

  /* ---------- 批量操作 ---------- */
  function selIds() { return Object.keys(selected); }
  function selCodes() { return selIds().map(function (id) { return id.slice(0, 6).toUpperCase(); }); }
  function renderBatch() {
    var n = selIds().length, all = $('select-all');
    all.checked = n > 0 && n === rooms.length;
    all.indeterminate = n > 0 && n < rooms.length;
    all.disabled = !rooms.length;
    $('batch-count').textContent = n ? '已选 ' + n + ' 个房间' : '勾选房间后可以批量操作';
    ['batch-timer', 'batch-cancel', 'batch-destroy'].forEach(function (id) { $(id).disabled = !n; });
    $('batch').classList.toggle('on', n > 0);
  }
  async function batch(hours, okText) {
    var ids = selIds();
    if (!ids.length) return;
    try {
      var res = await api('/api/admin/rooms/batch', { ids: ids, hours: hours });
      if (!res.ok) throw new Error(res.data.error || '操作失败');
      UI.toast(hours === 0 ? 'err' : hours === null ? 'info' : 'warn', okText, res.data.done + ' 个房间');
      if (hours === 0) selected = {};
      closeAll();
      load();
    } catch (e) { UI.toast('err', '操作失败', e.message); }
  }
  var batchHours = UI.hourSlider($('batch-hours'), function (v) {
    var b = $('batch-go'); if (b) b.textContent = '选中的 ' + selIds().length + ' 个房间 ' + v + ' 小时后销毁';
  });
  $('batch-timer').addEventListener('click', function () {
    var codes = selCodes();
    $('batch-meta').textContent = '已选 ' + codes.length + ' 个房间：' + codes.slice(0, 12).join('、') + (codes.length > 12 ? ' 等' : '') + '。会替换它们原来的倒计时。';
    batchHours.set(batchHours.value);
    openPanel($('batch-sheet'));
  });
  $('batch-go').addEventListener('click', function () { batch(batchHours.value, '已设为 ' + batchHours.value + ' 小时后销毁'); });
  $('batch-cancel').addEventListener('click', function () { batch(null, '已取消销毁倒计时'); });
  var ask = UI.destroyConfirm(document.body);
  $('batch-destroy').addEventListener('click', async function () {
    var n = selIds().length;
    if (!n) return;
    var code = String(1000 + Math.floor(Math.random() * 9000));
    if (await ask(code, { title: '立即销毁选中的 ' + n + ' 个房间？', text: '这些房间和全部聊天记录、文件会马上从服务器删除，在线的人会被请出，无法恢复。输入下面的数字确认。' })) {
      batch(0, '房间已销毁');
    }
  });

  /* ---------- 全局自动销毁 ---------- */
  function renderPolicy() {
    var on = !!policy.hours;
    $('policy').classList.toggle('on', on);
    $('policy-state').textContent = on ? '已开启 · 最长 ' + policy.hours + ' 小时' : '未开启';
    $('policy-text').textContent = on
      ? '所有房间最长存在 ' + policy.hours + ' 小时：开启前就有的房间从 ' + new Date(policy.since * 1000).toLocaleString() + ' 起算，之后新开的房间从创建时起算。'
      : '开启后，所有房间（包括以后新开的）都会在设定时间内自动销毁，房间和聊天记录、文件一起删除。';
    $('policy-open').textContent = on ? '修改' : '设置';
    drawPolicySheet();
  }
  function drawPolicySheet() {
    var on = !!policy.hours;
    $('policy-now').hidden = !on;
    if (on) $('policy-now-text').textContent = '现在：所有房间最长 ' + policy.hours + ' 小时';
    $('policy-go').textContent = (on ? '改为最长 ' : '开启：所有房间最长 ') + policyHours.value + ' 小时';
  }
  var policyHours = UI.hourSlider($('policy-hours'), function () { if (policyHours) drawPolicySheet(); },  // 第一次回调时还没赋值
    { min: 1, max: 168, value: 24, ticks: [1, 24, 72, 120, 168], page: 12 });
  async function setPolicy(hours, okText) {
    try {
      var res = await api('/api/admin/policy', { hours: hours });
      if (!res.ok) throw new Error(res.data.error || '操作失败');
      UI.toast(hours ? 'warn' : 'info', okText);
      closeAll();
      load();
    } catch (e) { UI.toast('err', '操作失败', e.message); }
  }
  $('policy-open').addEventListener('click', function () {
    policyHours.set(policy.hours || 24);
    openPanel($('policy-sheet'));
  });
  $('policy-go').addEventListener('click', function () {
    setPolicy(policyHours.value, '已开启全局自动销毁：所有房间最长 ' + policyHours.value + ' 小时');
  });
  $('policy-off').addEventListener('click', function () { setPolicy(null, '已关闭全局自动销毁'); });

  /* ---------- 管理单个房间 ---------- */
  var current = null;
  var hours = UI.hourSlider($('hours'), function (v) { var b = $('timer-set'); if (b) b.textContent = (current && current.own ? '改为 ' : '') + v + ' 小时后销毁'; });
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
  function find(id) { return rooms.filter(function (r) { return r.id === id; })[0]; }
  function drawTimer() {
    var on = !!current.destroyAt, byPolicy = on && current.policyAt && current.destroyAt === current.policyAt;
    $('timer-now').hidden = !on;
    if (on) $('timer-now-left').textContent = UI.fmtLeft(current.destroyAt - now()) + (byPolicy ? '（全局自动销毁）' : '');
    $('timer-cancel').hidden = !current.own;  // 全局自动销毁在这里取消不了
    $('timer-set').textContent = (current.own ? '改为 ' : '') + hours.value + ' 小时后销毁';
  }
  function openRoom(id) {
    var r = find(id);
    if (!r) return;
    openPanel($('manage-sheet'));
    current = r;
    $('sheet-code').textContent = r.code;
    $('sheet-meta').textContent = '在线 ' + r.online + ' 人 · ' + r.messages + ' 条消息' + (r.files ? ' · ' + r.files + ' 个文件（' + UI.fmtBytes(r.fileBytes) + '）' : '') + ' · 创建于 ' + UI.fmtAgo(now() - r.created);
    drawTimer();
    limit.set(r.limit);
    $('limit-now').textContent = '最多 ' + r.limit + ' 人';
    $('limit-online').textContent = '现在 ' + r.online + ' 人在线';
  }

  async function setTimer(hoursValue, okText) {
    var id = current.id;
    try {
      var res = await api('/api/admin/rooms/' + id + '/timer', { hours: hoursValue });
      if (!res.ok) throw new Error(res.data.error || '操作失败');
      UI.toast(hoursValue === 0 ? 'err' : hoursValue === null ? 'info' : 'warn', okText, '房间 ' + id.slice(0, 6).toUpperCase());
      closeAll();
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
