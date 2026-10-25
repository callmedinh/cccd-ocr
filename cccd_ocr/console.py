"""Bảo đảm console Windows in được tiếng Việt.

Mặc định console Windows dùng bảng mã cp1252/cp437, in tên kiểu "NGUYỄN VĂN AN"
sẽ ném ``UnicodeEncodeError`` và tắt chương trình giữa chừng. Hàm dưới đây đổi
luồng ra sang UTF-8 và đặt code page của console về 65001.
"""

from __future__ import annotations

import sys


def ensure_utf8_console() -> None:
    """Gọi một lần ở đầu mỗi chương trình có in chữ tiếng Việt."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass  # luồng đã bị chuyển hướng, không đổi được

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
