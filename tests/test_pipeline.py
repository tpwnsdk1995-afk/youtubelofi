import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pipeline
import state_manager as sm


def make_dummy_library(dir_path, n=8, seconds=5):
    dir_path = Path(dir_path)
    dir_path.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        freq = 300 + i * 15
        out = dir_path / f"track_{i}.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
             "-c:a", "libmp3lame", "-b:a", "96k", str(out), "-loglevel", "error"],
            check=True,
        )
    return dir_path


class TestPipeline(unittest.TestCase):
    def test_dry_run_end_to_end(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            library_dir = make_dummy_library(root / "music_library")
            state_path = root / "state" / "state.json"
            work_dir = root / "work"

            settings = {
                "video": {
                    "target_duration_seconds": 10,
                    "resolution": "320x240",
                    "fps": 1,
                    "crf": 30,
                    "preset": "ultrafast",
                },
                "audio": {
                    "approx_track_seconds": 5,
                    "crossfade_seconds": 1,
                    "bitrate": "96k",
                },
                "image": {"aspect_ratio": "16:9", "output_format": "png"},
                "youtube": {"category_id": "10", "privacy_status": "private", "made_for_kids": False},
                "state_file": str(state_path),
                "music_library_dir": str(library_dir),
            }
            scenes_config = {"scenes": [{"id": "test_scene", "prompt": "a test scene"}]}
            templates = {
                "scene_display_names": {"test_scene": "Test Scene"},
                "activity_phrases": ["to test"],
                "title_connectors": ["lofi test"],
                "description_blurbs": ["A test description."],
                "description_footer": "footer",
                "tag_pool": ["tag1", "tag2", "tag3"],
            }

            settings_path = root / "settings.yml"
            scenes_path = root / "scenes.yml"
            templates_path = root / "templates.yml"
            settings_path.write_text(yaml.dump(settings), encoding="utf-8")
            scenes_path.write_text(yaml.dump(scenes_config), encoding="utf-8")
            templates_path.write_text(yaml.dump(templates), encoding="utf-8")

            fake_image_bytes = subprocess.run(
                ["ffmpeg", "-f", "lavfi", "-i", "color=c=navy:s=64x64", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                check=True, capture_output=True,
            ).stdout

            def fake_generate_image(prompt, api_key, aspect_ratio, output_format, output_path, session=None):
                Path(output_path).write_bytes(fake_image_bytes)

            with mock.patch.dict("os.environ", {"STABILITY_IMAGE_API_KEY": "fake-key"}, clear=False), \
                 mock.patch("generate_image.generate_image", side_effect=fake_generate_image):
                result = pipeline.run_pipeline(
                    str(settings_path), str(scenes_path), str(templates_path),
                    work_dir=str(work_dir), dry_run=True,
                )

            self.assertIsNone(result["video_id"])
            self.assertEqual(result["scene_id"], "test_scene")
            self.assertIn("Test Scene", result["title"])

            state = sm.load_state(str(state_path))
            self.assertEqual(len(state["recent_videos"]), 1)
            self.assertEqual(state["recent_videos"][0]["scene_id"], "test_scene")

            # dry-run에서도 work_dir은 정리되어야 한다
            self.assertFalse(work_dir.exists())

    def test_missing_image_api_key_raises_before_heavy_work(self):
        with tempfile.TemporaryDirectory() as root, \
             mock.patch.dict("os.environ", {}, clear=True):
            root = Path(root)
            settings_path = root / "settings.yml"
            settings_path.write_text(yaml.dump({
                "video": {"target_duration_seconds": 10, "resolution": "320x240", "fps": 1, "crf": 30, "preset": "ultrafast"},
                "audio": {"approx_track_seconds": 5, "crossfade_seconds": 1, "bitrate": "96k"},
                "image": {}, "youtube": {}, "state_file": str(root / "state.json"),
                "music_library_dir": str(root / "music_library"),
            }), encoding="utf-8")
            (root / "scenes.yml").write_text(yaml.dump({"scenes": []}), encoding="utf-8")
            (root / "templates.yml").write_text(yaml.dump({}), encoding="utf-8")

            with self.assertRaises(RuntimeError):
                pipeline.run_pipeline(str(settings_path), str(root / "scenes.yml"), str(root / "templates.yml"), dry_run=True)


if __name__ == "__main__":
    unittest.main()
