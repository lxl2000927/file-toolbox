const assert = require('node:assert/strict');
const { test } = require('node:test');
const path = require('node:path');
const vm = require('node:vm');
const { buildSync } = require('esbuild');

function fixture() {
  let now = 0, nextTimer = 0;
  const timers = new Map();
  const code = buildSync({ entryPoints: [path.join(__dirname, '../renderer/src/composables/useToast.ts')],
    bundle: true, external: ['vue'], platform: 'node', format: 'cjs', write: false }).outputFiles[0].text;
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports,
    require: () => ({ reactive: (value) => value }), Date: { now: () => now },
    setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, at: now + delay }); return id; },
    clearTimeout: (id) => timers.delete(id),
  });
  function advance(ms) {
    const end = now + ms;
    for (;;) {
      const due = [...timers].filter(([, item]) => item.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      now = due[1].at; timers.delete(due[0]); due[1].fn();
    }
    now = end;
  }
  return { ...module.exports, advance, timers };
}

test('toast timer matches its rendered duration and errors allow more reading time', () => {
  const f = fixture(), toast = f.useToast();
  toast.info('info'); toast.error('error');
  const [info, error] = f.toastState.items;
  assert.ok(error.duration > info.duration);
  f.advance(info.duration - 1);
  assert.equal(f.toastState.items.length, 2);
  f.advance(1);
  assert.equal(f.toastState.items[0].id, error.id);
  f.advance(error.duration - info.duration);
  assert.equal(f.toastState.items.length, 0);
  assert.equal(f.timers.size, 0);
});

test('hover and focus pause independently and resume only the remaining time', () => {
  const f = fixture(), toast = f.useToast();
  const id = toast.show('read me');
  const duration = f.toastState.items[0].duration;
  f.advance(1000); toast.pause(id, 'pointer'); toast.pause(id, 'focus');
  f.advance(10000); toast.resume(id, 'pointer');
  assert.equal(f.toastState.items[0].paused, true);
  f.advance(10000); toast.resume(id, 'focus');
  f.advance(duration - 1001);
  assert.equal(f.toastState.items.length, 1);
  f.advance(1);
  assert.equal(f.toastState.items.length, 0);
});

test('dismissal and queue eviction clear timers, including paused notifications', () => {
  const f = fixture(), toast = f.useToast();
  const first = toast.show('first'); toast.pause(first, 'pointer');
  for (let i = 0; i < 5; i++) toast.show(String(i));
  assert.equal(f.toastState.items.length, 5);
  assert.equal(f.toastState.items.some((item) => item.id === first), false);
  toast.resume(first, 'pointer');
  for (const item of [...f.toastState.items]) toast.dismiss(item.id);
  assert.equal(f.toastState.items.length, 0);
  assert.equal(f.timers.size, 0);
  f.advance(10000);
  assert.equal(f.toastState.items.length, 0);
});
