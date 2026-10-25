"""Chuẩn hoá text: bỏ dấu để so khớp nhãn, sửa lỗi OCR số, chuẩn hoá ngày."""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- bỏ dấu

_SPECIAL_FOLD = {
    "đ": "d",
    "Đ": "D",
    "ơ": "o",
    "Ơ": "O",
    "ư": "u",
    "Ư": "U",
    "ă": "a",
    "Ă": "A",
    "â": "a",
    "Â": "A",
    "ê": "e",
    "Ê": "E",
    "ô": "o",
    "Ô": "O",
}


def strip_diacritics(text: str) -> str:
    """'Nơi thường trú' -> 'Noi thuong tru' (giữ nguyên hoa/thường)."""
    for src, dst in _SPECIAL_FOLD.items():
        text = text.replace(src, dst)
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def fold(text: str) -> str:
    """Khoá so khớp nhãn: bỏ dấu, viết thường, chỉ giữ chữ + số."""
    text = strip_diacritics(text).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def fold_compact(text: str) -> str:
    """Như ``fold`` nhưng bỏ cả khoảng trắng (so khớp nhãn dính liền)."""
    return fold_chars(text)[0]


def fold_chars(text: str) -> tuple[str, list[int]]:
    """Bỏ dấu từng ký tự và trả kèm bảng ánh xạ về vị trí trong chuỗi gốc.

    Nhờ bảng ánh xạ này, khi khớp được nhãn ``hovaten`` ở chuỗi đã bỏ dấu ta
    vẫn cắt đúng phần giá trị trên chuỗi gốc còn nguyên dấu tiếng Việt.
    """
    folded: list[str] = []
    index_map: list[int] = []
    for position, char in enumerate(text):
        base = _SPECIAL_FOLD.get(char, char)
        base = "".join(
            c for c in unicodedata.normalize("NFD", base) if not unicodedata.combining(c)
        ).lower()
        for sub in base:
            if sub.isalnum():
                folded.append(sub)
                index_map.append(position)
    return "".join(folded), index_map


# ------------------------------------------------- sửa lỗi OCR cho phần số

_DIGIT_LOOKALIKE = str.maketrans({
    "O": "0", "o": "0", "D": "0", "Q": "0",
    "I": "1", "l": "1", "i": "1", "|": "1", "!": "1", "]": "1", "[": "1",
    "Z": "2", "z": "2",
    "A": "4",
    "S": "5", "s": "5",
    "G": "6", "b": "6",
    "T": "7", "?": "7",
    "B": "8",
    "g": "9", "q": "9",
})


def digits_only(text: str, *, repair: bool = True) -> str:
    """Lấy ra dãy chữ số từ text; ``repair`` thì đổi ký tự dễ nhầm thành số."""
    if repair:
        text = text.translate(_DIGIT_LOOKALIKE)
    return re.sub(r"\D", "", text)


# --------------------------------------------------------------- ngày tháng

_DATE_RE = re.compile(
    r"(?<!\d)"
    r"(\d{1,2})\s*[/\-.\s]\s*(\d{1,2})\s*[/\-.\s]\s*(\d{4})"
    r"(?!\d)"
)

# Trường hợp OCR mất dấu phân cách: 15051995
_DATE_COMPACT_RE = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{4})(?!\d)")


def normalize_date(text: str) -> str:
    """Tìm và chuẩn hoá ngày đầu tiên trong text về 'dd/mm/yyyy'.

    Trả về chuỗi rỗng nếu không tìm thấy hoặc ngày không hợp lệ.
    """
    if not text:
        return ""
    for match in _DATE_RE.finditer(text):
        day, month, year = (int(g) for g in match.groups())
        if _valid_ymd(year, month, day):
            return f"{day:02d}/{month:02d}/{year:04d}"
    repaired = digits_only(text)
    for match in _DATE_COMPACT_RE.finditer(repaired):
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if _valid_ymd(year, month, day):
            return f"{day:02d}/{month:02d}/{year:04d}"
    return ""


def _valid_ymd(year: int, month: int, day: int) -> bool:
    if not (1900 <= year <= 2100):
        return False
    if not (1 <= month <= 12):
        return False
    days_in_month = [31, 29 if _is_leap(year) else 28, 31, 30, 31, 30,
                     31, 31, 30, 31, 30, 31]
    return 1 <= day <= days_in_month[month - 1]


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def date_from_mrz(yymmdd: str, *, pivot: int = 40) -> str:
    """'950515' -> '15/05/1995' (giữ đúng quy ước cửa sổ 100 năm của MRZ)."""
    yymmdd = digits_only(yymmdd)
    if len(yymmdd) != 6:
        return ""
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    year = 1900 + yy if yy > pivot else 2000 + yy
    if _valid_ymd(year, mm, dd):
        return f"{dd:02d}/{mm:02d}/{year:04d}"
    return ""


# ------------------------------------------------------------------ giới tính

def normalize_sex(text: str) -> str:
    """Chuẩn hoá về 'Nam' / 'Nữ' / ''. """
    if not text:
        return ""
    key = fold_compact(text)
    if key.startswith("nam") or key in {"m", "male", "mle"}:
        return "Nam"
    if key.startswith("nu") or key in {"f", "female", "fmle"}:
        return "Nữ"
    # 'Nữ' bị OCR thành 'N0', 'Nư'... — xét ký tự đầu
    if key.startswith("n"):
        return "Nữ"
    return ""


# ------------------------------------------------------------------ làm sạch

_MULTISPACE = re.compile(r"\s+")
_HAS_CONTENT = re.compile(r"[0-9A-Za-zÀ-ỹ]")


def clean_value(text: str) -> str:
    """Làm sạch giá trị đọc được: bỏ nhãn tiếng Anh, dấu hai chấm, khoảng trắng thừa.

    Chuỗi chỉ gồm ký hiệu (``/``, ``:``, ``-``) được coi là **rỗng** — tránh
    sinh ra giá trị rác như ``'/'`` khi nhãn và giá trị bị tách dòng.
    """
    if not text:
        return ""
    text = text.replace("\u00a0", " ")
    # Cắt phần song ngữ kiểu "Họ và tên / Full name:"
    text = re.sub(r"\s*/\s*(full name|date of birth|sex|nationality|place of origin|"
                  r"place of residence|date of expiry|no\.?|personal identification)\s*:?",
                  " ", text, flags=re.IGNORECASE)
    text = text.replace(":", " ").replace(";", " ")
    text = _MULTISPACE.sub(" ", text)
    text = text.strip(" .-_<>/|")
    if not _HAS_CONTENT.search(text):
        return ""
    return text


def drop_leading_noise(text: str) -> str:
    """Bỏ ký tự rác OCR ở đầu giá trị (``<<``, ngoặc, mũi tên...)."""
    return re.sub(r"^[^0-9A-Za-zÀ-ỹ]+", "", text)


# Ký tự có dấu tiếng Việt — dấu hiệu chắc chắn của một giá trị thật
_VIETNAMESE_MARKS = set("ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")


def has_vietnamese_marks(text: str) -> bool:
    """Có ký tự mang dấu tiếng Việt không.

    Dùng để phân biệt giá trị thật với nhãn song ngữ: nhãn tiếng Anh in trên
    thẻ (``Full name``, ``Place of residence``) không bao giờ có dấu.
    """
    return any(ch in _VIETNAMESE_MARKS for ch in text.lower())
