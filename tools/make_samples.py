"""Sinh ảnh CCCD mẫu (mặt trước + mặt sau) để kiểm thử pipeline.

Ảnh mẫu **không phải thẻ thật**: toàn bộ dữ liệu là hư cấu, chỉ nhằm kiểm tra
định dạng in, vị trí nhãn và MRZ. Số CCCD được sinh theo đúng cấu trúc (mã
tỉnh + mã giới tính/thế kỷ + 2 số cuối năm sinh) và MRZ có check digit hợp lệ.

    python tools/make_samples.py [--out samples] [--count 3]
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:          # cho phép chạy: python tools/make_samples.py
    sys.path.insert(0, str(ROOT))

from cccd_ocr.console import ensure_utf8_console
from cccd_ocr.mrz import check_digit

FONT_DIR = Path(r"C:\Windows\Fonts")
WIDTH, HEIGHT = 1400, 882   # ~85.6 x 54 mm ở ~415 dpi


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    candidates = [FONT_DIR / name, FONT_DIR / "arial.ttf"]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


@dataclass
class SampleCard:
    id_number: str
    full_name: str
    dob: str            # dd/mm/yyyy
    sex: str
    origin: str
    residence: str
    expiry: str
    issue_date: str
    features: str


SAMPLES = [
    SampleCard(
        id_number="001095012345",
        full_name="NGUYỄN VĂN AN",
        dob="15/05/1995",
        sex="Nam",
        origin="Xã Kim Liên, Huyện Nam Đàn, Tỉnh Nghệ An",
        residence="Số 12, Ngõ 45, Phố Huế, Phường Hàng Bài, Quận Hoàn Kiếm, Thành phố Hà Nội",
        expiry="15/05/2035",
        issue_date="20/06/2021",
        features="Sẹo mờ 1cm trên trán",
    ),
    SampleCard(
        id_number="079600123456",
        full_name="TRẦN THỊ BÍCH NGỌC",
        dob="20/11/2000",
        sex="Nữ",
        origin="Phường Bến Thành, Quận 1, Thành phố Hồ Chí Minh",
        residence=(
            "Căn hộ B2-1504, Chung cư Vinhomes Central Park, Phường 22, "
            "Quận Bình Thạnh, Thành phố Hồ Chí Minh"
        ),
        expiry="20/11/2040",
        issue_date="05/03/2022",
        features="Không có",
    ),
    SampleCard(
        id_number="048088765432",
        full_name="LÊ QUANG HUY",
        dob="02/01/1988",
        sex="Nam",
        origin="Xã Hòa Tiến, Huyện Hòa Vang, Thành phố Đà Nẵng",
        residence=(
            "Số 88, Đường Nguyễn Văn Linh, Phường Nam Dương, Quận Hải Châu, "
            "Thành phố Đà Nẵng"
        ),
        expiry="02/01/2028",
        issue_date="12/09/2023",
        features="Nốt ruồi dưới mắt trái",
    ),
]


# --------------------------------------------------------------------- MRZ

def _mrz_pad(text: str, length: int = 30) -> str:
    return text.ljust(length, "<")[:length]


def _birth_mrz(dob: str) -> str:
    day, month, year = dob.split("/")
    return f"{year[2:]}{month}{day}"


def build_mrz(card: SampleCard) -> list[str]:
    """Sinh 3 dòng MRZ TD1 với check digit hợp lệ."""
    row1 = _mrz_pad(f"IDVNM{card.id_number}")
    dob = _birth_mrz(card.dob)
    exp = _birth_mrz(card.expiry)
    sex = "M" if card.sex == "Nam" else "F"
    body = f"{dob}{check_digit(dob)}{sex}{exp}{check_digit(exp)}VNM"
    body += "<" * (29 - len(body))
    composite_source = (
        f"{card.id_number}{check_digit(card.id_number)}"
        f"{dob}{check_digit(dob)}{exp}{check_digit(exp)}"
    )
    row2 = _mrz_pad(f"{body}{check_digit(composite_source)}")
    name = "<<".join(card.full_name.split())
    row3 = _mrz_pad(name)
    return [row1, row2, row3]


# ------------------------------------------------------------- vẽ mặt trước

def draw_front(card: SampleCard) -> Image.Image:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (232, 240, 245))
    draw = ImageDraw.Draw(canvas)

    # nền chuyển sắc nhẹ + hoa văn mờ cho giống phôi thẻ thật
    for y in range(HEIGHT):
        shade = int(4 * (y / HEIGHT))
        draw.line([(0, y), (WIDTH, y)], fill=(228 - shade, 238 - shade, 246 - shade))
    rng = random.Random(card.id_number)
    for _ in range(120):
        x0, y0 = rng.randrange(WIDTH), rng.randrange(HEIGHT)
        r = rng.randrange(20, 90)
        draw.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], outline=(214, 226, 236), width=1)

    draw.rounded_rectangle([10, 10, WIDTH - 10, HEIGHT - 10], radius=26,
                           outline=(150, 170, 190), width=3)

    center = WIDTH // 2
    f_small, f_tiny = font("arialbd.ttf", 22), font("arial.ttf", 20)
    f_title, f_label = font("arialbd.ttf", 40), font("arial.ttf", 23)
    f_value, f_name = font("arial.ttf", 26), font("arialbd.ttf", 30)
    f_mono = font("consola.ttf", 22)

    centered(draw, "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", f_small, center, 34, (20, 20, 20))
    centered(draw, "Độc lập - Tự do - Hạnh phúc", f_tiny, center, 64, (40, 40, 40))
    centered(draw, "CĂN CƯỚC CÔNG DÂN", f_title, center, 108, (10, 40, 90))
    centered(draw, "CITIZEN IDENTITY CARD", f_tiny, center, 156, (70, 80, 95))

    left, right = 60, 900
    y = 200
    draw.text((left, y), "Số / No.:", font=f_label, fill=(60, 60, 60))
    draw.text((left + 150, y - 4), card.id_number, font=font("arialbd.ttf", 30),
              fill=(120, 20, 20))
    y += 58
    draw.text((left, y), "Họ và tên / Full name:", font=f_label, fill=(60, 60, 60))
    draw.text((left, y + 30), card.full_name, font=f_name, fill=(10, 10, 10))
    y += 88
    draw.text((left, y), "Ngày sinh / Date of birth:", font=f_label, fill=(60, 60, 60))
    draw.text((left + 330, y), card.dob, font=f_value, fill=(10, 10, 10))
    y += 46
    draw.text((left, y), "Giới tính / Sex:", font=f_label, fill=(60, 60, 60))
    draw.text((left + 210, y), card.sex, font=f_value, fill=(10, 10, 10))
    draw.text((left + 330, y), "Quốc tịch / Nationality:", font=f_label, fill=(60, 60, 60))
    draw.text((left + 640, y), "Việt Nam", font=f_value, fill=(10, 10, 10))
    y += 46
    draw.text((left, y), "Quê quán / Place of origin:", font=f_label, fill=(60, 60, 60))
    y += 30
    y = draw_wrapped(draw, card.origin, f_tiny, left + 20, y, right - left - 20)
    y += 12
    draw.text((left, y), "Nơi thường trú / Place of residence:", font=f_label,
              fill=(60, 60, 60))
    y += 30
    y = draw_wrapped(draw, card.residence, f_tiny, left + 20, y, right - left - 20)
    y += 16
    draw.text((left, y), "Có giá trị đến / Date of expiry:", font=f_label,
              fill=(60, 60, 60))
    draw.text((left + 400, y), card.expiry, font=f_value, fill=(10, 10, 10))

    # ảnh chân dung
    draw.rectangle([1040, 210, 1330, 600], fill=(200, 210, 220), outline=(120, 130, 140), width=2)
    centered(draw, "ẢNH", font("arial.ttf", 28), (1040 + 1330) // 2, 390, (140, 150, 160))

    # QR giả + dòng MRZ ngắn ở góc dưới
    draw.rectangle([1100, 620, 1270, 790], fill=(235, 235, 235), outline=(120, 120, 120))
    for i in range(14):
        for j in range(14):
            if rng.random() > 0.5:
                draw.rectangle([1108 + i * 11, 628 + j * 11, 1118 + i * 11, 638 + j * 11],
                               fill=(30, 30, 30))
    draw.rectangle([1060, 806, 1330, 846], outline=(120, 120, 120))
    draw.text((1000, 806), "idvn", font=f_mono, fill=(90, 90, 90))
    return canvas


# -------------------------------------------------------------- vẽ mặt sau

def draw_back(card: SampleCard) -> Image.Image:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (232, 240, 245))
    draw = ImageDraw.Draw(canvas)
    for y in range(HEIGHT):
        shade = int(4 * (y / HEIGHT))
        draw.line([(0, y), (WIDTH, y)], fill=(228 - shade, 238 - shade, 246 - shade))
    rng = random.Random(card.id_number + "b")
    for _ in range(90):
        x0, y0 = rng.randrange(WIDTH), rng.randrange(HEIGHT)
        r = rng.randrange(20, 90)
        draw.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], outline=(214, 226, 236), width=1)
    draw.rounded_rectangle([10, 10, WIDTH - 10, HEIGHT - 10], radius=26,
                           outline=(150, 170, 190), width=3)

    f_label = font("arial.ttf", 23)
    f_tiny = font("arial.ttf", 20)
    f_bold = font("arialbd.ttf", 22)
    f_mono = font("consola.ttf", 30)

    left = 70
    y = 70
    draw.text((left, y), "Đặc điểm nhận dạng / Personal identification:", font=f_label,
              fill=(60, 60, 60))
    y += 34
    y = draw_wrapped(draw, card.features, f_tiny, left + 20, y, 800)
    y += 40
    draw.text((left, y), "Ngày, tháng, năm cấp / Date of issue:", font=f_label,
              fill=(60, 60, 60))
    draw.text((left + 430, y), card.issue_date, font=font("arial.ttf", 24), fill=(10, 10, 10))
    y += 60
    draw_wrapped(
        draw,
        "CỤC TRƯỞNG CỤC CẢNH SÁT QUẢN LÝ HÀNH CHÍNH VỀ TRẬT TỰ XÃ HỘI",
        f_bold, left, y, 760,
    )

    # MRZ 3 dòng ở đáy
    mrz_top = HEIGHT - 210
    for index, line in enumerate(build_mrz(card)):
        draw.text((90, mrz_top + index * 46), line, font=f_mono, fill=(20, 20, 20))
    draw.line([(70, mrz_top - 14), (WIDTH - 70, mrz_top - 14)], fill=(160, 175, 190), width=1)
    return canvas


# ------------------------------------------------------------------ helpers

def centered(draw: ImageDraw.ImageDraw, text: str, fnt, center_x: int, y: int, fill) -> None:
    width = draw.textlength(text, font=fnt)
    draw.text((center_x - width / 2, y), text, font=fnt, fill=fill)


def draw_wrapped(draw: ImageDraw.ImageDraw, text: str, fnt, x: int, y: int,
                 max_width: float, line_height: int = 30) -> int:
    words, line = text.split(), ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if draw.textlength(candidate, font=fnt) <= max_width:
            line = candidate
        else:
            draw.text((x, y), line, font=fnt, fill=(20, 20, 20))
            y += line_height
            line = word
    if line:
        draw.text((x, y), line, font=fnt, fill=(20, 20, 20))
        y += line_height
    return y


def add_camera_effects(image: Image.Image, angle: float, seed: int) -> Image.Image:
    """Xoay nhẹ + nhiễu + nén JPEG: mô phỏng ảnh chụp điện thoại."""
    if angle:
        image = image.rotate(angle, resample=Image.BICUBIC, expand=True,
                             fillcolor=(245, 245, 245))
    array = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 4.0, array.shape).astype(np.int16)
    array = np.clip(array.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    ok, buffer = cv2.imencode(".jpg", array, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    if ok:
        array = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    return Image.fromarray(cv2.cvtColor(array, cv2.COLOR_BGR2RGB))


def main() -> None:
    ensure_utf8_console()
    parser = argparse.ArgumentParser(description="Sinh ảnh CCCD mẫu để kiểm thử")
    parser.add_argument("--out", default="samples", help="Thư mục đầu ra")
    parser.add_argument("--count", type=int, default=len(SAMPLES),
                        help="Số thẻ mẫu (tối đa 3)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    angles = [1.4, 0.0, -0.8]

    for index, card in enumerate(SAMPLES[: args.count]):
        front = add_camera_effects(draw_front(card), angles[index % len(angles)], index)
        back = add_camera_effects(draw_back(card), -angles[index % len(angles)], index + 7)
        front_path = out_dir / f"{index + 1:02d}_{card.id_number}_mat_truoc.jpg"
        back_path = out_dir / f"{index + 1:02d}_{card.id_number}_mat_sau.jpg"
        front.save(front_path, quality=88)
        back.save(back_path, quality=88)
        print(f"-> {front_path}")
        print(f"-> {back_path}")


if __name__ == "__main__":
    main()
