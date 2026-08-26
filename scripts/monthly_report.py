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

# 유튜브 파트너 프로그램(수익화) 기준. 채널의 최종 목표이므로 매달 진척을 추적한다.
# 구독자 1,000명 + 최근 12개월 공개 영상 시청 4,000시간.
YPP_SUBSCRIBERS = 1000
YPP_WATCH_HOURS = 4000
# 도달 예상이 이 개월 수를 넘으면 숫자를 그대로 보여주는 게 무의미하다
# (실제로 "10,435개월 후 도달" = 870년이 나온 적이 있다). 대신 배수로 환산해
# "얼마나 더 나와야 하는지"를 말한다.
MAX_MEANINGFUL_ETA_MONTHS = 120


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


def weekly_breakdown(month_videos, start, end):
    """한 달을 주 단위로 쪼개 추이를 본다. 월 전체 합계만 보면 '월초엔 잘 되다가
    월말에 죽었는지' 같은 흐름이 안 보이기 때문에, 월간에서만 제공하는 해상도다."""
    if not month_videos:
        return []
    buckets = {}
    for vid, v in month_videos.items():
        week_index = (v["published"].day - 1) // 7 + 1  # 1~5주차
        b = buckets.setdefault(week_index, {"count": 0, "views": 0})
        b["count"] += 1
        b["views"] += v.get("viewCount", 0)

    lines = ["[주차별 추이]"]
    for week in sorted(buckets):
        b = buckets[week]
        avg = b["views"] / b["count"] if b["count"] else 0
        lines.append(f"- {week}주차: {b['count']}편, {b['views']:,}회 (편당 {avg:.1f}회)")

    ordered = [buckets[w]["views"] / buckets[w]["count"] for w in sorted(buckets) if buckets[w]["count"]]
    if len(ordered) >= 2:
        if ordered[-1] > ordered[0]:
            lines.append("→ 월말로 갈수록 편당 반응이 좋아졌습니다. 최근 방향이 맞습니다.")
        elif ordered[-1] < ordered[0]:
            lines.append("→ 월말로 갈수록 편당 반응이 떨어졌습니다. 후반부에 바뀐 것이 있는지 점검하세요.")
    return lines


def ranking_lines(groups, title, min_count, unit="편"):
    """축별 전체 순위표. 월간은 표본이 커서 best/worst만이 아니라 전체를 보여줄 수 있다."""
    eligible = [(k, v) for k, v in groups.items() if v["count"] >= min_count]
    if len(eligible) < 2:
        return []
    ranked = sorted(eligible, key=lambda kv: -kv[1]["avg"])
    lines = [f"[{title}]"]
    for rank, (name, b) in enumerate(ranked, 1):
        lines.append(f"{rank}. {name} — 평균 {b['avg']:.1f}회 ({b['count']}{unit}, 합계 {b['total']:,}회)")
    return lines


