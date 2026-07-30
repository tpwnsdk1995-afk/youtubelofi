import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_image as gi
import state_manager as sm


def fake_gemini_response(image_bytes):
    return mock.Mock(
        status_code=200,
        json=lambda: {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "here is your image"},
                            {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(image_bytes).decode()}},
                        ]
                    }
                }
            ]
        },
    )


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

        fake_session = mock.Mock()
        fake_session.post.return_value = fake_gemini_response(b"fake-png-bytes")

        with tempfile.TemporaryDirectory() as d:
            out_path = Path(d) / "scene.png"
            gi.generate_image(scene["prompt"], "fake-key", "gemini-2.5-flash-image", "16:9 widescreen", out_path, session=fake_session)
            self.assertEqual(out_path.read_bytes(), b"fake-png-bytes")

        fake_session.post.assert_called_once()
        _, kwargs = fake_session.post.call_args
        self.assertIn(scene["prompt"], kwargs["json"]["contents"][0]["parts"][0]["text"])
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "fake-key")

    def test_generate_image_raises_on_error_status(self):
        fake_session = mock.Mock()
        fake_session.post.return_value = mock.Mock(status_code=402, text="payment required")
        with self.assertRaises(RuntimeError):
            gi.generate_image("prompt", "key", "gemini-2.5-flash-image", "16:9 widescreen", "/tmp/x.png", session=fake_session)

    def test_generate_image_raises_when_no_image_in_response(self):
        fake_session = mock.Mock()
        fake_session.post.return_value = mock.Mock(
            status_code=200,
            json=lambda: {"candidates": [{"content": {"parts": [{"text": "no image, sorry"}]}}]},
        )
        with self.assertRaises(RuntimeError):
            gi.generate_image("prompt", "key", "gemini-2.5-flash-image", "16:9 widescreen", "/tmp/x.png", session=fake_session)


if __name__ == "__main__":
    unittest.main()
