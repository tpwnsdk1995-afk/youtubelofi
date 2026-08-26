"""월간 리포트 테스트: 구간 계산, 이번 달 영상 필터, 축별 집계, 조치 제안 게이트."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import monthly_report as mr
import weekly_report as wr

KST = timezone(timedelta(hours=9))


def make_templates():
    return {
        "genres": {
            "calm": {"top_hashtags": ["#공부플리", "#조선로파이", "#lofi"]},
            "groove": {"top_hashtags": ["#조선로파이", "#플레이리스트", "#lofi"]},
        }
    }


def make_video(title, published, views, likes=0, desc="#공부플리 #조선로파이 #lofi\n본문", privacy="public"):
    return {
        "title": title,
        "description": desc,
        "publishedAt": published.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "privacyStatus": privacy,
        "viewCount": views,
        "likeCount": likes,
        "commentCount": 0,
    }


class TestPreviousMonthRange(unittest.TestCase):
    def test_normal_month(self):
        now = datetime(2026, 9, 1, 9, 0, tzinfo=KST)
        start, end = mr.previous_month_range(now)
        self.assertEqual((start.year, start.month, start.day), (2026, 8, 1))
        self.assertEqual((end.year, end.month, end.day), (2026, 9, 1))

    def test_january_wraps_to_december(self):
        now = datetime(2027, 1, 1, 9, 0, tzinfo=KST)
        start, end = mr.previous_month_range(now)
        self.assertEqual((start.year, start.month), (2026, 12))
        self.assertEqual((end.year, end.month), (2027, 1))


class TestVideosInRange(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 1, 0, 0, tzinfo=KST)
        self.end = datetime(2026, 9, 1, 0, 0, tzinfo=KST)

    def test_includes_only_public_videos_inside_range(self):
        videos = {
            "in": make_video("이번 달", datetime(2026, 8, 15, 12, 0, tzinfo=KST), 100),
            "before": make_video("지난 달", datetime(2026, 7, 20, 12, 0, tzinfo=KST), 999),
            "after": make_video("다음 달", datetime(2026, 9, 2, 12, 0, tzinfo=KST), 999),
            "private": make_video("비공개", datetime(2026, 8, 10, 12, 0, tzinfo=KST), 500, privacy="private"),
        }
        selected = mr.videos_in_range(videos, self.start, self.end)
        self.assertEqual(set(selected), {"in"})

    def test_boundary_inclusive_at_start_exclusive_at_end(self):
        videos = {
            "first_moment": make_video("1일 0시", self.start, 1),
            "last_moment": make_video("말일 23:59", self.end - timedelta(minutes=1), 1),
            "next_month": make_video("다음달 0시", self.end, 1),
        }
        selected = mr.videos_in_range(videos, self.start, self.end)
        self.assertEqual(set(selected), {"first_moment", "last_moment"})


class TestGroupByField(unittest.TestCase):
    def test_uses_cumulative_views_not_deltas(self):
        month_videos = {
            "a": {"viewCount": 100}, "b": {"viewCount": 50}, "c": {"viewCount": 30},
        }
        recent = {"a": {"genre": "calm"}, "b": {"genre": "calm"}, "c": {"genre": "groove"}}
        groups = mr.group_by_field(month_videos, recent, "genre")
        self.assertEqual(groups["calm"], {"count": 2, "total": 150, "avg": 75})
        self.assertEqual(groups["groove"]["avg"], 30)

    def test_fallback_classifies_untracked_videos(self):
        groups = mr.group_by_field({"x": {"viewCount": 10}}, {}, "genre", fallback=lambda v: "calm")
        self.assertEqual(groups["calm"]["count"], 1)


class TestCompare(unittest.TestCase):
    def test_requires_sample_size(self):
        groups = {"calm": {"count": 2, "avg": 100}, "groove": {"count": 9, "avg": 5}}
        self.assertIsNone(mr.compare(groups, mr.MIN_GROUP_VIDEOS))

    def test_requires_meaningful_gap(self):
        groups = {"calm": {"count": 9, "avg": 11}, "groove": {"count": 9, "avg": 10}}
        self.assertIsNone(mr.compare(groups, mr.MIN_GROUP_VIDEOS))

    def test_returns_best_worst(self):
        groups = {"calm": {"count": 9, "avg": 60}, "groove": {"count": 9, "avg": 10}}
        (best, _), (worst, _) = mr.compare(groups, mr.MIN_GROUP_VIDEOS)
        self.assertEqual((best, worst), ("calm", "groove"))


class TestMonthlyRecommendations(unittest.TestCase):
    def test_low_views_blocks_config_advice(self):
        recs = mr.build_monthly_recommendations(
            month_views=40, upload_count=31, expected_uploads=31,
            genre_groups={"calm": {"count": 20, "avg": 2}, "groove": {"count": 11, "avg": 1}},
            style_groups={}, situation_groups={},
            channel_stats={"subscriberCount": 0}, channel_prev=None, library_counts=None)
        joined = "\n".join(recs)
        self.assertIn("바꾸지 마세요", joined)
        self.assertNotIn("genre_rotation에서", joined)

    def test_flags_missed_uploads_with_rate(self):
        recs = mr.build_monthly_recommendations(
            month_views=10, upload_count=27, expected_uploads=31,
            genre_groups={}, style_groups={}, situation_groups={},
            channel_stats={"subscriberCount": 0}, channel_prev=None, library_counts=None)
        joined = "\n".join(recs)
        self.assertIn("27/31편", joined)
        self.assertIn("4일 누락", joined)

    def test_praises_full_month(self):
        recs = mr.build_monthly_recommendations(
            month_views=10, upload_count=31, expected_uploads=31,
            genre_groups={}, style_groups={}, situation_groups={},
            channel_stats={"subscriberCount": 0}, channel_prev=None, library_counts=None)
        self.assertIn("끊기지 않았습니다", "\n".join(recs))

    def test_enough_views_gives_rotation_and_scene_advice(self):
        recs = mr.build_monthly_recommendations(
            month_views=5000, upload_count=31, expected_uploads=31,
            genre_groups={"calm": {"count": 20, "avg": 200}, "groove": {"count": 11, "avg": 40}},
            style_groups={"minhwa": {"count": 10, "avg": 300}, "painterly": {"count": 21, "avg": 60}},
            situation_groups={"gwageo_d1": {"count": 4, "avg": 400},
                              "night_stroll": {"count": 4, "avg": 30}},
            channel_stats={"subscriberCount": 50}, channel_prev={"subscriberCount": 10},
            library_counts=None)
        joined = "\n".join(recs)
        self.assertIn("genre_rotation", joined)
        self.assertIn("scenes_joseon.yml", joined)
        self.assertIn("gwageo_d1", joined)

    def test_flags_zero_subscriber_conversion(self):
        recs = mr.build_monthly_recommendations(
            month_views=5000, upload_count=31, expected_uploads=31,
            genre_groups={}, style_groups={}, situation_groups={},
            channel_stats={"subscriberCount": 10}, channel_prev={"subscriberCount": 10},
            library_counts=None)
        self.assertIn("구독자 증가가 0", "\n".join(recs))


class TestBuildMonthlyReport(unittest.TestCase):
    def test_full_report_sections(self):
        now = datetime(2026, 9, 1, 9, 0, tzinfo=KST)
        channel_stats = {"subscriberCount": 40, "viewCount": 3000, "videoCount": 40}
        channel_prev = {"subscriberCount": 12, "viewCount": 900, "videoCount": 9}
        videos = {}
        recent = {}
        for i in range(10):
            vid = f"v{i}"
            videos[vid] = make_video(f"8월 영상 {i}", datetime(2026, 8, i + 1, 18, 0, tzinfo=KST), 100 - i * 5, likes=2)
            recent[vid] = {"genre": "calm" if i % 3 else "groove", "style": "minhwa", "situation_id": "gwageo_d1"}
        report = mr.build_monthly_report(
            now, channel_stats, channel_prev, videos, recent,
            wr.build_genre_lookup(make_templates()), library_counts={"calm": 58, "groove": 60})
        self.assertIn("2026년 8월 월간 리포트", report)
        self.assertIn("구독자: 40명 (+28)", report)
        self.assertIn("업로드: 10/31편", report)
        self.assertIn("[무드별 성과", report)
        self.assertIn("[이번 달 TOP 3]", report)
        self.assertIn("[음원 재고 (Drive)]", report)
        self.assertIn("[다음 달 조치]", report)

    def test_empty_month_does_not_crash(self):
        now = datetime(2026, 9, 1, 9, 0, tzinfo=KST)
        report = mr.build_monthly_report(
            now, {"subscriberCount": 0, "viewCount": 0, "videoCount": 0}, None, {}, {},
            wr.build_genre_lookup(make_templates()))
        self.assertIn("업로드: 0/31편", report)


if __name__ == "__main__":
    unittest.main()
