"""제목/설명/태그를 고정 템플릿이 아닌 여러 문구 풀의 조합으로 생성한다.
씬 x 활동문구 x 연결어 x 설명문구가 각각 독립적으로 로테이션되어 매번 다른 조합이 나온다.
"""

import argparse
import json

import yaml

import state_manager as sm


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_metadata(state, scene_id, templates, video_settings, rng=None):
    scene_name = templates["scene_display_names"].get(scene_id, scene_id.replace("_", " ").title())

    activity_phrase = sm.draw(state, "activity_phrase", templates["activity_phrases"], count=1, rng=rng)[0]
    title_connector = sm.draw(state, "title_connector", templates["title_connectors"], count=1, rng=rng)[0]
    blurb = sm.draw(state, "description_blurb", templates["description_blurbs"], count=1, rng=rng)[0]

    tag_pool = templates["tag_pool"]
    tag_count = min(6, len(tag_pool))
    tags = sm.draw(state, "tag", tag_pool, count=tag_count, rng=rng)

    title = f"{scene_name} — {title_connector} {activity_phrase}"
    description = "\n\n".join([blurb, templates.get("description_footer", "").strip()])

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "categoryId": video_settings["youtube"]["category_id"],
        "privacyStatus": video_settings["youtube"]["privacy_status"],
        "madeForKids": video_settings["youtube"]["made_for_kids"],
        "scene_id": scene_id,
    }


def main():
    parser = argparse.ArgumentParser(description="제목/설명/태그 메타데이터를 생성한다")
    parser.add_argument("--scene-id", required=True)
    parser.add_argument("--templates-config", default="config/title_templates.yml")
    parser.add_argument("--settings-config", default="config/settings.yml")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    templates = load_yaml(args.templates_config)
    settings = load_yaml(args.settings_config)
    state = sm.load_state(args.state)

    metadata = build_metadata(state, args.scene_id, templates, settings)

    sm.save_state(args.state, state)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"generated metadata -> {args.output}")
    print(f"  title: {metadata['title']}")


if __name__ == "__main__":
    main()
