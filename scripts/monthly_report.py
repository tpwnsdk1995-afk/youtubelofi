"""매월 1일 발송하는 월간 리포트.

주간 리포트와 목적이 다르다. 주간은 표본이 작아 대부분 "아직 바꾸지 마세요"로
끝난다 — 조회수 한 자릿수 구간에서 무드별 차이는 노이즈이기 때문이다. 월간은
30일치를 모으므로 무드/그림체/상황 훅 비교가 통계적으로 의미를 갖기 시작하는
첫 시점이고, 그래서 **"다음 달 무엇을 바꿀 것인가"를 결정하는 자리**다.

집계 방식도 주간과 다르다:
  - 주간: 지난주 스냅샷 대비 '증가분'. 신작은 비교 대상이 없어 빠진다.
  - 월간: 이번 달에 올린 영상들의 '누적 조회수'를 그대로 쓴다. 전부 이번 달에
    0에서 시작했으므로 서로 공정하게 비교된다. 스냅샷 없이도 성립한다.

주간 리포트와 겹치는 조회/포맷 로직은 weekly_report에서 그대로 가져다 쓴다.
"""

import argparse
import calendar
import sys
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

import upload_youtube as uy
import weekly_report as wr
import youtube_analytics

KST = wr.KST

# 월간은 표본이 크므로 주간보다 기준을 높인다. 이 조건을 넘겨야 "바꿔라"를 말한다.
MIN_GROUP_VIDEOS = 5      # 비교하려는 각 그룹(무드/그림체)의 최소 영상 수
MIN_SITUATION_VIDEOS = 3  # 상황 훅은 종류가 많아 그룹당 표본이 작다
MIN_MONTH_VIEWS = 100     # 이 밑이면 아직 노출 단계 - 설정 변경 제안 보류


def previous_month_range(now):
    """리포트가 다루는 구간 = 직전 달 1일 00:00 ~ 말일 24:00 (KST).
    매월 1일에 실행되므로 '지난달 전체'가 대상이다."""
    first_of_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_of_prev = first_of_this - timedelta(seconds=1)
    first_of_prev = last_of_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first_of_prev, first_of_this


def videos_in_range(videos, start, end):
    """구간 안에 게시된 공개 영상만 (video_id -> 정보 + published)."""
    selected = {}
    for vid, v in videos.items():
        if v.get("privacyStatus") != "public":
            continue
        published = datetime.fromisoformat(v["publishedAt"].replace("Z", "+00:00")).astimezone(KST)
        if start <= published < end:
            selected[vid] = dict(v, published=published)
    return selected


def group_by_field(month_videos, recent_by_id, field, fallback=None):
    """이번 달 영상들을 축(무드/그림체/상황)별로 묶어 누적 조회수 합계·평균을 낸다."""
    groups = {}
    for vid, v in month_videos.items():
        entry = recent_by_id.get(vid) or {}
        key = entry.get(field)
        if not key and fallback:
            key = fallback(vid)
        if not key:
            continue
        bucket = groups.setdefault(key, {"count": 0, "total": 0})
        bucket["count"] += 1
        bucket["total"] += v.get("viewCount", 0)
    for bucket in groups.values():
        bucket["avg"] = bucket["total"] / bucket["count"] if bucket["count"] else 0
    return groups


def compare(groups, min_videos):
    """표본과 격차가 충분할 때만 (best, worst)를 준다. 아니면 None."""
    eligible = {k: v for k, v in groups.items() if v["count"] >= min_videos}
    if len(eligible) < 2:
        return None
    ranked = sorted(eligible.items(), key=lambda kv: -kv[1]["avg"])
    best, worst = ranked[0], ranked[-1]
    if worst[1]["avg"] <= 0:
        return (best, worst) if best[1]["avg"] > 0 else None
    if best[1]["avg"] / worst[1]["avg"] < wr.MEANINGFUL_RATIO:
        return None
    return best, worst


