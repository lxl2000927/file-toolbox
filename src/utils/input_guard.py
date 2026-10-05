"""Bind deferred reads to the file identities captured by the trusted IPC host."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import stat


_identities = ContextVar("input_identities", default=None)


def _opened_path(source):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        final_path = kernel.GetFinalPathNameByHandleW
        final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        final_path.restype = wintypes.DWORD
        handle = msvcrt.get_osfhandle(source.fileno())
        size = final_path(handle, None, 0, 0)
        if not size:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(size + 1)
        written = final_path(handle, buffer, len(buffer), 0)
        if not written or written >= len(buffer):
            raise ValueError("无法核实已打开文件的路径")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            return "\\\\" + value[8:]
        return value[4:] if value.startswith("\\\\?\\") else value
    return os.readlink(f"/proc/self/fd/{source.fileno()}")


@contextmanager
def input_context(identities, *, required=False):
    if required and not isinstance(identities, dict):
        raise ValueError("缺少输入文件授权信息")
    token = _identities.set(identities)
    try:
        yield
    finally:
        _identities.reset(token)


def _open_for_rename(path):
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # GENERIC_READ | DELETE, share reads/writes but not deletion/replacement.
    handle = create(os.path.abspath(path), 0x80010000, 3, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close(handle)
        raise
    return os.fdopen(descriptor, "rb")


@contextmanager
def open_input(path, *, for_rename=False):
    identities = _identities.get()
    expected = None
    if identities is not None:
        expected = identities.get(path)
        if not isinstance(expected, dict):
            raise ValueError("输入文件未经授权，请重新选择")
    with (_open_for_rename(path) if for_rename and os.name == "nt" else open(path, "rb")) as source:
        actual = os.fstat(source.fileno())
        if not stat.S_ISREG(actual.st_mode):
            raise ValueError("输入路径不是普通文件")
        if expected is not None:
            # libuv uses the low 32-bit Windows volume serial. Python 3.13+
            # exposes the full 64-bit serial; Python <=3.12 already uses 32 bits.
            device = actual.st_dev & 0xFFFFFFFF if os.name == "nt" else actual.st_dev
            observed = {"dev": str(device), "ino": str(actual.st_ino),
                        "size": str(actual.st_size), "mtime_ns": str(actual.st_mtime_ns)}
            if any(expected.get(key) != value for key, value in observed.items()):
                raise ValueError("输入文件已发生变化，请重新选择后重试")
            canonical = expected.get("canonical")
            if (not isinstance(canonical, str)
                    or os.path.normcase(os.path.normpath(_opened_path(source))) != os.path.normcase(os.path.normpath(canonical))):
                raise ValueError("输入文件已发生变化，请重新选择后重试")
        yield source


def rename_input(path, destination):
    if os.name != "nt":
        # The desktop distribution targets Windows. Refuse a guarded rename on
        # platforms without its handle-based primitive rather than weaken it.
        if _identities.get() is not None:
            raise OSError("当前平台不支持受保护的原地重命名")
        return os.rename(path, destination)
    import ctypes
    from ctypes import wintypes
    import msvcrt
    target = os.path.abspath(destination)
    encoded = target.encode("utf-16-le")
    class RenameInfo(ctypes.Structure):
        _fields_ = [("ReplaceIfExists", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE),
                    ("FileNameLength", wintypes.DWORD), ("FileName", ctypes.c_ubyte * len(encoded))]
    info = RenameInfo()
    info.FileNameLength = len(encoded)
    info.FileName[:] = encoded
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    rename = kernel.SetFileInformationByHandle
    rename.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    rename.restype = wintypes.BOOL
    with open_input(path, for_rename=True) as source:
        if not rename(msvcrt.get_osfhandle(source.fileno()), 3, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())


def read_input_bytes(path, max_bytes):
    with open_input(path) as source:
        before = os.fstat(source.fileno())
        if before.st_size > max_bytes:
            raise ValueError("输入文件超过允许的大小上限")
        # read(n) can allocate n bytes even for a tiny file. Use the checked
        # actual length instead of reserving hundreds of MiB per small PDF.
        content = source.read(before.st_size + 1)
        after = os.fstat(source.fileno())
        if len(content) > max_bytes:
            raise ValueError("输入文件超过允许的大小上限")
        if len(content) != before.st_size or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
            raise ValueError("输入文件已发生变化，请重新选择后重试")
        return content
