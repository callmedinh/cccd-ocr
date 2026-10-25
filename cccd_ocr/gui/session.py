"""Phiên làm việc của GUI: ảnh đã tải lên, tiến độ quét, kết quả.

Tách khỏi phần HTTP để có thể kiểm thử mà không cần mở cổng mạng.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..aggregate import best_mrz, pick_best, reconcile
from ..cli import group_records
from ..models import CardRecord
from ..ocr_engine import OcrEngine, OcrOptions
from ..parser import parse_card
from ..preprocess import load_image

_LOGGER = logging.getLogger(__name__)

THUMB_MAX = 260
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

# Trường hiển thị trong bảng kết quả (đúng thứ tự cột)
TABLE_FIELDS = (
    "id_number", "full_name", "date_of_birth", "sex", "nationality",
    "place_of_origin", "place_of_residence", "date_of_issue", "date_of_expiry",
    "personal_identification", "issue_authority",
)


@dataclass
class Upload:
    """Một ảnh người dùng đã kéo vào."""

    id: str
    name: str
    path: Path
    thumb: Path
    side: str = ""       # 'front' | 'back' | '' (chưa xác định)


@dataclass
class Session:
    """Toàn bộ trạng thái của một lần làm việc."""

    work_dir: Path
    uploads: dict[str, Upload] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    running: bool = False
    done: int = 0
    total: int = 0
    current: str = ""
    error: str = ""
    engine_name: str = ""
    started_at: float = 0.0
    # Chỉ trả về cho client một lần, tránh gửi lặp
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------------ ảnh
    def add_upload(self, name: str, raw: bytes, side: str = "") -> Upload:
        """Lưu ảnh + tạo thumbnail, trả về bản ghi ``Upload``."""
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError(f"Định dạng không hỗ trợ: {suffix or '(không rõ)'}")
        if not raw:
            raise ValueError("Tệp rỗng")

        upload_id = uuid.uuid4().hex[:12]
        path = self.work_dir / f"{upload_id}{suffix}"
        path.write_bytes(raw)
        thumb = self.work_dir / f"{upload_id}_thumb.jpg"
        _make_thumbnail(path, thumb)

        upload = Upload(id=upload_id, name=name, path=path, thumb=thumb, side=side)
        self.uploads[upload_id] = upload
        self.order.append(upload_id)
        return upload

    def remove_upload(self, upload_id: str) -> None:
        upload = self.uploads.pop(upload_id, None)
        if upload is None:
            return
        if upload_id in self.order:
            self.order.remove(upload_id)
        for file in (upload.path, upload.thumb):
            try:
                file.unlink(missing_ok=True)
            except OSError:
                pass

    def clear_uploads(self) -> None:
        for upload_id in list(self.uploads):
            self.remove_upload(upload_id)
        self.rows = []
        self.done = self.total = 0
        self.current = ""
        self.error = ""

    def set_side(self, upload_id: str, side: str) -> None:
        upload = self.uploads.get(upload_id)
        if upload is not None and side in ("front", "back", ""):
            upload.side = side

    # ---------------------------------------------------------------- quét
    def start_scan(self, items: list[tuple[str, str]], options: OcrOptions) -> None:
        """Chạy quét trong luồng nền; ``items`` là [(upload_id, side)]."""
        if self.running:
            raise RuntimeError("Đang quét, vui lòng đợi")
        selected = [(self.uploads[uid], side) for uid, side in items
                    if uid in self.uploads]
        if not selected:
            raise ValueError("Chưa có ảnh nào được chọn")

        self.running = True
        self.done = 0
        self.total = len(selected)
        self.rows = []
        self.error = ""
        self.current = ""
        self.started_at = time.time()
        thread = threading.Thread(target=self._scan_worker, args=(selected, options),
                                  daemon=True)
        thread.start()

    def _scan_worker(self, selected: list[tuple[Upload, str]],
                     options: OcrOptions) -> None:
        try:
            engine = OcrEngine(options)
            _ = engine.backend  # nạp backend, báo lỗi sớm
            self.engine_name = engine.describe()

            records: list[CardRecord] = []
            for upload, side in selected:
                self.current = upload.name
                record = scan_one(engine, upload.path, upload.name, forced_side=side)
                records.append(record)
                self.done += 1

            for record in group_records(records):
                self.rows.append(row_from_record(record))
        except Exception as error:
            _LOGGER.exception("Quét thất bại")
            self.error = str(error) or error.__class__.__name__
        finally:
            self.running = False
            self.current = ""

    def status(self) -> dict:
        return {
            "running": self.running,
            "done": self.done,
            "total": self.total,
            "current": self.current,
            "error": self.error,
            "engine": self.engine_name,
            "rows": self.rows,
            "elapsed": round(time.time() - self.started_at, 1) if self.started_at else 0,
        }


# ------------------------------------------------------------------ pipeline

def scan_one(engine: OcrEngine, path: Path, name: str, *,
             forced_side: str = "") -> CardRecord:
    """Quét một ảnh (giống CLI) nhưng cho phép ép mặt trước / mặt sau."""
    variant_ocrs = engine.read_variants(str(path))
    parses = [parse_card(v.items, source=name) for v in variant_ocrs if v.items]
    if not parses:
        record = CardRecord(source_files=[name],
                            warnings=["Không đọc được chữ nào trong ảnh"])
        record.side = forced_side
        return record

    record = pick_best([p.record for p in parses])
    mrz_results = [p.mrz for p in parses]
    if any(not m.is_empty() for m in mrz_results):
        mrz_results.append(engine.read_mrz_pass(str(path)))
    record = reconcile(record, best_mrz(mrz_results))

    if forced_side:
        # Người dùng đã chỉ định mặt nào -> tin người dùng hơn máy đoán
        record.side = forced_side
    return record


def row_from_record(record: CardRecord) -> dict:
    """Chuyển ``CardRecord`` thành dict để gửi xuống trình duyệt."""
    row = {name: getattr(record, name) for name in TABLE_FIELDS}
    row["confidence"] = round(record.confidence, 2)
    row["warnings"] = list(record.warnings)
    row["sources"] = list(record.source_files)
    row["side"] = record.side
    return row


def record_from_row(row: dict) -> CardRecord:
    """Dựng lại ``CardRecord`` từ dict (đã có thể được người dùng sửa tay)."""
    record = CardRecord()
    for name in TABLE_FIELDS:
        value = row.get(name, "")
        setattr(record, name, str(value).strip() if value is not None else "")
    try:
        record.confidence = float(row.get("confidence") or 0)
    except (TypeError, ValueError):
        record.confidence = 0.0
    warnings = row.get("warnings") or []
    record.warnings = [str(w) for w in warnings if str(w).strip()]
    sources = row.get("sources") or []
    record.source_files = [str(s) for s in sources if str(s).strip()]
    return record


# ------------------------------------------------------------------ helpers

def _make_thumbnail(source: Path, destination: Path) -> None:
    """Tạo ảnh thu nhỏ cho lưới kéo-thả."""
    try:
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((THUMB_MAX, THUMB_MAX))
            image.save(destination, "JPEG", quality=82)
    except Exception as error:
        _LOGGER.warning("Không tạo được thumbnail cho %s: %s", source, error)
        Image.new("RGB", (160, 100), (230, 230, 230)).save(destination, "JPEG")


def decode_data_url(data: str) -> bytes:
    """Giải mã chuỗi base64 (có thể kèm tiền tố ``data:image/...;base64,``)."""
    if "," in data[:80] and data.lstrip().startswith("data:"):
        data = data.split(",", 1)[1]
    return base64.b64decode(data, validate=False)


def image_bytes(path: Path, max_width: int = 0) -> bytes:
    """Đọc ảnh trả về JPEG (tuỳ chọn thu nhỏ) để hiển thị trong trình duyệt."""
    if max_width:
        image = load_image(str(path))
        width = image.shape[1]
        if width > max_width:
            scale = max_width / width
            image = _resize(image, scale)
        ok, buffer = _encode_jpeg(image)
        if ok:
            return buffer
    return path.read_bytes()


def _resize(image, scale: float):
    import cv2

    height, width = image.shape[:2]
    return cv2.resize(image, (int(width * scale), int(height * scale)),
                      interpolation=cv2.INTER_AREA)


def _encode_jpeg(image) -> tuple[bool, bytes]:
    import cv2

    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    return ok, buffer.tobytes() if ok else b""


def bytes_to_data_url(raw: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
