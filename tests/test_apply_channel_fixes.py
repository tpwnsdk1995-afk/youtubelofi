import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import apply_channel_fixes as acf


class TestBuildBrandingUpdate(unittest.TestCase):
    """channels.update는 보낸 brandingSettings.channel로 통째로 덮어쓴다.

    keywords만 담아 보내면 채널 설명·국가가 지워진다. 여기서 틀리면 사장님이
    직접 써넣은 200자 설명이 한 번의 API 호출로 사라지므로, 보존을 못박아 둔다.
    """

    def setUp(self):
        self.current = {
            "snippet": {"title": "조선로파이", "description": "스니펫 설명", "country": "KR"},
            "brandingSettings": {
              "channel": {
                "title": "조선로파이",
                "description": "호랑이 담배 피우던 시절 —" + "가" * 180,
                "keywords": '"집중력 음악" "공부 플레이리스트"',
                "country": "KR",
                "defaultLanguage": "ko",
                "unsubscribedTrailer": "abc123",
              },
              "image": {"bannerExternalUrl": "https://example.invalid/banner"},
            },
        }

    def test_every_other_field_survives(self):
        body = acf.build_branding_update(self.current, '"조선 로파이"')
        ch = body["brandingSettings"]["channel"]
        self.assertEqual(ch["title"], "조선로파이")
        self.assertEqual(ch["description"], self.current["brandingSettings"]["channel"]["description"])
        self.assertEqual(ch["country"], "KR")
        self.assertEqual(ch["defaultLanguage"], "ko")
        self.assertEqual(ch["unsubscribedTrailer"], "abc123")

    def test_body_is_wrapped_in_brandingSettings(self):
        """part=brandingSettings 업데이트는 이 래퍼가 없으면 400 Required로 거부된다.

        앞서 body를 {"channel": ...}로 보내 네 번 실패했고, 그때 테스트가
        body["channel"]을 검사하는 바람에 같은 실수를 그대로 통과시켰다.
        """
        body = acf.build_branding_update(self.current, "새 키워드")
        self.assertEqual(set(body), {"brandingSettings"})
        self.assertIn("channel", body["brandingSettings"])

    def test_keywords_are_replaced(self):
        body = acf.build_branding_update(self.current, '"조선 로파이"')
        self.assertEqual(body["brandingSettings"]["channel"]["keywords"], '"조선 로파이"')

    def test_does_not_mutate_the_fetched_channel(self):
        """원본을 건드리면 같은 응답을 재사용하는 호출부가 조용히 오염된다."""
        acf.build_branding_update(self.current, "새 키워드")
        self.assertEqual(
            self.current["brandingSettings"]["channel"]["keywords"],
            '"집중력 음악" "공부 플레이리스트"',
        )

    def test_missing_branding_does_not_crash(self):
        body = acf.build_branding_update({}, "새 키워드")
        self.assertEqual(
            body["brandingSettings"]["channel"],
            {"keywords": "새 키워드", "title": "", "description": ""},
        )

    def test_title_and_description_are_filled_from_snippet(self):
        """읽을 때 이 둘은 snippet에만 온다. 안 채우면 API가 400 Required로 거부한다."""
        resource = {"snippet": {"title": "조선로파이", "description": "설명 200자", "country": "KR"},
                    "brandingSettings": {"channel": {"keywords": "old"}}}
        ch = acf.build_branding_update(resource, "new")["brandingSettings"]["channel"]
        self.assertEqual(ch["title"], "조선로파이")
        self.assertEqual(ch["description"], "설명 200자")
        self.assertEqual(ch["country"], "KR")

    def test_branding_values_win_over_snippet_when_present(self):
        ch = acf.build_branding_update(self.current, "new")["brandingSettings"]["channel"]
        self.assertEqual(ch["description"], self.current["brandingSettings"]["channel"]["description"])

    def test_country_is_omitted_rather_than_blanked(self):
        """빈 문자열로 보내면 설정돼 있던 국가가 지워진다."""
        ch = acf.build_branding_update({"snippet": {}, "brandingSettings": {}}, "new")["brandingSettings"]["channel"]
        self.assertNotIn("country", ch)


class TestJoseonKeywords(unittest.TestCase):
    def test_within_youtube_length_limit(self):
        self.assertLessEqual(len(acf.JOSEON_KEYWORDS), acf.MAX_KEYWORDS_LENGTH)

    def test_leads_with_the_niche_we_actually_own(self):
        """공부 키워드로 시작하면 리브랜딩 전과 같은 자리에서 경쟁하게 된다."""
        self.assertTrue(acf.JOSEON_KEYWORDS.startswith('"조선 로파이"'))
        for term in ("국악", "사극", "가야금", "korean lofi"):
            self.assertIn(term, acf.JOSEON_KEYWORDS)


