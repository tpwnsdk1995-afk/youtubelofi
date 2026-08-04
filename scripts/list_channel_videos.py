"""채널에 실제로 올라가 있는 전체 영상 목록과 각 영상의 공개 상태를 조회한다.
GitHub Actions 실행 로그로 영상 이력을 재구성하는 건 dry-run, 컨펌 플로우 이전
업로드, 임시 OAuth 테스트 등이 섞여 있어 오차가 나기 쉽다. 채널의 uploads
재생목록을 직접 조회하는 쪽이 실제 채널 상태에 대한 유일하게 정확한 소스다.
"""

import json
import sys

import upload_youtube as uy
from googleapiclient.discovery import build


def main():
    try:
        credentials = uy.get_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    youtube = build("youtube", "v3", credentials=credentials)

    channels_response = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads_playlist_id = channels_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    video_ids = []
    page_token = None
    while True:
        response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()
        video_ids.extend(item["contentDetails"]["videoId"] for item in response["items"])
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    results = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        response = youtube.videos().list(part="snippet,status", id=",".join(batch)).execute()
        for item in response["items"]:
            results.append({
                "video_id": item["id"],
                "title": item["snippet"]["title"],
                "publishedAt": item["snippet"]["publishedAt"],
                "privacyStatus": item["status"].get("privacyStatus"),
                "uploadStatus": item["status"].get("uploadStatus"),
            })

    print(f"총 영상 수: {len(results)}")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
