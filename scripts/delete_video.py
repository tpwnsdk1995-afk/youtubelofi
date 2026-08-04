"""거부된 영상을 유튜브에서 완전히 삭제한다.
check-telegram-response.yml이 거부(reject) 응답을 받으면 이 스크립트를 실행해
비공개로 방치하는 대신 채널에서 아예 제거한다.
"""

import argparse
import sys

import upload_youtube as uy


def delete_video(video_id, credentials, youtube_client=None):
    youtube = youtube_client or uy.build("youtube", "v3", credentials=credentials)
    youtube.videos().delete(id=video_id).execute()


def main():
    parser = argparse.ArgumentParser(description="유튜브 영상을 삭제한다")
    parser.add_argument("--video-id", required=True)
    args = parser.parse_args()

    try:
        credentials = uy.get_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    delete_video(args.video_id, credentials)
    print(f"deleted video id: {args.video_id}")


if __name__ == "__main__":
    main()