def monetization_progress(channel_stats, analytics, prev_channel):
    """수익화(YPP) 진척. 이 채널의 최종 목표이므로 매달 남은 거리를 보여준다.
    현재 증가 속도로 언제 도달하는지도 추정한다 (증가가 0이면 추정 불가로 명시)."""
    lines = ["[수익화 진척 (YouTube 파트너 프로그램)]"]

    subs = channel_stats.get("subscriberCount")
    if subs is None:
        lines.append("- 구독자 수가 비공개로 설정돼 있어 집계할 수 없습니다.")
    else:
        pct = subs / YPP_SUBSCRIBERS * 100
        lines.append(f"- 구독자: {subs:,} / {YPP_SUBSCRIBERS:,}명 ({pct:.1f}%) — {YPP_SUBSCRIBERS - subs:,}명 남음")
        if prev_channel and prev_channel.get("subscriberCount") is not None:
            gained = subs - prev_channel["subscriberCount"]
            if gained > 0:
                months_left = (YPP_SUBSCRIBERS - subs) / gained
                if months_left > MAX_MEANINGFUL_ETA_MONTHS:
                    # 989개월(82년) 같은 숫자는 산술적으로만 맞고 아무 판단에도 못 쓴다.
                    lines.append(f"  이번 달 +{gained:,}명. 이 속도로는 사실상 도달 불가입니다"
                                 f"(산술적으로 {months_left:,.0f}개월). "
                                 f"1년 안에 채우려면 월 +{(YPP_SUBSCRIBERS - subs) / 12:,.0f}명이 필요합니다.")
                else:
                    lines.append(f"  이번 달 +{gained:,}명. 이 속도가 유지되면 약 {months_left:.0f}개월 후 도달합니다.")
            else:
                lines.append("  이번 달 증가 없음 — 도달 시점을 추정할 수 없습니다.")

    if analytics:
        summary = analytics["summary"]
        watched_min = summary.get("estimatedMinutesWatched", 0) or 0
        hours = watched_min / 60
        lines.append(f"- 시청 시간: 이번 달 {hours:,.1f}시간 (기준 {YPP_WATCH_HOURS:,}시간 / 최근 12개월 누적)")
        if hours > 0:
            months_left = YPP_WATCH_HOURS / hours
            if months_left > MAX_MEANINGFUL_ETA_MONTHS:
                lines.append(f"  현재 속도로는 사실상 도달 불가입니다(산술적으로 {months_left:,.0f}개월). "
                             f"기준을 채우려면 월 시청 시간이 지금의 "
                             f"{YPP_WATCH_HOURS / 12 / hours:,.0f}배는 되어야 합니다 — "
                             f"편수보다 '한 편이 오래 재생되는 것'이 관건입니다.")
            else:
                lines.append(f"  이 속도가 유지되면 약 {months_left:.0f}개월 후 기준을 채웁니다.")
        avg_seconds = summary.get("averageViewDuration", 0) or 0
        views = summary.get("views", 0) or 0
        if views:
            lines.append(f"- 평균 시청 지속: {wr.fmt_duration(avg_seconds)} (조회수 {views:,}회 기준)")
    else:
        lines.append(f"- 시청 시간: 집계 불가 (Analytics 스코프 미승인). 기준은 최근 12개월 {YPP_WATCH_HOURS:,}시간입니다.")

    return lines


def channel_wide_ranking(videos, recent_by_id, top_n=5, bottom_n=3, genre_fn=None):
    """채널에 공개된 전체 영상을 조회수로 줄 세운다.

    월간은 '그 달'만 보면 안 된다. 그 달에 업로드가 없었어도 채널은 계속 돌아가고,
    사장님이 알아야 할 것은 '지금까지 올린 것 중 무엇이 먹혔나'이기 때문이다.
    주간에는 없는, 월간 전용 관점이다."""
    public = {vid: v for vid, v in videos.items() if v.get("privacyStatus") == "public"}
    if len(public) < 2:
        return []

    ranked = sorted(public.items(), key=lambda kv: -kv[1].get("viewCount", 0))
    total_views = sum(v.get("viewCount", 0) for v in public.values())
    avg = total_views / len(public)

    lines = [f"[채널 전체 성과 (공개 {len(public)}편 누적)]",
             f"누적 조회수 {total_views:,}회 · 편당 평균 {avg:.1f}회"]

    def label(vid):
        # recent_videos에 genre가 없는 옛 영상은 설명란으로 역추정한다.
        # (그냥 "?"로 두면 랭킹을 보고도 어떤 무드가 먹혔는지 알 수 없다)
        entry = recent_by_id.get(vid) or {}
        tag = entry.get("genre")
        if not tag and genre_fn:
            tag = genre_fn(vid)
        return tag or "무드 미상"

    lines.append(f"▶ 상위 {min(top_n, len(ranked))}편")
    for rank, (vid, v) in enumerate(ranked[:top_n], 1):
        short = v["title"][:34] + ("…" if len(v["title"]) > 34 else "")
        lines.append(f"  {rank}. {v.get('viewCount', 0):,}회 ({label(vid)}) {short}")

    if len(ranked) > top_n + bottom_n:
        lines.append(f"▶ 하위 {bottom_n}편")
        for vid, v in ranked[-bottom_n:]:
            short = v["title"][:34] + ("…" if len(v["title"]) > 34 else "")
            lines.append(f"  · {v.get('viewCount', 0):,}회 ({label(vid)}) {short}")

    top_views = ranked[0][1].get("viewCount", 0)
    if avg > 0 and top_views >= avg * 2:
        lines.append(f"→ 1위가 평균의 {top_views / avg:.1f}배입니다. 이 영상의 제목·무드·씬을 "
                     f"다음 달 기획의 기준선으로 삼으세요.")
    return lines


