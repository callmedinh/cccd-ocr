"""Gộp kết quả từ nhiều biến thể ảnh thành một ``CardRecord`` tốt nhất.

Vì sao không gộp thẳng ở mức ô text: các biến thể ảnh có toạ độ hơi lệch nhau,
gộp theo hình học dễ dính chữ của dòng này sang dòng khác. Thay vào đó ta
**bóc tách riêng từng biến thể** rồi bỏ phiếu ở mức *trường*: biến thể nào
đọc được nhiều trường và tự tin hơn thì làm bản gốc, các biến thể khác chỉ
bù vào chỗ còn trống.
"""

from __future__ import annotations

from .models import CardRecord
from .mrz import MrzResult

# Trọng số khi chấm điểm một lần bóc tách
_KEY_FIELDS = ("id_number", "full_name", "date_of_birth", "sex", "place_of_residence")

# Trường văn bản tự do: bản dài hơn thường là bản đọc đầy đủ hơn
_TEXT_FIELDS = frozenset({"full_name", "place_of_origin", "place_of_residence",
                          "personal_identification"})


def score_record(record: CardRecord) -> float:
    """Điểm càng cao càng đáng tin: nhiều trường + tự tin + đối chiếu khớp."""
    filled = sum(1 for name in CardRecord.field_names() if getattr(record, name))
    key = sum(1 for name in _KEY_FIELDS if getattr(record, name))
    bonus = 2.0 if _mrz_consistent(record) else 0.0
    penalty = 0.3 * len(record.warnings)
    return filled + key + record.confidence + bonus - penalty


def _mrz_consistent(record: CardRecord) -> bool:
    """Có dấu hiệu MRZ khớp với phần in (số CCCD và ngày sinh cùng có)."""
    return bool(record.id_number and record.date_of_birth and record.date_of_expiry)


def pick_best(records: list[CardRecord]) -> CardRecord:
    """Chọn bản ghi tốt nhất rồi bù các trường còn thiếu từ bản còn lại.

    Với các trường văn bản (họ tên, địa chỉ...), nếu biến thể này đọc ra một
    phần của giá trị mà biến thể khác đọc ra đầy đủ hơn (``VĂN AN`` nằm trong
    ``NGUYỄN VĂN AN``) thì lấy bản đầy đủ — thường do một từ bị rơi ở biến thể
    kia vì confidence thấp.
    """
    candidates = [r for r in records if not r.is_empty()]
    if not candidates:
        return records[0] if records else CardRecord()

    ranked = sorted(candidates, key=score_record, reverse=True)
    best = ranked[0]

    for name in CardRecord.field_names():
        values = [getattr(record, name) for record in ranked if getattr(record, name)]
        if not values:
            continue
        chosen = getattr(best, name) or values[0]
        if name in _TEXT_FIELDS:
            chosen = _longest_compatible(chosen, values)
        setattr(best, name, chosen)

    for other in ranked[1:]:
        for path in other.source_files:
            if path not in best.source_files:
                best.source_files.append(path)
    best.confidence = max(r.confidence for r in ranked)
    # Bỏ cảnh báo sinh ra từ biến thể ảnh đọc kém (giữ cảnh báo của bản tốt nhất)
    seen: list[str] = []
    for warning in best.warnings:
        if warning not in seen:
            seen.append(warning)
    best.warnings = seen
    return best


def _longest_compatible(current: str, values: list[str]) -> str:
    """Bản dài hơn nếu nó bao trùm bản hiện tại (bỏ dấu để so sánh)."""
    from .textnorm import fold_compact

    best, best_folded = current, fold_compact(current)
    for value in values:
        folded = fold_compact(value)
        if not folded:
            continue
        if len(value) > len(best) and (not best_folded or best_folded in folded):
            best, best_folded = value, folded
    return best


# ------------------------------------------------------------------ MRZ

def best_mrz(results: list[MrzResult]) -> MrzResult:
    """Chọn kết quả MRZ tốt nhất trong các lượt đọc.

    Tiêu chí quan trọng nhất là **tự nhất quán**: số CCCD phải khớp với ngày
    sinh và giới tính đọc được ở dòng 2 (mã giới tính/thế kỷ + 2 số cuối năm
    sinh). Nhờ đó một lần đọc sai số nhưng đúng ngày sinh sẽ bị loại.
    """
    usable = [m for m in results if not m.is_empty()]
    if not usable:
        return MrzResult()

    def rank(mrz: MrzResult) -> tuple[int, int, int, int]:
        fields = sum(1 for value in (mrz.id_number, mrz.date_of_birth, mrz.sex,
                                     mrz.date_of_expiry, mrz.full_name) if value)
        return (
            1 if _self_consistent(mrz) else 0,
            1 if mrz.valid else 0,
            fields,
            len(mrz.id_number),
        )

    return max(usable, key=rank)


def _self_consistent(mrz: MrzResult) -> bool:
    """Số CCCD khớp ngày sinh + giới tính cùng đọc từ MRZ."""
    if not mrz.id_number:
        return False
    if not (mrz.date_of_birth or mrz.sex):
        return False
    from . import idcheck

    return not idcheck.cross_check(mrz.id_number, mrz.date_of_birth, mrz.sex)


def reconcile(record: CardRecord, mrz: MrzResult) -> CardRecord:
    """Điền các trường còn trống từ MRZ và phân xử khi hai nguồn mâu thuẫn.

    Thứ tự quan trọng: **bù ngày sinh / giới tính trước**, rồi mới phân xử số
    CCCD — vì cấu trúc số CCCD (mã giới tính + 2 số cuối năm sinh) chính là
    công cụ để biết số nào đúng.
    """
    if mrz.is_empty():
        return record

    if mrz.date_of_birth and not record.date_of_birth:
        record.date_of_birth = mrz.date_of_birth
    if mrz.sex and not record.sex:
        record.sex = mrz.sex
    if mrz.date_of_expiry and not record.date_of_expiry:
        record.date_of_expiry = mrz.date_of_expiry
    if mrz.full_name and not record.full_name:
        record.full_name = mrz.full_name
        record.warnings.append("Họ tên lấy từ MRZ nên không có dấu tiếng Việt")

    if mrz.id_number:
        record.id_number = _arbitrate_id(record, mrz.id_number)
    return record


def _arbitrate_id(record: CardRecord, mrz_id: str) -> str:
    """Chọn giữa số in trên thẻ và số trong MRZ bằng phép đối chiếu cấu trúc."""
    from . import idcheck

    printed = record.id_number
    if not printed:
        return mrz_id
    if printed == mrz_id:
        return printed

    def consistent(value: str) -> bool:
        return not idcheck.cross_check(value, record.date_of_birth, record.sex)

    printed_ok, mrz_ok = consistent(printed), consistent(mrz_id)
    if mrz_ok and not printed_ok:
        record.warnings.append(
            f"Số đọc trên thẻ ({printed}) không khớp ngày sinh/giới tính; "
            f"dùng số từ MRZ ({mrz_id})"
        )
        return mrz_id
    record.warnings.append(
        f"Số CCCD in trên thẻ ({printed}) khác MRZ ({mrz_id}) — kiểm tra lại"
    )
    return printed
