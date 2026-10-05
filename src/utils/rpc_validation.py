"""Reject path shape coercions at the JSON-RPC boundary as well as in Electron."""
import os


def validate_path_params(method, params):
    if not isinstance(params, dict):
        raise ValueError("参数必须是对象")
    inputs = []

    def path(value, *, optional=False, output=False):
        if not isinstance(value, str) or (not value and not optional):
            raise ValueError("文件路径必须是字符串")
        if value and (not os.path.isabs(value) or any(ord(c) < 32 for c in value)):
            raise ValueError("文件路径必须是绝对路径")
        if value and not output:
            inputs.append(value)

    def batch(value):
        if not isinstance(value, list) or len(value) > 5000:
            raise ValueError("文件列表必须是一维数组，最多 5000 个文件")
        for item in value:
            path(item)

    if method in ("rename.preview", "rename.execute"):
        batch(params.get("files"))
        path(params.get("output_dir", ""), optional=True, output=True)
    elif method == 'pdf_tools.run':
        batch(params.get('files'))
        if not 1 <= len(params['files']) <= 3000:
            raise ValueError('请选择 1–3000 个文件')
        options = params.get('options', {})
        if not isinstance(options, dict):
            raise ValueError('PDF 选项必须是对象')
        path(options.get('output_dir', ''), optional=True, output=True)
    elif method.startswith("pdf_split."):
        if method in ("pdf_split.preview_many", "pdf_split.execute_async"):
            batch(params.get("pdf_paths"))
        else:
            path(params.get("pdf_path"))
        config = params.get("config", {})
        if not isinstance(config, dict):
            raise ValueError("PDF 配置必须是对象")
        path(config.get("output_dir", ""), optional=True, output=True)
    elif method.startswith("scan_split."):
        if method != "scan_split.preview_reference":
            path(params.get("pdf_path"))
        path(params.get("reference_image_path", ""), optional=method != "scan_split.preview_reference")
        path(params.get("output_dir", ""), optional=True, output=True)
    return inputs
