# PDF 工作台四项完善验收

本轮完成结果操作、批量页码操作、压缩/OCR 反馈及大批量异常回归。验收对象是当前工作区源码构建。

## 可验收功能

1. **输出结果**：PDF/图片可打开、在文件夹定位、复制路径；文件列表每页 50 条。成功、提示、部分失败、取消和失败分别呈现。TXT 写入失败保留已完成的 PDF；未产出新文件的取消保留并明确标记上次结果。打开、定位只接受已授权的普通文档，包括提取出的 JPEG2000/JBIG2 等原始图片格式。
2. **批量选页与移动**：支持 `1-5,8,12-20`、中文逗号、奇数页、偶数页及反选，按当前工作区顺序计算。非法范围不修改选择。移动位置表示所选页在最终结果中的起始位置，保持所选页相对顺序，支持撤销和重做。复杂重叠范围使用线性计算，避免大页数卡顿。
3. **压缩与 OCR**：图片压缩预设为清晰 220 DPI/88、均衡 150/78、体积优先 100/60，手动修改显示自定义。体积比较仅适用于没有错误或取消、完整覆盖所用来源页面的 PDF 输出；可能变大，如实显示增加比例。OCR 预览最多 20 页、每页 4000 字，明确标记截断并可复制当前预览；完整 UTF-8 TXT 默认随 PDF 保存，支持另存导出。全文超过 1 MiB 后暂存磁盘，避免全部文字堆在 IPC 或前端内存中。
4. **规模与异常**：继续使用 3000 份、9000 页、约 1.07 GB 混合文字/模拟扫描 PDF。验证反复取消三次后继续导入、4502 页范围选择/反选/移动/撤销、输出路径不是目录、模拟权限拒绝、TXT 保存失败和取消期间临时文件清理。

## 自动检查

- Python：124 项通过，其中本轮新增 10 项后端回归。
- Node/Vue/Electron 主进程：71 项通过；包括真实组件/组合函数的无结果排队取消通知、100000 页操作、输出分页与权限校验。
- TypeScript/Vue 类型检查、生产构建、`git diff --check` 通过。
- 真实 Electron 桌面验收：26 项；包含新增控件，以及原有扫描复核、实际中文 OCR、图片导出、方案持久化、日志、更新和动效流程。
- 一次独立审查发现的原始图片格式拒绝、排队取消误报问题均已补回归并修复。

桌面验收运行真实主进程、preload、Vue 和 Python 引擎，系统对话框返回合成样例路径；打开文件、定位文件和剪贴板在系统调用边界记录替身结果，不实际启动外部阅读器或覆盖用户剪贴板。权限拒绝使用故障注入，另有真实“文件被当作输出目录”的写入失败测试。

## 性能边界

最终桌面计时保存在 `acceptance-samples/scale-evidence/desktop-scale.json`。该报告同时记录导入、首屏/末尾预览、前端 JS 堆、长任务以及取消重试结果。每组只显示 12 张卡片。

| 本机桌面场景 | 1000 份 / 3000 页 | 3000 份 / 9000 页 |
| --- | ---: | ---: |
| 首组预览 | 422 ms | 656 ms |
| 全部导入 | 2104 ms | 5506 ms |
| 末尾预览 | 374 ms | 377 ms |
| 返回首组预览 | 327 ms | 330 ms |
| 测量期间渲染器长任务（>50 ms） | 0 | 0 |

4502 页批量选择反馈约 9 ms；反选、移动、撤销以及三次取消后恢复至 3000 份均通过，渲染器错误为零。计时包括界面更新与预览动画。

独立 Python 进程再次检查 3000 份来源耗时 1.832 秒，末页单页预览 8 ms，检查/预览阶段峰值工作集 66.2 MiB。此为单个 Python 进程的 Windows 工作集，不是整个应用内存；私有提交等原始数值见 `native-3000.json`。

上述为本机合成样例读数。大规模合并、图片压缩和 OCR 仍受输出文档大小、像素数与识别页数影响；本轮的加载/预览指标不代表 9000 页 OCR 的完成时间或内存。此前完整合并的验证记录见 [3000 份验收](acceptance-large-pdf-2026-10-04.md)。

## 复现与证据

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe scripts\benchmark_pdf_scale.py 3000
cd electron-app
npm test
npm run typecheck
npm run build
npm run test:desktop
npm run test:scale
```

- `acceptance-samples/refinement-python-tests.log`、`refinement-node-tests.log`：完整回归结果。
- `acceptance-samples/desktop-evidence/report.json`：桌面检查清单与渲染器错误记录。
- `acceptance-samples/desktop-evidence/bulk-selection-1120.png`：最小窗口批量操作布局。
- `acceptance-samples/desktop-evidence/ocr-text-and-export-1280.png`：逐页文字与完整 TXT 导出。
- `acceptance-samples/desktop-evidence/compression-result-1280.png`：真实压缩结果反馈。
- `acceptance-samples/scale-evidence/desktop-scale.json`、`native-3000.json`：规模测试与内存。

样例、输出和截图均在 Git 忽略的 `acceptance-samples` 中，未使用用户文档。
