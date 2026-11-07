/* Admin panel: refresh by itself when something changes (a payment, a booking, an album step, a help message).
   It never reloads while you type, tick rows for an action, or edit a record. */
(function () {
  var path = location.pathname;
  var base = path.indexOf('/admin/') > -1 ? path.slice(0, path.indexOf('/admin/')) + '/admin/' : '/admin/';
  if (/\/(login|logout|password_change)\//.test(path)) { return; }
  var last = null, busy = false;
  function editing() {
    if (/\/(add|change|delete)\/?$/.test(path) || /\/\d+\/(change|delete)\//.test(path)) { return true; }
    var a = document.activeElement;
    if (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && a.type !== 'button' && a.type !== 'submit') { return true; }
    if (document.querySelector('.action-select:checked, #action-toggle:checked')) { return true; }
    if (document.querySelector('.modal.show')) { return true; }
    return false;
  }
  function tick() {
    if (busy || document.hidden) { return; }
    busy = true;
    fetch(base + 'live/', { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        busy = false;
        if (!d || !d.ver) { return; }
        if (last === null) { last = d.ver; return; }
        if (d.ver !== last) { if (editing()) { return; } last = d.ver; location.reload(); }
      })
      .catch(function () { busy = false; });
  }
  setInterval(tick, 5000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) { tick(); } });
  tick();
})();
