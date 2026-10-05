const assert = require('node:assert/strict');
const { test } = require('node:test');
const path = require('node:path');

test('all desktop panels initialize and render without the optional native bridge', async () => {
  const { createServer } = await import('vite');
  const { createSSRApp } = require('vue');
  const { renderToString } = require('vue/server-renderer');
  const server = await createServer({
    configFile: path.join(__dirname, '../vite.renderer.config.ts'),
    server: { middlewareMode: true, hmr: false }, appType: 'custom',
  });
  try {
    for (const [name, expected] of [['ScanSplitPanel', '扫描参数分组'], ['PdfSplitPanel', 'PDF 拆分方式'],
      ['RenamePanel', '重命名规则'], ['AboutPanel', '设置分组'], ['PdfWorkbenchPanel', '输出方式']]) {
      const component = await server.ssrLoadModule(`/src/components/panels/${name}.vue`);
      const html = await renderToString(createSSRApp(component.default));
      assert.ok(html.includes(expected), `${name} should render its primary controls`);
    }
  } finally {
    await server.close();
  }
});
