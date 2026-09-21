"""채널이 '발견될 수 있는 상태'인지 점검한다 (읽기 전용).

영상은 매일 정상 업로드되는데 구독자가 0명이면, 영상 한 편씩 손보기 전에 채널 단위
설정부터 봐야 한다. 유튜브는 채널 설명·키워드가 비어 있으면 채널을 검색 대상으로
거의 잡지 않고, 재생목록이 없으면 한 영상을 본 사람을 다음 영상으로 넘길 수단이
없다. 파이프라인은 영상만 올리고 이 영역(채널명/핸들/배너/설명/키워드)은 사람이
유튜브 스튜디오에서 설정해야 하는데, 실제로 적용됐는지 확인할 방법이 그동안 없었다.

이 스크립트는 채널을 **수정하지 않는다.** 현재 상태를 읽어 문제만 지목한다.
"""

import argparse
import os
import re
import sys

import upload_youtube as uy
from googleapiclient.discovery import build

# 채널 설명이 이보다 짧으면 검색 대상으로 잡히기 어렵다. 유튜브는 채널 설명을
# 채널 검색의 주요 신호로 쓰는데, 한 줄짜리로는 어떤 키워드도 걸리지 않는다.
MIN_CHANNEL_DESCRIPTION = 100

# YPP 요건. 시청시간은 '유효 공개 영상의 최근 12개월' 기준이다.
YPP_SUBSCRIBERS = 1000
YPP_WATCH_HOURS = 4000

DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?$"
)


