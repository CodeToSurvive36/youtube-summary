#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPT_PATH = SKILL_DIR / "scripts" / "youtube_to_chinese_report.py"
SCHEMA_PATH = SKILL_DIR / "scripts" / "chinese_report.schema.json"

spec = importlib.util.spec_from_file_location("youtube_to_chinese_report", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class ReportLanguageTests(unittest.TestCase):
    def test_prompt_uses_requested_response_language_and_neutral_summary_field(self) -> None:
        prompt = module.build_codex_prompt(Path("/tmp/caption.json"), "Japanese")
        self.assertIn("Japanese", prompt)
        self.assertIn("`summary`", prompt)
        self.assertNotIn("chinese_summary", prompt)

    def test_renderer_reads_summary_and_uses_requested_language_labels(self) -> None:
        report = {"summary": "A grounded summary.", "mentioned_items": ["YouTube"]}
        rendered = module.render_markdown_report(report, "English")
        self.assertEqual("Summary\nA grounded summary.\n\nMentioned items\n- YouTube\n", rendered)

    def test_fetch_wrapper_accepts_yt_dlp_result_without_provider_controls(self) -> None:
        args = SimpleNamespace(video="abc123def45", langs="en")
        payload = {
            "schema_version": "caption.v2",
            "selected_result": {"provider": "yt-dlp", "text": "captions"},
        }
        with tempfile.TemporaryDirectory() as temp_name:
            output = Path(temp_name) / "caption.json"

            def fake_run(command, **kwargs):
                del kwargs
                output.write_text(json.dumps(payload), encoding="utf-8")
                self.assertNotIn("--strategy", command)
                self.assertNotIn("--providers", command)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(module.subprocess, "run", side_effect=fake_run):
                result = module.run_fetch_step(args, output)
        self.assertEqual(payload, result)
        self.assertEqual("yt-dlp", result["selected_result"]["provider"])

    def test_schema_requires_neutral_summary_field(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertIn("summary", schema["required"])
        self.assertNotIn("chinese_summary", schema["properties"])


if __name__ == "__main__":
    unittest.main()
