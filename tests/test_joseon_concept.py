"""조선 리브랜딩 (concept: joseon) 전용 테스트: 무드 로테이션, 상황-썸네일 커플링,
썸네일 생성, 무드별 씬 필터."""

import random
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_image as gi
import generate_metadata as gm
import make_thumbnail


def make_joseon_templates():
    return {
        "title_prefix": "Playlist",
        "genre_rotation": ["calm", "calm", "groove"],
        "genres": {
            "calm": {
                "label": "조선 로파이",
                "playlist_title": "조선 로파이 | 공부·집중",
                "situations": [
                    {"id": "gwageo_d1", "title": "과거시험 D-1, 전국 1등 유생이 듣던 소리",
                     "thumb_main": "과거시험 D-1", "thumb_sub": "전국 1등 유생의 조선 로파이"},
                    {"id": "hunjang", "title": "서당에서 훈장님 몰래 듣던 소리",
                     "thumb_main": "훈장님 몰래", "thumb_sub": "듣는 조선 로파이"},
                ],
                "taglines": ["집중력 풀충전 조선 로파이"],
                "description_blurbs": ["조선 서당 감성 믹스입니다."],
                "top_hashtags": ["#공부플리", "#조선로파이"],
                "extra_tags": ["공부음악", "집중음악"],
            },
            "groove": {
                "label": "조선 로파이",
                "playlist_title": "조선 로파이 | 산책·드라이브",
                "situations": [
                    {"id": "night_stroll", "title": "한양 밤거리를 걷는 기분",
                     "thumb_main": "한양 밤산책", "thumb_sub": "신나는 조선 로파이"},
                ],
                "taglines": ["산책할 때 듣는 신나는 조선 로파이"],
                "description_blurbs": ["흥겨운 국악 로파이예요."],
                "top_hashtags": ["#조선로파이", "#플레이리스트"],
                "extra_tags": ["산책음악", "드라이브음악"],
            },
        },
        "title_emojis": ["📜", "🏮"],
        "channel_name": "조선로파이",
        "description_footer": "footer line",
        "tag_pool": [f"tag{i}" for i in range(15)],
    }


def make_video_settings():
    return {"youtube": {"category_id": "10", "privacy_status": "private", "made_for_kids": False}}


class TestGenreRotation(unittest.TestCase):
    def test_rotation_is_weighted(self):
        state = {}
        templates = make_joseon_templates()
        rng = random.Random(1)
        draws = [gm.draw_genre(state, templates, rng=rng) for _ in range(30)]
        counts = Counter(draws)
        self.assertEqual(counts["calm"], 20)
        self.assertEqual(counts["groove"], 10)


class TestJoseonMetadata(unittest.TestCase):
    def test_title_and_thumb_come_from_same_situation(self):
        state = {}
        templates = make_joseon_templates()
        metadata = gm.build_metadata(state, [], templates, make_video_settings(),
                                     rng=random.Random(2), genre="calm")
        situations = templates["genres"]["calm"]["situations"]
        matched = [s for s in situations if s["thumb_main"] == metadata["thumb_main"]]
        self.assertEqual(len(matched), 1)
        self.assertIn(matched[0]["title"], metadata["title"])
        self.assertEqual(metadata["thumb_sub"], matched[0]["thumb_sub"])
        self.assertEqual(metadata["genre"], "calm")
        self.assertLessEqual(len(metadata["title"]), 100)

    def test_groove_uses_groove_pools(self):
        state = {}
        templates = make_joseon_templates()
        metadata = gm.build_metadata(state, [], templates, make_video_settings(),
                                     rng=random.Random(3), genre="groove")
        self.assertEqual(metadata["thumb_main"], "한양 밤산책")
        self.assertIn("#조선로파이", metadata["description"])
        for tag in ["산책음악", "드라이브음악"]:
            self.assertIn(tag, metadata["tags"])
        self.assertEqual(len(metadata["tags"]), len(set(metadata["tags"])))

    def test_legacy_templates_still_work(self):
        state = {}
        legacy = {
            "title_prefix": "Playlist (가사X)",
            "hook_phrases": ["훅1", "훅2"],
            "taglines": ["태그라인"],
            "title_emojis": ["🌙"],
            "description_blurbs": ["블러브"],
            "description_footer": "footer",
            "top_hashtags": ["#a", "#b"],
            "tag_pool": [f"t{i}" for i in range(5)],
        }
        metadata = gm.build_metadata(state, [], legacy, make_video_settings(), rng=random.Random(4))
        self.assertTrue(metadata["title"].startswith("Playlist (가사X)"))
        self.assertNotIn("thumb_main", metadata)


