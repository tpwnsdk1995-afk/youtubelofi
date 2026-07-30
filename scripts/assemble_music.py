"""music_library/의 원본 mp3들을 셔플백으로 골라 ffmpeg acrossfade로 이어붙여
목표 길이의 트랙 하나를 만든다. 외부 API 호출 없음 (비용 $0).
"""

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import state_manager as sm

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac"}


class LibraryTooSmallError(RuntimeError):
    pass


def list_library_files(library_dir):
    return sorted(
        p.name for p in Path(library_dir).iterdir() if p.suffix.lower() in AUDIO_EXTS
    )


def probe_duration(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(out.stdout)
    return float(data["format"]["duration"])


def pick_tracks(state, library_dir, files, target_seconds, crossfade_seconds, approx_track_seconds, rng=None):
    estimated_needed = math.ceil(target_seconds / approx_track_seconds)
    if len(files) < estimated_needed:
        raise LibraryTooSmallError(
            f"music_library에 곡이 부족합니다 (보유 {len(files)}개, 필요 약 {estimated_needed}개). "
            "Suno에서 새 배치를 다운로드해 music_library/에 추가해 주세요."
        )

    picked = []
    total = 0.0
    max_draws = estimated_needed * 3 + 5  # 무한루프 방지 안전장치
    while True:
        n = len(picked)
        needed_raw = target_seconds + crossfade_seconds * max(n - 1, 0)
        if total >= needed_raw:
            break
        if n >= max_draws:
            raise LibraryTooSmallError(
                "실제 곡 길이가 예상보다 짧아 목표 길이를 채우지 못했습니다. "
                "music_library에 곡을 더 추가해 주세요."
            )
        name = sm.draw(state, "music_track", files, count=1, rng=rng)[0]
        dur = probe_duration(Path(library_dir) / name)
        picked.append((name, dur))
        total += dur
    return picked


def build_crossfaded_track(library_dir, picked, crossfade_seconds, target_seconds, bitrate, output_path):
    inputs = []
    for name, _ in picked:
        inputs += ["-i", str(Path(library_dir) / name)]

    if len(picked) == 1:
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-t", str(target_seconds),
            "-c:a", "libmp3lame", "-b:a", bitrate,
            str(output_path),
        ]
    else:
        parts = []
        label = "0:a"
        for i in range(1, len(picked)):
            out_label = f"cf{i}"
            parts.append(
                f"[{label}][{i}:a]acrossfade=d={crossfade_seconds}:c1=tri:c2=tri[{out_label}]"
            )
            label = out_label
        filter_complex = ";".join(parts)
        cmd = [
            "ffmpeg", "-y", *inputs,
            "-filter_complex", filter_complex,
            "-map", f"[{label}]",
            "-t", str(target_seconds),
            "-c:a", "libmp3lame", "-b:a", bitrate,
            str(output_path),
        ]

    subprocess.run(cmd, check=True, capture_output=True)


def assemble(library_dir, state, target_seconds, crossfade_seconds, approx_track_seconds, bitrate, output_path, rng=None):
    files = list_library_files(library_dir)
    if not files:
        raise LibraryTooSmallError(
            "music_library가 비어 있습니다. Suno에서 다운로드한 mp3를 music_library/에 추가해 주세요."
        )
    picked = pick_tracks(state, library_dir, files, target_seconds, crossfade_seconds, approx_track_seconds, rng=rng)
    build_crossfaded_track(library_dir, picked, crossfade_seconds, target_seconds, bitrate, output_path)
    return picked


def main():
    parser = argparse.ArgumentParser(description="music_library에서 곡을 골라 이어붙인다")
    parser.add_argument("--library", default="music_library")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--target-seconds", type=float, required=True)
    parser.add_argument("--crossfade-seconds", type=float, default=3)
    parser.add_argument("--approx-track-seconds", type=float, default=180)
    parser.add_argument("--bitrate", default="192k")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    state = sm.load_state(args.state)
    try:
        picked = assemble(
            args.library,
            state,
            args.target_seconds,
            args.crossfade_seconds,
            args.approx_track_seconds,
            args.bitrate,
            args.output,
        )
    except LibraryTooSmallError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    sm.save_state(args.state, state)
    print(f"assembled {len(picked)} tracks -> {args.output}")
    for name, dur in picked:
        print(f"  - {name} ({dur:.1f}s)")


if __name__ == "__main__":
    main()
