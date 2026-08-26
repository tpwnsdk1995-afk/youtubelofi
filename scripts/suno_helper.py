"""수노(Suno) 음원 보충 도우미.

수노 자체는 자동화하지 않는다 (ToS상 금지, 그래서 처음부터 수동 다운로드 설계다).
이 스크립트가 대신 하는 것은 수노 작업의 '앞뒤'다:

  앞: 지금 어떤 무드가 몇 곡 부족한지 세고, 그만큼의 서로 다른 프롬프트를 만들어
      그대로 붙여넣을 수 있게 준다. 같은 프롬프트로 40곡을 뽑으면 40곡이 다 비슷해져
      영상이 지루해지고 반복 콘텐츠 판정 위험도 커지기 때문이다.

  뒤: Drive에 올라간 파일을 점검한다. 파이프라인은 매일 17:15에 Drive에서 음원을
      내려받는데, 여기서 문제가 있으면 그날 영상이 통째로 날아간다. 다운로드 없이
      메타데이터만으로 잡을 수 있는 것들을 미리 잡는다:
        - mp3가 아닌 파일 (파이프라인은 audio/mpeg만 읽는다. m4a/wav를 올리면
          Drive에는 보이는데 파이프라인 눈에는 없어서 "왜 안 늘지?"가 된다)
        - 너무 작은 파일 (잘렸거나 깨진 것)
        - 완전 중복 (md5 동일 — 같은 배치를 두 번 올린 경우)

점검은 읽기 전용이다. 이 스크립트는 Drive의 어떤 파일도 지우거나 옮기지 않는다.
"""

import argparse
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import state_manager
import sync_music_library
import weekly_report as wr

# 목표 재고. 영상 한 편이 40~50곡을 쓰므로 무드별 100곡은 있어야 영상 간 곡 중복이
# 눈에 띄게 줄어든다 (weekly_report와 같은 기준을 쓴다).
TARGET_PER_MOOD = wr.RECOMMENDED_LIBRARY_SIZE

# 약 45초 미만(128kbps 기준)이면 잘렸거나 깨진 파일로 본다. 정확한 길이는 파일을
# 받아봐야 알지만, 그건 매일 파이프라인이 이미 하고 있다. 여기서는 다운로드 없이
# 명백히 이상한 것만 걸러낸다.
MIN_BYTES = 700_000
# 3분짜리 mp3가 20MB를 넘을 일은 없다. 넘으면 무손실이거나 잘못된 파일이다.
MAX_BYTES = 20_000_000

# 한 번에 너무 많은 프롬프트를 뽑으면 텔레그램에서 읽기 힘들다.
MAX_PROMPTS_PER_MOOD = 25


def list_all_files(service, folder_id):
    """폴더 안의 '모든' 파일을 메타데이터와 함께 가져온다.

    sync_music_library.list_library_files는 mimeType='audio/mpeg'로 걸러내지만,
    여기서는 걸러지는 파일 자체가 진단 대상이라 필터 없이 전부 본다."""
    files = []
    page_token = None
    query = f"'{folder_id}' in parents and trashed=false"
    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, size, md5Checksum, mimeType)",
            pageToken=page_token,
            pageSize=1000,
        ).execute()
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def audit_folder(files):
    """한 무드 폴더의 파일 목록에서 문제를 찾아낸다."""
    usable, wrong_type, too_small, too_big = [], [], [], []
    by_md5 = {}

    for f in files:
        if f.get("mimeType") == "application/vnd.google-apps.folder":
            continue
        if f.get("mimeType") != "audio/mpeg":
            wrong_type.append(f)
            continue
        size = int(f.get("size") or 0)
        if size and size < MIN_BYTES:
            too_small.append(f)
            continue
        if size and size > MAX_BYTES:
            too_big.append(f)
            # 크기만 이상할 뿐 재생은 되므로 사용 가능으로 친다
        usable.append(f)
        md5 = f.get("md5Checksum")
        if md5:
            by_md5.setdefault(md5, []).append(f)

    duplicates = [group for group in by_md5.values() if len(group) > 1]
    # 중복 그룹에서 첫 곡만 실질적으로 쓸모 있다 — 나머지는 같은 소리다.
    duplicate_waste = sum(len(g) - 1 for g in duplicates)

    return {
        "usable": usable,
        "wrong_type": wrong_type,
        "too_small": too_small,
        "too_big": too_big,
        "duplicates": duplicates,
        "duplicate_waste": duplicate_waste,
        "effective": len(usable) - duplicate_waste,
    }


