"""YouTube Data API v3로 완성된 영상을 무인 업로드한다.
저장된 refresh token으로 매 실행마다 access token을 자동 갱신 (브라우저 재인증 불필요).
"""

import argparse
import json
import os
import sys

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TOKEN_URI = "https://oauth2.googleapis.com/token"
# youtube.upload 만으로는 업로드 후 수정(videos.update)이나 썸네일 교체가 403으로 막힌다.
# 전체 관리 스코프로 업로드/수정/썸네일/재생목록을 모두 커버한다.
SCOPES = ["https://www.googleapis.com/auth/youtube"]

REQUIRED_ENV_VARS = ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"]


def get_credentials(env=None):
    env = env or os.environ
    values = {name: env.get(name) for name in REQUIRED_ENV_VARS}
    missing = [name for name, val in values.items() if not val]
    if missing:
        raise RuntimeError(f"다음 환경변수가 설정되어 있지 않습니다: {', '.join(missing)}")
    return Credentials(
        None,
        refresh_token=values["YOUTUBE_REFRESH_TOKEN"],
        token_uri=TOKEN_URI,
        client_id=values["YOUTUBE_CLIENT_ID"],
        client_secret=values["YOUTUBE_CLIENT_SECRET"],
        scopes=SCOPES,
    )


def build_request_body(metadata):
    status = {
        "privacyStatus": metadata["privacyStatus"],
        "selfDeclaredMadeForKids": metadata["madeForKids"],
    }
    # 음악과 이미지 모두 AI 생성/편집이 관여했음을 유튜브에 공개 고지한다.
    if metadata.get("containsSyntheticMedia", True):
        status["containsSyntheticMedia"] = True

    return {
        "snippet": {
            "title": metadata["title"][:100],
            "description": metadata["description"],
            "tags": metadata["tags"],
            "categoryId": metadata["categoryId"],
        },
        "status": status,
    }


def upload_video(video_path, metadata, credentials, youtube_client=None, chunksize=8 * 1024 * 1024, num_retries=5):
    youtube = youtube_client or build("youtube", "v3", credentials=credentials)
    body = build_request_body(metadata)
    media = MediaFileUpload(video_path, chunksize=chunksize, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    # num_retries는 googleapiclient가 내부적으로 5xx/연결 오류에 지수 백오프 재시도를 적용한다.
    response = None
    while response is None:
        status, response = request.next_chunk(num_retries=num_retries)
    return response


def get_video(video_id, credentials, youtube_client=None):
    youtube = youtube_client or build("youtube", "v3", credentials=credentials)
    response = youtube.videos().list(part="snippet,status", id=video_id).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError(f"영상을 찾을 수 없습니다: {video_id}")
    return items[0]


def update_video(video_id, credentials, title=None, description=None, tags=None,
                  privacy_status=None, youtube_client=None):
    """이미 업로드된 영상의 제목/설명/태그/공개상태를 수정한다. videos.update는 전체
    snippet/status 객체를 요구하므로, 현재 값을 먼저 읽어와 바뀐 필드만 덮어쓴다."""
    youtube = youtube_client or build("youtube", "v3", credentials=credentials)
    current = get_video(video_id, credentials, youtube_client=youtube)

    snippet = current["snippet"]
    if title is not None:
        snippet["title"] = title[:100]
    if description is not None:
        snippet["description"] = description
    if tags is not None:
        snippet["tags"] = tags

    status = current["status"]
    if privacy_status is not None:
        status["privacyStatus"] = privacy_status

    body = {"id": video_id, "snippet": snippet, "status": status}
    return youtube.videos().update(part="snippet,status", body=body).execute()


def set_thumbnail(video_id, image_path, credentials, youtube_client=None):
    youtube = youtube_client or build("youtube", "v3", credentials=credentials)
    mimetype = "image/jpeg" if str(image_path).lower().endswith((".jpg", ".jpeg")) else "image/png"
    media = MediaFileUpload(str(image_path), mimetype=mimetype)
    return youtube.thumbnails().set(videoId=video_id, media_body=media).execute()


def get_or_create_playlist(state, credentials, category, title, description="", youtube_client=None):
    """무드/씬 category별로 재생목록을 하나씩 만들어 state에 저장해두고 재사용한다.
    (하나로 다 몰아넣는 대신) 무드별로 나누면 자동재생 체인이 더 일관되게 이어져
    세션 시청시간 확보에 유리하다."""
    youtube = youtube_client or build("youtube", "v3", credentials=credentials)
    playlist_ids = state.setdefault("playlist_ids", {})
    playlist_id = playlist_ids.get(category)
    if playlist_id:
        return playlist_id

    response = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": "public"},
        },
    ).execute()
    playlist_id = response["id"]
    playlist_ids[category] = playlist_id
    return playlist_id


def add_video_to_playlist(playlist_id, video_id, credentials, youtube_client=None):
    youtube = youtube_client or build("youtube", "v3", credentials=credentials)
    return youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute()


def main():
    parser = argparse.ArgumentParser(description="완성된 영상을 유튜브에 업로드한다")
    parser.add_argument("--video", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--result-output")
    args = parser.parse_args()

    try:
        credentials = get_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.metadata, encoding="utf-8") as f:
        metadata = json.load(f)

    response = upload_video(args.video, metadata, credentials)
    video_id = response["id"]
    print(f"uploaded video id: {video_id}")

    if args.result_output:
        with open(args.result_output, "w", encoding="utf-8") as f:
            json.dump({"video_id": video_id, "title": metadata["title"], "scene_id": metadata.get("scene_id")}, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
