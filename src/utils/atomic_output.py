"""Publish a complete new file without ever replacing an existing destination."""
import os
import uuid
from pathlib import Path

from src.utils.path_utils import _safe_output_name


def publish_exclusive(temporary, target):
    """Atomically publish on the same filesystem, refusing any existing target."""
    if os.name == 'nt':
        os.rename(temporary, target)
    else:
        os.link(temporary, target)
        os.unlink(temporary)


def write_new_output(directory, filename, write, cancel_check=None):
    directory = Path(directory)
    if not directory.is_absolute():
        raise ValueError('请选择绝对路径输出目录')
    filename = _safe_output_name(filename, 'output.pdf', require_pdf=False)
    stem, suffix = os.path.splitext(filename)
    stem = stem[:110]
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f'.toolbox-{uuid.uuid4().hex}.tmp'
    try:
        with temporary.open('xb') as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        if cancel_check and cancel_check():
            raise InterruptedError('已取消')
        for number in range(1, 10001):
            target = directory / f'{stem}{"" if number == 1 else "_" + str(number)}{suffix}'
            try:
                publish_exclusive(temporary, target)
                return str(target)
            except FileExistsError:
                continue
        raise OSError('同名文件过多，请更换输出名称')
    finally:
        temporary.unlink(missing_ok=True)
