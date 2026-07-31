"""Gemini API(gemini-2.5-flash-image, 일명 나노바나나)로 씬 이미지를 1장 생성한다.
무료 등급으로 하루 500장까지 가능해 비용 $0. 씬은 config/scenes.yml 풀에서
state_manager 셔플백으로 로테이션.
"""

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml

import state_manager as sm

GEMINI_ENDPOINT_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# 일시적 장애(과부하, 속도 제한 등)로 간주해 재시도할 상태 코드.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# 모든 씬 프롬프트에 공통으로 덧붙이는 실사(candid photo) 스타일 지정 문구.
# scenes.yml 각 항목에는 장면 내용만 적고, 사진처럼 보이게 하는 스타일 지시는 여기서 통일 관리한다.
PHOTOREALISM_SUFFIX = (
    "candid photo taken with a phone camera from a natural angle, everyday snapshot, "
    "slightly imperfect casual framing, soft natural or warm lamp lighting, "
    "realistic textures and materials, not illustration, not painting, not anime, "
    "not 3d render, not digital art, no text, no watermark"
)


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def draw_scene(state, scenes_config, rng=None):
    scenes = scenes_config["scenes"]
    scene_ids = [s["id"] for s in scenes]
    scene_id = sm.draw(state, "scene", scene_ids, count=1, rng=rng)[0]
    return next(s for s in scenes if s["id"] == scene_id)


def draw_modifiers(state, scenes_config, rng=None):
    """조명/소품 문구를 각각 독립적으로 셔플백에서 하나씩 뽑는다. 같은 장소(scene)가
    다시 뽑히더라도 조합이 달라져 실제 생성 이미지가 매번 눈에 띄게 달라지게 한다."""
    modifiers = scenes_config.get("modifiers", {})
    lighting_pool = modifiers.get("lighting", [])
    detail_pool = modifiers.get("details", [])
    lighting = sm.draw(state, "image_lighting", lighting_pool, count=1, rng=rng)[0] if lighting_pool else None
    detail = sm.draw(state, "image_detail", detail_pool, count=1, rng=rng)[0] if detail_pool else None
    return lighting, detail


def build_full_prompt(scene_prompt, aspect_ratio_hint, extra_details=None):
    parts = [scene_prompt]
    parts.extend(d for d in (extra_details or []) if d)
    parts.append(PHOTOREALISM_SUFFIX)
    if aspect_ratio_hint:
        parts.append(aspect_ratio_hint)
    return ", ".join(parts)


def generate_image(prompt, api_key, model, aspect_ratio_hint, output_path, session=None,
                    max_attempts=4, backoff_seconds=5, sleep=time.sleep, extra_details=None):
    """Gemini 이미지 생성 API를 호출한다. 과부하(503)/속도 제한(429) 등 일시적 오류는
    지수 백오프로 재시도하고(기본 최대 4회 시도: 0, 5, 10, 20초 대기), 그래도 실패하면
    에러를 그대로 올린다."""
    session = session or requests
    full_prompt = build_full_prompt(prompt, aspect_ratio_hint, extra_details=extra_details)

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = session.post(
                GEMINI_ENDPOINT_TEMPLATE.format(model=model),
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": full_prompt}]}],
                    "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
                },
                timeout=60,
            )
        except requests.exceptions.RequestException as e:
            last_error = RuntimeError(f"Gemini image API 요청 실패: {e}")
            resp = None

        if resp is not None:
            if resp.status_code == 200:
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
                return

            last_error = RuntimeError(f"Gemini image API error {resp.status_code}: {resp.text[:500]}")
            if resp.status_code not in RETRYABLE_STATUS_CODES:
                raise last_error

        if attempt < max_attempts:
            wait = backoff_seconds * (2 ** (attempt - 1))
            print(f"WARNING: {last_error} - {wait}초 후 재시도 ({attempt}/{max_attempts})", file=sys.stderr)
            sleep(wait)

    raise last_error


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
    lighting, detail = draw_modifiers(state, scenes_config)
    image_cfg = settings["image"]

    generate_image(
        scene["prompt"],
        api_key,
        image_cfg.get("model", "gemini-2.5-flash-image"),
        image_cfg.get("aspect_ratio_hint", "16:9 widescreen"),
        args.output,
        extra_details=[lighting, detail],
    )

    sm.save_state(args.state, state)

    sidecar = Path(args.output).with_suffix(".json")
    sidecar.write_text(json.dumps({"scene_id": scene["id"]}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"generated image for scene '{scene['id']}' -> {args.output}")


if __name__ == "__main__":
    main()