class TestSceneGenreFilter(unittest.TestCase):
    def make_scenes(self):
        return {"scenes": [
            {"id": "a", "genres": ["calm"], "styles": ["painterly"], "prompt": "pa"},
            {"id": "b", "genres": ["groove"], "styles": ["photoreal"], "prompt": "pb"},
            {"id": "c", "genres": ["calm", "groove"], "styles": ["painterly", "photoreal"], "prompt": "pc"},
        ]}

    def test_filter_by_genre(self):
        state = {}
        rng = random.Random(5)
        for _ in range(10):
            scene = gi.draw_scene(state, self.make_scenes(), rng=rng, genre="calm", pool_name="scene_calm")
            self.assertIn(scene["id"], {"a", "c"})

    def test_style_drawn_from_scene_styles(self):
        state = {}
        rng = random.Random(6)
        scene = {"id": "x", "styles": ["photoreal"]}
        self.assertEqual(gi.draw_style(state, scene, rng=rng), "photoreal")

    def test_situation_filters_incompatible_scenes(self):
        state = {}
        rng = random.Random(7)
        scenes = {"scenes": [
            {"id": "night_sc", "genres": ["groove"], "styles": ["painterly"], "time": "night", "prompt": "p"},
            {"id": "spring_day_sc", "genres": ["groove"], "styles": ["painterly"], "season": "spring", "time": "day", "prompt": "p"},
            {"id": "any_sc", "genres": ["groove"], "styles": ["painterly"], "prompt": "p"},
        ]}
        situation = {"id": "spring_picnic", "season": "spring", "time": "day"}
        for _ in range(10):
            scene = gi.draw_scene(state, scenes, rng=rng, genre="groove",
                                  pool_name="scene_groove_t", situation=situation)
            self.assertIn(scene["id"], {"spring_day_sc", "any_sc"})

    def test_situation_filter_falls_back_when_no_match(self):
        state = {}
        rng = random.Random(8)
        scenes = {"scenes": [
            {"id": "night_sc", "genres": ["calm"], "styles": ["painterly"], "time": "night", "prompt": "p"},
        ]}
        situation = {"id": "day_only", "time": "day"}
        scene = gi.draw_scene(state, scenes, rng=rng, genre="calm",
                              pool_name="scene_calm_t", situation=situation)
        self.assertEqual(scene["id"], "night_sc")

    def test_style_suffix_applied_to_prompt(self):
        prompt = gi.build_full_prompt("scene", "16:9", style_suffix=gi.JOSEON_STYLE_SUFFIXES["painterly"])
        self.assertIn("oil painting", prompt)
        self.assertNotIn("phone camera", prompt)


class TestMakeThumbnail(unittest.TestCase):
    def test_creates_jpeg_under_limit(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "scene.png"
            Image.new("RGB", (1920, 1080), (40, 30, 60)).save(src)
            out = Path(td) / "thumb.jpg"
            make_thumbnail.create_thumbnail(src, "과거시험 D-1", "전국 1등 유생의 조선 로파이", out)
            self.assertTrue(out.exists())
            self.assertLessEqual(out.stat().st_size, make_thumbnail.MAX_BYTES)
            with Image.open(out) as img:
                self.assertEqual(img.size, make_thumbnail.THUMB_SIZE)


if __name__ == "__main__":
    unittest.main()
