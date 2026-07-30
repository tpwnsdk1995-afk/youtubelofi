"""로파이 영상 생성/업로드 전체 파이프라인 오케스트레이션.

음악 조립(로컬, $0) -> 이미지 생성(API) -> 영상 조립(로컬) -> 메타데이터 생성(로컬) -> 유튜브 업로드
성공적으로 끝났을 때만 state.json에 로테이션 진행 상황을 반영한다 (중간 실패 시 다음 실행에서 재시도 가능하도록).
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

import assemble_music
import build_video as build_video_mod
import generate_image
import generate_metadata
import state_manager as sm
import upload_youtube


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_pipeline(settings_path="config/settings.yml", scenes_path="config/scenes.yml",
                  templates_path="config/title_templates.yml", work_dir=None, dry_run=False):
    settings = load_yaml(settings_path)
    scenes_config = load_yaml(scenes_path)
    templates = load_yaml(templates_path)

    state_path = settings["state_file"]
    library_dir = settings["music_library_dir"]
    v = settings["video"]
    a = settings["audio"]

    # 무거운 작업을 시작하기 전에 필요한 자격 증명을 먼저 확인해 빠르게 실패한다.
    image_api_key = os.environ.get("STABILITY_IMAGE_API_KEY")
    if not image_api_key:
        raise RuntimeError("STABILITY_IMAGE_API_KEY 환경변수가 설정되어 있지 않습니다.")
    youtube_credentials = None
    if not dry_run:
        youtube_credentials = upload_youtube.get_credentials()

    state = sm.load_state(state_path)

    work_dir = Path(work_dir or tempfile.mkdtemp(prefix="lofi_pipeline_"))
    work_dir.mkdir(parents=True, exist_ok=True)

    audio_path = work_dir / "final_audio.mp3"
    image_path = work_dir / "scene.png"
    video_path = work_dir / "final_video.mp4"

    try:
        print("== 1/4 음악 조립 ==")
        picked = assemble_music.assemble(
            library_dir, state,
            v["target_duration_seconds"], a["crossfade_seconds"], a["approx_track_seconds"], a["bitrate"],
            audio_path,
        )
        print(f"  {len(picked)}개 트랙 사용")

        print("== 2/4 이미지 생성 ==")
        scene = generate_image.draw_scene(state, scenes_config)
        generate_image.generate_image(
            scene["prompt"], image_api_key,
            settings["image"].get("aspect_ratio", "16:9"), settings["image"].get("output_format", "png"),
            image_path,
        )
        print(f"  씬: {scene['id']}")

        print("== 3/4 영상 조립 ==")
        build_video_mod.build_video(
            image_path, audio_path, video_path,
            v["resolution"], v["fps"], v["crf"], v["preset"], a["bitrate"],
        )

        print("== 4/4 메타데이터 생성 + 업로드 ==")
        metadata = generate_metadata.build_metadata(state, scene["id"], templates, settings)
        print(f"  제목: {metadata['title']}")

        if dry_run:
            print("dry-run 모드: 실제 업로드는 건너뜁니다.")
            result = {"video_id": None, "title": metadata["title"], "scene_id": scene["id"]}
        else:
            response = upload_youtube.upload_video(video_path, metadata, youtube_credentials)
            result = {"video_id": response["id"], "title": metadata["title"], "scene_id": scene["id"]}
            print(f"  업로드 완료: {result['video_id']}")

        # 여기까지 도달했다면 전체가 성공한 것이므로 로테이션 상태를 저장한다.
        sm.record_video(state, result)
        sm.save_state(state_path, state)
    finally:
        if not os.environ.get("KEEP_WORK_DIR"):
            shutil.rmtree(work_dir, ignore_errors=True)

    return result


def main():
    parser = argparse.ArgumentParser(description="로파이 영상 생성/업로드 전체 파이프라인")
    parser.add_argument("--settings-config", default="config/settings.yml")
    parser.add_argument("--scenes-config", default="config/scenes.yml")
    parser.add_argument("--templates-config", default="config/title_templates.yml")
    parser.add_argument("--work-dir")
    parser.add_argument("--dry-run", action="store_true", help="실제 업로드 없이 파이프라인만 검증")
    args = parser.parse_args()

    try:
        result = run_pipeline(
            args.settings_config, args.scenes_config, args.templates_config,
            work_dir=args.work_dir, dry_run=args.dry_run,
        )
    except (assemble_music.LibraryTooSmallError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