def audit_library(service, folder_id):
    """무드 폴더별로 점검 결과를 돌려준다."""
    result = {}
    for folder in sync_music_library.list_subfolders(service, folder_id):
        files = list_all_files(service, folder["id"])
        result[folder["name"]] = audit_folder(files)
    return result


def build_prompt(cfg, mood, state, rng):
    """축마다 셔플백을 따로 돌려 조합한다. 축이 독립적이라 조합 수가 곱으로 늘어나고,
    셔플백이라 같은 값이 연달아 나오지 않는다."""
    spec = cfg["moods"][mood]
    parts = []
    for axis in ("lead", "rhythm", "texture", "mood"):
        pool = spec[axis]
        parts.append(state_manager.draw(state, f"suno_{mood}_{axis}", pool, rng=rng)[0])
    bpm = state_manager.draw(state, f"suno_{mood}_bpm", [str(b) for b in spec["bpm"]], rng=rng)[0]

    lead, rhythm, texture, mood_word = parts
    return (
        f"Korean traditional lo-fi hip hop, {lead}, {rhythm}, {texture}, "
        f"{mood_word}, {bpm} BPM, {cfg['common_suffix']}"
    )


def needed_counts(audit, target=TARGET_PER_MOOD):
    """무드별로 몇 곡이 더 필요한지. 중복은 실질 재고에서 빼고 센다."""
    return {mood: max(0, target - data["effective"]) for mood, data in audit.items()}


def format_audit_lines(audit):
    lines = ["[현재 재고 점검]"]
    for mood, data in sorted(audit.items()):
        effective = data["effective"]
        mark = "" if effective >= TARGET_PER_MOOD else f" ⚠️ {TARGET_PER_MOOD - effective}곡 부족"
        lines.append(f"▶ {mood}: 쓸 수 있는 곡 {effective}곡 / 목표 {TARGET_PER_MOOD}곡{mark}")

        if data["wrong_type"]:
            names = ", ".join(f["name"] for f in data["wrong_type"][:3])
            more = f" 외 {len(data['wrong_type']) - 3}개" if len(data["wrong_type"]) > 3 else ""
            lines.append(f"  · mp3가 아닌 파일 {len(data['wrong_type'])}개 — 파이프라인이 "
                         f"읽지 못해 재고에 안 잡힙니다: {names}{more}")
            lines.append(f"    → 수노에서 받을 때 MP3로 받으시거나, mp3로 변환해 다시 올려주세요.")
        if data["too_small"]:
            names = ", ".join(f["name"] for f in data["too_small"][:3])
            lines.append(f"  · 너무 작은 파일 {len(data['too_small'])}개 (다운로드가 잘린 것으로 "
                         f"보입니다): {names}")
            lines.append(f"    → 지우고 다시 받아주세요. 그대로 두면 영상 중간에 끊깁니다.")
        if data["duplicate_waste"]:
            lines.append(f"  · 완전히 같은 곡 {data['duplicate_waste']}개가 중복 업로드돼 "
                         f"있습니다 (같은 배치를 두 번 올리신 것 같습니다).")
            lines.append(f"    → 곡 수는 늘어도 실제로는 같은 소리라 다양성에 도움이 안 됩니다.")
        if data["too_big"]:
            lines.append(f"  · 비정상적으로 큰 파일 {len(data['too_big'])}개 — 재생은 되지만 "
                         f"용량만 차지합니다.")
    return lines


