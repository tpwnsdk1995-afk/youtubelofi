import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import finalize_publish as fp


class TestFinalizePublish(unittest.TestCase):
    def test_main_calls_update_video_with_parsed_args(self):
        fake_credentials = mock.Mock()
        fake_response = {"status": {"privacyStatus": "public"}}

        argv = ["finalize_publish.py", "--video-id", "vid1", "--title", "새 제목"]
        with mock.patch.object(sys, "argv", argv), \
             mock.patch("finalize_publish.uy.get_credentials", return_value=fake_credentials), \
             mock.patch("finalize_publish.uy.update_video", return_value=fake_response) as fake_update:
            fp.main()

        fake_update.assert_called_once_with(
            "vid1", fake_credentials, title="새 제목", description=None, privacy_status="public",
        )

    def test_main_exits_when_credentials_missing(self):
        argv = ["finalize_publish.py", "--video-id", "vid1"]
        with mock.patch.object(sys, "argv", argv), \
             mock.patch("finalize_publish.uy.get_credentials", side_effect=RuntimeError("no creds")):
            with self.assertRaises(SystemExit):
                fp.main()


if __name__ == "__main__":
    unittest.main()
