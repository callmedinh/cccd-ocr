"""Backend dự phòng: RapidOCR / PP-OCRv5 (ONNX Runtime).

Ưu điểm: không cần cài phần mềm ngoài, chạy được mọi nơi có ``onnxruntime``.
Nhược điểm: bộ ``latin`` có từ điển **ASCII**, không sinh được dấu tiếng Việt
(``ộ ệ ữ``), nên chỉ nên dùng khi máy không cài được Tesseract. Số CCCD, ngày
tháng và MRZ vẫn đọc tốt vì không có dấu.
"""

from __future__ import annotations

import logging

import numpy as np

from .models import TextItem

_LOGGER = logging.getLogger(__name__)


class RapidOcrBackend:
    def __init__(self, *, model_type: str = "mobile", ocr_version: str = "PP-OCRv5") -> None:
        self.model_type = model_type
        self.ocr_version = ocr_version
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            from rapidocr import RapidOCR
            from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

            versions = {"PP-OCRv4": OCRVersion.PPOCRV4,
                        "PP-OCRv5": OCRVersion.PPOCRV5,
                        "PP-OCRv6": OCRVersion.PPOCRV6}
            models = {"mobile": ModelType.MOBILE, "server": ModelType.SERVER}
            logging.getLogger("RapidOCR").setLevel(logging.WARNING)
            self._engine = RapidOCR(params={
                "Global.log_level": "warning",
                "Global.text_score": 0.3,
                "Rec.lang_type": LangRec.LATIN,
                "Rec.ocr_version": versions.get(self.ocr_version, OCRVersion.PPOCRV5),
                "Rec.model_type": models.get(self.model_type, ModelType.MOBILE),
            })
        return self._engine

    def read_image(self, image: np.ndarray) -> list[TextItem]:
        result = self.engine(image)
        if result is None:
            return []
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None) or ()
        scores = getattr(result, "scores", None) or ()
        items: list[TextItem] = []
        for index, text in enumerate(texts):
            if not text or not str(text).strip():
                continue
            points = [[0.0, 0.0]] * 4
            if boxes is not None and index < len(boxes):
                points = [[float(p[0]), float(p[1])]
                          for p in np.asarray(boxes[index]).reshape(-1, 2)]
            score = float(scores[index]) if index < len(scores) else 0.0
            items.append(TextItem(text=str(text).strip(), score=score, box=points))
        return items
