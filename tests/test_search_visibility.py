import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import search_visibility as sv

OURS = "UC_ours"
THEIRS = "UC_theirs"


def item(channel_id, title="제목", channel_title="채널", video_id="v"):
    return {"id": {"videoId": video_id},
            "snippet": {"channelId": channel_id, "title": title, "channelTitle": channel_title}}


class TestFindOurRank(unittest.TestCase):
    def test_rank_is_one_based(self):
        items = [item(OURS)]
        self.assertEqual(sv.find_our_rank(items, OURS), 1)

    def test_finds_us_further_down(self):
        items = [item(THEIRS), item(THEIRS), item(OURS)]
        self.assertEqual(sv.find_our_rank(items, OURS), 3)

    def test_absent_is_none_not_zero(self):
        """0을 돌려주면 '1위'와 '없음'이 거짓으로 구분되지 않는다."""
        self.assertIsNone(sv.find_our_rank([item(THEIRS)], OURS))
        self.assertIsNone(sv.find_our_rank([], OURS))

    def test_reports_the_first_occurrence(self):
        items = [item(THEIRS), item(OURS, video_id="a"), item(OURS, video_id="b")]
        self.assertEqual(sv.find_our_rank(items, OURS), 2)


class TestSummarize(unittest.TestCase):
    def test_says_found_with_rank_when_present(self):
        items = [item(THEIRS), item(OURS, title="조선 로파이 플리")]
        lines = "\n".join(sv.summarize("조선 로파이", items, OURS, {}))
        self.assertIn("2위에 우리 영상", lines)

    def test_says_absent_when_missing(self):
        lines = "\n".join(sv.summarize("조선 로파이", [item(THEIRS)], OURS, {}))
        self.assertIn("안에 우리 없음", lines)

    def test_marks_our_row_in_the_top_list(self):
        items = [item(OURS, channel_title="조선로파이")]
        lines = sv.summarize("q", items, OURS, {})
        self.assertTrue(any(line.strip().startswith("★") for line in lines[1:]))

    def test_missing_view_count_is_labelled_not_guessed(self):
        lines = "\n".join(sv.summarize("q", [item(THEIRS)], OURS, {}))
        self.assertIn("조회수 미상", lines)

    def test_empty_results_do_not_crash(self):
        lines = "\n".join(sv.summarize("q", [], OURS, {}))
        self.assertIn("우리 없음", lines)


class TestQueries(unittest.TestCase):
    def test_covers_both_the_niche_and_the_crowded_terms(self):
        """차별화 자리만 보면 '왜 조회수가 없나'의 절반만 답하게 된다."""
        joined = " ".join(sv.DEFAULT_QUERIES)
        self.assertIn("조선", joined)
        self.assertIn("국악", joined)
        self.assertIn("공부 플레이리스트", joined)

    def test_results_per_query_is_within_the_api_maximum(self):
        self.assertLessEqual(sv.RESULTS_PER_QUERY, 50)


if __name__ == "__main__":
    unittest.main()
