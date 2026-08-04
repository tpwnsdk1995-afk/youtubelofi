"""여러 영상 ID의 실제 YouTube 상태(공개 여부, 업로드/처리 상태)를 한 번에 조회한다.
GitHub Actions에서 videos.update가 성공했다고 보고해도, 그게 YouTube 쪽에서 정책
심사 등으로 나중에 뒤집히지 않았는지는 별도로 확인해야 해서 만든 진단 스크립트다.
"""

import argparse
import json
import sys

import upload_youtube as uy


def main():
    parser = argparse.ArgumentParser(description="영상 ID들의 실제 공개 상태를 조회한다")
    parser.add_argument("--video-ids", required=True, help="쉼표로 구분된 영상 ID 목록")
    args = parser.parse_args()

    video_ids = [v.strip() for v in args.video_ids.split(",") if v.strip()]

    try:
        credentials = uy.get_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    from googleapiclient.discovery import build
    youtube = build("youtube", "v3", credentials=credentials)

    response = youtube.videos().list(part="snippet,status,processingDetails", id=",".join(video_ids)).execute()
    found = {item["id"]: item for item in response.get("items", [])}

    results = []
    for vid in video_ids:
        item = found.get(vid)
        if item is None:
            results.append({"video_id": vid, "found": False})
            continue
        results.append({
            "video_id": vid,
            "found": True,
            "title": item["snippet"]["title"],
            "privacyStatus": item["status"].get("privacyStatus"),
            "uploadStatus": item["status"].get("uploadStatus"),
            "rejectionReason": item["status"].get("rejectionReason"),
            "processingStatus": item.get("processingDetails", {}).get("processingStatus"),
        })

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
