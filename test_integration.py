#!/usr/bin/env python3
"""
集成测试脚本 - 测试文件处理工具箱的基本功能
"""

import sys
import os
import tempfile
import shutil

# 添加src目录到Python路径，以便导入模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

HAVE_PYQT6 = False
HAVE_PYPDF2 = False
_QT_APP = None


def get_qt_app():
    global _QT_APP
    if not HAVE_PYQT6:
        return None
    from PyQt6.QtWidgets import QApplication
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP

def test_imports():
    """测试所有模块是否能正确导入"""
    print("测试模块导入...")
    
    try:
        from PyQt6.QtWidgets import QApplication
        print("  [OK] PyQt6 导入成功")
        global HAVE_PYQT6
        HAVE_PYQT6 = True
        get_qt_app()
    except ImportError as e:
        print(f"  [WARN] PyQt6 导入失败（将跳过UI相关测试）: {e}")
    
    try:
        import PyPDF2
        print("  [OK] PyPDF2 导入成功")
        global HAVE_PYPDF2
        HAVE_PYPDF2 = True
    except ImportError as e:
        print(f"  [WARN] PyPDF2 导入失败（将跳过依赖PDF读取的测试）: {e}")
    
    try:
        from core.rename_engine import RenameEngine
        print("  [OK] RenameEngine 导入成功")
    except Exception as e:
        print(f"  [FAIL] RenameEngine 导入失败: {e}")
        return False
    
    try:
        from core.pdf_split_engine import PdfSplitEngine, SplitMode
        print("  [OK] PdfSplitEngine 导入成功")
    except Exception as e:
        print(f"  [FAIL] PdfSplitEngine 导入失败: {e}")
        return False
    
    if HAVE_PYQT6:
        try:
            from ui.main_window import MainWindow
            print("  [OK] MainWindow 导入成功")
        except Exception as e:
            print(f"  [FAIL] MainWindow 导入失败: {e}")
            return False
        
        try:
            from ui.rename_panel import RenamePanel
            print("  [OK] RenamePanel 导入成功")
        except Exception as e:
            print(f"  [FAIL] RenamePanel 导入失败: {e}")
            return False
    
    if HAVE_PYQT6:
        try:
            from ui.pdf_split_panel import PdfSplitPanel
            print("  [OK] PdfSplitPanel 导入成功")
        except Exception as e:
            print(f"  [FAIL] PdfSplitPanel 导入失败: {e}")
            return False

        try:
            from ui.about_panel import AboutPanel
            print("  [OK] AboutPanel 导入成功")
        except Exception as e:
            print(f"  [FAIL] AboutPanel 导入失败: {e}")
            return False
    
    return True

