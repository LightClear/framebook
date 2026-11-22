import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def _dpapi(data, encrypt):
    """Use the current Windows user's DPAPI key without displaying system UI."""
    buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.addressof(buf))
    result = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    transform = library.CryptProtectData if encrypt else library.CryptUnprotectData
    if not transform(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(result.data))


def read_config(path):
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return {}
    return json.loads(_dpapi(data, False))


def save_config(path, config):
    encrypted = _dpapi(json.dumps(config, ensure_ascii=False).encode(), True)
    with tempfile.NamedTemporaryFile(dir=Path(path).parent, delete=False) as tmp:
        name = tmp.name
        try:
            tmp.write(encrypted)
            tmp.close()
            os.replace(name, path)
        finally:
            tmp.close()
            Path(name).unlink(missing_ok=True)
