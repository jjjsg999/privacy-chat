// Draggable panel widths (desktop only): the sidebar, and the artifact pane when it is docked beside the chat.
// Drag the edge, use Left/Right arrows when the handle is focused (Shift for bigger steps), Home or double-click to reset.
(() => {
  function resizer({panel, id, key, property, min, max, fallback, edge, label}) {
    if (!panel) return;
    const clamp = value => Math.min(max(), Math.max(min, Math.round(value)));
    let width = fallback();
    let saved = false;
    try { const value = Number(localStorage.getItem(key)); if (value) { width = value; saved = true; } } catch (_) {}
    const handle = document.createElement('div');
    handle.id = id; handle.tabIndex = 0;
    handle.setAttribute('role', 'separator'); handle.setAttribute('aria-orientation', 'vertical');
    handle.setAttribute('aria-label', label); handle.title = 'Drag to resize. Double-click to reset.';
    handle.setAttribute('aria-valuemin', min);
    const set = value => {
      width = clamp(value);
      document.documentElement.style.setProperty(property, `${width}px`);
      handle.setAttribute('aria-valuenow', width); handle.setAttribute('aria-valuemax', max());
    };
    const save = () => { saved = true; try { localStorage.setItem(key, String(width)); } catch (_) {} };
    const reset = () => {
      saved = false; try { localStorage.removeItem(key); } catch (_) {}
      if (property === '--sidebar-w') set(fallback()); else { document.documentElement.style.removeProperty(property); width = fallback(); handle.setAttribute('aria-valuenow', width); }
    };
    if (saved || property === '--sidebar-w') set(width); else handle.setAttribute('aria-valuenow', width);
    panel.append(handle);
    // Dragging away from the panel's free edge grows it: right for the sidebar, left for the artifact pane.
    const direction = edge === 'left' ? -1 : 1;
    handle.addEventListener('pointerdown', event => {
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);  // keeps the drag when the pointer passes over the artifact iframe
      const startX = event.clientX, startWidth = panel.getBoundingClientRect().width;
      document.body.classList.add('resizing');
      const move = moveEvent => set(startWidth + direction * (moveEvent.clientX - startX));
      const stop = () => {
        handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', stop);
        handle.removeEventListener('pointercancel', stop);
        document.body.classList.remove('resizing'); save();
      };
      handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', stop); handle.addEventListener('pointercancel', stop);
    });
    handle.addEventListener('dblclick', reset);
    handle.addEventListener('keydown', event => {
      const step = (event.shiftKey ? 48 : 16) * direction;
      const current = panel.getBoundingClientRect().width || width;
      if (event.key === 'ArrowLeft') set(current - step);
      else if (event.key === 'ArrowRight') set(current + step);
      else if (event.key === 'Home') { event.preventDefault(); reset(); return; }
      else return;
      event.preventDefault(); save();
    });
    // A narrower window lowers the limit; a saved width larger than that is clamped, not lost.
    addEventListener('resize', () => { if (saved) document.documentElement.style.setProperty(property, `${clamp(width)}px`); });
  }

  const sidebar = document.getElementById('sidebar');
  resizer({panel: sidebar, id: 'sidebar-resizer', key: 'privacy-llm-sidebar-width', property: '--sidebar-w',
    min: 200, max: () => 560, fallback: () => 248, edge: 'right', label: 'Resize sidebar'});
  // The artifact pane keeps at least 360px for the conversation beside it.
  const sidebarWidth = () => (sidebar && sidebar.getBoundingClientRect().width) || 0;
  resizer({panel: document.getElementById('artifact-pane'), id: 'artifact-resizer', key: 'privacy-llm-artifact-width', property: '--artifact-w',
    min: 360, max: () => Math.max(360, innerWidth - sidebarWidth() - 360),
    fallback: () => Math.min(780, Math.max(400, innerWidth * 0.44)), edge: 'left', label: 'Resize artifact pane'});
})();