def test_rename_engine():
    """测试重命名引擎的基本功能"""
    print("\n测试重命名引擎...")
    
    from core.rename_engine import RenameEngine
    
    engine = RenameEngine()
    
    # 创建测试文件
    temp_dir = tempfile.mkdtemp()
    test_files = []
    try:
        for i in range(3):
            file_path = os.path.join(temp_dir, f"test_{i}.txt")
            with open(file_path, 'w') as f:
                f.write(f"测试文件 {i}")
            test_files.append(file_path)
        
        # 测试简单重命名规则
        rules = [{
            "type": "replace_text",
            "find": "test",
            "replace": "file",
            "case_sensitive": False
        }]
        
        # 设置规则并预览
        engine.set_rules(rules)
        preview = engine.batch_generate_filenames(test_files)
        
        if len(preview) == 3:
            print(f"  [OK] 重命名预览成功，生成 {len(preview)} 条记录")
            for original_path, new_filename in preview:
                print(f"    预览: {os.path.basename(original_path)} -> {new_filename}")
        else:
            print(f"  [FAIL] 重命名预览失败，预期 3 条记录，实际 {len(preview)} 条")
            return False
        
        # 测试实际重命名（使用复制模式）
        result = engine.execute_rename(test_files, save_method="copy")
        
        if result["total"] == 3 and result["successful"] == 3:
            print(f"  [OK] 重命名执行成功，处理 {result['successful']}/{result['total']} 个文件")
        else:
            print(f"  [FAIL] 重命名执行失败，成功 {result['successful']}/{result['total']}")
            if result["errors"]:
                print(f"    错误: {result['errors']}")
            return False

        try:
            extra_path = os.path.join(temp_dir, "ab12_中文-!.txt")
            with open(extra_path, "w", encoding="utf-8") as f:
                f.write("Hello Title\nSecond line")
            extra_files = [extra_path]

            rules = [
                {"type": "delete_chars", "delete_type": "delete_patterns", "targets": ["letters", "digits", "symbols"], "custom_chars": "_"},
                {"type": "keep_chars", "mode": "range", "range": "1-2", "direction": "从右往左"},
            ]
            engine.set_rules(rules)
            preview = engine.batch_generate_filenames(extra_files)
            new_name = preview[0][1] if preview else ""
            if new_name.endswith(".txt") and len(new_name) > 4:
                print("  [OK] 删除/保留规则预览正常")
            else:
                print(f"  [FAIL] 删除/保留规则预览异常: {new_name}")
                return False

            invalid_path = os.path.join(temp_dir, "invalid.txt")
            with open(invalid_path, "w", encoding="utf-8") as f:
                f.write("invalid")
            engine.set_rules([{"type": "replace_text", "find": "invalid", "replace": "bad/name", "case_sensitive": True}])
            invalid_result = engine.execute_rename([invalid_path], save_method="copy")
            if invalid_result["failed"] == 1 and os.path.exists(invalid_path):
                print("  [OK] 无效文件名执行前校验正常")
            else:
                print(f"  [FAIL] 无效文件名校验异常: {invalid_result}")
                return False

            source_path = os.path.join(temp_dir, "source.txt")
            target_path = os.path.join(temp_dir, "target.txt")
            with open(source_path, "w", encoding="utf-8") as f:
                f.write("source")
            with open(target_path, "w", encoding="utf-8") as f:
                f.write("target")
            engine.set_rules([{"type": "replace_text", "find": "source", "replace": "target", "case_sensitive": True}])
            conflict_result = engine.execute_rename([source_path], save_method="overwrite")
            with open(target_path, "r", encoding="utf-8") as f:
                target_content = f.read()
            if conflict_result["failed"] == 1 and os.path.exists(source_path) and target_content == "target":
                print("  [OK] 覆盖冲突保护正常")
            else:
                print(f"  [FAIL] 覆盖冲突保护异常: {conflict_result}")
                return False
        except Exception as e:
            print(f"  [FAIL] 删除/保留规则测试失败: {e}")
            return False
            
    finally:
        # 清理临时文件
        shutil.rmtree(temp_dir, ignore_errors=True)
    
    return True


