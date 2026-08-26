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
        self.assertIn("[이번 주 진단]", report)
        self.assertIn("바꾸지 마세요", report)
        self.assertIn("[다음 주 계획]", report)
        self.assertIn("한 줄 요약:", report)

    def test_traffic_sources_translate_unmapped_youtube_codes(self):
        out = "\n".join(wr.format_traffic_sources(
            {"YT_OTHER_PAGE": 3, "YT_CHANNEL": 2, "END_SCREEN": 1}))
        self.assertIn("유튜브 기타 페이지", out)
        self.assertIn("채널 페이지", out)
        self.assertIn("종료 화면", out)
        self.assertNotIn("YT_OTHER_PAGE", out)


class TestSuccessFactorAnalysis(unittest.TestCase):
    """순위표만으로는 "그래서 뭘 하지"에 답이 안 된다. 무엇이 갈랐는지를
    낼 때 표본이 작으면 '가설'로 못박아야 한다."""

    def make(self, multiplier=1):
        videos = {}
        views = [4, 4, 2, 1, 1, 1, 1, 1, 0]
        for i, v in enumerate(views):
            videos[f"v{i}"] = {
                "privacyStatus": "public",
                "viewCount": v * multiplier,
                "publishedAt": f"2026-08-{13 + i:02d}T10:14:00Z",
                "title": f"Playlist 상황 묘사 {i} 🍵 효익 문구 {i}",
            }
        recent = {
            "v0": {"genre": "groove", "scene_id": "feast"},
            "v1": {"genre": "groove", "scene_id": "feast"},
            "v7": {"genre": "calm", "scene_id": "desk"},
            "v8": {"genre": "calm", "scene_id": "desk"},
        }
        return videos, recent

    def test_small_sample_is_labelled_hypothesis_not_conclusion(self):
        videos, recent = self.make()
        out = "\n".join(wr.success_factor_analysis(videos, recent))
        self.assertIn("가설 단계", out)
        # 표본이 작을 때 "비중을 늘려라"라고 말하면 안 된다
        self.assertNotIn("쪽으로 늘린다", out)
        self.assertIn("진짜인지", out)

    def test_large_sample_gives_concrete_action(self):
        videos, recent = self.make(multiplier=20)
        out = "\n".join(wr.success_factor_analysis(videos, recent))
        self.assertNotIn("가설 단계", out)
        self.assertIn("쪽으로 늘린다", out)

    def test_reports_winner_hook_and_losing_group(self):
        videos, recent = self.make()
        out = "\n".join(wr.success_factor_analysis(videos, recent))
        self.assertIn("▶ 1위", out)
        self.assertIn("제목 훅", out)
        self.assertIn("무엇이 갈랐나", out)
        self.assertIn("공통점", out)
        self.assertIn("그래서 다음에 이렇게 합니다", out)

    def test_skipped_when_too_few_videos(self):
        videos = {"a": {"privacyStatus": "public", "viewCount": 1, "title": "가"}}
        self.assertEqual(wr.success_factor_analysis(videos, {}), [])

    def test_single_video_values_are_not_compared(self):
        """1편 vs 1편으로 "4배 앞선다"를 말하면 안 된다."""
        videos = {
            f"v{i}": {"privacyStatus": "public", "viewCount": 10 - i,
                      "title": f"Playlist 훅{i} 🍵 효익{i}"}
            for i in range(5)
        }
        recent = {f"v{i}": {"genre": f"unique{i}"} for i in range(5)}
        out = "\n".join(wr.success_factor_analysis(videos, recent))
        self.assertNotIn("무드:", out)

    def test_title_split_strips_variation_selector(self):
        hook, benefit = wr.split_title_hook("Playlist 궁궐 연회 ✍️ 어깨 들썩이는 국악")
        self.assertEqual(hook, "궁궐 연회")
        self.assertEqual(benefit, "어깨 들썩이는 국악")

    def test_title_split_without_emoji(self):
        hook, benefit = wr.split_title_hook("Playlist (가사X) 그냥 제목")
        self.assertEqual(hook, "그냥 제목")
        self.assertEqual(benefit, "")

    def test_particle_follows_final_consonant(self):
        self.assertEqual(wr.with_particle("화요일"), "화요일이")
        self.assertEqual(wr.with_particle("무드"), "무드가")
        self.assertEqual(wr.with_particle("무드", ("을", "를")), "무드를")
        self.assertEqual(wr.with_particle("씬", ("을", "를")), "씬을")

    def test_weekday_action_does_not_suggest_changing_share(self):
        videos, recent = self.make(multiplier=20)
        out = "\n".join(wr.success_factor_analysis(videos, recent))
        if "게시 요일" in out and "쪽으로 늘린다" in out:
            self.assertNotIn("게시 요일을 ", out.split("그래서 다음에")[-1])


