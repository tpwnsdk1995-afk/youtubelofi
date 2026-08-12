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

# [조선 컨셉] 씬별 styles 값(painterly/photoreal)에 따라 로테이션되는 그림체 문구.
# 레퍼런스 채널들(조선재즈 등)이 실사와 유화 컨셉아트를 섞어 쓰는 것을 따른다.
JOSEON_STYLE_SUFFIXES = {
    "painterly": (
        "lavish animated film concept art, rich oil painting texture, dramatic "
        "chiaroscuro lighting, glowing warm light sources, deep atmospheric shadows, "
        "vivid saturated colors, volumetric light rays, intricate environment details, "
        "masterpiece, cinematic composition, no text, no watermark"
    ),
    "photoreal": (
        "cinematic film still, shot on 35mm, shallow depth of field, dramatic key "
        "lighting, hyper detailed fabric textures, professional color grading, moody "
        "atmosphere, photorealistic, no text, no watermark"
    ),
    # 시그니처 마스코트(까치호랑이) 전용 — 조선재즈류 실사 사극과 확실히 구분되는 화풍
    "minhwa": (
        "Korean minhwa folk painting style, bold confident black outlines, flat "
        "vibrant obangsaek colors (red, cobalt blue, yellow, white, black), decorative "
        "stylized clouds pine trees and waves, subtle hanji paper texture, charming "
        "naive playful folk art like a Joseon woodblock print, whimsical and warm, "
        "no text, no watermark"
    ),
}


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _tag_compatible(a, b):
    """계절/시간대 태그 비교: 양쪽 다 값이 지정된 경우에만 충돌로 본다 (미지정=무관)."""
    return not a or not b or a == b


def scene_matches_situation(scene, situation):
    """씬과 상황 문구의 계절(season)/시간대(time) 태그가 충돌하지 않는지 검사한다.
    (예: '꽃놀이 봄' 문구에 눈 내리는 겨울 씬이 뽑히는 어색함 방지)"""
    if not situation:
        return True
    return (_tag_compatible(scene.get("season"), situation.get("season"))
            and _tag_compatible(scene.get("time"), situation.get("time")))


def draw_scene(state, scenes_config, rng=None, genre=None, pool_name="scene", situation=None):
    """씬을 셔플백으로 뽑는다. genre가 주어지면 그 무드에 어울리는 씬만 후보로 쓰고,
    무드별로 풀 이름을 분리해(pool_name) 셔플백 순환이 서로 섞이지 않게 한다.
    situation이 주어지면 계절/시간대가 충돌하는 씬을 후보에서 제외한다 (호환 씬이
    하나도 없으면 안전하게 무드 필터만 적용)."""
    scenes = scenes_config["scenes"]
    if genre is not None:
        scenes = [s for s in scenes if genre in s.get("genres", [])]
        if not scenes:
            raise ValueError(f"'{genre}' 무드에 해당하는 씬이 없습니다")
    if situation is not None:
        matching = [s for s in scenes if scene_matches_situation(s, situation)]
        if matching:
            scenes = matching
    scene_ids = [s["id"] for s in scenes]
    scene_id = sm.draw(state, pool_name, scene_ids, count=1, rng=rng)[0]
    return next(s for s in scenes if s["id"] == scene_id)


def draw_style(state, scene, rng=None):
    """씬이 허용하는 그림체(styles) 중 하나를 셔플백으로 뽑는다."""
    styles = scene.get("styles") or ["painterly"]
    return sm.draw(state, "image_style", styles, count=1, rng=rng)[0]


def draw_modifiers(state, scenes_config, rng=None):
    """조명/소품 문구를 각각 독립적으로 셔플백에서 하나씩 뽑는다. 같은 장소(scene)가
    다시 뽑히더라도 조합이 달라져 실제 생성 이미지가 매번 눈에 띄게 달라지게 한다."""
    modifiers = scenes_config.get("modifiers", {})
    lighting_pool = modifiers.get("lighting", [])
    detail_pool = modifiers.get("details", [])
    lighting = sm.draw(state, "image_lighting", lighting_pool, count=1, rng=rng)[0] if lighting_pool else None
    detail = sm.draw(state, "image_detail", detail_pool, count=1, rng=rng)[0] if detail_pool else None
    return lighting, detail


def build_full_prompt(scene_prompt, aspect_ratio_hint, extra_details=None, style_suffix=None):
    parts = [scene_prompt]
    parts.extend(d for d in (extra_details or []) if d)
    parts.append(style_suffix or PHOTOREALISM_SUFFIX)
    if aspect_ratio_hint:
        parts.append(aspect_ratio_hint)
    return ", ".join(parts)


def generate_image(prompt, api_key, model, aspect_ratio_hint, output_path, session=None,
                    max_attempts=4, backoff_seconds=5, sleep=time.sleep, extra_details=None,
                    style_suffix=None):
    """Gemini 이미지 생성 API를 호출한다. 과부하(503)/속도 제한(429) 등 일시적 오류는
    지수 백오프로 재시도하고(기본 최대 4회 시도: 0, 5, 10, 20초 대기), 그래도 실패하면
    에러를 그대로 올린다."""
    session = session or requests
    full_prompt = build_full_prompt(prompt, aspect_ratio_hint, extra_details=extra_details,
                                     style_suffix=style_suffix)

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