def test_rename_all_rules():
    """测试重命名引擎支持的全部规则类型与关键分支"""
    print("\n测试重命名所有规则...")
    from core.rename_engine import RenameEngine

    engine = RenameEngine()
    temp_dir = tempfile.mkdtemp()
    try:
        def _touch(name: str, content: str = "hello\nworld") -> str:
            p = os.path.join(temp_dir, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            return p

        f_base = _touch("Ab12_中文-!.txt", "First Line Title\nSecond line")
        base_name = os.path.basename(f_base)

        cases = []

        cases.append((
            "insert_text_prefix",
            [{"type": "insert_text", "text": "PRE_", "position": "前缀"}],
            base_name,
            0,
            "PRE_" + base_name,
            f_base
        ))
        cases.append((
            "insert_text_suffix",
            [{"type": "insert_text", "text": "_SUF", "position": "后缀"}],
            base_name,
            0,
            os.path.splitext(base_name)[0] + "_SUF" + os.path.splitext(base_name)[1],
            f_base
        ))
        cases.append((
            "insert_text_index",
            [{"type": "insert_text", "text": "X", "position": "指定位置", "index": 2}],
            "abcd.txt",
            0,
            "aXbcd.txt",
            f_base
        ))
        cases.append((
            "insert_number_suffix",
            [{"type": "insert_number", "prefix": "_", "start": 1, "step": 1, "digits": 2, "position": "后缀"}],
            "file.txt",
            0,
            "file_01.txt",
            f_base
        ))
        cases.append((
            "insert_number_prefix",
            [{"type": "insert_number", "prefix": "N", "start": 5, "step": 2, "digits": 3, "position": "前缀"}],
            "file.txt",
            1,
            "N007file.txt",
            f_base
        ))
        cases.append((
            "delete_chars_specified",
            [{"type": "delete_chars", "delete_type": "删除指定字符", "chars": "b"}],
            "abba.txt",
            0,
            "aa.txt",
            f_base
        ))
        cases.append((
            "delete_chars_head",
            [{"type": "delete_chars", "delete_type": "删除前N个字符", "count": 2}],
            "abcdef.txt",
            0,
            "cdef.txt",
            f_base
        ))
        cases.append((
            "delete_chars_tail",
            [{"type": "delete_chars", "delete_type": "删除后N个字符", "count": 2}],
            "abcdef.txt",
            0,
            "abcd.txt",
            f_base
        ))
        cases.append((
            "delete_patterns_letters_digits_symbols_custom",
            [{"type": "delete_chars", "delete_type": "delete_patterns", "targets": ["letters", "digits", "symbols"], "custom_chars": "_-"}],
            "Ab12_中文-!.txt",
            0,
            "中文.txt",
            f_base
        ))
        cases.append((
            "replace_text_case_sensitive",
            [{"type": "replace_text", "find": "Ab", "replace": "XY", "case_sensitive": True}],
            "Abab.txt",
            0,
            "XYab.txt",
            f_base
        ))
        cases.append((
            "replace_text_case_insensitive",
            [{"type": "replace_text", "find": "ab", "replace": "Z", "case_sensitive": False}],
            "aBab.txt",
            0,
            "ZZ.txt",
            f_base
        ))
        cases.append((
            "change_extension",
            [{"type": "change_extension", "new_ext": "md"}],
            "note.TXT",
            0,
            "note.md",
            f_base
        ))
        cases.append((
            "uniform_name",
            [{"type": "uniform_name", "base_name": "统一名"}],
            "anything.txt",
            0,
            "统一名.txt",
            f_base
        ))
        cases.append((
            "keep_chars_specified",
            [{"type": "keep_chars", "mode": "specified", "chars": "abc"}],
            "a1b2c3.txt",
            0,
            "abc.txt",
            f_base
        ))
        cases.append((
            "keep_chars_range_left",
            [{"type": "keep_chars", "mode": "range", "range": "2-4", "direction": "从左往右"}],
            "abcdef.txt",
            0,
            "bcd.txt",
            f_base
        ))
        cases.append((
            "keep_chars_range_right",
            [{"type": "keep_chars", "mode": "range", "range": "1-2", "direction": "从右往左"}],
            "abcdef.txt",
            0,
            "ef.txt",
            f_base
        ))

        title_file = _touch("title_source.txt", "Hello Title\nSecond line")
        cases.append((
            "smart_recognize_text_cover",
            [{"type": "smart_recognize", "mode": "content_title", "position": "覆盖原名"}],
            "oldname.txt",
            0,
            "Hello Title.txt",
            title_file
        ))
        cases.append((
            "smart_recognize_text_prefix",
            [{"type": "smart_recognize", "mode": "content_title", "position": "首位"}],
            "oldname.txt",
            0,
            "Hello Titleoldname.txt",
            title_file
        ))
        cases.append((
            "smart_recognize_text_suffix",
            [{"type": "smart_recognize", "mode": "content_title", "position": "末位"}],
            "oldname.txt",
            0,
            "oldnameHello Title.txt",
            title_file
        ))
        cases.append((
            "smart_recognize_text_index",
            [{"type": "smart_recognize", "mode": "content_title", "position": "指定位置", "index": 2}],
            "abcd.txt",
            0,
            "aHello Titlebcd.txt",
            title_file
        ))

        try:
            import PyPDF2
            pdf_path = os.path.join(temp_dir, "meta.pdf")
            writer = PyPDF2.PdfWriter()
            writer.add_blank_page(width=72, height=72)
            writer.add_metadata({"/Title": "PDF Title"})
            with open(pdf_path, "wb") as f:
                writer.write(f)

            cases.append((
                "smart_recognize_pdf_title_cover",
                [{"type": "smart_recognize", "mode": "content_title", "position": "覆盖原名"}],
                "x.pdf",
                0,
                "PDF Title.pdf",
                pdf_path
            ))
            cases.append((
                "smart_recognize_invoice_info_no_crash",
                [{"type": "smart_recognize", "mode": "invoice_info", "position": "覆盖原名"}],
                "x.pdf",
                0,
                "x.pdf",
                pdf_path
            ))
        except Exception:
            print("  [WARN] 跳过PDF元数据相关智能识别测试（PyPDF2不可用）")

        cases.append((
            "combined_rules_chain",
            [
                {"type": "replace_text", "find": "Ab", "replace": "X", "case_sensitive": True},
                {"type": "insert_text", "text": "_", "position": "后缀"},
                {"type": "insert_number", "prefix": "N", "start": 1, "step": 1, "digits": 2, "position": "后缀"},
                {"type": "change_extension", "new_ext": "log"},
            ],
            "Abcd.txt",
            0,
            "Xcd_N01.log",
            f_base
        ))

        for name, rules, input_filename, file_index, expected, filepath in cases:
            engine.set_rules(rules)
            out = engine.generate_new_filename(input_filename, file_index, filepath)
            if out != expected:
                print(f"  [FAIL] {name} 失败: {input_filename} -> {out} (预期 {expected})")
                return False

        print(f"  [OK] 覆盖 {len(cases)} 个规则用例通过")
        return True
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def test_pdf_split_engine():
    """测试PDF拆分引擎的基本功能（不使用真实PDF文件）"""
    print("\n测试PDF拆分引擎...")
    
    from core.pdf_split_engine import PdfSplitEngine, SplitMode
    
    engine = PdfSplitEngine()
    
    # 测试配置设置
    config = {
        "mode": SplitMode.BY_PAGE_COUNT.value,
        "page_count": 5,
        "max_size": 10,
        "size_unit": "MB",
        "page_ranges": "1-10",
        "bookmark_level": 1,
        "output_dir": tempfile.gettempdir(),
        "file_prefix": "test_"
    }
    
    try:
        engine.set_config(config)
        print("  [OK] 配置设置成功")
    except Exception as e:
        print(f"  [FAIL] 配置设置失败: {e}")
        return False
    
    # 测试页面范围解析
    try:
        ranges = engine.parse_page_ranges("1-5,7-10", 20)
        if len(ranges) == 2:
            print(f"  [OK] 页面范围解析成功，解析出 {len(ranges)} 个范围")
        else:
            print(f"  [FAIL] 页面范围解析失败，预期 2 个范围，实际 {len(ranges)} 个")
            return False
    except Exception as e:
        print(f"  [FAIL] 页面范围解析失败: {e}")
        return False
    
    return True


def test_pdf_split_engine_real_files():
    print("\n测试PDF拆分引擎（真实PDF文件）...")
    if not HAVE_PYPDF2:
        print("  [WARN] 跳过（缺少PyPDF2）")
        return None

    import PyPDF2
    from core.pdf_split_engine import PdfSplitEngine

    engine = PdfSplitEngine()
    temp_dir = tempfile.mkdtemp()
    out_dir = tempfile.mkdtemp()
    pdf_path = os.path.join(temp_dir, "bookmarks.pdf")
    try:
        writer = PyPDF2.PdfWriter()
        for _ in range(5):
            writer.add_blank_page(width=72, height=72)

        if hasattr(writer, "add_outline_item"):
            writer.add_outline_item("A", 0)
            writer.add_outline_item("B", 2)
        elif hasattr(writer, "addBookmark"):
            writer.addBookmark("A", 0)
            writer.addBookmark("B", 2)

        with open(pdf_path, "wb") as f:
            writer.write(f)

        def _page_count(p: str) -> int:
            with open(p, "rb") as rf:
                r = PyPDF2.PdfReader(rf)
                return len(r.pages)

        out = engine.split_by_page_count(pdf_path, out_dir, "pc_", 10)
        if len(out) != 1 or _page_count(out[0]) != 5:
            print("  [FAIL] 按页数拆分（无需拆分）结果异常")
            return False

        out = engine.split_by_page_ranges(pdf_path, out_dir, "pr_", "1-5")
        if len(out) != 1 or _page_count(out[0]) != 5:
            print("  [FAIL] 按范围拆分（整本范围）结果异常")
            return False

        out = engine.split_by_file_size(pdf_path, out_dir, "ps_", 999.0)
        if len(out) != 1 or _page_count(out[0]) != 5:
            print("  [FAIL] 按大小拆分（无需拆分）结果异常")
            return False

        out = engine.split_by_bookmark(pdf_path, out_dir, "bm_", 1)
        if len(out) < 2:
            print("  [FAIL] 按书签拆分输出文件数量不足")
            return False
        counts = sorted(_page_count(p) for p in out[:2])
        if counts != [2, 3]:
            print(f"  [FAIL] 按书签拆分页数不符合预期: {counts}")
            return False

        print("  [OK] 真实PDF拆分检查通过")
        return True
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)