def build_monthly_recommendations(month_views, upload_count, expected_uploads,
                                  genre_groups, style_groups, situation_groups,
                                  channel_stats, channel_prev, library_counts):
    """월간 조치 제안. 주간과 달리 여기서는 실제로 '바꾸라'고 말할 수 있어야 한다 —
    다만 표본이 받쳐줄 때만."""
    lines = []

    # 1) 업로드 지속성 - 알고리즘이 가장 크게 보는 축
    if upload_count < expected_uploads:
        missed = expected_uploads - upload_count
        rate = upload_count / expected_uploads * 100
        lines.append(f"• 업로드 {upload_count}/{expected_uploads}편 (달성률 {rate:.0f}%, {missed}일 누락). "
                     f"매일 같은 시각 업로드가 노출에 가장 크게 작용합니다. "
                     f"누락이 5일 이상이면 원인(한도초과/워크플로우 실패)을 구조적으로 해결해야 합니다.")
    else:
        lines.append(f"• 업로드 {upload_count}/{expected_uploads}편 — 한 달 내내 끊기지 않았습니다. 이 리듬을 유지하세요.")

    # 2) 음원 재고
    if library_counts:
        low = [f"{n_}: {c}곡" for n_, c in sorted(library_counts.items()) if c < wr.RECOMMENDED_LIBRARY_SIZE]
        if low:
            lines.append(f"• 음원 보충 권장 ({', '.join(low)}) — 영상당 40~50곡을 쓰므로 "
                         f"무드별 {wr.RECOMMENDED_LIBRARY_SIZE}곡 이상이어야 영상 간 곡 중복이 줄어듭니다.")

    # 3) 표본 게이트 - 이 밑에서는 무엇을 바꿔도 효과를 측정할 수 없다
    if month_views < MIN_MONTH_VIEWS:
        lines.append(f"• 이번 달 신작 조회수 합계가 {month_views:,}회로, 아직 무드·그림체·문구의 "
                     f"우열을 가릴 수준이 아닙니다(기준 {MIN_MONTH_VIEWS:,}회). "
                     f"**설정을 바꾸지 마세요** — 지금 차이는 대부분 우연이고, 바꾸면 다음 달에 "
                     f"무엇이 효과였는지 알 수 없게 됩니다.")
        lines.append("• 이 단계에서 유효한 것은 세 가지뿐입니다: 매일 업로드 유지, 음원 보강, "
                     "그리고 노출수·클릭률 분석 켜기(SETUP.md 8절).")
        return lines

    # 4) 표본이 충분할 때만 나오는 실제 조정 지시
    genre_cmp = compare(genre_groups, MIN_GROUP_VIDEOS)
    if genre_cmp:
        (best, bv), (worst, wv) = genre_cmp
        lines.append(f"• **무드 조정**: {best} 평균 {bv['avg']:.0f}회 vs {worst} 평균 {wv['avg']:.0f}회. "
                     f"config/title_templates_joseon.yml의 genre_rotation에서 {best} 비중을 "
                     f"한 칸 늘리세요 (한 번에 한 칸씩만 — 여러 개를 동시에 바꾸면 원인 분리가 안 됩니다).")
    else:
        lines.append("• 무드(calm/groove) 간 유의미한 차이 없음 → genre_rotation 유지.")

    style_cmp = compare(style_groups, MIN_GROUP_VIDEOS)
    if style_cmp:
        (best, bv), (worst, wv) = style_cmp
        lines.append(f"• **그림체 조정**: {best} 평균 {bv['avg']:.0f}회 vs {worst} 평균 {wv['avg']:.0f}회. "
                     f"config/scenes_joseon.yml에서 {best} 씬을 늘리고 {worst} 씬을 줄이세요.")

    # 5) 상황 훅 - 월간에서만 볼 수 있는 해상도
    ranked_situations = sorted(
        ((k, v) for k, v in situation_groups.items() if v["count"] >= MIN_SITUATION_VIDEOS),
        key=lambda kv: -kv[1]["avg"],
    )
    if len(ranked_situations) >= 2:
        best_id, best_v = ranked_situations[0]
        worst_id, worst_v = ranked_situations[-1]
        lines.append(f"• **문구 조정**: 상황 훅 '{best_id}'가 평균 {best_v['avg']:.0f}회로 가장 강하고, "
                     f"'{worst_id}'가 {worst_v['avg']:.0f}회로 가장 약합니다. "
                     f"강한 쪽과 비슷한 결의 situations를 추가하고, 약한 쪽은 문구를 고치거나 빼세요.")

    # 6) 구독 전환
    if channel_prev and channel_stats["subscriberCount"] is not None:
        gained = channel_stats["subscriberCount"] - (channel_prev.get("subscriberCount") or 0)
        if month_views >= MIN_MONTH_VIEWS and gained == 0:
            lines.append("• 조회수는 나오는데 구독자 증가가 0입니다. 영상이 소비만 되고 채널로 "
                         "이어지지 않는 상태이니, 채널 아트·설명이 '구독할 이유'를 말하고 있는지 점검하세요.")

    return lines


