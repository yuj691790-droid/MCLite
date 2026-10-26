from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclite import auth


def make_jwt(payload: dict) -> str:
    head = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{head}.{body}.sig"


class AuthFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.accounts = Path(self.tmp.name) / "accounts.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _happy_patches(self, xsts_payload=None):
        device = {
            "device_code": "dc",
            "user_code": "UC",
            "verification_uri": "https://microsoft.com/link",
            "expires_in": 900,
            "interval": 5,
        }
        token_responses = [
            (400, {"error": "authorization_pending"}),
            (200, {"access_token": "ms-token", "refresh_token": "refresh-1", "expires_in": 3600}),
        ]
        calls = {"xbl": 0, "xsts": 0, "mc": 0}

        def fake_post_form(url, fields, **kwargs):
            if "devicecode" in url:
                return 200, dict(device)
            return token_responses.pop(0)

        def fake_post_json(url, payload, **kwargs):
            if "user.auth.xboxlive.com" in url:
                calls["xbl"] += 1
                return 200, {"Token": "xbl-token", "DisplayClaims": {"xui": [{"uhs": "uhs-1"}]}}
            if "xsts.auth.xboxlive.com" in url:
                calls["xsts"] += 1
                return 200, {
                    "Token": make_jwt({"xuid": "12345"}),
                    "DisplayClaims": {"xui": [{"uhs": "uhs-2"}]},
                }
            if "login_with_xbox" in url:
                calls["mc"] += 1
                return 200, {"access_token": "mc-token", "expires_in": 3600}
            raise AssertionError(url)

        def fake_fetch_json(url, **kwargs):
            return {"id": "00000000-0000-0000-0000-000000000001", "name": "Steve"}

        return device, calls, fake_post_form, fake_post_json, fake_fetch_json

    def test_full_login_chain_and_persistence(self):
        device, calls, post_form, post_json, fetch_json = self._happy_patches()
        with mock.patch.object(auth, "post_form", post_form), \
             mock.patch.object(auth, "post_json", post_json), \
             mock.patch.object(auth, "fetch_json", fetch_json), \
             mock.patch.object(auth.time, "sleep", lambda _s: None):
            client = auth.MicrosoftAuth("client-id", self.accounts)
            start = client.start_device_code()
            self.assertEqual(start.user_code, "UC")
            session = client.wait_for_login(start)

        self.assertEqual(session.username, "Steve")
        self.assertEqual(session.uuid, "00000000-0000-0000-0000-000000000001")
        self.assertEqual(session.access_token, "mc-token")
        self.assertEqual(session.refresh_token, "refresh-1")
        self.assertEqual(session.xuid, "12345")
        self.assertFalse(session.expired)
        self.assertEqual(calls, {"xbl": 1, "xsts": 1, "mc": 1})
        saved = json.loads(self.accounts.read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["username"], "Steve")
        self.assertNotIn("device_code", saved[0])

    def test_refresh_uses_refresh_token(self):
        device, calls, post_form, post_json, fetch_json = self._happy_patches()
        seen = {}

        def refresh_form(url, fields, **kwargs):
            seen["refresh"] = fields.get("refresh_token")
            return 200, {"access_token": "ms-2", "refresh_token": "refresh-2", "expires_in": 3600}

        with mock.patch.object(auth, "post_form", refresh_form), \
             mock.patch.object(auth, "post_json", post_json), \
             mock.patch.object(auth, "fetch_json", fetch_json), \
             mock.patch.object(auth.time, "sleep", lambda _s: None):
            client = auth.MicrosoftAuth("client-id", self.accounts)
            expired = auth.AuthSession(
                username="Steve", uuid="u", access_token="old", refresh_token="refresh-1",
                expires_at=0.0, client_id="client-id",
            )
            session = client.refresh(expired)

        self.assertEqual(seen["refresh"], "refresh-1")
        self.assertEqual(session.refresh_token, "refresh-2")
        self.assertEqual(session.access_token, "mc-token")

    def test_xsts_error_is_human_readable(self):
        device, calls, post_form, post_json, fetch_json = self._happy_patches()

        def xsts_error(url, payload, **kwargs):
            if "user.auth.xboxlive.com" in url:
                return 200, {"Token": "xbl", "DisplayClaims": {"xui": [{"uhs": "u"}]}}
            return 401, {"XErr": 2148916233}

        with mock.patch.object(auth, "post_form", post_form), \
             mock.patch.object(auth, "post_json", xsts_error), \
             mock.patch.object(auth, "fetch_json", fetch_json), \
             mock.patch.object(auth.time, "sleep", lambda _s: None):
            client = auth.MicrosoftAuth("client-id", self.accounts)
            start = client.start_device_code()
            with self.assertRaises(auth.AuthError) as ctx:
                client.wait_for_login(start)
        self.assertIn("Xbox", str(ctx.exception))

    def test_declined_login(self):
        device = {
            "device_code": "dc", "user_code": "UC", "verification_uri": "https://x",
            "expires_in": 900, "interval": 5,
        }
        with mock.patch.object(auth, "post_form", lambda url, fields, **kw: (200, dict(device)) if "devicecode" in url else (400, {"error": "authorization_declined"})), \
             mock.patch.object(auth.time, "sleep", lambda _s: None):
            client = auth.MicrosoftAuth("cid", None)
            start = client.start_device_code()
            with self.assertRaises(auth.AuthError):
                client.wait_for_login(start)


if __name__ == "__main__":
    unittest.main()