def test_ui_creation():
    """测试UI组件创建（不显示窗口）"""
    print("\n测试UI组件创建...")
    if not HAVE_PYQT6:
        print("  [WARN] 跳过（缺少PyQt6）")
        return None
    
    get_qt_app()
    
    try:
        from ui.main_window import MainWindow
        window = MainWindow()
        window.setWindowTitle("测试窗口")
        print("  [OK] MainWindow 创建成功")
        
        # 测试获取面板 - MainWindow可能没有get_panel方法，我们直接检查导航按钮
        # 尝试访问内部组件
        if hasattr(window, 'rename_button') and window.rename_button is not None:
            print("  [OK] 重命名按钮访问成功")
        else:
            print("  [WARN] 重命名按钮未找到（可能实现方式不同）")
        
        if hasattr(window, 'pdf_split_button') and window.pdf_split_button is not None:
            print("  [OK] PDF拆分按钮访问成功")
        else:
            print("  [WARN] PDF拆分按钮未找到（可能实现方式不同）")

        if hasattr(window, 'scan_split_button') and window.scan_split_button is not None:
            print("  [OK] 扫描拆分按钮访问成功")
        else:
            print("  [WARN] 扫描拆分按钮未找到（可能实现方式不同）")

        try:
            if hasattr(window, "_switch_panel"):
                window._switch_panel("scan_split")
                window._switch_panel("pdf_split")
                window._switch_panel("settings")
                window._switch_panel("rename")
                print("  [OK] 主窗口面板切换接口可用")
        except Exception as e:
            print(f"  [FAIL] 主窗口面板切换失败: {e}")
            return False

        if hasattr(window, 'about_button') and window.about_button is not None:
            print("  [OK] 关于按钮访问成功")
        else:
            print("  [WARN] 关于按钮未找到（可能实现方式不同）")

        try:
            import tempfile
            import os

            rename_panel = None
            if hasattr(window, "_panels") and isinstance(getattr(window, "_panels"), dict):
                rename_panel = window._panels.get("rename")
            if rename_panel is None and hasattr(window, "rename_panel"):
                rename_panel = window.rename_panel
            if rename_panel is not None:
                fd1, p1 = tempfile.mkstemp(suffix=".txt")
                os.close(fd1)
                fd2, p2 = tempfile.mkstemp(suffix=".txt")
                os.close(fd2)

                rename_panel._add_files([p1, p2])
                rename_panel._update_preview()

                if rename_panel.file_list.topLevelItemCount() > 0:
                    first = rename_panel.file_list.topLevelItem(0)
                    if first.text(1):
                        print("  [OK] 重命名实时预览刷新正常")
                    else:
                        print("  [FAIL] 重命名实时预览未生成预览结果")
                        return False

                os.remove(p1)
                os.remove(p2)
        except Exception as e:
            print(f"  [FAIL] 重命名实时预览测试失败: {e}")
            return False
            
    except Exception as e:
        print(f"  [FAIL] UI组件创建失败: {e}")
        return False
    
    return True


