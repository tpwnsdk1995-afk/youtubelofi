"""정적 이미지 1장 + 오디오를 합쳐 오디오 길이만큼 반복되는 영상을 만든다.
애니메이션 없이 이미지를 그대로 반복하므로 긴 영상(1~4시간)에서도 인코딩이 빠르다.
"""

import argparse
import subprocess

import yaml


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_video(image_path, audio_path, output_path, resolution, fps, crf, preset, audio_bitrate):
    width, height = resolution.split("x")
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
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
    args = parser.parse_args()

    settings = load_yaml(args.settings_config)
    v = settings["video"]
    a = settings["audio"]

    build_video(
        args.image,
        args.audio,
        args.output,
        v["resolution"],
        v["fps"],
        v["crf"],
        v["preset"],
        a["bitrate"],
    )
    print(f"built video -> {args.output}")


if __name__ == "__main__":
    main()
