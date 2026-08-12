import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_metadata as gm


def make_templates():
    return {
        "title_prefix": "Playlist (가사X)",
        "hook_phrases": ["새벽 공부 루틴", "미친 집중력 모드"],
        "taglines": ["집중할 때 흐르는 음악", "ADHD도 공부하게 만드는 음악"],
        "title_emojis": ["🌙", "🎧"],
        "description_blurbs": ["새벽까지 이어지는 잔잔한 믹스입니다."],
        "channel_name": "noa music",
        "description_footer": "footer line 1\nfooter line 2",
        "top_hashtags": ["#공부플리", "#lofimusic", "#lofijazz"],
        "tag_pool": [f"tag{i}" for i in range(10)],
    }


def make_video_settings():
    return {
        "youtube": {"category_id": "10", "privacy_status": "private", "made_for_kids": False},
    }


class TestFormatTimestamp(unittest.TestCase):
    def test_under_an_hour(self):
        self.assertEqual(gm.format_timestamp(258), "4:18")

    def test_over_an_hour(self):
        self.assertEqual(gm.format_timestamp(3723), "1:02:03")

    def test_zero(self):
        self.assertEqual(gm.format_timestamp(0), "0:00")


class TestBuildMetadata(unittest.TestCase):
    def test_title_uses_expected_pattern(self):
        state = {}
        title = gm.build_title(state, make_templates(), rng=random.Random(1))
        self.assertTrue(title.startswith("Playlist (가사X) "))

    def test_description_includes_tracklist_and_footer(self):
        state = {}
        chapters = [
            {"filename": "a.mp3", "name": "Softmere", "start_seconds": 0.0, "duration_seconds": 180.0},
            {"filename": "b.mp3", "name": "Dimcrest", "start_seconds": 177.0, "duration_seconds": 200.0},
        ]
        description, tags = gm.build_description(state, make_templates(), chapters, rng=random.Random(2))
        self.assertIn("0:00 Softmere", description)
        self.assertIn("2:57 Dimcrest", description)
        self.assertIn("footer line 1", description)
        self.assertIn("#공부플리 #lofimusic #lofijazz", description)
        self.assertEqual(len(tags), len(set(tags)))

    def test_build_metadata_end_to_end(self):
        state = {}
        chapters = [{"filename": "a.mp3", "name": "Softmere", "start_seconds": 0.0, "duration_seconds": 180.0}]
        metadata = gm.build_metadata(state, chapters, make_templates(), make_video_settings(), scene_id="rainy_window", rng=random.Random(3))
        self.assertEqual(metadata["categoryId"], "10")
        self.assertEqual(metadata["privacyStatus"], "private")
        self.assertFalse(metadata["madeForKids"])
        self.assertTrue(metadata["containsSyntheticMedia"])
        self.assertEqual(metadata["scene_id"], "rainy_window")
        self.assertIn("Softmere", metadata["description"])


if __name__ == "__main__":
    unittest.main()
