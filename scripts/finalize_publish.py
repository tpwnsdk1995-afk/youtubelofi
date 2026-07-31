"""사용자 확인 후 비공개로 올려둔 영상을 공개로 전환한다 (필요시 제목/설명도 함께 수정).
publish-video.yml이 항상 private로 업로드해두고, 사용자가 채팅에서 확인해주면
이 스크립트를 (finalize-publish.yml 워크플로우를 통해) 실행해 실제로 공개한다.
"""

import argparse
import sys

import upload_youtube as uy


def main():
    parser = argparse.ArgumentParser(description="비공개 영상을 공개로 전환한다")
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--privacy-status", default="public")
    parser.add_argument("--title", default=None)
    parser.add_argument("--description", default=None)
    args = parser.parse_args()

    try:
        credentials = uy.get_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    response = uy.update_video(
        args.video_id, credentials,
        title=args.title, description=args.description, privacy_status=args.privacy_status,
    )
    print(f"영상 {args.video_id} 업데이트 완료 (privacyStatus={response['status']['privacyStatus']})")


if __name__ == "__main__":
    main()
