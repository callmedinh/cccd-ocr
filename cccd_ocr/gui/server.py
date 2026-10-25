"""Máy chủ HTTP cục bộ cho giao diện kéo-thả.

Chỉ dùng thư viện chuẩn (``http.server``) — không cần Flask/FastAPI. Máy chủ
chỉ lắng nghe trên 127.0.0.1 nên ảnh thẻ công dân không rời khỏi máy.

Chạy:  python gui.py
"""

from __future__ import annotations

import json
import logging
import mimetypes
import shutil
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..console import ensure_utf8_console
from ..excel_writer import csv_bytes, workbook_bytes
from ..ocr_engine import OcrOptions
from .session import (
    Session,
    decode_data_url,
    image_bytes,
    record_from_row,
)

_LOGGER = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Chặn tải quá nhiều ảnh một lúc: mỗi ảnh giữ 1 luồng OCR, 8 là hợp lý cho máy cá nhân
MAX_UPLOAD_BYTES = 40 * 1024 * 1024


class GuiServer(ThreadingHTTPServer):
    """Máy chủ giữ một ``Session`` dùng chung."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], work_dir: Path) -> None:
        super().__init__(address, _Handler)
        self.session = Session(work_dir=work_dir)


class _Handler(BaseHTTPRequestHandler):
    server_version = "CCCDOCR/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- tiện ích
    @property
    def session(self) -> Session:
        return self.server.session  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        _LOGGER.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _fail(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("Tệp quá lớn (giới hạn 40 MB mỗi ảnh)")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Dữ liệu gửi lên không hợp lệ") from error

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                return self.serve_static("index.html")
            if path.startswith("/static/"):
                return self.serve_static(path[len("/static/"):])
            if path == "/api/status":
                return self._send_json({"ok": True, **self.session.status()})
            if path == "/api/state":
                # Khôi phục phiên làm việc khi người dùng refresh trang
                uploads = [
                    {"id": uid, "name": self.session.uploads[uid].name,
                     "side": self.session.uploads[uid].side}
                    for uid in self.session.order
                ]
                return self._send_json({"ok": True, "uploads": uploads,
                                        "rows": self.session.rows})
            if path.startswith("/api/thumb/"):
                return self.serve_upload(path.rsplit("/", 1)[-1], thumb=True)
            if path.startswith("/api/image/"):
                return self.serve_upload(path.rsplit("/", 1)[-1], thumb=False)
            return self._fail("Không có đường dẫn này", 404)
        except Exception as error:
            _LOGGER.exception("GET %s lỗi", path)
            return self._fail(str(error), 500)

    def serve_static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            return self._fail("Không tìm thấy tệp", 404)
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
                "application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)

    def serve_upload(self, upload_id: str, *, thumb: bool) -> None:
        upload = self.session.uploads.get(upload_id)
        if upload is None:
            return self._fail("Ảnh không còn trong phiên làm việc", 404)
        if thumb:
            return self._send(200, upload.thumb.read_bytes(), "image/jpeg")
        self._send(200, image_bytes(upload.path, max_width=1400), "image/jpeg")

    # ----------------------------------------------------------------- POST
    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/upload":
                return self.api_upload()
            if path == "/api/side":
                return self.api_side()
            if path == "/api/remove":
                return self.api_remove()
            if path == "/api/reset":
                self.session.clear_uploads()
                return self._send_json({"ok": True})
            if path == "/api/scan":
                return self.api_scan()
            if path == "/api/export":
                return self.api_export()
            return self._fail("Không có đường dẫn này", 404)
        except ValueError as error:
            self._fail(str(error))
        except Exception as error:
            _LOGGER.exception("POST %s lỗi", path)
            self._fail(str(error) or error.__class__.__name__, 500)

    def api_upload(self) -> None:
        payload = self.read_json()
        name = str(payload.get("name") or "image.jpg")
        data = str(payload.get("data") or "")
        side = str(payload.get("side") or "")
        raw = decode_data_url(data)
        upload = self.session.add_upload(name, raw, side=side)
        self._send_json({"ok": True, "id": upload.id, "name": upload.name,
                         "side": upload.side})

    def api_side(self) -> None:
        payload = self.read_json()
        self.session.set_side(str(payload.get("id") or ""),
                             str(payload.get("side") or ""))
        self._send_json({"ok": True})

    def api_remove(self) -> None:
        payload = self.read_json()
        self.session.remove_upload(str(payload.get("id") or ""))
        self._send_json({"ok": True})

    def api_scan(self) -> None:
        payload = self.read_json()
        items = [(str(item.get("id") or ""), str(item.get("side") or ""))
                 for item in payload.get("items") or []]
        options = OcrOptions(
            backend=str(payload.get("backend") or "auto"),
            lang=str(payload.get("lang") or "vie"),
            thorough=bool(payload.get("thorough")),
            variants=not bool(payload.get("fast")),
        )
        self.session.start_scan(items, options, merge=bool(payload.get("merge", True)))
        self._send_json({"ok": True, "total": self.session.total})

    def api_export(self) -> None:
        payload = self.read_json()
        rows = payload.get("rows") or []
        fmt = str(payload.get("fmt") or "xlsx").lower()
        records = [record_from_row(row) for row in rows if isinstance(row, dict)]
        if not records:
            raise ValueError("Chưa có dòng nào để xuất")

        if fmt == "csv":
            body = csv_bytes(records)
            self._send(200, body, "text/csv; charset=utf-8", {
                "Content-Disposition": 'attachment; filename="cccd.csv"',
            })
            return
        body = workbook_bytes(records)
        self._send(200, body,
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", {
                       "Content-Disposition": 'attachment; filename="cccd.xlsx"',
                   })


# ------------------------------------------------------------------ khởi động

def serve(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = True,
          work_dir: Path | None = None) -> None:
    """Chạy máy chủ cho tới khi người dùng dừng (Ctrl+C)."""
    ensure_utf8_console()
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(message)s")

    temp_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="cccd-ocr-"))
    temp_dir.mkdir(parents=True, exist_ok=True)

    server = GuiServer((host, port), temp_dir)
    url = f"http://{host}:{server.server_port}/"
    print("=" * 62)
    print("  CCCD OCR - giao diện kéo thả")
    print(f"  Mở trình duyệt tại: {url}")
    print("  Ảnh chỉ được xử lý trên máy này, không gửi lên internet.")
    print("  Nhấn Ctrl+C để dừng.")
    print("=" * 62)

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang dừng máy chủ...")
    finally:
        server.shutdown()
        server.server_close()
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except OSError:
            pass
        print("Đã dừng.")
