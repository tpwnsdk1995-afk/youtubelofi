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
import make_thumbnail
import state_manager as sm
import sync_music_library
import upload_youtube


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_pipeline(settings_path="config/settings.yml", scenes_path="config/scenes.yml",
                  templates_path="config/title_templates.yml", track_names_path="config/track_name_parts.yml",
                  work_dir=None, dry_run=False, concept_override=None):
    settings = load_yaml(settings_path)

    # 조선 리브랜딩 전환 스위치: settings.yml의 concept가 joseon이면 조선 컨셉 설정으로
    # 교체된다 (씬/문구/썸네일/무드 로테이션). legacy면 기존 동작 그대로.
    # concept_override는 스위치를 켜기 전 리허설(dry-run)용 일회성 오버라이드.
    concept = concept_override or settings.get("concept", "legacy")
    if concept == "joseon":
        scenes_path = "config/scenes_joseon.yml"
        templates_path = "config/title_templates_joseon.yml"

    scenes_config = load_yaml(scenes_path)
    templates = load_yaml(templates_path)
    name_parts = load_yaml(track_names_path)

    state_path = settings["state_file"]
    library_dir = settings["music_library_dir"]
    v = settings["video"]
    a = settings["audio"]

    # 무거운 작업을 시작하기 전에 필요한 자격 증명을 먼저 확인해 빠르게 실패한다.
    image_api_key = os.environ.get("GEMINI_API_KEY")
    if not image_api_key:
        raise RuntimeError("GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.")
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
        gdrive_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
        gdrive_folder_id = os.environ.get("GDRIVE_MUSIC_FOLDER_ID")
        if gdrive_json and gdrive_folder_id:
            print("== 0/4 Google Drive에서 음악 라이브러리 동기화 ==")
            library_dir = work_dir / "music_library"
            drive_service = sync_music_library.get_drive_service(gdrive_json)
            files, downloaded = sync_music_library.sync_library(drive_service, gdrive_folder_id, library_dir)
            print(f"  Drive 폴더 내 {len(files)}개 중 {len(downloaded)}개 새로 다운로드")

        # 조선 컨셉이면 오늘의 무드(calm=집중/groove=산책·드라이브)를 먼저 뽑는다 —
        # 음원 폴더, 씬 후보, 문구 풀이 전부 무드를 따라간다.
        genre = None
        situation = None
        if concept == "joseon":
            # 무드별 라이브러리 현황을 항상 로그에 남긴다 (곡 부족을 빨리 알아차리도록)
            for g in templates["genres"]:
                gdir = Path(library_dir) / g
                count = len(list(gdir.glob("*.mp3"))) if gdir.is_dir() else 0
                print(f"  라이브러리 [{g}]: {count}곡")
            genre = generate_metadata.draw_genre(state, templates)
            # 상황 문구를 먼저 뽑아 씬 선택과 계절/시간대를 맞춘다 (꽃놀이 문구 + 밤 씬 방지)
            situation = generate_metadata.draw_situation(state, templates, genre)
            print(f"오늘의 무드: {genre} / 상황: {situation['id']}")
            music_dir = Path(library_dir) / genre
            if not music_dir.is_dir() or not any(music_dir.glob("*.mp3")):
                raise RuntimeError(
                    f"음원 폴더가 비어 있습니다: Drive 음악 폴더 안에 '{genre}' 하위 폴더를 만들고 "
                    f"해당 무드의 mp3를 채워주세요 (calm=집중용 잔잔, groove=산책/드라이브용 신남)."
                )
        else:
            music_dir = library_dir

        print("== 1/4 음악 조립 ==")
        chapters = assemble_music.assemble(
            music_dir, state,
            a["crossfade_seconds"], a["bitrate"],
            audio_path,
            reuse_ratio=a.get("reuse_ratio", 0.0),
            track_count_min=a.get("track_count_min"),
            track_count_max=a.get("track_count_max"),
            name_prefixes=name_parts["prefixes"],
            name_suffixes=name_parts["suffixes"],
        )
        print(f"  {len(chapters)}개 트랙 사용")

        print("== 2/4 이미지 생성 ==")
        style_suffix = None
        if concept == "joseon":
            scene = generate_image.draw_scene(state, scenes_config, genre=genre, pool_name=f"scene_{genre}", situation=situation)
            style = generate_image.draw_style(state, scene)
            style_suffix = generate_image.JOSEON_STYLE_SUFFIXES[style]
            print(f"  그림체: {style}")
        else:
            scene = generate_image.draw_scene(state, scenes_config)
        lighting, detail = generate_image.draw_modifiers(state, scenes_config)
        generate_image.generate_image(
            scene["prompt"], image_api_key,
            settings["image"].get("model", "gemini-2.5-flash-image"),
            settings["image"].get("aspect_ratio_hint", "16:9 widescreen"),
            image_path,
            extra_details=[lighting, detail],
            style_suffix=style_suffix,
        )
        print(f"  씬: {scene['id']} ({lighting}, {detail})")

        print("== 3/4 영상 조립 ==")
        build_video_mod.build_video(
            image_path, audio_path, video_path,
            v["resolution"], v["fps"], v["crf"], v["preset"], a["bitrate"],
            watermark_text=templates.get("channel_name"),
        )

        print("== 4/4 메타데이터 생성 + 업로드 ==")
        metadata = generate_metadata.build_metadata(state, chapters, templates, settings, scene_id=scene["id"], genre=genre, situation=situation)
        print(f"  제목: {metadata['title']}")

        # 썸네일 (조선 컨셉 전용): 씬 이미지 + 팝 텍스트. 실패해도 업로드는 진행한다.
        thumbnail_path = None
        if metadata.get("thumb_main"):
            thumbnail_path = work_dir / "thumbnail.jpg"
            try:
                make_thumbnail.create_thumbnail(
                    image_path, metadata["thumb_main"], metadata.get("thumb_sub", ""), thumbnail_path,
                )
                print(f"  썸네일 생성: {metadata['thumb_main']} / {metadata.get('thumb_sub', '')}")
            except Exception as e:
                print(f"WARNING: 썸네일 생성 실패 (업로드는 계속 진행): {e}", file=sys.stderr)
                thumbnail_path = None

        if dry_run:
            print("dry-run 모드: 실제 업로드는 건너뜁니다.")
            result = {"video_id": None, "title": metadata["title"], "scene_id": scene["id"], "description": metadata["description"]}
            if thumbnail_path is not None:
                # 리허설에서 사용자가 결과물을 눈으로 확인할 수 있게 워킹 디렉토리 밖에 복사
                shutil.copy(thumbnail_path, "thumbnail_preview.jpg")
                print("  썸네일 미리보기 저장: thumbnail_preview.jpg")
        else:
            response = upload_youtube.upload_video(video_path, metadata, youtube_credentials)
            result = {"video_id": response["id"], "title": metadata["title"], "scene_id": scene["id"], "description": metadata["description"]}
            print(f"  업로드 완료 (비공개, 확인 대기): {result['video_id']}")

            if thumbnail_path is not None:
                try:
                    upload_youtube.set_thumbnail(result["video_id"], thumbnail_path, youtube_credentials)
                    print("  썸네일 설정 완료")
                except Exception as e:
                    print(f"WARNING: 썸네일 설정 실패 (업로드 자체는 성공): {e}", file=sys.stderr)

            try:
                if concept == "joseon":
                    # 조선 컨셉은 무드별 재생목록 (calm/groove)
                    category = genre
                    playlist_title = templates["genres"][genre]["playlist_title"]
                else:
                    category = scene.get("category", "default")
                    playlist_titles = templates.get("playlist_titles", {})
                    playlist_title = playlist_titles.get(category, f"{templates.get('channel_name', 'noa music')} 로파이 플레이리스트")
                playlist_id = upload_youtube.get_or_create_playlist(state, youtube_credentials, category, playlist_title)
                upload_youtube.add_video_to_playlist(playlist_id, result["video_id"], youtube_credentials)
                print(f"  재생목록에 추가 ({category}): {playlist_id}")
            except Exception as e:
                # 재생목록 추가는 부가 기능이라 실패해도 업로드 자체는 성공으로 취급한다.
                print(f"WARNING: 재생목록 추가 실패 (업로드 자체는 성공): {e}", file=sys.stderr)

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
    parser.add_argument("--track-names-config", default="config/track_name_parts.yml")
    parser.add_argument("--work-dir")
    parser.add_argument("--dry-run", action="store_true", help="실제 업로드 없이 파이프라인만 검증")
    parser.add_argument("--concept", choices=["legacy", "joseon"], default=None,
                        help="settings.yml의 concept를 일회성으로 오버라이드 (리허설용)")
    parser.add_argument("--result-output", help="결과 JSON을 저장할 파일 경로 (워크플로우에서 확인 이슈 생성에 사용)")
    args = parser.parse_args()

    try:
        result = run_pipeline(
            args.settings_config, args.scenes_config, args.templates_config, args.track_names_config,
            work_dir=args.work_dir, dry_run=args.dry_run, concept_override=args.concept,
        )
    except (assemble_music.LibraryTooSmallError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))

    if args.result_output:
        with open(args.result_output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
