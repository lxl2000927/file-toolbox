# 文件处理工具箱 File Toolbox

一款面向 Windows 的桌面文件处理工具，提供批量重命名、PDF 页面整理、扫描拆分、离线 OCR 与操作历史追踪。

![Version](https://img.shields.io/badge/version-v2.7.0-5b6ee1?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows-2f80ed?style=flat-square)
![Desktop](https://img.shields.io/badge/desktop-Electron%20%2B%20Vue-42b883?style=flat-square)
![Engine](https://img.shields.io/badge/engine-Python-3776ab?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

## File Toolbox 是什么

File Toolbox 是一个为日常文件整理、扫描件归档和 PDF 批处理设计的桌面应用。它把前端交互迁移到 Electron + Vue 3，并保留 Python 引擎负责重命名、PDF 拆分、扫描识别等核心处理逻辑，让界面响应更现代，处理能力也更容易扩展。

- **批量重命名**：适合照片、合同、扫描件、凭证等文件的统一命名与批量整理。
- **PDF 工作台**：缩略图整理页面、合并、空白页复核、图片转换、压缩和离线中英文 OCR。
- **PDF 普通拆分**：按页数、文件大小、页码范围或书签拆分 PDF，支持预览结果。
- **PDF 扫描拆分**：通过二维码、印章或特征图像识别标记页，适合扫描件批量分册。
- **历史日志**：记录操作结果、错误提示和来源信息，便于复盘、导出和问题定位。

[下载最新版](https://github.com/lxl2000927/file-toolbox/releases/latest) · [查看发布记录](https://github.com/lxl2000927/file-toolbox/releases) · [提交问题](https://github.com/lxl2000927/file-toolbox/issues)

> v2.0.0 起，File Toolbox 已从旧版 PyQt 桌面应用迁移为 Electron 桌面应用，旧 PyQt 入口、旧窗口面板和旧发布脚本已移除。

## 功能特性

### PDF 工作台

- 导入多份 PDF 或图片，按缩略图选择、拖动排序、旋转、删除、提取页面；支持撤销与重做。
- 工作区支持最多 3000 份文件 / 100000 页，分批导入、可中途停止；只加载当前组缩略图，失败文件单独报告。大规模实测见 [3000 份验收](docs/acceptance-large-pdf-2026-10-04.md)。
- 支持正反面交错合并、背面逆序；页数不一致时保留多出的页面并提示。
- 空白检测只标记候选页，检查后手动删除；不会自动删除疑似空白页。
- 页面导出 PNG/JPEG、提取 PDF 内嵌原始图像、多图片合并为 PDF。
- 支持按 `1-5,8,12-20`、奇偶页或反选批量选页，按目标位置稳定移动，并可撤销。
- 输出结果可打开、定位和复制路径；部分失败或取消保留完整输出，文件列表分页显示。
- 提供保留文字的结构优化，以及清晰、均衡、体积优先三档图片压缩；只有完整且范围一致的 PDF 输出才显示体积变化比例。
- 离线中英文 OCR 为扫描件增加可搜索文字层，保留原页面外观；带数字标题或页码的扫描件也能识别图片正文。
- OCR 可逐页查看、复制预览（前 20 页，每页最多 4000 字），默认同时保存完整 UTF-8 TXT，并可另存导出；预览截断会明确提示。
- 工作台与扫描参数方案独立保存，重启后可复用。输出前会核对源文件内容是否与预览一致。
- 工作区自动保存并恢复页序、旋转和设置，缺失或变化的原文件可重新定位；扫描复核的标记与合并分段也会保留。
- 来源按文件夹分组，可搜索文件名、筛选单份来源，并按工作区页码或原文档页码定位。
- 支持单文件、按来源或按页数分卷输出，大型任务会检查内存与可用磁盘空间；预览由独立引擎处理。
- 输出生成新文件，同名自动编号；中途取消或失败时列出已完成的输出。

验收说明见 [基础功能验收](docs/acceptance-2026-10-05.md) 和 [2.7.0 发布核验](docs/release-2.7.0.md)。

### 任务中心

- 集中查看后台任务、处理进度和输出结果，支持暂停、继续、取消以及手动续做失败或中断的任务。
- 暂停在安全的处理边界生效，当前页面的原生识别调用结束后响应；取消时保留已完成文件。
- 重启不会自动运行上次任务。续做前会重新验证输入和输出，复用已完成的输出单元；重命名操作沿用原有撤销流程。
- 工作区恢复不保存撤销栈；恢复的输出目录需要重新选择确认后再用于新的导出。

### 批量重命名

- 支持智能识别、查找替换、插入字符、删除/保留、自定义规则等重命名方式。
- 支持实时预览新文件名，执行前可检查冲突、空名称和重复名称。
- 支持覆盖原文件或输出为副本，适合批量整理扫描件、合同、照片和文档。
- 支持自然排序，处理中英文混排、数字序号等文件名更直观。

### PDF 普通拆分

- 支持按页数、文件大小、页码范围、书签等方式拆分 PDF。
- 支持指定输出目录，也可默认输出到源文件所在目录。
- 支持拆分预览、进度反馈和统一输出命名处理。
- 输出文件会自动处理重名，避免覆盖已有文件或同批次产物冲突。

### PDF 扫描拆分

- 支持二维码、印章、特征图像三类标记页识别方式。
- 支持参考图像或 PDF 参考页，特征匹配可配合 ROI 框选区域提升稳定性。
- 支持二维码不解码内容，仅识别二维码区域作为拆分标记。
- 支持命中后跳过指定页数、DPI 兜底重试和阶段耗时统计。
- 支持扫描日志、命中统计、疑似异常分段提示和历史摘要记录。
- 自动模式可选择参与的检测器；特征匹配默认执行数量、比例、覆盖范围和几何校验。
- 主流程先全量扫描，再进入独立复核工作区；可增加/移除标记页、合并相邻分段、撤销修改，确认后才输出。
- 未发现标记页时明确提示，允许补充标记或手动确认导出整份文档；扫描本身不写入文件。超长分段检查仅提示疑似漏检。
- 复核输出可选结构优化与中英文 OCR；切换源文件或识别参数后需要重新扫描，避免使用过期结果。
- DPI 兜底次数可设置为 0–2 次（自动模式最多 1 次），仅对二维码候选或已读到二维码但关键词未匹配的页面重试；增强图按需生成。
- 写入失败会保留并列出已生成文件、失败分段和待处理分段；扫描与写入分别显示进度。
- 参考预览在后台执行，连续调整参数时只处理最新的待处理请求。OpenCV 多线程关闭时使用单线程，开启时最多使用 8 个线程。

### 历史日志与设置

- 设置页提供操作历史日志，支持级别、来源和关键词筛选。
- 日志级别支持颜色标识，日志来源使用与左侧功能导航一致的图标。
- 支持自动刷新、手动刷新、清空历史、导出 TXT 和导出 JSON。
- 支持打开数据目录，便于定位本地历史记录和诊断文件。

### Windows 发布版本

- 安装版：适合长期使用，可选择安装目录并创建桌面快捷方式。
- 便携单文件版：免安装运行，适合临时使用或随身携带。
- 压缩版：解压即用，适合需要查看完整应用目录结构的场景。

## 下载与使用

从 GitHub Releases 下载对应版本：

- `File.Toolbox-2.7.0-x64-setup.exe`：安装版。
- `File.Toolbox-2.7.0-x64-portable.exe`：便携单文件版。
- `File.Toolbox-2.7.0-x64.zip`：压缩版。

下载后按版本类型运行：

- 安装版：运行安装程序，安装完成后从开始菜单或桌面快捷方式启动。
- 便携单文件版：直接双击 exe 运行。
- 压缩版：解压 zip 后运行目录内的 `File Toolbox.exe`。
- NSIS 安装版支持在“设置 → 更新”中检查、下载并重启安装新版本；便携版和压缩版通过发布页手动更新。

### 使用提示

- 首次运行未签名 Windows 程序时，系统可能出现 SmartScreen 或杀软提示。
- 便携单文件版首次启动可能略慢，属于自解压和安全扫描带来的正常现象。
- 引擎启动时会预热 OpenCV、PyMuPDF、NumPy 等依赖，可能有短暂等待。
- PDF 拆分和扫描拆分建议先用预览或单页测试确认规则，再执行正式处理。

## 开发环境

### 前置要求

- Windows 10/11
- Python 3.10+
- Node.js 20.19+ 或 22.12+
- npm

### 安装 Python 依赖

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\setup_ocr.py
```

OCR 安装脚本下载固定版本的官方语言数据并校验 SHA-256；识别时完全离线运行，不需要另装 Tesseract 程序。

### 安装 Electron 依赖

```bash
cd electron-app
npm install
```

### 本地开发运行

```bash
cd electron-app
npm run dev
```

开发模式下，Electron 主进程会通过 Python 解释器启动 `engine/server.py`，并通过 stdio JSON-RPC 与 Python 引擎通信。

完成依赖安装和 `npm run build` 后，也可双击项目根目录的 `start-toolbox.cmd`。启动脚本会复用或启动本项目的 Vite 服务，再打开桌面程序；启动日志保存在系统临时目录中。

拖入文件或文件夹后，需要在系统对话框中确认路径授权；文件选择框选中的路径直接授权。
已选文件若被替换，任务会提示重新选择。扫描图片和 PDF 页面渲染限制为每幅 3200 万像素，
超过时请降低 DPI 或缩小参考图片；普通 A4 页面在 300 DPI 下可正常处理。

## 验证命令

发布前建议至少运行以下检查：

```bash
cd electron-app
npm run typecheck
npm test
npm run build

cd ..
.venv\Scripts\python.exe -m unittest discover -s tests
.venv\Scripts\python.exe scripts\create_acceptance_samples.py
cd electron-app
npm run test:desktop
```

桌面集成测试使用独立数据目录及合成样例，连接真实 Electron 主进程、preload 和 Python 引擎；文件选择对话框由样例路径替代。测试截图、报告和输出均在 `acceptance-samples/` 中。

## 打包发布

### 打包 Python 引擎

先在根目录运行 `.venv\Scripts\python.exe scripts\setup_ocr.py`；打包配置会检查并包含中英文 OCR 语言数据。

```bash
cd engine
pyinstaller engine.spec --clean --noconfirm
```

打包后会生成 `engine/dist/engine.exe`，Electron 发布包会将该文件复制到应用资源目录。

### 打包 Windows 三版本

```bash
cd electron-app
npm run package
```

默认输出目录为 `electron-app/release`，会生成：

- 安装版：`File.Toolbox-2.7.0-x64-setup.exe`
- 便携单文件版：`File.Toolbox-2.7.0-x64-portable.exe`
- 压缩版：`File.Toolbox-2.7.0-x64.zip`

也可以单独打包指定版本：

```bash
npm run package:nsis
npm run package:portable
npm run package:zip
```

发布可供软件内更新的正式版本时，需要设置 `GH_TOKEN`，并确保 `package.json` 版本与 Git 标签一致：

```powershell
$env:GH_TOKEN="<GitHub token>"
npm run package:publish
```

GitHub Release 必须包含 NSIS 安装包、`latest.yml` 和对应的 `.blockmap`。客户端使用 `latest.yml` 中的版本、下载地址和 SHA-512 校验信息完成更新；草稿版和预发布版不会进入正式更新通道。

## 项目结构

```text
├── engine/
│   ├── server.py                    # Python JSON-RPC 服务入口
│   └── engine.spec                  # PyInstaller 引擎打包配置
├── src/
│   ├── core/
│   │   ├── rename_engine.py         # 批量重命名引擎
│   │   ├── pdf_split_engine.py      # PDF 普通拆分引擎
│   │   └── pdf_scan_split_engine.py # PDF 扫描拆分引擎
│   └── utils/
│       ├── history_manager.py       # 历史记录管理
│       ├── path_utils.py            # 路径工具
│       └── pdf_output.py            # PDF 输出写入工具
├── electron-app/
│   ├── main/
│   │   ├── index.ts                 # Electron 主进程与 IPC
│   │   └── python-bridge.ts         # Python 引擎桥接
│   ├── preload/
│   │   └── index.ts                 # 渲染端安全 API 暴露
│   ├── renderer/
│   │   ├── index.html
│   │   └── src/
│   │       ├── App.vue              # Vue 应用入口
│   │       ├── components/          # 导航、状态栏、通用控件和业务面板
│   │       ├── composables/         # Toast、弹窗、任务状态等组合函数
│   │       └── styles.css           # 全局主题样式
│   ├── electron-builder.yml         # Windows 打包配置
│   └── package.json
├── requirements.txt
└── README.md
```

## 技术架构

- Electron 主进程负责窗口管理、系统对话框、路径授权、更新检查和 Python 引擎生命周期。
- preload 只暴露受控 API，渲染端不能直接访问 Node.js 能力。
- Vue 3 渲染端负责界面交互、任务状态、日志筛选和用户反馈。
- Python 引擎通过 stdio JSON-RPC 执行重命名、PDF 拆分、扫描拆分和历史记录读写。
- 数值和图像依赖在引擎主线程中预热，避免后台首次导入时卡住；扫描和参考预览在后台执行。

## 注意事项

- 当前 Windows 产物未进行代码签名，正式分发时建议配置代码签名证书。
- 打包前需要先生成 Python 引擎，否则 Electron 发布包无法包含 `engine.exe`。
- `electron-app/node_modules/`、`electron-app/release*/`、`engine/dist/` 等生成目录不会提交到 Git。
- v2.0.0 为大版本重构，旧 PyQt 运行方式和旧打包脚本不再保留。

## 许可证

MIT License
