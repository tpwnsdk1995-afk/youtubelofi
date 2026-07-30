import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_video as bv


class TestBuildVideo(unittest.TestCase):
    def test_build_video_with_watermark_produces_valid_output(self):
        with tempfile.TemporaryDirectory() as d:
            image_path = Path(d) / "scene.png"
            audio_path = Path(d) / "audio.mp3"
            out_path = Path(d) / "out.mp4"

            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:s=640x360", "-frames:v", "1", str(image_path)],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                 "-c:a", "libmp3lame", "-b:a", "96k", str(audio_path)],
                check=True, capture_output=True,
            )

            bv.build_video(
                image_path, audio_path, out_path,
                resolution="320x240", fps=1, crf=30, preset="ultrafast", audio_bitrate="96k",
                watermark_text="noa music",
            )

            self.assertTrue(out_path.exists())
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(out_path)],
                check=True, capture_output=True, text=True,
            )
            self.assertAlmostEqual(float(probe.stdout.strip()), 3.0, delta=0.5)

    def test_build_video_without_watermark_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            image_path = Path(d) / "scene.png"
            audio_path = Path(d) / "audio.mp3"
            out_path = Path(d) / "out.mp4"

            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:s=640x360", "-frames:v", "1", str(image_path)],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-c:a", "libmp3lame", "-b:a", "96k", str(audio_path)],
                check=True, capture_output=True,
            )

            bv.build_video(
                image_path, audio_path, out_path,
                resolution="320x240", fps=1, crf=30, preset="ultrafast", audio_bitrate="96k",
            )
            self.assertTrue(out_path.exists())

    def test_escape_drawtext_handles_special_characters(self):
        escaped = bv._escape_drawtext("it's: 100%")
        self.assertNotIn("'", escaped.replace("\\'", ""))
        self.assertIn("\\:", escaped)
        self.assertIn("\\%", escaped)


if __name__ == "__main__":
    unittest.main()
