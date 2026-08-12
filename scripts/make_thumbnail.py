"""씬 이미지에 붓글씨 스타일의 큰 한글 문구 + 낙관(도장)을 얹어 썸네일을 만든다.

채널 정체성의 핵심: 모든 썸네일이 같은 구성(좌측 큰 붓글씨 + 우하단 붉은 낙관)을
유지해 피드에서 한 채널임을 즉시 알아볼 수 있게 한다. 문구/장면만 매일 바뀐다.

폰트는 CI 러너에 apt로 설치되는 나눔손글씨(NanumBrush)를 1순위로 쓰고, 없으면
한글을 지원하는 다른 폰트로 폴백한다. 유튜브 썸네일 규격: 1280x720, 2MB 이하.
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

THUMB_SIZE = (1280, 720)
MAX_BYTES = 2 * 1024 * 1024  # 유튜브 썸네일 용량 제한

# 앞에 있는 것부터 시도. NanumBrush가 붓글씨 느낌이라 1순위.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumBrush.ttf",
    "/usr/share/fonts/truetype/nanum/NanumPen.ttf",
    "/usr/share/fonts/truetype/nanum/NanumMyeongjoBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # 한글 미지원 최후 폴백
]


def find_font(font_path=None):
    candidates = ([font_path] if font_path else []) + FONT_CANDIDATES
    for path in candidates:
        if path and Path(path).exists():
            return path
    raise RuntimeError("사용 가능한 폰트를 찾지 못했습니다 (fonts-nanum 설치 필요)")


def _cover_crop(image, size):
    """비율을 유지하며 size를 꽉 채우도록 스케일 후 중앙 크롭."""
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _fit_font(draw, lines, font_path, max_width, start_size=200, min_size=60):
    """가장 긴 줄이 max_width 안에 들어가는 최대 폰트 크기를 찾는다."""
    size = start_size
    while size > min_size:
        font = ImageFont.truetype(font_path, size)
        widest = max(draw.textbbox((0, 0), line, font=font)[2] for line in lines)
        if widest <= max_width:
            return font
        size -= 10
    return ImageFont.truetype(font_path, min_size)


def _draw_stamp(image, draw, stamp_text, font_path):
    """우하단 붉은 낙관(도장). 글자는 세로로 쌓는다 (전통 낙관 느낌)."""
    margin = 36
    chars = list(stamp_text.strip())
    if not chars:
        return
    char_size = 52
    pad = 18
    font = ImageFont.truetype(font_path, char_size)
    box_w = char_size + pad * 2
    box_h = char_size * len(chars) + pad * 2 + 6 * (len(chars) - 1)
    x1 = image.width - margin - box_w
    y1 = image.height - margin - box_h
    draw.rounded_rectangle(
        [x1, y1, x1 + box_w, y1 + box_h], radius=10,
        fill=(190, 42, 34, 235), outline=(120, 20, 16, 255), width=3,
    )
    y = y1 + pad
    for ch in chars:
        bbox = draw.textbbox((0, 0), ch, font=font)
        w = bbox[2] - bbox[0]
        draw.text((x1 + (box_w - w) / 2 - bbox[0], y - bbox[1]), ch, font=font, fill=(255, 248, 240, 255))
        y += char_size + 6


def create_thumbnail(image_path, text, output_path, stamp_text=None, font_path=None, size=THUMB_SIZE):
    font_file = find_font(font_path)

    base = Image.open(image_path).convert("RGB")
    thumb = _cover_crop(base, size).convert("RGBA")

    overlay = Image.new("RGBA", thumb.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 좌측 어두운 그라데이션 (문구 가독성 확보). 왼쪽 55% 영역에서 서서히 사라짐.
    gradient_w = int(thumb.width * 0.55)
    for x in range(gradient_w):
        alpha = int(150 * (1 - x / gradient_w))
        draw.line([(x, 0), (x, thumb.height)], fill=(10, 8, 6, alpha))

    if text:
        lines = [ln for ln in str(text).split("\n") if ln.strip()]
        max_text_w = int(thumb.width * 0.52)
        font = _fit_font(draw, lines, font_file, max_text_w)
        line_heights = []
        for ln in lines:
            bbox = draw.textbbox((0, 0), ln, font=font, stroke_width=8)
            line_heights.append(bbox[3] - bbox[1])
        gap = int(font.size * 0.18)
        total_h = sum(line_heights) + gap * (len(lines) - 1)
        y = (thumb.height - total_h) // 2
        x = 56
        for ln, lh in zip(lines, line_heights):
            bbox = draw.textbbox((0, 0), ln, font=font, stroke_width=8)
            # 그림자 -> 외곽선 -> 본문 순서로 겹쳐 그려 어떤 배경에서도 읽히게 한다.
            draw.text((x + 5 - bbox[0], y + 5 - bbox[1]), ln, font=font, fill=(0, 0, 0, 160))
            draw.text((x - bbox[0], y - bbox[1]), ln, font=font, fill=(255, 250, 240, 255),
                      stroke_width=8, stroke_fill=(25, 18, 12, 255))
            y += lh + gap

    if stamp_text:
        _draw_stamp(thumb, draw, stamp_text, font_file)

    composed = Image.alpha_composite(thumb, overlay).convert("RGB")
    composed = composed.filter(ImageFilter.SHARPEN)

    # 2MB 제한을 넘지 않을 때까지 품질을 낮춰가며 저장.
    output_path = Path(output_path)
    for quality in (92, 85, 78, 70):
        composed.save(output_path, format="JPEG", quality=quality)
        if output_path.stat().st_size <= MAX_BYTES:
            break
    return output_path


def main():
    parser = argparse.ArgumentParser(description="씬 이미지 + 문구로 유튜브 썸네일을 만든다")
    parser.add_argument("--image", required=True)
    parser.add_argument("--text", required=True, help="썸네일 문구 (줄바꿈은 \\n)")
    parser.add_argument("--stamp", default=None, help="우하단 낙관 글자 (예: 노아)")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    out = create_thumbnail(args.image, args.text.replace("\\n", "\n"), args.output, stamp_text=args.stamp)
    print(f"thumbnail -> {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
