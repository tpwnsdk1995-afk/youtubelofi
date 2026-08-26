import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import suno_helper as sh
import weekly_report as wr

CFG = wr.load_yaml(str(Path(__file__).resolve().parents[1] / "config" / "suno_prompts.yml"))


def mp3(name, size=4_300_000, md5=None):
    return {"name": name, "mimeType": "audio/mpeg", "size": str(size), "md5Checksum": md5 or name}


class TestAudit(unittest.TestCase):
    def test_counts_healthy_files(self):
        a = sh.audit_folder([mp3(f"{i}.mp3") for i in range(10)])
        self.assertEqual(a["effective"], 10)
        self.assertEqual(a["wrong_type"], [])

    def test_flags_non_mp3_and_excludes_from_count(self):
        """파이프라인은 audio/mpeg만 읽는다. m4a를 올리면 Drive에는 보이는데
        재고는 안 늘어나므로 사장님이 원인을 알 수 없다."""
        files = [mp3("ok.mp3"), {"name": "x.m4a", "mimeType": "audio/mp4", "size": "4000000"}]
        a = sh.audit_folder(files)
        self.assertEqual(a["effective"], 1)
        self.assertEqual(len(a["wrong_type"]), 1)

    def test_flags_truncated_file(self):
        a = sh.audit_folder([mp3("ok.mp3"), mp3("cut.mp3", size=100_000)])
        self.assertEqual(a["effective"], 1)
        self.assertEqual(len(a["too_small"]), 1)

    def test_exact_duplicates_do_not_count_as_variety(self):
        files = [mp3("a.mp3", md5="SAME"), mp3("b.mp3", md5="SAME"), mp3("c.mp3")]
        a = sh.audit_folder(files)
        self.assertEqual(len(a["usable"]), 3)
        self.assertEqual(a["duplicate_waste"], 1)
        self.assertEqual(a["effective"], 2)

    def test_ignores_nested_folders(self):
        files = [mp3("a.mp3"), {"name": "sub", "mimeType": "application/vnd.google-apps.folder"}]
        self.assertEqual(sh.audit_folder(files)["effective"], 1)

    def test_missing_size_metadata_is_not_treated_as_truncated(self):
        a = sh.audit_folder([{"name": "a.mp3", "mimeType": "audio/mpeg", "md5Checksum": "x"}])
        self.assertEqual(a["effective"], 1)
        self.assertEqual(a["too_small"], [])


class TestNeededCounts(unittest.TestCase):
    def test_shortfall_uses_effective_not_raw(self):
        audit = {"calm": sh.audit_folder(
            [mp3("a.mp3", md5="S"), mp3("b.mp3", md5="S")] + [mp3(f"{i}.mp3") for i in range(8)])}
        # 파일은 10개지만 중복 1개를 빼면 실질 9곡
        self.assertEqual(sh.needed_counts(audit)["calm"], sh.TARGET_PER_MOOD - 9)

    def test_no_shortfall_when_target_met(self):
        audit = {"calm": sh.audit_folder([mp3(f"{i}.mp3") for i in range(sh.TARGET_PER_MOOD)])}
        self.assertEqual(sh.needed_counts(audit)["calm"], 0)


class TestPrompts(unittest.TestCase):
    def test_prompt_has_required_elements(self):
        p = sh.build_prompt(CFG, "calm", {}, random.Random(1))
        self.assertIn("BPM", p)
        self.assertIn("instrumental", p)
        self.assertIn("no vocals", p)
        self.assertTrue(p.startswith("Korean traditional lo-fi hip hop"))

    def test_consecutive_prompts_differ(self):
        """같은 프롬프트로 40곡을 뽑으면 40곡이 다 비슷해진다 — 이걸 막는 것이 요점이다."""
        state = {}
        rng = random.Random(3)
        prompts = [sh.build_prompt(CFG, "calm", state, rng) for _ in range(20)]
        self.assertEqual(len(set(prompts)), 20)

    def test_both_moods_are_configured(self):
        self.assertEqual(set(CFG["moods"]), {"calm", "groove"})

    def test_no_internal_commas_in_building_blocks(self):
        """조각 안에 쉼표가 있으면 조합 구분자와 섞여 수노가 잘못 읽는다."""
        for mood, spec in CFG["moods"].items():
            for axis in ("lead", "rhythm", "texture", "mood"):
                for value in spec[axis]:
                    self.assertNotIn(",", value, f"{mood}.{axis}: {value}")

    def test_groove_is_faster_than_calm(self):
        self.assertGreater(min(CFG["moods"]["groove"]["bpm"]), max(CFG["moods"]["calm"]["bpm"]))


class TestReport(unittest.TestCase):
    def _audit(self):
        return {
            "calm": sh.audit_folder([mp3(f"c{i}.mp3") for i in range(58)]),
            "groove": sh.audit_folder([mp3(f"g{i}.mp3") for i in range(60)]),
        }

    def test_report_states_shortfall_and_gives_prompts(self):
        out = sh.build_report(CFG, self._audit(), {}, random.Random(5), limit=3)
        self.assertIn("한 줄 요약", out)
        self.assertIn("calm 42곡", out)
        self.assertIn("groove 40곡", out)
        self.assertIn("Instrumental 토글", out)
        self.assertIn("BPM", out)

    def test_report_when_nothing_needed(self):
        full = {"calm": sh.audit_folder([mp3(f"c{i}.mp3") for i in range(sh.TARGET_PER_MOOD)])}
        out = sh.build_report(CFG, full, {}, random.Random(5))
        self.assertIn("보충하실 것이 없습니다", out)
        self.assertNotIn("BPM", out)

    def test_report_avoids_telegram_markdown_asterisks(self):
        """send_telegram_message는 parse_mode를 쓰지 않아 **가 별표 그대로 찍힌다."""
        out = sh.build_report(CFG, self._audit(), {}, random.Random(5), limit=2)
        self.assertNotIn("**", out)

    def test_limit_caps_prompts_and_says_so(self):
        out = sh.build_report(CFG, self._audit(), {}, random.Random(5), limit=2)
        self.assertIn("다시 실행해 주세요", out)


if __name__ == "__main__":
    unittest.main()
