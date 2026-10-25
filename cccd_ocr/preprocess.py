"""Tiền xử lý ảnh: nắn phối cảnh, phóng to, tăng tương phản.

Ảnh CCCD chụp bằng điện thoại thường bị: nghiêng, lệch phối cảnh, thiếu sáng,
lóa đèn flash. Ta tạo vài *biến thể* của cùng một ảnh rồi cho OCR đọc hết,
sau đó gộp kết quả — cách này bù được việc một biến thể đọc hỏng chỗ này
nhưng biến thể khác lại đọc tốt chỗ đó.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageOps

# Chiều rộng tối thiểu trước khi OCR. Tesseract đọc dấu tiếng Việt tốt hơn hẳn
# khi ảnh thẻ được phóng lên cỡ này (dấu mũ + dấu thanh chồng nhau rất nhỏ).
MIN_WIDTH = 2400
MAX_WIDTH = 3600


def load_image(path: str) -> np.ndarray:
    """Đọc ảnh an toàn với đường dẫn Unicode (PIL), trả về BGR như OpenCV."""
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)  # xoay theo EXIF của điện thoại
        rgb = img.convert("RGB")
        return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)


def resize_for_ocr(image: np.ndarray) -> np.ndarray:
    """Phóng to/thu nhỏ ảnh về khoảng chiều rộng phù hợp cho OCR."""
    h, w = image.shape[:2]
    if w < MIN_WIDTH:
        scale = min(MAX_WIDTH / w, MIN_WIDTH / w)
    elif w > MAX_WIDTH:
        scale = MAX_WIDTH / w
    else:
        return image
    interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    return cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=interp)


def find_card_quad(image: np.ndarray) -> np.ndarray | None:
    """Tìm viền thẻ (tứ giác lớn nhất) để nắn phối cảnh."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    image_area = image.shape[0] * image.shape[1]
    best, best_area = None, 0.0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:8]:
        area = cv2.contourArea(contour)
        if area < image_area * 0.18:
            break
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) == 4 and area > best_area:
            best, best_area = approx.reshape(4, 2), area
    return best


def _order_points(points: np.ndarray) -> np.ndarray:
    """Sắp 4 điểm về thứ tự TL, TR, BR, BL."""
    points = points.astype("float32")
    ordered = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    ordered[0] = points[np.argmin(sums)]           # top-left
    ordered[2] = points[np.argmax(sums)]           # bottom-right
    diffs = np.diff(points, axis=1).ravel()
    ordered[1] = points[np.argmin(diffs)]          # top-right
    ordered[3] = points[np.argmax(diffs)]          # bottom-left
    return ordered


def warp_card(image: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Nắn phối cảnh tứ giác về hình chữ nhật tỉ lệ thẻ CCCD (85.6 x 54 mm)."""
    tl, tr, br, bl = _order_points(quad)
    width = max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))
    height = max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))
    if width < 200 or height < 120:
        return image
    ratio = 85.6 / 54.0
    if height > 0 and not (1.2 < (width / height) < 2.1):
        height = width / ratio  # ép lại tỉ lệ thẻ nếu tứ giác bị méo
    dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                   dtype="float32")
    matrix = cv2.getPerspectiveTransform(np.array([tl, tr, br, bl], dtype="float32"), dst)
    return cv2.warpPerspective(image, matrix, (int(width), int(height)),
                               flags=cv2.INTER_CUBIC)


def enhance_gray(image: np.ndarray) -> np.ndarray:
    """Xám + cân bằng tương phản cục bộ (CLAHE) + khử nhiễu nhẹ."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    enhanced = cv2.fastNlMeansDenoising(enhanced, None, 7, 7, 21)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def enhance_color(image: np.ndarray) -> np.ndarray:
    """Tăng tương phản trên ảnh màu (giữ thông tin màu của phôi thẻ)."""
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    l_ch = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l_ch)
    return cv2.cvtColor(cv2.merge([l_ch, a_ch, b_ch]), cv2.COLOR_LAB2BGR)


def build_variants(image: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Sinh danh sách (tên, ảnh) để OCR chạy lần lượt rồi bỏ phiếu.

    Chỉ 2 biến thể: một bản đã nắn phối cảnh/xám hoá (thường tốt nhất) và một
    bản giữ màu. Nhiều biến thể hơn không tăng độ chính xác đủ để bù thời gian.
    """
    working = resize_for_ocr(image)

    quad = find_card_quad(working)
    if quad is not None:
        warped = warp_card(working, quad)
        if warped is not working:
            base = resize_for_ocr(warped)
            return [("warped_gray", enhance_gray(base)),
                    ("color", enhance_color(base))]

    return [("gray", enhance_gray(working)),
            ("color", enhance_color(working))]
