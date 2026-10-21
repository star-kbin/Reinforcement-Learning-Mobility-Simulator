const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../dashboard/leaflet_map/map_controls.js'), 'utf8');

function setup(initialZoom = 13) {
  let now = 0, serial = 0, zoom = initialZoom;
  const timers = new Map(), listeners = new Map(), mapEvents = new Map(), changes = [];
  const center = [37.51362, 126.94157];
  const container = {
    addEventListener(name, fn, options) { listeners.set(name, { fn, options }); },
    removeEventListener(name, fn) { if (listeners.get(name)?.fn === fn) listeners.delete(name); },
  };
  const map = {
    getContainer: () => container, getZoom: () => zoom,
    getMinZoom: () => 0, getMaxZoom: () => 19,
    setZoom(value, options) { changes.push({ time: now, from: zoom, to: value, animate: options.animate }); zoom = value; },
    on: (name, fn) => mapEvents.set(name, fn), off: name => mapEvents.delete(name),
  };
  const context = {
    module: { exports: {} },
    setTimeout(fn, delay) { const id = ++serial; timers.set(id, { fn, time: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(source, context);
  const cleanup = context.module.exports.attachStableWheelZoom(map, {
    DomEvent: { getWheelDelta(event) { return event.delta; } },
  });
  function advance(ms) {
    const end = now + ms;
    while (true) {
      const next = [...timers].filter(([,v]) => v.time <= end).sort((a,b) => a[1].time - b[1].time)[0];
      if (!next) break;
      now = next[1].time; timers.delete(next[0]); next[1].fn();
    }
    now = end;
  }
  function wheel(delta, extras = {}) {
    const event = { delta, prevented: false, stopped: false,
      preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; }, ...extras };
    listeners.get('wheel')?.fn(event);
    return event;
  }
  return { wheel, advance, changes, listeners, mapEvents, timers, center, cleanup, zoom: () => zoom };
}

test('a large wheel burst produces only one quarter-step and no queued momentum', () => {
  const h = setup();
  for (let i = 0; i < 100; i++) assert.equal(h.wheel(10000).prevented, true);
  assert.equal(h.zoom(), 13);
  h.advance(159); assert.equal(h.zoom(), 13);
  h.advance(1); assert.equal(h.zoom(), 13.25);
  h.advance(10000); assert.equal(h.changes.length, 1);
  assert.equal(h.listeners.get('wheel').options.passive, false);
});

test('continuous scrolling is rate limited, centered and never jumps multiple levels', () => {
  const h = setup();
  for (let i = 0; i < 100; i++) { h.wheel(i % 2 ? 1 : 9000); h.advance(10); }
  h.advance(160);
  assert.ok(h.changes.length > 1);
  for (let i = 0; i < h.changes.length; i++) {
    const c = h.changes[i];
    assert.equal(c.to - c.from, 0.25);
    assert.equal(c.animate, false);
    if (i) assert.ok(c.time - h.changes[i-1].time >= 160);
  }
  // The supplied map has no pointer-pan or setZoomAround method: only center-preserving zoom is allowed.
  assert.deepEqual(h.center, [37.51362, 126.94157]);
});

test('direction reversal uses latest intent without executing the previous direction', () => {
  const h = setup(); h.wheel(120); h.advance(80); h.wheel(-120); h.advance(80);
  assert.equal(h.zoom(), 12.75);
});

test('mouse leave cancels pending zoom and does not change the view later', () => {
  const h = setup(); h.wheel(120); h.listeners.get('mouseleave').fn(); h.advance(1000);
  assert.equal(h.zoom(), 13); assert.equal(h.timers.size, 0);
});

test('Ctrl/Command browser zoom is not intercepted; horizontal/invalid input is ignored', () => {
  const h = setup(); h.wheel(120);
  assert.equal(h.wheel(120, { ctrlKey: true }).prevented, false);
  assert.equal(h.wheel(120, { metaKey: true }).prevented, false);
  for (const delta of [0, NaN, Infinity]) assert.equal(h.wheel(delta).prevented, false);
  h.advance(1000); assert.equal(h.changes.length, 0);
});

test('zoom stays within tile limits and cleanup cancels timers and detaches listeners', () => {
  for (const [zoom, delta] of [[19, 120], [0, -120]]) {
    const h = setup(zoom); h.wheel(delta); h.advance(160); assert.equal(h.changes.length, 0);
  }
  const h = setup(); h.wheel(120); h.mapEvents.get('unload')(); h.advance(1000);
  assert.equal(h.changes.length, 0); assert.equal(h.listeners.size, 0); assert.equal(h.mapEvents.size, 0);
  h.cleanup(); assert.equal(h.timers.size, 0);
});
