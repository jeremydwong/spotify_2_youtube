#!/usr/bin/env python

import json
import os
import stat
import tempfile
import unittest
from http.cookiejar import Cookie
from unittest.mock import MagicMock, patch

from spotify2ytmusic import ytmusic_login as L


def cookie(name, value, domain=".youtube.com"):
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        domain,
        True,
        True,
        "/",
        True,
        True,
        None,
        False,
        None,
        None,
        {},
    )


SIGNED_IN = [
    cookie("SAPISID", "s"),
    cookie("__Secure-3PAPISID", "s3"),
    cookie("SID", "x"),
    cookie("NID", "google-only", domain=".google.com"),
]


def fake_client_factory(accounts):
    """accounts: list of labels for authuser 0..n-1; beyond that Google repeats the last."""

    def factory(headers):
        n = int(headers["x-goog-authuser"])
        yt = MagicMock()
        name = accounts[min(n, len(accounts) - 1)]
        yt.get_account_info.return_value = {
            "accountName": name,
            "channelHandle": "@" + name,
        }
        return yt

    return factory


class TestLogin(unittest.TestCase):
    def test_build_headers_only_youtube_and_placeholder_auth(self):
        with patch(
            "yt_dlp.cookies.extract_cookies_from_browser", return_value=SIGNED_IN
        ):
            cookies = L.read_youtube_cookies("chrome")
        self.assertNotIn("NID", cookies)
        h = L.build_headers(cookies, 2)
        self.assertEqual(h["x-goog-authuser"], "2")
        self.assertIn("SAPISIDHASH", h["authorization"])
        self.assertIn("__Secure-3PAPISID=s3", h["cookie"])

    def test_not_signed_in(self):
        with self.assertRaises(L.LoginError) as cm:
            L.build_headers({"YSC": "x"})
        self.assertIn("signed in", str(cm.exception))

    def test_find_accounts_stops_on_repeat(self):
        accts = L.find_accounts(
            {"SAPISID": "a", "__Secure-3PAPISID": "b"},
            fake_client_factory(["Dad", "Kid"]),
        )
        self.assertEqual(accts, [(0, "Dad (@Dad)"), (1, "Kid (@Kid)")])

    def test_login_end_to_end_picks_account_and_writes_private_file(self):
        with tempfile.TemporaryDirectory() as d, patch(
            "yt_dlp.cookies.extract_cookies_from_browser", return_value=SIGNED_IN
        ):
            out = os.path.join(d, "auth.json")
            label = L.login(
                "chrome",
                out,
                choose=lambda a: 1,
                client_factory=fake_client_factory(["Dad", "Kid"]),
            )
            self.assertEqual(label, "Kid (@Kid)")
            self.assertEqual(json.load(open(out))["x-goog-authuser"], "1")
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)

    def test_headers_accepted_by_ytmusicapi_as_browser_auth(self):
        from ytmusicapi.auth.auth_parse import determine_auth_type
        from ytmusicapi.auth.types import AuthType
        from requests.structures import CaseInsensitiveDict

        h = L.build_headers({"SAPISID": "a", "__Secure-3PAPISID": "b"})
        self.assertEqual(determine_auth_type(CaseInsensitiveDict(h)), AuthType.BROWSER)

    def test_no_terminal_asks_for_account_flag(self):
        accounts = [(0, "A"), (1, "B")]
        with patch.object(L.sys, "stdin") as stdin:
            stdin.isatty.return_value = False
            with self.assertRaises(L.LoginError) as cm:
                L._ask_account(accounts)
        self.assertIn("--account", str(cm.exception))
        self.assertIn("1: B", str(cm.exception))

    def test_extractor_error_is_explained(self):
        with patch(
            "yt_dlp.cookies.extract_cookies_from_browser",
            side_effect=PermissionError("Operation not permitted"),
        ):
            with self.assertRaises(L.LoginError) as cm:
                L.read_youtube_cookies("safari")
        self.assertIn("Full Disk Access", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
