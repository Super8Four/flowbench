// Accessibility state follows AdminLTE's existing sidebar and treeview controls.
const controllers = new WeakMap();
const focusSelector = 'a[href], button, input, select, textarea, [tabindex]';
const navigationKeys = ['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End'];

export function initNavigation(doc = document, win = window) {
  if (controllers.has(doc)) return controllers.get(doc);
  const sidebar = doc.getElementById('workspace-sidebar');
  const toggle = doc.getElementById('navigation-toggle');
  const close = doc.getElementById('navigation-close');
  if (!sidebar || !toggle || !close) return null;
  const groups = [...sidebar.querySelectorAll('[data-navigation-group]')].map((button) => ({
    button, item: button.closest('.nav-item'), menu: doc.getElementById(button.getAttribute('aria-controls')),
  })).filter((group) => group.item && group.menu);
  const background = [...doc.querySelectorAll('.app-header, .app-main, .app-footer')];
  const previousInert = new Map();
  let drawerOpen = false, returnFocus = toggle;
  const visible = (element) => !element.disabled && element.tabIndex >= 0 &&
    !element.closest('[inert]') && element.getClientRects().length > 0;
  const focusables = () => [...sidebar.querySelectorAll(focusSelector)].filter(visible);
  const focusDrawer = () => (visible(close) ? close : focusables()[0] || sidebar).focus();
  const mobile = () => {
    // Match the breakpoint AdminLTE obtains from sidebar-expand-lg's CSS.
    const content = win.getComputedStyle(doc.body, '::before').content;
    const breakpoint = Number(content?.replace(/[^\d.-]/g, '')) || 992;
    return win.innerWidth <= breakpoint;
  };

  function syncGroups() {
    for (const {button, item, menu} of groups) {
      const expanded = item.classList.contains('menu-open');
      if (!expanded && menu.contains(doc.activeElement)) button.focus();
      button.setAttribute('aria-expanded', String(expanded));
      menu.inert = !expanded;
      menu.setAttribute('aria-hidden', String(!expanded));
    }
  }
  function sync() {
    syncGroups();
    const narrow = mobile();
    const expanded = narrow ? doc.body.classList.contains('sidebar-open') :
      !doc.body.classList.contains('sidebar-collapse');
    const nextDrawerOpen = narrow && expanded;
    const wasOpen = drawerOpen;
    if (nextDrawerOpen && !drawerOpen) {
      returnFocus = doc.activeElement && doc.activeElement !== doc.body && !sidebar.contains(doc.activeElement)
        ? doc.activeElement : toggle;
    }
    drawerOpen = nextDrawerOpen;
    if (nextDrawerOpen) {
      for (const element of background) {
        if (!previousInert.has(element)) previousInert.set(element, element.inert);
        element.inert = true;
      }
    } else {
      for (const [element, inert] of previousInert) element.inert = inert;
      previousInert.clear();
    }
    if (!expanded && sidebar.contains(doc.activeElement)) toggle.focus();
    sidebar.inert = !expanded;
    sidebar.setAttribute('aria-hidden', String(!expanded));
    toggle.setAttribute('aria-expanded', String(expanded));
    close.hidden = !narrow;
    if (drawerOpen && !wasOpen) focusDrawer();
    else if (wasOpen && !drawerOpen && !expanded) {
      (returnFocus.isConnected && !returnFocus.closest('[inert]') ? returnFocus : toggle).focus();
    } else if (close.hidden && doc.activeElement === close) toggle.focus();
  }
  function closeDrawer() {
    if (drawerOpen) toggle.click(); // Keep AdminLTE's state, overlay and events authoritative.
    sync();
  }
  close.addEventListener('click', closeDrawer);
  doc.addEventListener('keydown', (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) {
      // AdminLTE's bubbling menu handler also intercepts modified arrow keys.
      // Keep the browser action, while preventing that handler from cancelling it.
      if (sidebar.contains(event.target) && event.target.closest('.sidebar-menu') && navigationKeys.includes(event.key))
        event.stopPropagation();
      return;
    }
    if (drawerOpen && event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation(); closeDrawer(); return;
    }
    if (!sidebar.contains(event.target)) return;
    if (drawerOpen && event.key === 'Tab') {
      // The vendor's page-wide focus helper must not bypass this drawer boundary.
      event.stopPropagation();
      const items = focusables(), first = items[0], last = items.at(-1);
      if (!items.length) { event.preventDefault(); sidebar.focus(); }
      else if (event.shiftKey && (doc.activeElement === first || !items.includes(doc.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (doc.activeElement === last || !items.includes(doc.activeElement))) {
        event.preventDefault(); first.focus();
      }
      return;
    }
    const group = groups.find(({item}) => item.contains(event.target));
    if (event.key === 'Escape' && group?.item.classList.contains('menu-open')) {
      event.preventDefault(); event.stopPropagation(); group.button.click(); syncGroups(); group.button.focus();
      return;
    }
    if (navigationKeys.includes(event.key) &&
        event.target.closest('.sidebar-menu')) {
      const items = [...sidebar.querySelectorAll('.sidebar-menu a, .sidebar-menu button')].filter(visible);
      const index = items.indexOf(doc.activeElement);
      if (index < 0 || !items.length) return;
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 :
        (index + (['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : -1) + items.length) % items.length;
      event.preventDefault(); event.stopPropagation(); items[next].focus();
    }
  }, true);
  doc.addEventListener('focusin', (event) => {
    if (drawerOpen && !sidebar.contains(event.target)) focusDrawer();
  });
  for (const {item} of groups) {
    for (const name of ['expanded.lte.treeview', 'collapsed.lte.treeview', 'load.lte.treeview'])
      item.addEventListener(name, syncGroups);
  }
  for (const name of ['expanded.lte.push-menu', 'collapsed.lte.push-menu']) sidebar.addEventListener(name, sync);
  const observer = new win.MutationObserver(sync);
  observer.observe(doc.body, {attributes: true, attributeFilter: ['class']});
  for (const {item} of groups) observer.observe(item, {attributes: true, attributeFilter: ['class']});
  win.addEventListener('resize', sync);
  const controller = {sync};
  controllers.set(doc, controller);
  sync();
  return controller;
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initNavigation(), {once: true});
  else initNavigation();
}
