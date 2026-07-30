import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_image as gi
import state_manager as sm


class TestGenerateImage(unittest.TestCase):
    def test_draw_scene_and_generate_writes_file_and_sidecar(self):
        scenes_config = {
            "scenes": [
                {"id": "a", "prompt": "prompt a"},
                {"id": "b", "prompt": "prompt b"},
            ]
        }
        state = {}
        scene = gi.draw_scene(state, scenes_config)
        self.assertIn(scene["id"], ("a", "b"))

        fake_resp = mock.Mock(status_code=200, content=b"fake-png-bytes")
        fake_session = mock.Mock()
        fake_session.post.return_value = fake_resp

        with tempfile.TemporaryDirectory() as d:
            out_path = Path(d) / "scene.png"
            gi.generate_image(scene["prompt"], "fake-key", "16:9", "png", out_path, session=fake_session)
            self.assertEqual(out_path.read_bytes(), b"fake-png-bytes")

        fake_session.post.assert_called_once()
        _, kwargs = fake_session.post.call_args
        self.assertEqual(kwargs["data"]["prompt"], scene["prompt"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fake-key")

    def test_generate_image_raises_on_error_status(self):
        fake_resp = mock.Mock(status_code=402, text="payment required")
        fake_session = mock.Mock()
        fake_session.post.return_value = fake_resp
        with self.assertRaises(RuntimeError):
            gi.generate_image("prompt", "key", "16:9", "png", "/tmp/x.png", session=fake_session)


if __name__ == "__main__":
    unittest.main()
