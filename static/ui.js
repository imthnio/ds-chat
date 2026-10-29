/* 聊天页和管理后台共用的界面部件。
   提示条、小时滑块、销毁确认框分别移植自 Lucian UX/UI Prompt #070、#017、#269
   （MIT License (c) 2026 Seokwoooo · https://lucianlabs.dev/uxui-prompt/）。 */

// 在第一次绘制前定主题：记住的选择 → 跟随系统
(function () {
  try {
    var s = localStorage.getItem('dschat:theme');
    if (s === 'dark' || s === 'light') document.documentElement.setAttribute('data-theme', s);
  } catch (e) {}
})();

window.UI = (function () {
  var rm = function () { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; };

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; });
  }

  /* ---------- 主题切换 ---------- */
  function themeButton(btn) {
    var root = document.documentElement;
    function current() {
      var t = root.getAttribute('data-theme');
      if (t) return t;
      return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    function label() { btn.setAttribute('aria-label', current() === 'dark' ? '切换到浅色' : '切换到深色'); }
    btn.addEventListener('click', function () {
      var next = current() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('dschat:theme', next); } catch (e) {}
      label();
    });
    label();
  }

  /* ---------- 提示条 ---------- */
  /* Lucian UX/UI Prompt #070 · MIT License (c) 2026 Seokwoooo · https://lucianlabs.dev/uxui-prompt/#070 */
  var toastState = null;
  function toastRoot() {
    if (toastState) return toastState;
    var root = document.createElement('div');
    root.className = 'ux u070 toasts';
    root.innerHTML = '<div class="u070-zone" role="region" aria-label="通知" tabindex="-1"><div class="u070-live" aria-live="polite"></div></div>';
    document.body.appendChild(root);
    var zone = root.querySelector('.u070-zone'), live = root.querySelector('.u070-live');
    var s = toastState = { root: root, zone: zone, live: live, list: [], open: false };
    var last = performance.now();
    (function loop(now) {
      var dt = now - last; last = now;
      if (!s.open) s.list.slice().forEach(function (t, i) {
        if (i > 0 || t.left === Infinity) return;
        t.left -= dt;
        t.el.style.setProperty('--left', Math.max(0, t.left / t.dur).toFixed(3));
        if (t.left <= 0) removeToast(t);
      });
      requestAnimationFrame(loop);
    })(last);
    zone.addEventListener('focusin', function () { setOpen(true); });
    zone.addEventListener('focusout', function (e) { if (!zone.contains(e.relatedTarget)) setOpen(false); });
    return s;
  }
  function layout() {
    var s = toastState, acc = 0;
    s.list.forEach(function (t, i) {
      var el = t.el;
      el.classList.toggle('back', !s.open && i > 0);
      if (s.open) { el.style.setProperty('--y', -acc + 'px'); el.style.setProperty('--s', 1); el.style.setProperty('--o', 1); acc += el.offsetHeight + 8; }
      else { el.style.setProperty('--y', -(i * 14) + 'px'); el.style.setProperty('--s', (1 - i * 0.06).toFixed(2)); el.style.setProperty('--o', i > 2 ? 0 : 1); }
      el.style.zIndex = 50 - i;
      el.inert = !s.open && i > 0;
    });
  }
  function setOpen(v) { if (toastState.open === v) return; toastState.open = v; layout(); }
  function removeToast(t, dir) {
    var s = toastState, i = s.list.indexOf(t);
    if (i < 0) return;
    s.list.splice(i, 1);
    t.el.classList.add('leave');
    if (dir) { t.el.style.setProperty('--dir', dir); t.el.classList.add('side'); }
    setTimeout(function () { t.el.remove(); }, 450);
    if (!s.list.length) s.open = false;
    layout();
  }
  var ICON = {
    ok: '<svg viewBox="0 0 18 18"><path d="M4 9.4l3.2 3.2L14 5.6"/></svg>',
    err: '<svg viewBox="0 0 18 18"><path d="M9 5v5M9 13h.01"/></svg>',
    info: '<svg viewBox="0 0 18 18"><path d="M3.5 4.5h11v7h-6l-3 2.5v-2.5h-2z"/></svg>',
    warn: '<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="6"/><path d="M9 5.8V9l2 1.4"/></svg>'
  };
  var COLOR = { ok: 'var(--ux-mint)', err: 'var(--ux-danger)', info: 'var(--ux-accent)', warn: 'var(--ux-warn)' };
  function toast(kind, title, sub, dur) {
    var s = toastRoot(), el = document.createElement('div');
    el.className = 'u070-t enter';
    el.style.setProperty('--c', COLOR[kind] || COLOR.info);
    el.setAttribute('role', kind === 'err' ? 'alert' : 'status');
    el.innerHTML = '<span class="u070-ic" aria-hidden="true">' + (ICON[kind] || ICON.info) + '</span><span class="u070-tx"><b></b>' + (sub ? '<small></small>' : '') + '</span><button class="u070-x" type="button" aria-label="关闭"><svg viewBox="0 0 14 14"><path d="M3.5 3.5l7 7M10.5 3.5l-7 7"/></svg></button><span class="u070-bar" aria-hidden="true"></span>';
    el.querySelector('b').textContent = title;
    if (sub) el.querySelector('small').textContent = sub;
    var t = { el: el, dur: dur || 4500, left: dur || 4500 };
    s.zone.appendChild(el);
    s.list.unshift(t);
    void el.offsetWidth;
    el.classList.remove('enter');
    layout();
    s.live.textContent = title + (sub ? '，' + sub : '');
    el.querySelector('.u070-x').addEventListener('click', function () { removeToast(t); });
    el.addEventListener('pointerenter', function (e) { if (e.pointerType === 'mouse') setOpen(true); });
    el.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse' && !(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('.u070-t')) && !s.zone.contains(document.activeElement)) setOpen(false); });
    el.addEventListener('click', function (e) { if (!e.target.closest('button') && s.list.length > 1) setOpen(!s.open); });
    var sx = 0, dx = 0, down = false;
    el.addEventListener('pointerdown', function (e) { if (e.target.closest('button')) return; down = true; sx = e.clientX; dx = 0; el.setPointerCapture(e.pointerId); el.classList.add('drag'); });
    el.addEventListener('pointermove', function (e) { if (!down) return; dx = e.clientX - sx; el.style.setProperty('--dx', dx + 'px'); el.style.setProperty('--o', Math.max(0.2, 1 - Math.abs(dx) / 260)); });
    var end = function () { if (!down) return; down = false; el.classList.remove('drag'); if (Math.abs(dx) > 90) removeToast(t, dx > 0 ? 1 : -1); else { el.style.setProperty('--dx', '0px'); layout(); } };
    el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end);
    return t;
  }

  /* ---------- 1～12 小时滑块 ---------- */
  /* Lucian UX/UI Prompt #017 · MIT License (c) 2026 Seokwoooo · https://lucianlabs.dev/uxui-prompt/#017 */
  // opts：{ min, max, value, ticks, page }，默认 1～12 小时
  function hourSlider(root, onChange, opts) {
    opts = opts || {};
    var track = root.querySelector('.u017-track');
    var thumb = root.querySelector('.u017-thumb');
    var bub = root.querySelector('.u017-bub');
    var out = root.querySelector('.u017-val');
    var tickBox = root.querySelector('.u017-ticks');
    var MIN = opts.min || 1, MAX = opts.max || 12, STEP = 1, PAGE = opts.page || 3;
    var v = opts.value || 6, lastX = 0, lastT = 0, still = 0;
    thumb.setAttribute('aria-valuemin', MIN); thumb.setAttribute('aria-valuemax', MAX);
    var ticks = (opts.ticks || [1, 3, 6, 9, 12]).map(function (h) {
      var s = document.createElement('span');
      s.textContent = h + 'h';
      s.style.setProperty('--p', ((h - MIN) / (MAX - MIN)).toFixed(4));
      s.dataset.h = h;
      tickBox.appendChild(s);
      return s;
    });
    function render() {
      var p = (v - MIN) / (MAX - MIN);
      root.style.setProperty('--v', p.toFixed(4));
      out.textContent = v + ' 小时';
      thumb.setAttribute('aria-valuenow', String(v));
      thumb.setAttribute('aria-valuetext', v + ' 小时');
      ticks.forEach(function (t) { t.classList.toggle('on', +t.dataset.h <= v); });
      if (onChange) onChange(v);
    }
    function tilt(deg) {
      if (rm()) return;
      bub.style.setProperty('--tilt', Math.max(-22, Math.min(22, deg)).toFixed(1) + 'deg');
      clearTimeout(still);
      still = setTimeout(function () { bub.style.setProperty('--tilt', '0deg'); }, 90);
    }
    function fromX(x) {
      var r = track.getBoundingClientRect();
      var p = Math.max(0, Math.min(1, (x - r.left) / r.width));
      var nv = MIN + Math.round(p * (MAX - MIN) / STEP) * STEP;
      if (nv !== v) { v = nv; render(); }
    }
    track.addEventListener('pointerdown', function (e) {
      if (e.button !== 0) return;
      track.setPointerCapture(e.pointerId);
      root.classList.add('is-drag');
      lastX = e.clientX; lastT = performance.now();
      fromX(e.clientX);
      thumb.focus({ preventScroll: true });
    });
    track.addEventListener('pointermove', function (e) {
      if (!track.hasPointerCapture(e.pointerId)) return;
      var now = performance.now();
      var vel = (e.clientX - lastX) / Math.max(8, now - lastT);
      lastX = e.clientX; lastT = now;
      tilt(-vel * 14);
      fromX(e.clientX);
    });
    function end() { root.classList.remove('is-drag'); bub.style.setProperty('--tilt', '0deg'); }
    track.addEventListener('pointerup', end);
    track.addEventListener('pointercancel', end);
    thumb.addEventListener('keydown', function (e) {
      var d = { ArrowRight: STEP, ArrowUp: STEP, ArrowLeft: -STEP, ArrowDown: -STEP, PageUp: PAGE, PageDown: -PAGE }[e.key];
      if (e.key === 'Home') d = -MAX; if (e.key === 'End') d = MAX;
      if (!d) return;
      e.preventDefault();
      v = Math.max(MIN, Math.min(MAX, v + d));
      tilt(d > 0 ? -9 : 9);
      render();
    });
    render();
    return { get value() { return v; }, set: function (n) { v = Math.max(MIN, Math.min(MAX, n)); render(); } };
  }

  /* ---------- 人数上限加减器 ---------- */
  /* Lucian UX/UI Prompt #018 · MIT License (c) 2026 Seokwoooo · https://lucianlabs.dev/uxui-prompt/#018 */
  function stepper(root, MIN, MAX, onChange) {
    var step = root.querySelector('.u018-step');
    var mi = root.querySelector('.mi'), pl = root.querySelector('.pl');
    var cols = root.querySelectorAll('.u018-col');
    var sr = root.querySelector('.u018-sr');
    var q = MAX;
    Array.prototype.forEach.call(cols, function (c) {
      for (var d = 0; d < 10; d++) { var s = document.createElement('span'); s.textContent = d; c.appendChild(s); }
      c.style.width = '17px';
    });
    function render() {
      var tens = Math.floor(q / 10), ones = q % 10;
      cols[0].style.transform = 'translateY(' + (-tens * 34) + 'px)';
      cols[0].classList.toggle('is-hide', tens === 0);
      cols[1].style.transform = 'translateY(' + (-ones * 34) + 'px)';
      mi.setAttribute('aria-disabled', String(q <= MIN));
      pl.setAttribute('aria-disabled', String(q >= MAX));
      sr.textContent = q + ' 人';
    }
    function bump(d) {
      var n = q + d;
      if (n < MIN || n > MAX) {
        step.classList.remove('is-shake'); void step.offsetWidth; step.classList.add('is-shake');
        return;
      }
      q = n;
      render();
      if (onChange) onChange(q);
    }
    mi.addEventListener('click', function () { bump(-1); });
    pl.addEventListener('click', function () { bump(1); });
    step.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowUp' || e.key === 'ArrowRight') { e.preventDefault(); bump(1); }
      if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') { e.preventDefault(); bump(-1); }
    });
    render();
    return { get value() { return q; }, set: function (v) { q = Math.max(MIN, Math.min(MAX, v)); render(); } };
  }

  /* ---------- 输入房间码才能销毁 ---------- */
  /* Lucian UX/UI Prompt #269 · MIT License (c) 2026 Seokwoooo · https://lucianlabs.dev/uxui-prompt/#269 */
  function destroyConfirm(host) {
    var root = document.createElement('div');
    root.className = 'ux u269 confirm';
    root.innerHTML = '<div class="u269-dim" aria-hidden="true"></div><div class="u269-md" role="alertdialog" aria-modal="true" aria-hidden="true"><h4 class="u269-ht"><i aria-hidden="true">!</i><span></span></h4><p></p><div class="u269-nm" aria-hidden="true"></div><input class="u269-in" aria-label="输入房间码" autocomplete="off" spellcheck="false" autocapitalize="characters"><div class="u269-bs"><button type="button" class="u269-cx">取消</button><button type="button" class="u269-go" disabled>永久销毁</button></div></div>';
    host.appendChild(root);
    var md = root.querySelector('.u269-md'), nm = root.querySelector('.u269-nm'), inp = root.querySelector('.u269-in'), go = root.querySelector('.u269-go'), cx = root.querySelector('.u269-cx'), dim = root.querySelector('.u269-dim');
    var uid = 'u269' + Math.random().toString(36).slice(2, 7); root.querySelector('.u269-ht').id = uid; md.setAttribute('aria-labelledby', uid);
    var NAME = '', L = [], lastLen = 0, done = null, back = null;
    md.inert = true;
    function sync() {
      var v = inp.value.toUpperCase(), k = 0; while (k < v.length && v[k] === NAME[k]) k++;
      L.forEach(function (s, i) { s.classList.toggle('is-ok', i < k); });
      if (v.length > lastLen && k < v.length && !rm()) { inp.classList.remove('is-shake'); void inp.offsetWidth; inp.classList.add('is-shake'); }
      lastLen = v.length; go.disabled = v !== NAME;
    }
    function close(result) {
      root.classList.remove('is-m'); md.setAttribute('aria-hidden', 'true'); md.inert = true;
      if (back) back.focus({ preventScroll: true });
      if (done) { var d = done; done = null; d(result); }
    }
    go.addEventListener('click', function () { if (!go.disabled) close(true); });
    cx.addEventListener('click', function () { close(false); });
    dim.addEventListener('click', function () { close(false); });
    root.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && root.classList.contains('is-m')) { e.stopPropagation(); close(false); }
      // 吃掉这次回车：不然焦点回到“立即销毁”按钮后，同一下回车会再点它一次，把确认框又打开
      if (e.key === 'Enter' && document.activeElement === inp) { e.preventDefault(); if (!go.disabled) go.click(); }
    });
    inp.addEventListener('input', sync);
    var ht = root.querySelector('.u269-ht span'), desc = md.querySelector('p');
    // ask(要输入的码, { title, text })：默认是销毁单个房间的文案
    return function ask(code, words) {
      if (done) close(false);
      words = words || {};
      ht.textContent = words.title || '立即销毁这个房间？';
      desc.textContent = words.text || '房间和全部聊天记录会马上从服务器删除，在线的人会被请出，无法恢复。输入下面的房间码确认。';
      NAME = code.toUpperCase();
      nm.innerHTML = ''; L = NAME.split('').map(function (ch) { var s = document.createElement('span'); s.textContent = ch; nm.appendChild(s); return s; });
      back = document.activeElement;
      root.classList.add('is-m'); md.setAttribute('aria-hidden', 'false'); md.inert = false;
      inp.value = ''; lastLen = 0; sync();
      setTimeout(function () { inp.focus({ preventScroll: true }); }, rm() ? 0 : 200);
      return new Promise(function (resolve) { done = resolve; });
    };
  }

  /* ---------- 时间 ---------- */
  function fmtLeft(sec) {
    sec = Math.max(0, Math.ceil(sec));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    var two = function (n) { return (n < 10 ? '0' : '') + n; };
    return (h ? h + ':' + two(m) : m) + ':' + two(s);
  }
  function fmtClock(ts) {
    var d = new Date(ts * 1000);
    return d.getHours() + ':' + (d.getMinutes() < 10 ? '0' : '') + d.getMinutes();
  }
  function fmtDay(ts) {
    var d = new Date(ts * 1000), today = new Date();
    var start = function (x) { return new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime(); };
    var diff = Math.round((start(today) - start(d)) / 86400000);
    if (diff === 0) return '今天';
    if (diff === 1) return '昨天';
    return (d.getFullYear() !== today.getFullYear() ? d.getFullYear() + '年' : '') + (d.getMonth() + 1) + '月' + d.getDate() + '日';
  }
  function fmtBytes(n) {
    if (n < 1024) return n + ' B';
    var u = ['KB', 'MB', 'GB', 'TB'], i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return (n < 10 ? n.toFixed(1) : Math.round(n)) + ' ' + u[i];
  }
  function fmtAgo(sec) {
    if (sec < 60) return '刚刚';
    if (sec < 3600) return Math.floor(sec / 60) + ' 分钟前';
    if (sec < 86400) return Math.floor(sec / 3600) + ' 小时前';
    return Math.floor(sec / 86400) + ' 天前';
  }

  return { esc: esc, themeButton: themeButton, toast: toast, hourSlider: hourSlider, stepper: stepper, destroyConfirm: destroyConfirm, fmtLeft: fmtLeft, fmtClock: fmtClock, fmtDay: fmtDay, fmtAgo: fmtAgo, fmtBytes: fmtBytes };
})();
