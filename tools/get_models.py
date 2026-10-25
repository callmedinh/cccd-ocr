"""Tải dữ liệu ngôn ngữ Tesseract cần cho tiếng Việt vào ``models/tessdata``.

Chạy một lần sau khi cài Tesseract:

    python tools/get_models.py

Vì sao không chép vào ``C:\\Program Files\\Tesseract-OCR\\tessdata``: thư mục
đó cần quyền Administrator. Tesseract cho phép trỏ tới thư mục dữ liệu khác
bằng ``--tessdata-dir``, nên dự án tự giữ bộ dữ liệu riêng.
"""

from __future__ import annotations

import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cccd_ocr.console import ensure_utf8_console

TARGET = ROOT / "models" / "tessdata"

BASE_URL = "https://github.com/tesseract-ocr/tessdata_best/raw/main"

# vie: tiếng Việt (bắt buộc) — eng/osd: dự phòng và phát hiện hướng ảnh
FILES = {
    "vie.traineddata": f"{BASE_URL}/vie.traineddata",
    "eng.traineddata": f"{BASE_URL}/eng.traineddata",
    "osd.traineddata": f"{BASE_URL}/osd.traineddata",
}

# Cần cả thư mục configs để Tesseract hiểu đầu ra 'tsv'
CONFIG_FILES = ("tsv", "txt", "hocr")


def download(url: str, destination: Path) -> None:
    print(f"  đang tải {destination.name} ...", flush=True)
    with urllib.request.urlopen(url, timeout=300) as response, \
            destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    size_mb = destination.stat().st_size / 1_048_576
    print(f"  xong {destination.name} ({size_mb:.1f} MB)")


def find_system_tessdata() -> Path | None:
    """Thư mục tessdata của bản Tesseract đã cài (để chép configs)."""
    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tessdata"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tessdata"),
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/local/share/tessdata"),
        Path("/opt/homebrew/share/tessdata"),
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def main() -> int:
    ensure_utf8_console()
    TARGET.mkdir(parents=True, exist_ok=True)

    for name, url in FILES.items():
        destination = TARGET / name
        if destination.exists() and destination.stat().st_size > 100_000:
            print(f"  đã có {name}, bỏ qua")
            continue
        try:
            download(url, destination)
        except Exception as error:
            print(f"  LỖI khi tải {name}: {error}")
            if name == "vie.traineddata":
                return 1

    system = find_system_tessdata()
    configs_dir = TARGET / "configs"
    if system is not None and (system / "configs").is_dir():
        configs_dir.mkdir(exist_ok=True)
        for config in CONFIG_FILES:
            source = system / "configs" / config
            if source.exists():
                shutil.copy2(source, configs_dir / config)
        print(f"  đã chép configs: {', '.join(CONFIG_FILES)}")
    elif not configs_dir.exists():
        print("  CẢNH BÁO: không tìm thấy thư mục configs của Tesseract.")
        print("           Nếu Tesseract báo lỗi 'tsv', hãy chép thủ công")
        print("           <tesseract>\\tessdata\\configs vào models\\tessdata\\configs")

    print(f"\nXong. Dữ liệu nằm ở: {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