def build_monthly_report(now, channel_stats, channel_prev, videos, recent_by_id, genre_lookup,
                         library_counts=None, analytics=None, analytics_error=None):
    start, end = previous_month_range(now)
    label = f"{start.year}년 {start.month}월"
    expected = calendar.monthrange(start.year, start.month)[1]

    month_videos = videos_in_range(videos, start, end)
    month_views = sum(v.get("viewCount", 0) for v in month_videos.values())
    month_likes = sum(v.get("likeCount") or 0 for v in month_videos.values())

    lines = [f"📅 조선로파이 {label} 월간 리포트", ""]

    # 채널 전체 (전월 스냅샷 대비)
    lines.append("[채널 누적]")
    sub_text = f"{wr.fmt_num(channel_stats['subscriberCount'])}명"
    view_text = f"{wr.fmt_num(channel_stats['viewCount'])}회"
    if channel_prev:
        if channel_stats["subscriberCount"] is not None and channel_prev.get("subscriberCount") is not None:
            sub_text += f" ({wr.fmt_delta(channel_stats['subscriberCount'] - channel_prev['subscriberCount'])})"
        view_text += f" ({wr.fmt_delta(channel_stats['viewCount'] - channel_prev.get('viewCount', channel_stats['viewCount']))})"
    lines.append(f"구독자: {sub_text}")
    lines.append(f"총 조회수: {view_text}")
    lines.append(f"공개 영상: {channel_stats['videoCount']}개")
    lines.append("")

    # 이번 달 성과
    lines.append(f"[{label} 성과]")
    lines.append(f"업로드: {len(month_videos)}/{expected}편")
    lines.append(f"신작 조회수 합계: {month_views:,}회")
    if month_videos:
        lines.append(f"영상당 평균: {month_views / len(month_videos):.1f}회")
    lines.append(f"좋아요 합계: {month_likes:,}개")
    lines.append("")

    description_by_id = {vid: v.get("description", "") for vid, v in month_videos.items()}
    genre_groups = group_by_field(
        month_videos, recent_by_id, "genre",
        fallback=lambda vid: wr.infer_genre(vid, recent_by_id, description_by_id, genre_lookup),
    )
    style_groups = group_by_field(month_videos, recent_by_id, "style")
    situation_groups = group_by_field(month_videos, recent_by_id, "situation_id")

    for title, groups in (("무드별", genre_groups), ("그림체별", style_groups)):
        if groups:
            lines.append(f"[{title} 성과 (누적 조회수)]")
            for name, b in sorted(groups.items(), key=lambda kv: -kv[1]["avg"]):
                lines.append(f"- {name}: {b['count']}편, 합계 {b['total']:,}회, 평균 {b['avg']:.1f}회/편")
            lines.append("")

    # TOP / 최저 (이번 달 신작만)
    if month_videos:
        ranked = sorted(month_videos.items(), key=lambda kv: -kv[1].get("viewCount", 0))
        lines.append("[이번 달 TOP 3]")
        for vid, v in ranked[:3]:
            short = v["title"][:38] + ("…" if len(v["title"]) > 38 else "")
            lines.append(f"- {v.get('viewCount', 0):,}회 · {short} · youtu.be/{vid}")
        lines.append("")
        if len(ranked) > 3:
            lines.append("[이번 달 하위 3]")
            for vid, v in ranked[-3:]:
                short = v["title"][:38] + ("…" if len(v["title"]) > 38 else "")
                lines.append(f"- {v.get('viewCount', 0):,}회 · {short} · youtu.be/{vid}")
            lines.append("")

    if library_counts:
        lines.append("[음원 재고 (Drive)]")
        for name, n in sorted(library_counts.items()):
            mark = " ⚠️ 보충 필요" if n < wr.RECOMMENDED_LIBRARY_SIZE else ""
            lines.append(f"- {name}: {n}곡{mark}")
        lines.append("")

    funnel = wr.diagnose_funnel(analytics)
    if funnel:
        lines.append("[원인 진단]")
        lines.extend(funnel)
        lines.append("")
    elif analytics_error:
        lines.append("[원인 진단]")
        lines.append(f"- 노출수/클릭률 분석이 꺼져 있습니다: {analytics_error}")
        lines.append("")

    recs = build_monthly_recommendations(
        month_views, len(month_videos), expected,
        genre_groups, style_groups, situation_groups,
        channel_stats, channel_prev, library_counts,
    )
    if recs:
        lines.append("[다음 달 조치]")
        lines.extend(recs)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="월간 채널 리포트를 생성해 텔레그램으로 보낸다")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--monthly-stats", default="state/monthly_stats.json")
    parser.add_argument("--templates-config", default="config/title_templates_joseon.yml")
    parser.add_argument("--output", default="monthly_report.txt")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 파일로만 저장")
    args = parser.parse_args()

    templates = wr.load_yaml(args.templates_config)
    genre_lookup = wr.build_genre_lookup(templates)

    state = wr.load_json(args.state, {})
    recent_by_id = {v["video_id"]: v for v in state.get("recent_videos", []) if v.get("video_id")}

    try:
        credentials = uy.get_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    youtube = build("youtube", "v3", credentials=credentials)

    channel_stats, uploads_playlist_id = wr.fetch_channel_stats(youtube)
    video_ids = wr.fetch_all_video_ids(youtube, uploads_playlist_id)
    videos = wr.fetch_video_details(youtube, video_ids)

    prev = wr.load_json(args.monthly_stats, {})
    channel_prev = prev.get("channel")

    library_counts = wr.fetch_library_counts()
    now = datetime.now(KST)
    analytics, analytics_error = youtube_analytics.safe_fetch(credentials, now)

    report = build_monthly_report(now, channel_stats, channel_prev, videos, recent_by_id,
                                  genre_lookup, library_counts=library_counts,
                                  analytics=analytics, analytics_error=analytics_error)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    wr.save_json(args.monthly_stats, {
        "snapshot_at": now.isoformat(),
        "channel": channel_stats,
    })

    if not args.dry_run:
        import send_telegram_message
        sys.argv = ["send_telegram_message.py", f"--text={report}"]
        send_telegram_message.main()


if __name__ == "__main__":
    main()
