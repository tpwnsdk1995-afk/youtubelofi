"""channel_health.py가 지목한 채널 단위 문제를 실제로 고친다.

파이프라인은 영상만 올리고 채널 자체(키워드·재생목록)는 건드리지 않는다. 그 결과
리브랜딩 때 영상·제목·썸네일은 전부 조선 컨셉으로 바뀌었는데 채널 키워드는 노아뮤직
시절 '공부 플레이리스트' 문구가 그대로 남아, 유튜브에는 여전히 경쟁이 가장 치열한
일반 공부음악 채널로 등록돼 있었다. 이 스크립트가 그걸 맞춘다.

세 가지를 하고, **전부 기본값은 '하지 않음'이다.** 플래그를 줘야 실행된다.
채널을 바꾸는 작업이라 실수로 도는 일이 없어야 한다.
"""

import argparse
import json
import os
import sys

import upload_youtube as uy
from googleapiclient.discovery import build

# 채널 키워드. 앞쪽이 우리만의 자리(조선/국악)이고, 뒤쪽 두 개는 calm 무드가 실제로
# 겨냥하는 공부 수요를 남겨둔 것이다. 공부 키워드만 있던 기존 값은 수만 개 채널과
# 정면으로 붙는 자리라 구독자 0명인 채널이 노출을 받을 수 없었다.
JOSEON_KEYWORDS = (
    '"조선 로파이" "국악 로파이" "한국풍 로파이" "사극 브금" "가야금 로파이" '
    '"국악 플레이리스트" "조선 감성 음악" "korean lofi" "korea lofi" '
    '"traditional korean lofi" "공부 플레이리스트" "집중력 음악"'
)

# 유튜브 채널 키워드 길이 상한
MAX_KEYWORDS_LENGTH = 500

# 무드 판별용 태그. 두 무드의 extra_tags에서 겹치지 않는 것만 골랐다
# (config/title_templates_joseon.yml과 맞춰야 한다).
MOOD_TAGS = {
    "calm": {"공부할때듣는음악", "공부음악", "공부플레이리스트", "집중음악",
             "시험기간", "새벽공부", "studymotivation", "focus"},
    "groove": {"산책음악", "드라이브음악", "드라이브팝", "기분전환음악",
               "신나는음악", "무드음악", "국악힙합", "chillhop"},
}

MOOD_PLAYLIST_TITLE = {
    "calm": "조선 로파이 | 공부·집중",
    "groove": "조선 로파이 | 산책·드라이브",
}


def build_branding_update(channel_resource, new_keywords):
    """keywords만 바꾼 channels.update 요청 body를 통째로 만든다.

    두 가지를 지켜야 한다.

    1. **본문 모양.** part=brandingSettings로 업데이트하려면 body가
       `{"id": ..., "brandingSettings": {"channel": {...}}}`여야 한다. 래퍼 없이
       `{"channel": ...}`를 보내면 필드 이름도 없는 `400 Required`만 돌아온다
       (2026-09-21에 네 번 실패하고서야 찾았다). 그래서 이 함수가 id를 뺀
       **완성된 body 전체**를 만든다 — 호출부가 모양을 다시 조립하지 않게.
    2. **덮어쓰기.** channels.update는 보낸 brandingSettings.channel로 통째로
       갈아끼운다. keywords만 담아 보내면 채널 설명·국가가 지워지므로, 읽어온
       값을 복사한 뒤 keywords 한 필드만 교체한다. title/description은 혹시
       brandingSettings에 없으면 snippet에서 메운다.
    """
    branding = channel_resource.get("brandingSettings", {})
    snippet = channel_resource.get("snippet", {})
    channel = dict(branding.get("channel", {}))
    channel["keywords"] = new_keywords
    channel["title"] = channel.get("title") or snippet.get("title") or ""
    channel["description"] = channel.get("description") or snippet.get("description") or ""
    country = channel.get("country") or snippet.get("country")
    if country:
        channel["country"] = country
    return {"brandingSettings": {"channel": channel}}