def parse_duration(iso):
    """ISO 8601 재생시간(PT1H23M45S)을 초로 바꾼다. 못 읽으면 0을 준다.

    영상 길이는 시청시간 잠재력 계산의 분모라, 여기서 조용히 틀리면 'YPP까지
    얼마나 남았나'가 통째로 어긋난다. 그래서 알 수 없는 형식은 추측하지 않고 0으로
    두어 과대평가되지 않게 한다.
    """
    if not iso:
        return 0
    m = DURATION_RE.match(iso)
    if not m:
        return 0
    parts = {k: int(v) if v else 0 for k, v in m.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def fmt_hms(seconds):
    hh, rem = divmod(int(seconds), 3600)
    mm, ss = divmod(rem, 60)
    return f"{hh}:{mm:02d}:{ss:02d}" if hh else f"{mm}:{ss:02d}"


def fetch_channel(youtube):
    resp = youtube.channels().list(
        part="snippet,statistics,brandingSettings,contentDetails",
        mine=True,
    ).execute()
    items = resp.get("items") or []
    if not items:
        raise RuntimeError("채널을 찾을 수 없습니다 (토큰이 다른 계정일 수 있음)")
    return items[0]


def fetch_playlists(youtube):
    playlists, token = [], None
    while True:
        resp = youtube.playlists().list(
            part="snippet,contentDetails", mine=True, maxResults=50, pageToken=token
        ).execute()
        playlists.extend(resp.get("items", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return playlists


def fetch_uploads(youtube, uploads_playlist_id):
    video_ids, token = [], None
    while True:
        resp = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=token,
        ).execute()
        video_ids.extend(i["contentDetails"]["videoId"] for i in resp.get("items", []))
        token = resp.get("nextPageToken")
        if not token:
            break

    videos = []
    for i in range(0, len(video_ids), 50):
        resp = youtube.videos().list(
            part="snippet,contentDetails,status,statistics",
            id=",".join(video_ids[i:i + 50]),
        ).execute()
        for item in resp.get("items", []):
            videos.append({
                "video_id": item["id"],
                "title": item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"],
                "privacy": item["status"].get("privacyStatus"),
                "duration_seconds": parse_duration(item["contentDetails"].get("duration")),
                "views": int(item.get("statistics", {}).get("viewCount", 0)),
                "tag_count": len(item["snippet"].get("tags") or []),
                "category_id": item["snippet"].get("categoryId"),
            })
    return videos


def fetch_playlist_video_ids(youtube, playlist_id):
    ids, token = set(), None
    while True:
        resp = youtube.playlistItems().list(
            part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=token
        ).execute()
        ids.update(i["contentDetails"]["videoId"] for i in resp.get("items", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return ids


def diagnose(channel, playlists, videos, playlist_members):
    """채널 상태에서 '고쳐야 할 것'만 뽑는다. 정상 항목은 굳이 말하지 않는다.

    반환은 (심각도, 문구) 목록이고 심각도가 높은 순으로 정렬돼 나온다. 리포트가
    길어지면 사장님이 무엇부터 손댈지 못 정하므로, 순서 자체가 우선순위다.
    """
    problems = []
    snippet = channel.get("snippet", {})
    branding = channel.get("brandingSettings", {})
    bch = branding.get("channel", {})

    if not snippet.get("customUrl"):
        problems.append((3, "핸들(@...)이 설정되지 않음 — 채널 주소로 공유·검색이 안 됨"))

    desc = (bch.get("description") or snippet.get("description") or "").strip()
    if not desc:
        problems.append((3, "채널 설명이 비어 있음 — 채널 검색에 거의 잡히지 않음"))
    elif len(desc) < MIN_CHANNEL_DESCRIPTION:
        problems.append((2, f"채널 설명이 {len(desc)}자로 너무 짧음 (권장 {MIN_CHANNEL_DESCRIPTION}자 이상)"))

    if not (bch.get("keywords") or "").strip():
        problems.append((3, "채널 키워드가 비어 있음 — 유튜브가 채널 주제를 모름"))

    if not branding.get("image", {}).get("bannerExternalUrl"):
        problems.append((2, "배너 이미지 없음 — 채널 방문자에게 빈 채널로 보임"))

    if not snippet.get("country"):
        problems.append((1, "국가 설정 없음 — 한국 시청자 추천에 불리"))

    public = [v for v in videos if v["privacy"] == "public"]
    public_ids = {v["video_id"] for v in public}
    if not playlists:
        problems.append((3, "재생목록이 하나도 없음 — 한 영상을 본 사람을 다음 영상으로 넘길 수단이 없음"))
    else:
        in_any = set().union(*playlist_members.values()) if playlist_members else set()
        orphans = [v for v in public if v["video_id"] not in in_any]
        if orphans:
            problems.append((2, f"재생목록에 안 들어간 공개 영상 {len(orphans)}편 — 연속 재생에서 빠짐"))

        # 담긴 영상이 전부 비공개면 방문자에게는 빈 목록으로 보인다. 리브랜딩 후
        # 구 영상을 비공개로 돌리면 그 시절 재생목록이 이 상태로 남는데, 채널
        # 홈에 껍데기 목록이 진열돼 "관리 안 되는 채널"로 보이게 된다.
        empty = [p for p in playlists if not (playlist_members.get(p["id"], set()) & public_ids)]
        if empty:
            names = ", ".join(p["snippet"]["title"] for p in empty[:4])
            problems.append((2, f"공개 영상이 하나도 없는 재생목록 {len(empty)}개 ({names}) — 방문자에게 빈 목록으로 보임"))

    no_tags = [v for v in public if v["tag_count"] == 0]
    if no_tags:
        problems.append((2, f"태그가 없는 공개 영상 {len(no_tags)}편"))

    wrong_cat = [v for v in public if v["category_id"] and v["category_id"] != "10"]
    if wrong_cat:
        problems.append((2, f"카테고리가 음악(10)이 아닌 공개 영상 {len(wrong_cat)}편"))

    zero_len = [v for v in public if v["duration_seconds"] == 0]
    if zero_len:
        problems.append((1, f"재생시간을 읽지 못한 공개 영상 {len(zero_len)}편 (처리 중이거나 형식 이상)"))

    problems.sort(key=lambda p: -p[0])
    return problems


def build_report(channel, playlists, videos, playlist_members, problems):
    snippet = channel.get("snippet", {})
    stats = channel.get("statistics", {})
    branding = channel.get("brandingSettings", {}).get("channel", {})

    subs = int(stats.get("subscriberCount", 0))
    views = int(stats.get("viewCount", 0))
    public = [v for v in videos if v["privacy"] == "public"]
    total_seconds = sum(v["duration_seconds"] for v in public)

    lines = ["🔍 조선로파이 채널 점검", ""]

    lines.append("[채널 설정]")
    lines.append(f"이름: {snippet.get('title') or '(없음)'}")
    lines.append(f"핸들: {snippet.get('customUrl') or '(미설정)'}")
    desc = (branding.get("description") or snippet.get("description") or "").strip()
    lines.append(f"설명: {len(desc)}자" + (f" — {desc.splitlines()[0][:40]}" if desc else " (비어 있음)"))
    kw = (branding.get("keywords") or "").strip()
    lines.append(f"키워드: {kw if kw else '(비어 있음)'}")
    lines.append(f"국가: {snippet.get('country') or '(미설정)'}")
    lines.append("")

    lines.append("[규모]")
    lines.append(f"구독자 {subs}명 / 총 조회 {views}회 / 공개 영상 {len(public)}편 (전체 {len(videos)}편)")
    if public:
        avg = total_seconds / len(public)
        lines.append(f"공개 영상 길이 합계 {fmt_hms(total_seconds)} (평균 {fmt_hms(avg)})")
    lines.append("")

    lines.append("[재생목록]")
    if playlists:
        public_ids = {v["video_id"] for v in public}
        for p in playlists:
            n_public = len(playlist_members.get(p["id"], set()) & public_ids)
            total = p["contentDetails"]["itemCount"]
            flag = "  ⚠️ 공개 0편" if n_public == 0 else ""
            lines.append(f"· {p['snippet']['title']} — 공개 {n_public}편 / 전체 {total}편{flag}")
    else:
        lines.append("(없음)")
    lines.append("")

    lines.append("[수익화까지]")
    lines.append(f"구독자 {subs} / {YPP_SUBSCRIBERS}명")
    lines.append(f"시청시간은 Analytics 스코프가 있어야 측정됩니다 (목표 {YPP_WATCH_HOURS}시간)")
    if public and total_seconds:
        # 전편을 끝까지 본 사람 기준으로 4,000시간을 채우는 데 필요한 '완주 조회수'.
        # 실제 시청은 이보다 훨씬 짧으므로 이 숫자는 '최선의 경우'이고, 진짜 필요한
        # 조회수는 이것의 몇 배다. 낙관적 하한선으로만 읽어야 한다.
        need = YPP_WATCH_HOURS * 3600 / (total_seconds / len(public))
        lines.append(f"영상 평균 길이 기준, 전편 완주 시청이 최소 {need:,.0f}회 필요 (실제로는 훨씬 더)")
    lines.append("")

    lines.append("[고쳐야 할 것]")
    if problems:
        mark = {3: "🔴", 2: "🟡", 1: "⚪"}
        for sev, text in problems:
            lines.append(f"{mark[sev]} {text}")
    else:
        lines.append("채널 단위 설정에서 발견된 문제 없음")

    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="채널 설정 점검 (읽기 전용)")
    parser.add_argument("--telegram", action="store_true", help="결과를 텔레그램으로도 보낸다")
    args = parser.parse_args(argv)

    try:
        credentials = uy.get_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    youtube = build("youtube", "v3", credentials=credentials)
    channel = fetch_channel(youtube)
    playlists = fetch_playlists(youtube)
    videos = fetch_uploads(youtube, channel["contentDetails"]["relatedPlaylists"]["uploads"])

    playlist_members = {p["id"]: fetch_playlist_video_ids(youtube, p["id"]) for p in playlists}

    problems = diagnose(channel, playlists, videos, playlist_members)
    report = build_report(channel, playlists, videos, playlist_members, problems)
    print(report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("```\n" + report + "\n```\n")

    if args.telegram:
        import send_telegram_message as stm

        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            # 진단은 이미 로그·요약에 남았다. 알림 실패로 점검 자체를 실패시키지 않는다.
            print("경고: 텔레그램 환경변수가 없어 전송을 건너뜁니다", file=sys.stderr)
            return 0
        chunks = stm.split_message(report)
        for i, chunk in enumerate(chunks, 1):
            suffix = f"\n\n({i}/{len(chunks)})" if len(chunks) > 1 else ""
            stm.send(bot_token, chat_id, chunk + suffix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
