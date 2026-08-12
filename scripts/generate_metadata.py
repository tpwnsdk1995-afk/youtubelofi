"""제목/설명/태그를 고정 템플릿이 아닌 여러 문구 풀의 조합으로 생성한다.
훅 문구 x 태그라인 x 이모지가 독립적으로 로테이션되어 매번 다른 제목이 나오고,
설명란에는 크로스페이드 타임스탬프가 반영된 실제 트랙리스트(챕터)가 자동으로 들어간다.
"""

import argparse
import json

import yaml

import state_manager as sm


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def format_timestamp(seconds):
    total = int(seconds)
    hh, rem = divmod(total, 3600)
    mm, ss = divmod(rem, 60)
    if hh:
        return f"{hh}:{mm:02d}:{ss:02d}"
    return f"{mm}:{ss:02d}"


def build_tracklist_section(chapters):
    lines = ["Tracklist🎧"]
    for ch in chapters:
        lines.append(f"{format_timestamp(ch['start_seconds'])} {ch['name']}")
    return "\n".join(lines)


def is_joseon(templates):
    """조선 컨셉 템플릿(무드별 genres 구조)인지 판별한다."""
    return "genres" in templates


def draw_genre(state, templates, rng=None):
    """무드(calm/groove)를 셔플백으로 뽑는다. genre_rotation에 같은 무드를 여러 번
    넣으면 그 비율만큼 가중된다 (예: [calm, calm, groove] = 2:1)."""
    rotation = templates.get("genre_rotation") or list(templates["genres"].keys())
    return sm.draw(state, "genre", rotation, count=1, rng=rng)[0]


def draw_situation(state, templates, genre, rng=None):
    """상황 어그로 훅을 무드별 풀에서 뽑는다. 제목(title)과 썸네일 문구(thumb_main/
    thumb_sub)가 세트라 영상 제목과 썸네일이 항상 같은 상황을 말한다."""
    situations = templates["genres"][genre]["situations"]
    ids = [s["id"] for s in situations]
    sid = sm.draw(state, f"situation_{genre}", ids, count=1, rng=rng)[0]
    return next(s for s in situations if s["id"] == sid)


def build_title(state, templates, rng=None, genre=None, situation=None):
    emoji = sm.draw(state, "title_emoji", templates["title_emojis"], count=1, rng=rng)[0]
    if is_joseon(templates):
        if situation is None:
            situation = draw_situation(state, templates, genre, rng=rng)
        g = templates["genres"][genre]
        tagline = sm.draw(state, f"tagline_{genre}", g["taglines"], count=1, rng=rng)[0]
        # 유튜브 제목은 최대 100자 (초과 시 API가 거부하므로 방어적으로 자른다)
        return f"{templates['title_prefix']} {situation['title']} {emoji} {tagline}"[:100]
    hook = sm.draw(state, "hook_phrase", templates["hook_phrases"], count=1, rng=rng)[0]
    tagline = sm.draw(state, "tagline", templates["taglines"], count=1, rng=rng)[0]
    return f"{templates['title_prefix']} {hook} {emoji} {tagline}"


def build_description(state, templates, chapters, rng=None, genre=None):
    if is_joseon(templates):
        g = templates["genres"][genre]
        blurb = sm.draw(state, f"blurb_{genre}", g["description_blurbs"], count=1, rng=rng)[0]
        top_hashtags = " ".join(g["top_hashtags"])
        # 무드 전용 태그 + 공통 풀 조합 (중복 제거, 최대 20개)
        common_count = min(12, len(templates["tag_pool"]))
        common = sm.draw(state, "tag", templates["tag_pool"], count=common_count, rng=rng)
        tags = list(dict.fromkeys(g.get("extra_tags", []) + common))[:20]
    else:
        blurb = sm.draw(state, "description_blurb", templates["description_blurbs"], count=1, rng=rng)[0]
        top_hashtags = " ".join(templates["top_hashtags"])
        tag_pool = templates["tag_pool"]
        tag_count = min(20, len(tag_pool))
        tags = sm.draw(state, "tag", tag_pool, count=tag_count, rng=rng)
    hashtag_block = " ".join(f"#{t}" for t in tags)

    parts = [
        top_hashtags,
        "",
        blurb,
        "",
        build_tracklist_section(chapters),
        "",
        templates["description_footer"].strip(),
        "",
        hashtag_block,
    ]
    return "\n".join(parts), tags


def build_metadata(state, chapters, templates, video_settings, scene_id=None, rng=None, genre=None):
    metadata = {
        "categoryId": video_settings["youtube"]["category_id"],
        "privacyStatus": video_settings["youtube"]["privacy_status"],
        "madeForKids": video_settings["youtube"]["made_for_kids"],
        "containsSyntheticMedia": video_settings["youtube"].get("contains_synthetic_media", True),
        "scene_id": scene_id,
    }

    if is_joseon(templates):
        if genre is None:
            genre = draw_genre(state, templates, rng=rng)
        situation = draw_situation(state, templates, genre, rng=rng)
        metadata["title"] = build_title(state, templates, rng=rng, genre=genre, situation=situation)
        metadata["description"], metadata["tags"] = build_description(state, templates, chapters, rng=rng, genre=genre)
        metadata["genre"] = genre
        # 썸네일 텍스트 (make_thumbnail.py가 사용): 노란 메인 + 흰 서브
        metadata["thumb_main"] = situation["thumb_main"]
        metadata["thumb_sub"] = situation["thumb_sub"]
    else:
        metadata["title"] = build_title(state, templates, rng=rng)
        metadata["description"], metadata["tags"] = build_description(state, templates, chapters, rng=rng)

    return metadata


def main():
    parser = argparse.ArgumentParser(description="제목/설명/태그 메타데이터를 생성한다")
    parser.add_argument("--scene-id", default=None)
    parser.add_argument("--chapters-json", help="assemble_music.py가 만든 챕터 목록(JSON 파일 경로)")
    parser.add_argument("--templates-config", default="config/title_templates.yml")
    parser.add_argument("--settings-config", default="config/settings.yml")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    templates = load_yaml(args.templates_config)
    settings = load_yaml(args.settings_config)
    state = sm.load_state(args.state)

    if args.chapters_json:
        with open(args.chapters_json, encoding="utf-8") as f:
            chapters = json.load(f)
    else:
        chapters = []

    metadata = build_metadata(state, chapters, templates, settings, scene_id=args.scene_id)

    sm.save_state(args.state, state)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"generated metadata -> {args.output}")
    print(f"  title: {metadata['title']}")


if __name__ == "__main__":
    main()
