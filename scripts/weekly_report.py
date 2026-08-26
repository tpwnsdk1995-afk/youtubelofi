"""매주 채널 반응을 집계해 텔레그램으로 보내는 주간 리포트.

조회수 등 유튜브 API가 주는 값은 항상 "현재까지 누적"이라 최근 7일간의 증가분을
알려면 지난 주 실행 시점의 스냅샷이 필요하다. 그래서 매 실행마다
`state/weekly_stats.json`에 그 시점의 채널/영상별 누적 수치를 저장해 두고,
다음 실행에서 그 값과의 차이를 "이번 주 증가분"으로 계산한다. 스냅샷이 없는
첫 실행이나 신규 영상은 증가분 대신 누적값을 그대로 보여준다.

무드(calm/groove)는 state.json의 recent_videos에 기록이 있으면 그대로 쓰고,
(리포트 도입 이전에 올라간) 옛 영상은 설명란 앞부분의 top_hashtags 문자열로
역추정한다 - config/title_templates_joseon.yml에 정의된 문자열과 대조한다.

리포트 끝에는 "이번 주 조치 제안"을 붙인다. 표본이 적을 때 성급하게 설정을 바꾸면
노이즈를 신호로 착각하게 되므로, 무드/그림체 비교 기반 제안은 주간 조회수 증가가
MIN_TOTAL_DELTA 이상이고 각 그룹에 MIN_GROUP_VIDEOS개 이상 쌓였을 때만 낸다.
그 전까지는 "아직 판단하지 말 것"을 명시적으로 알려준다.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from googleapiclient.discovery import build

import sync_music_library
import upload_youtube as uy
import youtube_analytics

KST = timezone(timedelta(hours=9))

# 조치 제안을 낼지 말지 가르는 최소 표본. 이 밑에서는 무드/그림체 차이가 대부분
# 노이즈라 설정 변경을 권하지 않는다.
MIN_TOTAL_DELTA = 30      # 주간 채널 전체 조회수 증가분
MIN_GROUP_VIDEOS = 3      # 비교하려는 각 그룹(무드/그림체)의 최소 영상 수
MEANINGFUL_RATIO = 1.5    # 이 배수 이상 차이 나야 "유의미한 차이"로 본다

# 라이브러리 권장 규모. 한 영상이 40~50곡을 쓰는데 라이브러리가 이보다 작으면
# 영상 간 곡 중복이 심해져 유튜브 반복 콘텐츠 정책에 불리하다.
RECOMMENDED_LIBRARY_SIZE = 100

# 퍼널 진단 기준. 유튜브 공식 도움말이 "대부분 채널의 노출 클릭률은 2~10%"라고
# 밝히고 있어 2%를 썸네일 개선 신호로 삼는다.
# (support.google.com/youtube/answer/9314486)
MIN_WEEKLY_IMPRESSIONS = 1000   # 이 밑이면 노출 자체가 없는 단계 = 콘텐츠 문제 아님
LOW_CTR = 2.0                   # % - 노출은 되는데 안 눌린다 = 썸네일 문제
LOW_VIEW_MINUTES = 3.0          # 분 - 눌렀는데 바로 나간다 = 음악/도입부 문제


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


def fetch_library_counts():
    """Drive 음악 라이브러리의 무드별 곡 수를 센다 (다운로드 없이 목록 조회만).
    자격 증명이 없으면 None을 반환해 리포트에서 해당 섹션을 생략한다."""
    sa_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    folder_id = os.environ.get("GDRIVE_MUSIC_FOLDER_ID")
    if not sa_json or not folder_id:
        return None
    try:
        service = sync_music_library.get_drive_service(sa_json)
        counts = {}
        for folder in sync_music_library.list_subfolders(service, folder_id):
            counts[folder["name"]] = len(sync_music_library.list_library_files(service, folder["id"]))
        return counts
    except Exception as e:  # 라이브러리 조회 실패가 리포트 전체를 막으면 안 된다
        print(f"WARNING: Drive 라이브러리 조회 실패: {e}", file=sys.stderr)
        return None


def group_stats(deltas, recent_by_id, field, fallback=None):
    """영상별 조회수 증가분을 특정 축(무드/그림체 등)으로 묶어 합계·평균을 낸다.
    fallback(video_id)이 주어지면 state 기록이 없는 옛 영상도 분류에 포함한다."""
    groups = {}
    for vid, delta in deltas.items():
        entry = recent_by_id.get(vid) or {}
        key = entry.get(field)
        if not key and fallback:
            key = fallback(vid)
        if not key:
            continue
        bucket = groups.setdefault(key, {"count": 0, "total": 0})
        bucket["count"] += 1
        bucket["total"] += delta
    for bucket in groups.values():
        bucket["avg"] = bucket["total"] / bucket["count"] if bucket["count"] else 0
    return groups


def compare_groups(groups):
    """가장 잘 된 그룹과 가장 안 된 그룹을 (충분한 표본이 있을 때만) 반환한다.
    표본이 모자라거나 차이가 미미하면 None."""
    eligible = {k: v for k, v in groups.items() if v["count"] >= MIN_GROUP_VIDEOS}
    if len(eligible) < 2:
        return None
    ranked = sorted(eligible.items(), key=lambda kv: -kv[1]["avg"])
    best, worst = ranked[0], ranked[-1]
    if worst[1]["avg"] <= 0:
        # 0으로 나눌 수 없고, 한쪽이 아예 0이면 차이는 명백하다
        return (best, worst) if best[1]["avg"] > 0 else None
    if best[1]["avg"] / worst[1]["avg"] < MEANINGFUL_RATIO:
        return None
    return best, worst


def diagnose_funnel(analytics, prev_summary=None):
    """노출 -> 클릭 -> 시청 순서로 어디서 막히는지 짚는다.
    각 단계는 조치가 완전히 다르므로, 어느 단계가 병목인지 아는 것이 핵심이다.
    (분석 데이터가 없으면 빈 리스트)"""
    if not analytics:
        return []

    summary = analytics["summary"]
    impressions = summary.get("videoThumbnailImpressions")
    ctr = summary.get("videoThumbnailImpressionsClickRate")
    avg_seconds = summary.get("averageViewDuration", 0) or 0
    avg_minutes = avg_seconds / 60
    views = summary.get("views", 0) or 0
    subs = summary.get("subscribersGained", 0) or 0

    lines = []

    # 지난 주 대비 추세를 먼저 (개선되고 있는지가 사용자의 핵심 관심사)
    if prev_summary:
        prev_imp = prev_summary.get("videoThumbnailImpressions")
        if impressions is not None and prev_imp:
            change = (impressions - prev_imp) / prev_imp * 100
            direction = "증가" if change >= 0 else "감소"
            lines.append(f"- 노출수 추세: 지난주 대비 {abs(change):.0f}% {direction}")

    if impressions is None:
        # 노출 지표를 못 받은 경우(스코프/지표 미지원) - 시청 시간만으로 판단
        if views and avg_minutes < LOW_VIEW_MINUTES:
            lines.append(f"- 평균 시청 {avg_minutes:.1f}분으로 짧습니다. 도입부 곡이 "
                         f"바로 붙잡지 못하고 있을 가능성이 큽니다.")
        return lines

    if impressions < MIN_WEEKLY_IMPRESSIONS:
        lines.append(f"- **병목: 노출 단계** (주간 노출 {impressions:,}회). 알고리즘이 아직 "
                     f"우리 영상을 사람들에게 보여주지 않는 단계입니다. 썸네일이나 음악의 "
                     f"문제가 아니므로 그쪽을 고쳐도 지금은 숫자가 안 움직입니다.")
        lines.append("- 이 단계에서 유효한 것: 업로드 일관성 유지, 제목/태그의 검색 키워드, "
                     "재생목록 정리. 시간이 필요한 구간입니다.")
    elif ctr is not None and ctr < LOW_CTR:
        lines.append(f"- **병목: 썸네일** (노출 {impressions:,}회는 충분한데 클릭률 {ctr:.1f}%). "
                     f"유튜브가 보여주고는 있는데 사람들이 안 누릅니다. "
                     f"대부분 채널이 2~10% 구간이라 개선 여지가 큽니다.")
        lines.append("- 조치: config/title_templates_joseon.yml의 thumb_main 문구를 더 짧고 "
                     "굵게, 또는 config/scenes_joseon.yml에서 인물/표정이 큰 씬 비중을 늘리세요.")
    elif avg_minutes < LOW_VIEW_MINUTES:
        lines.append(f"- **병목: 음악/도입부** (클릭률 {ctr:.1f}%로 양호한데 평균 시청 "
                     f"{avg_minutes:.1f}분). 눌러서 들어왔다가 바로 나갑니다.")
        lines.append("- 조치: 첫 곡이 튀거나 이질적일 수 있습니다. Suno에서 도입부용으로 "
                     "잔잔하게 시작하는 곡을 보강해 주세요.")
    else:
        lines.append(f"- 퍼널 정상: 노출 {impressions:,}회 → 클릭률 {ctr:.1f}% → 평균 시청 "
                     f"{avg_minutes:.1f}분. 이 조합이면 볼륨을 늘리는 게 맞습니다.")
        if views and subs == 0:
            lines.append("- 다만 구독 전환이 0입니다. 채널 아트/설명이 '이 채널을 구독할 이유'를 "
                         "충분히 말하고 있는지 점검해 보세요.")

    return lines


def format_traffic_sources(sources):
    """유입 경로를 사람이 읽는 이름으로."""
    names = {
        "YT_SEARCH": "유튜브 검색",
        "RELATED_VIDEO": "추천 영상",
        "BROWSE": "탐색 기능(홈/구독)",
        "PLAYLIST": "재생목록",
        "NOTIFICATION": "알림",
        "EXT_URL": "외부 링크",
        "CHANNEL": "채널 페이지",
        "NO_LINK_OTHER": "기타",
        "SUBSCRIBER": "구독 피드",
    }
    total = sum(sources.values()) or 1
    lines = []
    for key, views in sorted(sources.items(), key=lambda kv: -kv[1])[:5]:
        label = names.get(key, key)
        lines.append(f"- {label}: {views:,}회 ({views / total * 100:.0f}%)")
    return lines


def build_recommendations(channel_stats, channel_prev, total_delta, genre_groups,
                          style_groups, new_video_count, library_counts, history):
    """리포트 하단의 "이번 주 조치 제안". 데이터가 뒷받침하는 것만 제안하고,
    표본이 모자라면 '아직 바꾸지 말 것'을 분명히 말한다."""
    lines = []

    if channel_prev is None:
        lines.append("• 이번이 첫 리포트라 비교 기준만 저장했습니다. 증감 기반 제안은 다음 주부터 나옵니다.")
        lines.append("• 이번 주에는 설정을 바꾸지 마세요 — 비교할 대상이 없어 무엇이 효과였는지 알 수 없습니다.")
        return lines

    # 1) 업로드 지속성 — 알고리즘 학습에 가장 기본이 되는 축
    if new_video_count < 7:
        missed = 7 - new_video_count
        lines.append(f"• 이번 주 업로드 {new_video_count}/7편 ({missed}일 누락). 매일 같은 시각 업로드가 "
                     f"노출에 가장 크게 작용하니 누락 원인(워크플로우 실패/한도초과)을 확인하세요.")

    # 2) 음원 재고 — 곡이 모자라면 영상 간 중복이 늘어 반복 콘텐츠 정책에 불리
    if library_counts:
        low = [f"{name} {n}곡" for name, n in sorted(library_counts.items()) if n < RECOMMENDED_LIBRARY_SIZE]
        if low:
            lines.append(f"• 음원 보충 권장: {', '.join(low)} (영상당 40~50곡 사용 → "
                         f"무드별 {RECOMMENDED_LIBRARY_SIZE}곡 이상이어야 영상 간 곡 중복이 줄어듭니다). "
                         f"Suno에서 뽑아 Drive의 해당 무드 폴더에 넣어주세요.")

    # 3) 표본이 부족하면 여기서 멈춘다 — 이 구간의 무드/그림체 차이는 노이즈다
    if total_delta < MIN_TOTAL_DELTA:
        lines.append(f"• 주간 조회수 증가가 {total_delta}회로 아직 통계적으로 의미 있는 수준이 아닙니다"
                     f"(기준 {MIN_TOTAL_DELTA}회). **무드 비중·씬·썸네일 설정을 지금 바꾸지 마세요** — "
                     f"지금 차이는 대부분 우연입니다.")
        flat_weeks = count_flat_weeks(history)
        if flat_weeks >= 3:
            lines.append(f"• 다만 {flat_weeks}주 연속 노출이 거의 없습니다. 이건 콘텐츠 문제가 아니라 "
                         f"'채널이 아직 발견되지 않은' 단계일 가능성이 큽니다. 업로드를 계속 쌓으면서, "
                         f"바꾼다면 한 번에 하나씩(예: 썸네일 문구 길이)만 바꿔 효과를 분리하세요.")
        else:
            lines.append("• 지금 할 일은 하나뿐입니다: 매일 업로드를 끊지 않고 표본을 쌓기.")
        return lines

    # 4) 표본이 충분할 때만 나오는 실제 조정 제안
    genre_cmp = compare_groups(genre_groups)
    if genre_cmp:
        (best_name, best), (worst_name, worst) = genre_cmp
        lines.append(f"• 무드 성과 차이가 뚜렷합니다: {best_name} 평균 {best['avg']:.0f}회 vs "
                     f"{worst_name} 평균 {worst['avg']:.0f}회. "
                     f"config/title_templates_joseon.yml의 genre_rotation에서 {best_name} 비중을 "
                     f"한 칸 늘리는 것을 검토하세요(한 번에 한 칸씩만).")
        lines.append(f"• {best_name} 비중을 늘리면 그 무드 음원 소비가 빨라집니다. Drive의 "
                     f"{best_name}/ 폴더를 먼저 채워두세요.")
    else:
        lines.append("• 무드(calm/groove) 간 유의미한 성과 차이는 아직 없습니다. genre_rotation 유지.")

    style_cmp = compare_groups(style_groups)
    if style_cmp:
        (best_name, best), (worst_name, worst) = style_cmp
        lines.append(f"• 그림체별 차이: {best_name} 평균 {best['avg']:.0f}회 vs {worst_name} 평균 "
                     f"{worst['avg']:.0f}회. config/scenes_joseon.yml에서 {best_name} 스타일을 쓰는 "
                     f"씬을 늘리고 {worst_name} 씬을 줄이는 것을 검토하세요.")

    if channel_stats["subscriberCount"] == 0:
        lines.append("• 조회수는 나오는데 구독자가 0입니다. 영상 길이 대비 이탈 지점(유튜브 스튜디오 "
                     "'시청 지속 시간')을 한 번 확인해 보세요 — 도입부 30초가 문제일 수 있습니다.")

    return lines


def should_append_history(history, now, min_days=6):
    """추세 기록은 주 1회만 남긴다. 진단 목적의 수동 재실행이 같은 주에 여러 항목을
    쌓으면 '몇 주째 정체' 판단이 망가지기 때문이다."""
    if not history:
        return True
    last_at = history[-1].get("at")
    if not last_at:
        return True
    try:
        last = datetime.fromisoformat(last_at)
    except ValueError:
        return True
    return (now - last) >= timedelta(days=min_days)


def count_flat_weeks(history):
    """최근 몇 주 연속으로 주간 조회수 증가가 MIN_TOTAL_DELTA 미만이었는지 센다."""
    if not history:
        return 0
    flat = 0
    for entry in reversed(history):
        delta = entry.get("weekly_view_delta")
        if delta is None or delta >= MIN_TOTAL_DELTA:
            break
        flat += 1
    return flat


def fmt_delta(n):
    if n is None:
        return "(비교 데이터 없음)"
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:,}"


def fmt_num(n):
    return "?" if n is None else f"{n:,}"


def fmt_avg(n):
    """평균이 1 미만일 때 '+0회'로 뭉개지지 않도록 작은 값은 소수점 한 자리로 보여준다."""
    return f"{n:+.1f}" if abs(n) < 10 else f"{n:+.0f}"


def build_headline(channel_stats, sub_delta, view_delta, new_video_count, analytics):
    """리포트 맨 위 한 줄 요약. 바빠서 이것만 읽어도 이번 주가 어땠는지 알 수 있어야 한다."""
    bits = []
    if view_delta is not None:
        bits.append(f"조회수 {fmt_delta(view_delta)}회")
    if sub_delta is not None:
        bits.append(f"구독자 {fmt_delta(sub_delta)}명")
    bits.append(f"업로드 {new_video_count}/7편")
    if analytics:
        imp = analytics["summary"].get("videoThumbnailImpressions")
        if imp is not None:
            bits.append(f"노출 {imp:,}회")
    return "한 줄 요약: " + " · ".join(bits)


def week_trend_lines(history, weeks=4):
    """최근 몇 주 조회수 증가 추이. 이번 주가 좋아지는 중인지 나빠지는 중인지 본다."""
    entries = [h for h in history or [] if h.get("weekly_view_delta") is not None]
    if len(entries) < 2:
        return []
    recent = entries[-weeks:]
    lines = ["[최근 추이 (주간 조회수 증가)]"]
    for e in recent:
        at = e.get("at", "")[:10]
        lines.append(f"- {at}: {fmt_delta(e['weekly_view_delta'])}회 (누적 구독 {fmt_num(e.get('subscriberCount'))}명)")
    first, last = recent[0]["weekly_view_delta"], recent[-1]["weekly_view_delta"]
    if last > first:
        lines.append("→ 증가 추세입니다. 지금 하는 것을 유지하세요.")
    elif last < first:
        lines.append("→ 감소 추세입니다. 업로드 누락이 없었는지부터 확인하세요.")
    else:
        lines.append("→ 변동 없음.")
    return lines


def build_next_week_plan(new_video_count, library_counts, analytics, analytics_error, total_delta):
    """다음 주 실행 계획. 사장님이 직접 하실 일과 자동으로 되는 일을 분리해
    '내가 뭘 해야 하지'가 한눈에 보이게 한다."""
    manual = []
    auto = ["매일 17:15 영상 생성 → 19:14 자동 공개 (응답 안 하셔도 공개됨)",
            "다음 월요일 09:00 주간 리포트 발송"]

    if library_counts:
        for name, n in sorted(library_counts.items()):
            if n < RECOMMENDED_LIBRARY_SIZE:
                need = RECOMMENDED_LIBRARY_SIZE - n
                manual.append(f"Suno에서 {name} 음원 {need}곡 이상 뽑아 Drive의 {name}/ 폴더에 넣기 (현재 {n}곡)")
    if analytics_error:
        manual.append("노출수·클릭률 분석 켜기 — SETUP.md 8절 재인증 (안 켜면 원인 진단이 계속 비어 있습니다)")
    if new_video_count < 7:
        manual.append(f"업로드 누락 {7 - new_video_count}일분 원인 확인 (한도초과였는지, 워크플로우 실패였는지)")

    lines = ["[다음 주 계획]"]
    lines.append("▶ 사장님이 하실 일")
    if manual:
        for i, item in enumerate(manual, 1):
            lines.append(f"  {i}. {item}")
    else:
        lines.append("  없음 — 이번 주는 손댈 것이 없습니다.")
    lines.append("▶ 자동으로 처리됨")
    for item in auto:
        lines.append(f"  · {item}")
    if total_delta < MIN_TOTAL_DELTA:
        lines.append("▶ 하지 말아야 할 일")
        lines.append("  · 무드 비중·씬·썸네일 문구 수정 (표본 부족 — 효과를 측정할 수 없습니다)")
    return lines


def build_report(now, channel_stats, channel_prev, videos, video_prev, recent_by_id, genre_lookup,
                 library_counts=None, history=None, analytics=None, prev_analytics_summary=None,
                 analytics_error=None):
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

    if analytics:
        summary = analytics["summary"]
        start_date, end_date = analytics["period"]
        lines.append(f"[유입 지표 ({start_date} ~ {end_date})]")
        impressions = summary.get("videoThumbnailImpressions")
        ctr = summary.get("videoThumbnailImpressionsClickRate")
        if impressions is not None:
            lines.append(f"노출수: {impressions:,}회")
        if ctr is not None:
            lines.append(f"노출 클릭률(CTR): {ctr:.2f}%")
        avg_seconds = summary.get("averageViewDuration", 0) or 0
        lines.append(f"평균 시청 시간: {avg_seconds / 60:.1f}분")
        watched = summary.get("estimatedMinutesWatched", 0) or 0
        lines.append(f"총 시청 시간: {watched:,.0f}분")
        lines.append("")

        if analytics.get("traffic_sources"):
            lines.append("[유입 경로]")
            lines.extend(format_traffic_sources(analytics["traffic_sources"]))
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
        entry = recent_by_id.get(vid) or {}
        style = entry.get("style")
        tag = f"{genre}/{style}" if style else genre
        title_short = v["title"][:40] + ("…" if len(v["title"]) > 40 else "")
        lines.append(f"- {published.strftime('%m/%d')} ({tag}) {title_short}")
        lines.append(f"  조회수 {fmt_num(v['viewCount'])}회 · 좋아요 {fmt_num(v['likeCount'])} · youtu.be/{vid}")
    lines.append("")

    # 조회수 증가분 계산 (스냅샷 있는 영상만)
    deltas = {}
    for vid, v in public_videos.items():
        prev = video_prev.get(vid)
        if prev is not None:
            deltas[vid] = v["viewCount"] - prev.get("viewCount", v["viewCount"])

    # 무드별 / 그림체별 반응 (증가분 있는 영상 기준)
    genre_groups = group_stats(
        deltas, recent_by_id, "genre",
        fallback=lambda vid: infer_genre(vid, recent_by_id, description_by_id, genre_lookup),
    )
    style_groups = group_stats(deltas, recent_by_id, "style")

    if genre_groups:
        lines.append("[무드별 반응 (최근 7일 조회수 증가분)]")
        for genre, b in sorted(genre_groups.items(), key=lambda kv: -kv[1]["total"]):
            lines.append(f"- {genre}: 영상 {b['count']}개, 합계 {fmt_delta(b['total'])}회, 평균 {fmt_avg(b['avg'])}회/영상")
        lines.append("")

    if style_groups:
        lines.append("[그림체별 반응 (최근 7일 조회수 증가분)]")
        for style, b in sorted(style_groups.items(), key=lambda kv: -kv[1]["total"]):
            lines.append(f"- {style}: 영상 {b['count']}개, 합계 {fmt_delta(b['total'])}회, 평균 {fmt_avg(b['avg'])}회/영상")
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
        lines.append("")
    else:
        lines.append("(지난 주 스냅샷이 없어 증가분 비교는 다음 리포트부터 제공됩니다)")
        lines.append("")

    if library_counts:
        lines.append("[음원 재고 (Drive)]")
        for name, n in sorted(library_counts.items()):
            mark = " ⚠️ 보충 필요" if n < RECOMMENDED_LIBRARY_SIZE else ""
            lines.append(f"- {name}: {n}곡{mark}")
        lines.append("")

    # 표본 판정에는 채널 전체 증가분을 쓴다 — 영상별 합계는 이번 주 신작(지난 주
    # 스냅샷에 없던 영상)의 조회수를 통째로 놓치기 때문에 과소평가된다.
    funnel = diagnose_funnel(analytics, prev_analytics_summary)
    if funnel:
        lines.append("[원인 진단]")
        lines.extend(funnel)
        lines.append("")
    elif analytics_error:
        lines.append("[원인 진단]")
        lines.append(f"- 노출수/클릭률 분석이 꺼져 있습니다: {analytics_error}")
        lines.append("")

    total_delta = view_delta if view_delta is not None else (sum(deltas.values()) if deltas else 0)
    recommendations = build_recommendations(
        channel_stats, channel_prev, total_delta, genre_groups, style_groups,
        len(new_videos), library_counts, history,
    )
    if recommendations:
        lines.append("[이번 주 진단]")
        lines.extend(recommendations)
        lines.append("")

    trend = week_trend_lines(history)
    if trend:
        lines.extend(trend)
        lines.append("")

    lines.extend(build_next_week_plan(
        len(new_videos), library_counts, analytics, analytics_error, total_delta))

    # 헤드라인은 맨 마지막에 계산해 맨 앞에 끼워 넣는다 (집계 결과가 다 나온 뒤라야 정확)
    headline = build_headline(channel_stats, sub_delta, view_delta, len(new_videos), analytics)
    lines.insert(1, headline)

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
    history = prev_snapshot.get("history", [])

    library_counts = fetch_library_counts()

    now = datetime.now(KST)
    # Analytics는 추가 스코프가 필요하다. 미승인이면 (None, 사유)가 오고
    # 리포트는 해당 섹션만 빠진 채 정상 발송된다.
    analytics, analytics_error = youtube_analytics.safe_fetch(credentials, now)
    prev_analytics_summary = (prev_snapshot.get("analytics") or {}).get("summary")

    report = build_report(now, channel_stats, channel_prev, videos, video_prev, recent_by_id,
                          genre_lookup, library_counts=library_counts, history=history,
                          analytics=analytics, prev_analytics_summary=prev_analytics_summary,
                          analytics_error=analytics_error)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    # 다음 주 비교를 위해 이번 실행 시점 스냅샷 저장. history에는 주별 채널 수치를
    # 누적해 "몇 주째 정체인지" 같은 추세 판단에 쓴다 (최근 12주만 유지).
    weekly_view_delta = None
    if channel_prev:
        weekly_view_delta = channel_stats["viewCount"] - channel_prev.get("viewCount", channel_stats["viewCount"])
    if should_append_history(history, now):
        history = history + [{
            "at": now.isoformat(),
            "subscriberCount": channel_stats["subscriberCount"],
            "viewCount": channel_stats["viewCount"],
            "weekly_view_delta": weekly_view_delta,
        }]
    snapshot = {
        "snapshot_at": now.isoformat(),
        "channel": channel_stats,
        "videos": {vid: {"viewCount": v["viewCount"], "likeCount": v["likeCount"]} for vid, v in videos.items()},
        "history": history[-12:],
    }
    if analytics:
        # 다음 주에 노출수 추세("지난주 대비 N% 증가")를 계산하기 위해 보관
        snapshot["analytics"] = analytics
    elif prev_snapshot.get("analytics"):
        # 이번 주 조회가 실패했다면 직전 값을 지워버리지 않는다
        snapshot["analytics"] = prev_snapshot["analytics"]
    save_json(args.weekly_stats, snapshot)

    if not args.dry_run:
        import send_telegram_message
        sys.argv = ["send_telegram_message.py", f"--text={report}"]
        send_telegram_message.main()


if __name__ == "__main__":
    main()