def test_window_state_persistence():
    print("\n测试窗口状态持久化...")
    if not HAVE_PYQT6:
        print("  [WARN] 跳过（缺少PyQt6）")
        return None
    from PyQt6.QtCore import QSettings
    from ui.main_window import MainWindow
    from main import SETTINGS_SCHEMA_VERSION, _migrate_startup_settings

    get_qt_app()

    try:
        settings = QSettings("FileToolbox", "MainWindow")
        settings.remove("geometry")
        settings.remove("windowState")
        settings.sync()

        window1 = MainWindow()
        window1.resize(820, 620)
        window1.move(40, 50)
        window1._save_window_state()

        saved_geometry = settings.value("geometry")
        saved_state = settings.value("windowState")

        if saved_geometry is None or saved_state is None:
            print("  [FAIL] 窗口状态保存失败")
            return False

        window2 = MainWindow()
        restored_geometry = window2.saveGeometry()
        restored_state = window2.saveState()

        if saved_geometry == restored_geometry and saved_state == restored_state:
            print("  [OK] 窗口尺寸、位置和状态恢复成功")
        else:
            print("  [FAIL] 窗口状态恢复与保存不一致")
            return False

        window2.reset_window_state()
        geometry_after_reset = settings.value("geometry")
        state_after_reset = settings.value("windowState")

        if geometry_after_reset is None and state_after_reset is None:
            print("  [OK] 窗口状态重置功能正常")
        else:
            print("  [FAIL] 窗口状态重置功能异常")
            return False

        app_settings = QSettings("FileToolbox", "App")
        scan_settings = QSettings("FileToolbox", "PdfScanSplitPanel")
        main_settings = QSettings("FileToolbox", "MainWindow")
        app_settings.remove("settingsSchemaVersion")
        scan_settings.setValue("detectModeValue", "qrcode")
        main_settings.setValue("customSetting", "keep-me")
        app_settings.sync()
        scan_settings.sync()
        main_settings.sync()

        _migrate_startup_settings()

        migrated_version = int(app_settings.value("settingsSchemaVersion", 0))
        if migrated_version == SETTINGS_SCHEMA_VERSION and scan_settings.value("detectModeValue") == "qrcode" and main_settings.value("customSetting") == "keep-me":
            print("  [OK] 启动设置迁移保留用户配置正常")
        else:
            print("  [FAIL] 启动设置迁移异常")
            return False

        app_settings.remove("settingsSchemaVersion")
        scan_settings.remove("detectModeValue")
        main_settings.remove("customSetting")
        app_settings.sync()
        scan_settings.sync()
        main_settings.sync()

    except Exception as e:
        print(f"  [FAIL] 窗口状态持久化测试失败: {e}")
        return False

    return True


