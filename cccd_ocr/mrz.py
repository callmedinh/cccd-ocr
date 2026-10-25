"""Giải mã MRZ (3 dòng x 30 ký tự, chuẩn TD1) ở mặt sau CCCD.

CCCD gắn chip có MRZ dạng::

    IDVNM001095012345<<<<<<<<<<<<<<
    9505158M3505150VNM<<<<<<<<<<<6
    NGUYEN<VAN<AN<<<<<<<<<<<<<<<<

Parser ở đây cố tình **dễ tính** (lenient): số CCCD 12 chữ số của Việt Nam
không vừa đúng ô 9 ký tự của TD1, nên vị trí các ô có thể lệch giữa các lô
thẻ. Vì vậy ta bám vào cấu trúc ngữ nghĩa thay vì offset tuyệt đối:

* số CCCD  -> chuỗi 12 chữ số đầu tiên
* ``YYMMDD`` + check + giới tính + ``YYMMDD`` + check + quốc tịch
* dòng 3   -> họ tên, ``<<`` là dấu cách, ``<`` là padding

Check digit ICAO vẫn được tính để làm tín hiệu tin cậy, không dùng để loại bỏ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_WEIGHTS = (7, 3, 1)


def char_value(ch: str) -> int:
    """Giá trị ICAO 9303: '0'-'9' -> 0-9, 'A'-'Z' -> 10-35, '<' -> 0."""
    if ch.isdigit():
        return int(ch)
    if "A" <= ch <= "Z":
        return ord(ch) - ord("A") + 10
    return 0


def check_digit(data: str) -> int:
    """Check digit theo trọng số 7-3-1 của ICAO 9303."""
    total = 0
    for index, ch in enumerate(data):
        total += char_value(ch) * _WEIGHTS[index % 3]
    return total % 10


def verify(data: str, expected: str) -> bool:
    """So khớp check digit; ``expected`` không phải số thì coi như không xác nhận."""
    if not expected or not expected.isdigit():
        return False
    return check_digit(data) == int(expected)


@dataclass
class MrzResult:
    """Kết quả đọc MRZ (giá trị rỗng nếu không tìm thấy)."""

    id_number: str = ""
    date_of_birth: str = ""   # dd/mm/yyyy
    sex: str = ""
    date_of_expiry: str = ""  # dd/mm/yyyy
    nationality_code: str = ""
    full_name: str = ""
    valid: bool = False
    raw_lines: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.id_number or self.full_name or self.date_of_birth)


# ------------------------------------------------------------------ chuẩn hoá

_MRZ_ALLOWED = re.compile(r"[^A-Z0-9<]")


def normalize_mrz_line(line: str) -> str:
    """Đưa dòng MRZ về bảng chữ cho phép: chỉ A-Z, 0-9, '<'."""
    line = line.upper().replace(" ", "").replace("«", "<").replace("‹", "<")
    line = line.replace("|", "I")
    return _MRZ_ALLOWED.sub("", line)


def _fix_mrz_digits(segment: str) -> str:
    """Ô toàn số trong MRZ hay bị OCR nhầm Q/O/D→0, I/L→1, S→5, B→8, Z→2."""
    table = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                           "S": "5", "B": "8", "Z": "2", "G": "6", "T": "7",
                           "A": "4", "E": "8", "R": "8"})
    return segment.translate(table)


def is_mrz_like(line: str) -> bool:
    """Đoán một dòng text có phải MRZ không.

    Ngưỡng độ dài đặt ở 14 vì dòng họ tên ngắn (``NGUYEN<<VAN<<AN<``) chỉ có
    ~17 ký tự, không thể đòi 18 như hai dòng đầu.
    """
    compact = re.sub(r"\s", "", line).upper()
    if len(compact) < 14:
        return False
    allowed = sum(1 for ch in compact if ch.isalnum() or ch == "<")
    if allowed / len(compact) < 0.9:
        return False
    return compact.count("<") >= 2 or compact.startswith(("IDVNM", "1DVNM", "ID"))


# -------------------------------------------------------------------- decode

_ID12_RE = re.compile(r"\d{12}")
_ID_PREFIX_RE = re.compile(r"^(?:I?D?VNM|1DVNM)[<]?")
_ROW2_RE = re.compile(
    r"(?<!\d)(\d{6})(\d)([MF<X])(\d{6})(\d)([<A-Z]{3})"
)
_ROW2_LOOSE_RE = re.compile(r"(?<!\d)(\d{6})(\d?)([MF<])(\d{6})(\d?)")


def decode_mrz(lines: list[str]) -> MrzResult:
    """Giải mã danh sách dòng MRZ (đã qua ``normalize_mrz_line``)."""
    result = MrzResult(raw_lines=tuple(lines))
    if not lines:
        return result

    result.id_number = _decode_id(lines)
    row2 = _find_row2(lines)
    if row2 is not None:
        match_row2(result, row2)
    result.full_name = _decode_name(lines)
    return result


def _decode_id(lines: list[str]) -> str:
    """Số CCCD 12 chữ số nằm ngay sau tiền tố ``IDVNM`` của dòng 1."""
    first = lines[0]
    body = _ID_PREFIX_RE.sub("", first)
    repaired = _fix_mrz_digits(body)
    match = _ID12_RE.search(repaired)
    if match:
        return match.group(0)
    # Dự phòng: tìm ở mọi dòng, kể cả khi OCR làm rơi mất tiền tố
    for line in lines:
        match = _ID12_RE.search(_fix_mrz_digits(line.replace("<", "")))
        if match:
            return match.group(0)
    return ""


def _find_row2(lines: list[str]) -> str | None:
    """Dòng 2 là dòng có mẫu YYMMDD + check + giới tính + YYMMDD + check."""
    for line in lines:
        repaired = _fix_mrz_digits(line)
        if _ROW2_RE.search(repaired):
            return repaired
    for line in lines:
        repaired = _fix_mrz_digits(line)
        if _ROW2_LOOSE_RE.search(repaired):
            return repaired
    return None


def match_row2(result: MrzResult, row2: str) -> None:
    """Bóc ngày sinh / giới tính / hạn dùng / quốc tịch từ dòng 2."""
    from .textnorm import date_from_mrz

    strict = _ROW2_RE.search(row2)
    if strict:
        dob, dob_check, sex_char, exp, exp_check, nationality = strict.groups()
        result.valid = verify(dob, dob_check) and verify(exp, exp_check)
    else:
        loose = _ROW2_LOOSE_RE.search(row2)
        if not loose:
            return
        dob, dob_check, sex_char, exp, exp_check = loose.groups()
        nationality = ""

    # Lọc theo mốc thời gian hợp lý: thẻ CCCD gắn chip cấp từ 2021 nên hạn dùng
    # không thể rơi vào thập niên 1940. Thà để trống còn hơn ghi giá trị rác.
    result.date_of_birth = date_from_mrz(dob)
    if not _plausible(result.date_of_birth, kind="birth"):
        result.date_of_birth = ""
    result.date_of_expiry = date_from_mrz(exp)
    if not _plausible(result.date_of_expiry, kind="expiry"):
        result.date_of_expiry = ""
    result.sex = "Nam" if sex_char == "M" else "Nữ" if sex_char == "F" else ""
    nationality = (nationality or "").strip("<")
    if nationality and set(nationality) != {"<"}:
        result.nationality_code = nationality


def _plausible(value: str, *, kind: str) -> bool:
    """Ngày đọc từ MRZ có nằm trong khoảng hợp lý không."""
    if not value:
        return False
    parts = value.split("/")
    if len(parts) != 3 or not parts[2].isdigit():
        return False
    year = int(parts[2])
    current_year = date.today().year
    if kind == "birth":
        return 1900 <= year <= current_year - 5
    # Hạn dùng: thẻ còn hiệu lực hoặc vừa hết hạn trong vài năm
    return current_year - 5 <= year <= current_year + 40


def _decode_name(lines: list[str]) -> str:
    """Dòng 3 chỉ gồm chữ cái và '<' — có ``<<`` ngăn cách họ với tên."""
    for line in lines:
        if re.search(r"\d", line) or "<" not in line:
            continue
        letters = sum(1 for ch in line if ch.isalpha())
        if letters < 3:
            continue
        if "<<" in line:
            return _mrz_name_to_text(line)
    return ""


def _mrz_name_to_text(line: str) -> str:
    """``NGUYEN<VAN<AN<<<<`` -> ``NGUYEN VAN AN``."""
    primary, _, secondary = line.partition("<<")
    parts = [p for p in primary.split("<") if p]
    text = " ".join(parts)
    extra = [p for p in secondary.split("<") if p] if secondary else []
    if extra:
        text = " ".join([text, *extra]).strip()
    return text.strip()


def extract_mrz(ocr_items) -> MrzResult:
    """Tìm MRZ trong danh sách ``TextItem`` và giải mã 3 dòng cuối cùng.

    Không lọc theo vùng ảnh: ``is_mrz_like`` đã đủ chặt (phải có từ 2 dấu '<',
    gần như toàn ASCII), còn MRZ luôn là 3 dòng dưới cùng nên chỉ cần sắp theo
    trục dọc rồi lấy 3 dòng cuối.
    """
    if not ocr_items:
        return MrzResult()

    candidates = [(item.y_center, normalize_mrz_line(item.text))
                  for item in ocr_items if is_mrz_like(item.text)]
    if candidates:
        candidates.sort(key=lambda pair: pair[0])
        return decode_mrz([line for _y, line in candidates][-3:])

    # Dự phòng: ghép toàn bộ text rồi tách các cụm giống MRZ
    joined = re.sub(r"\s+", "", " ".join(item.text for item in ocr_items).upper())
    chunks = re.findall(r"(?:I?D?VN[M]|1DVNM)[A-Z0-9<]{20,}", joined)
    if chunks:
        return decode_mrz([normalize_mrz_line(chunk) for chunk in chunks[:3]])
    return MrzResult()
