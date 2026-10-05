# v2.6.0 发布核验

用户于 2026-10-05 授权最终审查后提交、打包并发布 2.6.0；更新日志与附件参考既有 v2.5.0 发布。

## 发布约定

- 基线：`9039aafbc9161a043e1a84370f7182c967e4aa4e`（v2.5.0），发布准备开始时本地 main 与远端 main 一致。
- 日志沿用“版本概述 / 新增 / 优化 / 修复”；CHANGELOG.md 与 GitHub Release 使用同一段正文。
- 版本：package.json、package-lock.json、README、Git 标签 `v2.6.0` 保持一致。
- 沿用五个附件：`File.Toolbox-2.6.0-x64-setup.exe`、`File.Toolbox-2.6.0-x64-portable.exe`、`File.Toolbox-2.6.0-x64.zip`、`File.Toolbox-2.6.0-x64-setup.exe.blockmap`、`latest.yml`。
- 所有产物核验通过后提交和推送；先上传草稿并校验附件，再转为正式最新版本。

## 本地核验结果

- [x] 独立代码审查：修复拆分与工作台并发输出同名文件的覆盖风险。新增回归同时覆盖整份复制和逐页重写，修复前两条路径均失败，修复后通过。
- [x] Python 125 项、Node 71 项通过；类型检查、生产构建和 `git diff --check` 通过。
- [x] 桌面验收 26 项通过；1000/3000 份文件加载、预览、反复取消恢复和批量移动撤销通过。
- [x] 中英文 OCR 数据 SHA-256 校验通过，PyInstaller 引擎重新构建；从隔离目录启动冻结引擎，实际合并、缩略图、中文 OCR/PDF/TXT、压缩、图片提取、普通拆分、扫描、重命名撤销、方案及日志通过。
- [x] 安装包、便携包、ZIP 构建通过。修复原打包配置引用未入库安装脚本的问题，改为受版本管理的 `packaging/installer.nsh`。
- [x] ZIP CRC 与目录结构通过；ZIP 中 app.asar 与实测展开版一致，嵌入引擎与已测独立引擎字节一致。
- [x] 实际运行展开版和便携单文件版，验证版本 2.6.0、分发类型、内置引擎就绪、五个导航入口及渲染器无异常。未执行系统安装或覆盖现有安装。
- [x] latest.yml 中版本、安装包文件名、长度和 SHA-512 与实际文件一致，外置 blockmap 可正确解压解析。

## 发布操作

提交和推送后应确认 main 与 `v2.6.0` 指向同一发布提交；上传五项附件到草稿，逐项核对远端长度与 SHA-256，再设为正式最新版本。发布后检查 latest API、更新元数据和 Release 正文一致性。

| 附件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| 安装版 | 195626166 | `1c638102f8df3f4a71c00719f85a7ecddd8576fc5964ee775f537815ddc6a155` |
| 便携版 | 195415210 | `0a51a1f46701ab3bf1c82975ee3490cf43b980633556067dd0b2ce49ce9607f2` |
| ZIP | 238617772 | `1ff05a2aa7df2106a54fb3a4a2665f42a093ca2c4bd7d39ed641e5a6a339956d` |
| 安装包 blockmap | 206551 | `3bf45dca3b3cb48e0d87d9e6eea05a9af748c39cc7f3e6221c8ff415bc4d0e0c` |
| latest.yml | 361 | `c6188cc6c29821ae57e40f67d43b8e328c5a7e544f9cd8d9cafd4b84838d3aaf` |

详细测试证据保存在 Git 忽略的 `acceptance-samples/release-2.6.0/`。
