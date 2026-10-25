"""CCCD OCR - trích xuất thông tin Căn cước công dân Việt Nam từ ảnh.

Pipeline:
    ảnh  ->  tiền xử lý (nắn phối cảnh, phóng to, tăng tương phản)
         ->  OCR (Tesseract ``vie``; dự phòng RapidOCR/PP-OCRv5)
         ->  ghép dòng theo hình học  ->  bóc tách nhãn (parser)
         ->  MRZ mặt sau + đối chiếu cấu trúc số CCCD
         ->  Excel / CSV (Google Sheets)
"""

from .models import CardRecord, TextItem
from .ocr_engine import OcrEngine, OcrOptions
from .parser import parse_card

__version__ = "1.0.0"
__all__ = ["CardRecord", "OcrEngine", "OcrOptions", "TextItem", "__version__", "parse_card"]
