"""정적 이미지 1장 + 오디오를 합쳐 오디오 길이만큼 반복되는 영상을 만든다.
애니메이션 없이 이미지를 그대로 반복하므로 긴 영상(1~4시간)에서도 인코딩이 빠르다.
채널명 워터마크 텍스트를 우하단에 함께 새길 수 있다.
"""

import argparse
import subprocess

import yaml

DEFAULT_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _escape_drawtext(text):
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def build_video(image_path, audio_path, output_path, resolution, fps, crf, preset, audio_bitrate,
                 watermark_text=None, font_path=DEFAULT_FONT_PATH):
    width, height = resolution.split("x")
    vf_parts = [
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
    ]
    if watermark_text:
        escaped = _escape_drawtext(watermark_text)
        vf_parts.append(
            f"drawtext=fontfile={font_path}:text='{escaped}':fontcolor=white@0.85:fontsize=28:"
            "x=w-tw-30:y=h-th-30:shadowcolor=black@0.6:shadowx=2:shadowy=2"
        )
    vf = ",".join(vf_parts)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-shortest",
        "-r", str(fps),
        "-vf", vf,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-movflags", "+faststart",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="정적 이미지 + 오디오로 반복 영상을 만든다")
    parser.add_argument("--image", required=True)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--settings-config", default="config/settings.yml")
    parser.add_argument("--templates-config", default="config/title_templates.yml")
    parser.add_argument("--no-watermark", action="store_true")
    args = parser.parse_args()

    settings = load_yaml(args.settings_config)
    v = settings["video"]
    a = settings["audio"]

    watermark_text = None
    if not args.no_watermark:
        templates = load_yaml(args.templates_config)
        watermark_text = templates.get("channel_name")

    build_video(
        args.image,
        args.audio,
        args.output,
        v["resolution"],
        v["fps"],
        v["crf"],
        v["preset"],
        a["bitrate"],
        watermark_text=watermark_text,
    )
    print(f"built video -> {args.output}")


if __name__ == "__main__":
    main()
