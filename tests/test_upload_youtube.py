import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import upload_youtube as uy


class TestUploadYoutube(unittest.TestCase):
    def test_get_credentials_raises_when_missing(self):
        with self.assertRaises(RuntimeError):
            uy.get_credentials(env={})

    def test_get_credentials_succeeds_when_present(self):
        env = {
            "YOUTUBE_CLIENT_ID": "id",
            "YOUTUBE_CLIENT_SECRET": "secret",
            "YOUTUBE_REFRESH_TOKEN": "token",
        }
        creds = uy.get_credentials(env=env)
        self.assertEqual(creds.client_id, "id")
        self.assertEqual(creds.refresh_token, "token")

    def test_build_request_body_truncates_title_and_maps_fields(self):
        metadata = {
            "title": "x" * 150,
            "description": "desc",
            "tags": ["a", "b"],
            "categoryId": "10",
            "privacyStatus": "private",
            "madeForKids": False,
        }
        body = uy.build_request_body(metadata)
        self.assertEqual(len(body["snippet"]["title"]), 100)
        self.assertEqual(body["snippet"]["tags"], ["a", "b"])
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertFalse(body["status"]["selfDeclaredMadeForKids"])
        self.assertTrue(body["status"]["containsSyntheticMedia"])  # 명시 안 해도 기본값 True

    def test_build_request_body_can_opt_out_of_synthetic_media_flag(self):
        metadata = {
            "title": "t", "description": "d", "tags": [], "categoryId": "10",
            "privacyStatus": "private", "madeForKids": False, "containsSyntheticMedia": False,
        }
        body = uy.build_request_body(metadata)
        self.assertNotIn("containsSyntheticMedia", body["status"])

    def test_upload_video_drives_resumable_upload_loop(self):
        metadata = {
            "title": "t", "description": "d", "tags": [], "categoryId": "10",
            "privacyStatus": "private", "madeForKids": False,
        }
        fake_request = mock.Mock()
        fake_request.next_chunk.side_effect = [(None, None), (None, {"id": "vid123"})]
        fake_videos = mock.Mock()
        fake_videos.insert.return_value = fake_request
        fake_youtube = mock.Mock()
        fake_youtube.videos.return_value = fake_videos

        with mock.patch("upload_youtube.MediaFileUpload", return_value=mock.Mock()):
            response = uy.upload_video("/tmp/fake.mp4", metadata, credentials=None, youtube_client=fake_youtube)

        self.assertEqual(response["id"], "vid123")
        self.assertEqual(fake_request.next_chunk.call_count, 2)
        fake_request.next_chunk.assert_called_with(num_retries=5)

    def test_get_video_returns_first_item(self):
        fake_youtube = mock.Mock()
        fake_youtube.videos.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "vid1", "snippet": {"title": "old"}, "status": {"privacyStatus": "private"}}]
        }
        item = uy.get_video("vid1", credentials=None, youtube_client=fake_youtube)
        self.assertEqual(item["snippet"]["title"], "old")

    def test_get_video_raises_when_not_found(self):
        fake_youtube = mock.Mock()
        fake_youtube.videos.return_value.list.return_value.execute.return_value = {"items": []}
        with self.assertRaises(RuntimeError):
            uy.get_video("missing", credentials=None, youtube_client=fake_youtube)

    def test_update_video_merges_overrides_onto_current_snippet(self):
        fake_youtube = mock.Mock()
        fake_youtube.videos.return_value.list.return_value.execute.return_value = {
            "items": [{
                "id": "vid1",
                "snippet": {"title": "old title", "description": "old desc", "tags": ["a"], "categoryId": "10"},
                "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
            }]
        }
        fake_youtube.videos.return_value.update.return_value.execute.return_value = {
            "id": "vid1", "status": {"privacyStatus": "public"},
        }

        response = uy.update_video(
            "vid1", credentials=None, title="new title", privacy_status="public", youtube_client=fake_youtube,
        )

        self.assertEqual(response["status"]["privacyStatus"], "public")
        _, kwargs = fake_youtube.videos.return_value.update.call_args
        self.assertEqual(kwargs["body"]["snippet"]["title"], "new title")
        self.assertEqual(kwargs["body"]["snippet"]["description"], "old desc")  # 지정 안 한 필드는 유지
        self.assertEqual(kwargs["body"]["status"]["privacyStatus"], "public")

    def test_set_thumbnail_calls_thumbnails_set(self):
        fake_youtube = mock.Mock()
        fake_youtube.thumbnails.return_value.set.return_value.execute.return_value = {"items": []}
        with mock.patch("upload_youtube.MediaFileUpload", return_value=mock.Mock()):
            uy.set_thumbnail("vid1", "/tmp/x.png", credentials=None, youtube_client=fake_youtube)
        fake_youtube.thumbnails.return_value.set.assert_called_once()
        _, kwargs = fake_youtube.thumbnails.return_value.set.call_args
        self.assertEqual(kwargs["videoId"], "vid1")

    def test_get_or_create_playlist_reuses_saved_id(self):
        state = {"main_playlist_id": "existing_pl"}
        fake_youtube = mock.Mock()
        playlist_id = uy.get_or_create_playlist(state, credentials=None, title="t", youtube_client=fake_youtube)
        self.assertEqual(playlist_id, "existing_pl")
        fake_youtube.playlists.return_value.insert.assert_not_called()

    def test_get_or_create_playlist_creates_and_saves_when_missing(self):
        state = {}
        fake_youtube = mock.Mock()
        fake_youtube.playlists.return_value.insert.return_value.execute.return_value = {"id": "new_pl"}
        playlist_id = uy.get_or_create_playlist(state, credentials=None, title="t", youtube_client=fake_youtube)
        self.assertEqual(playlist_id, "new_pl")
        self.assertEqual(state["main_playlist_id"], "new_pl")

    def test_add_video_to_playlist_calls_playlist_items_insert(self):
        fake_youtube = mock.Mock()
        fake_youtube.playlistItems.return_value.insert.return_value.execute.return_value = {"id": "item1"}
        uy.add_video_to_playlist("pl1", "vid1", credentials=None, youtube_client=fake_youtube)
        _, kwargs = fake_youtube.playlistItems.return_value.insert.call_args
        self.assertEqual(kwargs["body"]["snippet"]["playlistId"], "pl1")
        self.assertEqual(kwargs["body"]["snippet"]["resourceId"]["videoId"], "vid1")


if __name__ == "__main__":
    unittest.main()
