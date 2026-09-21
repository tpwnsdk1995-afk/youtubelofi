import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import setup_analytics_auth as saa


class TestBuildAuthUrl(unittest.TestCase):
    def _params(self, client_id="cid.apps.googleusercontent.com"):
        url = saa.build_auth_url(client_id)
        return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)

    def test_requests_both_scopes_together(self):
        """따로 받으면 refresh token이 하나로 안 묶여 업로드가 멈춘다."""
        scopes = self._params()["scope"][0].split()
        self.assertIn("https://www.googleapis.com/auth/youtube", scopes)
        self.assertIn("https://www.googleapis.com/auth/yt-analytics.readonly", scopes)

    def test_asks_for_a_refresh_token(self):
        """access_type=offline과 prompt=consent가 둘 다 있어야 refresh token이 나온다."""
        p = self._params()
        self.assertEqual(p["access_type"][0], "offline")
        self.assertEqual(p["prompt"][0], "consent")

    def test_redirect_matches_what_the_oauth_client_registers(self):
        self.assertEqual(self._params()["redirect_uri"][0], "http://localhost")
        self.assertEqual(saa.REDIRECT_URI, "http://localhost")

    def test_empty_client_id_is_refused(self):
        for bad in ("", None):
            with self.assertRaises(ValueError):
                saa.build_auth_url(bad)


class TestGrantedScopes(unittest.TestCase):
    def test_detects_analytics_grant(self):
        payload = {"scope": "https://www.googleapis.com/auth/youtube "
                            "https://www.googleapis.com/auth/yt-analytics.readonly"}
        scopes = saa.granted_scopes(payload)
        self.assertTrue(any("yt-analytics" in s for s in scopes))

    def test_missing_scope_field_is_empty_not_a_crash(self):
        self.assertEqual(saa.granted_scopes({}), [])


class TestExchangeRequiresRefreshToken(unittest.TestCase):
    def test_response_without_refresh_token_raises(self):
        """코드는 1회용이다. 여기서 조용히 넘어가면 처음부터 다시 로그인해야 한다."""
        saved = saa.urllib.request.urlopen

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"access_token": "abc", "scope": "x"}'

        saa.urllib.request.urlopen = lambda *a, **k: FakeResponse()
        try:
            with self.assertRaises(RuntimeError):
                saa.exchange_code("cid", "secret", "code")
        finally:
            saa.urllib.request.urlopen = saved


if __name__ == "__main__":
    unittest.main()
