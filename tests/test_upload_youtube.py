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


if __name__ == "__main__":
    unittest.main()
