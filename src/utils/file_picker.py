from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtCore import QDir


class FilePicker:
    @staticmethod
    def get_existing_directory(parent=None, caption="选择目录", directory=""):
        return QFileDialog.getExistingDirectory(
            parent=parent,
            caption=caption,
            directory=directory or QDir.homePath(),
            options=QFileDialog.Option.ShowDirsOnly
        )
    
    @staticmethod
    def get_open_file_name(parent=None, caption="选择文件", directory="", 
                          filter="所有文件 (*.*)"):
        file_path, _ = QFileDialog.getOpenFileName(
            parent=parent,
            caption=caption,
            directory=directory or QDir.homePath(),
            filter=filter
        )
        return file_path
    
    @staticmethod
    def get_open_file_names(parent=None, caption="选择文件", directory="", 
                           filter="所有文件 (*.*)"):
        file_paths, _ = QFileDialog.getOpenFileNames(
            parent=parent,
            caption=caption,
            directory=directory or QDir.homePath(),
            filter=filter
        )
        return file_paths
    
    @staticmethod
    def get_save_file_name(parent=None, caption="保存文件", directory="", 
                          filter="所有文件 (*.*)", default_extension=""):
        file_path, _ = QFileDialog.getSaveFileName(
            parent=parent,
            caption=caption,
            directory=directory or QDir.homePath(),
            filter=filter
        )
        
        if file_path and default_extension:
            if not file_path.endswith(default_extension):
                file_path += default_extension
        
        return file_path
    
    @staticmethod
    def get_pdf_files(parent=None):
        return FilePicker.get_open_file_names(
            parent=parent,
            caption="选择PDF文件",
            filter="PDF文件 (*.pdf)"
        )
    
    @staticmethod
    def get_any_files(parent=None):
        return FilePicker.get_open_file_names(
            parent=parent,
            caption="选择文件",
            filter="所有文件 (*.*)"
        )
