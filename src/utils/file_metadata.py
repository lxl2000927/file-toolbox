"""Copy metadata through open handles, without following destination links."""
import os


def copy_stream_metadata(source, destination):
    destination.flush()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_time = kernel.GetFileTime
        set_time = kernel.SetFileTime
        time_pointer = ctypes.POINTER(wintypes.FILETIME)
        get_time.argtypes = set_time.argtypes = [wintypes.HANDLE, time_pointer, time_pointer, time_pointer]
        get_time.restype = set_time.restype = wintypes.BOOL
        access, modified = wintypes.FILETIME(), wintypes.FILETIME()
        if not get_time(msvcrt.get_osfhandle(source.fileno()), None, ctypes.byref(access), ctypes.byref(modified)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not set_time(msvcrt.get_osfhandle(destination.fileno()), None, ctypes.byref(access), ctypes.byref(modified)):
            raise ctypes.WinError(ctypes.get_last_error())
        if os.fstat(source.fileno()).st_file_attributes & 1:  # FILE_ATTRIBUTE_READONLY
            class BasicInfo(ctypes.Structure):
                _fields_ = [("CreationTime", ctypes.c_longlong), ("LastAccessTime", ctypes.c_longlong),
                            ("LastWriteTime", ctypes.c_longlong), ("ChangeTime", ctypes.c_longlong),
                            ("FileAttributes", wintypes.DWORD)]
            info = BasicInfo()
            info.FileAttributes = os.fstat(destination.fileno()).st_file_attributes | 1
            set_info = kernel.SetFileInformationByHandle
            set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            set_info.restype = wintypes.BOOL
            if not set_info(msvcrt.get_osfhandle(destination.fileno()), 0, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
    else:
        metadata = os.fstat(source.fileno())
        os.utime(destination.fileno(), ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        os.fchmod(destination.fileno(), metadata.st_mode & 0o777)
