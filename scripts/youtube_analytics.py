"""YouTube Analytics API(v2) 조회 - 주간 리포트의 '원인 분석'용.

Data API(youtube v3)는 조회수/좋아요/구독자 같은 '결과'만 준다. 왜 그 결과가
나왔는지를 알려면 Analytics API가 필요하다:

  - videoThumbnailImpressions       노출수. 알고리즘이 우리 영상을 몇 번 보여줬나
  - videoThumbnailImpressionsClickRate  노출 클릭률(CTR). 썸네일이 클릭을 유도하나
  - averageViewDuration             평균 시청 시간. 음악이 실제로 붙잡고 있나
  - subscribersGained               어떤 영상이 구독으로 이어지나
  - insightTrafficSourceType        유입 경로(검색/추천/탐색)

노출·CTR 지표는 2026-01-15에 Analytics API에 추가됐다. 그 전에는 스튜디오
화면에서만 볼 수 있었다.

**이 모듈은 추가 OAuth 스코프(yt-analytics.readonly)를 요구한다.** 기존
refresh token은 youtube 스코프만 가지고 발급됐으므로, 재인증 전까지는 여기의
모든 함수가 (None, 사유) 를 돌려주고 리포트는 해당 섹션만 생략한 채 정상 동작한다.
"""

import sys
from datetime import timedelta

from googleapiclient.discovery import build

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"

# 노출/CTR은 비교적 최근에 추가된 지표라, 혹시 거부되면 핵심 지표만으로 재시도한다.
FULL_METRICS = (
    "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
    "subscribersGained,videoThumbnailImpressions,videoThumbnailImpressionsClickRate"
)
CORE_METRICS = "views,estimatedMinutesWatched,averageViewDuration,subscribersGained"


def date_range(now, days=7, lag_days=1):
    """Analytics 데이터는 당일 집계가 불완전하므로 어제까지를 구간 끝으로 삼는다."""
    end = (now - timedelta(days=lag_days)).date()
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def _rows_to_dict(response):
    """Analytics 응답(columnHeaders + rows)을 {지표명: 값} 으로 바꾼다."""
    headers = [h["name"] for h in response.get("columnHeaders", [])]
    rows = response.get("rows", [])
    if not rows:
        return {name: 0 for name in headers}
    return dict(zip(headers, rows[0]))


def fetch_channel_summary(credentials, start_date, end_date):
    """채널 전체 요약. (dict, None) 또는 (None, 사유문자열)."""
    try:
        service = build("youtubeAnalytics", "v2", credentials=credentials)
    except Exception as e:
        return None, f"Analytics 클라이언트 생성 실패: {e}"

    for metrics in (FULL_METRICS, CORE_METRICS):
        try:
            response = service.reports().query(
                ids="channel==MINE", startDate=start_date, endDate=end_date, metrics=metrics,
            ).execute()
            return _rows_to_dict(response), None
        except Exception as e:
            last_error = e
            if _is_auth_error(e):
                return None, ("Analytics 스코프 미승인 - SETUP.md의 재인증 절차로 "
                              "refresh token을 다시 발급해야 노출수/CTR 분석이 켜집니다.")
    return None, f"Analytics 조회 실패: {last_error}"


def fetch_traffic_sources(credentials, start_date, end_date):
    """유입 경로별 조회수. 실패하면 (None, 사유)."""
    try:
        service = build("youtubeAnalytics", "v2", credentials=credentials)
        response = service.reports().query(
            ids="channel==MINE", startDate=start_date, endDate=end_date,
            metrics="views", dimensions="insightTrafficSourceType", sort="-views",
        ).execute()
    except Exception as e:
        return None, f"유입 경로 조회 실패: {e}"

    sources = {}
    for row in response.get("rows", []):
        sources[row[0]] = row[1]
    return sources, None


def _is_auth_error(exc):
    text = str(exc)
    return "403" in text or "401" in text or "insufficient" in text.lower() or "scope" in text.lower()


def safe_fetch(credentials, now):
    """리포트에서 쓰는 진입점. 실패해도 예외를 밖으로 내보내지 않는다."""
    start_date, end_date = date_range(now)
    summary, err = fetch_channel_summary(credentials, start_date, end_date)
    if summary is None:
        print(f"WARNING: {err}", file=sys.stderr)
        return None, err
    sources, src_err = fetch_traffic_sources(credentials, start_date, end_date)
    if src_err:
        print(f"WARNING: {src_err}", file=sys.stderr)
    return {"period": (start_date, end_date), "summary": summary, "traffic_sources": sources or {}}, None
