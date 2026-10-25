"""Giao diện dòng lệnh: quét ảnh CCCD -> file Excel/CSV."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .aggregate import best_mrz, pick_best, reconcile
from .console import ensure_utf8_console
from .excel_writer import write_csv, write_excel
from .models import CardRecord
from .ocr_engine import OcrEngine, OcrOptions
from .parser import parse_card

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

_LOGGER = logging.getLogger("cccd_ocr")


def collect_images(inputs: list[str]) -> list[Path]:
    """Nhận file ảnh hoặc thư mục (quét đệ quy), bỏ trùng, giữ thứ tự."""
    found: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES))
        elif path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            found.append(path)
        elif path.is_file():
            raise SystemExit(f"Không phải ảnh: {path}")
        else:
            raise SystemExit(f"Không tìm thấy: {path}")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def group_records(records: list[CardRecord], *, merge: bool = True) -> list[CardRecord]:
    """Gộp mặt trước và mặt sau thành một dòng cho mỗi công dân.

    Ba lượt ghép, theo thứ tự chắc chắn giảm dần:

    1. **Theo số CCCD** — chắc chắn nhất, chỉ cần một trong hai mặt đọc được số.
    2. **Theo thứ tự trong nhóm chưa có số** — mặt trước thứ *i* ghép với mặt sau
       thứ *i* (người dùng đã kéo ảnh vào đúng cột).
    3. **Bù mặt còn thiếu cho nhóm đã có số** — chỉ làm khi số mặt trước và số mặt
       sau còn lại bằng nhau, vì ghép nhầm người này sang người khác còn tệ hơn
       là để riêng.

    ``merge=False`` giữ mỗi ảnh thành một dòng riêng (người dùng tắt ghép cặp).
    """
    if not merge:
        for record in records:
            _final_check(record)
        return list(records)

    groups: dict[str, CardRecord] = {}
    order: list[str] = []
    loose: list[CardRecord] = []
    for record in records:
        if not record.id_number:
            loose.append(record)
            continue
        existing = groups.get(record.id_number)
        if existing is None:
            groups[record.id_number] = record
            order.append(record.id_number)
        else:
            existing.merge_from(record)

    identified = [groups[key] for key in order]
    remaining_fronts = [r for r in loose if r.side == "front"]
    remaining_backs = [r for r in loose if r.side == "back"]
    others = [r for r in loose if r.side not in ("front", "back")]

    # (2) ghép trong nhóm chưa có số CCCD
    leftovers: list[CardRecord] = []
    paired = min(len(remaining_fronts), len(remaining_backs))
    for index in range(paired):
        leftovers.append(_merge_pair(remaining_fronts[index], remaining_backs[index]))
    remaining_fronts = remaining_fronts[paired:]
    remaining_backs = remaining_backs[paired:]

    # (3) bù mặt còn thiếu cho nhóm đã có số CCCD — chỉ khi 1:1
    remaining_backs = _attach(identified, remaining_backs, side="back")
    remaining_fronts = _attach(identified, remaining_fronts, side="front")

    merged = identified + leftovers + remaining_fronts + remaining_backs + others
    for record in merged:
        _final_check(record)
    return merged


def _has_side(record: CardRecord, side: str) -> bool:
    """Bản ghi đã có thông tin của mặt này chưa (dựa vào trường đặc trưng)."""
    if record.side == side:
        return True
    if side == "front":
        return bool(record.full_name or record.place_of_residence)
    return bool(record.date_of_issue or record.personal_identification)


def _attach(identified: list[CardRecord], pool: list[CardRecord], *,
            side: str) -> list[CardRecord]:
    """Ghép các bản ghi lẻ vào nhóm đã có số CCCD còn thiếu mặt ``side``.

    Chỉ ghép khi hai bên cùng số lượng — lúc đó thứ tự là manh mối duy nhất và
    cũng là manh mối đáng tin (người dùng kéo ảnh vào cùng một lượt).
    """
    if not pool:
        return pool
    targets = [record for record in identified if not _has_side(record, side)]
    if len(targets) != len(pool):
        return pool  # không cân -> để riêng, không đoán bừa
    for target, extra in zip(targets, pool, strict=False):
        target.merge_from(extra)
    return []


def _merge_pair(first: CardRecord, second: CardRecord) -> CardRecord:
    """Gộp hai bản ghi cùng một người, giữ bản đầy đủ hơn làm gốc."""
    base = first if _filled(first) >= _filled(second) else second
    base.merge_from(second if base is first else first)
    return base


def _filled(record: CardRecord) -> int:
    return sum(1 for name in CardRecord.field_names() if getattr(record, name))


def _final_check(record: CardRecord) -> None:
    """Đối chiếu lần cuối sau khi đã gộp: số CCCD vs ngày sinh / giới tính."""
    from . import idcheck

    for warning in idcheck.cross_check(record.id_number, record.date_of_birth,
                                       record.sex):
        if warning not in record.warnings:
            record.warnings.append(warning)


def scan_image(engine: OcrEngine, image_path: Path) -> CardRecord:
    """Quét một ảnh: OCR nhiều biến thể -> bỏ phiếu -> đối chiếu MRZ."""
    variant_ocrs = engine.read_variants(str(image_path))
    parses = [parse_card(v.items, source=image_path.name) for v in variant_ocrs if v.items]
    if not parses:
        return CardRecord(source_files=[image_path.name],
                          warnings=["Không đọc được chữ nào trong ảnh"])

    record = pick_best([p.record for p in parses])
    mrz_results = [p.mrz for p in parses]

    # Ảnh có dấu hiệu MRZ -> chạy thêm một lượt OCR chuyên cho dải MRZ.
    # Lượt này dùng whitelist ký tự + psm 6 nên đọc số/ngày chính xác hơn hẳn.
    if any(not mrz.is_empty() for mrz in mrz_results):
        mrz_results.append(engine.read_mrz_pass(str(image_path)))

    return reconcile(record, best_mrz(mrz_results))


def scan(inputs: list[str], options: OcrOptions, *,
         on_progress=None) -> list[CardRecord]:
    """Chạy toàn bộ pipeline (OCR + bóc tách + gộp mặt trước/sau)."""
    images = collect_images(inputs)
    if not images:
        raise SystemExit("Không có ảnh nào để xử lý.")

    engine = OcrEngine(options)
    _ = engine.backend  # nạp/kiểm tra backend trước để báo lỗi sớm, trước khi OCR

    records: list[CardRecord] = []
    for index, image_path in enumerate(images, start=1):
        started = time.perf_counter()
        record = scan_image(engine, image_path)
        elapsed = time.perf_counter() - started
        if on_progress:
            on_progress(index, len(images), image_path, record, elapsed)
        records.append(record)
    return group_records(records)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cccd-ocr",
        description="Quét ảnh Căn cước công dân và điền thông tin vào file Excel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Ví dụ:
  python main.py anh/ -o ket_qua.xlsx --csv
  python main.py mat_truoc.jpg mat_sau.jpg
  python main.py anh/ --thorough --model server
  python main.py anh/ --backend rapidocr      # khi máy chưa cài Tesseract
""",
    )
    parser.add_argument("inputs", nargs="+", help="Ảnh hoặc thư mục chứa ảnh CCCD")
    parser.add_argument("-o", "--output", default="output/cccd.xlsx",
                        help="File Excel đầu ra (mặc định: output/cccd.xlsx)")
    parser.add_argument("--csv", action="store_true",
                        help="Xuất thêm CSV cùng tên (mở trực tiếp bằng Google Sheets)")
    parser.add_argument("--backend", default="auto", choices=["auto", "tesseract", "rapidocr"],
                        help="auto: Tesseract nếu có, không thì RapidOCR (mặc định)")
    parser.add_argument("--lang", default="vie",
                        help="Ngôn ngữ Tesseract (mặc định 'vie')")
    parser.add_argument("--psm", type=int, default=3,
                        choices=[3, 4, 6, 11, 12],
                        help="Chế độ phân trang Tesseract (mặc định 3 = tự động)")
    parser.add_argument("--thorough", action="store_true",
                        help="Đọc cả ảnh gốc, ảnh xám và lượt psm 6 (chậm ~3x, chính xác hơn)")
    parser.add_argument("--fast", action="store_true",
                        help="Chỉ OCR 1 biến thể ảnh, 1 lượt (nhanh ~4x)")
    parser.add_argument("--model", default="mobile", choices=["mobile", "server"],
                        help="Cỡ model cho RapidOCR: mobile (nhanh) / server (chính xác)")
    parser.add_argument("--append", action="store_true",
                        help="Ghi nối vào file Excel đã có thay vì tạo mới")
    parser.add_argument("--json", metavar="PATH", help="Xuất thêm JSON chi tiết (debug)")
    parser.add_argument("-q", "--quiet", action="store_true", help="Không in tiến độ")
    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_console()
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    options = OcrOptions(
        backend=args.backend,
        lang=args.lang,
        psm=args.psm,
        model_type=args.model,
        thorough=args.thorough,
        variants=not args.fast,
    )

    def progress(index: int, total: int, path: Path, record: CardRecord, elapsed: float) -> None:
        if args.quiet:
            return
        status = record.id_number or "???"
        name = record.full_name or "(không đọc được tên)"
        flag = "!" if record.warnings else "v"
        print(f"[{index}/{total}] {flag} {path.name}  {status}  {name}  "
              f"({elapsed:.1f}s)", flush=True)

    records = scan(args.inputs, options, on_progress=progress)

    output = Path(args.output)
    write_excel(records, output, append=args.append)
    written = [output]
    if args.csv:
        csv_path = output.with_suffix(".csv")
        write_csv(records, csv_path)
        written.append(csv_path)

    if args.json:
        payload = [{"record": r.as_dict()} for r in records]
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written.append(Path(args.json))

    if not args.quiet:
        warnings_total = sum(len(r.warnings) for r in records)
        missing = [r for r in records if not r.id_number or not r.full_name]
        print(f"\nĐã xử lý {len(records)} công dân.")
        if missing:
            print(f"{len(missing)} dòng còn thiếu thông tin quan trọng — xem cột 'Cảnh báo'.")
        if warnings_total:
            print(f"Có {warnings_total} cảnh báo cần kiểm tra.")
        for path in written:
            print(f"-> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
