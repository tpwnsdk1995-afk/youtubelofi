"""사람들이 실제로 검색했을 때 우리가 나오는지 확인한다 (읽기 전용).

"왜 조회수가 안 나오나"를 Analytics 없이도 상당 부분 답할 수 있다. 노출수를 못 봐도,
**우리가 노리는 검색어를 직접 쳐보면** 우리가 결과에 뜨는지 아닌지는 바로 알 수 있기
때문이다. 안 뜨면 콘텐츠 이전에 색인·경쟁 문제이고, 뜨는데 조회수가 없으면 제목과
썸네일이 안 눌리는 것이다 — 조치가 완전히 다르다.

덤으로 각 검색어의 상위 결과가 누구인지도 보여준다. 경쟁이 비어 있는 자리인지
(리브랜딩의 전제였다) 실제로 확인하는 유일한 방법이다.

채널을 수정하지 않는다.
"""

import argparse
import os
import sys

import upload_youtube as uy
from googleapiclient.discovery import build

# 우리가 실제로 노리는 검색어. 앞쪽은 차별화 자리(경쟁이 비어 있길 기대하는 곳),
# 뒤쪽은 수요는 크지만 경쟁이 심한 곳 — 둘을 같이 봐야 "어디서 싸울지"가 정해진다.
DEFAULT_QUERIES = [
    "조선 로파이",
    "국악 로파이",
    "가야금 로파이",
    "사극 브금",
    "국악 플레이리스트",
    "한국풍 로파이",
    "korean lofi",
    "조선 브금",
    "공부 플레이리스트",
    "집중 음악",
]

# 한 검색어당 확인할 결과 수. 50이 search.list 1회 최대치다. 여기 안에 없으면
# 사람이 스크롤해서 우리를 발견할 가능성은 사실상 없다고 본다.
RESULTS_PER_QUERY = 50

# 상위 몇 개를 "누가 이기고 있나"로 보여줄지
TOP_SHOWN = 3


def find_our_rank(items, channel_id):
    """결과 목록에서 우리 채널이 처음 나오는 순위(1부터). 없으면 None."""
    for i, item in enumerate(items, 1):
        if item["snippet"]["channelId"] == channel_id:
            return i
    return None


def summarize(query, items, channel_id, view_counts):
    rank = find_our_rank(items, channel_id)
    lines = []
    if rank:
        mine = items[rank - 1]
        lines.append(f"▶ \"{query}\" — {rank}위에 우리 영상")
        lines.append(f"   {mine['snippet']['title'][:50]}")
    else:
        lines.append(f"▶ \"{query}\" — 상위 {len(items)}개 안에 우리 없음")
    for item in items[:TOP_SHOWN]:
        vid = item["id"].get("videoId")
        views = view_counts.get(vid)
        v = f"{views:,}회" if views is not None else "조회수 미상"
        mark = "★" if item["snippet"]["channelId"] == channel_id else " "
        lines.append(f"  {mark} {item['snippet']['channelTitle'][:16]} | "
                     f"{item['snippet']['title'][:38]} ({v})")
    return lines


def fetch_view_counts(youtube, items):
    """상위 결과의 조회수를 한 번에 가져온다 (videos.list는 1회 1유닛이라 싸다)."""
    ids = [i["id"]["videoId"] for i in items[:TOP_SHOWN] if i["id"].get("videoId")]
    if not ids:
        return {}
    resp = youtube.videos().list(part="statistics", id=",".join(ids)).execute()
    return {v["id"]: int(v.get("statistics", {}).get("viewCount", 0))
            for v in resp.get("items", [])}


def main(argv=None):
    parser = argparse.ArgumentParser(description="검색 노출 확인 (읽기 전용)")
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--queries", default="", help="쉼표로 구분한 검색어 (기본값 대신)")
    args = parser.parse_args(argv)

    queries = [q.strip() for q in args.queries.split(",") if q.strip()] or DEFAULT_QUERIES

    try:
        credentials = uy.get_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    youtube = build("youtube", "v3", credentials=credentials)
    channel_id = youtube.channels().list(part="id", mine=True).execute()["items"][0]["id"]

    report = ["🔎 검색 노출 확인", f"(검색어 {len(queries)}개 × 상위 {RESULTS_PER_QUERY}개)", ""]
    found, missing = [], []

    for q in queries:
        resp = youtube.search().list(
            part="snippet", q=q, type="video", maxResults=RESULTS_PER_QUERY,
            regionCode="KR", relevanceLanguage="ko",
        ).execute()
        items = resp.get("items", [])
        views = fetch_view_counts(youtube, items)
        report.extend(summarize(q, items, channel_id, views))
        report.append("")
        (found if find_our_rank(items, channel_id) else missing).append(q)

    report.append("[요약]")
    report.append(f"검색에 잡히는 검색어: {len(found)}개 / {len(queries)}개")
    if found:
        report.append("  잡힘: " + ", ".join(found))
    if missing:
        report.append("  안 잡힘: " + ", ".join(missing))
    report.append("")
    if not found:
        report.append("→ 노린 검색어 어디에도 안 잡힌다. 조회수 이전에 색인·경쟁 문제다.")
    elif missing:
        report.append("→ 잡히는 검색어가 있다. 거기부터 제목·썸네일로 클릭을 가져와야 한다.")
    else:
        report.append("→ 전 검색어에서 잡힌다. 문제는 노출이 아니라 클릭이다.")

    text = "\n".join(report)
    print(text)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("```\n" + text + "\n```\n")

    if args.telegram:
        import send_telegram_message as stm

        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if token and chat_id:
            chunks = stm.split_message(text)
            for i, chunk in enumerate(chunks, 1):
                suffix = f"\n\n({i}/{len(chunks)})" if len(chunks) > 1 else ""
                stm.send(token, chat_id, chunk + suffix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
