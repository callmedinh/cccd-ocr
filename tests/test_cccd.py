"""Kiểm thử không cần OCR: MRZ, chuẩn hoá text, cấu trúc số CCCD, bóc tách, Excel.

Chạy:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cccd_ocr import aggregate, idcheck, mrz, textnorm  # noqa: E402
from cccd_ocr.excel_writer import write_csv, write_excel  # noqa: E402
from cccd_ocr.models import CardRecord, TextItem  # noqa: E402
from cccd_ocr.parser import group_lines, parse_card  # noqa: E402

# ------------------------------------------------------------------ helpers

def item(text: str, x: float, y: float, w: float = 300.0, h: float = 30.0) -> TextItem:
    """Tạo một ô text OCR giả với hộp bao cho trước."""
    return TextItem(
        text=text,
        score=0.95,
        box=[(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
    )


def build_mrz_rows(id_number: str, dob: str, sex: str, expiry: str,
                   name: str) -> list[str]:
    """Dựng 3 dòng MRZ TD1 có check digit hợp lệ từ dữ liệu dạng dd/mm/yyyy."""
    day, month, year = dob.split("/")
    dob_mrz = f"{year[2:]}{month}{day}"
    day_e, month_e, year_e = expiry.split("/")
    exp_mrz = f"{year_e[2:]}{month_e}{day_e}"
    sex_char = "M" if sex == "Nam" else "F"

    row1 = f"IDVNM{id_number}".ljust(30, "<")
    body = (f"{dob_mrz}{mrz.check_digit(dob_mrz)}{sex_char}"
            f"{exp_mrz}{mrz.check_digit(exp_mrz)}VNM")
    composite = (f"{id_number}{mrz.check_digit(id_number)}"
                 f"{dob_mrz}{mrz.check_digit(dob_mrz)}"
                 f"{exp_mrz}{mrz.check_digit(exp_mrz)}")
    row2 = f"{body}{'<' * (29 - len(body))}{mrz.check_digit(composite)}"
    row3 = "<<".join(name.split()).ljust(30, "<")
    return [row1, row2, row3]


def front_items() -> list[TextItem]:
    return [
        item("Số / No.: 001095012345", 44, 200, 320),
        item("Họ và tên / Full name:", 45, 290, 300),
        item("NGUYỄN VĂN AN", 46, 322, 320),
        item("Ngày sinh / Date of birth: 15/05/1995", 47, 380, 420),
        item("Giới tính / Sex: Nam", 47, 420, 200),
        item("Quốc tịch / Nationality: Việt Nam", 500, 420, 330),
        item("Quê quán / Place of origin:", 47, 470, 300),
        item("Xã Kim Liên, Huyện Nam Đàn, Tỉnh Nghệ An", 68, 500, 500),
        item("Nơi thường trú / Place of residence: Số 12, Ngõ 45, Phố Huế, "
             "Phường Hàng Bài, Quận Hoàn Kiếm, Hà Nội", 49, 560, 900),
        item("Có giá trị đến / Date of expiry: 15/05/2035", 49, 620, 460),
    ]


# --------------------------------------------------------------------- tests

class TestTextNorm(unittest.TestCase):
    def test_strip_diacritics_handles_vietnamese_specials(self):
        self.assertEqual(textnorm.strip_diacritics("Nơi thường trú"), "Noi thuong tru")
        self.assertEqual(textnorm.strip_diacritics("Đặc điểm"), "Dac diem")

    def test_fold_compact(self):
        self.assertEqual(textnorm.fold_compact("Họ và tên / Full name"), "hovatenfullname")

    def test_fold_chars_maps_back_to_original(self):
        folded, index_map = textnorm.fold_chars("Số / No.: 001")
        self.assertEqual(folded, "sono001")
        self.assertEqual(len(folded), len(index_map))
        self.assertEqual(index_map[0], 0)          # 'S' ở vị trí 0
        self.assertEqual("Số / No.: 001"[index_map[2]], "N")

    def test_normalize_date_formats(self):
        self.assertEqual(textnorm.normalize_date("15/05/1995"), "15/05/1995")
        self.assertEqual(textnorm.normalize_date("15-5-1995"), "15/05/1995")
        self.assertEqual(textnorm.normalize_date("ngày 15.05.1995"), "15/05/1995")
        self.assertEqual(textnorm.normalize_date("001095012345"), "")

    def test_normalize_date_rejects_invalid(self):
        self.assertEqual(textnorm.normalize_date("31/02/1995"), "")
        self.assertEqual(textnorm.normalize_date("15/13/1995"), "")

    def test_date_from_mrz_century_window(self):
        self.assertEqual(textnorm.date_from_mrz("950515"), "15/05/1995")
        self.assertEqual(textnorm.date_from_mrz("350515"), "15/05/2035")
        self.assertEqual(textnorm.date_from_mrz("000229"), "29/02/2000")

    def test_normalize_sex(self):
        self.assertEqual(textnorm.normalize_sex("Nam"), "Nam")
        self.assertEqual(textnorm.normalize_sex("Nữ"), "Nữ")
        self.assertEqual(textnorm.normalize_sex("NU"), "Nữ")
        self.assertEqual(textnorm.normalize_sex("N0"), "Nữ")
        self.assertEqual(textnorm.normalize_sex("?"), "")

    def test_clean_value_drops_punctuation_only(self):
        self.assertEqual(textnorm.clean_value("/"), "")
        self.assertEqual(textnorm.clean_value(": -"), "")
        # Chỉ còn lại phần song ngữ của nhãn -> không phải giá trị
        self.assertEqual(textnorm.clean_value("/ Full name:"), "")
        self.assertEqual(textnorm.clean_value("  Nguyễn   Văn  An "), "Nguyễn Văn An")


class TestMrz(unittest.TestCase):
    def test_check_digit_known_values(self):
        # Ví dụ chuẩn ICAO 9303: "L898902C<" -> 3
        self.assertEqual(mrz.check_digit("L898902C<"), 3)
        self.assertEqual(mrz.check_digit("740812"), 2)
        self.assertEqual(mrz.check_digit("120415"), 9)

    def test_char_value(self):
        self.assertEqual(mrz.char_value("0"), 0)
        self.assertEqual(mrz.char_value("A"), 10)
        self.assertEqual(mrz.char_value("Z"), 35)
        self.assertEqual(mrz.char_value("<"), 0)

    def test_decode_synthetic_card(self):
        rows = build_mrz_rows("001095012345", "15/05/1995", "Nam",
                              "15/05/2035", "NGUYEN VAN AN")
        result = mrz.decode_mrz([mrz.normalize_mrz_line(row) for row in rows])
        self.assertEqual(result.id_number, "001095012345")
        self.assertEqual(result.date_of_birth, "15/05/1995")
        self.assertEqual(result.date_of_expiry, "15/05/2035")
        self.assertEqual(result.sex, "Nam")
        self.assertEqual(result.nationality_code, "VNM")
        self.assertEqual(result.full_name, "NGUYEN VAN AN")
        self.assertTrue(result.valid)

    def test_decode_repairs_common_ocr_confusions(self):
        # "0" bị đọc thành "Q", "I" bị đọc thành "1" — lỗi rất hay gặp
        rows = build_mrz_rows("001095012345", "15/05/1995", "Nam",
                              "15/05/2035", "NGUYEN VAN AN")
        noisy = [rows[0].replace("00", "QQ", 1).replace("IDVNM", "1DVNM", 1), *rows[1:]]
        result = mrz.decode_mrz([mrz.normalize_mrz_line(row) for row in noisy])
        self.assertEqual(result.id_number, "001095012345")

    def test_is_mrz_like(self):
        self.assertTrue(mrz.is_mrz_like("IDVNM001095012345<<<<<<<<<<<<<<"))
        self.assertTrue(mrz.is_mrz_like("NGUYEN<<VAN<<AN<<<<<<<<<<<<<<"))
        self.assertFalse(mrz.is_mrz_like("CỤC TRƯỞNG CỤC CẢNH SÁT QUẢN LÝ"))
        self.assertFalse(mrz.is_mrz_like("Số 12, Ngõ 45"))

    def test_extract_mrz_picks_bottom_lines_only(self):
        rows = build_mrz_rows("079600123456", "20/11/2000", "Nữ",
                              "20/11/2040", "TRAN THI BICH NGOC")
        items = [
            item("Đặc điểm nhận dạng / Personal identification:", 70, 80),
            item("Ngày, tháng, năm cấp / Date of issue: 05/03/2022", 70, 200),
            *[item(row, 90, 700 + 46 * index, 700) for index, row in enumerate(rows)],
        ]
        result = mrz.extract_mrz(items)
        self.assertEqual(result.id_number, "079600123456")
        self.assertEqual(result.date_of_birth, "20/11/2000")
        self.assertEqual(result.sex, "Nữ")

    def test_extract_mrz_returns_empty_for_front(self):
        self.assertTrue(mrz.extract_mrz(front_items()).is_empty())


class TestIdCheck(unittest.TestCase):
    def test_valid_shape(self):
        self.assertTrue(idcheck.is_valid_shape("001095012345"))
        self.assertFalse(idcheck.is_valid_shape("00109501234"))
        self.assertFalse(idcheck.is_valid_shape("0010950123456"))

    def test_parse_structure(self):
        structure = idcheck.parse_structure("001095012345")
        assert structure is not None
        self.assertEqual(structure["province_code"], "001")
        self.assertEqual(structure["century"], 20)
        self.assertEqual(structure["sex"], "Nam")
        self.assertEqual(structure["birth_year_suffix"], "95")

    def test_cross_check_consistent(self):
        self.assertEqual(idcheck.cross_check("001095012345", "15/05/1995", "Nam"), [])
        self.assertEqual(idcheck.cross_check("079600123456", "20/11/2000", "Nữ"), [])

    def test_cross_check_detects_wrong_birth_year(self):
        warnings = idcheck.cross_check("001095012345", "15/05/1999", "Nam")
        self.assertTrue(any("năm sinh" in w for w in warnings))

    def test_cross_check_detects_wrong_sex(self):
        warnings = idcheck.cross_check("001095012345", "15/05/1995", "Nữ")
        self.assertTrue(any("giới tính" in w for w in warnings))

    def test_cross_check_detects_old_cmnd(self):
        warnings = idcheck.cross_check("123456789", "", "")
        self.assertTrue(any("9 chữ số" in w for w in warnings))


class TestGroupLines(unittest.TestCase):
    def test_merges_same_row_and_sorts_by_y(self):
        items = [
            item("B", 300, 100),
            item("A", 50, 100),
            item("C", 50, 200),
        ]
        lines = group_lines(items)
        self.assertEqual([line.text for line in lines], ["A B", "C"])


class TestParserFront(unittest.TestCase):
    def setUp(self):
        self.result = parse_card(front_items(), source="front.jpg")

    def test_all_fields(self):
        record = self.result.record
        self.assertEqual(record.id_number, "001095012345")
        self.assertEqual(record.full_name, "NGUYỄN VĂN AN")
        self.assertEqual(record.date_of_birth, "15/05/1995")
        self.assertEqual(record.sex, "Nam")
        self.assertEqual(record.nationality, "Việt Nam")
        self.assertEqual(record.place_of_origin,
                         "Xã Kim Liên, Huyện Nam Đàn, Tỉnh Nghệ An")
        self.assertEqual(record.place_of_residence,
                         "Số 12, Ngõ 45, Phố Huế, Phường Hàng Bài, Quận Hoàn Kiếm, Hà Nội")
        self.assertEqual(record.date_of_expiry, "15/05/2035")

    def test_two_fields_on_one_line_do_not_bleed(self):
        # "Giới tính: Nam" và "Quốc tịch: Việt Nam" in chung một dòng
        self.assertEqual(self.result.record.sex, "Nam")
        self.assertEqual(self.result.record.nationality, "Việt Nam")

    def test_side_detected_as_front(self):
        self.assertEqual(self.result.record.side, "front")

    def test_no_warning_on_consistent_card(self):
        self.assertEqual(self.result.record.warnings, [])

    def test_address_starting_with_so_is_not_truncated(self):
        self.assertTrue(self.result.record.place_of_residence.startswith("Số 12"))

    def test_english_label_never_leaks_into_fields(self):
        """Nhãn song ngữ đọc sai một ký tự vẫn không được lọt vào giá trị."""
        items = [
            item("Số / No.: 001095012345", 44, 200),
            item("Họ và tên / Full name:", 45, 290),
            item("Nguyễn Văn An", 46, 322),
            item("Ngày sinh / Date of birth: 15/05/1995", 47, 380),
            item("Full nam6", 45, 446),   # ảnh mờ: "Full name" đọc thành "Full nam6"
        ]
        record = parse_card(items).record
        self.assertEqual(record.full_name, "Nguyễn Văn An")

    def test_name_field_rejects_line_with_digits(self):
        items = [
            item("Số / No.: 001095012345", 44, 200),
            item("Họ và tên / Full name:", 45, 290),
            item("15/05/1995 ẢNH", 46, 322),   # dòng rác, không thể là họ tên
        ]
        record = parse_card(items).record
        self.assertEqual(record.full_name, "")


class TestParserBack(unittest.TestCase):
    def setUp(self):
        rows = build_mrz_rows("001095012345", "15/05/1995", "Nam",
                              "15/05/2035", "NGUYEN VAN AN")
        items = [
            item("Đặc điểm nhận dạng / Personal identification:", 70, 80, 560),
            item("Sẹo mờ 1cm trên trán", 90, 110, 300),
            item("Ngày, tháng, năm cấp / Date of issue: 20/06/2021", 70, 200, 560),
            item("CỤC TRƯỞNG CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI",
                 70, 260, 800),
            *[item(row, 90, 700 + 46 * index, 700) for index, row in enumerate(rows)],
        ]
        self.result = parse_card(items, source="back.jpg")

    def test_back_fields(self):
        record = self.result.record
        self.assertEqual(record.date_of_issue, "20/06/2021")
        self.assertEqual(record.personal_identification, "Sẹo mờ 1cm trên trán")
        self.assertEqual(record.issue_authority,
                         "CỤC TRƯỞNG CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI")

    def test_mrz_supplies_missing_core_fields(self):
        record = self.result.record
        self.assertEqual(record.id_number, "001095012345")
        self.assertEqual(record.date_of_birth, "15/05/1995")
        self.assertEqual(record.sex, "Nam")
        self.assertEqual(record.date_of_expiry, "15/05/2035")

    def test_side_detected_as_back(self):
        self.assertEqual(self.result.record.side, "back")


class TestMergeFrontBack(unittest.TestCase):
    def test_front_wins_for_name_back_for_issue_date(self):
        front = parse_card(front_items(), source="front.jpg").record
        rows = build_mrz_rows("001095012345", "15/05/1995", "Nam",
                              "15/05/2035", "NGUYEN VAN AN")
        back_items = [
            item("Đặc điểm nhận dạng / Personal identification: Sẹo mờ", 70, 80, 600),
            item("Ngày, tháng, năm cấp / Date of issue: 20/06/2021", 70, 200, 560),
            *[item(row, 90, 700 + 46 * i, 700) for i, row in enumerate(rows)],
        ]
        back = parse_card(back_items, source="back.jpg").record

        # Gộp: nạp mặt sau trước để kiểm tra bên ưu tiên có đúng không
        merged = back
        merged.merge_from(front)

        self.assertEqual(merged.full_name, "NGUYỄN VĂN AN")      # mặt trước thắng
        self.assertEqual(merged.date_of_issue, "20/06/2021")     # mặt sau giữ
        self.assertEqual(merged.place_of_residence,
                         "Số 12, Ngõ 45, Phố Huế, Phường Hàng Bài, Quận Hoàn Kiếm, Hà Nội")
        self.assertIn("front.jpg", merged.source_files)
        self.assertIn("back.jpg", merged.source_files)


class TestAggregate(unittest.TestCase):
    def test_best_mrz_prefers_self_consistent(self):
        good = mrz.MrzResult(id_number="001095012345", date_of_birth="15/05/1995",
                             sex="Nam", date_of_expiry="15/05/2035")
        bad = mrz.MrzResult(id_number="480988765432", date_of_birth="",
                            sex="", date_of_expiry="15/05/2035")
        self.assertEqual(aggregate.best_mrz([bad, good]).id_number, "001095012345")

    def test_reconcile_prefers_structurally_correct_id(self):
        record = CardRecord(id_number="480988765432")
        mrz_result = mrz.MrzResult(id_number="048088765432", date_of_birth="02/01/1988",
                                   sex="Nam", date_of_expiry="02/01/2028")
        merged = aggregate.reconcile(record, mrz_result)
        self.assertEqual(merged.id_number, "048088765432")

    def test_pick_best_fills_missing_fields(self):
        weak = CardRecord(id_number="001095012345")
        strong = CardRecord(id_number="001095012345", full_name="NGUYỄN VĂN AN",
                            date_of_birth="15/05/1995", sex="Nam",
                            place_of_residence="Hà Nội")
        best = aggregate.pick_best([weak, strong])
        self.assertEqual(best.full_name, "NGUYỄN VĂN AN")
        self.assertEqual(best.place_of_residence, "Hà Nội")

    def test_pick_best_prefers_longer_text_that_contains_shorter(self):
        # Một biến thể ảnh rơi mất từ đầu của họ tên (confidence thấp)
        partial = CardRecord(id_number="001095012345", full_name="VĂN AN",
                             date_of_birth="15/05/1995", sex="Nam")
        full = CardRecord(id_number="001095012345", full_name="NGUYỄN VĂN AN",
                          date_of_birth="15/05/1995", sex="Nam")
        self.assertEqual(aggregate.pick_best([partial, full]).full_name, "NGUYỄN VĂN AN")
        self.assertEqual(aggregate.pick_best([full, partial]).full_name, "NGUYỄN VĂN AN")


class TestPlausibility(unittest.TestCase):
    def test_mrz_rejects_impossible_expiry_year(self):
        # Dòng 2 đọc sai: hạn dùng rơi vào 1946 -> phải bỏ trống, không ghi rác
        result = mrz.decode_mrz(["IDVNM001095012345<<<<<<<<<<<<<<",
                                 "9505151M4601124VNM<<<<<<<<<<<<",
                                 "NGUYEN<<VAN<<AN<<<<<<<<<<<<<<"])
        self.assertEqual(result.date_of_expiry, "")
        self.assertEqual(result.date_of_birth, "15/05/1995")


class TestExcelWriter(unittest.TestCase):
    def test_write_excel_and_csv(self):
        record = CardRecord(id_number="001095012345", full_name="NGUYỄN VĂN AN",
                            date_of_birth="15/05/1995", sex="Nam",
                            nationality="Việt Nam", warnings=["thử nghiệm"])
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = Path(tmp) / "out.xlsx"
            csv_path = Path(tmp) / "out.csv"
            write_excel([record], xlsx)
            write_csv([record], csv_path)

            self.assertTrue(xlsx.exists())
            from openpyxl import load_workbook

            sheet = load_workbook(xlsx).active
            self.assertEqual(sheet.cell(row=1, column=2).value, "Số CCCD")
            self.assertEqual(sheet.cell(row=2, column=2).value, "001095012345")
            self.assertEqual(sheet.cell(row=2, column=3).value, "NGUYỄN VĂN AN")
            self.assertEqual(sheet.freeze_panes, "B2")

            text = csv_path.read_text(encoding="utf-8-sig")
            self.assertIn("001095012345", text)
            self.assertIn("NGUYỄN VĂN AN", text)

    def test_excel_append(self):
        first = CardRecord(id_number="001095012345", full_name="A")
        second = CardRecord(id_number="079600123456", full_name="B")
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = Path(tmp) / "out.xlsx"
            write_excel([first], xlsx)
            write_excel([second], xlsx, append=True)
            from openpyxl import load_workbook

            sheet = load_workbook(xlsx).active
            self.assertEqual(sheet.max_row, 3)          # 1 tiêu đề + 2 dòng
            self.assertEqual(sheet.cell(row=3, column=2).value, "079600123456")


if __name__ == "__main__":
    unittest.main(verbosity=2)
