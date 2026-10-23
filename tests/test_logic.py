"""Offline checks for schedule, encryption, warnings, and the public-file guard."""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scanner"))

import crypto_payload
import datasrc
import publish_util
import schedule
import site_rules
import trendline_scan as TS


class ScheduleTests(unittest.TestCase):
    def test_floor_and_15m_due(self):
        now = dt.datetime(2026, 10, 23, 14, 10, tzinfo=dt.timezone.utc)
        self.assertEqual(schedule.floor_slot(now).strftime("%H:%M"), "14:02")
        self.assertTrue(schedule.due("15m", 0, now))
        last = dt.datetime(2026, 10, 23, 14, 5, tzinfo=dt.timezone.utc).timestamp()
        self.assertFalse(schedule.due("15m", last, now))
        later = dt.datetime(2026, 10, 23, 14, 18, tzinfo=dt.timezone.utc)
        self.assertTrue(schedule.due("15m", last, later))

    def test_higher_timeframes_wait_for_their_close(self):
        last = dt.datetime(2026, 10, 23, 0, 6, tzinfo=dt.timezone.utc).timestamp()
        at_1 = dt.datetime(2026, 10, 23, 1, 2, tzinfo=dt.timezone.utc)
        at_4 = dt.datetime(2026, 10, 23, 4, 3, tzinfo=dt.timezone.utc)
        self.assertFalse(schedule.due("4h", last, at_1))
        self.assertTrue(schedule.due("4h", last, at_4))
        self.assertFalse(schedule.due("1d", last, at_4))
        self.assertTrue(schedule.due("30m", last, dt.datetime(2026, 10, 23, 0, 32, tzinfo=dt.timezone.utc)))

    def test_next_slot_matches_and_is_in_the_future(self):
        now = dt.datetime(2026, 10, 23, 14, 10, tzinfo=dt.timezone.utc)
        for tf in schedule.TFS:
            nxt = schedule.next_slot(tf, now)
            self.assertGreater(nxt, now)
            self.assertTrue(schedule.matches(tf, nxt))
        self.assertEqual(schedule.next_slot("1M", now), dt.datetime(2026, 11, 1, 0, 2, tzinfo=dt.timezone.utc))
        self.assertEqual(schedule.next_slot("1w", now), dt.datetime(2026, 10, 26, 0, 2, tzinfo=dt.timezone.utc))

    def test_select(self):
        self.assertEqual(schedule.select("all", {}), schedule.TFS)
        self.assertEqual(schedule.select("4h", {}), ["4h"])
        self.assertEqual(schedule.select("15m,1d", {}), ["15m", "1d"])
        fresh = dt.datetime(2026, 10, 23, 14, 5, tzinfo=dt.timezone.utc).timestamp()
        now = dt.datetime(2026, 10, 23, 14, 10, tzinfo=dt.timezone.utc)
        due = schedule.select("due", {tf: {"generated_ts": fresh} for tf in schedule.TFS}, now)
        self.assertNotIn("15m", due)
        self.assertNotIn("4h", due)

    def test_ist_label_uses_kolkata_offset(self):
        stamp = dt.datetime(2026, 10, 23, 14, 2, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(schedule.fmt_ist(stamp), "23 Oct, 07:32 PM IST")


class CryptoTests(unittest.TestCase):
    def test_roundtrip_and_wrong_password(self):
        password = "correct-horse"
        payload = crypto_payload.encrypt_json({"ok": True, "n": 3}, password)
        self.assertEqual(crypto_payload.decrypt_json(payload, password), {"ok": True, "n": 3})
        raw = json.dumps(payload)
        self.assertNotIn("correct-horse", raw)
        self.assertNotIn('"ok"', raw)
        with self.assertRaises(Exception):
            crypto_payload.decrypt_json(payload, "nope")

    def test_webcrypto_accepts_the_same_payload(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        password = "webcrypto-check"
        payload = crypto_payload.encrypt_json({"ok": True, "answer": 7}, password)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "payload.json")
            json.dump(payload, open(path, "w"))
            script = os.path.join(tmp, "check.mjs")
            open(script, "w").write(r"""
import { readFileSync } from "fs";
const payload = JSON.parse(readFileSync(process.argv[2], "utf8"));
const password = process.argv[3];
const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const mat = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
const key = await crypto.subtle.deriveKey(
  { name: "PBKDF2", salt: b64(payload.salt), iterations: payload.iter, hash: "SHA-256" },
  mat, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(payload.iv) }, key, b64(payload.ct));
const obj = JSON.parse(new TextDecoder().decode(plain));
if (!obj.ok || obj.answer !== 7) process.exit(2);
""")
            import subprocess
            proc = subprocess.run([node, script, path, password], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


class SiteRuleTests(unittest.TestCase):
    def test_warnings_and_zone_match_the_worker(self):
        hit = {
            "side": "LONG", "rsi14": 80, "pullback_entry": 100, "sl": 70,
            "close": 100, "trendline_now": 90, "atr14": 1, "rr_pullback": [1.2],
            "touches": 2, "state": "FAILED", "vol_ratio": 0.4,
            "pullback_zone": [110, 90], "forming": {"price": 100},
        }
        warnings = site_rules.warnings(hit)
        self.assertEqual(warnings[:3], ["RSI overbought", "wide SL 30%", "extended"])
        self.assertIn("TP1 R:R<1.5", warnings)
        self.assertIn("2 touches", warnings)
        self.assertIn("false breakout", warnings)
        self.assertIn("weak volume", warnings)
        self.assertTrue(site_rules.in_zone(hit))
        hit["forming"]["price"] = 120
        self.assertFalse(site_rules.in_zone(hit))

    def test_stock_suffix_still_excluded(self):
        self.assertTrue(TS.is_excluded("AAPLB"))
        self.assertFalse(TS.is_excluded("BTC"))
        self.assertFalse(TS.is_excluded("BNB"))


class PublishTests(unittest.TestCase):
    def test_public_tree_has_ciphertext_only(self):
        password = "unit-test-password"
        payload = {
            "tf": "1h", "generated_ts": 1_700_000_000, "runtime_s": 1.2, "symbols": 1,
            "errors": 0, "sources": ["mexc"],
            "hits": [{
                "pullback_entry": 10, "state": "RETEST_CONFIRMED_VOLUME",
                "anchor1_ms": 5, "breakout_level": 11,
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data")
            dist = os.path.join(tmp, "dist")
            publish_util.build_data_dir(data, [], fresh={"1h": payload}, password=password)
            text = open(os.path.join(data, "1h.json"), encoding="utf-8").read()
            self.assertNotIn("pullback_entry", text)
            self.assertNotIn("RETEST_CONFIRMED_VOLUME", text)
            back = crypto_payload.decrypt_json(json.load(open(os.path.join(data, "1h.json"))), password)
            self.assertEqual(back["hits"][0]["breakout_level"], 11)
            publish_util.assemble_site(dist, os.path.join(ROOT, "site"), data)
            publish_util.assert_public_safe(dist)
            bad = os.path.join(dist, "data", "leak.json")
            open(bad, "w").write('{"pullback_entry": 1}')
            with self.assertRaises(SystemExit):
                publish_util.assert_public_safe(dist)

    def test_http_400_does_not_lock_binance(self):
        orig = datasrc._get
        datasrc._down_until[0] = 0
        datasrc.status["binance_locked"] = False
        datasrc.BINANCE = "https://binance.example"

        def fake(base, path, params, tries=2):
            if "binance.example" in base:
                raise RuntimeError("HTTP 400: invalid symbol")
            return [[1_700_000_000_000, "1", "2", "0.5", "1", "9", 0, "0"]]

        datasrc._get = fake
        try:
            rows, src = datasrc.klines("NOPEUSDT", "1h", 2)
            self.assertEqual(src, "mexc")
            self.assertFalse(datasrc.status.get("binance_locked"))
            self.assertEqual(rows[0][4], 1.0)
        finally:
            datasrc._get = orig
            datasrc.BINANCE = "https://data-api.binance.vision/api/v3"
            datasrc._down_until[0] = 0
            datasrc.status["binance_locked"] = False

    def test_resample_drops_partial_leading_bucket(self):
        rows = [[5000, 1, 2, 1, 1, 1], [10000, 2, 4, 1, 3, 5], [20000, 3, 3, 3, 3, 1]]
        out = datasrc._resample(rows, 1, 10000, 0)
        self.assertEqual([r[0] for r in out], [10000, 20000])
        self.assertEqual(out[0][2], 4)


if __name__ == "__main__":
    unittest.main()
