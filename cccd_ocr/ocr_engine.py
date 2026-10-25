"""Tầng OCR: chọn backend, chạy trên nhiều biến thể ảnh.

Kiến trúc: mỗi backend chỉ cần cung cấp ``read_image(numpy BGR) -> list[TextItem]``
(ô text theo từng từ, kèm toạ độ). Mọi thứ còn lại — ghép dòng, bóc tách
trường, đọc MRZ, bỏ phiếu giữa các biến thể — đều dùng chung.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .backends.tesseract_backend import (
    TesseractEngine,
    TesseractNotFound,
    TesseractOptions,
)
from .models import TextItem
from .mrz import MrzResult, extract_mrz

_LOGGER = logging.getLogger(__name__)

BACKENDS = ("auto", "tesseract", "rapidocr")


@dataclass
class OcrOptions:
    """Tuỳ chọn OCR dùng chung."""

    backend: str = "auto"        # auto | tesseract | rapidocr
    lang: str = "vie"            # 'vie' đọc dấu tốt nhất; 'vie+eng' thiên về ASCII
    psm: int = 3
    model_type: str = "mobile"   # rapidocr: mobile | server
    ocr_version: str = "PP-OCRv5"
    variants: bool = True        # OCR nhiều biến thể ảnh rồi bỏ phiếu
    thorough: bool = False       # chạy thêm lượt psm 6 (chậm hơn)


@dataclass
class VariantOcr:
    """Kết quả OCR của một biến thể ảnh."""

    name: str
    items: list[TextItem] = field(default_factory=list)


class OcrEngine:
    """Cửa ngõ duy nhất tới OCR."""

    def __init__(self, options: OcrOptions | None = None) -> None:
        self.options = options or OcrOptions()
        self._backend = None
        self.backend_name = ""

    # ------------------------------------------------------------- backend
    @property
    def backend(self):
        if self._backend is None:
            self._backend = self._build_backend()
        return self._backend

    def _build_backend(self):
        choice = self.options.backend
        if choice in ("auto", "tesseract"):
            try:
                engine = TesseractEngine(TesseractOptions(lang=self.options.lang,
                                                          psm=self.options.psm))
                engine.version()  # xác nhận binary chạy được
                self.backend_name = "tesseract"
                _LOGGER.info("Dùng Tesseract: %s", engine.version())
                return engine
            except (TesseractNotFound, RuntimeError, OSError) as error:
                if choice == "tesseract":
                    raise
                _LOGGER.warning(
                    "Tesseract không dùng được (%s) -> chuyển sang RapidOCR "
                    "(bộ này KHÔNG đọc được dấu tiếng Việt)", error)
        self.backend_name = "rapidocr"
        from .ocr_rapid import RapidOcrBackend

        return RapidOcrBackend(model_type=self.options.model_type,
                               ocr_version=self.options.ocr_version)

    def describe(self) -> str:
        return f"{self.backend_name} (lang={self.options.lang}, psm={self.options.psm})"

    # --------------------------------------------------------------- read
    def read_image(self, image: np.ndarray) -> list[TextItem]:
        return self.backend.read_image(image)

    def read_variants(self, path: str, *, variants: bool | None = None) -> list[VariantOcr]:
        """OCR lần lượt các biến thể ảnh của cùng một tấm thẻ."""
        from .preprocess import build_variants, load_image

        use_variants = self.options.variants if variants is None else variants
        image = load_image(path)
        if not use_variants:
            return [VariantOcr(name="original", items=self.read_image(image))]

        results: list[VariantOcr] = []
        for name, variant in build_variants(image):
            results.append(VariantOcr(name=name, items=self.read_image(variant)))
        if self.options.thorough:
            # Thêm lượt psm 6 trên ảnh xám: tốt hơn cho khối chữ nhỏ, dày
            for name, variant in build_variants(image):
                if "gray" not in name:
                    continue
                results.append(VariantOcr(
                    name=f"{name}_psm6",
                    items=self.read_image_psm(variant, 6),
                ))
        return results

    def read_image_psm(self, image: np.ndarray, psm: int) -> list[TextItem]:
        read = self.backend.read_image
        try:
            return read(image, psm=psm)
        except TypeError:
            return read(image)

    def read_mrz_pass(self, path: str) -> MrzResult:
        """Lượt OCR chuyên cho vùng MRZ (whitelist ký tự, psm 6, tiếng Anh)."""
        from .preprocess import build_variants, load_image

        try:
            image = load_image(path)
            read_mrz = getattr(self.backend, "read_mrz", None)
            if callable(read_mrz):
                found = extract_mrz(read_mrz(image))
                if not found.is_empty():
                    return found
            for _name, variant in build_variants(image):
                height = variant.shape[0]
                found = extract_mrz(self.read_image(variant[int(height * 0.62):]))
                if not found.is_empty():
                    return found
        except Exception as error:
            _LOGGER.debug("Lượt MRZ thất bại: %s", error)
        return MrzResult()
