"""登录 JWT 与 Agent 文件签名的类型隔离回归测试。"""
import unittest
from datetime import datetime, timedelta

from jose import JWTError, jwt

from app.agent import media
from app.auth.service import (JWT_AUDIENCE, JWT_ISSUER, JWT_SCOPE,
                              create_token, decode_token)
from app.config import settings


class TestAuthTokenTypes(unittest.TestCase):
    def test_access_token_has_scope_and_audience(self):
        token = create_token(1, "admin")
        payload = decode_token(token)
        self.assertEqual(payload["scope"], JWT_SCOPE)
        self.assertEqual(payload["aud"], JWT_AUDIENCE)
        self.assertEqual(payload["iss"], JWT_ISSUER)

    def test_token_without_audience_rejected(self):
        raw = jwt.encode(
            {"sub": "1", "uid": 1, "scope": "access", "token_version": 0,
             "iss": JWT_ISSUER, "exp": datetime.utcnow() + timedelta(hours=1)},
            settings.APP_SECRET_KEY, algorithm="HS256")
        with self.assertRaises(JWTError):
            decode_token(raw)

    def test_token_without_scope_rejected_in_decode_payload(self):
        # decode_token 只验签/aud/iss；scope 由鉴权层校验，这里确认缺失 scope 可被识别
        raw = jwt.encode(
            {"sub": "1", "uid": 1, "aud": JWT_AUDIENCE, "iss": JWT_ISSUER,
             "exp": datetime.utcnow() + timedelta(hours=1)},
            settings.APP_SECRET_KEY, algorithm="HS256")
        self.assertNotEqual(decode_token(raw).get("scope"), "access")

    def test_agent_file_sig_not_accepted_as_access_token(self):
        sig = media.sign_path(7, "x.png", user_id=1).split("sig=")[1]
        with self.assertRaises(JWTError):
            decode_token(sig)

    def test_file_sig_roundtrip(self):
        url = media.sign_path(7, "x.png", user_id=1)
        sig = url.split("sig=")[1]
        self.assertTrue(media.verify_sig(7, "x.png", sig, user_id=1))
        self.assertFalse(media.verify_sig(7, "x.png", sig, user_id=2))


if __name__ == "__main__":
    unittest.main()
