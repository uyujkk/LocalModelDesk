import copy
import math
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from monitor_fixture import sample
from telemetry import GIB, fetch, normalize, number, rate


class TelemetryTests(unittest.TestCase):
    def test_two_cards_are_distinct_and_units_are_bytes(self):
        result = normalize(sample())
        self.assertEqual([gpu["index"] for gpu in result["gpus"]], [0, 1])
        self.assertAlmostEqual(result["vram_used"] / GIB, 21.9)
        self.assertEqual(result["gpu_power"], 323)
        self.assertEqual(result["gpu_util"], 68)
        self.assertEqual(result["context_used"], 26016)
        self.assertEqual(result["speed"], 96.8)

    def test_missing_metrics_stay_unknown(self):
        result = normalize({"live": {}})
        for field in ["speed", "gpu_util", "gpu_power", "cpu", "ram_used", "vram_used", "context_used"]:
            self.assertIsNone(result[field])
        self.assertEqual(result["gpus"], [])

    def test_incomplete_card_totals_not_reported_as_complete(self):
        data = sample()
        del data["hardware"]["gpus"][1]["power"]
        result = normalize(data)
        self.assertIsNone(result["gpu_power"])
        self.assertEqual(result["gpus"][0]["power"], 168)

    def test_aggregate_not_duplicated_for_dual_gpu(self):
        data = {"live": {}, "hardware_static": {"gpu_count": 2, "gpu_name": "unknown"}, "hardware": {"gpu_util": 90}}
        self.assertEqual(normalize(data)["gpus"], [])
        data["hardware_static"]["gpu_count"] = 1
        self.assertEqual(len(normalize(data)["gpus"]), 1)

    def test_speed_phase_and_single_token(self):
        data = sample()
        data["live"].update(state="reading", prefill_tok_s_mean=2400)
        self.assertEqual(normalize(data)["speed"], 2400)
        data["live"]["state"] = "idle"
        data["totals"] = {"output_tokens": 300, "decode_ms": 3000}
        self.assertEqual(normalize(data)["speed"], 100)
        self.assertIsNone(rate({"output_tokens": 1, "decode_ms": 0.5}))

    def test_booleans_nan_and_huge_values_rejected(self):
        for value in [True, False, float("nan"), float("inf"), -1, 10**1000]:
            self.assertIsNone(number(value))

    def test_versioned_fallback_fields(self):
        data = {"schema_version": 1, "model": {"context": 262144}, "rates": {"answer_tok_s": 80},
                "work": {"slots": [{"state": "decoding", "held_tokens": 1234}]}, "ram": {"used": GIB},
                "devices": [{"index": 1, "name": "GPU", "mem_used": GIB}]}
        result = normalize(data, "telemetry")
        self.assertEqual(result["context_used"], 1234)
        self.assertEqual(result["speed"], 80)
        self.assertIsNone(result["cpu"])
        data["schema_version"] = 2
        with self.assertRaises(ValueError):
            normalize(data, "telemetry")

    def test_fetch_404_fallback_is_read_only(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Type": "application/json"}
        response.read.return_value = b'{"schema_version":1}'
        opener = MagicMock()
        opener.open.side_effect = [urllib.error.HTTPError("url", 404, "missing", {}, None), response]
        with patch("urllib.request.build_opener", return_value=opener):
            self.assertEqual(fetch()["source"], "telemetry")
        calls = opener.open.call_args_list
        self.assertEqual(calls[0].args[0].get_method(), "GET")
        self.assertTrue(calls[1].args[0].full_url.endswith("/v1/telemetry"))

    def test_oversize_response_rejected(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Type": "application/json"}
        response.read.return_value = b" " * (2*1024*1024+1)
        opener = MagicMock()
        opener.open.return_value = response
        with patch("urllib.request.build_opener", return_value=opener), self.assertRaises(ValueError):
            fetch()


if __name__ == "__main__":
    unittest.main(verbosity=2)
