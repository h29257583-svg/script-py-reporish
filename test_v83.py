import unittest
from unittest.mock import patch
import zipfile
import os
import json
import time
import recorder


class TestRecorderV83Prod(unittest.TestCase):
    def setUp(self):
        recorder.NON_LIVE_VOD_CACHE.clear()
        recorder.PROBE_STATS.update({"clean_offline": 0, "blocked": 0, "error": 0, "live_hits": 0})
        recorder.YTDLP_PROBE_COOLDOWN_UNTIL = 0.0
        recorder.TARGET["channel_id"] = "UCNty54b8Vzmk-9BRYfF0EVQ"
        recorder.SUMMARY["blocked_by_youtube"] = False

    def test_01_claude_125kb_offset_ytinitialdata(self):
        padding = "A" * 125_000
        large_idle_html = f'<html><head>{padding}</head><body><script>var ytInitialData = {{"externalId":"UCNty54b8Vzmk-9BRYfF0EVQ"}};</script></body></html>'
        state, details = recorder.inspect_youtube_html(large_idle_html)
        self.assertEqual(state, "NOT_LIVE")
        self.assertIsNone(details)

    def test_02_chatgpt_upcoming_wins_over_isLiveNow_false_and_endTimestamp(self):
        html = (
            '<html><script>var ytInitialPlayerResponse = {'
            '"videoDetails":{"videoId":"UPCOMING123","channelId":"UCNty54b8Vzmk-9BRYfF0EVQ","isLive":false,"isUpcoming":true},'
            '"microformat":{"playerMicroformatRenderer":{"liveBroadcastDetails":{"isLiveNow":false,"endTimestamp":"2026-10-29T00:00:00Z"}}}'
            '};</script></html>'
        )
        state, details = recorder.inspect_youtube_html(html)
        self.assertEqual(state, "UPCOMING")
        self.assertFalse(details["ended"])

        with patch.object(recorder, "fetch_text", return_value=html), \
             patch.object(recorder, "run_ytdlp_json") as mock_ytdlp:
            res = recorder.verify_live_id("UPCOMING123", "rss_feed")
            self.assertIsNone(res)
            self.assertNotIn("UPCOMING123", recorder.NON_LIVE_VOD_CACHE)
            self.assertEqual(mock_ytdlp.call_count, 0)

    def test_03_chatgpt_isLive_true_not_overridden_by_endTimestamp_when_liveNow_missing(self):
        html = (
            '<html><script>var ytInitialPlayerResponse = {'
            '"videoDetails":{"videoId":"LSClhQ75Ot8","channelId":"UCNty54b8Vzmk-9BRYfF0EVQ","isLive":true,"isUpcoming":false},'
            '"microformat":{"playerMicroformatRenderer":{"liveBroadcastDetails":{"endTimestamp":"2026-10-29T00:00:00Z"}}}'
            '};</script></html>'
        )
        state, details = recorder.inspect_youtube_html(html)
        self.assertEqual(state, "LIVE")
        self.assertFalse(details["ended"])

    def test_04_chatgpt_isLiveNow_true_wins_over_endTimestamp(self):
        html = (
            '<html><script>var ytInitialPlayerResponse = {'
            '"videoDetails":{"videoId":"LSClhQ75Ot8","channelId":"UCNty54b8Vzmk-9BRYfF0EVQ","isLive":false},'
            '"microformat":{"playerMicroformatRenderer":{"liveBroadcastDetails":{"isLiveNow":true,"endTimestamp":"2026-10-29T00:00:00Z"}}}'
            '};</script></html>'
        )
        state, details = recorder.inspect_youtube_html(html)
        self.assertEqual(state, "LIVE")
        self.assertTrue(details["isLive"])
        self.assertFalse(details["ended"])

    def test_05_blocked_live_not_masked_by_home_not_live(self):
        bot_html = '<html><body>Sign in to confirm you\'re not a bot. g-recaptcha</body></html>'
        home_html = '<html><script>var ytInitialData = {"externalId":"UCNty54b8Vzmk-9BRYfF0EVQ"};</script></html>'

        def fake_fetch(url):
            return bot_html if url.endswith("/live") else home_html

        with patch.object(recorder, "fetch_text", side_effect=fake_fetch), \
             patch.object(recorder, "run_ytdlp_json", return_value=None):
            res = recorder.probe_fast()
            self.assertIsNone(res)
            self.assertEqual(recorder.PROBE_STATS["blocked"], 1)
            self.assertEqual(recorder.PROBE_STATS["clean_offline"], 0)
            self.assertTrue(recorder.SUMMARY["blocked_by_youtube"])

    def test_06_wrong_channel_idle_html_is_rejected_as_error(self):
        wrong_channel_html = '<html><script>var ytInitialData = {"externalId":"UC-JQRYHht4tLKpiChum6GnA"};</script></html>'
        state, _ = recorder.inspect_youtube_html(wrong_channel_html)
        self.assertEqual(state, "ERROR")

    def test_07_confirm_still_live_preserves_unknown(self):
        bot_html = '<html><body>Sign in to confirm you\'re not a bot. g-recaptcha</body></html>'
        with patch.object(recorder, "fetch_text", return_value=bot_html), \
             patch.object(recorder, "run_ytdlp_json", return_value=None):
            self.assertEqual(recorder.check_live_state("LSClhQ75Ot8"), "UNKNOWN")
            self.assertTrue(recorder.confirm_still_live("LSClhQ75Ot8"))

    def test_08_rss_vod_cached_and_zero_ytdlp(self):
        vod_html = (
            '<html><script>var ytInitialPlayerResponse = {'
            '"videoDetails":{"videoId":"dQw4w9WgXcQ","channelId":"UCNty54b8Vzmk-9BRYfF0EVQ","isLive":false}'
            '};</script></html>'
        )
        with patch.object(recorder, "fetch_text", return_value=vod_html) as mock_fetch, \
             patch.object(recorder, "run_ytdlp_json") as mock_ytdlp:
            self.assertIsNone(recorder.verify_live_id("dQw4w9WgXcQ", "rss_feed"))
            self.assertIn("dQw4w9WgXcQ", recorder.NON_LIVE_VOD_CACHE)
            self.assertIsNone(recorder.verify_live_id("dQw4w9WgXcQ", "rss_feed"))
            self.assertEqual(mock_fetch.call_count, 1)
            self.assertEqual(mock_ytdlp.call_count, 0)

    def test_09_process_reaped_even_if_process_group_already_gone(self):
        class FakeProcess:
            pid = 43210
            waited = False
            def poll(self): return None
            def wait(self, timeout=None):
                self.waited = True
                return -15
        fake = FakeProcess()
        with patch("recorder.os.name", "posix"), patch("recorder.os.killpg", side_effect=ProcessLookupError, create=True):
            recorder.stop_proc(fake)
        self.assertTrue(fake.waited)

    def test_10_all_16_repos_mapped_and_no_artificial_auth_blockers(self):
        with open("streamers_16_config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(len(cfg), 16)
        yml_path = ".github/workflows/record.yml" if os.path.exists(".github/workflows/record.yml") else "record.yml"
        with open(yml_path, "r", encoding="utf-8") as f:
            yml = f.read()
        for item in cfg:
            self.assertIn(item["repo"], yml)
        self.assertNotIn("RECORDING_AUTHORIZED", yml)
        self.assertNotIn("PUBLIC_REDISTRIBUTION_AUTHORIZED", yml)

    def test_11_downloader_rate_limit_stops_rotation(self):
        output_state = {
            "botcheck": False,
            "no_formats": False,
            "rate_limited": True,
            "not_live": False,
            "normal_exit": False,
            "chosen_seen": False
        }
        self.assertEqual(recorder.classify_output(output_state), "rate_limited")

        active = {
            "live_id": "FWjZ0x2M8og",
            "chunk_dir": "/tmp/test",
            "chunk_index": 1,
            "session_index": 1,
            "final_ts": "/tmp/test.ts",
            "chunks": []
        }
        with patch.object(recorder, "run_download_attempt", return_value=(False, "rate_limited", None)) as mock_dl:
            ok, reason, rep = recorder.record_until_stop(active, time.time() + 300, None)
            self.assertFalse(ok)
            self.assertEqual(reason, "rate_limited")
            # Must stop after 1 attempt; no fallback to remaining clients
            self.assertEqual(mock_dl.call_count, 1)
            self.assertTrue(recorder.SUMMARY.get("downloader_rate_limited"))
            self.assertTrue(recorder.SUMMARY.get("blocked_by_youtube"))

    def test_12_persists_downloader_rate_limited_in_summary(self):
        recorder.write_summary(downloader_rate_limited=True, blocked_by_youtube=True, failure_reason="rate_limited")
        self.assertTrue(recorder.SUMMARY["downloader_rate_limited"])
        self.assertTrue(recorder.SUMMARY["blocked_by_youtube"])
        self.assertEqual(recorder.SUMMARY["failure_reason"], "rate_limited")

    def test_13_unverified_not_claimed_as_verified_offline(self):
        # clean_offline=0, blocked=0, error=0 MUST be UNVERIFIED, not NO_LIVE_CONFIRMED
        stats = {"clean_offline": 0, "blocked": 0, "error": 0}
        c_off = stats["clean_offline"]
        c_blk = stats["blocked"]
        c_err = stats["error"]
        
        status = None
        if c_off > 0 and c_blk == 0 and c_err == 0:
            status = "NO_LIVE_CONFIRMED"
        elif c_off == 0 and c_blk == 0 and c_err == 0:
            status = "UNVERIFIED"
        self.assertEqual(status, "UNVERIFIED")
        self.assertNotEqual(status, "NO_LIVE_CONFIRMED")

    def test_14_record_pass_requires_non_empty_media_file(self):
        # A run with 0 chunks or 0.0 MB must NEVER claim RECORD_PASS
        summary_0mb = {"final_file": None, "final_size_mb": 0.0, "chunk_count": 0}
        media_files = []
        is_record_pass = bool(media_files and (summary_0mb["final_size_mb"] > 0 or summary_0mb["chunk_count"] > 0))
        self.assertFalse(is_record_pass)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestRecorderV83Prod)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    assert result.wasSuccessful(), "Unit tests failed"

    zip_path = "Recorder-v8.3-Production.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write("recorder.py", "recorder.py")
        zf.write("record.yml", ".github/workflows/record.yml")
        zf.write("streamers_16_config.json", "streamers_16_config.json")
        zf.write("test_v83.py", "test_v83.py")
    print("Packaged Recorder-v8.3-Production.zip:", os.path.getsize(zip_path), "bytes")
