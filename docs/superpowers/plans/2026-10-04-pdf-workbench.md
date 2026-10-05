# PDF Workbench Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. User has authorized implementation and verification in the existing checkout.

**Goal:** 为 10 月 5 日验收提供八项 PDF 改进及可复现的验证材料。

**Architecture:** Python 处理真实文件，通过现有异步队列和授权 IPC 暴露。Vue 工作台管理无损编辑状态，扫描结果由独立复核组件确认。方案独立持久化。

**Tech Stack:** Electron 42 / Vue 3 / Python 3.14 / PyMuPDF / Pillow / OpenCV / Tesseract language data.

**Spec:** `docs/superpowers/specs/2026-10-04-pdf-workbench.md`

## Global Constraints

- 保留用户既有修改，不自动提交、不复制参考仓库代码。
- 原文件只读、输出不覆盖、取消保留已完成结果，所有路径通过既有授权。
- 预览内容签名防止过期输出；中文 OCR 离线运行；方案跨重启保留。

## Review Focus

1. 混合尺寸/旋转 PDF 与 EXIF 图片：输出方向与页面预览一致。
2. 中途取消/一个输出失败/同名输出：不留下损坏文件，已完成结果可找回。
3. 输入在复核期间变化：拒绝旧快照，提示重新载入。
4. 黑底、少量文字和浅灰扫描背景：不直接误删页面。
5. 切换页面/任务迟到/重启：不应用过期结果、不丢保存方案。

### Task 1: Core document operations
Files: `src/core/pdf_tools_engine.py`, `src/utils/atomic_output.py`, `tests/test_pdf_tools.py`.
Interface: `run_pdf_tool(action, files, options, cancel_check=None, progress=None) -> dict`.
- [x] Write and run failing real-file tests for inspect, ordering/rotation, interleave, blank candidates, image conversion/extraction, compression, cancellation and stale signatures.
- [x] Implement bounded reads/rendering and atomic output. Run focused tests.

### Task 2: Offline OCR and durable presets
Files: `src/core/pdf_ocr.py`, `src/utils/preset_store.py`, `scripts/setup_ocr.py`, `engine/engine.spec`, tests.
Interfaces: OCR PDF transformation and `PresetStore.list/save/delete`; stored records have id/name/scope/settings, no input paths.
- [x] Test Chinese raster text recognition/searchable output and persisted settings reload.
- [x] Fetch pinned official language data with checksums, integrate resources and cancel checks. Verify offline fixture.

### Task 3: Secure RPC and bridge
Files: `engine/server.py`, `src/utils/rpc_validation.py`, `electron-app/{main,preload}/index.ts`, shared types, RPC/security tests.
- [x] Add `pdf_tools.run`, `presets.list/save/delete` with explicit allowlists; validate files, nested references and output directory.
- [x] Exercise actual stdio RPC through completion/cancel and unauthorized inputs. Record history and preserve partial outputs.

### Task 4: PDF workbench
Files: new `PdfWorkbenchPanel.vue`, thumbnail and preset components, state helpers, `App.vue`, `SideNav.vue`, Node tests.
- [x] Test page editing undo/redo, interleave and stale async responses, then implement responsive workspace and parameter sidebar.
- [x] Connect every action to real APIs; show processing steps, candidates, output sizes and errors. Lazy batch thumbnails.

### Task 5: Scan review and workflows
Files: `ScanSplitPanel.vue`, `ScanReview.vue`, shared helpers/tests.
- [x] Test marker ownership/exclusion and edited boundaries, then route primary scan to full scan-only → review → confirmed segment export.
- [x] Save scan options, naming and optional output optimization; invalidate review on input/options changes.

### Task 6: Acceptance
Files: `scripts/create_acceptance_samples.py`, `docs/acceptance-2026-10-05.md`, README.
- [x] Run Python and Node suites, typecheck/build, real RPC acceptance, UI layout checks, and final review/fixes.
- [x] Create reproducible sample files and checklist. Restart application with latest engine and verify readiness.

## Execution notes

Implementation stays in the authorized existing workspace. No commits or worktree moves are needed for this user's acceptance request. Results and limitations will be retained in the acceptance report.

## Completion evidence (2026-10-04)

Python: 102 passing tests. Node: 39 passing tests. Typecheck and build passed. Real-file acceptance: 11 operations with unchanged input bytes. Desktop acceptance: 13 checks through the actual main/preload/Python bridge, with isolated fixture dialogs and screenshots. Final review fixes cover stale scan review and per-output OCR resource release. A further hybrid OCR regression verifies searchable image text, no duplicated native header, repeat processing, and pixel-identical appearance. See docs/acceptance-2026-10-05.md for reproduction and limits.

The local application was launched with scripts/start_desktop.cjs and its File Toolbox window was confirmed responsive. No commit or release publication was performed.
