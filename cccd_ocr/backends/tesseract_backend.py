"""Backend Tesseract 5 (model ``vie``) — chính xác nhất cho tiếng Việt offline.

Vì sao dùng Tesseract làm mặc định: bộ nhận dạng ``latin`` của PP-OCRv5 có
từ điển **thuần ASCII**, không thể sinh ra ``ộ ỷ ệ ĩ`` — dùng nó cho CCCD thì
mất sạch dấu. Tesseract với ``vie.traineddata`` đọc đúng dấu tiếng Việt và
trả về toạ độ từng từ (dùng để ghép dòng).

Không cần ``pytesseract``: gọi thẳng binary và đọc output TSV.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..models import TextItem

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_CANDIDATE_BINARIES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
)

# Ký tự cho phép ở vùng MRZ (chỉ chữ hoa, số và dấu '<')
MRZ_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"


class TesseractNotFound(RuntimeError):
    """Không tìm thấy binary tesseract."""


def find_tesseract() -> str:
    """Tìm binary tesseract: biến môi trường -> PATH -> đường dẫn quen thuộc."""
    env = os.environ.get("TESSERACT_CMD")
    if env and Path(env).exists():
        return env
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in _CANDIDATE_BINARIES:
        if candidate and Path(candidate).exists():
            return candidate
    raise TesseractNotFound(
        "Không tìm thấy Tesseract. Cài bằng: winget install UB-Mannheim.TesseractOCR "
        "hoặc đặt biến môi trường TESSERACT_CMD trỏ tới tesseract.exe"
    )


def default_tessdata_dir() -> Path | None:
    """Thư mục tessdata cục bộ của dự án nếu có ``vie.traineddata``."""
    project = Path(__file__).resolve().parents[2]
    local = project / "models" / "tessdata"
    if (local / "vie.traineddata").exists():
        return local
    env = os.environ.get("TESSDATA_PREFIX")
    if env and (Path(env) / "vie.traineddata").exists():
        return Path(env)
    return None


@dataclass
class TesseractOptions:
    """Tuỳ chọn nhận dạng của Tesseract."""

    lang: str = "vie"         # 'vie' đọc dấu tốt nhất; 'vie+eng' thiên về ASCII
    psm: int = 3              # 3 = tự động chia khối, 6 = một khối text thống nhất
    oem: int = 1              # 1 = LSTM, chính xác nhất
    min_conf: float = 25.0    # bỏ từ có conf thấp (0-100)
    dpi: int = 300


class TesseractEngine:
    """Đối tượng OCR dùng lại được (không nạp model mỗi lần)."""

    def __init__(self, options: TesseractOptions | None = None) -> None:
        self.options = options or TesseractOptions()
        self.binary = find_tesseract()
        data_dir = default_tessdata_dir()
        self._configs = _configs(data_dir)

    # ------------------------------------------------------------------- read
    def read_image(self, image: np.ndarray, *, lang: str | None = None,
                   psm: int | None = None, whitelist: str | None = None,
                   min_conf: float | None = None) -> list[TextItem]:
        """OCR một ảnh BGR (numpy) -> danh sách dòng text kèm hộp bao."""
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            return []
        args: list[str] = [
            self.binary, "stdin", "stdout",
            "-l", lang or self.options.lang,
            "--psm", str(psm if psm is not None else self.options.psm),
            "--oem", str(self.options.oem),
            "-c", "preserve_interword_spaces=1",
            "--dpi", str(self.options.dpi),
        ]
        args.extend(self._configs)
        if whitelist:
            args.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
        args.append("tsv")

        try:
            completed = subprocess.run(
                args,
                input=encoded.tobytes(),
                capture_output=True,
                timeout=120,
                creationflags=_NO_WINDOW,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return []
        if completed.returncode != 0 and not completed.stdout:
            raise RuntimeError(
                f"tesseract lỗi: {completed.stderr.decode('utf-8', 'replace')[:400]}"
            )
        return parse_tsv(
            completed.stdout.decode("utf-8", "replace"),
            min_conf=self.options.min_conf if min_conf is None else min_conf,
        )

    def read_mrz(self, image: np.ndarray) -> list[TextItem]:
        """OCR riêng vùng MRZ: tiếng Anh + chỉ ký tự MRZ, chế độ một khối.

        Lưu ý: khi dùng ``tessedit_char_whitelist``, Tesseract báo ``conf = 0``
        cho mọi từ (không phải lỗi đọc), nên phải hạ ngưỡng lọc confidence.
        """
        cropped = crop_bottom(image)
        items = self.read_image(cropped, lang="eng", psm=6,
                                whitelist=MRZ_WHITELIST, min_conf=0.0)
        if not items:
            items = self.read_image(cropped, lang="eng", psm=11, min_conf=0.0)
        return items

    def version(self) -> str:
        try:
            out = subprocess.run([self.binary, "--version"], capture_output=True,
                                 timeout=20, creationflags=_NO_WINDOW, check=False)
            return out.stdout.decode("utf-8", "replace").strip().splitlines()[0]
        except Exception:
            return "unknown"


def _configs(tessdata_dir: Path | None) -> list[str]:
    configs: list[str] = []
    if tessdata_dir is not None:
        configs.extend(["--tessdata-dir", str(tessdata_dir)])
    return configs


def crop_bottom(image: np.ndarray, ratio: float = 0.34) -> np.ndarray:
    """Cắt phần dưới ảnh (nơi MRZ 3 dòng luôn nằm trên CCCD)."""
    height = image.shape[0]
    top = int(height * (1 - ratio))
    return image[top:]


# ------------------------------------------------------------------ TSV parse

_TSV_FIELDS = ("level", "page_num", "block_num", "par_num", "line_num", "word_num",
               "left", "top", "width", "height", "conf", "text")


def parse_tsv(payload: str, *, min_conf: float = 25.0) -> list[TextItem]:
    """Chuyển output TSV thành **các ô theo từng từ**.

    Trả về mức từ (word) chứ không phải mức dòng: Tesseract hay gộp hai dòng
    thị giác khác nhau vào cùng một ``line_num`` (nhất là với bố cục nhãn +
    giá trị của CCCD), nên để ``group_lines`` tự ghép dòng theo toạ độ sẽ
    chính xác hơn.
    """
    reader = csv.DictReader(io.StringIO(payload), delimiter="\t", fieldnames=_TSV_FIELDS,
                            quoting=csv.QUOTE_NONE)
    items: list[TextItem] = []
    for row in reader:
        if row.get("level") != "5":
            continue  # 5 = word
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            continue
        if conf < min_conf:
            continue
        left, top = int(row["left"]), int(row["top"])
        width, height = int(row["width"]), int(row["height"])
        items.append(TextItem(
            text=text,
            score=conf / 100.0,
            box=[(float(left), float(top)), (float(left + width), float(top)),
                 (float(left + width), float(top + height)), (float(left), float(top + height))],
        ))
    return items