def month_over_month(month_views, month_uploads, prev_snapshot):
    """전월 대비 성장률. 월간은 '지난달보다 나아졌나'가 핵심 질문이다."""
    prev = (prev_snapshot or {}).get("last_month") or {}
    prev_views = prev.get("month_views")
    prev_uploads = prev.get("month_uploads")
    if prev_views is None:
        return []

    lines = ["[전월 대비]"]
    if prev_views > 0:
        change = (month_views - prev_views) / prev_views * 100
        arrow = "▲" if change >= 0 else "▼"
        lines.append(f"- 신작 조회수: {prev_views:,}회 → {month_views:,}회 ({arrow} {abs(change):.0f}%)")
    else:
        lines.append(f"- 신작 조회수: {prev_views:,}회 → {month_views:,}회")
    if prev_uploads is not None:
        lines.append(f"- 업로드: {prev_uploads}편 → {month_uploads}편")
    return lines


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


def monthly_traffic_lines(analytics):
    """한 달 치 유입 지표. 주간에도 같은 블록이 있지만 월간은 표본이 4~5배라
    여기 숫자는 실제로 의사결정에 쓸 수 있는 수준이다."""
    if not analytics:
        return []
    summary = analytics["summary"]
    start_date, end_date = analytics["period"]
    lines = [f"[유입 지표 ({start_date} ~ {end_date})]"]
    impressions = summary.get("videoThumbnailImpressions")
    ctr = summary.get("videoThumbnailImpressionsClickRate")
    if impressions is not None:
        lines.append(f"노출수: {impressions:,}회")
    if ctr is not None:
        lines.append(f"노출 클릭률(CTR): {ctr:.2f}%")
    views = summary.get("views", 0) or 0
    watched = summary.get("estimatedMinutesWatched", 0) or 0
    avg_seconds = summary.get("averageViewDuration", 0) or 0
    lines.append(f"조회수: {views:,}회")
    lines.append(f"평균 시청 시간: {wr.fmt_duration(avg_seconds)}")
    lines.append(f"총 시청 시간: {watched:,.0f}분 ({watched / 60:,.1f}시간)")
    if analytics.get("traffic_sources"):
        lines.append("")
        lines.append("[유입 경로 (한 달 누적)]")
        lines.extend(wr.format_traffic_sources(analytics["traffic_sources"]))
        top = max(analytics["traffic_sources"].items(), key=lambda kv: kv[1])
        total = sum(analytics["traffic_sources"].values()) or 1
        share = top[1] / total * 100
        if top[0] == "YT_SEARCH" and share >= 40:
            lines.append("→ 검색 의존도가 높습니다. 추천·탐색 유입이 붙어야 규모가 커지므로 "
                         "제목의 검색 키워드는 유지하되, 같은 무드를 연달아 올려 "
                         "'다음 영상' 추천이 걸리게 하는 편이 낫습니다.")
        elif top[0] == "EXT_URL" and share >= 40:
            lines.append("→ 외부 유입 비중이 큽니다. 유튜브 내부 노출이 아직 안 붙은 상태이니 "
                         "제목·태그의 검색 키워드를 점검하세요.")
    return lines


