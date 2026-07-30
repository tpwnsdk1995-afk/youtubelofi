"""Stability AI Stable Image Core API로 씬 이미지를 1장 생성한다.
씬은 config/scenes.yml 풀에서 state_manager 셔플백으로 로테이션.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests
import yaml

import state_manager as sm

STABLE_IMAGE_ENDPOINT = "https://api.stability.ai/v2beta/stable-image/generate/core"


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def draw_scene(state, scenes_config, rng=None):
    scenes = scenes_config["scenes"]
    scene_ids = [s["id"] for s in scenes]
    scene_id = sm.draw(state, "scene", scene_ids, count=1, rng=rng)[0]
    return next(s for s in scenes if s["id"] == scene_id)


def generate_image(prompt, api_key, aspect_ratio, output_format, output_path, session=None):
    session = session or requests
    resp = session.post(
        STABLE_IMAGE_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "image/*"},
        files={"none": (None, "")},
        data={"prompt": prompt, "aspect_ratio": aspect_ratio, "output_format": output_format},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Stability AI image API error {resp.status_code}: {resp.text[:500]}")
    Path(output_path).write_bytes(resp.content)


def main():
    parser = argparse.ArgumentParser(description="씬 이미지 1장을 생성한다")
    parser.add_argument("--scenes-config", default="config/scenes.yml")
    parser.add_argument("--settings-config", default="config/settings.yml")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    api_key = os.environ.get("STABILITY_IMAGE_API_KEY")
    if not api_key:
        print("ERROR: STABILITY_IMAGE_API_KEY 환경변수가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    scenes_config = load_yaml(args.scenes_config)
    settings = load_yaml(args.settings_config)
    state = sm.load_state(args.state)

    scene = draw_scene(state, scenes_config)
    image_cfg = settings["image"]

    generate_image(
        scene["prompt"],
        api_key,
        image_cfg.get("aspect_ratio", "16:9"),
        image_cfg.get("output_format", "png"),
        args.output,
    )

    sm.save_state(args.state, state)

    sidecar = Path(args.output).with_suffix(".json")
    sidecar.write_text(json.dumps({"scene_id": scene["id"]}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"generated image for scene '{scene['id']}' -> {args.output}")


if __name__ == "__main__":
    main()
