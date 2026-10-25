#!/usr/bin/env python
"""Giao diện kéo-thả: mở trình duyệt, kéo ảnh CCCD vào, tải Excel.

    python gui.py
    python gui.py --port 9000 --no-browser
"""

from __future__ import annotations

import argparse
import sys

from cccd_ocr.console import ensure_utf8_console
from cccd_ocr.gui import serve


def main() -> int:
    ensure_utf8_console()
    parser = argparse.ArgumentParser(
        description="Giao diện kéo-thả để quét ảnh Căn cước công dân",
    )
    parser.add_argument("--port", type=int, default=8765,
                        help="Cổng máy chủ cục bộ (mặc định 8765)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Địa chỉ lắng nghe (mặc định 127.0.0.1 — chỉ máy này)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Không tự mở trình duyệt")
    args = parser.parse_args()

    try:
        serve(args.host, args.port, open_browser=not args.no_browser)
    except OSError as error:
        print(f"Không mở được cổng {args.port}: {error}")
        print(f"Thử cổng khác: python gui.py --port {args.port + 1}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