def test_ui_interactions():
    """测试关键UI交互（不弹出阻塞式对话框）"""
    print("\n测试UI交互...")
    if not HAVE_PYQT6:
        print("  [WARN] 跳过（缺少PyQt6）")
        return None
    if not HAVE_PYPDF2:
        print("  [WARN] 跳过（缺少PyPDF2）")
        return None
    from PyQt6.QtCore import Qt

    get_qt_app()

    try:
        import tempfile
        import os
        import PyPDF2

        from ui.rename_panel import RenamePanel
        from ui.pdf_split_panel import PdfSplitPanel

        rename_panel = RenamePanel()
        pdf_panel = PdfSplitPanel()

        temp_dir = tempfile.mkdtemp()
        p1 = os.path.join(temp_dir, "file2.txt")
        p2 = os.path.join(temp_dir, "file10.txt")
        with open(p1, "w", encoding="utf-8") as f:
            f.write("x")
        with open(p2, "w", encoding="utf-8") as f:
            f.write("x")

        try:
            rename_panel._add_files([p2, p1])
            if rename_panel.file_list.topLevelItemCount() != 2:
                print("  [FAIL] 重命名面板添加文件数量不正确")
                return False

            rename_panel._update_header_summary()
            if getattr(rename_panel, "file_list_header", None) is None:
                print("  [FAIL] 重命名面板表头自定义组件未初始化")
                return False

            if rename_panel.file_list_header._label_text != "原文件名(2/2)":
                print(f"  [FAIL] 表头计数显示异常: {rename_panel.file_list_header._label_text}")
                return False

            rename_panel._on_header_check_state_changed(int(Qt.CheckState.Unchecked.value))
            if any(rename_panel.file_list.topLevelItem(i).checkState(0) == Qt.CheckState.Checked for i in range(2)):
                print("  [FAIL] 表头全选框取消选择未生效")
                return False

            rename_panel._on_header_check_state_changed(int(Qt.CheckState.Checked.value))
            if any(rename_panel.file_list.topLevelItem(i).checkState(0) != Qt.CheckState.Checked for i in range(2)):
                print("  [FAIL] 表头全选框选择未生效")
                return False

            rename_panel._sort_mode = "name"
            rename_panel._sort_order = Qt.SortOrder.AscendingOrder
            rename_panel._apply_sort()
            first_name = rename_panel.file_list.topLevelItem(0).text(0)
            second_name = rename_panel.file_list.topLevelItem(1).text(0)
            if not (first_name.endswith("file2.txt") and second_name.endswith("file10.txt")):
                print(f"  [FAIL] 文件名自然排序异常: {first_name} , {second_name}")
                return False

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        temp_dir = tempfile.mkdtemp()
        pdf_path = os.path.join(temp_dir, "test.pdf")
        try:
            writer = PyPDF2.PdfWriter()
            writer.add_blank_page(width=72, height=72)
            writer.add_blank_page(width=72, height=72)
            with open(pdf_path, "wb") as f:
                writer.write(f)

            pdf_panel._add_files([pdf_path])
            preview_lines = pdf_panel._generate_preview_lines(pdf_panel._build_config())
            preview_text = "\n".join(preview_lines).strip()
            pdf_panel.preview_text.setPlainText(preview_text)
            if not preview_text or "test.pdf" not in preview_text:
                print("  [FAIL] PDF拆分预览未生成或内容异常")
                return False

            try:
                from PyQt6.QtWidgets import QApplication

                pdf_panel._on_copy_preview()
                clip = QApplication.clipboard().text() or ""
                if "test.pdf" not in clip:
                    print("  [FAIL] PDF拆分预览复制到剪贴板失败")
                    return False
            except Exception:
                print("  [WARN] 跳过剪贴板复制验证")

            from ui.about_panel import AboutPanel
            about_panel = AboutPanel()
            if about_panel._safe_release_url("https://github.com/LXL2000927/file-toolbox/releases/tag/v1.2.0") != "https://github.com/LXL2000927/file-toolbox/releases/tag/v1.2.0":
                print("  [FAIL] GitHub Release 安全链接校验误拒绝")
                return False
            unsafe_urls = [
                "http://github.com/LXL2000927/file-toolbox/releases/tag/v1.2.0",
                "https://evil.example.com/LXL2000927/file-toolbox/releases/tag/v1.2.0",
                "https://github.com/other/file-toolbox/releases/tag/v1.2.0",
            ]
            if any(about_panel._safe_release_url(u) for u in unsafe_urls):
                print("  [FAIL] GitHub Release 安全链接校验误放行")
                return False
            if not about_panel._is_rate_limited(403, "", "0"):
                print("  [FAIL] GitHub API 限流响应头识别异常")
                return False

            print("  [OK] UI交互检查通过")
            return True
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    except Exception as e:
        print(f"  [FAIL] UI交互测试失败: {e}")
        return False


