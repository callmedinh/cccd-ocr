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


def group_records(records: list[CardRecord]) -> list[CardRecord]:
    """Gộp mặt trước và mặt sau thành một dòng cho mỗi công dân.

    Có hai lượt ghép:

    1. **Theo số CCCD** — chắc chắn nhất, chỉ cần một trong hai mặt đọc được số.
    2. **Theo thứ tự** — dành cho các ảnh không đọc ra số: mặt trước thứ *i* ghép
       với mặt sau thứ *i*. Nhờ vậy người dùng chỉ cần kéo ảnh vào đúng cột
       "Mặt trước" / "Mặt sau" là ghép được cặp, không phải đặt tên tệp.
    """
    by_id: dict[str, CardRecord] = {}
    loose: list[CardRecord] = []
    for record in records:
        if not record.id_number:
            loose.append(record)
            continue
        existing = by_id.get(record.id_number)
        if existing is None:
            by_id[record.id_number] = record
        else:
            existing.merge_from(record)

    merged = list(by_id.values())
    merged.extend(_pair_by_order(loose))
    for record in merged:
        _final_check(record)
    return merged


def _pair_by_order(records: list[CardRecord]) -> list[CardRecord]:
    """Ghép các bản ghi chưa có số CCCD: mặt trước *i* ↔ mặt sau *i*."""
    fronts = [r for r in records if r.side == "front"]
    backs = [r for r in records if r.side == "back"]
    others = [r for r in records if r.side not in ("front", "back")]

    result: list[CardRecord] = []
    paired = 0
    for front, back in zip(fronts, backs, strict=False):
        base = front if _filled(front) >= _filled(back) else back
        base.merge_from(back if base is front else front)
        result.append(base)
        paired += 1

    result.extend(fronts[paired:])
    result.extend(backs[paired:])
    result.extend(others)
    return result


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