if __name__ == "__main__":
    unittest.main()


def make_analytics(impressions=None, ctr=None, avg_seconds=600, views=100, subs=0, watched=1000):
    summary = {
        "views": views,
        "estimatedMinutesWatched": watched,
        "averageViewDuration": avg_seconds,
        "subscribersGained": subs,
    }
    if impressions is not None:
        summary["videoThumbnailImpressions"] = impressions
    if ctr is not None:
        summary["videoThumbnailImpressionsClickRate"] = ctr
    return {"period": ("2026-08-17", "2026-08-23"), "summary": summary, "traffic_sources": {}}


class TestDiagnoseFunnel(unittest.TestCase):
    def test_no_analytics_returns_empty(self):
        self.assertEqual(wr.diagnose_funnel(None), [])

    def test_low_impressions_blames_discovery_not_content(self):
        lines = wr.diagnose_funnel(make_analytics(impressions=120, ctr=5.0))
        joined = "\n".join(lines)
        self.assertIn("병목: 노출 단계", joined)
        self.assertIn("썸네일이나 음악의 문제가 아니므로", joined.replace("\n", ""))

    def test_high_impressions_low_ctr_blames_thumbnail(self):
        lines = wr.diagnose_funnel(make_analytics(impressions=50000, ctr=1.1))
        joined = "\n".join(lines)
        self.assertIn("병목: 썸네일", joined)
        self.assertIn("title_templates_joseon.yml", joined)

    def test_good_ctr_short_watch_blames_music(self):
        lines = wr.diagnose_funnel(make_analytics(impressions=50000, ctr=6.0, avg_seconds=90))
        joined = "\n".join(lines)
        self.assertIn("병목: 음악/도입부", joined)
        self.assertIn("Suno", joined)

    def test_healthy_funnel_says_scale_up(self):
        lines = wr.diagnose_funnel(make_analytics(impressions=50000, ctr=6.0, avg_seconds=900, subs=20))
        joined = "\n".join(lines)
        self.assertIn("퍼널 정상", joined)

    def test_healthy_funnel_but_no_subs_flags_branding(self):
        lines = wr.diagnose_funnel(make_analytics(impressions=50000, ctr=6.0, avg_seconds=900, subs=0))
        self.assertIn("구독 전환이 0", "\n".join(lines))

    def test_impression_trend_vs_last_week(self):
        cur = make_analytics(impressions=2000, ctr=5.0, avg_seconds=900)
        prev = {"videoThumbnailImpressions": 1000}
        lines = wr.diagnose_funnel(cur, prev)
        self.assertIn("노출수 추세: 지난주 대비 100% 증가", "\n".join(lines))

    def test_small_sample_refuses_to_blame_music(self):
        """2026-08-26 오진 재발 방지: 조회수 7회 · 평균 14초를 근거로
        '음악이 문제'라고 단정했던 버그. 같은 채널 월 평균은 14분 24초였다."""
        lines = wr.diagnose_funnel(make_analytics(impressions=None, avg_seconds=14, views=7))
        joined = "\n".join(lines)
        self.assertIn("표본이", joined)
        self.assertIn("판단하지 않습니다", joined)
        self.assertNotIn("병목: 음악", joined)

    def test_enough_sample_does_blame_music(self):
        lines = wr.diagnose_funnel(make_analytics(impressions=None, avg_seconds=20, views=200))
        self.assertIn("병목: 음악/도입부", "\n".join(lines))

    def test_enough_sample_good_retention_says_needs_reach(self):
        lines = wr.diagnose_funnel(make_analytics(impressions=None, avg_seconds=864, views=200, subs=0))
        joined = "\n".join(lines)
        self.assertIn("시청 지속 양호", joined)
        self.assertIn("14분 24초", joined)
        self.assertIn("구독 전환이 0", joined)

    def test_zero_views_says_no_data(self):
        lines = wr.diagnose_funnel(make_analytics(impressions=None, avg_seconds=0, views=0))
        self.assertIn("조회수가 0", "\n".join(lines))


class TestFormatDuration(unittest.TestCase):
    def test_under_a_minute_shows_seconds(self):
        self.assertEqual(wr.fmt_duration(14), "14초")

    def test_minutes_and_seconds(self):
        self.assertEqual(wr.fmt_duration(867), "14분 27초")

    def test_handles_none(self):
        self.assertEqual(wr.fmt_duration(None), "0초")


