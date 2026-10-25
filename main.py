#!/usr/bin/env python
"""Điểm vào: quét ảnh CCCD -> Excel/CSV.

    python main.py <ảnh hoặc thư mục> [-o output.xlsx] [--csv]
"""

import sys

from cccd_ocr.cli import main

if __name__ == "__main__":
    sys.exit(main())
