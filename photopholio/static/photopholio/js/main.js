// Gallery lightbox: clicking a thumbnail opens the Bootstrap modal with the full image.
document.addEventListener('click', function (e) {
  const btn = e.target.closest('[data-lightbox]');
  if (!btn) return;
  document.getElementById('lightboxImg').src = btn.dataset.src;
  document.getElementById('lightboxImg').alt = btn.dataset.caption || '';
  document.getElementById('lightboxCaption').textContent = btn.dataset.caption || '';
});

// Light / dark switch. The page already has its theme (set in <head>); this only handles the button.
(function () {
  var button = document.getElementById('themeToggle');
  if (!button) return;
  function current() { return document.documentElement.getAttribute('data-bs-theme') === 'dark' ? 'dark' : 'light'; }
  function label() {
    var dark = current() === 'dark';
    button.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
    button.setAttribute('aria-pressed', dark ? 'true' : 'false');
  }
  label();
  function apply(next) {
    document.documentElement.setAttribute('data-bs-theme', next);
    var cs = document.querySelector('meta[name="color-scheme"]'); if (cs) cs.setAttribute('content', next);
    try { localStorage.setItem('pp-theme', next); } catch (e) {}
    label();
  }
  button.addEventListener('click', function () {
    var next = current() === 'dark' ? 'light' : 'dark';
    var root = document.documentElement;
    var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (document.startViewTransition && !calm) {
      document.startViewTransition(function () { apply(next); });   // a short crossfade: one paint, no per-element transitions
      return;
    }
    apply(next);
  });
})();

// Toasts: every Django message arrives as a toast. ppToast('success', 'text') shows one from script too.
(function () {
  var host = document.getElementById('toastHost');
  if (!host || !window.bootstrap) return;
  var icons = {success: 'bi-check-circle-fill', danger: 'bi-x-octagon-fill', warning: 'bi-exclamation-triangle-fill', info: 'bi-info-circle-fill',
    accepted: 'bi-patch-check-fill', money: 'bi-wallet2', neutral: 'bi-box-arrow-right'};
  function show(el) {
    var delay = parseInt(el.getAttribute('data-bs-delay'), 10) || 5500;
    el.style.setProperty('--pp-delay', delay + 'ms');
    var t = bootstrap.Toast.getOrCreateInstance(el, {autohide: false});
    var gone = false;
    function dismiss() {                         // slide out first, then let Bootstrap hide it
      if (gone) return; gone = true;
      el.classList.add('leaving');
      setTimeout(function () { t.hide(); }, 280);
    }
    var bar = el.querySelector('.pp-toast-bar');
    if (bar) bar.addEventListener('animationend', dismiss);   // the bar is the timer, so hovering (pause) holds the toast
    else setTimeout(dismiss, delay);
    var close = el.querySelector('.btn-close');
    if (close) close.addEventListener('click', function (e) { e.stopImmediatePropagation(); e.preventDefault(); dismiss(); }, true);
    el.addEventListener('mouseenter', function () { el.classList.add('paused'); });
    el.addEventListener('mouseleave', function () { el.classList.remove('paused'); });
    el.addEventListener('hidden.bs.toast', function () { el.remove(); });
    el.addEventListener('animationend', function (e) { if (e.target === el) el.classList.remove('pp-enter'); });
    el.classList.add('pp-enter');
    t.show();
  }
  Array.prototype.forEach.call(host.querySelectorAll('.toast'), show);
  icons.notify = 'bi-bell-fill';
  window.ppToastShow = show;
  // opts: {delay: ms, href: link for the whole message, title: bold first line}
  window.ppToast = function (type, text, opts) {
    opts = opts || {};
    type = icons[type] ? type : 'info';
    var el = document.createElement('div');
    el.className = 'toast pp-toast t-' + type;
    el.setAttribute('role', type === 'danger' ? 'alert' : 'status');
    el.setAttribute('data-bs-delay', String(opts.delay || (type === 'danger' || type === 'warning' ? 9000 : 5500)));
    el.innerHTML = '<div class="toast-body"><i class="bi ' + icons[type] + ' pp-toast-ico" aria-hidden="true"></i><span class="pp-toast-msg"></span>' +
      '<button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button></div><div class="pp-toast-bar"></div>';
    var msg = el.querySelector('.pp-toast-msg');
    var holder = msg;
    if (opts.href) { holder = document.createElement('a'); holder.className = 'pp-toast-link'; holder.href = opts.href; msg.appendChild(holder); }
    if (opts.title) { var t = document.createElement('b'); t.textContent = opts.title; holder.appendChild(t); }
    if (text) { var s = document.createElement('span'); s.textContent = text; holder.appendChild(s); }
    host.appendChild(el);
    show(el);
  };
})();

