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
  button.addEventListener('click', function () {
    var next = current() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-bs-theme', next);
    try { localStorage.setItem('pp-theme', next); } catch (e) {}
    label();
  });
})();
