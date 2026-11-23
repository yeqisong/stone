"""飞书回调安全：签名、解密、去重、回复体归一化。"""
import base64
import hashlib
import json
import os
import time
import unittest

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.feishu.security import (claim_once, decrypt_payload, rate_ok,
                                 reset_seen_for_test, verify_signature)
from app.feishu.client import normalize_reply


def _sign(body: bytes, ts: str, nonce: str, key: str) -> str:
    return hashlib.sha256(
        f"{ts}{nonce}{key}{body.decode()}".encode("utf-8")).hexdigest()


class TestFeishuSignature(unittest.TestCase):
    def setUp(self):
        self.body = json.dumps({"type": "event", "x": 1}, separators=(",", ":")).encode()
        self.ts = str(int(time.time()))
        self.nonce = "nonce-123"
        self.key = "encrypt-key"

    def test_valid_signature(self):
        sig = _sign(self.body, self.ts, self.nonce, self.key)
        ok, why = verify_signature(self.body, self.ts, self.nonce, sig, self.key)
        self.assertTrue(ok, why)

    def test_bad_signature_rejected(self):
        ok, _ = verify_signature(self.body, self.ts, self.nonce, "0" * 64, self.key)
        self.assertFalse(ok)

    def test_stale_timestamp_rejected(self):
        old = str(int(time.time()) - 3600)
        sig = _sign(self.body, old, self.nonce, self.key)
        ok, why = verify_signature(self.body, old, self.nonce, sig, self.key)
        self.assertFalse(ok)
        self.assertIn("时间戳", why)

    def test_missing_headers_rejected(self):
        ok, _ = verify_signature(self.body, "", "", "", self.key)
        self.assertFalse(ok)


class TestFeishuDecrypt(unittest.TestCase):
    def test_roundtrip_and_bad_cipher(self):
        key = "encrypt-key"
        plain = json.dumps({"type": "event", "event": {"x": 1}},
                           ensure_ascii=False).encode()
        padder = padding.PKCS7(128).padder()
        padded = padder.update(plain) + padder.finalize()
        iv = os.urandom(16)
        enc = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()),
                     modes.CBC(iv)).encryptor()
        ct = enc.update(padded) + enc.finalize()
        token = base64.b64encode(iv + ct).decode()
        self.assertEqual(decrypt_payload(token, key)["type"], "event")
        with self.assertRaises(ValueError):
            decrypt_payload("not-base64", key)


class TestFeishuDedup(unittest.TestCase):
    def tearDown(self):
        reset_seen_for_test()

    def test_claim_once(self):
        reset_seen_for_test()
        self.assertTrue(claim_once("m:1"))
        self.assertFalse(claim_once("m:1"))
        self.assertTrue(claim_once("m:2"))
        self.assertTrue(claim_once(""))


class TestFeishuRateLimit(unittest.TestCase):
    def tearDown(self):
        reset_seen_for_test()

    def test_rate_limit_window(self):
        reset_seen_for_test()
        self.assertTrue(all(rate_ok("u:1", limit=3, window=60) for _ in range(3)))
        self.assertFalse(rate_ok("u:1", limit=3, window=60))
        self.assertTrue(rate_ok("u:2", limit=3, window=60))


class TestNormalizeReply(unittest.TestCase):
    def test_plain_text(self):
        p = normalize_reply("你好")
        self.assertEqual(p["msg_type"], "text")
        self.assertEqual(json.loads(p["content"])["text"], "你好")

    def test_card_json(self):
        card = {"header": {"title": {"content": "确认"}}}
        p = normalize_reply(json.dumps({"msg_type": "interactive", "card": card}))
        self.assertEqual(p["msg_type"], "interactive")
        self.assertEqual(json.loads(p["content"])["header"]["title"]["content"], "确认")

    def test_json_text_message(self):
        raw = json.dumps({"msg_type": "text", "content": {"text": "好的"}})
        p = normalize_reply(raw)
        self.assertEqual(p["msg_type"], "text")
        self.assertEqual(json.loads(p["content"])["text"], "好的")


if __name__ == "__main__":
    unittest.main()
