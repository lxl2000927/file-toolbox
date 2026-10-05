const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { parse, compileScript } = require('@vue/compiler-sfc');
const { buildSync } = require('esbuild');
const vue = require('vue');

// Run real component setup and lifecycle hooks without needing Electron or a DOM library.
function componentFixture(relativePath, props = {}, overrides = {}) {
  const filename = path.join(__dirname, '../renderer/src', relativePath);
  const { descriptor } = parse(fs.readFileSync(filename, 'utf8'), { filename });
  const script = compileScript(descriptor, { id: 'component-regression-test' }).content;
  const code = buildSync({ stdin: { contents: script, loader: 'ts', resolveDir: path.dirname(filename) },
    bundle: true, external: ['vue', '*.vue'], platform: 'node', format: 'cjs', write: false,
  }).outputFiles[0].text;
  const listeners = new Map();
  const document = { activeElement: null, body: { style: {} },
    addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: (type) => listeners.delete(type),
  };
  const window = { innerWidth: 1120, innerHeight: 720,
    addEventListener() {}, removeEventListener() {}, setTimeout, clearTimeout, setInterval, clearInterval,
  };
  const module = { exports: {} };
  vm.runInNewContext(code, { module, exports: module.exports,
    require: (id) => id.endsWith('.vue') ? {} : require(id),
    document, window, sessionStorage: { getItem: () => null, setItem() {} },
    setTimeout, clearTimeout, requestAnimationFrame: (fn) => fn(), ...overrides,
  });
  const component = module.exports.default;
  component.render = () => null;
  const node = () => ({ children: [], parent: null });
  const renderer = vue.createRenderer({
    createElement: node, createText: node, createComment: node,
    insert(child, parent, anchor) {
      if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1);
      child.parent = parent;
      const i = anchor ? parent.children.indexOf(anchor) : -1;
      parent.children.splice(i < 0 ? parent.children.length : i, 0, child);
    },
    remove(child) { child.parent?.children.splice(child.parent.children.indexOf(child), 1); child.parent = null; },
    parentNode: (child) => child.parent, nextSibling: (child) => child.parent?.children[child.parent.children.indexOf(child) + 1],
    setText() {}, setElementText() {}, patchProp() {},
  });
  const active = vue.ref(true), componentProps = vue.reactive(props);
  let instance;
  const app = renderer.createApp({ render: () => vue.h(vue.KeepAlive, null, {
    default: () => active.value ? vue.h(component, { ...componentProps, ref: (value) => { if (value) instance = value.$; } }) : null,
  }) });
  app.mount(node());
  return { state: instance.setupState, props: componentProps, document, listeners,
    async flush() { await vue.nextTick(); await vue.nextTick(); },
    async deactivate() { active.value = false; await vue.nextTick(); },
    async activate() { active.value = true; await vue.nextTick(); },
    dispose: () => app.unmount(),
  };
}

module.exports = { componentFixture };