class TestInferMood(unittest.TestCase):
    def test_calm_tags_pick_calm(self):
        self.assertEqual(acf.infer_mood(["공부음악", "시험기간", "lofi"]), "calm")

    def test_groove_tags_pick_groove(self):
        self.assertEqual(acf.infer_mood(["드라이브음악", "국악힙합"]), "groove")

    def test_unknown_tags_return_none(self):
        """모르면 넣지 않는다 — 엉뚱한 목록에 들어가면 사람이 찾아 빼야 한다."""
        self.assertIsNone(acf.infer_mood(["lofi", "플레이리스트"]))
        self.assertIsNone(acf.infer_mood([]))
        self.assertIsNone(acf.infer_mood(None))

    def test_a_tie_returns_none(self):
        self.assertIsNone(acf.infer_mood(["공부음악", "드라이브음악"]))

    def test_mood_tag_sets_do_not_overlap(self):
        """두 무드가 같은 태그를 공유하면 판정이 흔들린다."""
        self.assertEqual(acf.MOOD_TAGS["calm"] & acf.MOOD_TAGS["groove"], set())

    def test_every_mood_has_a_playlist_title(self):
        self.assertEqual(set(acf.MOOD_TAGS), set(acf.MOOD_PLAYLIST_TITLE))


class TestMainRefusesWithoutWork(unittest.TestCase):
    def test_no_flags_does_nothing_and_exits_nonzero(self):
        """실수로 인자 없이 돌았을 때 채널을 건드리면 안 된다."""
        self.assertEqual(acf.main([]), 2)


if __name__ == "__main__":
    unittest.main()


class TestBuildVideoLanguageUpdate(unittest.TestCase):
    """videos.update도 보낸 snippet으로 통째로 덮어쓴다.

    언어 두 필드만 담아 보내면 제목·설명·태그가 34편에서 한꺼번에 날아간다.
    되돌릴 방법이 없으므로 보존을 테스트로 못박는다.
    """

    def _video(self, **snippet_overrides):
        snippet = {
            "title": "Playlist 과거시험 D-1 📖 공부가 잘되는 조선 감성",
            "description": "#공부플리 #조선로파이\n\nTracklist🎧\n0:00 첫 곡",
            "categoryId": "10",
            "tags": ["조선로파이", "국악로파이", "가야금"],
            "thumbnails": {"default": {"url": "https://example.invalid/t.jpg"}},
        }
        snippet.update(snippet_overrides)
        return {"id": "vid123", "snippet": snippet}

    def test_title_description_tags_and_category_survive(self):
        video = self._video()
        body = acf.build_video_language_update(video, "ko")
        sn = body["snippet"]
        self.assertEqual(sn["title"], video["snippet"]["title"])
        self.assertEqual(sn["description"], video["snippet"]["description"])
        self.assertEqual(sn["tags"], video["snippet"]["tags"])
        self.assertEqual(sn["categoryId"], "10")

    def test_both_language_fields_are_set(self):
        body = acf.build_video_language_update(self._video(), "ko")
        self.assertEqual(body["snippet"]["defaultLanguage"], "ko")
        self.assertEqual(body["snippet"]["defaultAudioLanguage"], "ko")

    def test_id_is_included(self):
        self.assertEqual(acf.build_video_language_update(self._video(), "ko")["id"], "vid123")

    def test_does_not_mutate_the_fetched_video(self):
        video = self._video()
        acf.build_video_language_update(video, "ko")
        self.assertNotIn("defaultLanguage", video["snippet"])

    def test_refuses_when_required_fields_are_missing(self):
        """title이나 categoryId 없이 보내면 API가 그 필드를 지운다. 아예 막는다."""
        with self.assertRaises(ValueError):
            acf.build_video_language_update(self._video(title=""), "ko")
        with self.assertRaises(ValueError):
            acf.build_video_language_update(self._video(categoryId=None), "ko")


class TestPlaylistDescriptions(unittest.TestCase):
    def test_every_mood_playlist_has_a_description(self):
        for title in acf.MOOD_PLAYLIST_TITLE.values():
            self.assertIn(title, acf.PLAYLIST_DESCRIPTIONS)

    def test_descriptions_carry_the_niche_keywords(self):
        for text in acf.PLAYLIST_DESCRIPTIONS.values():
            self.assertIn("조선", text)
            self.assertTrue("국악" in text or "가야금" in text)

    def test_descriptions_use_our_voice_not_the_reference_channel(self):
        """하게체(~다네/~주시게)가 우리 말투다. 조선재즈의 하오체(~다오)는 금지."""
        for text in acf.PLAYLIST_DESCRIPTIONS.values():
            self.assertNotIn("다오", text)
            self.assertTrue("라네" in text or "주시게" in text)