class TestTrafficSources(unittest.TestCase):
    def test_formats_with_percentages(self):
        lines = wr.format_traffic_sources({"YT_SEARCH": 60, "RELATED_VIDEO": 40})
        self.assertIn("- 유튜브 검색: 60회 (60%)", lines)
        self.assertIn("- 추천 영상: 40회 (40%)", lines)

    def test_unknown_key_passes_through(self):
        lines = wr.format_traffic_sources({"SOME_NEW_SOURCE": 5})
        self.assertIn("SOME_NEW_SOURCE", "\n".join(lines))


class TestReportWithAnalytics(unittest.TestCase):
    def test_includes_funnel_and_traffic(self):
        now = datetime(2026, 8, 24, 9, 0, tzinfo=KST)
        channel_stats = {"subscriberCount": 0, "viewCount": 200, "videoCount": 7}
        channel_prev = {"subscriberCount": 0, "viewCount": 100, "videoCount": 7}
        analytics = make_analytics(impressions=50000, ctr=1.2, avg_seconds=600)
        analytics["traffic_sources"] = {"YT_SEARCH": 80, "BROWSE": 20}
        report = wr.build_report(now, channel_stats, channel_prev, {}, {}, {},
                                 wr.build_genre_lookup(make_templates()),
                                 history=[], analytics=analytics)
        self.assertIn("[유입 지표", report)
        self.assertIn("노출 클릭률(CTR): 1.20%", report)
        self.assertIn("[유입 경로]", report)
        self.assertIn("[원인 진단]", report)
        self.assertIn("병목: 썸네일", report)

    def test_reports_analytics_disabled_reason(self):
        now = datetime(2026, 8, 24, 9, 0, tzinfo=KST)
        channel_stats = {"subscriberCount": 0, "viewCount": 6, "videoCount": 7}
        report = wr.build_report(now, channel_stats, {"viewCount": 6}, {}, {}, {},
                                 wr.build_genre_lookup(make_templates()),
                                 history=[], analytics=None,
                                 analytics_error="Analytics 스코프 미승인 - 재인증 필요")
        self.assertIn("노출수/클릭률 분석이 꺼져 있습니다", report)
        self.assertIn("재인증", report)


class TestTelegramSplit(unittest.TestCase):
    def test_short_message_stays_one_chunk(self):
        import send_telegram_message as stm
        self.assertEqual(stm.split_message("짧은 메시지"), ["짧은 메시지"])

    def test_splits_on_line_boundaries(self):
        import send_telegram_message as stm
        text = "\n".join(f"줄 {i} " + "가" * 100 for i in range(100))
        chunks = stm.split_message(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), stm.CHUNK_SIZE)
        # 줄이 중간에 잘리지 않고 재조립되어야 한다
        self.assertEqual("\n".join(chunks), text)

    def test_single_overlong_line_is_hard_split(self):
        import send_telegram_message as stm
        text = "가" * (stm.CHUNK_SIZE * 2 + 50)
        chunks = stm.split_message(text)
        self.assertEqual("".join(chunks), text)
        for c in chunks:
            self.assertLessEqual(len(c), stm.CHUNK_SIZE)


class TestWeeklyEnrichment(unittest.TestCase):
    def test_headline_includes_key_numbers(self):
        headline = wr.build_headline({"subscriberCount": 5}, 2, 30, 7, None)
        self.assertIn("조회수 +30회", headline)
        self.assertIn("구독자 +2명", headline)
        self.assertIn("업로드 7/7편", headline)

    def test_trend_needs_two_points(self):
        self.assertEqual(wr.week_trend_lines([{"weekly_view_delta": 5, "at": "2026-08-24"}]), [])

    def test_trend_detects_rising(self):
        history = [
            {"at": "2026-08-10T09:00:00+09:00", "weekly_view_delta": 5, "subscriberCount": 0},
            {"at": "2026-08-17T09:00:00+09:00", "weekly_view_delta": 40, "subscriberCount": 3},
        ]
        out = "\n".join(wr.week_trend_lines(history))
        self.assertIn("증가 추세", out)

    def test_plan_separates_manual_and_auto(self):
        out = "\n".join(wr.build_next_week_plan(5, {"calm": 58}, None, "스코프 미승인", 3))
        self.assertIn("사장님이 하실 일", out)
        self.assertIn("자동으로 처리됨", out)
        self.assertIn("calm 음원 42곡", out)
        self.assertIn("하지 말아야 할 일", out)

    def test_plan_says_nothing_to_do_when_healthy(self):
        out = "\n".join(wr.build_next_week_plan(7, {"calm": 150, "groove": 150}, {"summary": {}}, None, 500))
        self.assertIn("손댈 것이 없습니다", out)
