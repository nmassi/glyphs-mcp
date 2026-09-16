import json
import urllib.error
import unittest
from unittest.mock import patch

import font_name_check


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self._body


class FontNameCheckTests(unittest.TestCase):
    def test_exact_match_is_collision(self):
        result = font_name_check.interpret_fontdata_response(
            "Plan",
            {"data": {"query": "Plan", "confidence": {
                "1.0": 2, "0.9": 3, "0.8": 8, "0.5": 373, "0.4": 162, "0.3": 13,
            }, "trademark": False}},
        )
        self.assertEqual(result["status"], "collision")
        self.assertEqual(result["matches"], {
            "exact": 2,
            "close": 11,
            "partial": 548,
            "confidence": {"1.0": 2, "0.9": 3, "0.8": 8, "0.5": 373, "0.4": 162, "0.3": 13},
        })

    def test_trademark_requires_review(self):
        result = font_name_check.interpret_fontdata_response(
            "Mark",
            {"data": {"query": "Mark", "confidence": {"1.0": 0}, "trademark": "MONOTYPE GMBH"}},
        )
        self.assertEqual(result["status"], "review")

    def test_unexpected_schema_is_error(self):
        result = font_name_check.interpret_fontdata_response("Broken", {"data": {}})
        self.assertEqual(result["error"]["type"], "invalid_response")

    @patch("font_name_check.urllib.request.urlopen")
    def test_normalizes_and_encodes_name(self, urlopen):
        urlopen.return_value = FakeResponse({
            "data": {"query": "New Name", "confidence": {"1.0": 0}, "trademark": False},
        })
        result = font_name_check.check_font_name("  New   Name  ")
        self.assertEqual(result["name"], "New Name")
        self.assertIn("q=New+Name", urlopen.call_args.args[0].full_url)

    @patch("font_name_check.urllib.request.urlopen")
    def test_network_failure_is_structured(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("offline")
        result = font_name_check.check_font_name("Candidate")
        self.assertEqual(result["error"]["type"], "network_error")

    def test_empty_name_is_rejected(self):
        self.assertEqual(font_name_check.check_font_name("   ")["error"]["type"], "invalid_name")


if __name__ == "__main__":
    unittest.main()
