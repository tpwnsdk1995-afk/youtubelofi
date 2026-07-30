"""Gemini API(gemini-2.5-flash-image, 일명 나노바나나)로 씬 이미지를 1장 생성한다.
무료 등급으로 하루 500장까지 가능해 비용 $0. 씬은 config/scenes.yml 풀에서
state_manager 셔플백으로 로테이션.
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import requests
import yaml

import state_manager as sm

GEMINI_ENDPOINT_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def draw_scene(state, scenes_config, rng=None):
    scenes = scenes_config["scenes"]
    scene_ids = [s["id"] for s in scenes]
    scene_id = sm.draw(state, "scene", scene_ids, count=1, rng=rng)[0]
    return next(s for s in scenes if s["id"] == scene_id)


def generate_image(prompt, api_key, model, aspect_ratio_hint, output_path, session=None):
    session = session or requests
    full_prompt = f"{prompt}, {aspect_ratio_hint}" if aspect_ratio_hint else prompt

    resp = session.post(
        GEMINI_ENDPOINT_TEMPLATE.format(model=model),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini image API error {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    image_bytes = None
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline_data = part.get("inlineData")
            if inline_data and inline_data.get("data"):
                image_bytes = base64.b64decode(inline_data["data"])
                break
        if image_bytes:
            break

    if image_bytes is None:
        raise RuntimeError(f"Gemini 응답에 이미지 데이터가 없습니다: {json.dumps(data)[:500]}")

    Path(output_path).write_bytes(image_bytes)


def main():
    parser = argparse.ArgumentParser(description="씬 이미지 1장을 생성한다")
    parser.add_argument("--scenes-config", default="config/scenes.yml")
    parser.add_argument("--settings-config", default="config/settings.yml")
    parser.add_argument("--state", default="state/state.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.", file=sys.stderr)
        sys.exit(1)

    scenes_config = load_yaml(args.scenes_config)
    settings = load_yaml(args.settings_config)
    state = sm.load_state(args.state)

    scene = draw_scene(state, scenes_config)
    image_cfg = settings["image"]

    generate_image(
        scene["prompt"],
        api_key,
        image_cfg.get("model", "gemini-2.5-flash-image"),
        image_cfg.get("aspect_ratio_hint", "16:9 widescreen"),
        args.output,
    )

    sm.save_state(args.state, state)

    sidecar = Path(args.output).with_suffix(".json")
    sidecar.write_text(json.dumps({"scene_id": scene["id"]}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"generated image for scene '{scene['id']}' -> {args.output}")


if __name__ == "__main__":
    main()
