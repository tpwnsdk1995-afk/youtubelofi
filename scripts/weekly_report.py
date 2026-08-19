"""매주 채널 반응을 집계해 텔레그램으로 보내는 주간 리포트.

조회수 등 유튜브 API가 주는 값은 항상 "현재까지 누적"이라 최근 7일간의 증가분을
알려면 지난 주 실행 시점의 스냅샷이 필요하다. 그래서 매 실행마다
`state/weekly_stats.json`에 그 시점의 채널/영상별 누적 수치를 저장해 두고,
다음 실행에서 그 값과의 차이를 "이번 주 증가분"으로 계산한다. 스냅샷이 없는
첫 실행이나 신규 영상은 증가분 대신 누적값을 그대로 보여준다.

무드(calm/groove)는 state.json의 recent_videos에 기록이 있으면 그대로 쓰고,
(리포트 도입 이전에 올라간) 옛 영상은 설명란 앞부분의 top_hashtags 문자열로
역추정한다 - config/title_templates_joseon.yml에 정의된 문자열과 대조한다.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from googleapiclient.discovery import build

import upload_youtube as uy

KST = timezone(timedelta(hours=9))


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(path, default):
    p = Path(path)
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_channel_stats(youtube):
    resp = youtube.channels().list(part="statistics,contentDetails", mine=True).execute()
    item = resp["items"][0]
    stats = item["statistics"]
    uploads_playlist_id = item["contentDetails"]["relatedPlaylists"]["uploads"]
    return {
        "subscriberCount": None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0)),
        "viewCount": int(stats.get("viewCount", 0)),
        "videoCount": int(stats.get("videoCount", 0)),
    }, uploads_playlist_id


def fetch_all_video_ids(youtube, uploads_playlist_id):
    video_ids = []
    page_token = None
    while True:
        resp = youtube.playlistItems().list(
            part="contentDetails", playlistId=uploads_playlist_id, maxResults=50, pageToken=page_token,
        ).execute()
        video_ids.extend(item["contentDetails"]["videoId"] for item in resp["items"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return video_ids


def fetch_video_details(youtube, video_ids):
    results = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        resp = youtube.videos().list(part="snippet,statistics,status", id=",".join(batch)).execute()
        for item in resp["items"]:
            stats = item.get("statistics", {})
            results[item["id"]] = {
                "title": item["snippet"]["title"],
                "description": item["snippet"].get("description", ""),
                "publishedAt": item["snippet"]["publishedAt"],
                "privacyStatus": item["status"].get("privacyStatus"),
                "viewCount": int(stats.get("viewCount", 0)),
                "likeCount": int(stats.get("likeCount", 0)) if "likeCount" in stats else None,
                "commentCount": int(stats.get("commentCount", 0)) if "commentCount" in stats else None,
            }
    return results


def build_genre_lookup(templates):
    """무드별 description 상단 top_hashtags 문자열 -> 무드 이름."""
    lookup = {}
    for genre, g in templates.get("genres", {}).items():
        lookup[" ".join(g["top_hashtags"])] = genre
    return lookup


def infer_genre(video_id, recent_by_id, description_by_id, genre_lookup):
    entry = recent_by_id.get(video_id)
    if entry and entry.get("genre"):
        return entry["genre"]
    desc = description_by_id.get(video_id, "")
    first_line = desc.split("\n", 1)[0].strip()
    return genre_lookup.get(first_line)


def fmt_delta(n):
    if n is None:
        return "(비교 데이터 없음)"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:,}"


def fmt_num(n):
    return "?" if n is None else f"{n:,}"


def build_report(now, channel_stats, channel_prev, videos, video_prev, recent_by_id, genre_lookup):
    week_start = now - timedelta(days=7)
    lines = []
    lines.append(f"📊 조선로파이 주간 리포트 ({week_start.strftime('%m/%d')} ~ {now.strftime('%m/%d')})")
    lines.append("")

    sub_delta = None
    view_delta = None
    if channel_prev:
        if channel_stats["subscriberCount"] is not None and channel_prev.get("subscriberCount") is not None:
            sub_delta = channel_stats["subscriberCount"] - channel_prev["subscriberCount"]
        view_delta = channel_stats["viewCount"] - channel_prev.get("viewCount", channel_stats["viewCount"])

    lines.append("[채널 전체]")
    sub_text = f"{fmt_num(channel_stats['subscriberCount'])}명"
    if sub_delta is not None:
        sub_text += f" ({fmt_delta(sub_delta)})"
    lines.append(f"구독자: {sub_text}")
    view_text = f"{fmt_num(channel_stats['viewCount'])}회"
    if view_delta is not None:
        view_text += f" ({fmt_delta(view_delta)})"
    lines.append(f"총 조회수: {view_text}")
    lines.append(f"공개 영상 수: {channel_stats['videoCount']}개")
    lines.append("")

    public_videos = {vid: v for vid, v in videos.items() if v["privacyStatus"] == "public"}
    description_by_id = {vid: v.get("description", "") for vid, v in public_videos.items()}

    # 이번 주 신작 (published_at이 7일 이내)
    new_videos = []
    for vid, v in public_videos.items():
        published = datetime.fromisoformat(v["publishedAt"].replace("Z", "+00:00")).astimezone(KST)
        if published >= week_start:
            new_videos.append((vid, v, published))
    new_videos.sort(key=lambda t: t[2], reverse=True)

    lines.append(f"[이번 주 신작 ({len(new_videos)}개)]")
    if not new_videos:
        lines.append("(없음)")
    for vid, v, published in new_videos:
        genre = infer_genre(vid, recent_by_id, description_by_id, genre_lookup) or "?"
        title_short = v["title"][:40] + ("…" if len(v["title"]) > 40 else "")
        lines.append(f"- ({genre}) {title_short}")
        lines.append(f"  조회수 {fmt_num(v['viewCount'])}회 · 좋아요 {fmt_num(v['likeCount'])} · youtu.be/{vid}")
    lines.append("")

    # 조회수 증가분 계산 (스냅샷 있는 영상만)
    deltas = {}
    for vid, v in public_videos.items():
        prev = video_prev.get(vid)
        if prev is not None:
            deltas[vid] = v["viewCount"] - prev.get("viewCount", v["viewCount"])

    # 무드별 반응 (증가분 있는 영상 기준)
    genre_totals = {}
    for vid, delta in deltas.items():
        genre = infer_genre(vid, recent_by_id, description_by_id, genre_lookup)
        if not genre:
            continue
        bucket = genre_totals.setdefault(genre, {"count": 0, "total": 0})
        bucket["count"] += 1
        bucket["total"] += delta

    if genre_totals:
        lines.append("[무드별 반응 (최근 7일 조회수 증가분)]")
        for genre, b in sorted(genre_totals.items(), key=lambda kv: -kv[1]["total"]):
            avg = b["total"] / b["count"] if b["count"] else 0
            lines.append(f"- {genre}: 영상 {b['count']}개, 합계 {fmt_delta(b['total'])}회, 평균 {avg:+.0f}회/영상")
        lines.append("")

    # TOP 3 / 반응 저조
    if deltas:
        ranked = sorted(deltas.items(), key=lambda kv: -kv[1])
        lines.append("[조회수 증가 TOP 3]")
        for vid, delta in ranked[:3]:
            v = public_videos[vid]
            title_short = v["title"][:40] + ("…" if len(v["title"]) > 40 else "")
            lines.append(f"- {fmt_delta(delta)}회 · {title_short} · youtu.be/{vid}")
        lines.append("")

        # 게시 7일 이상 지났는데 이번 주 증가분이 가장 적은 영상 (신작 제외)
        stale_candidates = [
            (vid, delta) for vid, delta in ranked
            if vid not in {v[0] for v in new_videos}
        ]
        if stale_candidates:
            lines.append("[반응 저조 (게시 7일+ 경과, 이번 주 증가 하위 3)]")
            for vid, delta in stale_candidates[-3:]:
                v = public_videos[vid]
                title_short = v["title"][:40] + ("…" if len(v["title"]) > 40 else "")
                lines.append(f"- {fmt_delta(delta)}회 · {title_short} · youtu.be/{vid}")
    else:
        lines.append("(지난 주 스냅샷이 없어 증가분 비교는 다음 리포트부터 제공됩니다)")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="주간 채널 반응 리포트를 생성해 텔레그램으로 보낸다")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--weekly-stats", default="state/weekly_stats.json")
    parser.add_argument("--templates-config", default="config/title_templates_joseon.yml")
    parser.add_argument("--output", default="weekly_report.txt")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 리포트만 파일로 저장")
    args = parser.parse_args()

    templates = load_yaml(args.templates_config)
    genre_lookup = build_genre_lookup(templates)

    state = load_json(args.state, {})
    recent_by_id = {v["video_id"]: v for v in state.get("recent_videos", []) if v.get("video_id")}

    try:
        credentials = uy.get_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    youtube = build("youtube", "v3", credentials=credentials)

    channel_stats, uploads_playlist_id = fetch_channel_stats(youtube)
    video_ids = fetch_all_video_ids(youtube, uploads_playlist_id)
    videos = fetch_video_details(youtube, video_ids)

    prev_snapshot = load_json(args.weekly_stats, {})
    channel_prev = prev_snapshot.get("channel")
    video_prev = prev_snapshot.get("videos", {})

    now = datetime.now(KST)
    report = build_report(now, channel_stats, channel_prev, videos, video_prev, recent_by_id, genre_lookup)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    # 다음 주 비교를 위해 이번 실행 시점 스냅샷 저장
    save_json(args.weekly_stats, {
        "snapshot_at": now.isoformat(),
        "channel": channel_stats,
        "videos": {vid: {"viewCount": v["viewCount"], "likeCount": v["likeCount"]} for vid, v in videos.items()},
    })

    if not args.dry_run:
        import send_telegram_message
        sys.argv = ["send_telegram_message.py", f"--text={report}"]
        send_telegram_message.main()


if __name__ == "__main__":
    main()