def format_prompt_lines(cfg, audit, state, rng, limit=MAX_PROMPTS_PER_MOOD):
    need = needed_counts(audit)
    lines = []
    for mood in sorted(need):
        count = need[mood]
        if count == 0:
            continue
        shown = min(count, limit)
        spec = cfg["moods"][mood]
        lines.append("")
        lines.append(f"[{mood} 프롬프트 {shown}개] — {spec['description']}")
        if shown < count:
            lines.append(f"(총 {count}곡 필요하지만 길어서 {shown}개만 보냅니다. "
                         f"다 쓰시면 다시 실행해 주세요 — 매번 다른 조합이 나옵니다.)")
        for i in range(shown):
            lines.append(f"{i + 1}. {build_prompt(cfg, mood, state, rng)}")
    return lines


def build_report(cfg, audit, state, rng, limit=MAX_PROMPTS_PER_MOOD):
    need = needed_counts(audit)
    total_need = sum(need.values())

    lines = ["🎵 수노 음원 보충 안내", ""]
    if total_need == 0:
        lines.append("한 줄 요약: 지금은 보충하실 것이 없습니다.")
    else:
        detail = " · ".join(f"{m} {n}곡" for m, n in sorted(need.items()) if n)
        lines.append(f"한 줄 요약: {detail} 더 필요합니다 (총 {total_need}곡).")
    lines.append("")
    lines.extend(format_audit_lines(audit))

    if total_need:
        lines.extend(format_prompt_lines(cfg, audit, state, rng, limit=limit))
        lines.append("")
        lines.append("[사용법]")
        lines.append("1. 수노에서 위 문장을 'Style of Music' 칸에 그대로 붙여넣습니다.")
        lines.append("2. Instrumental 토글을 반드시 켭니다 — 보컬이 들어가면 못 씁니다"
                     " (채널 제목이 '가사X'입니다).")
        lines.append("3. 생성된 곡을 MP3 형식으로 다운로드합니다.")
        lines.append("4. Drive의 해당 무드 폴더(calm/ 또는 groove/)에 넣습니다.")
        lines.append("5. 끝입니다 — 다음 영상부터 자동으로 섞여 들어갑니다.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="수노 음원 보충용 프롬프트를 만들고 Drive 재고를 점검한다")
    parser.add_argument("--prompts-config", default="config/suno_prompts.yml")
    parser.add_argument("--state", default="state/suno_state.json",
                        help="프롬프트 셔플백 상태 (매번 다른 조합이 나오게 한다)")
    parser.add_argument("--limit", type=int, default=MAX_PROMPTS_PER_MOOD,
                        help="무드당 최대 프롬프트 수")
    parser.add_argument("--output", default="suno_report.txt")
    parser.add_argument("--seed", type=int, default=None, help="테스트용 난수 시드")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 파일로만 저장")
    args = parser.parse_args()

    cfg = wr.load_yaml(args.prompts_config)

    sa_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    folder_id = os.environ.get("GDRIVE_MUSIC_FOLDER_ID")
    if not sa_json or not folder_id:
        print("ERROR: GDRIVE_SERVICE_ACCOUNT_JSON / GDRIVE_MUSIC_FOLDER_ID 가 필요합니다.",
              file=sys.stderr)
        sys.exit(1)

    service = sync_music_library.get_drive_service(sa_json)
    audit = audit_library(service, folder_id)
    if not audit:
        print("ERROR: Drive 음악 폴더 안에 무드 하위 폴더(calm/, groove/)가 없습니다.",
              file=sys.stderr)
        sys.exit(1)

    state = state_manager.load_state(args.state)
    rng = random.Random(args.seed) if args.seed is not None else random
    report = build_report(cfg, audit, state, rng, limit=args.limit)
    state_manager.save_state(args.state, state)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)

    if not args.dry_run:
        import send_telegram_message
        sys.argv = ["send_telegram_message.py", f"--text={report}"]
        send_telegram_message.main()


if __name__ == "__main__":
    main()
