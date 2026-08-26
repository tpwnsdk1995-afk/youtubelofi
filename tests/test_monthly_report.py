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
        self.assertIn("한 줄 요약:", report)
        self.assertIn("[주차별 추이]", report)
        self.assertIn("[무드별 순위]", report)
        self.assertIn("[이번 달 TOP 3]", report)
        self.assertIn("[수익화 진척", report)
        self.assertIn("구독자: 40 / 1,000명", report)
        self.assertIn("[음원 재고 (Drive)]", report)
        self.assertIn("[다음 달 실행 계획]", report)

    def test_empty_month_does_not_crash(self):
        now = datetime(2026, 9, 1, 9, 0, tzinfo=KST)
        report = mr.build_monthly_report(
            now, {"subscriberCount": 0, "viewCount": 0, "videoCount": 0}, None, {}, {},
            wr.build_genre_lookup(make_templates()))
        self.assertIn("업로드: 0/31편", report)


class TestMonthlyOnlySections(unittest.TestCase):
    """월간에만 있는 해상도 - 주간 리포트로는 낼 수 없는 것들."""

    def test_weekly_breakdown_splits_month_into_weeks(self):
        month_videos = {
            "a": {"published": datetime(2026, 8, 2, tzinfo=KST), "viewCount": 100},
            "b": {"published": datetime(2026, 8, 9, tzinfo=KST), "viewCount": 200},
            "c": {"published": datetime(2026, 8, 20, tzinfo=KST), "viewCount": 300},
        }
        out = "\n".join(mr.weekly_breakdown(month_videos, None, None))
        self.assertIn("1주차", out)
        self.assertIn("2주차", out)
        self.assertIn("3주차", out)

    def test_weekly_breakdown_detects_improvement(self):
        month_videos = {
            "a": {"published": datetime(2026, 8, 1, tzinfo=KST), "viewCount": 10},
            "b": {"published": datetime(2026, 8, 25, tzinfo=KST), "viewCount": 500},
        }
        out = "\n".join(mr.weekly_breakdown(month_videos, None, None))
        self.assertIn("좋아졌습니다", out)

    def test_ranking_lists_every_qualifying_group(self):
        groups = {
            "a": {"count": 5, "total": 500, "avg": 100},
            "b": {"count": 5, "total": 250, "avg": 50},
            "c": {"count": 5, "total": 50, "avg": 10},
        }
        out = mr.ranking_lines(groups, "테스트 순위", 5)
        self.assertIn("1. a", out[1])
        self.assertIn("2. b", out[2])
        self.assertIn("3. c", out[3])

    def test_ranking_skips_undersampled_groups(self):
        groups = {"a": {"count": 5, "total": 500, "avg": 100}, "b": {"count": 1, "total": 1, "avg": 1}}
        self.assertEqual(mr.ranking_lines(groups, "제목", 5), [])

    def test_monetization_shows_remaining_and_eta(self):
        out = "\n".join(mr.monetization_progress(
            {"subscriberCount": 250, "viewCount": 9000}, None, {"subscriberCount": 200}))
        self.assertIn("250 / 1,000명", out)
        self.assertIn("750명 남음", out)
        self.assertIn("15개월 후 도달", out)

    def test_monetization_no_eta_when_flat(self):
        out = "\n".join(mr.monetization_progress(
            {"subscriberCount": 5}, None, {"subscriberCount": 5}))
        self.assertIn("추정할 수 없습니다", out)

    def test_monetization_reports_watch_hours_when_analytics_on(self):
        analytics = {"summary": {"estimatedMinutesWatched": 6000}}
        out = "\n".join(mr.monetization_progress({"subscriberCount": 10}, analytics, None))
        self.assertIn("100.0시간", out)

    def test_monetization_flags_unrealistic_eta(self):
        out = "\n".join(mr.monetization_progress(
            {"subscriberCount": 11, "viewCount": 20}, None, {"subscriberCount": 10}))
        self.assertIn("사실상 도달 불가", out)

    def test_channel_wide_ranking_renders_without_month_uploads(self):
        videos = {
            f"v{i}": {"privacyStatus": "public", "title": f"영상{i}", "viewCount": i * 10}
            for i in range(1, 11)
        }
        out = "\n".join(mr.channel_wide_ranking(videos, {}))
        self.assertIn("채널 전체 성과 (공개 10편 누적)", out)
        self.assertIn("상위 5편", out)
        self.assertIn("하위 3편", out)
        self.assertIn("100회", out)

    def test_channel_wide_ranking_skips_private(self):
        videos = {
            "a": {"privacyStatus": "public", "title": "공개", "viewCount": 10},
            "b": {"privacyStatus": "private", "title": "비공개", "viewCount": 9999},
            "c": {"privacyStatus": "public", "title": "공개2", "viewCount": 5},
        }
        out = "\n".join(mr.channel_wide_ranking(videos, {}))
        self.assertIn("공개 2편 누적", out)
        self.assertNotIn("비공개", out)

    def test_channel_wide_ranking_needs_two_videos(self):
        videos = {"a": {"privacyStatus": "public", "title": "하나", "viewCount": 10}}
        self.assertEqual(mr.channel_wide_ranking(videos, {}), [])

    def test_channel_wide_ranking_flags_outlier_top(self):
        videos = {
            "a": {"privacyStatus": "public", "title": "대박", "viewCount": 1000},
            "b": {"privacyStatus": "public", "title": "보통", "viewCount": 10},
            "c": {"privacyStatus": "public", "title": "보통2", "viewCount": 10},
        }
        out = "\n".join(mr.channel_wide_ranking(videos, {}))
        self.assertIn("기준선으로 삼으세요", out)

    def test_month_over_month_growth(self):
        out = "\n".join(mr.month_over_month(150, 30, {"last_month": {"month_views": 100, "month_uploads": 20}}))
        self.assertIn("전월 대비", out)
        self.assertIn("▲ 50%", out)
        self.assertIn("20편 → 30편", out)

    def test_month_over_month_decline(self):
        out = "\n".join(mr.month_over_month(50, 10, {"last_month": {"month_views": 100, "month_uploads": 20}}))
        self.assertIn("▼ 50%", out)

    def test_month_over_month_empty_without_prior_snapshot(self):
        self.assertEqual(mr.month_over_month(50, 10, {}), [])
        self.assertEqual(mr.month_over_month(50, 10, None), [])

    def test_monthly_traffic_lines_include_watch_hours(self):
        analytics = {
            "summary": {"views": 120, "estimatedMinutesWatched": 900, "averageViewDuration": 450},
            "period": ("2026-08-01", "2026-08-31"),
            "traffic_sources": {"YT_SEARCH": 80, "EXT_URL": 40},
        }
        out = "\n".join(mr.monthly_traffic_lines(analytics))
        self.assertIn("2026-08-01 ~ 2026-08-31", out)
        self.assertIn("15.0시간", out)
        self.assertIn("유입 경로 (한 달 누적)", out)
        self.assertIn("검색 의존도가 높습니다", out)

    def test_monthly_traffic_lines_empty_without_analytics(self):
        self.assertEqual(mr.monthly_traffic_lines(None), [])

    def test_next_month_plan_has_three_sections_and_targets(self):
        out = "\n".join(mr.build_next_month_plan(
            20, 31, {"calm": 58}, None, "스코프 미승인", 10, {"subscriberCount": 5}))
        self.assertIn("사장님이 하실 일", out)
        self.assertIn("자동으로 처리됨", out)
        self.assertIn("하지 말아야 할 일", out)
        self.assertIn("다음 달 이 리포트가 볼 숫자", out)
        self.assertIn("Suno", out)
        self.assertIn("재인증", out)
        self.assertIn("11일분", out)
        self.assertIn("구독자 15명 이상", out)

    def test_next_month_plan_no_manual_work_when_healthy(self):
        out = "\n".join(mr.build_next_month_plan(
            31, 31, {"calm": 120}, {"summary": {}}, None, 5000, {"subscriberCount": 900}))
        self.assertIn("없음 — 이번 달은 손댈 것이 없습니다.", out)
        self.assertNotIn("하지 말아야 할 일", out)
        self.assertIn("7,500회 이상", out)

    def test_channel_wide_ranking_uses_genre_fallback(self):
        videos = {
            "a": {"privacyStatus": "public", "title": "가", "viewCount": 10},
            "b": {"privacyStatus": "public", "title": "나", "viewCount": 5},
        }
        out = "\n".join(mr.channel_wide_ranking(videos, {}, genre_fn=lambda vid: "calm"))
        self.assertIn("(calm)", out)
        self.assertNotIn("(?)", out)

    def test_channel_wide_ranking_says_unknown_when_no_fallback(self):
        videos = {
            "a": {"privacyStatus": "public", "title": "가", "viewCount": 10},
            "b": {"privacyStatus": "public", "title": "나", "viewCount": 5},
        }
        out = "\n".join(mr.channel_wide_ranking(videos, {}))
        self.assertIn("무드 미상", out)

    def test_ranking_does_not_command_when_sample_is_tiny(self):
        """랭킹은 '기준선으로 삼으라'고 하는데 요인 분석은 '가설'이라고 하면 모순이다."""
        videos = {
            "a": {"privacyStatus": "public", "title": "대박", "viewCount": 8},
            "b": {"privacyStatus": "public", "title": "보통", "viewCount": 1},
            "c": {"privacyStatus": "public", "title": "보통2", "viewCount": 1},
        }
        out = "\n".join(mr.channel_wide_ranking(videos, {}))
        self.assertIn("우연과 구분되지 않습니다", out)
        self.assertNotIn("기준선으로 삼으세요", out)

    def test_ranking_commands_once_sample_is_enough(self):
        videos = {
            "a": {"privacyStatus": "public", "title": "대박", "viewCount": 80},
            "b": {"privacyStatus": "public", "title": "보통", "viewCount": 10},
            "c": {"privacyStatus": "public", "title": "보통2", "viewCount": 10},
        }
        out = "\n".join(mr.channel_wide_ranking(videos, {}))
        self.assertIn("기준선으로 삼으세요", out)


if __name__ == "__main__":
    unittest.main()
