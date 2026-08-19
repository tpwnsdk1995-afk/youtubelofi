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


class TestGroupStats(unittest.TestCase):
    def test_groups_by_field_with_fallback(self):
        deltas = {"a": 10, "b": 20, "c": 5}
        recent_by_id = {"a": {"genre": "calm"}, "b": {"genre": "calm"}}
        groups = wr.group_stats(deltas, recent_by_id, "genre", fallback=lambda vid: "groove")
        self.assertEqual(groups["calm"], {"count": 2, "total": 30, "avg": 15})
        self.assertEqual(groups["groove"]["count"], 1)

    def test_skips_entries_without_key(self):
        groups = wr.group_stats({"a": 10}, {}, "style")
        self.assertEqual(groups, {})


class TestCompareGroups(unittest.TestCase):
    def test_requires_minimum_sample(self):
        groups = {"calm": {"count": 2, "total": 100, "avg": 50}, "groove": {"count": 5, "total": 10, "avg": 2}}
        self.assertIsNone(wr.compare_groups(groups))

    def test_requires_meaningful_ratio(self):
        groups = {"calm": {"count": 5, "total": 55, "avg": 11}, "groove": {"count": 5, "total": 50, "avg": 10}}
        self.assertIsNone(wr.compare_groups(groups))

    def test_returns_best_and_worst(self):
        groups = {"calm": {"count": 5, "total": 150, "avg": 30}, "groove": {"count": 5, "total": 50, "avg": 10}}
        result = wr.compare_groups(groups)
        self.assertIsNotNone(result)
        (best_name, _), (worst_name, _) = result
        self.assertEqual(best_name, "calm")
        self.assertEqual(worst_name, "groove")


class TestCountFlatWeeks(unittest.TestCase):
    def test_counts_consecutive_low_weeks(self):
        history = [
            {"weekly_view_delta": 500},
            {"weekly_view_delta": 5},
            {"weekly_view_delta": 3},
        ]
        self.assertEqual(wr.count_flat_weeks(history), 2)

    def test_zero_when_latest_is_healthy(self):
        self.assertEqual(wr.count_flat_weeks([{"weekly_view_delta": 200}]), 0)

    def test_empty_history(self):
        self.assertEqual(wr.count_flat_weeks([]), 0)


class TestShouldAppendHistory(unittest.TestCase):
    def test_appends_when_empty(self):
        self.assertTrue(wr.should_append_history([], datetime(2026, 8, 24, 9, 0, tzinfo=KST)))

    def test_skips_same_week_rerun(self):
        now = datetime(2026, 8, 24, 9, 0, tzinfo=KST)
        history = [{"at": (now - timedelta(hours=2)).isoformat()}]
        self.assertFalse(wr.should_append_history(history, now))

    def test_appends_after_a_week(self):
        now = datetime(2026, 8, 24, 9, 0, tzinfo=KST)
        history = [{"at": (now - timedelta(days=7)).isoformat()}]
        self.assertTrue(wr.should_append_history(history, now))

    def test_appends_when_timestamp_malformed(self):
        now = datetime(2026, 8, 24, 9, 0, tzinfo=KST)
        self.assertTrue(wr.should_append_history([{"at": "not-a-date"}], now))


