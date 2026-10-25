"""Kiểu dữ liệu dùng chung."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .textnorm import has_vietnamese_marks

# Nhãn tiếng Việt để hiển thị trong cảnh báo (thay cho tên biến Python)
FIELD_LABELS = {
    "id_number": "Số CCCD",
    "full_name": "Họ và tên",
    "date_of_birth": "Ngày sinh",
    "sex": "Giới tính",
    "nationality": "Quốc tịch",
    "place_of_origin": "Quê quán",
    "place_of_residence": "Nơi thường trú",
    "date_of_expiry": "Có giá trị đến",
    "date_of_issue": "Ngày cấp",
    "personal_identification": "Đặc điểm nhận dạng",
    "issue_authority": "Nơi cấp",
}


def _count_marks(text: str) -> int:
    """Số ký tự có dấu tiếng Việt — dùng để chọn bản đọc "đầy đủ dấu" hơn."""
    return sum(1 for ch in text.lower() if has_vietnamese_marks(ch))


# Trường nào do mặt nào của thẻ in ra (mặt đó là nguồn đáng tin hơn)
_FIELD_OWNER = {
    "id_number": "front",
    "full_name": "front",
    "date_of_birth": "front",
    "sex": "front",
    "nationality": "front",
    "place_of_origin": "front",
    "place_of_residence": "front",
    "date_of_expiry": "front",
    "date_of_issue": "back",
    "personal_identification": "back",
    "issue_authority": "back",
}


@dataclass
class TextItem:
    """Một đoạn text OCR kèm hình học (dùng để ghép dòng, đoán vị trí)."""

    text: str
    score: float
    box: list[tuple[float, float]]  # 4 điểm: TL, TR, BR, BL

    @property
    def x_left(self) -> float:
        return min(p[0] for p in self.box)

    @property
    def x_right(self) -> float:
        return max(p[0] for p in self.box)

    @property
    def y_top(self) -> float:
        return min(p[1] for p in self.box)

    @property
    def y_bottom(self) -> float:
        return max(p[1] for p in self.box)

    @property
    def y_center(self) -> float:
        return (self.y_top + self.y_bottom) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.y_bottom - self.y_top)

    @property
    def width(self) -> float:
        return max(1.0, self.x_right - self.x_left)


@dataclass
class CardRecord:
    """Thông tin một công dân (gộp mặt trước + mặt sau)."""

    id_number: str = ""
    full_name: str = ""
    date_of_birth: str = ""      # dd/mm/yyyy
    sex: str = ""                # Nam / Nữ
    nationality: str = ""
    place_of_origin: str = ""
    place_of_residence: str = ""
    date_of_expiry: str = ""     # dd/mm/yyyy
    date_of_issue: str = ""      # dd/mm/yyyy
    personal_identification: str = ""
    issue_authority: str = ""
    source_files: list[str] = field(default_factory=list)
    side: str = ""               # 'front' | 'back' | '' (không xác định)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def merge_from(self, other: CardRecord) -> None:
        """Gộp mặt trước và mặt sau của cùng một người (theo số CCCD).

        Khi hai mặt đọc ra giá trị khác nhau, ưu tiên **mặt sở hữu trường đó**:
        họ tên / ngày sinh / quê quán / thường trú in ở mặt trước, còn ngày cấp /
        đặc điểm nhận dạng / nơi cấp in ở mặt sau. Nếu không rõ mặt nào sở hữu
        thì chọn giá trị có dấu tiếng Việt đầy đủ hơn (MRZ không có dấu).
        """
        for name in self.field_names():
            current, new = getattr(self, name), getattr(other, name)
            if not current:
                setattr(self, name, new)
            elif not new or current == new:
                continue
            else:
                winner = self._pick_value(name, current, new, other)
                if winner == new:
                    setattr(self, name, winner)
                    label = FIELD_LABELS.get(name, name)
                    self.warnings.append(
                        f"{label}: hai mặt đọc khác nhau ('{current}' / '{new}'), "
                        f"giữ '{winner}'"
                    )
        for path in other.source_files:
            if path not in self.source_files:
                self.source_files.append(path)
        if not self.side:
            self.side = other.side
        self.confidence = max(self.confidence, other.confidence)
        for w in other.warnings:
            if w not in self.warnings:
                self.warnings.append(w)
        self._drop_stale_warnings()

    def _pick_value(self, name: str, current: str, new: str,
                    other: CardRecord) -> str:
        """Quyết định giữ giá trị nào khi hai mặt mâu thuẫn."""
        owner = _FIELD_OWNER.get(name)
        if owner == "front":
            if self.side == "front":
                return current
            if other.side == "front":
                return new
        elif owner == "back":
            if self.side == "back":
                return current
            if other.side == "back":
                return new
        # Cùng mặt hoặc không rõ: chọn bản có dấu tiếng Việt đầy đủ hơn
        current_marks = _count_marks(current)
        new_marks = _count_marks(new)
        if new_marks > current_marks:
            return new
        if current_marks > new_marks:
            return current
        return current if len(current) >= len(new) else new

    def _drop_stale_warnings(self) -> None:
        """Bỏ cảnh báo đã hết đúng sau khi gộp (ví dụ tên MRZ đã được thay)."""
        if has_vietnamese_marks(self.full_name):
            self.warnings = [w for w in self.warnings
                             if "từ MRZ" not in w and not w.startswith("Họ tên")]

    @staticmethod
    def field_names() -> tuple[str, ...]:
        return (
            "id_number",
            "full_name",
            "date_of_birth",
            "sex",
            "nationality",
            "place_of_origin",
            "place_of_residence",
            "date_of_expiry",
            "date_of_issue",
            "personal_identification",
            "issue_authority",
        )

    def is_empty(self) -> bool:
        return not any(getattr(self, n) for n in self.field_names())

    def to_row(self, index: int) -> dict[str, Any]:
        """Dòng để ghi ra Excel/CSV (theo đúng thứ tự cột)."""
        return {
            "STT": index,
            "Số CCCD": self.id_number,
            "Họ và tên": self.full_name,
            "Ngày sinh": self.date_of_birth,
            "Giới tính": self.sex,
            "Quốc tịch": self.nationality,
            "Quê quán": self.place_of_origin,
            "Nơi thường trú": self.place_of_residence,
            "Ngày cấp": self.date_of_issue,
            "Có giá trị đến": self.date_of_expiry,
            "Đặc điểm nhận dạng": self.personal_identification,
            "Nơi cấp": self.issue_authority,
            "Độ tin cậy": round(self.confidence, 2),
            "Cảnh báo": "; ".join(self.warnings),
            "Tệp nguồn": "; ".join(self.source_files),
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
