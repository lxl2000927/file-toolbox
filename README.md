# 文件处理工具箱 File Toolbox

一款面向 Windows 的桌面文件处理工具，专注批量文件重命名、PDF 普通拆分、PDF 扫描拆分与操作历史追踪。

![Version](https://img.shields.io/badge/version-v2.5.0-5b6ee1?style=flat-square)
![Platform](https://img.shields.io/badge/platform-Windows-2f80ed?style=flat-square)
![Desktop](https://img.shields.io/badge/desktop-Electron%20%2B%20Vue-42b883?style=flat-square)
![Engine](https://img.shields.io/badge/engine-Python-3776ab?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

## File Toolbox 是什么

File Toolbox 是一个为日常文件整理、扫描件归档和 PDF 批处理设计的桌面应用。它把前端交互迁移到 Electron + Vue 3，并保留 Python 引擎负责重命名、PDF 拆分、扫描识别等核心处理逻辑，让界面响应更现代，处理能力也更容易扩展。

- **批量重命名**：适合照片、合同、扫描件、凭证等文件的统一命名与批量整理。
- **PDF 普通拆分**：按页数、文件大小、页码范围或书签拆分 PDF，支持预览结果。
- **PDF 扫描拆分**：通过二维码、印章或特征图像识别标记页，适合扫描件批量分册。
- **历史日志**：记录操作结果、错误提示和来源信息，便于复盘、导出和问题定位。

[下载最新版](https://github.com/lxl2000927/file-toolbox/releases/latest) · [查看发布记录](https://github.com/lxl2000927/file-toolbox/releases) · [提交问题](https://github.com/lxl2000927/file-toolbox/issues)

> v2.0.0 起，File Toolbox 已从旧版 PyQt 桌面应用迁移为 Electron 桌面应用，旧 PyQt 入口、旧窗口面板和旧发布脚本已移除。

## 功能特性

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

- `File.Toolbox-2.5.0-x64-setup.exe`：安装版。
- `File.Toolbox-2.5.0-x64-portable.exe`：便携单文件版。
- `File.Toolbox-2.5.0-x64.zip`：压缩版。

下载后按版本类型运行：

- 安装版：运行安装程序，安装完成后从开始菜单或桌面快捷方式启动。
- 便携单文件版：直接双击 exe 运行。
- 压缩版：解压 zip 后运行目录内的 `File Toolbox.exe`。
- NSIS 安装版支持在“设置 → 更新”中检查、下载并重启安装新版本；便携版和压缩版通过发布页手动更新。

### 使用提示

- 首次运行未签名 Windows 程序时，系统可能出现 SmartScreen 或杀软提示。
- 便携单文件版首次启动可能略慢，属于自解压和安全扫描带来的正常现象。
- 扫描拆分首次使用时会加载 OpenCV、PyMuPDF、NumPy 等依赖，可能有短暂等待。
- PDF 拆分和扫描拆分建议先用预览或单页测试确认规则，再执行正式处理。

## 开发环境

### 前置要求

- Windows 10/11
- Python 3.10+
- Node.js 20.19+ 或 22.12+
- npm

### 安装 Python 依赖

```bash
python -m pip install -r requirements.txt
```

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

### 实验性 Tauri 第一阶段（仅 Windows、仅重命名）

Tauri 工作流目前仅用于实验性第一阶段：仅支持 Windows，且仅迁移了批量重命名流程。PDF 拆分、扫描拆分、更新和发布安装包仍不在该流程范围内；上面的 Electron 开发与构建说明保持不变，仍是完整功能的开发路径。

从仓库根目录创建环境并运行 Tauri：

```powershell
py -3.14 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
Set-Location electron-app
npm ci
npm run tauri:dev
npm run tauri:build:debug
```

首次 Windows 调试构建基线为 `electron-app/src-tauri/target/debug/app.exe` 的 **14,116,352 bytes**。这是未打包的调试可执行文件大小，不是安装程序或最终发行包大小。

## 验证命令

发布前建议至少运行以下检查：

```bash
cd electron-app
npm run typecheck
npm run build

cd ..
python -m compileall engine src
```

## 打包发布

### 打包 Python 引擎

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

- 安装版：`File.Toolbox-2.5.0-x64-setup.exe`
- 便携单文件版：`File.Toolbox-2.5.0-x64-portable.exe`
- 压缩版：`File.Toolbox-2.5.0-x64.zip`

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
- 扫描拆分相关重依赖会延迟加载，减少便携版启动阻塞。

## 注意事项

- 当前 Windows 产物未进行代码签名，正式分发时建议配置代码签名证书。
- 打包前需要先生成 Python 引擎，否则 Electron 发布包无法包含 `engine.exe`。
- `electron-app/node_modules/`、`electron-app/release*/`、`engine/dist/` 等生成目录不会提交到 Git。
- v2.0.0 为大版本重构，旧 PyQt 运行方式和旧打包脚本不再保留。

## 许可证

MIT License
