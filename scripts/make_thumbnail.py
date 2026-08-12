"""씬 이미지에 팝 스타일 텍스트를 얹어 유튜브 썸네일을 만든다 (조선 컨셉).

레이아웃 (사용자 승인 시안): 하단 어두운 그라데이션 위에
  - 흰색 서브 문구 (남색 외곽선) — 상황 훅 ("전국 1등 유생의 조선 로파이")
  - 노란 메인 문구 (검정 두꺼운 외곽선 + 그림자) — 핵심 어그로 ("과거시험 D-1")
폰트는 저장소에 커밋된 검은고딕(Black Han Sans, OFL 라이선스)을 쓴다.
유튜브 썸네일 규격: 1280x720, 2MB 이하.
"""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

THUMB_SIZE = (1280, 720)
MAX_BYTES = 2 * 1024 * 1024

REPO_ROOT = Path(__file__).resolve().parents[1]
FONT_CANDIDATES = [
    str(REPO_ROOT / "fonts" / "BlackHanSans-Regular.ttf"),
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # 한글 미지원 최후 폴백
]


def find_font(font_path=None):
    for path in ([font_path] if font_path else []) + FONT_CANDIDATES:
        if path and Path(path).exists():
            return path
    raise RuntimeError("사용 가능한 폰트를 찾지 못했습니다 (fonts/BlackHanSans-Regular.ttf 확인)")


def _cover_crop(image, size):
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _fit_font(draw, text, font_path, max_width, start_size, min_size=60, stroke_width=0):
    """텍스트가 max_width 안에 들어가는 최대 폰트 크기를 찾는다 (긴 문구 방어)."""
    size = start_size
    while size > min_size:
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 8
    return ImageFont.truetype(font_path, min_size)


def create_thumbnail(image_path, main_text, sub_text, output_path, font_path=None, size=THUMB_SIZE):
    font_file = find_font(font_path)

    base = Image.open(image_path).convert("RGB")
    thumb = _cover_crop(base, size).convert("RGBA")

    ov = Image.new("RGBA", thumb.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    # 하단 가독성용 어두운 그라데이션
    for y in range(size[1]):
        t = max(0.0, (y - size[1] * 0.42) / (size[1] * 0.58))
        d.line([(0, y), (size[0], y)], fill=(5, 5, 12, int(135 * t ** 1.6)))

    x = 48
    max_text_w = size[0] - x * 2

    if sub_text:
        f_sub = _fit_font(d, sub_text, font_file, max_text_w, start_size=68, stroke_width=10)
        y = size[1] - 292
        d.text((x + 4, y + 5), sub_text, font=f_sub, fill=(0, 0, 0, 160),
               stroke_width=10, stroke_fill=(0, 0, 0, 160))
        d.text((x, y), sub_text, font=f_sub, fill=(255, 255, 255, 255),
               stroke_width=10, stroke_fill=(20, 28, 60, 255))

    if main_text:
        f_main = _fit_font(d, main_text, font_file, max_text_w, start_size=170, stroke_width=16)
        y2 = size[1] - 208
        # 부드러운 그림자 -> 노랑 본문 + 검정 외곽선 순서로 겹쳐 어떤 배경에서도 읽히게 한다
        sh = Image.new("RGBA", thumb.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        sd.text((x + 8, y2 + 10), main_text, font=f_main, fill=(0, 0, 0, 200),
                stroke_width=16, stroke_fill=(0, 0, 0, 200))
        ov.alpha_composite(sh.filter(ImageFilter.GaussianBlur(7)))
        d.text((x, y2), main_text, font=f_main, fill=(255, 221, 0, 255),
               stroke_width=16, stroke_fill=(8, 8, 8, 255))

    composed = Image.alpha_composite(thumb, ov).convert("RGB")

    output_path = Path(output_path)
    for quality in (92, 85, 78, 70):
        composed.save(output_path, format="JPEG", quality=quality)
        if output_path.stat().st_size <= MAX_BYTES:
            break
    return output_path


def main():
    parser = argparse.ArgumentParser(description="씬 이미지 + 문구로 유튜브 썸네일을 만든다")
    parser.add_argument("--image", required=True)
    parser.add_argument("--main", required=True, help="노란 메인 문구 (예: 과거시험 D-1)")
    parser.add_argument("--sub", default="", help="흰 서브 문구 (예: 전국 1등 유생의 조선 로파이)")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    out = create_thumbnail(args.image, args.main, args.sub, args.output)
    print(f"thumbnail -> {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
