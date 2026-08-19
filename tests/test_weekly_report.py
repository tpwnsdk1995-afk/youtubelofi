"""주간 리포트 로직 테스트: 무드 추정, 증가분 계산, 리포트 섹션 구성."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import weekly_report as wr

KST = timezone(timedelta(hours=9))


def make_templates():
    return {
        "genres": {
            "calm": {"top_hashtags": ["#공부플리", "#조선로파이", "#lofi"]},
            "groove": {"top_hashtags": ["#조선로파이", "#플레이리스트", "#lofi"]},
        }
    }


class TestGenreInference(unittest.TestCase):
    def test_prefers_state_record(self):
        recent_by_id = {"v1": {"video_id": "v1", "genre": "calm"}}
        lookup = wr.build_genre_lookup(make_templates())
        self.assertEqual(wr.infer_genre("v1", recent_by_id, {}, lookup), "calm")

    def test_falls_back_to_description(self):
        lookup = wr.build_genre_lookup(make_templates())
        desc_by_id = {"v2": "#조선로파이 #플레이리스트 #lofi\n\n블러브 본문..."}
        self.assertEqual(wr.infer_genre("v2", {}, desc_by_id, lookup), "groove")

    def test_unknown_when_no_match(self):
        lookup = wr.build_genre_lookup(make_templates())
        self.assertIsNone(wr.infer_genre("v3", {}, {"v3": "아무 관련 없는 설명"}, lookup))


class TestFormatting(unittest.TestCase):
    def test_fmt_delta_positive_and_negative(self):
        self.assertEqual(wr.fmt_delta(120), "+120")
        self.assertEqual(wr.fmt_delta(-5), "-5")
        self.assertEqual(wr.fmt_delta(None), "(비교 데이터 없음)")

    def test_fmt_num(self):
        self.assertEqual(wr.fmt_num(1234), "1,234")
        self.assertEqual(wr.fmt_num(None), "?")


class TestBuildReport(unittest.TestCase):
    def test_first_run_no_snapshot(self):
        now = datetime(2026, 8, 24, 9, 0, tzinfo=KST)  # Monday
        channel_stats = {"subscriberCount": 100, "viewCount": 5000, "videoCount": 10}
        videos = {
            "v1": {
                "title": "이번 주 신작", "description": "#공부플리 #조선로파이 #lofi\n본문",
                "publishedAt": (now - timedelta(days=1)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "privacyStatus": "public", "viewCount": 300, "likeCount": 10, "commentCount": 1,
            },
        }
        report = wr.build_report(now, channel_stats, None, videos, {}, {}, wr.build_genre_lookup(make_templates()))
        self.assertIn("이번 주 신작 (1개)", report)
        self.assertIn("youtu.be/v1", report)
        self.assertIn("다음 리포트부터 제공됩니다", report)

    def test_with_prior_snapshot_computes_deltas(self):
        now = datetime(2026, 8, 24, 9, 0, tzinfo=KST)
        channel_stats = {"subscriberCount": 110, "viewCount": 5500, "videoCount": 10}
        channel_prev = {"subscriberCount": 100, "viewCount": 5000, "videoCount": 10}
        old_published = (now - timedelta(days=20)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        videos = {
            "v_calm": {
                "title": "잘 되는 calm 영상", "description": "#공부플리 #조선로파이 #lofi\n본문",
                "publishedAt": old_published, "privacyStatus": "public",
                "viewCount": 1000, "likeCount": 50, "commentCount": 2,
            },
            "v_groove": {
                "title": "덜 되는 groove 영상", "description": "#조선로파이 #플레이리스트 #lofi\n본문",
                "publishedAt": old_published, "privacyStatus": "public",
                "viewCount": 600, "likeCount": 20, "commentCount": 1,
            },
        }
        video_prev = {
            "v_calm": {"viewCount": 800, "likeCount": 40},
            "v_groove": {"viewCount": 580, "likeCount": 19},
        }
        report = wr.build_report(now, channel_stats, channel_prev, videos, video_prev, {}, wr.build_genre_lookup(make_templates()))
        self.assertIn("구독자: 110명 (+10)", report)
        self.assertIn("총 조회수: 5,500회 (+500)", report)
        self.assertIn("calm: 영상 1개, 합계 +200회", report)
        self.assertIn("groove: 영상 1개, 합계 +20회", report)
        self.assertIn("조회수 증가 TOP 3", report)
        self.assertIn("반응 저조", report)


if __name__ == "__main__":
    unittest.main()
