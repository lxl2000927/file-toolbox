# 更新日志

## v2.4.0

本版本重点围绕 PDF 依赖迁移、Python 引擎稳定性、任务取消行为、更新检查逻辑、界面细节和 Windows 打包链路进行整理。相比上一版，`v2.4.0` 更偏向稳定性和发布质量修复，目标是让普通拆分、扫描拆分、重命名和更新检查在实际使用、开发调试、打包发布时都更可靠。

### 重点更新

- PDF 处理依赖从 `PyPDF2` 迁移到 `pypdf`，普通 PDF 拆分、扫描拆分、智能重命名和通用 PDF 输出工具已统一使用新依赖。
- 更新 Python 引擎打包配置，将 `pypdf` 加入 PyInstaller hidden imports，并移除旧 `PyPDF2` 引用，避免源码和打包结果依赖不一致。
- 优化 Electron 与 Python 引擎启动链路，开发环境会检测项目虚拟环境是否真的可执行，损坏时自动回退到系统 Python，减少“Python 引擎启动中，请稍候”卡住的问题。
- 修复 GitHub 更新检查逻辑，只基于正式 Release 判断更新，过滤草稿版和预发布版，避免 `2.4.0` 尚未发布时误把旧版本日志显示为当前更新内容。
- 完成应用版本、README、安装包文件名和构建要求同步，当前版本统一为 `2.4.0`。

### 功能与体验优化

- 普通 PDF 拆分任务取消后不再强制删除已经成功写出的文件，界面会提示“已取消，但保留了 X 个已生成的文件”。
- 扫描拆分任务取消后同样会保留已经完成的分段 PDF，适合长文档处理中途停止但保留阶段性结果。
- 普通拆分结果增加顶层 `output_files` 汇总字段，同时保留每个文件操作记录里的输出列表，前端可稳定统计取消后保留的文件数量。
- 普通拆分预览空状态去掉突兀图标和嵌套卡片，改为更轻量的页面提示，视觉上更接近整体界面风格。
- 设置页更新检查空状态改为页面级展示，减少小卡片嵌套带来的杂乱感。
- 检查出新版本时，更新日志改为直接在页面内容区展示，不再包在额外边框容器里。
- 删除设置页重复的“检查更新”按钮，只保留顶部主要操作入口。
- 重命名页和普通拆分页右侧分段按钮增加滑块过渡动画，切换模式更顺滑。
- 修复分段按钮动画卡顿问题，滑块移动改用更稳定的 `transform` 计算方式。
- 修复部分分段按钮选中后白字不可见的问题，仅对带滑块动画的分段控件使用透明 active 背景。
- 左侧导航恢复上一版固定宽度布局，移除底部无效折叠按钮，改善三个主功能入口的文字排版。
- 重命名自定义规则输入框恢复完整宽度，修复右侧多出空白列导致看起来“少一块”的问题。

### 后端与稳定性修复

- 修复普通拆分 `pdf_paths` 文件校验遗漏，避免非法路径或不存在的 PDF 进入 Python 引擎执行阶段。
- 修复路径授权中空字符串可能导致校验范围异常的问题，提升 Electron 主进程 IPC 路径访问安全性。
- 修复扫描拆分布尔参数解析问题，字符串形式的 `false`、`0`、`off`、`no` 不再被 Python 的 `bool("false")` 误判为启用。
- 修复普通拆分页码范围输入无效时的行为，例如输入 `abc` 或异常范围格式时不再静默回退成整本 PDF 输出，而是返回明确错误。
- 修复 PDF 输出工具对页码越界的静默钳制行为，越界页码会直接报错，避免隐藏上层拆分计划问题。
- 优化普通拆分和扫描拆分的取消逻辑，取消发生在文件写入前、写入中、写入后时都能按策略处理输出文件。
- 优化后台任务队列释放流程，降低任务启动失败或取消后遗留任务状态的风险。
- 优化 Python 引擎 shutdown 流程，退出前主动刷新历史记录，减少程序关闭时历史日志丢失的可能性。
- 优化 Python 引擎 stderr 重定向，尽量捕获 OpenCV 等底层库输出，方便排查运行问题。
- 优化 Python 引擎鉴权比较逻辑，使用字节级安全比较，避免非 ASCII 输入导致鉴权阶段抛异常。
- 移除普通 PDF 拆分同步执行入口，统一走异步任务，避免大 PDF 拆分时阻塞 Python 引擎主循环和取消请求。
- 修复智能重命名中部分索引参数非数字时可能导致崩溃的问题。

