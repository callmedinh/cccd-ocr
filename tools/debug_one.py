"""Công cụ debug: in text OCR thô + kết quả bóc tách (và so sánh các biến thể).

    python tools/debug_one.py <ảnh> [--fast] [--raw]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cccd_ocr.aggregate import best_mrz, pick_best, reconcile, score_record
from cccd_ocr.console import ensure_utf8_console
from cccd_ocr.ocr_engine import OcrEngine, OcrOptions
from cccd_ocr.parser import group_lines, parse_card


def main() -> int:
    ensure_utf8_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--backend", default="auto")
    parser.add_argument("--lang", default="vie")
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--raw", action="store_true", help="In cả từng ô text thô")
    args = parser.parse_args()

    engine = OcrEngine(OcrOptions(backend=args.backend, lang=args.lang,
                                  variants=not args.fast))
    variants = engine.read_variants(args.image, variants=not args.fast)
    print(f"== {Path(args.image).name} | backend={engine.describe()} ==")

    parses = []
    for variant in variants:
        result = parse_card(variant.items, source=Path(args.image).name)
        parses.append(result)
        print(f"\n-- biến thể '{variant.name}': {len(variant.items)} ô text, "
              f"score={score_record(result.record):.2f}")
        if args.raw:
            for item in variant.items:
                print(f"   [{item.score:.2f}] ({item.x_left:.0f},{item.y_top:.0f}) {item.text!r}")
        for index, line in enumerate(group_lines(variant.items)):
            print(f"  {index:2d} y={line.y_center:6.0f} x={line.x_left:5.0f} "
                  f"[{line.score:.2f}] {line.text}")

    record = pick_best([p.record for p in parses])
    mrz = best_mrz([p.mrz for p in parses])
    if not mrz.valid:
        mrz = best_mrz([mrz, engine.read_mrz_pass(args.image)])
    reconcile(record, mrz)

    print("\n== KẾT QUẢ CUỐI ==")
    for name in record.field_names():
        print(f"{name:26s} = {getattr(record, name)!r}")
    print(f"\nconfidence = {record.confidence:.3f}")
    print(f"MRZ: valid={mrz.valid} id={mrz.id_number} dob={mrz.date_of_birth} "
          f"sex={mrz.sex} exp={mrz.date_of_expiry} name={mrz.full_name}")
    for raw in mrz.raw_lines:
        print(f"     {raw}")
    for warning in record.warnings:
        print(f"  ! {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