class TestRecommendations(unittest.TestCase):
    def test_first_report_says_do_not_change(self):
        recs = wr.build_recommendations(
            {"subscriberCount": 0}, None, 0, {}, {}, 7, None, [])
        joined = "\n".join(recs)
        self.assertIn("첫 리포트", joined)
        self.assertIn("바꾸지 마세요", joined)

    def test_low_sample_blocks_config_advice(self):
        recs = wr.build_recommendations(
            {"subscriberCount": 0}, {"viewCount": 5}, 6,
            {"calm": {"count": 5, "total": 4, "avg": 0.8}, "groove": {"count": 4, "total": 2, "avg": 0.5}},
            {}, 7, None, [])
        joined = "\n".join(recs)
        self.assertIn("바꾸지 마세요", joined)
        self.assertNotIn("genre_rotation에서", joined)

    def test_flags_missed_uploads(self):
        recs = wr.build_recommendations(
            {"subscriberCount": 0}, {"viewCount": 5}, 6, {}, {}, 4, None, [])
        self.assertIn("4/7편", "\n".join(recs))

    def test_flags_low_library(self):
        recs = wr.build_recommendations(
            {"subscriberCount": 0}, {"viewCount": 5}, 6, {}, {}, 7,
            {"calm": 58, "groove": 120}, [])
        joined = "\n".join(recs)
        self.assertIn("calm 58곡", joined)
        self.assertNotIn("groove 120곡", joined)

    def test_flat_weeks_message(self):
        history = [{"weekly_view_delta": 2}, {"weekly_view_delta": 3}, {"weekly_view_delta": 1}]
        recs = wr.build_recommendations(
            {"subscriberCount": 0}, {"viewCount": 5}, 6, {}, {}, 7, None, history)
        self.assertIn("3주 연속", "\n".join(recs))

    def test_enough_data_gives_rotation_advice(self):
        genre_groups = {"calm": {"count": 5, "total": 300, "avg": 60}, "groove": {"count": 4, "total": 40, "avg": 10}}
        recs = wr.build_recommendations(
            {"subscriberCount": 12}, {"viewCount": 100}, 340, genre_groups, {}, 7, None, [])
        joined = "\n".join(recs)
        self.assertIn("genre_rotation", joined)
        self.assertIn("calm", joined)

    def test_enough_data_but_no_difference_says_keep(self):
        genre_groups = {"calm": {"count": 5, "total": 100, "avg": 20}, "groove": {"count": 4, "total": 76, "avg": 19}}
        recs = wr.build_recommendations(
            {"subscriberCount": 12}, {"viewCount": 100}, 176, genre_groups, {}, 7, None, [])
        self.assertIn("genre_rotation 유지", "\n".join(recs))

    def test_style_advice_when_separated(self):
        style_groups = {"minhwa": {"count": 3, "total": 300, "avg": 100}, "painterly": {"count": 5, "total": 50, "avg": 10}}
        recs = wr.build_recommendations(
            {"subscriberCount": 12}, {"viewCount": 100}, 350, {}, style_groups, 7, None, [])
        joined = "\n".join(recs)
        self.assertIn("scenes_joseon.yml", joined)
        self.assertIn("minhwa", joined)


class TestSampleGateUsesChannelDelta(unittest.TestCase):
    def test_new_videos_count_toward_sample_size(self):
        """이번 주 신작은 지난 주 스냅샷에 없어 영상별 delta 합계에서 빠진다.
        표본 판정은 채널 전체 증가분(+40)을 써야 한다."""
        now = datetime(2026, 8, 24, 9, 0, tzinfo=KST)
        channel_stats = {"subscriberCount": 5, "viewCount": 40, "videoCount": 7}
        channel_prev = {"subscriberCount": 0, "viewCount": 0, "videoCount": 0}
        new_published = (now - timedelta(days=2)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        videos = {
            "n1": {
                "title": "신작", "description": "#공부플리 #조선로파이 #lofi\n본문",
                "publishedAt": new_published, "privacyStatus": "public",
                "viewCount": 40, "likeCount": 1, "commentCount": 0,
            },
        }
        report = wr.build_report(now, channel_stats, channel_prev, videos, {}, {},
                                 wr.build_genre_lookup(make_templates()), history=[])
        # +40이므로 "지금 바꾸지 마세요" 게이트에 걸리지 않아야 한다
        self.assertNotIn("바꾸지 마세요", report)


class TestReportIncludesRecommendations(unittest.TestCase):
    def test_report_has_action_section_and_library(self):
        now = datetime(2026, 8, 24, 9, 0, tzinfo=KST)
        channel_stats = {"subscriberCount": 0, "viewCount": 12, "videoCount": 7}
        channel_prev = {"subscriberCount": 0, "viewCount": 6, "videoCount": 7}
        old_published = (now - timedelta(days=20)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        videos = {
            "v1": {
                "title": "옛 영상", "description": "#공부플리 #조선로파이 #lofi\n본문",
                "publishedAt": old_published, "privacyStatus": "public",
                "viewCount": 8, "likeCount": 0, "commentCount": 0,
            },
        }
        report = wr.build_report(
            now, channel_stats, channel_prev, videos, {"v1": {"viewCount": 5}}, {},
            wr.build_genre_lookup(make_templates()),
            library_counts={"calm": 58, "groove": 60}, history=[],
        )
        self.assertIn("[음원 재고 (Drive)]", report)
        self.assertIn("보충 필요", report)
        self.assertIn("[이번 주 조치 제안]", report)
        self.assertIn("바꾸지 마세요", report)


if __name__ == "__main__":
    unittest.main()