### 更新检查修复

- GitHub Release 查询从只取最新一条改为获取多条 Release 后筛选最新正式版本。
- 忽略 `draft` 和 `prerelease`，避免测试版本、草稿版本影响普通用户更新判断。
- 当前版本已经大于或等于最新正式版本时，不再返回旧 Release 的 `body` 和 `url`，前端也不会展示旧版本更新日志。
- 无可用正式 Release 时会明确显示“未发现可用的正式 Release，无需更新”。

### 依赖变化

- `PyPDF2>=3.0.0` 替换为 `pypdf>=4.0`。
- 保持上一版 Python 依赖风格，仅设置最低版本，不添加上限，便于 Python 3.14 环境安装新版 `numpy`、`opencv-python` 等依赖。
- 新增 `esbuild` 开发依赖，适配 Vite 8 对构建依赖的要求。
- 明确 Node.js 版本要求为 `20.19+` 或 `22.12+`，避免使用不满足 Vite 8 要求的 Node 版本。

### 构建与打包

- 修复 PyInstaller `engine.spec` 路径配置，确保打包入口指向 `engine/server.py`，并能正确解析项目根目录下的 `src.*` 模块。
- Python 引擎已通过 PyInstaller 打包为 `engine.exe`，并输出到 Electron 配置使用的 `engine/dist/` 目录。
- Electron Builder 会通过 `extraResources` 将 `engine/dist/engine.exe` 打入 `resources/engine/engine.exe`。
- 开发模式 Electron 启动增加独立 `--user-data-dir=.dev-user-data`，避免默认用户数据目录的单实例锁影响开发启动。
- `.gitignore` 新增开发期用户数据目录，避免 Electron 缓存、Local Storage 等运行时文件污染 Git 变更。
- 已完成 Windows 安装版、便携版和 zip 三类发行产物生成。

### 打包产物

- 安装版：`File Toolbox-2.4.0-x64-setup.exe`
- 便携版：`File Toolbox-2.4.0-x64-portable.exe`
- 压缩版：`File Toolbox-2.4.0-x64.zip`
- 增量更新辅助文件：`File Toolbox-2.4.0-x64-setup.exe.blockmap`
- 已确认未解包目录中包含 Python 引擎：`win-unpacked/resources/engine/engine.exe`

### 验证结果

- `npm run build:main` 通过，Electron 主进程和 preload 构建正常。
- `npm run build:renderer` 通过，Vue 渲染进程构建正常。
- `npm run typecheck` 通过，TypeScript 类型检查正常。
- Python 核心模块 `py_compile` 通过，普通拆分、扫描拆分、重命名、通用 PDF 输出和引擎入口语法正常。
- `git diff --check` 通过，仅有 Windows 环境下 LF/CRLF 换行提示，无空白错误。
- PyInstaller 打包 Python 引擎成功，生成 `engine/dist/engine.exe`。
- Electron Builder 打包成功，生成安装包、便携包和 zip，并确认 Python 引擎已被包含在发行包内。

### 升级说明

- 从旧版本升级后，可直接使用新版安装包覆盖安装。
- 首次运行未签名 Windows 程序时，系统可能出现 SmartScreen 或杀毒软件提示，这是未配置代码签名证书时的常见现象。
- 如果使用便携版，首次启动可能略慢，属于 Electron 和 Python 引擎加载带来的正常现象。
- 如果自行从源码构建，请使用 Node.js `20.19+` 或 `22.12+`，并先安装 Python 依赖后再打包 Python 引擎。
