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
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

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
    return {
        "snippet": {
            "title": metadata["title"][:100],
            "description": metadata["description"],
            "tags": metadata["tags"],
            "categoryId": metadata["categoryId"],
        },
        "status": {
            "privacyStatus": metadata["privacyStatus"],
            "selfDeclaredMadeForKids": metadata["madeForKids"],
        },
    }


def upload_video(video_path, metadata, credentials, youtube_client=None, chunksize=8 * 1024 * 1024):
    youtube = youtube_client or build("youtube", "v3", credentials=credentials)
    body = build_request_body(metadata)
    media = MediaFileUpload(video_path, chunksize=chunksize, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
    return response


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
