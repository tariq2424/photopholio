/* PhotoPholio app layer.
   1. Soft navigation: links and forms load without a full page reload (the browser still does a normal load if anything is unusual).
   2. Themed dropdowns and a themed calendar that replace the browser's own (which ignore the site theme).
   3. Home page: section rail, photographer filter chips, step tabs.
   4. New notifications: the bell panel opens by itself (a bottom sheet on small screens) and closes after a few seconds.
   Everything degrades: without JavaScript the native select, date field, links and forms keep working. */
(function () {
  'use strict';
  var root = document.documentElement;
  var calm = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  function $(s, c) { return (c || document).querySelector(s); }
  function $$(s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); }
  function clamp(v, a, b) { return Math.max(a === undefined ? 0 : a, Math.min(b === undefined ? 1 : b, v)); }
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html) e.innerHTML = html; return e; }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }

  /* ---------- page lifetime: things registered here are undone when the next page is swapped in ---------- */
  var cleanups = [];
  window.ppOnLeave = function (fn) { cleanups.push(fn); };
  window.ppListen = function (target, type, fn, opts) {
    target.addEventListener(type, fn, opts);
    cleanups.push(function () { target.removeEventListener(type, fn, opts); });
  };
  function leave() { cleanups.splice(0).forEach(function (fn) { try { fn(); } catch (e) {} }); }

  /* ---------- themed popup (shared by the dropdown and the calendar) ---------- */
  var pop = el('div', 'pp-pop'); pop.hidden = true; pop.id = 'ppPop';
  var backdrop = el('div', 'pp-backdrop'); backdrop.hidden = true;
  var openState = null;
  function mountPop() { if (!pop.parentNode) { document.body.appendChild(backdrop); document.body.appendChild(pop); } }
  function place() {
    if (!openState) return;
    var r = openState.btn.getBoundingClientRect(), vw = innerWidth, vh = innerHeight;
    var sheet = vw < 576;
    pop.classList.toggle('sheet', sheet); backdrop.hidden = !sheet;
    if (sheet) { pop.style.top = pop.style.left = pop.style.minWidth = ''; return; }
    pop.style.minWidth = Math.max(r.width, openState.kind === 'date' ? 300 : 0) + 'px';
    var ph = pop.offsetHeight, pw = pop.offsetWidth;
    var below = vh - r.bottom, above = r.top;
    var top = (below >= ph + 10 || below >= above) ? r.bottom + 6 : r.top - ph - 6;
    top = Math.max(8, Math.min(top, vh - ph - 8));
    pop.style.top = top + 'px';
    pop.style.left = Math.max(8, Math.min(r.left, vw - pw - 8)) + 'px';
  }
  function closePop(refocus) {
    if (!openState) return;
    var o = openState; openState = null;
    pop.hidden = true; pop.innerHTML = ''; backdrop.hidden = true;
    o.btn.setAttribute('aria-expanded', 'false'); o.btn.removeAttribute('aria-activedescendant'); o.wrap.classList.remove('open');
    if (refocus) { try { o.btn.focus({preventScroll: true}); } catch (e) {} }
  }
  function showPop(state) {
    closePop(false); mountPop();
    openState = state;
    state.btn.setAttribute('aria-expanded', 'true'); state.btn.setAttribute('aria-controls', 'ppPop'); state.wrap.classList.add('open');
    pop.hidden = false; place();
    requestAnimationFrame(place);
  }
  document.addEventListener('pointerdown', function (e) {
    if (!openState) return;
    if (pop.contains(e.target) || openState.wrap.contains(e.target)) return;
    closePop(false);
  }, true);
  backdrop.addEventListener('click', function () { closePop(true); });
  var ticking = false;
  function again() { if (ticking) return; ticking = true; requestAnimationFrame(function () { ticking = false; place(); }); }
  addEventListener('scroll', function (e) { if (openState && !pop.contains(e.target)) again(); }, true);
  addEventListener('resize', again);
  document.addEventListener('keydown', function (e) {
    if (openState && e.key === 'Escape') { e.preventDefault(); closePop(true); }
  });

  /* make `input.value = x` from other scripts refresh the visible label */
  function watchValue(node, proto, sync) {
    var d = Object.getOwnPropertyDescriptor(proto, 'value');
    if (!d || !d.set) return;
    Object.defineProperty(node, 'value', {configurable: true, get: function () { return d.get.call(node); },
      set: function (v) { d.set.call(node, v); sync(); }});
  }
  function labelFor(node) {
    var l = node.id ? $('label[for="' + node.id + '"]') : null;
    return node.getAttribute('aria-label') || (l ? l.textContent.trim() : '') || node.getAttribute('title') || '';
  }
  function wrapControl(node, kind) {
    var wrap = el('div', 'pp-ctl pp-' + kind);
    var btn = el('button', 'pp-btn'); btn.type = 'button';
    btn.setAttribute('aria-haspopup', kind === 'date' ? 'dialog' : 'listbox'); btn.setAttribute('aria-expanded', 'false');
    var lab = labelFor(node); if (lab) btn.setAttribute('aria-label', lab);
    btn.innerHTML = '<span class="pp-val"></span><i class="bi ' + (kind === 'date' ? 'bi-calendar3' : 'bi-chevron-down') + ' pp-chev" aria-hidden="true"></i>';
    node.parentNode.insertBefore(wrap, node);
    wrap.appendChild(btn); wrap.appendChild(node);
    node.classList.add('pp-native'); node.tabIndex = -1; node.setAttribute('aria-hidden', 'true');
    node.addEventListener('focus', function () { try { btn.focus({preventScroll: true}); } catch (e) {} });
    node.addEventListener('invalid', function () { wrap.classList.add('is-invalid'); });
    node.addEventListener('change', function () { wrap.classList.remove('is-invalid'); });
    if (node.classList.contains('is-invalid')) wrap.classList.add('is-invalid');
    return {wrap: wrap, btn: btn, val: btn.firstChild};
  }

  /* ---------- themed dropdown ---------- */
  function enhanceSelect(sel) {
    if (sel.dataset.pp || sel.multiple || sel.size > 1 || sel.classList.contains('no-pp')) return;
    sel.dataset.pp = '1';
    var c = wrapControl(sel, 'select'), wrap = c.wrap, btn = c.btn, val = c.val;
    function sync() {
      var o = sel.options[sel.selectedIndex];
      val.textContent = o ? o.text : '';
      wrap.classList.toggle('is-empty', !sel.value);
      btn.disabled = sel.disabled;
    }
    sync();
    sel.addEventListener('change', sync);
    watchValue(sel, HTMLSelectElement.prototype, sync);
    if (sel.form) sel.form.addEventListener('reset', function () { setTimeout(sync, 0); });

    var active = -1, items = [], buf = '', bufT = null;
    function setActive(i, scroll) {
      if (active >= 0 && items[active]) items[active].node.classList.remove('act');
      active = i;
      if (i >= 0 && items[i]) {
        items[i].node.classList.add('act');
        btn.setAttribute('aria-activedescendant', items[i].node.id);
        if (scroll !== false) items[i].node.scrollIntoView({block: 'nearest'});
      }
    }
    function choose(i) {
      var it = items[i]; if (!it || it.disabled) return;
      sel.selectedIndex = it.index;
      sel.dispatchEvent(new Event('input', {bubbles: true}));
      sel.dispatchEvent(new Event('change', {bubbles: true}));
      closePop(true);
    }
    function step(d) {
      var i = active;
      do { i += d; } while (i >= 0 && i < items.length && items[i].disabled);
      if (i >= 0 && i < items.length) setActive(i);
    }
    function open() {
      if (btn.disabled) return;
      pop.className = 'pp-pop pp-list'; pop.setAttribute('role', 'listbox'); pop.removeAttribute('aria-modal');
      pop.setAttribute('aria-label', labelFor(sel) || 'Choose');
      pop.innerHTML = ''; items = [];
      var uid = 'ppo' + Date.now().toString(36);
      Array.prototype.forEach.call(sel.children, function (ch) {
        if (ch.tagName === 'OPTGROUP') {
          pop.appendChild(el('div', 'pp-group', esc(ch.label)));
          Array.prototype.forEach.call(ch.children, add);
        } else add(ch);
      });
      function add(o) {
        var n = el('div', 'pp-opt' + (o.disabled ? ' dis' : '') + (o.index === sel.selectedIndex ? ' sel' : ''));
        n.id = uid + '-' + o.index; n.setAttribute('role', 'option'); n.setAttribute('aria-selected', o.index === sel.selectedIndex ? 'true' : 'false');
        n.innerHTML = '<span>' + esc(o.text) + '</span><i class="bi bi-check2" aria-hidden="true"></i>';
        var k = items.length;
        n.addEventListener('mousedown', function (e) { e.preventDefault(); });
        n.addEventListener('click', function () { choose(k); });
        n.addEventListener('mousemove', function () { if (active !== k && !o.disabled) setActive(k, false); });
        items.push({node: n, index: o.index, disabled: o.disabled, text: o.text.toLowerCase()});
        pop.appendChild(n);
      }
      showPop({btn: btn, wrap: wrap, kind: 'select'});
      var cur = items.findIndex(function (it) { return it.index === sel.selectedIndex; });
      active = -1; setActive(cur >= 0 ? cur : 0);
    }
    btn.addEventListener('click', function () { if (openState && openState.btn === btn) closePop(false); else open(); });
    btn.addEventListener('keydown', function (e) {
      var isOpen = openState && openState.btn === btn, k = e.key;
      if (!isOpen) {
        if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'Enter' || k === ' ') { e.preventDefault(); open(); }
        return;
      }
      if (k === 'ArrowDown') { e.preventDefault(); step(1); }
      else if (k === 'ArrowUp') { e.preventDefault(); step(-1); }
      else if (k === 'Home') { e.preventDefault(); setActive(0); }
      else if (k === 'End') { e.preventDefault(); setActive(items.length - 1); }
      else if (k === 'Enter' || k === ' ') { e.preventDefault(); choose(active); }
      else if (k === 'Tab') { closePop(false); }
      else if (k.length === 1) {                        // type to jump
        buf += k.toLowerCase(); clearTimeout(bufT); bufT = setTimeout(function () { buf = ''; }, 700);
        var hit = items.findIndex(function (it) { return !it.disabled && it.text.indexOf(buf) === 0; });
        if (hit >= 0) setActive(hit);
      }
    });
  }

  /* ---------- themed calendar ---------- */
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  var DOW = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function iso(y, m, d) { return y + '-' + pad(m + 1) + '-' + pad(d); }
  function parse(s) { var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || ''); return m ? {y: +m[1], m: +m[2] - 1, d: +m[3]} : null; }
  function nice(s) {
    var p = parse(s); if (!p) return '';
    try { return new Date(p.y, p.m, p.d).toLocaleDateString('en-IN', {weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'}); }
    catch (e) { return s; }
  }
  function todayIso() { var n = new Date(); return iso(n.getFullYear(), n.getMonth(), n.getDate()); }

  function enhanceDate(inp) {
    if (inp.dataset.pp || inp.classList.contains('no-pp')) return;
    inp.dataset.pp = '1';
    var c = wrapControl(inp, 'date'), wrap = c.wrap, btn = c.btn, val = c.val;
    var holder = inp.getAttribute('data-placeholder') || 'Pick a date';
    function sync() {
      var v = inp.value;
      val.textContent = v ? nice(v) : holder;
      wrap.classList.toggle('is-empty', !v);
      btn.disabled = inp.disabled;
    }
    sync();
    inp.addEventListener('change', sync);
    watchValue(inp, HTMLInputElement.prototype, sync);
    if (inp.form) inp.form.addEventListener('reset', function () { setTimeout(sync, 0); });

    var st = null;
    function inRange(s) { return (!inp.min || s >= inp.min) && (!inp.max || s <= inp.max); }
    function monthOk(y, m) { var a = iso(y, m, 1), b = iso(y, m, new Date(y, m + 1, 0).getDate()); return (!inp.min || b >= inp.min) && (!inp.max || a <= inp.max); }
    function pick(s) {
      if (!inRange(s)) return;
      inp.value = s;
      inp.dispatchEvent(new Event('input', {bubbles: true}));
      inp.dispatchEvent(new Event('change', {bubbles: true}));
      closePop(true);
    }
    function render(focusIso) {
      var html = '', now = todayIso();
      if (st.view === 'days') {
        var first = (new Date(st.y, st.m, 1).getDay() + 6) % 7, days = new Date(st.y, st.m + 1, 0).getDate();
        html += '<div class="pp-cal-head"><button type="button" class="pp-nav" data-act="prev" aria-label="Previous month"><i class="bi bi-chevron-left"></i></button>' +
          '<span class="pp-cal-title"><button type="button" data-act="months">' + MONTHS[st.m] + '</button><button type="button" data-act="years">' + st.y + '</button></span>' +
          '<button type="button" class="pp-nav" data-act="next" aria-label="Next month"><i class="bi bi-chevron-right"></i></button></div><div class="pp-dow">';
        DOW.forEach(function (d) { html += '<span>' + d + '</span>'; });
        html += '</div><div class="pp-days">';
        for (var i = 0; i < first; i++) html += '<span></span>';
        for (var d = 1; d <= days; d++) {
          var s = iso(st.y, st.m, d), cls = 'pp-day' + (s === inp.value ? ' sel' : '') + (s === now ? ' today' : '');
          html += '<button type="button" class="' + cls + '" data-iso="' + s + '"' + (inRange(s) ? '' : ' disabled') + (s === inp.value ? ' aria-pressed="true"' : '') + '>' + d + '</button>';
        }
        html += '</div><div class="pp-cal-foot">' + (inp.required ? '<span></span>' : '<button type="button" data-act="clear">Clear</button>') +
          '<button type="button" data-act="today"' + (inRange(now) ? '' : ' disabled') + '>Today</button></div>';
      } else if (st.view === 'months') {
        html += '<div class="pp-cal-head"><button type="button" class="pp-nav" data-act="yprev" aria-label="Previous year"><i class="bi bi-chevron-left"></i></button>' +
          '<span class="pp-cal-title"><button type="button" data-act="years">' + st.y + '</button></span>' +
          '<button type="button" class="pp-nav" data-act="ynext" aria-label="Next year"><i class="bi bi-chevron-right"></i></button></div><div class="pp-grid">';
        MONTHS.forEach(function (n, i) { html += '<button type="button" data-month="' + i + '"' + (monthOk(st.y, i) ? '' : ' disabled') + (i === st.m ? ' class="sel"' : '') + '>' + n.slice(0, 3) + '</button>'; });
        html += '</div>';
      } else {
        var base = st.page;
        html += '<div class="pp-cal-head"><button type="button" class="pp-nav" data-act="pprev" aria-label="Earlier years"><i class="bi bi-chevron-left"></i></button>' +
          '<span class="pp-cal-title"><span>' + base + ' – ' + (base + 11) + '</span></span>' +
          '<button type="button" class="pp-nav" data-act="pnext" aria-label="Later years"><i class="bi bi-chevron-right"></i></button></div><div class="pp-grid">';
        for (var y = base; y < base + 12; y++) {
          var ok = (!inp.min || y >= +inp.min.slice(0, 4)) && (!inp.max || y <= +inp.max.slice(0, 4));
          html += '<button type="button" data-year="' + y + '"' + (ok ? '' : ' disabled') + (y === st.y ? ' class="sel"' : '') + '>' + y + '</button>';
        }
        html += '</div>';
      }
      pop.innerHTML = html;
      place();
      if (focusIso) { var t = $('[data-iso="' + focusIso + '"]', pop); if (t && !t.disabled) t.focus({preventScroll: true}); }
    }
    function shiftMonth(d) { st.m += d; if (st.m < 0) { st.m = 11; st.y--; } if (st.m > 11) { st.m = 0; st.y++; } }
    function open() {
      if (btn.disabled) return;
      var p = parse(inp.value) || parse(todayIso());
      var start = iso(p.y, p.m, p.d);
      if (inp.min && start < inp.min) { p = parse(inp.min); }
      if (inp.max && start > inp.max) { p = parse(inp.max); }
      st = {y: p.y, m: p.m, view: 'days', page: p.y - 4, focus: iso(p.y, p.m, p.d)};
      pop.className = 'pp-pop pp-cal'; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-modal', 'false');
      pop.setAttribute('aria-label', (labelFor(inp) || 'Choose a date'));
      showPop({btn: btn, wrap: wrap, kind: 'date'});
      render(st.focus);
    }
    pop.addEventListener('click', onPop);
    function onPop(e) {
      if (!openState || openState.btn !== btn) return;
      var t = e.target.closest('button'); if (!t || t.disabled) return;
      if (t.dataset.iso) { pick(t.dataset.iso); return; }
      if (t.dataset.month !== undefined) { st.m = +t.dataset.month; st.view = 'days'; render(); return; }
      if (t.dataset.year !== undefined) { st.y = +t.dataset.year; st.view = 'months'; render(); return; }
      switch (t.dataset.act) {
        case 'prev': shiftMonth(-1); render(); break;
        case 'next': shiftMonth(1); render(); break;
        case 'months': st.view = 'months'; render(); break;
        case 'years': st.view = 'years'; st.page = st.y - 4; render(); break;
        case 'yprev': st.y--; render(); break;
        case 'ynext': st.y++; render(); break;
        case 'pprev': st.page -= 12; render(); break;
        case 'pnext': st.page += 12; render(); break;
        case 'today': pick(todayIso()); break;
        case 'clear': inp.value = ''; inp.dispatchEvent(new Event('input', {bubbles: true})); inp.dispatchEvent(new Event('change', {bubbles: true})); closePop(true); break;
      }
    }
    pop.addEventListener('keydown', function (e) {
      if (!openState || openState.btn !== btn || st.view !== 'days') return;
      var t = e.target.closest('[data-iso]'); if (!t) return;
      var p = parse(t.dataset.iso), dt = new Date(p.y, p.m, p.d), delta = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7}[e.key];
      if (delta === undefined && e.key !== 'PageUp' && e.key !== 'PageDown') return;
      e.preventDefault();
      if (delta !== undefined) dt.setDate(dt.getDate() + delta); else dt.setMonth(dt.getMonth() + (e.key === 'PageUp' ? -1 : 1));
      var s = iso(dt.getFullYear(), dt.getMonth(), dt.getDate());
      if (!inRange(s)) return;
      st.y = dt.getFullYear(); st.m = dt.getMonth(); render(s);
    });
    btn.addEventListener('click', function () { if (openState && openState.btn === btn) closePop(false); else open(); });
    btn.addEventListener('keydown', function (e) {
      if ((e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') && !(openState && openState.btn === btn)) { e.preventDefault(); open(); }
    });
  }
  /* ---------- suggestions in the site's own theme (the browser's own list cannot follow dark mode) ----------
     - inputs with a <datalist> (city) use that list
     - name / email / venue / address remember what was sent before, on this browser only */
  var REMEMBER = {first_name: 1, email: 1, venue_name: 1, event_address: 1, event_city: 1};
  var sug = {box: null, inp: null, items: [], idx: -1};
  function sugStore(name) { try { return JSON.parse(localStorage.getItem('pp-sug:' + name) || '[]'); } catch (e) { return []; } }
  function sugList(inp) {
    var q = inp.value.trim().toLowerCase(), src = [];
    if (inp.dataset.ppList) { var dl = document.getElementById(inp.dataset.ppList); if (dl) src = $$('option', dl).map(function (o) { return o.value; }); }
    else src = sugStore(inp.dataset.sugKey || inp.name);
    return src.filter(function (v) { return v && v.toLowerCase() !== q && (!q || v.toLowerCase().indexOf(q) > -1); }).slice(0, 6);
  }
  function sugClose() { if (sug.box) { sug.box.hidden = true; sug.box.innerHTML = ''; } sug.inp = null; sug.items = []; sug.idx = -1; }
  function sugPlace() {
    if (!sug.box || !sug.inp) return;
    var r = sug.inp.getBoundingClientRect(), below = innerHeight - r.bottom, h = Math.min(sug.box.scrollHeight, 280);
    sug.box.style.left = r.left + 'px'; sug.box.style.width = r.width + 'px';
    sug.box.style.top = (below < h + 12 && r.top > h + 12 ? r.top - h - 6 : r.bottom + 6) + 'px';
  }
  function sugPick(v) {
    var inp = sug.inp; if (!inp) return;
    inp.value = v; inp.dispatchEvent(new Event('input', {bubbles: true})); inp.dispatchEvent(new Event('change', {bubbles: true})); sugClose();
  }
  function sugOpen(inp) {
    var list = sugList(inp);
    if (!list.length) { sugClose(); return; }
    if (!sug.box) { sug.box = el('div', 'pp-pop pp-sug'); sug.box.setAttribute('role', 'listbox'); sug.box.addEventListener('mousedown', function (e) { e.preventDefault(); }); document.body.appendChild(sug.box); }
    sug.inp = inp; sug.items = list; sug.idx = -1; sug.box.innerHTML = '';
    list.forEach(function (v, i) {
      var o = el('div', 'pp-opt'); o.setAttribute('role', 'option'); o.textContent = v;
      o.addEventListener('click', function () { sugPick(v); });
      o.addEventListener('mouseenter', function () { sugMark(i); });
      sug.box.appendChild(o);
    });
    sug.box.hidden = false; sugPlace();
  }
  function sugMark(i) { sug.idx = i; $$('.pp-opt', sug.box).forEach(function (o, j) { o.classList.toggle('act', j === i); }); }
  function enhanceSuggest(scope) {
    $$('input[list]', scope).forEach(function (inp) {
      if (inp.dataset.pp) return; inp.dataset.pp = '1'; inp.dataset.ppList = inp.getAttribute('list'); inp.removeAttribute('list'); inp.setAttribute('autocomplete', 'off'); inp.dataset.sug = '1';
    });
    $$('input[type=text],input[type=email],input:not([type]),textarea', scope).forEach(function (inp) {
      var onLogin = inp.name === 'username' && /\/login\/?$/.test(location.pathname);        // the login box remembers usernames; sign-up never does
      if (inp.dataset.sug || !(REMEMBER[inp.name] || onLogin) || inp.classList.contains('no-pp')) return;
      if (onLogin) inp.dataset.sugKey = 'login_username';
      inp.dataset.sug = '1'; inp.setAttribute('autocomplete', 'off');
    });
  }
  document.addEventListener('focusin', function (e) { var t = e.target; if (t && t.dataset && t.dataset.sug) sugOpen(t); });
  document.addEventListener('input', function (e) { var t = e.target; if (t && t.dataset && t.dataset.sug) sugOpen(t); });
  document.addEventListener('focusout', function (e) { if (e.target === sug.inp) setTimeout(function () { if (document.activeElement !== sug.inp) sugClose(); }, 120); });
  document.addEventListener('keydown', function (e) {
    if (!sug.box || sug.box.hidden || e.target !== sug.inp) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); sugMark((sug.idx + 1) % sug.items.length); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sugMark((sug.idx - 1 + sug.items.length) % sug.items.length); }
    else if (e.key === 'Enter' && sug.idx > -1) { e.preventDefault(); sugPick(sug.items[sug.idx]); }
    else if (e.key === 'Escape') sugClose();
  });
  addEventListener('scroll', function () { if (sug.box && !sug.box.hidden) sugPlace(); }, {passive: true, capture: true});
  addEventListener('resize', function () { if (sug.box && !sug.box.hidden) sugPlace(); });
  document.addEventListener('submit', function (e) {
    $$('input[data-sug],textarea[data-sug]', e.target).forEach(function (inp) {
      var key = inp.dataset.sugKey || inp.name, v = inp.value.trim(); if (!(REMEMBER[inp.name] || inp.dataset.sugKey) || v.length < 2 || v.length > 160) return;
      var list = sugStore(key).filter(function (x) { return x.toLowerCase() !== v.toLowerCase(); }); list.unshift(v);
      try { localStorage.setItem('pp-sug:' + key, JSON.stringify(list.slice(0, 8))); } catch (x) {}
    });
  }, true);

  /* show / hide password (an eye button) on every password box: login, sign up, reset, change */
  function enhancePassword(inp) {
    if (inp.dataset.pw || inp.classList.contains('no-pp')) return;
    inp.dataset.pw = '1';
    var wrap = el('div', 'pw-wrap'); inp.parentNode.insertBefore(wrap, inp); wrap.appendChild(inp);
    var b = el('button', 'pw-eye', '<i class="bi bi-eye-slash" aria-hidden="true"></i>');
    b.type = 'button'; b.setAttribute('aria-label', 'Show password'); b.setAttribute('aria-pressed', 'false'); b.title = 'Show password';
    b.addEventListener('mousedown', function (e) { e.preventDefault(); });          // keep the cursor in the box
    b.addEventListener('click', function () {
      var show = inp.type === 'password';
      inp.type = show ? 'text' : 'password';
      b.setAttribute('aria-pressed', show ? 'true' : 'false');
      b.setAttribute('aria-label', show ? 'Hide password' : 'Show password'); b.title = show ? 'Hide password' : 'Show password';
      $('i', b).className = 'bi ' + (show ? 'bi-eye' : 'bi-eye-slash');
      inp.focus();
    });
    wrap.appendChild(b);
  }
  /* album page: switch the example spread between 1, 2 and 4 photos a page */
  function enhanceAlbum(scope) {
    $$('[data-spread]', scope).forEach(function (box) {
      if (box.dataset.pp) return; box.dataset.pp = '1';
      var sp = $('.spread', box), btns = $$('.alb-layouts button', box);
      btns.forEach(function (b) { b.addEventListener('click', function () {
        btns.forEach(function (x) { x.classList.toggle('on', x === b); });
        sp.className = 'spread ' + b.dataset.l;
      }); });
    });
  }
  function enhance(scope) {
    enhanceAlbum(scope);
    $$('input[type="password"]', scope).forEach(enhancePassword);
    enhanceSuggest(scope);
    $$('select.form-select', scope).forEach(enhanceSelect);
    $$('input[type="date"]', scope).forEach(enhanceDate);
  }

  /* ---------- home page: rail, hero parallax, filter chips, step tabs (nothing here holds the scroll) ---------- */
  function homeBits() {
    var rail = $('#rail'), bento = $('.bento'), how = $('#how');
    if (!rail && !bento && !how) return;
    root.classList.add('pp-tabs');
    ppOnLeave(function () { root.classList.remove('pp-tabs'); var hh=$('.hero'); if (hh) hh.style.removeProperty('--hp'); if (rail && rail.parentNode) rail.parentNode.removeChild(rail); });
    // filter chips for the photographer grid, built from the tiles themselves
    if (bento) {
      var tiles = $$('.fr', bento), chips = $('.chips'), seen = {}, order = [];
      tiles.forEach(function (t) { var k = t.dataset.s; if (k && !seen[k]) { seen[k] = t.dataset.l; order.push(k); } });
      function apply(key) {
        var n = 0;
        tiles.forEach(function (t) {
          var show = !key || t.dataset.s === key;
          t.classList.toggle('out', !show);
          if (show) { n++; if (!calm) { t.classList.remove('pop'); void t.offsetWidth; t.classList.add('pop'); } }
        });
        bento.dataset.n = Math.min(n, 8); bento.classList.toggle('big', n >= 4);
      }
      if (chips && order.length > 1) {
        order.forEach(function (k) { var b = el('button', '', esc(seen[k])); b.type = 'button'; b.dataset.f = k; chips.appendChild(b); });
        chips.addEventListener('click', function (e) {
          var b = e.target.closest('button'); if (!b) return;
          $$('button', chips).forEach(function (x) { x.classList.toggle('on', x === b); });
          apply(b.dataset.f);
        });
      } else if (chips) chips.hidden = true;
      apply('');
    }
    // how it works tabs
    if (how) {
      var tabs = $$('.how-tabs button', how), steps = $$('.how-step', how);
      tabs.forEach(function (t, i) {
        t.addEventListener('click', function () {
          tabs.forEach(function (x, j) { x.classList.toggle('on', i === j); });
          steps.forEach(function (x, j) { x.classList.toggle('on', i === j); });
        });
      });
    }
    // rail and hero parallax
    var secs = ['top', 'browse', 'styles', 'how', 'join'].map(function (id) { return document.getElementById(id); });
    var railLinks = rail ? $$('a', rail) : [];
    if (rail) document.body.appendChild(rail);
    var raf = 0, heroEl = $('.hero');
    function frame() {
      raf = 0;
      if (!calm && heroEl && scrollY < innerHeight * 1.2) heroEl.style.setProperty('--hp', clamp(scrollY / (innerHeight * .9)).toFixed(3));
      if (rail) {
        var cur = 0;
        secs.forEach(function (s, i) { if (s && s.getBoundingClientRect().top < innerHeight * .45) cur = i; });
        railLinks.forEach(function (l, i) { l.classList.toggle('on', i === cur); });
      }
    }
    function queue() { if (!raf) raf = requestAnimationFrame(frame); }
    ppListen(window, 'scroll', queue, {passive: true});
    ppListen(window, 'resize', queue);
    frame();
  }

  /* ---------- notifications: toast, never auto-open ---------- */
  /* the bell panel opens by itself for a new notification and stays open until the user closes it */
  var retryTimer = 0, dim = null;
  function bellParts() { var b = $('#bell'); return b ? {bell: b, btn: $('.bell-btn', b), menu: $('.bell-menu', b)} : null; }
  function busyNow() {
    var a = document.activeElement;
    return !!(openState || $('.modal.show') || (a && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable) && a.type !== 'submit' && a.type !== 'button'));
  }
  function bellDropdown(p) { return window.bootstrap ? bootstrap.Dropdown.getOrCreateInstance(p.btn) : null; }
  function openBell(tries) {
    var p = bellParts(); if (!p || !p.btn) return;
    clearTimeout(retryTimer);
    if (p.menu.classList.contains('show')) return;                       // already open: the list was refreshed above
    if (busyNow() && (tries || 0) < 30) { retryTimer = setTimeout(function () { openBell((tries || 0) + 1); }, 2000); return; }   // wait until the user is not typing
    var dd = bellDropdown(p); if (!dd) return;
    autoOpened = true;
    dd.show();                                                           // shows what happened, then tucks itself away unless the user touches it
  }
  var autoOpened = false, closeT = 0;
  function keepOpen() { clearTimeout(closeT); autoOpened = false; }
  function armClose() {
    clearTimeout(closeT);
    if (!autoOpened) return;
    closeT = setTimeout(function () {
      var p = bellParts(); if (!p) return;
      if (p.menu.matches('.selecting')) { keepOpen(); return; }
      autoOpened = false; try { bellDropdown(p).hide(); } catch (x) {}
    }, 6000);
  }
  document.addEventListener('pointerdown', function (e) { if (e.target.closest && e.target.closest('.bell-menu')) keepOpen(); }, true);
  document.addEventListener('pointerover', function (e) { if (autoOpened && e.target.closest && e.target.closest('.bell-menu')) keepOpen(); }, true);
  document.addEventListener('focusin', function (e) { if (autoOpened && e.target.closest && e.target.closest('.bell-menu')) keepOpen(); });
  document.addEventListener('keydown', function () { if (autoOpened && $('.bell-menu.show')) keepOpen(); }, true);
  document.addEventListener('shown.bs.dropdown', function (e) {
    var bell = $('#bell'); if (!bell || !bell.contains(e.target) || innerWidth >= 992) return;
    if (!dim) { dim = el('div', 'bell-dim'); dim.addEventListener('click', function () { var p = bellParts(); if (p) try { bellDropdown(p).hide(); } catch (x) {} }); }
    document.body.appendChild(dim);
  });
  document.addEventListener('shown.bs.dropdown', function (e) { var b = $('#bell'); if (b && b.contains(e.target)) armClose(); });
  document.addEventListener('hidden.bs.dropdown', function () { clearTimeout(closeT); autoOpened = false; var m = $('.bell-menu'); if (m) selectMode(m, false); if (dim && dim.parentNode) dim.parentNode.removeChild(dim); });
  document.addEventListener('click', function (e) {
    if (e.target.closest('.bell-close')) { var p = bellParts(); if (p) try { bellDropdown(p).hide(); } catch (x) {} }
  });

  /* ---------- select mode: tick several notifications, then clear / delete / mark read together ---------- */
  function csrf() { var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/); return m ? decodeURIComponent(m[1]) : ''; }
  function selectMode(scope, on) {
    if (!scope) return;
    scope.classList.toggle('selecting', on);
    var bar = $('.bell-selbar, .notif-selbar', scope.closest('#bell') || document);
    bar = scope.matches('.bell-menu') ? $('.bell-selbar', scope) : $('#pSelBar');
    if (bar) bar.hidden = !on;
    if (!on) { $$('input[type=checkbox]', scope).forEach(function (c) { c.checked = false; }); }
    if (bar && !on) $$('input[type=checkbox]', bar).forEach(function (c) { c.checked = false; });
    selSync(scope);
  }
  function selSync(scope) {
    var rows = $$('.bell-check input, .notif-check input', scope), n = rows.filter(function (c) { return c.checked; }).length;
    var bar = scope.matches('.bell-menu') ? $('.bell-selbar', scope) : $('#pSelBar');
    if (!bar) return;
    $$('button[data-sel=clear],button[data-psel=clear],button[data-psel=read],button[data-psel=delete]', bar).forEach(function (b) { b.disabled = !n; var c = $('b', b); if (c) c.textContent = n; });
    var all = $('input[data-sel=all],input[data-psel=all]', bar); if (all) all.checked = rows.length > 0 && n === rows.length;
  }
  function ticked(scope) { return $$('.bell-check input, .notif-check input', scope).filter(function (c) { return c.checked; }).map(function (c) { return c.value; }); }
  function runBulk(scope, action, url) {
    var ids = ticked(scope); if (!ids.length) return;
    var fd = new FormData(); ids.forEach(function (i) { fd.append('ids', i); }); fd.append('action', action);
    fetch(url, {method: 'POST', body: fd, credentials: 'same-origin', headers: {'X-Requested-With': 'fetch', 'X-CSRFToken': csrf()}})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) { if (window.ppToast) ppToast('danger', (d && d.error) || 'Could not update.'); return; }
        setBadge(d.unread);
        var msg = {delete: 'Deleted', clear: 'Cleared', read: 'Marked as read'}[action] + ' (' + ids.length + ')';
        if (window.ppToast) ppToast('success', msg, {quiet: true});
        if (scope.matches('.bell-menu')) {
          var rows = ids.map(function (i) { var c = $('.bell-check input[value="' + i + '"]', scope); return c && c.closest('.bell-row'); });
          var after = function () {
            if (!$('.bell-row', scope)) {
              var list = $('.bell-list', scope), b = $('#bell');
              list.innerHTML = '<div class="bell-empty"><i class="bi bi-bell-slash" aria-hidden="true"></i><span>You are all caught up.</span><a href="' + b.dataset.listUrl + '?filter=cleared">View cleared</a></div>';
              selectMode(scope, false); $$('[data-sel=start],form', $('.bell-head-actions', scope)).forEach(function (x) { x.hidden = true; });
            } else selSync(scope);
          };
          if (window.ppSlideOut) { ppSlideOut(rows, after); } else { rows.forEach(function (r) { if (r) r.remove(); }); after(); }
        } else {
          var prow = ids.map(function (i) { var c = $('input[value="' + i + '"]', scope); return c && c.closest('.n-row, .notif-row, li, tr, .panel'); }).filter(function (r) { return r && r !== scope; });
          var go = function () { selectMode(scope, false); dirty = false; liveRefresh(true); };
          if (window.ppSlideOut && action !== 'read' && prow.length) { ppSlideOut(prow, go); } else { go(); }
        }
      }).catch(function () { if (window.ppToast) ppToast('danger', 'No connection. Try again.'); });
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('[data-sel],[data-psel]'); if (!b) return;
    var panel = b.hasAttribute('data-sel'), scope = panel ? $('.bell-menu') : $('#pList'), what = b.dataset.sel || b.dataset.psel;
    if (!scope) return;
    if (what === 'all') return;                                   // handled on change
    e.preventDefault();
    if (what === 'start') selectMode(scope, true);
    else if (what === 'stop') selectMode(scope, false);
    else runBulk(scope, what, $('#bell').dataset.bulkUrl);
  });
  document.addEventListener('change', function (e) {
    var t = e.target; if (!t.matches) return;
    var scope = t.closest('.bell-menu') || t.closest('#pSelBar, #pList') && $('#pList'); if (!scope) return;
    if (t.matches('[data-sel=all],[data-psel=all]')) { $$('.bell-check input, .notif-check input', scope).forEach(function (c) { c.checked = t.checked; }); }
    if (t.closest('.bell-check, .notif-check, .bell-selbar, .notif-selbar')) selSync(scope);
  });
  /* in select mode a tap on the row ticks it instead of opening it */
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('.selecting .bell-item, .selecting .notif-main'); if (!a) return;
    e.preventDefault(); e.stopPropagation();
    var row = a.closest('.bell-row, .notif-row'), c = $('input[type=checkbox]', row);
    if (c) { c.checked = !c.checked; selSync(row.closest('.bell-menu') || $('#pList')); }
  }, true);

  var pollAt = 0, pollTimer = 0, lastSwap = 0;
  function userId() { var m = $('meta[name="pp-user"]'); return m ? m.content : ''; }
  function setBadge(n) {
    var badge = $('#bellBadge'); if (!badge) return;
    badge.hidden = !n; badge.textContent = n > 9 ? '9+' : n;
  }
  var lastActive = Date.now(), dirty = false;
  ['pointerdown', 'keydown', 'scroll', 'touchstart'].forEach(function (ev) { addEventListener(ev, function () { lastActive = Date.now(); }, {passive: true, capture: true}); });
  document.addEventListener('input', function (e) { if (e.target.closest && e.target.closest('main form')) dirty = true; }, true);
  /* quiet: only remember "everything up to here is already seen" (used after the user's own action, so it never pops the panel) */
  function poll(force, quiet) {
    var bell = $('#bell'); if (!bell || !window.fetch || (document.hidden && !force)) return;
    var now = Date.now();
    if (!force && !quiet && now - pollAt < 3000) return;
    if (!force && !quiet && now - lastActive > 120000 && now - pollAt < 30000) return;   // idle tab: slow down
    pollAt = now;
    var key = 'pp-nid:' + userId(), after = null;
    try { after = sessionStorage.getItem(key); } catch (e) {}
    var asking = !quiet && after !== null;
    var url = bell.dataset.countUrl + (asking ? '?after=' + encodeURIComponent(after) + '&next=' + encodeURIComponent(location.pathname + location.search) : '');
    fetch(url, {credentials: 'same-origin', headers: {'X-Requested-With': 'fetch'}})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        setBadge(d.unread);
        var prevVer = bell.dataset.ver; if (d.ver) bell.dataset.ver = d.ver;
        var stateMoved = !!(d.ver && prevVer && d.ver !== prevVer && Date.now() - lastSwap > 5000);
        try { if (after === null || quiet || d.max_id > +after) sessionStorage.setItem(key, String(d.max_id)); } catch (e) {}
        if (!asking || !d.latest || !d.latest.length) { if (stateMoved && !quiet) liveRefresh(); return; }
        if (d.html) { var menu = $('.bell-menu', bell); if (menu && !menu.classList.contains('selecting')) menu.innerHTML = d.html; }
        liveRefresh().then(function () { openBell(); });       // the page catches up first, then the panel shows what happened
      }).catch(function () {});
  }
  function startPolling() {
    clearInterval(pollTimer);
    if (!$('#bell')) return;
    pollTimer = setInterval(function () { poll(false); }, 10000);
  }
  /* when something changes on the server the page you are looking at is refreshed in place (no reload, scroll stays) */
  var chip = null;
  function hideChip() { if (chip && chip.parentNode) chip.parentNode.removeChild(chip); chip = null; }
  function showChip() {
    if (chip) return;
    chip = el('div', 'live-chip', '<i class="bi bi-arrow-repeat" aria-hidden="true"></i><span>This page has new updates</span><button type="button">Refresh</button>');
    $('button', chip).addEventListener('click', function () { dirty = false; hideChip(); liveRefresh(true); });
    document.body.appendChild(chip);
  }
  function liveRefresh(force) {
    var m = $('main');
    if (!soft || !m) return Promise.resolve(false);
    var blocked = !force && (dirty || busyNow() || /\/pay\/?$/.test(location.pathname));
    if (blocked) { showChip(); return Promise.resolve(false); }
    var url = location.href;
    return fetch(url, {credentials: 'same-origin', headers: {'X-PP-Soft': '1'}}).then(function (r) {
      if ((r.headers.get('content-type') || '').indexOf('text/html') < 0 || r.url.split('#')[0] !== url.split('#')[0]) return false;
      return r.text().then(function (h) {
        if (location.href !== url) return false;
        if (!force && (dirty || busyNow())) { showChip(); return false; }
        return swap(h, r.url, {live: true});
      });
    }).catch(function () { return false; });
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(false); });
  addEventListener('focus', function () { poll(false); });
  addEventListener('online', function () { poll(true); });

  /* ---------- soft navigation ---------- */
  var soft = !!(window.fetch && window.history && history.pushState && window.DOMParser && window.AbortController);
  var ctrl = null, loadingT = 0;
  function busy(on) {
    clearTimeout(loadingT);
    if (on) loadingT = setTimeout(function () { root.classList.add('pp-loading'); }, 90);
    else root.classList.remove('pp-loading');
  }
  function stashToasts(doc) {
    var list = [];
    $$('#toastHost .toast', doc).forEach(function (t) {
      var m = t.querySelector('.pp-toast-msg'); if (!m) return;
      var type = (/t-(\w+)/.exec(t.className) || [])[1] || 'info';
      list.push({type: type, text: m.textContent.trim()});
    });
    if (list.length) { try { sessionStorage.setItem('pp-toasts', JSON.stringify(list)); } catch (e) {} }
  }
  function showStashed() {
    var raw = null;
    try { raw = sessionStorage.getItem('pp-toasts'); sessionStorage.removeItem('pp-toasts'); } catch (e) {}
    if (!raw) return;
    try { JSON.parse(raw).forEach(function (t) { window.ppToast(t.type, t.text); }); } catch (e) {}
  }
  function runScripts(scope) {
    $$('script', scope).forEach(function (old) {
      if (old.src) return;
      var s = document.createElement('script'); s.text = old.textContent;
      old.parentNode.replaceChild(s, old);
    });
  }
  function initPage(first) {
    enhance($('main'));
    homeBits();
    if (!first && window.ppReveal) window.ppReveal();
  }
  function swap(html, finalUrl, opts) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    var newMain = $('main', doc);
    if (!newMain) return false;
    var mine = $('meta[name="pp-user"]'), theirs = $('meta[name="pp-user"]', doc);
    if ((mine ? mine.content : '') !== (theirs ? theirs.content : '')) {      // logged in or out: the whole navbar changes, so load it properly
      stashToasts(doc); location.assign(finalUrl); return true;
    }
    var keepY = scrollY;
    leave(); closePop(false);
    var cur = location.href.split('#')[0], dest = finalUrl.split('#')[0];
    if (opts.push && !opts.live) {
      try { history.replaceState({scroll: scrollY}, ''); } catch (e) {}
      if (cur === dest) history.replaceState({scroll: 0}, '', finalUrl); else history.pushState({scroll: 0}, '', finalUrl);
    }
    document.title = doc.title;
    var oldMain = $('main');
    var adopted = document.adoptNode(newMain);
    if (opts.live) adopted.classList.add('live');
    oldMain.parentNode.replaceChild(adopted, oldMain);
    runScripts(adopted);
    dirty = false; hideChip();
    // the bell panel and badge come from the fresh page
    var bm = $('.bell-menu'), nm = $('.bell-menu', doc);
    if (bm && nm && !bm.classList.contains('selecting')) bm.innerHTML = nm.innerHTML;
    var nb = $('#bellBadge', doc), cb = $('#bellBadge'); if (nb && cb) { cb.hidden = nb.hidden; cb.textContent = nb.textContent; }
    $$('.bell-menu input[name="next"]').forEach(function (i) { i.value = location.pathname + location.search; });
    // messages from this response
    var host = $('#toastHost');
    $$('#toastHost .toast', doc).forEach(function (t) { var n = document.adoptNode(t); host.appendChild(n); if (window.ppToastShow) window.ppToastShow(n); });
    var nav = $('#nav'); if (!opts.live && nav && nav.classList.contains('show') && window.bootstrap) { try { bootstrap.Collapse.getOrCreateInstance(nav).hide(); } catch (e) {} }
    if (window.ppHere) window.ppHere();
    lastSwap = Date.now(); sugClose();
    initPage();
    if (opts.live) { scrollTo({top: keepY, left: 0, behavior: 'instant'}); return true; }
    var hash = finalUrl.indexOf('#') > -1 ? finalUrl.slice(finalUrl.indexOf('#')) : '';
    var target = hash.length > 1 ? document.getElementById(decodeURIComponent(hash.slice(1))) : null;
    var instant = {top: 0, left: 0, behavior: 'instant'};
    if (target) { target.scrollIntoView(); }
    else if (opts.restore !== undefined) { instant.top = opts.restore; scrollTo(instant); }
    else if (opts.post && cur === dest) { instant.top = keepY; scrollTo(instant); }      // an action on this same page (arrived, confirm, complete...): stay where you were
    else scrollTo(instant);
    adopted.setAttribute('tabindex', '-1'); try { adopted.focus({preventScroll: true}); } catch (e) {}
    poll(opts.post, opts.post);       // after your own action: just note what is already there, no panel
    return true;
  }
  /* pages are fetched a moment before the click (on hover / touch), so the click itself is nearly instant */
  var pre = {};
  function prefetch(href) {
    if (!soft || pre[href] || document.hidden) return;
    var keys = Object.keys(pre); if (keys.length > 6) delete pre[keys[0]];
    pre[href] = {t: Date.now(), p: fetch(href, {credentials: 'same-origin', headers: {'X-PP-Soft': '1'}}).then(function (r) {
      var type = r.headers.get('content-type') || '';
      if (!r.ok || type.indexOf('text/html') < 0) return null;
      return r.text().then(function (h) { return {html: h, url: r.url || href}; });
    }).catch(function () { return null; })};
  }
  function takePrefetch(href) {
    var e = pre[href]; delete pre[href];
    return e && Date.now() - e.t < 15000 ? e.p : null;
  }
  function softLink(e) {
    var a = e.target.closest && e.target.closest('a[href]'); if (!a) return null;
    if ((a.target && a.target !== '_self') || a.hasAttribute('download') || a.hasAttribute('data-no-soft') || a.hasAttribute('data-bs-toggle')) return null;
    var u; try { u = new URL(a.href, location.href); } catch (err) { return null; }
    if (!internal(u) || !/^https?:$/.test(u.protocol) || /(logout|\/open\/?$)/.test(u.pathname)) return null;
    if (u.pathname === location.pathname && u.search === location.search) return null;
    return u;
  }
  var hoverT = 0;
  document.addEventListener('pointerover', function (e) {
    if (e.pointerType === 'touch') return;
    var u = softLink(e); if (!u) return;
    clearTimeout(hoverT); hoverT = setTimeout(function () { prefetch(u.href); }, 80);
  }, {passive: true});
  document.addEventListener('pointerdown', function (e) { var u = softLink(e); if (u) prefetch(u.href); }, {passive: true, capture: true});
  function request(url, init, opts) {
    if (ctrl) ctrl.abort();
    ctrl = new AbortController();
    var mine = ctrl;
    busy(true);
    init = init || {};
    init.credentials = 'same-origin'; init.signal = mine.signal; init.headers = {'X-PP-Soft': '1'};
    var isPost = init.method === 'POST';
    var hit = !isPost && opts && opts.push ? takePrefetch(url) : null;
    if (hit) {
      return hit.then(function (d) {
        if (mine !== ctrl) return;
        if (!d) { location.assign(url); return; }
        if (!swap(d.html, d.url, opts)) location.assign(url);
      }).then(function () { if (mine === ctrl) { busy(false); ctrl = null; } });
    }
    return fetch(url, init).then(function (r) {
      var type = r.headers.get('content-type') || '';
      if (type.indexOf('text/html') < 0) {
        if (!isPost) { location.assign(url); return; }
        if (window.ppToast) window.ppToast('danger', 'That did not work. Please reload the page and try again.');
        return;
      }
      return r.text().then(function (html) {
        if (mine !== ctrl) return;
        if (!swap(html, r.url || url, opts)) { if (!isPost) location.assign(url); }
      });
    }).catch(function (err) {
      if (err && err.name === 'AbortError') return;
      if (!isPost) { location.assign(url); return; }
      if (window.ppToast) window.ppToast('danger', 'We could not reach the server. Check your connection and try again.');
    }).then(function () { if (mine === ctrl) { busy(false); ctrl = null; } });
  }
  function internal(u) { return u.origin === location.origin && !/^\/(admin|static|media)(\/|$)/.test(u.pathname); }

  if (soft) {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
    document.addEventListener('click', function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      var a = e.target.closest('a[href]'); if (!a) return;
      if ((a.target && a.target !== '_self') || a.hasAttribute('download') || a.hasAttribute('data-no-soft') || a.hasAttribute('data-bs-toggle')) return;
      var u; try { u = new URL(a.href, location.href); } catch (err) { return; }
      if (!internal(u) || !/^https?:$/.test(u.protocol)) return;
      if (u.pathname === location.pathname && u.search === location.search && u.hash) return;     // same-page anchor: the browser scrolls
      e.preventDefault();
      request(u.href, {method: 'GET'}, {push: true});
    });
    document.addEventListener('submit', function (e) {
      var f = e.target;
      if (e.defaultPrevented || !(f instanceof HTMLFormElement) || f.hasAttribute('data-no-soft') || f.hasAttribute('data-clear') || (f.target && f.target !== '_self')) return;
      var u; try { u = new URL(f.getAttribute('action') || location.href, location.href); } catch (err) { return; }
      if (!internal(u)) return;
      var method = ((e.submitter && e.submitter.getAttribute('formmethod')) || f.method || 'get').toUpperCase();
      var fd;
      try { fd = new FormData(f, e.submitter || undefined); } catch (err) { fd = new FormData(f); if (e.submitter && e.submitter.name) fd.append(e.submitter.name, e.submitter.value); }
      e.preventDefault();
      var btn = e.submitter, was = btn ? btn.disabled : false;
      if (btn) { btn.disabled = true; btn.classList.add('pp-busy'); }
      function done() { if (btn) { btn.disabled = was; btn.classList.remove('pp-busy'); } }
      if (method === 'GET') {
        var q = new URLSearchParams();
        fd.forEach(function (v, k) { if (typeof v === 'string' && v !== '') q.append(k, v); });
        u.search = q.toString();
        request(u.href, {method: 'GET'}, {push: true}).then(done, done);
      } else {
        request(u.href, {method: 'POST', body: fd}, {push: true, post: true}).then(done, done);
      }
    });
    var here = location.pathname + location.search;
    window.addEventListener('popstate', function (e) {
      var now = location.pathname + location.search;
      if (now === here) return;                       // only the #anchor changed: the browser handles it
      here = now;
      request(location.href, {method: 'GET'}, {push: false, restore: e.state && typeof e.state.scroll === 'number' ? e.state.scroll : 0});
    });
    try { history.replaceState({scroll: scrollY}, ''); } catch (e) {}
    window.ppHere = function () { here = location.pathname + location.search; };
  }

  /* ---------- start ---------- */
  function start() {
    mountPop();
    showStashed();
    initPage(true);
    startPolling();
    poll(true);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
