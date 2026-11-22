// Draggable sidebar width (desktop only). Drag the edge, use Left/Right arrows when focused, or double-click to reset.
(() => {
  const sidebar = document.getElementById('sidebar');
  if (!sidebar) return;
  const KEY = 'privacy-llm-sidebar-width', MIN = 200, MAX = 560, DEFAULT = 270;
  const clamp = value => Math.min(MAX, Math.max(MIN, Math.round(value)));
  let width = DEFAULT;
  try { const saved = Number(localStorage.getItem(KEY)); if (saved) width = clamp(saved); } catch (_) {}
  const handle = document.createElement('div');
  handle.id = 'sidebar-resizer'; handle.tabIndex = 0;
  handle.setAttribute('role', 'separator'); handle.setAttribute('aria-orientation', 'vertical');
  handle.setAttribute('aria-label', 'Resize sidebar'); handle.title = 'Drag to resize. Double-click to reset.';
  handle.setAttribute('aria-valuemin', MIN); handle.setAttribute('aria-valuemax', MAX);
  const set = value => {
    width = clamp(value);
    document.documentElement.style.setProperty('--sidebar-w', `${width}px`);
    handle.setAttribute('aria-valuenow', width);
  };
  const save = () => { try { localStorage.setItem(KEY, String(width)); } catch (_) {} };
  set(width);
  sidebar.append(handle);
  handle.addEventListener('pointerdown', event => {
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    const startX = event.clientX, startWidth = width;
    document.body.classList.add('resizing');
    const move = moveEvent => set(startWidth + moveEvent.clientX - startX);
    const stop = () => {
      handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', stop);
      handle.removeEventListener('pointercancel', stop);
      document.body.classList.remove('resizing'); save();
    };
    handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', stop); handle.addEventListener('pointercancel', stop);
  });
  handle.addEventListener('dblclick', () => { set(DEFAULT); save(); });
  handle.addEventListener('keydown', event => {
    const step = event.shiftKey ? 48 : 16;
    if (event.key === 'ArrowLeft') set(width - step);
    else if (event.key === 'ArrowRight') set(width + step);
    else if (event.key === 'Home') set(DEFAULT);
    else return;
    event.preventDefault(); save();
  });
})();