def infer_mood(tags):
    """영상 태그로 무드를 고른다. 확실하지 않으면 None — 추측하지 않는다.

    엉뚱한 재생목록에 넣으면 사장님이 직접 찾아 빼야 하므로, 한쪽으로 명확히
    기울 때만 판정한다.
    """
    tagset = {t.strip() for t in (tags or [])}
    scores = {mood: len(tagset & words) for mood, words in MOOD_TAGS.items()}
    best = max(scores, key=scores.get)
    others = [s for m, s in scores.items() if m != best]
    if scores[best] == 0 or scores[best] <= max(others, default=0):
        return None
    return best


def set_keywords(youtube, channel, keywords, dry_run):
    current = (channel.get("brandingSettings", {}).get("channel", {}).get("keywords") or "").strip()
    if len(keywords) > MAX_KEYWORDS_LENGTH:
        raise ValueError(f"키워드가 {len(keywords)}자로 상한({MAX_KEYWORDS_LENGTH})을 넘습니다")
    print(f"  현재: {current or '(비어 있음)'}")
    print(f"  변경: {keywords}")
    if current == keywords:
        print("  → 이미 같은 값입니다. 건너뜁니다.")
        return False
    if dry_run:
        print("  → (dry-run) 실제로 바꾸지 않았습니다")
        return False
    body = build_branding_update(channel, keywords)
    body["id"] = channel["id"]
    ch = body["brandingSettings"]["channel"]
    print(f"  보낼 항목: title={len(ch['title'])}자 / description={len(ch['description'])}자 "
          f"/ country={ch.get('country', '(없음)')}")
    if not ch["description"]:
        # 설명이 빈 채로 보내면 지금 걸려 있는 200자 설명이 지워진다.
        raise RuntimeError("채널 설명을 읽지 못했습니다 — 설명이 지워질 수 있어 중단합니다")
    youtube.channels().update(part="brandingSettings", body=body).execute()
    print("  → 변경했습니다")
    return True


def add_orphans_to_playlists(youtube, orphans, playlist_by_title, dry_run):
    added, skipped = 0, []
    for v in orphans:
        mood = infer_mood(v["tags"])
        title = MOOD_PLAYLIST_TITLE.get(mood)
        playlist_id = playlist_by_title.get(title) if title else None
        if not playlist_id:
            skipped.append(v)
            print(f"  ? {v['video_id']} {v['title'][:40]} — 무드를 못 정해 건너뜀")
            continue
        print(f"  + {v['video_id']} {v['title'][:40]} → {title}")
        if dry_run:
            continue
        youtube.playlistItems().insert(
            part="snippet",
            body={"snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": v["video_id"]},
            }},
        ).execute()
        added += 1
    return added, skipped


def delete_playlists(youtube, playlists, dry_run):
    deleted = 0
    for p in playlists:
        # 지우기 전에 담긴 영상 ID를 남긴다. 재생목록 삭제는 되돌릴 수 없지만,
        # 영상 자체는 지워지지 않으므로 이 목록만 있으면 똑같이 다시 만들 수 있다.
        print(f"  - {p['title']} (담긴 영상 {len(p['video_ids'])}편: {', '.join(sorted(p['video_ids'])) or '없음'})")
        if dry_run:
            continue
        youtube.playlists().delete(id=p["id"]).execute()
        deleted += 1
    return deleted


