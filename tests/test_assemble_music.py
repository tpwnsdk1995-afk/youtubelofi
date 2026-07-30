import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import assemble_music as am


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


class TestPickTracks(unittest.TestCase):
    def test_no_reuse_uses_each_file_exactly_once(self):
        with tempfile.TemporaryDirectory() as d:
            library_dir = make_dummy_library(d, n=6)
            files = am.list_library_files(library_dir)
            state = {}
            picked = am.pick_tracks(state, library_dir, files, reuse_ratio=0.0, rng=random.Random(1))
            names = [name for name, _ in picked]
            self.assertEqual(sorted(names), sorted(files))

    def test_reuse_ratio_adds_extra_plays(self):
        with tempfile.TemporaryDirectory() as d:
            library_dir = make_dummy_library(d, n=8)
            files = am.list_library_files(library_dir)
            state = {}
            picked = am.pick_tracks(state, library_dir, files, reuse_ratio=0.25, rng=random.Random(2))
            # 8곡 x 1.25 = 10곡 분량이 나와야 함 (일부는 두 번)
            self.assertEqual(len(picked), 10)
            names = [name for name, _ in picked]
            self.assertEqual(set(names), set(files))  # 모든 곡이 최소 1번은 등장

    def test_full_assemble_produces_longer_than_baseline_with_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            library_dir = make_dummy_library(d, n=6, seconds=4)
            state = {}
            out_no_reuse = Path(d) / "no_reuse.mp3"
            out_with_reuse = Path(d) / "with_reuse.mp3"

            am.assemble(library_dir, {}, crossfade_seconds=1, bitrate="96k",
                        output_path=out_no_reuse, reuse_ratio=0.0, rng=random.Random(3))
            am.assemble(library_dir, {}, crossfade_seconds=1, bitrate="96k",
                        output_path=out_with_reuse, reuse_ratio=0.5, rng=random.Random(4))

            dur_no_reuse = am.probe_duration(out_no_reuse)
            dur_with_reuse = am.probe_duration(out_with_reuse)
            self.assertGreater(dur_with_reuse, dur_no_reuse)

    def test_empty_library_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(am.LibraryTooSmallError):
                am.assemble(d, {}, crossfade_seconds=1, bitrate="96k", output_path=Path(d) / "out.mp3")


class TestBuildChapters(unittest.TestCase):
    PREFIXES = ["Soft", "Dim", "Hush"]
    SUFFIXES = ["mere", "crest", "loom"]

    def test_first_chapter_starts_at_zero_and_names_are_unique(self):
        state = {}
        picked = [("a.mp3", 100.0), ("b.mp3", 120.0), ("c.mp3", 90.0)]
        chapters = am.build_chapters(state, picked, self.PREFIXES, self.SUFFIXES, crossfade_seconds=3, rng=random.Random(1))
        self.assertEqual(chapters[0]["start_seconds"], 0.0)
        names = [c["name"] for c in chapters]
        self.assertEqual(len(names), len(set(names)))

    def test_start_seconds_accounts_for_crossfade_overlap(self):
        state = {}
        picked = [("a.mp3", 100.0), ("b.mp3", 120.0)]
        chapters = am.build_chapters(state, picked, self.PREFIXES, self.SUFFIXES, crossfade_seconds=3, rng=random.Random(1))
        self.assertEqual(chapters[1]["start_seconds"], 100.0 - 3)

    def test_reused_track_keeps_same_name_each_occurrence(self):
        state = {}
        picked = [("a.mp3", 100.0), ("b.mp3", 50.0), ("a.mp3", 100.0)]
        chapters = am.build_chapters(state, picked, self.PREFIXES, self.SUFFIXES, crossfade_seconds=2, rng=random.Random(1))
        self.assertEqual(chapters[0]["name"], chapters[2]["name"])

    def test_assemble_returns_chapters_with_persistent_names(self):
        with tempfile.TemporaryDirectory() as d:
            library_dir = make_dummy_library(d, n=4, seconds=3)
            state = {}
            out_path = Path(d) / "out.mp3"
            chapters = am.assemble(
                library_dir, state, crossfade_seconds=1, bitrate="96k", output_path=out_path,
                reuse_ratio=0.0, name_prefixes=self.PREFIXES, name_suffixes=self.SUFFIXES, rng=random.Random(2),
            )
            self.assertEqual(len(chapters), 4)
            self.assertEqual(chapters[0]["start_seconds"], 0.0)
            self.assertIn("track_names", state)


if __name__ == "__main__":
    unittest.main()