def build_next_month_plan(month_uploads, expected_uploads, library_counts, analytics,
                          analytics_error, month_views, channel_stats):
    """다음 달 실행 계획. 주간과 같은 3분할을 쓰되, 월간에는 '다음 달 판정 기준'을
    숫자로 못박는다 — 한 달 뒤 이 리포트가 스스로를 채점할 수 있어야 하기 때문이다."""
    manual = []
    auto = ["매일 17:15 영상 생성 → 19:14 자동 공개 (응답 안 하셔도 공개됨)",
            "매주 월요일 09:00 주간 리포트",
            "매달 1일 09:00 월간 리포트"]

    if library_counts:
        for name, n in sorted(library_counts.items()):
            if n < wr.RECOMMENDED_LIBRARY_SIZE:
                need = wr.RECOMMENDED_LIBRARY_SIZE - n
                manual.append(f"Suno에서 {name} 음원 {need}곡 이상 뽑아 Drive의 {name}/ 폴더에 넣기 "
                              f"(현재 {n}곡 / 권장 {wr.RECOMMENDED_LIBRARY_SIZE}곡)")
    if analytics_error:
        manual.append("노출수·클릭률 분석 켜기 — SETUP.md 8절 재인증")
    if month_uploads < expected_uploads:
        manual.append(f"업로드 누락 {expected_uploads - month_uploads}일분 원인 확인 "
                      f"(한도초과 / 워크플로우 실패 구분)")

    lines = ["[다음 달 계획]"]
    lines.append("▶ 사장님이 하실 일")
    if manual:
        for i, item in enumerate(manual, 1):
            lines.append(f"  {i}. {item}")
    else:
        lines.append("  없음 — 이번 달은 손댈 것이 없습니다.")
    lines.append("▶ 자동으로 처리됨")
    for item in auto:
        lines.append(f"  · {item}")

    dont = []
    if month_views < MIN_MONTH_VIEWS:
        dont.append("무드 비중·씬·썸네일 문구 수정 "
                    f"(한 달 신작 조회수 {month_views:,}회 — {MIN_MONTH_VIEWS}회 미만이라 "
                    "바꿔도 효과를 측정할 수 없습니다)")
        dont.append("밀린 날짜 몰아서 업로드 (하루 1편 초과는 반복 콘텐츠로 잡힙니다)")
    if dont:
        lines.append("▶ 하지 말아야 할 일")
        for item in dont:
            lines.append(f"  · {item}")

    # 다음 달 판정 기준 - 월간에만 있는 항목
    lines.append("▶ 다음 달 이 리포트가 볼 숫자")
    target_uploads = 28
    lines.append(f"  · 업로드 {target_uploads}편 이상 (이번 달 {month_uploads}편)")
    if month_views > 0:
        lines.append(f"  · 신작 조회수 {int(month_views * 1.5):,}회 이상 (이번 달 대비 +50%)")
    else:
        lines.append(f"  · 신작 조회수 {MIN_MONTH_VIEWS}회 이상 (진단을 낼 수 있는 최소 표본)")
    subs = channel_stats.get("subscriberCount")
    if subs is not None:
        lines.append(f"  · 구독자 {subs + 10:,}명 이상 (현재 {subs:,}명)")
    return lines


