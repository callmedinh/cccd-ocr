"""Bóc tách trường thông tin từ kết quả OCR.

Chiến lược: **bám nhãn in trên thẻ** (label-anchored). Nhãn được so khớp sau
khi bỏ dấu, nên ``Họ và tên``, ``HO VA TEN``, ``Ho va ten / Full name`` đều
khớp cùng một mẫu. Giá trị lấy từ phần còn lại của chính dòng đó, hoặc từ các
dòng ngay bên dưới nếu nhãn và giá trị bị tách rời (trường hợp ``Nơi thường
trú`` xuống dòng trên thẻ thật).

MRZ ở mặt sau được dùng làm nguồn đối chiếu thứ hai cho số CCCD, ngày sinh,
giới tính và hạn dùng.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from . import idcheck
from .models import CardRecord, TextItem
from .mrz import MrzResult, extract_mrz
from .textnorm import (
    clean_value,
    fold_chars,
    has_vietnamese_marks,
    normalize_date,
    normalize_sex,
)

# ------------------------------------------------------------------- dòng text


@dataclass
class Line:
    """Một dòng thị giác trên thẻ (gộp các ô text cùng độ cao)."""

    text: str
    items: list[TextItem]
    y_center: float
    x_left: float
    x_right: float
    height: float

    @property
    def score(self) -> float:
        if not self.items:
            return 0.0
        return sum(i.score for i in self.items) / len(self.items)


def group_lines(items: list[TextItem]) -> list[Line]:
    """Gộp các ô text thành dòng theo toạ độ dọc, sắp theo thứ tự đọc."""
    if not items:
        return []
    ordered = sorted(items, key=lambda i: (i.y_center, i.x_left))
    clusters: list[list[TextItem]] = []
    for item in ordered:
        placed = False
        for cluster in clusters:
            reference = cluster[-1]
            tolerance = 0.55 * max(reference.height, item.height)
            if abs(item.y_center - _cluster_center(cluster)) <= tolerance:
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])

    lines: list[Line] = []
    for cluster in clusters:
        cluster.sort(key=lambda i: i.x_left)
        lines.append(
            Line(
                text=" ".join(i.text for i in cluster),
                items=cluster,
                y_center=_cluster_center(cluster),
                x_left=min(i.x_left for i in cluster),
                x_right=max(i.x_right for i in cluster),
                height=max(i.height for i in cluster),
            )
        )
    lines.sort(key=lambda line: line.y_center)
    return lines


def _cluster_center(cluster: list[TextItem]) -> float:
    return sum(i.y_center for i in cluster) / len(cluster)


# ---------------------------------------------------------------------- nhãn

# Khoá nhãn đã bỏ dấu + bỏ khoảng trắng, xếp theo độ dài giảm dần khi so khớp.
LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "id_label": ("sodinhdanhcanhan", "socancuoccongdan", "socancuoc", "socccd",
                 "sono", "so", "idno", "personalidentificationnumber"),
    "full_name": ("hovaten", "hoten", "fullname", "tencongdan"),
    "date_of_birth": ("ngaysinh", "dateofbirth", "sinhnhay"),
    "sex": ("gioitinh", "sex", "gender"),
    "nationality": ("quoctich", "nationality", "quoctichvietnam"),
    "place_of_origin": ("quequan", "placeoforigin", "noisinh"),
    "place_of_residence": ("noithuongtru", "placeofresidence", "thuongtru"),
    "date_of_expiry": ("cogiatriden", "ngayhethan", "dateofexpiry", "giatricden",
                       "cogiatricen", "co giatri den"),
    "date_of_issue": ("ngaythangnamcap", "ngaycapngaythangnam", "ngaycap",
                      "dateofissue", "ngaythangcap"),
    "personal_identification": ("dacdiemnhandang", "personalid",
                                "personalidentification", "nhandang"),
    "issue_authority": ("noicap", "issueauthority", "cuctruongcuccanhsat",
                        "cuctruongcuc"),
    "header_title": ("cancuoccongdan", "cancuoc", "cancuoccongdanchip"),
    "mrz_marker": ("idvnm",),
}

# Nhãn "yếu": khớp được nhưng dễ trùng với chữ thường (``Số 12, Ngõ 45``),
# nên không dùng làm mốc để cắt giá trị của trường khác.
_WEAK_ALIASES = frozenset({"so"})

# Nhãn nào cho phép giá trị nằm ở các dòng phía dưới (không cùng dòng).
MULTILINE_FIELDS = frozenset({
    "place_of_origin",
    "place_of_residence",
    "personal_identification",
    "issue_authority",
    "full_name",
    "nationality",
})

_ALL_ALIASES: list[tuple[str, str]] = sorted(
    ((alias, key) for key, aliases in LABEL_ALIASES.items() for alias in aliases),
    key=lambda pair: -len(pair[0]),
)


@dataclass
class LabelHit:
    """Kết quả khớp một nhãn trên một dòng."""

    field: str
    line_index: int
    alias: str
    remainder: str           # phần text còn lại trên cùng dòng (đã cắt ở nhãn kế tiếp)
    folded_remainder: str
    at_line_start: bool = False


def alias_occurrences(text: str) -> list[tuple[str, str, int, int]]:
    """Mọi vị trí xuất hiện của nhãn trong một dòng: (field, alias, start, end).

    Trả về toạ độ trên **chuỗi gốc** (có dấu) nhờ ``fold_chars``. Danh sách
    không chồng lấn và sắp theo thứ tự xuất hiện — nhờ vậy xử lý được cả kiểu
    in hai trường trên một dòng: ``Giới tính: Nam   Quốc tịch: Việt Nam``.
    """
    folded, index_map = fold_chars(text)
    if not folded:
        return []
    raw_hits: list[tuple[str, str, int, int]] = []
    for alias, key in _ALL_ALIASES:
        position = folded.find(alias)
        while position != -1:
            start = index_map[position]
            end = index_map[position + len(alias) - 1] + 1
            raw_hits.append((key, alias, start, end))
            position = folded.find(alias, position + 1)

    raw_hits.sort(key=lambda hit: (hit[2], -(hit[3] - hit[2])))
    pruned: list[tuple[str, str, int, int]] = []
    last_end = 0
    for hit in raw_hits:
        if hit[2] < last_end:
            continue  # chồng lấn với nhãn trước -> bỏ
        pruned.append(hit)
        last_end = hit[3]
    return pruned


def match_label(text: str, *, strong_only: bool = True) -> tuple[str, str] | None:
    """Nhãn **đầu dòng** (dùng để biết một dòng có mở đầu bằng nhãn không)."""
    hits = alias_occurrences(text)
    if not hits or hits[0][2] != 0:
        return None
    key, alias, _start, _end = hits[0]
    if strong_only and alias in _WEAK_ALIASES:
        return None
    return key, alias


def _remainder_after(text: str, hits: list[tuple[str, str, int, int]],
                     position: int) -> str:
    """Cắt phần text sau nhãn thứ ``position``, dừng ở **nhãn mạnh** kế tiếp.

    Nhãn yếu (``Số``) bị bỏ qua khi tìm điểm dừng: địa chỉ kiểu
    ``Số 12, Ngõ 45`` có chữ "Số" chứ không phải nhãn "Số / No." của thẻ.
    """
    _key, _alias, _start, end = hits[position]
    stop = len(text)
    for _key2, alias2, start2, _end2 in hits[position + 1:]:
        if alias2 in _WEAK_ALIASES:
            continue
        stop = start2
        break
    return text[end:stop]


def find_label(lines: list[Line], field_name: str) -> LabelHit | None:
    """Tìm nhãn của một trường (có thể nằm giữa dòng, không chỉ ở đầu dòng)."""
    best: LabelHit | None = None
    best_rank: tuple[int, int, int] | None = None
    for index, line in enumerate(lines):
        hits = alias_occurrences(line.text)
        for position, (key, alias, start, _end) in enumerate(hits):
            if key != field_name:
                continue
            remainder = _remainder_after(line.text, hits, position)
            hit = LabelHit(
                field=field_name,
                line_index=index,
                alias=alias,
                remainder=remainder,
                folded_remainder=fold_chars(remainder)[0],
                at_line_start=(start == 0),
            )
            # Ưu tiên: có giá trị đi kèm > nhãn ở đầu dòng > dòng phía trên
            rank = (
                1 if hit.folded_remainder else 0,
                1 if hit.at_line_start else 0,
                -index,
            )
            if best_rank is None or rank > best_rank:
                best, best_rank = hit, rank
                if rank[0] == 1:
                    return hit  # đã tốt nhất có thể
    return best


def value_after_label(lines: list[Line], hit: LabelHit, *, multiline: bool,
                      max_follow: int = 3,
                      validator=None) -> str:
    """Ghép giá trị: phần còn lại trên dòng nhãn + các dòng tiếp theo.

    ``validator`` (nếu có) nhận text và trả về True/False — dùng để loại các
    dòng không thể là giá trị của trường này (ví dụ dòng chứa chữ số thì không
    thể là họ tên).
    """
    chunks: list[str] = []
    remainder = clean_value(hit.remainder)
    if remainder and not _is_english_label(remainder):
        if validator is None or validator(remainder):
            chunks.append(remainder)

    anchor = lines[hit.line_index]
    previous_bottom = anchor.y_center
    for offset in range(1, max_follow + 1):
        next_index = hit.line_index + offset
        if next_index >= len(lines):
            break
        candidate = lines[next_index]
        # Gặp bất kỳ nhãn mạnh nào (kể cả giữa dòng) -> hết trường này.
        # Trên ảnh mờ, Tesseract hay trộn thứ tự dòng nên chỉ kiểm tra đầu dòng
        # là không đủ, giá trị sẽ bị ghép lẫn sang trường khác.
        if any(alias not in _WEAK_ALIASES
               for _key, alias, _start, _end in alias_occurrences(candidate.text)):
            break
        if candidate.y_center - previous_bottom > 2.6 * max(anchor.height, 10):
            break  # cách quá xa -> khối khác
        if not multiline:
            break
        if candidate.x_left < anchor.x_left - 3 * anchor.height:
            break  # thụt lề trái hẳn -> khối khác
        text = clean_value(candidate.text)
        if not text or _is_english_label(text):
            continue
        if validator is not None and not validator(text):
            continue
        chunks.append(text)
        previous_bottom = candidate.y_center
        if len(chunks) >= max_follow:
            break

    return clean_value(" ".join(c for c in chunks if c))


_ENGLISH_KEYS = (
    "fullname", "dateofbirth", "sex", "nationality", "placeoforigin",
    "placeofresidence", "dateofexpiry", "dateofissue", "personalidentification",
    "no", "idno",
)


def _is_english_label(text: str) -> bool:
    """Chuỗi chỉ là phần song ngữ của nhãn (``/ Full name``) — không phải giá trị.

    So khớp mờ vì trên ảnh mờ chữ này hay bị đọc sai một ký tự
    (``Full nam6``, ``Nalonaliiy``), nếu so khớp tuyệt đối thì nó sẽ lọt vào
    cột dữ liệu. Hai điều kiện chặn dương tính giả: chuỗi phải **không có dấu
    tiếng Việt** (giá trị thật thì gần như luôn có) và phải ngắn.
    """
    stripped = text.strip(" /:-.()[]|")
    if not stripped or len(stripped) > 32 or has_vietnamese_marks(stripped):
        return False
    folded = fold_chars(stripped)[0]
    if not folded:
        return False
    for key in _ENGLISH_KEYS:
        if folded == key or SequenceMatcher(None, folded, key).ratio() >= 0.7:
            return True
    return False


# --------------------------------------------------------------- số CCCD

_ID_CANDIDATE_RE = re.compile(r"\d{9,12}")


def _id_candidates(lines: list[Line]) -> list[tuple[str, float, int]]:
    """Mọi ứng viên số CCCD: (số, điểm tin cậy, chỉ số dòng)."""
    found: list[tuple[str, float, int]] = []
    for index, line in enumerate(lines):
        for match in _ID_CANDIDATE_RE.finditer(line.text):
            value = match.group(0)
            if len(value) == 9:  # CMND cũ vẫn cho qua nhưng điểm thấp
                score = line.score * 0.6
            elif len(value) == 12:
                score = line.score
            else:
                continue
            if idcheck.is_valid_shape(value):
                score += 0.2
            found.append((value, score, index))
    return found


def extract_id_number(lines: list[Line], mrz: MrzResult) -> tuple[str, list[str]]:
    """Chọn số CCCD.

    Ưu tiên số **in cỡ lớn trên mặt trước** vì mắt người cũng đối chiếu theo
    đó và nó được đọc ở độ phân giải cao; số trong MRZ chỉ dùng khi không đọc
    được số in (MRZ là phông chữ nhỏ, dễ sai hơn). Nếu hai nguồn lệch nhau thì
    vẫn giữ số in nhưng ghi cảnh báo để người dùng kiểm tra.
    """
    warnings: list[str] = []
    candidates = _id_candidates(lines)
    printed = _pick_printed_id(lines, candidates)
    mrz_id = mrz.id_number if idcheck.is_valid_shape(mrz.id_number) else ""

    if printed and mrz_id and printed != mrz_id:
        warnings.append(
            f"Số CCCD in trên thẻ ({printed}) khác MRZ ({mrz_id}) — kiểm tra lại"
        )
    if printed:
        return printed, warnings
    if mrz_id:
        return mrz_id, warnings
    if candidates:
        value, _score, _index = max(
            candidates, key=lambda c: (idcheck.is_valid_shape(c[0]), c[1]))
        return value, warnings
    return "", warnings


def _pick_printed_id(lines: list[Line],
                     candidates: list[tuple[str, float, int]]) -> str:
    """Số CCCD in trên thẻ: ưu tiên dòng có nhãn 'Số / No.'."""
    if not candidates:
        return ""
    hit = find_label(lines, "id_label")
    if hit is not None:
        labelled = [(value, score) for value, score, index in candidates
                    if index == hit.line_index or index == hit.line_index + 1]
        if labelled:
            value, _ = max(labelled, key=lambda pair: (idcheck.is_valid_shape(pair[0]),
                                                       pair[1]))
            return value
    value, _score, _index = max(
        candidates, key=lambda c: (idcheck.is_valid_shape(c[0]), c[1]))
    return value


# ------------------------------------------------------------- parser chính


@dataclass
class ParseResult:
    record: CardRecord
    lines: list[Line] = field(default_factory=list)
    mrz: MrzResult = field(default_factory=MrzResult)


def parse_card(items: list[TextItem], source: str = "",
               mrz: MrzResult | None = None) -> ParseResult:
    """Phân tích kết quả OCR của **một** ảnh thành ``CardRecord``.

    ``mrz`` có thể được truyền vào nếu phía gọi đã đọc MRZ bằng một lượt riêng
    (chính xác hơn việc tự tìm trong tập ``items``).
    """
    lines = group_lines(items)
    if mrz is None or mrz.is_empty():
        mrz = extract_mrz(items)
    record = CardRecord(source_files=[source] if source else [])
    scores: list[float] = []

    def take(field_name: str, *, multiline: bool | None = None,
             validator=None) -> str:
        hit = find_label(lines, field_name)
        if hit is None:
            return ""
        multi = (field_name in MULTILINE_FIELDS) if multiline is None else multiline
        long_fields = ("place_of_residence", "place_of_origin", "personal_identification")
        value = value_after_label(
            lines, hit,
            multiline=multi,
            max_follow=4 if field_name in long_fields else 2,
            validator=validator,
        )
        if value:
            scores.append(lines[hit.line_index].score)
        return value

    # --- số CCCD
    record.id_number, id_warnings = extract_id_number(lines, mrz)
    record.warnings.extend(id_warnings)

    # --- họ tên
    name = take("full_name", validator=_looks_like_name)
    if mrz.full_name:
        if not name:
            name = mrz.full_name
            record.warnings.append("Họ tên lấy từ MRZ nên không có dấu tiếng Việt")
        elif not _names_agree(name, mrz.full_name):
            record.warnings.append(
                f"Họ tên mặt trước '{name}' khác MRZ '{mrz.full_name}'"
            )
    record.full_name = name

    # --- ngày sinh
    dob = normalize_date(take("date_of_birth"))
    if not dob and mrz.date_of_birth:
        dob = mrz.date_of_birth
    record.date_of_birth = dob

    # --- giới tính
    sex = normalize_sex(take("sex"))
    if not sex and mrz.sex:
        sex = mrz.sex
    record.sex = sex

    # --- quốc tịch
    nationality = take("nationality", validator=_looks_like_country)
    if not nationality and mrz.nationality_code == "VNM":
        nationality = "Việt Nam"
    record.nationality = _clean_nationality(nationality)

    # --- quê quán / thường trú
    record.place_of_origin = take("place_of_origin")
    record.place_of_residence = take("place_of_residence")

    # --- ngày cấp / hết hạn
    record.date_of_issue = normalize_date(take("date_of_issue"))
    expiry = normalize_date(take("date_of_expiry"))
    if not expiry and mrz.date_of_expiry:
        expiry = mrz.date_of_expiry
    record.date_of_expiry = expiry

    # --- đặc điểm nhận dạng / nơi cấp
    record.personal_identification = take("personal_identification")
    authority = take("issue_authority")
    if not authority:
        authority = _authority_from_lines(lines)
    record.issue_authority = _normalize_authority(authority)

    # --- đối chiếu chéo với cấu trúc số CCCD
    record.warnings.extend(
        idcheck.cross_check(record.id_number, record.date_of_birth, record.sex)
    )

    record.confidence = (sum(scores) / len(scores)) if scores else 0.0
    if mrz.valid:
        record.confidence = max(record.confidence, 0.9)
    if not scores and mrz.is_empty():
        record.confidence = 0.0
    record.side = _detect_side(lines, mrz)
    return ParseResult(record=record, lines=lines, mrz=mrz)


# Nhãn đặc trưng để nhận biết đang đọc mặt nào của thẻ
_FRONT_MARKERS = ("hovaten", "hoten", "quequan", "noithuongtru", "gioitinh",
                  "quoctich", "ngaysinh")
_BACK_MARKERS = ("dacdiemnhandang", "ngaythangnamcap", "canhsatquanlyhanhchinh",
                 "noicap", "idvnm")


def _detect_side(lines: list[Line], mrz: MrzResult) -> str:
    """Đoán ảnh này là mặt trước hay mặt sau (để gộp đúng bên ưu tiên)."""
    folded = [fold_chars(line.text)[0] for line in lines]
    front = sum(1 for key in folded
                if any(key.startswith(marker) for marker in _FRONT_MARKERS))
    back = sum(1 for key in folded
               if any(marker in key for marker in _BACK_MARKERS))
    if not mrz.is_empty():
        back += 2
    if front > back:
        return "front"
    if back > front:
        return "back"
    return ""


def _looks_like_name(text: str) -> bool:
    """Họ tên không chứa chữ số và phải có ít nhất 2 chữ cái."""
    if re.search(r"\d", text):
        return False
    letters = sum(1 for ch in text if ch.isalpha())
    return letters >= 2


def _looks_like_country(text: str) -> bool:
    """Tên quốc gia: chữ cái, không có chữ số, không quá dài."""
    return not re.search(r"\d", text) and len(text) <= 40


def _letters(text: str) -> str:
    """Chỉ giữ chữ cái, bỏ dấu — để so tên mặt trước với MRZ."""
    return re.sub(r"[^a-z]", "", fold_chars(text)[0])


def _names_agree(printed: str, mrz_name: str, threshold: float = 0.82) -> bool:
    """So tên in với tên trong MRZ (MRZ không dấu, có thể sai 1-2 ký tự)."""
    from difflib import SequenceMatcher

    a, b = _letters(printed), _letters(mrz_name)
    if not a or not b:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


_NATIONALITY_MAP = {
    "vietnam": "Việt Nam",
    "viet nam": "Việt Nam",
    "vietnamese": "Việt Nam",
    "vnm": "Việt Nam",
}


def _clean_nationality(value: str) -> str:
    if not value:
        return ""
    key = re.sub(r"\s+", " ", value.strip().lower())
    if key in _NATIONALITY_MAP:
        return _NATIONALITY_MAP[key]
    if fold_chars(value)[0].startswith("viet"):
        return "Việt Nam"
    return value


_AUTHORITY_RE = re.compile(
    r"(CUC TRUONG CUC CANH SAT QUAN LY HANH CHINH VE TRAT TU XA HOI"
    r"|CỤC TRƯỞNG CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI"
    r"|CUC CANH SAT QUAN LY HANH CHINH VE TRAT TU XA HOI"
    r"|CUC QUAN LY XUAT NHAP CANH)",
    re.IGNORECASE,
)

_DEFAULT_AUTHORITY = "CỤC TRƯỞNG CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI"

# Nơi cấp in sẵn trên phôi thẻ, không kèm nhãn -> tìm theo cụm mở đầu
_AUTHORITY_PREFIXES = ("cuctruongcuc", "cuctruong", "cucquanlyxuatnhapcanh",
                       "cuc canh sat")


def _authority_from_lines(lines: list[Line]) -> str:
    """Tìm dòng chứa tên cơ quan cấp (thường in ngay dưới ngày cấp)."""
    for line in lines:
        folded = fold_chars(line.text)[0]
        if folded.startswith(_AUTHORITY_PREFIXES) or "canhsatquanlyhanhchinh" in folded:
            return line.text
    return ""


def _normalize_authority(value: str) -> str:
    """Nơi cấp luôn in bằng chữ hoa — chuẩn hoá về đúng câu in trên phôi thẻ."""
    if not value:
        return ""
    folded = fold_chars(value)[0]
    if "canhsatquanlyhanhchinh" in folded or folded.startswith(("cuctruongcuc",
                                                                "cuctruong")):
        return _DEFAULT_AUTHORITY
    if "quanlyhanhchinh" in folded and "trattu" in folded:
        return _DEFAULT_AUTHORITY
    if "xuatnhapcanh" in folded:
        return "CỤC QUẢN LÝ XUẤT NHẬP CẢNH"
    return value
