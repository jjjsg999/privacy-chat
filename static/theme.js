// Light / dark / follow-the-system theme. Loaded in <head> so the theme is applied before the page paints.
(() => {
  const KEY = 'privacy-llm-theme', ORDER = ['auto', 'light', 'dark'];
  const read = () => { try { const value = localStorage.getItem(KEY); return ORDER.includes(value) ? value : 'auto'; } catch (_) { return 'auto'; } };
  const apply = theme => { if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme; else delete document.documentElement.dataset.theme; };
  apply(read());
  // Text size of the conversation (chat page). Applied here, before the page paints, so a saved size does not flash.
  const SIZE_KEY = 'privacy-llm-chat-text', SIZES = [0.875, 1, 1.125, 1.25, 1.375, 1.5];
  const readSize = () => { try { const value = Number(localStorage.getItem(SIZE_KEY)); return SIZES.includes(value) ? value : 1; } catch (_) { return 1; } };
  const applySize = size => document.documentElement.style.setProperty('--chat-zoom', String(size));
  let size = readSize();
  applySize(size);
  addEventListener('DOMContentLoaded', () => {
    const smaller = document.getElementById('text-smaller'), larger = document.getElementById('text-larger'), value = document.getElementById('text-size-value');
    if (!smaller || !larger || !value) return;
    const show = () => {
      value.textContent = `${Math.round(size * 100)}%`;
      // aria-disabled, not disabled, so keyboard focus is not lost when the last step is reached
      smaller.setAttribute('aria-disabled', String(size === SIZES[0])); larger.setAttribute('aria-disabled', String(size === SIZES[SIZES.length - 1]));
    };
    const step = direction => {
      size = SIZES[Math.min(SIZES.length - 1, Math.max(0, SIZES.indexOf(size) + direction))];
      try { localStorage.setItem(SIZE_KEY, String(size)); } catch (_) { /* storage blocked: lasts until reload */ }
      applySize(size); show();
    };
    smaller.onclick = () => step(-1); larger.onclick = () => step(1);
    show();
  });
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
