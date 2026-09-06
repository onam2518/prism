/* Shared keyboard contract for existing Alpine dialogs/tabs; no application state. */
(() => {
  const modal = '[aria-modal="true"]:is([role="dialog"],[role="alertdialog"])';
  const visible = el => {
    for (let node = el; node; node = node.parentElement) if (node._x_isShown === false) return false;
    return el.isConnected && !el.closest('[inert]') && !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  };
  const focusable = root => [...root.querySelectorAll('button,a[href],input,select,textarea,[tabindex]')].filter(el => !el.disabled && el.tabIndex >= 0 && visible(el));
  const tabsIn = list => [...list.querySelectorAll('[role="tab"]')].filter(tab => tab.closest('[role="tablist"]') === list);
  const enabledTab = tab => !tab.disabled && tab.getAttribute('aria-disabled') !== 'true' && visible(tab);
  function syncTabStops() {
    document.querySelectorAll('[role="tablist"]').forEach(list => tabsIn(list).forEach(tab => {
      // Hidden and disabled tabs retain their component-owned tabindex until they can participate again.
      if (enabledTab(tab)) tab.tabIndex = tab.getAttribute('aria-selected') === 'true' ? 0 : -1;
    }));
  }
  let tabsScheduled = false;
  function scheduleTabStops() {
    if (!tabsScheduled) { tabsScheduled = true; requestAnimationFrame(() => { tabsScheduled = false; syncTabStops(); }); }
  }
  // Alpine changes aria-selected in place and can insert x-for tablists. tabindex is deliberately not observed,
  // so our own normalization cannot schedule another pass.
  new MutationObserver(scheduleTabStops).observe(document.body, {
    subtree: true, childList: true, attributes: true, attributeFilter: ['aria-selected']
  });
  let current = null, scheduled = false;
  const openers = new WeakMap();
  const scope = el => el.closest('[data-dialog-scope]') || el;
  function sync() {
    scheduled = false;
    const dialogs = [...document.querySelectorAll(modal)].filter(visible);
    const layer = el => Number(getComputedStyle(el.closest('.ds-dialog-backdrop') || el).zIndex) || 0;
    dialogs.sort((a, b) => layer(a) - layer(b));
    const next = dialogs.pop() || null;
    if (next === current) return;
    const previous = current;
    current = next;
    if (next) {
      if (!openers.has(next)) openers.set(next, document.activeElement);
      next.tabIndex = -1;
      const first = next.querySelector('[data-dialog-initial-focus]') || focusable(next)[0] || next;
      const restore = previous && !visible(previous) && openers.get(previous);
      if (restore && visible(restore) && scope(next).contains(restore)) restore.focus({preventScroll: true});
      else if (!scope(next).contains(document.activeElement)) first.focus({preventScroll: true});
    } else if (previous) {
      const opener = openers.get(previous);
      if (opener && visible(opener)) opener.focus({preventScroll: true});
    }
    if (previous && !visible(previous)) openers.delete(previous);
  }
  new MutationObserver(() => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(sync); }
  }).observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['style','hidden','aria-modal']});
  document.addEventListener('keydown', e => {
    sync();
    // The review assistant remains independently keyboard operable beside detail.
    const companion = e.target.closest('[role="dialog"]:not([aria-modal="true"])');
    if (current && companion && !scope(current).contains(companion)) return;
    if (current && e.key === 'Escape') {
      // The inner picker gets first refusal; its Escape must not close the dialog.
      const picker = scope(current).querySelector('.mpick__btn[aria-expanded="true"]');
      if (picker) { e.preventDefault(); e.stopImmediatePropagation(); picker.click(); picker.focus(); return; }
      if ([...document.querySelectorAll('.hybdd')].some(visible)) return;
      const close = current.querySelector('[data-dialog-close]');
      if (close && !close.disabled) { e.preventDefault(); e.stopImmediatePropagation(); close.click(); }
      return;
    }
    if (current && e.key === 'Tab') {
      const root = scope(current), items = focusable(root);
      if (current.closest('[data-dialog-scope]')) document.querySelectorAll('[data-dialog-companion]').forEach(panel => { if (visible(panel)) items.push(...focusable(panel)); });
      // Model menus are teleported by the existing component outside its dialog.
      if (root.querySelector('.mpick__btn[aria-expanded="true"]')) document.querySelectorAll('.mpick__menu').forEach(menu => { if (visible(menu)) items.push(...focusable(menu)); });
      const hasCompanions = items.some(el => !root.contains(el));
      const index = items.indexOf(document.activeElement);
      if (!items.length) { e.preventDefault(); current.focus(); }
      else if (hasCompanions && index >= 0) { e.preventDefault(); items[(index + (e.shiftKey ? -1 : 1) + items.length) % items.length].focus(); }
      else if (index < 0 || (e.shiftKey ? index === 0 : index === items.length - 1)) {
        e.preventDefault(); items[e.shiftKey ? items.length - 1 : 0].focus();
      }
    }
    const tab = e.target.closest('[role="tab"]'), list = tab && tab.closest('[role="tablist"]');
    if (!list || e.altKey || e.ctrlKey || e.metaKey) return;
    const vertical = list.getAttribute('aria-orientation') === 'vertical';
    const keys = vertical ? ['ArrowUp','ArrowDown'] : ['ArrowLeft','ArrowRight'];
    if (![...keys, 'Home','End'].includes(e.key)) return;
    const tabs = tabsIn(list).filter(enabledTab);
    let index = tabs.indexOf(tab);
    if (e.key === 'Home') index = 0;
    else if (e.key === 'End') index = tabs.length - 1;
    else index = (index + (e.key === keys[0] ? -1 : 1) + tabs.length) % tabs.length;
    if (tabs[index]) { e.preventDefault(); tabs[index].focus(); tabs[index].click(); }
  }, true);
  syncTabStops();
  sync();
})();
