import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import state_manager as sm


class TestShuffleBag(unittest.TestCase):
    def test_no_repeat_until_pool_exhausted(self):
        state = {}
        pool = ["a", "b", "c", "d"]
        drawn = sm.draw(state, "test", pool, count=4, rng=random.Random(0))
        self.assertEqual(sorted(drawn), sorted(pool))

    def test_reshuffles_after_exhaustion(self):
        state = {}
        pool = ["a", "b", "c"]
        first = sm.draw(state, "test", pool, count=3, rng=random.Random(1))
        second = sm.draw(state, "test", pool, count=3, rng=random.Random(1))
        self.assertEqual(sorted(first), sorted(pool))
        self.assertEqual(sorted(second), sorted(pool))

    def test_new_items_get_added(self):
        state = {}
        sm.draw(state, "test", ["a", "b"], count=2, rng=random.Random(2))
        drawn = sm.draw(state, "test", ["a", "b", "c"], count=3, rng=random.Random(2))
        self.assertIn("c", drawn)

    def test_removed_items_dropped(self):
        state = {}
        sm.draw(state, "test", ["a", "b", "c"], count=1, rng=random.Random(3))
        # 'a'가 라이브러리에서 삭제된 상황을 시뮬레이션
        drawn = sm.draw(state, "test", ["b", "c"], count=2, rng=random.Random(3))
        self.assertNotIn("a", drawn)
        self.assertTrue(all(item in ("b", "c") for item in drawn))

    def test_save_and_load_roundtrip(self):
        import tempfile

        state = {}
        sm.draw(state, "test", ["a", "b", "c"], count=2, rng=random.Random(4))
        sm.record_video(state, {"video_id": "xyz"})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            sm.save_state(str(path), state)
            loaded = sm.load_state(str(path))
        self.assertEqual(loaded["recent_videos"][0]["video_id"], "xyz")
        self.assertEqual(loaded["pools"]["test"]["history"], state["pools"]["test"]["history"])

    def test_load_missing_file_returns_default(self):
        state = sm.load_state("/nonexistent/path/state.json")
        self.assertEqual(state, {"pools": {}, "recent_videos": []})

    def test_no_duplicates_within_single_call_when_pool_large_enough(self):
        state = {}
        pool = [str(i) for i in range(10)]
        # 먼저 6개를 뽑아 remaining을 4개만 남긴 뒤, 다음 호출에서 6개를 요청해
        # 재셔플이 호출 중간에 발생하도록 유도한다.
        sm.draw(state, "test", pool, count=6, rng=random.Random(5))
        drawn = sm.draw(state, "test", pool, count=6, rng=random.Random(5))
        self.assertEqual(len(drawn), len(set(drawn)))


if __name__ == "__main__":
    unittest.main()