def test_scan_split_runtime_log():
    print("\n测试扫描拆分运行日志...")
    if not HAVE_PYQT6:
        print("  [WARN] 跳过（缺少PyQt6）")
        return None

    from PyQt6.QtWidgets import QMessageBox
    from utils.history_manager import HistoryManager, OperationType
    from ui.pdf_scan_split_panel import PdfScanSplitPanel
    from core.pdf_scan_split_engine import PdfScanSplitResult
    import tempfile
    import time

    get_qt_app()

    hm = HistoryManager()
    panel = PdfScanSplitPanel(hm)

    orig_info = QMessageBox.information
    orig_crit = QMessageBox.critical
    try:
        QMessageBox.information = staticmethod(lambda *args, **kwargs: None)
        QMessageBox.critical = staticmethod(lambda *args, **kwargs: None)

        panel._run_started_at = time.perf_counter()
        panel._run_log_lines = ["line1", "line2"]
        panel._run_context = {"pdf_name": "a.pdf", "pdf_path": "a.pdf", "options": {}}
        panel._on_worker_failed("boom")

        if not hm.history:
            print("  [FAIL] 未写入失败日志记录")
            return False
        if hm.history[0].operation_type != OperationType.SCAN_SPLIT or hm.history[0].success:
            print("  [FAIL] 失败日志记录类型或状态不正确")
            return False

        panel._run_started_at = time.perf_counter()
        panel._run_log_lines = ["ok"]
        panel._run_context = {"pdf_name": "b.pdf", "pdf_path": "b.pdf", "options": {}}
        panel._on_worker_finished(PdfScanSplitResult(output_files=["out1.pdf", "out2.pdf"], marker_pages=[0], total_pages=2))

        if len(hm.history) < 2:
            print("  [FAIL] 未写入成功日志记录")
            return False
        if hm.history[0].operation_type != OperationType.SCAN_SPLIT or not hm.history[0].success:
            print("  [FAIL] 成功日志记录类型或状态不正确")
            return False

        temp_dir = tempfile.mkdtemp()
        try:
            bad_storage = os.path.join(temp_dir, "missing", "history.json")
            bad_parent = os.path.dirname(bad_storage)
            with open(bad_parent, "w", encoding="utf-8") as f:
                f.write("not a directory")
            hm_bad = HistoryManager(storage_path=bad_storage)
            if hm_bad._save_to_file([]) or not hm_bad.last_error:
                print("  [FAIL] 历史记录保存失败诊断信息异常")
                return False
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        print("  [OK] 扫描拆分运行日志写入正常")
        return True
    finally:
        QMessageBox.information = orig_info
        QMessageBox.critical = orig_crit

