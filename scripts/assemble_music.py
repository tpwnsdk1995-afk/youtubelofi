"""music_library/의 원본 mp3 전체를 매 실행마다 셔플된 순서로 한 번씩 이어붙이고,
그중 일부(reuse_ratio 비율)만 무작위로 한 번 더 섞어 넣어 2시간을 살짝 넘기면서도
매번 다른 길이(2시간 10분, 2시간 15분 등)가 나오게 한다. 정확히 목표 길이에 맞추지
않고, 이어붙인 결과를 그대로 사용한다. 외부 API 호출 없음 (비용 $0).
"""

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

import yaml

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


def pick_tracks(state, library_dir, files, reuse_ratio=0.0, rng=None):
    """라이브러리의 모든 곡을 한 번씩 뽑고(로테이션 상태에 반영), 그중 일부를
    reuse_ratio 비율만큼 무작위로 골라 한 번 더 섞어 넣는다. 예: 40곡에
    reuse_ratio=0.25면 10곡이 추가로 한 번 더 재생되어, 어떤 곡이 뽑히느냐에
    따라 매번 총 길이가 자연스럽게 달라진다.
    """
    rng = rng or random
    order = sm.draw(state, "music_track", files, count=len(files), rng=rng)

    reuse_count = round(len(files) * reuse_ratio)
    reuse_sample = rng.sample(files, min(reuse_count, len(files))) if reuse_count > 0 else []

    combined = order + reuse_sample
    rng.shuffle(combined)

    picked = []
    for name in combined:
        try:
            dur = probe_duration(Path(library_dir) / name)
        except subprocess.CalledProcessError:
            print(
                f"WARNING: '{name}' 파일을 읽을 수 없어 건너뜁니다 (손상되었거나 지원하지 않는 형식일 수 있습니다).",
                file=sys.stderr,
            )
            continue
        picked.append((name, dur))
    return picked


def build_chapters(state, picked, name_prefixes, name_suffixes, crossfade_seconds, rng=None):
    """picked(재생 순서대로의 (파일명, 길이) 목록)를 바탕으로, 각 트랙에 영구 고유
    이름을 배정하고 크로스페이드를 반영한 실제 시작 시각을 계산한다."""
    chapters = []
    cum_duration = 0.0
    for i, (filename, duration) in enumerate(picked):
        start = max(cum_duration - i * crossfade_seconds, 0.0)
        name = sm.get_or_assign_track_name(state, filename, name_prefixes, name_suffixes, rng=rng)
        chapters.append({
            "filename": filename,
            "name": name,
            "start_seconds": start,
            "duration_seconds": duration,
        })
        cum_duration += duration
    return chapters


def build_crossfaded_track(library_dir, picked, crossfade_seconds, bitrate, output_path):
    inputs = []
    for name, _ in picked:
        inputs += ["-i", str(Path(library_dir) / name)]

    if len(picked) == 1:
        cmd = [
            "ffmpeg", "-y", *inputs,
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
            "-c:a", "libmp3lame", "-b:a", bitrate,
            str(output_path),
        ]

    subprocess.run(cmd, check=True, capture_output=True)


def assemble(library_dir, state, crossfade_seconds, bitrate, output_path,
             reuse_ratio=0.0, name_prefixes=None, name_suffixes=None, rng=None):
    files = list_library_files(library_dir)
    if not files:
        raise LibraryTooSmallError(
            "music_library가 비어 있습니다. Suno에서 다운로드한 mp3를 music_library/에 추가해 주세요."
        )
    picked = pick_tracks(state, library_dir, files, reuse_ratio=reuse_ratio, rng=rng)
    if not picked:
        raise LibraryTooSmallError(
            "music_library의 모든 파일을 읽을 수 없습니다. 파일이 손상되지 않았는지 확인해 주세요."
        )
    build_crossfaded_track(library_dir, picked, crossfade_seconds, bitrate, output_path)
    chapters = build_chapters(
        state, picked,
        name_prefixes or ["Soft"], name_suffixes or ["mere"],
        crossfade_seconds, rng=rng,
    )
    return chapters


def main():
    parser = argparse.ArgumentParser(description="music_library 전체를 한 번씩 이어붙인다")
    parser.add_argument("--library", default="music_library")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--track-names-config", default="config/track_name_parts.yml")
    parser.add_argument("--crossfade-seconds", type=float, default=3)
    parser.add_argument("--reuse-ratio", type=float, default=0.25, help="일부 곡을 한 번 더 재생하는 비율 (0.25 = 25%%)")
    parser.add_argument("--bitrate", default="192k")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.track_names_config, encoding="utf-8") as f:
        name_parts = yaml.safe_load(f)

    state = sm.load_state(args.state)
    try:
        chapters = assemble(
            args.library,
            state,
            args.crossfade_seconds,
            args.bitrate,
            args.output,
            name_prefixes=name_parts["prefixes"],
            name_suffixes=name_parts["suffixes"],
            reuse_ratio=args.reuse_ratio,
        )
    except LibraryTooSmallError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    sm.save_state(args.state, state)
    print(f"assembled {len(chapters)} tracks -> {args.output}")
    for ch in chapters:
        mm, ss = divmod(int(ch["start_seconds"]), 60)
        hh, mm = divmod(mm, 60)
        print(f"  - {hh:02d}:{mm:02d}:{ss:02d} {ch['name']} ({ch['filename']}, {ch['duration_seconds']:.1f}s)")


if __name__ == "__main__":
    main()
