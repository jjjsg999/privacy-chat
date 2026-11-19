// Shared mobile navigation for the non-chat workspace pages.
(() => {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('toggle-sidebar');
  function open(value) {
    sidebar.classList.toggle('open', value);
    toggle.setAttribute('aria-expanded', String(value));
  }
  toggle.addEventListener('click', () => open(!sidebar.classList.contains('open')));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && sidebar.classList.contains('open')) {
      open(false); toggle.focus();
    }
  });
  document.addEventListener('click', event => {
    if (!sidebar.contains(event.target) && !toggle.contains(event.target)) open(false);
  });
})();