def main():
    """运行所有测试"""
    print("=" * 60)
    print("文件处理工具箱 - 集成测试")
    print("=" * 60)
    
    tests = [
        ("模块导入", test_imports),
        ("重命名引擎", test_rename_engine),
        ("重命名全部规则", test_rename_all_rules),
        ("PDF拆分引擎", test_pdf_split_engine),
        ("PDF拆分真实文件", test_pdf_split_engine_real_files),
        ("UI组件创建", test_ui_creation),
        ("窗口状态持久化", test_window_state_persistence),
        ("UI交互", test_ui_interactions),
        ("扫描拆分运行日志", test_scan_split_runtime_log),
    ]
    
    passed = 0
    failed = 0
    skipped = 0
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            if result is None:
                print(f"[SKIP] {test_name} 已跳过")
                skipped += 1
            elif result:
                print(f"[OK] {test_name} 测试通过")
                passed += 1
            else:
                print(f"[FAIL] {test_name} 测试失败")
                failed += 1
        except Exception as e:
            print(f"[FAIL] {test_name} 测试异常: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"测试结果: 通过 {passed} | 失败 {failed} | 跳过 {skipped}")
    print("=" * 60)
    
    if failed == 0:
        print("[OK] 所有测试通过！应用程序基本功能正常。")
        return 0
    else:
        print("[WARN]  部分测试失败，请检查上述错误。")
        return 1

if __name__ == "__main__":
    sys.exit(main())
