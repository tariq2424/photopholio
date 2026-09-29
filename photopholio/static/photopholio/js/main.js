// Gallery lightbox: clicking a thumbnail opens the Bootstrap modal with the full image.
document.addEventListener('click', function (e) {
  const btn = e.target.closest('[data-lightbox]');
  if (!btn) return;
  document.getElementById('lightboxImg').src = btn.dataset.src;
  document.getElementById('lightboxImg').alt = btn.dataset.caption || '';
  document.getElementById('lightboxCaption').textContent = btn.dataset.caption || '';
});
