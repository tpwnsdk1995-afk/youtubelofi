import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_music_library as sml


class TestSyncMusicLibrary(unittest.TestCase):
    def test_list_library_files_paginates(self):
        fake_service = mock.Mock()
        fake_service.files.return_value.list.return_value.execute.side_effect = [
            {"files": [{"id": "1", "name": "a.mp3"}], "nextPageToken": "next"},
            {"files": [{"id": "2", "name": "b.mp3"}], "nextPageToken": None},
        ]
        files = sml.list_library_files(fake_service, "folder123")
        self.assertEqual([f["name"] for f in files], ["a.mp3", "b.mp3"])

    def test_sync_library_downloads_new_files_and_skips_existing(self):
        fake_service = mock.Mock()
        fake_service.files.return_value.list.return_value.execute.return_value = {
            "files": [{"id": "1", "name": "a.mp3"}, {"id": "2", "name": "b.mp3"}],
            "nextPageToken": None,
        }

        with tempfile.TemporaryDirectory() as d:
            local_dir = Path(d) / "music_library"
            local_dir.mkdir()
            (local_dir / "a.mp3").write_bytes(b"already-here")

            with mock.patch.object(sml, "download_file") as fake_download:
                files, downloaded = sml.sync_library(fake_service, "folder123", local_dir)

            self.assertEqual(len(files), 2)
            self.assertEqual(downloaded, ["b.mp3"])
            fake_download.assert_called_once_with(fake_service, "2", local_dir / "b.mp3")

    def test_get_drive_service_builds_from_service_account_json(self):
        fake_json = '{"type": "service_account", "client_email": "x@y.iam.gserviceaccount.com", "private_key": "fake", "token_uri": "https://oauth2.googleapis.com/token"}'
        with mock.patch("sync_music_library.service_account.Credentials.from_service_account_info") as fake_from_info, \
             mock.patch("sync_music_library.build") as fake_build:
            fake_from_info.return_value = "fake-creds"
            sml.get_drive_service(fake_json)
            fake_from_info.assert_called_once()
            fake_build.assert_called_once_with("drive", "v3", credentials="fake-creds")


if __name__ == "__main__":
    unittest.main()
