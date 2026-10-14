// Actual navigation controller, disposable DOM and vendor state/event boundary.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const {initNavigation} = await import(process.argv[2] ? pathToFileURL(process.argv[2]).href :
  new URL('../app/static/navigation.js', import.meta.url).href);
globalThis.fetch = () => { throw new Error('Navigation must not make network requests.'); };
const vendorSource = await readFile(new URL('../app/static/vendor/adminlte/adminlte.min.js', import.meta.url), 'utf8');
const vendorMethod = vendorSource.match(/handleMenuNavigation\(e\)\{([\s\S]*?)\}respectReducedMotion\(/);
assert.ok(vendorMethod, 'Review the vendor keyboard boundary when the bundled AdminLTE implementation changes.');
const vendorMenuNavigation = new Function('e', vendorMethod[1]);

class Element {
  constructor(doc, tag, attributes = {}, parent = null) {
    this.doc = doc; this.tagName = tag.toUpperCase(); this.attributes = new Map(Object.entries(attributes));
    this.id = attributes.id || ''; this.children = []; this.parentElement = parent;
    this.listeners = new Map(); this.hidden = false; this.disabled = false;
    const classes = new Set((attributes.class || '').split(' ').filter(Boolean));
    this.classList = {contains: (name) => classes.has(name), add: (name) => classes.add(name),
      remove: (name) => classes.delete(name)};
    parent?.children.push(this);
    if (this.id) doc.ids.set(this.id, this);
  }
  get inert() { return this.attributes.has('inert'); }
  set inert(value) { if (value) this.attributes.set('inert', ''); else this.attributes.delete('inert'); }
  get isConnected() { return true; }
  get tabIndex() { return Number(this.attributes.get('tabindex') ?? (['A', 'BUTTON', 'INPUT'].includes(this.tagName) ? 0 : -1)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  matches(selector) {
    if (selector.includes(',')) return selector.split(',').some((part) => this.matches(part.trim()));
    if (selector.includes(' ')) {
      const [ancestor, child] = selector.split(' ');
      return this.matches(child) && Boolean(this.parentElement?.closest(ancestor));
    }
    if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
    const attr = selector.match(/^(\w+)?\[([^\]]+)\]$/);
    if (attr) return (!attr[1] || this.tagName === attr[1].toUpperCase()) && this.attributes.has(attr[2]);
    return this.tagName === selector.toUpperCase();
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  contains(element) { return element === this || this.children.some((child) => child.contains(element)); }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((part) => part.trim());
    const descendants = this.children.flatMap((child) => [child, ...child.querySelectorAll('*')]);
    return selector === '*' ? descendants : descendants.filter((child) => selectors.some((part) => child.matches(part)));
  }
  getClientRects() {
    for (let node = this; node; node = node.parentElement) {
      if (node.hidden || node.classList.contains('nav-treeview') && !node.parentElement.classList.contains('menu-open')) return [];
    }
    return [{}];
  }
  addEventListener(name, callback) {
    const callbacks = this.listeners.get(name) || []; callbacks.push(callback); this.listeners.set(name, callbacks);
  }
  dispatch(name, event = {}) {
    if (!event.target) event.target = this;
    for (const callback of this.listeners.get(name) || []) callback(event);
  }
  focus() {
    if (this.closest('[inert]') || !this.getClientRects().length) return;
    this.doc.activeElement = this; this.doc.dispatch('focusin', {target: this});
  }
  click() { this.focus(); this.dispatch('click'); }
}

function fixture(width = 375) {
  const doc = {ids: new Map(), listeners: new Map(),
    getElementById(id) { return this.ids.get(id) || null; },
    querySelectorAll(selector) { return this.body.querySelectorAll(selector); },
    addEventListener: Element.prototype.addEventListener, dispatch: Element.prototype.dispatch};
  const body = doc.body = new Element(doc, 'body', {class: width <= 992 ? 'sidebar-collapse' : ''});
  doc.activeElement = body;
  const header = new Element(doc, 'nav', {class: 'app-header'}, body);
  const toggle = new Element(doc, 'button', {id: 'navigation-toggle'}, header);
  const home = new Element(doc, 'a', {href: '/'}, header);
  const sidebar = new Element(doc, 'aside', {id: 'workspace-sidebar', tabindex: '-1'}, body);
  const brand = new Element(doc, 'a', {href: '/'}, sidebar);
  const close = new Element(doc, 'button', {id: 'navigation-close'}, sidebar);
  const nav = new Element(doc, 'ul', {class: 'nav sidebar-menu'}, sidebar);
  const groups = ['sessions', 'evidence'].map((name) => {
    const item = new Element(doc, 'li', {class: 'nav-item menu-open'}, nav);
    const button = new Element(doc, 'button', {'data-navigation-group': '', 'aria-controls': name}, item);
    const menu = new Element(doc, 'ul', {id: name, class: 'nav-treeview'}, item);
    const child = new Element(doc, 'a', {href: '/' + name}, menu);
    // Simulate the AdminLTE Treeview contract, including sibling accordion closure.
    button.addEventListener('click', () => {
      const opening = !item.classList.contains('menu-open');
      for (const sibling of nav.children) sibling.classList.remove('menu-open');
      if (opening) item.classList.add('menu-open');
      item.dispatch(opening ? 'expanded.lte.treeview' : 'collapsed.lte.treeview');
    });
    return {item, button, menu, child};
  });
  const final = new Element(doc, 'a', {href: '/configuration'}, nav);
  const main = new Element(doc, 'main', {class: 'app-main'}, body);
  const input = new Element(doc, 'input', {}, main);
  const footer = new Element(doc, 'footer', {class: 'app-footer'}, body);
  let observed;
  const win = {innerWidth: width, listeners: new Map(), getComputedStyle: () => ({content: '"992"'}),
    MutationObserver: class { constructor(callback) { observed = callback; } observe() {} },
    addEventListener: Element.prototype.addEventListener, dispatch: Element.prototype.dispatch};
  toggle.addEventListener('click', () => {
    const expanded = win.innerWidth <= 992 ? body.classList.contains('sidebar-open') : !body.classList.contains('sidebar-collapse');
    body.classList.remove('sidebar-open'); body.classList.remove('sidebar-collapse');
    if (expanded) body.classList.add('sidebar-collapse');
    else if (win.innerWidth <= 992) body.classList.add('sidebar-open');
    observed?.();
  });
  const controller = initNavigation(doc, win);
  function key(target, value, shiftKey = false, modifiers = {}) {
    target.focus();
    const event = {target, key: value, shiftKey, ...modifiers, prevented: false, stopped: false,
      preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }};
    doc.dispatch('keydown', event);
    // Run the actual bundled menu handler after our capture listener. Merely
    // returning cannot pass: the vendor cancels modified arrows and moves focus.
    if (!event.stopped && target.closest('.nav, .navbar-nav, .dropdown-menu')) {
      vendorMenuNavigation(event); event.vendorHandled = true;
    }
    return event;
  }
  return {doc, win, body, header, toggle, home, sidebar, brand, close, groups, final, main, input,
    footer, key, controller, observe: () => observed()};
}

const f = fixture();
assert.equal(initNavigation(f.doc, f.win), f.controller, 'Initialization must not add duplicate handlers.');
assert.equal(f.sidebar.inert, true, 'A closed mobile sidebar must leave the keyboard path.');
assert.equal(f.sidebar.getAttribute('aria-hidden'), 'true');
assert.equal(f.toggle.getAttribute('aria-expanded'), 'false');
f.toggle.focus(); f.brand.focus(); assert.equal(f.doc.activeElement, f.toggle, 'Hidden brand cannot receive focus.');
f.toggle.click();
assert.equal(f.sidebar.inert, false); assert.equal(f.toggle.getAttribute('aria-expanded'), 'true');
assert.equal(f.doc.activeElement, f.close, 'Opening moves focus to a visible close control.');
assert.equal(f.header.inert, true); assert.equal(f.main.inert, true); assert.equal(f.footer.inert, true);
f.input.focus(); assert.equal(f.doc.activeElement, f.close, 'Covered content must not receive focus.');
const forward = f.key(f.final, 'Tab');
assert.equal(forward.prevented, true); assert.equal(f.doc.activeElement, f.brand);
const backward = f.key(f.brand, 'Tab', true);
assert.equal(backward.prevented, true); assert.equal(f.doc.activeElement, f.final);
const escape = f.key(f.groups[0].child, 'Escape');
assert.equal(escape.prevented, true); assert.equal(f.sidebar.inert, true);
assert.equal(f.doc.activeElement, f.toggle, 'Escape must restore the opening control.');
assert.equal(f.main.inert, false);
f.toggle.click(); f.close.click(); assert.equal(f.doc.activeElement, f.toggle);
f.toggle.click(); f.body.classList.remove('sidebar-open'); f.body.classList.add('sidebar-collapse'); f.observe();
assert.equal(f.doc.activeElement, f.toggle, 'Vendor overlay closure restores focus too.');

const d = fixture(1440);
assert.equal(d.sidebar.inert, false); assert.equal(d.close.hidden, true);
assert.equal(d.header.inert, false); assert.equal(d.toggle.getAttribute('aria-expanded'), 'true');
d.groups[0].button.click();
assert.equal(d.groups[0].button.getAttribute('aria-expanded'), 'false');
assert.equal(d.groups[0].menu.inert, true);
d.groups[0].button.click();
assert.equal(d.groups[0].button.getAttribute('aria-expanded'), 'true');
assert.equal(d.groups[1].button.getAttribute('aria-expanded'), 'false', 'Accordion sibling state must synchronize.');
assert.equal(d.groups[1].menu.inert, true);
d.groups[1].button.click();
d.key(d.groups[0].button, 'ArrowDown');
assert.equal(d.doc.activeElement, d.groups[1].button, 'Arrow navigation must skip collapsed children.');
const browserBack = d.key(d.groups[1].button, 'ArrowLeft', false, {altKey: true});
assert.equal(browserBack.prevented, false, 'Modified browser navigation keys must keep their native action.');
assert.equal(browserBack.stopped, true, 'The vendor bubbling handler must not cancel browser Back.');
assert.equal(browserBack.vendorHandled, undefined);
assert.equal(d.doc.activeElement, d.groups[1].button);
for (const [name, modifiers] of [['Home', {ctrlKey: true}], ['End', {metaKey: true}]]) {
  const shortcut = d.key(d.groups[1].button, name, false, modifiers);
  assert.equal(shortcut.prevented, false); assert.equal(shortcut.stopped, true);
  assert.equal(shortcut.vendorHandled, undefined);
}
const unrelated = d.key(d.groups[1].button, 'a', false, {altKey: true});
assert.equal(unrelated.prevented, false); assert.equal(unrelated.stopped, false);
d.key(d.groups[1].child, 'Escape');
assert.equal(d.groups[1].menu.inert, true); assert.equal(d.doc.activeElement, d.groups[1].button);
d.toggle.click(); assert.equal(d.sidebar.inert, true); assert.equal(d.main.inert, false);
d.toggle.click(); assert.equal(d.sidebar.inert, false);
d.groups[0].button.focus(); d.win.innerWidth = 375;
d.body.classList.add('sidebar-collapse'); d.win.dispatch('resize');
assert.equal(d.sidebar.inert, true); assert.equal(d.doc.activeElement, d.toggle);
d.toggle.click(); d.close.focus();
d.win.innerWidth = 1440; d.body.classList.remove('sidebar-collapse'); d.body.classList.remove('sidebar-open'); d.win.dispatch('resize');
assert.equal(d.close.hidden, true); assert.equal(d.sidebar.inert, false); assert.equal(d.main.inert, false);
assert.equal(d.doc.activeElement, d.toggle, 'A mobile-only close control cannot keep focus after desktop resize.');
// Restore preexisting inert state rather than enabling unrelated content.
f.main.inert = true; f.toggle.click(); f.close.click(); assert.equal(f.main.inert, true);
console.log('Navigation behavior passed: mobile inertness, focus bounds/return, Escape, vendor overlay, accordion state, visible arrows, desktop collapse and breakpoint transitions.');
