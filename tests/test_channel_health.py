import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import channel_health as ch


class TestParseDuration(unittest.TestCase):
    def test_parses_hours_minutes_seconds(self):
        self.assertEqual(ch.parse_duration("PT1H23M45S"), 5025)

    def test_parses_partial_forms(self):
        self.assertEqual(ch.parse_duration("PT45S"), 45)
        self.assertEqual(ch.parse_duration("PT2M"), 120)
        self.assertEqual(ch.parse_duration("PT2H"), 7200)

    def test_unreadable_duration_is_zero_not_a_guess(self):
        """길이를 못 읽으면 0이어야 한다.

        추측값을 넣으면 '시청시간 잠재력'이 실제보다 커 보여 수익화까지 남은
        거리를 과소평가하게 된다. 모르면 0으로 두고 리포트가 따로 지목한다.
        """
        for bad in ("", None, "1H2M", "garbage"):
            self.assertEqual(ch.parse_duration(bad), 0)


class TestDiagnose(unittest.TestCase):
    def _channel(self, **overrides):
        base = {
            "snippet": {"title": "조선로파이", "customUrl": "@joseonlofi", "country": "KR"},
            "brandingSettings": {
                "channel": {"description": "가" * 200, "keywords": "조선 로파이"},
                "image": {"bannerExternalUrl": "https://example.invalid/banner"},
            },
        }
        base["snippet"].update(overrides.pop("snippet", {}))
        base["brandingSettings"]["channel"].update(overrides.pop("channel", {}))
        base["brandingSettings"]["image"].update(overrides.pop("image", {}))
        return base

    def _video(self, vid="v1", privacy="public", tags=3, category="10", seconds=3600):
        return {
            "video_id": vid, "title": "t", "published_at": "2026-09-01T00:00:00Z",
            "privacy": privacy, "duration_seconds": seconds, "views": 0,
            "tag_count": tags, "category_id": category,
        }

    def _playlist(self, pid="p1", count=1):
        return {"id": pid, "snippet": {"title": "목록"}, "contentDetails": {"itemCount": count}}

    def test_healthy_channel_reports_nothing(self):
        problems = ch.diagnose(
            self._channel(), [self._playlist()], [self._video()], {"p1": {"v1"}}
        )
        self.assertEqual(problems, [])

    def test_empty_branding_is_flagged_at_top_severity(self):
        channel = self._channel(
            snippet={"customUrl": None, "country": None},
            channel={"description": "", "keywords": ""},
            image={"bannerExternalUrl": None},
        )
        problems = ch.diagnose(channel, [self._playlist()], [self._video()], {"p1": {"v1"}})
        texts = [t for _, t in problems]
        self.assertTrue(any("핸들" in t for t in texts))
        self.assertTrue(any("채널 설명이 비어" in t for t in texts))
        self.assertTrue(any("키워드" in t for t in texts))
        self.assertTrue(any("배너" in t for t in texts))
        self.assertTrue(any("국가" in t for t in texts))

    def test_problems_are_ordered_worst_first(self):
        """리포트 순서가 곧 우선순위다. 심각한 것이 아래로 밀리면 안 된다."""
        channel = self._channel(channel={"keywords": ""}, image={"bannerExternalUrl": None})
        problems = ch.diagnose(channel, [self._playlist()], [self._video()], {"p1": {"v1"}})
        severities = [s for s, _ in problems]
        self.assertEqual(severities, sorted(severities, reverse=True))
        self.assertIn("키워드", problems[0][1])

    def test_orphan_public_videos_are_counted(self):
        videos = [self._video("v1"), self._video("v2"), self._video("v3")]
        problems = ch.diagnose(self._channel(), [self._playlist()], videos, {"p1": {"v1"}})
        self.assertTrue(any("재생목록에 안 들어간 공개 영상 2편" in t for _, t in problems))

    def test_private_videos_do_not_count_as_orphans(self):
        """비공개 영상은 재생목록에 없어도 정상이다 — 승인 대기 중인 오늘치가 매일 있다."""
        videos = [self._video("v1"), self._video("v2", privacy="private")]
        problems = ch.diagnose(self._channel(), [self._playlist()], videos, {"p1": {"v1"}})
        self.assertFalse(any("재생목록에 안 들어간" in t for _, t in problems))

    def test_playlist_holding_only_private_videos_is_flagged(self):
        """리브랜딩 후 남은 구 재생목록을 잡는다 — 방문자에겐 빈 목록으로 보인다."""
        videos = [self._video("v1"), self._video("old", privacy="private")]
        playlists = [self._playlist("p1"), self._playlist("legacy")]
        problems = ch.diagnose(
            self._channel(), playlists, videos, {"p1": {"v1"}, "legacy": {"old"}}
        )
        texts = [t for _, t in problems]
        self.assertTrue(any("공개 영상이 하나도 없는 재생목록 1개" in t for t in texts))

    def test_missing_playlists_flagged(self):
        problems = ch.diagnose(self._channel(), [], [self._video()], {})
        self.assertTrue(any("재생목록이 하나도 없음" in t for _, t in problems))

    def test_tag_and_category_gaps_flagged(self):
        videos = [self._video("v1", tags=0), self._video("v2", category="22")]
        problems = ch.diagnose(self._channel(), [self._playlist()], videos, {"p1": {"v1", "v2"}})
        texts = [t for _, t in problems]
        self.assertTrue(any("태그가 없는 공개 영상 1편" in t for t in texts))
        self.assertTrue(any("음악(10)이 아닌 공개 영상 1편" in t for t in texts))

    def test_short_description_flagged_separately_from_empty(self):
        channel = self._channel(channel={"description": "짧다"})
        problems = ch.diagnose(channel, [self._playlist()], [self._video()], {"p1": {"v1"}})
        texts = [t for _, t in problems]
        self.assertTrue(any("너무 짧음" in t for t in texts))
        self.assertFalse(any("비어 있음" in t for t in texts))


class TestBuildReport(unittest.TestCase):
    def test_report_states_subscriber_gap_and_problems(self):
        channel = {
            "snippet": {"title": "조선로파이", "customUrl": "@joseonlofi", "country": "KR"},
            "statistics": {"subscriberCount": "0", "viewCount": "144"},
            "brandingSettings": {"channel": {"description": "가" * 200, "keywords": "조선"}},
        }
        videos = [{
            "video_id": "v1", "title": "t", "published_at": "2026-09-01T00:00:00Z",
            "privacy": "public", "duration_seconds": 3600, "views": 5,
            "tag_count": 3, "category_id": "10",
        }]
        report = ch.build_report(channel, [], videos, {}, [(3, "재생목록이 하나도 없음")])
        self.assertIn("구독자 0 / 1000명", report)
        self.assertIn("재생목록이 하나도 없음", report)
        self.assertIn("1:00:00", report)

    def test_report_survives_channel_with_no_public_videos(self):
        """전편 비공개인 상태(리브랜딩 직후 등)에서도 0으로 나누지 않아야 한다."""
        channel = {
            "snippet": {"title": "t"}, "statistics": {"subscriberCount": "0", "viewCount": "0"},
            "brandingSettings": {"channel": {}},
        }
        report = ch.build_report(channel, [], [], {}, [])
        self.assertIn("공개 영상 0편", report)


if __name__ == "__main__":
    unittest.main()