def build_monthly_report(now, channel_stats, channel_prev, videos, recent_by_id, genre_lookup,
                         library_counts=None, analytics=None, analytics_error=None,
                         prev_snapshot=None):
    start, end = previous_month_range(now)
    label = f"{start.year}년 {start.month}월"
    expected = calendar.monthrange(start.year, start.month)[1]

    month_videos = videos_in_range(videos, start, end)
    month_views = sum(v.get("viewCount", 0) for v in month_videos.values())
    month_likes = sum(v.get("likeCount") or 0 for v in month_videos.values())

    lines = [f"📅 조선로파이 {label} 월간 리포트", ""]
    # 헤드라인은 집계가 끝난 뒤 lines[1]에 끼워 넣는다 (아래 참조)

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

    mom = month_over_month(month_views, len(month_videos), prev_snapshot)
    if mom:
        lines.extend(mom)
        lines.append("")

    # 이번 달 성과
    lines.append(f"[{label} 성과]")
    lines.append(f"업로드: {len(month_videos)}/{expected}편")
    lines.append(f"신작 조회수 합계: {month_views:,}회")
    if month_videos:
        lines.append(f"영상당 평균: {month_views / len(month_videos):.1f}회")
    lines.append(f"좋아요 합계: {month_likes:,}개")
    lines.append("")

    traffic = monthly_traffic_lines(analytics)
    if traffic:
        lines.extend(traffic)
        lines.append("")

    description_by_id = {vid: v.get("description", "") for vid, v in month_videos.items()}
    genre_groups = group_by_field(
        month_videos, recent_by_id, "genre",
        fallback=lambda vid: wr.infer_genre(vid, recent_by_id, description_by_id, genre_lookup),
    )
    style_groups = group_by_field(month_videos, recent_by_id, "style")
    situation_groups = group_by_field(month_videos, recent_by_id, "situation_id")

    scene_groups = group_by_field(month_videos, recent_by_id, "scene_id")

    week_lines = weekly_breakdown(month_videos, start, end)
    if week_lines:
        lines.extend(week_lines)
        lines.append("")

    for groups, title, min_count in (
        (genre_groups, "무드별 순위", 1),
        (style_groups, "그림체별 순위", 1),
        (situation_groups, "제목 훅(상황)별 순위", MIN_SITUATION_VIDEOS),
        (scene_groups, "씬별 순위", MIN_SITUATION_VIDEOS),
    ):
        block = ranking_lines(groups, title, min_count)
        if block:
            lines.extend(block)
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

    all_descriptions = {vid: v.get("description", "") for vid, v in videos.items()}
    wide = channel_wide_ranking(
        videos, recent_by_id,
        genre_fn=lambda vid: wr.infer_genre(vid, recent_by_id, all_descriptions, genre_lookup),
    )
    if wide:
        lines.extend(wide)
        lines.append("")

    lines.extend(monetization_progress(channel_stats, analytics, channel_prev))
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
        lines.append("[다음 달 실행 계획]")
        lines.extend(recs)
        lines.append("")

    lines.extend(build_next_month_plan(
        len(month_videos), expected, library_counts, analytics, analytics_error,
        month_views, channel_stats,
    ))

    sub_gain = None
    if channel_prev and channel_stats["subscriberCount"] is not None \
            and channel_prev.get("subscriberCount") is not None:
        sub_gain = channel_stats["subscriberCount"] - channel_prev["subscriberCount"]
    parts = [f"업로드 {len(month_videos)}/{expected}편", f"신작 조회수 {month_views:,}회"]
    if sub_gain is not None:
        parts.append(f"구독자 {wr.fmt_delta(sub_gain)}명")
    if month_videos:
        parts.append(f"편당 평균 {month_views / len(month_videos):.1f}회")
    lines.insert(1, "한 줄 요약: " + " · ".join(parts))

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
    # 월간은 '지난달 전체'가 대상이므로 분석 구간도 그 달에 맞춘다.
    # (주간의 기본값인 최근 7일을 쓰면 월간 리포트가 엉뚱한 구간을 진단한다)
    m_start, m_end = previous_month_range(now)
    analytics, analytics_error = youtube_analytics.safe_fetch(
        credentials, now,
        start_date=m_start.date().isoformat(),
        end_date=(m_end - timedelta(days=1)).date().isoformat(),
    )

    report = build_monthly_report(now, channel_stats, channel_prev, videos, recent_by_id,
                                  genre_lookup, library_counts=library_counts,
                                  analytics=analytics, analytics_error=analytics_error,
                                  prev_snapshot=prev)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    # 다음 달 리포트가 "전월 대비"를 계산하려면 이번 달 신작 실적을 남겨야 한다.
    m_videos = videos_in_range(videos, m_start, m_end)
    wr.save_json(args.monthly_stats, {
        "snapshot_at": now.isoformat(),
        "channel": channel_stats,
        "last_month": {
            "label": f"{m_start.year}-{m_start.month:02d}",
            "month_views": sum(v.get("viewCount", 0) for v in m_videos.values()),
            "month_uploads": len(m_videos),
        },
    })

    if not args.dry_run:
        import send_telegram_message
        sys.argv = ["send_telegram_message.py", f"--text={report}"]
        send_telegram_message.main()


if __name__ == "__main__":
    main()
