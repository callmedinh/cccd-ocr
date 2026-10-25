"""Kiểm thử tích hợp cho máy chủ GUI (HTTP thật, cổng ngẫu nhiên).

Chạy:  python -m unittest discover -s tests -v
Phần quét ảnh sẽ tự bỏ qua nếu máy chưa cài Tesseract.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cccd_ocr.cli import group_records  # noqa: E402
from cccd_ocr.gui.server import GuiServer  # noqa: E402
from cccd_ocr.gui.session import record_from_row, row_from_record  # noqa: E402
from cccd_ocr.models import CardRecord  # noqa: E402

SAMPLES = ROOT / "samples"
FRONT = SAMPLES / "01_001095012345_mat_truoc.jpg"
BACK = SAMPLES / "01_001095012345_mat_sau.jpg"


def _tesseract_available() -> bool:
    try:
        from cccd_ocr.backends.tesseract_backend import TesseractEngine

        TesseractEngine().version()
        return True
    except Exception:
        return False


class _ServerFixture(unittest.TestCase):
    """Khởi động máy chủ GUI trên một cổng trống của hệ điều hành."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.work_dir = tempfile.TemporaryDirectory()
        cls.server = GuiServer(("127.0.0.1", 0), Path(cls.work_dir.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.work_dir.cleanup()

    # ------------------------------------------------------------- tiện ích
    def get(self, path: str) -> tuple[int, bytes, str]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=30) as response:
                return response.status, response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            with error:  # đóng response để không rò rỉ tài nguyên
                return error.code, error.read(), error.headers.get("Content-Type", "")

    def post_json(self, path: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read() or b"{}")

    def upload(self, path: Path, side: str) -> str:
        raw = path.read_bytes()
        status, body = self.post_json("/api/upload", {
            "name": path.name,
            "data": base64.b64encode(raw).decode(),
            "side": side,
        })
        self.assertEqual(status, 200, body)
        return body["id"]


class TestStaticFiles(_ServerFixture):
    def test_index_is_served(self):
        status, body, content_type = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"CCCD", body)

    def test_assets_are_served(self):
        for name, expected in (("app.js", "text/javascript"),
                               ("style.css", "text/css")):
            status, body, content_type = self.get(f"/static/{name}")
            self.assertEqual(status, 200, name)
            self.assertIn(expected.split("/")[0], content_type)
            self.assertGreater(len(body), 500)

    def test_path_traversal_is_rejected(self):
        status, _body, _ct = self.get("/static/../../main.py")
        self.assertIn(status, (400, 404))


class TestUploadAndSession(_ServerFixture):
    def test_upload_thumb_and_remove(self):
        upload_id = self.upload(FRONT, "front")
        status, body, content_type = self.get(f"/api/thumb/{upload_id}")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "image/jpeg")
        self.assertTrue(body.startswith(b"\xff\xd8"), "phải là JPEG")

        status, body = self.post_json("/api/remove", {"id": upload_id})
        self.assertEqual(status, 200)
        status, _body, _ct = self.get(f"/api/thumb/{upload_id}")
        self.assertEqual(status, 404)

    def test_rejects_unknown_extension(self):
        status, body = self.post_json("/api/upload", {
            "name": "tailieu.pdf",
            "data": base64.b64encode(b"%PDF-1.4").decode(),
        })
        self.assertEqual(status, 400)
        self.assertIn("không hỗ trợ", body["error"])

    def test_side_can_be_changed(self):
        upload_id = self.upload(FRONT, "" )
        status, _body = self.post_json("/api/side", {"id": upload_id, "side": "back"})
        self.assertEqual(status, 200)
        self.assertEqual(self.server.session.uploads[upload_id].side, "back")

    def test_reset_clears_everything(self):
        self.upload(FRONT, "front")
        status, _body = self.post_json("/api/reset", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.server.session.uploads, {})

    def test_status_shape(self):
        status, body, _ct = self.get("/api/status")
        self.assertEqual(status, 200)
        self.assertIn("running", body_json_of(body))
        for key in ("done", "total", "rows", "error"):
            self.assertIn(key, body_json_of(body))


