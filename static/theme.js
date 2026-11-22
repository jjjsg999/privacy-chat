// Light / dark / follow-the-system theme. Loaded in <head> so the theme is applied before the page paints.
(() => {
  const KEY = 'privacy-llm-theme', ORDER = ['auto', 'light', 'dark'];
  const read = () => { try { const value = localStorage.getItem(KEY); return ORDER.includes(value) ? value : 'auto'; } catch (_) { return 'auto'; } };
  const apply = theme => { if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme; else delete document.documentElement.dataset.theme; };
  apply(read());
  addEventListener('DOMContentLoaded', () => {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    const button = document.createElement('button');
    button.type = 'button'; button.id = 'theme-toggle';
    const show = theme => { button.textContent = `Theme: ${theme[0].toUpperCase()}${theme.slice(1)}`; button.title = 'Switch between following the system, light and dark'; };
    show(read());
    button.onclick = () => {
      const next = ORDER[(ORDER.indexOf(read()) + 1) % ORDER.length];
      try { localStorage.setItem(KEY, next); } catch (_) { /* storage blocked: the choice lasts until reload */ }
      apply(next); show(next);
    };
    sidebar.append(button);
  });
})();
