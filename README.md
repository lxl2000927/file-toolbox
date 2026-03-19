# 文件处理工具箱 (File Toolbox)

一个基于 PyQt6 的桌面应用程序，提供批量文件重命名、PDF普通拆分与扫描拆分功能。

## 功能特性

### 1. 批量重命名工具
- 支持多种重命名规则（插入字符、插入编号、替换、删除/保留、智能识别、自定义等）
- 实时文件名预览
- 支持覆盖原文件或另存为副本
- 操作撤销功能
- 文件列表支持全选/反选与计数显示
- 支持文件名自然排序（中英文混排、数字按数值排序）

### 2. PDF拆分工具
- 按页数拆分（指定每份最大页数）
- 按文件大小拆分（指定目标文件大小）
- 按页码范围拆分（指定需要提取的页面范围）
- 按书签拆分（根据PDF文档的书签结构拆分）
- 实时进度显示
- 拆分结果预览（面板内显示，支持复制预览）

### 3. 扫描拆分工具
- 支持二维码识别 / 印章识别 / 特征匹配（参考图像）三种标记页检测方式
- 支持框选 ROI 缩小识别范围，提高速度与稳定性
- 支持命中后跳过 N 页（提升长文档处理速度）
- 支持 OpenCV 多线程优化与 GPU 加速开关（环境不支持会自动回退）

## 安装

### 前提条件
- Python 3.10+
- pip

### 安装步骤
1. 克隆仓库
```bash
git clone <repository-url>
cd file-toolbox
```

2. 创建虚拟环境（可选但推荐）
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate
```

3. 安装依赖
```bash
pip install -r requirements.txt
```

## 使用

运行主程序：
```bash
python src/main.py
```

### 操作提示
- 支持拖拽文件到列表区域快速添加
- PDF拆分：不勾选“指定输出目录”时，默认输出到源文件同目录
- 重命名：建议先勾选“实时预览”确认结果，再点击“开始重命名”
- 扫描拆分：二维码/印章模式可选“框选特征点(ROI)”提升稳定性；低配电脑可尝试开启多线程优化

## 项目结构

```
src/
├── main.py                 # 应用入口
├── ui/                     # 用户界面组件
│   ├── main_window.py     # 主窗口
│   ├── rename_panel.py    # 重命名工具面板
│   ├── pdf_split_panel.py # PDF拆分工具面板
│   ├── pdf_scan_split_panel.py # PDF扫描拆分面板
│   ├── about_panel.py     # 设置 / 日志面板
│   └── widgets/           # 通用UI组件
├── core/                   # 核心逻辑
│   ├── rename_engine.py   # 重命名引擎
│   ├── pdf_split_engine.py # PDF拆分引擎
│   └── pdf_scan_split_engine.py # PDF扫描拆分引擎
└── utils/                  # 工具类
    ├── file_picker.py     # 文件选择器
    ├── style_manager.py   # 样式管理器
    └── history_manager.py # 历史管理器
```

## 开发

### 运行测试
项目包含集成测试脚本，用于验证核心功能与关键UI交互：

```bash
# 运行集成测试
python test_integration.py

# 或者通过虚拟环境的Python运行
venv\Scripts\python.exe test_integration.py
```

当前集成测试覆盖：
- 模块导入
- 重命名引擎（预览/执行/删除与保留规则）
- PDF拆分引擎（配置与页面范围解析）
- UI组件创建与关键交互（表头全选/计数、自然排序、拆分预览生成、复制预览）
- 主窗口状态持久化（窗口尺寸位置保存/恢复/重置）
- 扫描拆分运行日志（成功/失败写入历史）

### 代码风格
- 遵循PEP 8规范
- 类型提示：使用Python类型提示提高代码可读性
- 模块化设计：UI、核心逻辑和工具类分离

## 许可证

MIT License