def body_json_of(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


class TestScanFlow(_ServerFixture):
    @unittest.skipUnless(_tesseract_available(), "chưa cài Tesseract")
    @unittest.skipUnless(FRONT.exists() and BACK.exists(), "chưa có ảnh mẫu")
    def test_scan_pairs_front_and_back(self):
        front_id = self.upload(FRONT, "front")
        back_id = self.upload(BACK, "back")

        status, body = self.post_json("/api/scan", {
            "items": [{"id": front_id, "side": "front"}, {"id": back_id, "side": "back"}],
            "fast": True,
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(body["total"], 2)

        rows = self._wait_for_rows()
        self.assertEqual(len(rows), 1, "mặt trước và mặt sau phải gộp thành 1 dòng")
        row = rows[0]
        self.assertEqual(row["id_number"], "001095012345")
        self.assertEqual(row["date_of_birth"], "15/05/1995")
        self.assertEqual(row["sex"], "Nam")
        self.assertTrue(row["date_of_issue"], "ngày cấp phải lấy từ mặt sau")
        self.assertGreaterEqual(len(row["sources"]), 2)

        # Xuất Excel từ chính dữ liệu vừa quét
        request = urllib.request.Request(
            self.base + "/api/export",
            data=json.dumps({"fmt": "xlsx", "rows": rows}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
        self.assertGreater(len(payload), 4000)
        self.assertTrue(payload.startswith(b"PK"), "xlsx là tệp zip")

    def _wait_for_rows(self) -> list[dict]:
        deadline = time.time() + 180
        while time.time() < deadline:
            _status, body, _ct = self.get("/api/status")
            state = body_json_of(body)
            if not state["running"]:
                self.assertEqual(state["error"], "", "quét không được lỗi")
                return state["rows"]
            time.sleep(0.5)
        self.fail("quét quá lâu, không có kết quả")


class TestRowConversion(unittest.TestCase):
    def test_round_trip_keeps_fields(self):
        record = CardRecord(id_number="001095012345", full_name="Nguyễn Văn An",
                            date_of_birth="15/05/1995", sex="Nam",
                            confidence=0.93, warnings=["thử"], source_files=["a.jpg"])
        row = row_from_record(record)
        self.assertEqual(row["full_name"], "Nguyễn Văn An")
        self.assertEqual(row["confidence"], 0.93)

        # Người dùng sửa tay trên bảng
        row["full_name"] = "NGUYỄN VĂN AN"
        rebuilt = record_from_row(row)
        self.assertEqual(rebuilt.full_name, "NGUYỄN VĂN AN")
        self.assertEqual(rebuilt.id_number, "001095012345")
        self.assertEqual(rebuilt.warnings, ["thử"])
        self.assertEqual(rebuilt.source_files, ["a.jpg"])

    def test_missing_values_become_empty_strings(self):
        rebuilt = record_from_row({"id_number": None, "confidence": "abc"})
        self.assertEqual(rebuilt.id_number, "")
        self.assertEqual(rebuilt.confidence, 0.0)


class TestPairByOrder(unittest.TestCase):
    def test_unidentified_front_and_back_pair_in_order(self):
        front1 = CardRecord(place_of_residence="Địa chỉ 1")
        front1.side = "front"
        front2 = CardRecord(place_of_residence="Địa chỉ 2")
        front2.side = "front"
        back1 = CardRecord(date_of_issue="01/01/2022")
        back1.side = "back"
        back2 = CardRecord(date_of_issue="02/02/2023")
        back2.side = "back"

        merged = group_records([front1, front2, back1, back2])
        self.assertEqual(len(merged), 2)
        first = next(r for r in merged if r.place_of_residence == "Địa chỉ 1")
        self.assertEqual(first.date_of_issue, "01/01/2022")
        second = next(r for r in merged if r.place_of_residence == "Địa chỉ 2")
        self.assertEqual(second.date_of_issue, "02/02/2023")

    def test_extra_front_stays_alone(self):
        front = CardRecord(place_of_residence="Chỉ có mặt trước")
        front.side = "front"
        merged = group_records([front])
        self.assertEqual(len(merged), 1)

    def test_identified_front_absorbs_unidentified_back(self):
        """Mặt trước đọc được số CCCD, mặt sau mờ không đọc được -> vẫn phải ghép."""
        front = CardRecord(id_number="001095012345", full_name="NGUYỄN VĂN AN")
        front.side = "front"
        back = CardRecord(date_of_issue="20/06/2021")
        back.side = "back"
        back.personal_identification = "Sẹo mờ"

        merged = group_records([front, back])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].id_number, "001095012345")
        self.assertEqual(merged[0].date_of_issue, "20/06/2021")

    def test_ambiguous_loose_back_is_not_guessed(self):
        """3 mặt trước có số + 1 mặt sau mờ: không đoán bừa, để riêng."""
        fronts = []
        for index in range(3):
            record = CardRecord(id_number=f"00109501234{index}", full_name=f"NGƯỜI {index}")
            record.side = "front"
            fronts.append(record)
        back = CardRecord(personal_identification="Sẹo mờ")
        back.side = "back"

        merged = group_records([*fronts, back])
        self.assertEqual(len(merged), 4)
        self.assertTrue(all(not record.date_of_issue for record in merged))

    def test_merge_disabled_keeps_every_image(self):
        front = CardRecord(id_number="001095012345", full_name="A")
        front.side = "front"
        back = CardRecord(id_number="001095012345", date_of_issue="20/06/2021")
        back.side = "back"
        merged = group_records([front, back], merge=False)
        self.assertEqual(len(merged), 2)

    def test_records_with_same_id_merge_even_without_sides(self):
        a = CardRecord(id_number="001095012345", full_name="A")
        b = CardRecord(id_number="001095012345", date_of_issue="01/01/2022")
        merged = group_records([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].full_name, "A")
        self.assertEqual(merged[0].date_of_issue, "01/01/2022")


if __name__ == "__main__":
    unittest.main(verbosity=2)