// Clearing from the bell works without reloading the page. Without JavaScript the form still posts normally.
(function () {
  var bell = document.getElementById('bell');
  if (!bell || !window.fetch) return;
  var badge = document.getElementById('bellBadge');
  function setBadge(n) { if (badge) { badge.hidden = !n; badge.textContent = n > 9 ? '9+' : n; } }
  function empty() {
    var list = bell.querySelector('.bell-list');
    list.innerHTML = '<div class="bell-empty"><i class="bi bi-bell-slash" aria-hidden="true"></i><span>You are all caught up.</span><a href="' + bell.dataset.listUrl + '?filter=cleared">View cleared</a></div>';
    var head = bell.querySelector('.bell-head-actions'); if (head) head.hidden = true;
  }
  // Rows glide out to the right, then close up. The notification stays in History (it is only cleared, not deleted).
  window.ppSlideOut = function (rows, done) {
    rows = rows.filter(Boolean);
    var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!rows.length || calm) { rows.forEach(function (r) { r.remove(); }); if (done) done(); return; }
    rows.forEach(function (r, i) {
      r.style.setProperty('--d', (i * 60) + 'ms');
      r.style.maxHeight = r.offsetHeight + 'px';
      void r.offsetWidth;
      r.classList.add('bell-out');
    });
    setTimeout(function () { rows.forEach(function (r) { r.remove(); }); if (done) done(); }, 380 + rows.length * 60);
  };
  bell.addEventListener('submit', function (e) {
    var form = e.target.closest('form[data-clear]');
    if (!form) return;
    e.preventDefault();
    fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin',
                        headers: {'X-Requested-With': 'fetch'}})
      .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
      .then(function (d) {
        setBadge(d.unread);
        if (form.dataset.clear === 'all' || !d.remaining) { ppSlideOut(Array.prototype.slice.call(bell.querySelectorAll('.bell-row')), empty); return; }
        ppSlideOut([form.closest('.bell-row')]);
      })
      .catch(function () { form.submit(); });
  });
})();

// Small touches: sections that rise into view, numbers that count up.
window.ppReveal = function () {
  var calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (calm || !('IntersectionObserver' in window)) return;
  var groups = ['.film-head', '.bento .fr', '.kinds .kind', '.grid-cards .pcard-wrap', '.flow li', '.trust-row > div', '.cta-band', '.section-head', '.panel', '.pkg'];
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      if (!en.isIntersecting) return;
      en.target.classList.add('in'); io.unobserve(en.target);
      (function (el) { setTimeout(function () { el.classList.remove('reveal', 'in'); el.style.removeProperty('--d'); }, 1100); })(en.target);
      var n = en.target.querySelector('[data-count]') || (en.target.hasAttribute('data-count') ? en.target : null);
      if (n) countUp(n);
    });
  }, {threshold: .12});
  groups.forEach(function (sel) {
    Array.prototype.forEach.call(document.querySelectorAll(sel), function (el, i) {
      if (el.getBoundingClientRect().top < innerHeight * .9) return;   // already on screen: leave it be
      el.classList.add('reveal'); el.style.setProperty('--d', Math.min(i % 6, 5) * 70 + 'ms'); io.observe(el);
    });
  });
  function countUp(n) {
    var to = parseInt(n.getAttribute('data-count'), 10) || 0, pre = n.getAttribute('data-prefix') || '', suf = n.getAttribute('data-suffix') || '';
    if (!to) return;
    var t0 = null;
    function step(t) {
      if (t0 === null) t0 = t;
      var p = Math.min(1, (t - t0) / 1100), e = 1 - Math.pow(1 - p, 3);
      n.textContent = pre + Math.round(to * e) + suf;
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
};
window.ppReveal();


// "Clear all" on the notifications page: every row glides out to the right, then the page reloads with the list cleared.
document.addEventListener('submit', function (e) {
  var f = e.target;
  if (!f || !f.hasAttribute || !f.hasAttribute('data-slide-all') || f.dataset.sliding || !window.ppSlideOut) { return; }
  var rows = Array.prototype.slice.call(document.querySelectorAll('.notif-row'));
  if (!rows.length) { return; }
  e.preventDefault(); e.stopImmediatePropagation(); f.dataset.sliding = '1';
  ppSlideOut(rows, function () { f.submit(); });
}, true);