def main(argv=None):
    parser = argparse.ArgumentParser(description="채널 단위 문제를 고친다 (기본은 아무것도 안 함)")
    parser.add_argument("--set-keywords", action="store_true", help="채널 키워드를 조선 컨셉으로 교체")
    parser.add_argument("--fix-orphans", action="store_true", help="재생목록에 없는 공개 영상을 무드별 목록에 추가")
    parser.add_argument("--delete-empty-playlists", action="store_true",
                        help="공개 영상이 하나도 없는 재생목록 삭제 (되돌릴 수 없음)")
    parser.add_argument("--dry-run", action="store_true", help="무엇을 할지만 출력하고 실제로는 안 바꿈")
    args = parser.parse_args(argv)

    if not (args.set_keywords or args.fix_orphans or args.delete_empty_playlists):
        print("할 일이 지정되지 않았습니다. --set-keywords / --fix-orphans / "
              "--delete-empty-playlists 중 하나 이상을 주세요.", file=sys.stderr)
        return 2

    try:
        credentials = uy.get_credentials()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    youtube = build("youtube", "v3", credentials=credentials)
    resp = youtube.channels().list(
        part="id,snippet,brandingSettings,contentDetails", mine=True
    ).execute()
    items = resp.get("items") or []
    if not items:
        print("ERROR: 채널을 찾을 수 없습니다", file=sys.stderr)
        return 1
    channel = items[0]

    if args.dry_run:
        print("=== DRY RUN — 아무것도 바꾸지 않습니다 ===\n")

    failures = []
    if args.set_keywords:
        print("[채널 키워드]")
        # 키워드 교체가 실패해도 재생목록 정리는 독립적으로 해야 한다. 한 구간의
        # 예외가 나머지를 통째로 막으면, 고칠 수 있는 것까지 안 고친 채로 끝난다.
        try:
            set_keywords(youtube, channel, JOSEON_KEYWORDS, args.dry_run)
        except Exception as e:
            failures.append(f"키워드 교체 실패: {e}")
            print(f"  ✗ 실패: {e}")
            print("  --- 진단용: 채널이 실제로 돌려준 값 ---")
            print("  brandingSettings =", json.dumps(
                channel.get("brandingSettings", {}), ensure_ascii=False, sort_keys=True))
            print("  snippet keys =", sorted(channel.get("snippet", {})))
        print()

    if not (args.fix_orphans or args.delete_empty_playlists):
        return 1 if failures else 0

    # 재생목록과 영상 상태를 모아 온다
    playlists, token = [], None
    while True:
        r = youtube.playlists().list(part="snippet,contentDetails", mine=True,
                                     maxResults=50, pageToken=token).execute()
        playlists.extend(r.get("items", []))
        token = r.get("nextPageToken")
        if not token:
            break

    members = {}
    for p in playlists:
        ids, tok = set(), None
        while True:
            r = youtube.playlistItems().list(part="contentDetails", playlistId=p["id"],
                                             maxResults=50, pageToken=tok).execute()
            ids.update(i["contentDetails"]["videoId"] for i in r.get("items", []))
            tok = r.get("nextPageToken")
            if not tok:
                break
        members[p["id"]] = ids

    uploads_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    video_ids, tok = [], None
    while True:
        r = youtube.playlistItems().list(part="contentDetails", playlistId=uploads_id,
                                         maxResults=50, pageToken=tok).execute()
        video_ids.extend(i["contentDetails"]["videoId"] for i in r.get("items", []))
        tok = r.get("nextPageToken")
        if not tok:
            break

    videos = []
    for i in range(0, len(video_ids), 50):
        r = youtube.videos().list(part="snippet,status", id=",".join(video_ids[i:i + 50])).execute()
        for item in r.get("items", []):
            videos.append({
                "video_id": item["id"],
                "title": item["snippet"]["title"],
                "privacy": item["status"].get("privacyStatus"),
                "tags": item["snippet"].get("tags") or [],
            })

    public = [v for v in videos if v["privacy"] == "public"]
    public_ids = {v["video_id"] for v in public}
    in_any = set().union(*members.values()) if members else set()
    playlist_by_title = {p["snippet"]["title"]: p["id"] for p in playlists}

    if args.fix_orphans:
        print("[재생목록에 없는 공개 영상]")
        orphans = [v for v in public if v["video_id"] not in in_any]
        if not orphans:
            print("  없음")
        else:
            added, skipped = add_orphans_to_playlists(youtube, orphans, playlist_by_title, args.dry_run)
            print(f"  → {added}편 추가, {len(skipped)}편 건너뜀")
        print()

    if args.delete_empty_playlists:
        print("[공개 영상이 없는 재생목록]")
        empty = [
            {"id": p["id"], "title": p["snippet"]["title"], "video_ids": members[p["id"]]}
            for p in playlists if not (members[p["id"]] & public_ids)
        ]
        if not empty:
            print("  없음")
        else:
            n = delete_playlists(youtube, empty, args.dry_run)
            print(f"  → {n}개 삭제")
        print()

    if failures:
        print("실패한 작업:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
