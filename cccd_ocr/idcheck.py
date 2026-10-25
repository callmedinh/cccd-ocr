"""Kiểm tra cấu trúc số CCCD 12 chữ số.

Cấu trúc (theo quy định về số định danh cá nhân):

======  ============================================================
Vị trí  Ý nghĩa
======  ============================================================
1-3     Mã tỉnh/thành phố nơi đăng ký khai sinh
4       Mã thế kỷ sinh + giới tính
          0,1 = nam sinh thế kỷ 20     2,3 = nữ sinh thế kỷ 20
          4,5 = nam sinh thế kỷ 21     6,7 = nữ sinh thế kỷ 21
          8,9 = các thế kỷ sau
5-6     Hai chữ số cuối của năm sinh
7-12    Số ngẫu nhiên, không mang thông tin
======  ============================================================

Nhờ đó ta **đối chiếu chéo** được số CCCD với ngày sinh và giới tính đọc
được trên thẻ — đây là cách phát hiện lỗi OCR mạnh nhất mà không cần mạng.
"""

from __future__ import annotations

import re

# (thế kỷ, giới tính) theo mã ở vị trí thứ 4
_CODE_TABLE: dict[int, tuple[int, str]] = {
    0: (20, "Nam"), 1: (20, "Nam"),
    2: (20, "Nữ"), 3: (20, "Nữ"),
    4: (21, "Nam"), 5: (21, "Nam"),
    6: (21, "Nữ"), 7: (21, "Nữ"),
    8: (22, "Nam"), 9: (22, "Nữ"),
}


def is_valid_shape(id_number: str) -> bool:
    return bool(re.fullmatch(r"\d{12}", id_number or ""))


def parse_structure(id_number: str) -> dict[str, object] | None:
    """Tách số CCCD thành các thành phần có nghĩa; None nếu sai định dạng."""
    if not is_valid_shape(id_number):
        return None
    code = int(id_number[3])
    century, sex = _CODE_TABLE[code]
    return {
        "province_code": id_number[:3],
        "century_gender_code": code,
        "century": century,
        "sex": sex,
        "birth_year_suffix": id_number[4:6],
        "sequence": id_number[6:],
    }


def cross_check(id_number: str, date_of_birth: str, sex: str) -> list[str]:
    """Đối chiếu số CCCD với ngày sinh / giới tính. Trả về danh sách cảnh báo."""
    warnings: list[str] = []
    if not id_number:
        return warnings
    if not is_valid_shape(id_number):
        if re.fullmatch(r"\d{9}", id_number or ""):
            warnings.append("Số CCCD chỉ có 9 chữ số — có thể là CMND cũ, kiểm tra lại")
        else:
            warnings.append(f"Số CCCD '{id_number}' không đúng 12 chữ số")
        return warnings

    structure = parse_structure(id_number)
    assert structure is not None

    if date_of_birth:
        parts = date_of_birth.split("/")
        if len(parts) == 3 and parts[2].isdigit():
            year = int(parts[2])
            if structure["birth_year_suffix"] != f"{year % 100:02d}":
                warnings.append(
                    f"Số CCCD ứng với năm sinh ...{structure['birth_year_suffix']}"
                    f" nhưng ngày sinh đọc được là {date_of_birth}"
                )
            if structure["century"] != year // 100 + 1:
                warnings.append(
                    f"Mã thế kỷ trong số CCCD ({structure['century']}) "
                    f"không khớp năm sinh {year}"
                )

    if sex and structure["sex"] != sex:
        warnings.append(
            f"Số CCCD ứng với giới tính {structure['sex']} "
            f"nhưng thẻ ghi {sex}"
        )
    return warnings
