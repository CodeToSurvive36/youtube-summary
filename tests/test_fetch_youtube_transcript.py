#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPT_PATH = SKILL_DIR / "scripts" / "fetch_youtube_transcript.py"
FIXTURE_PATH = TESTS_DIR / "fixtures" / "browser_transcript_panel.txt"

spec = importlib.util.spec_from_file_location("fetch_youtube_transcript", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class BrowserTranscriptParsingTests(unittest.TestCase):
    def test_extract_transcript_block_keeps_panel_only(self) -> None:
        body_text = "Header\nNoise\n" + FIXTURE_PATH.read_text(encoding="utf-8") + "\nSuggested videos"
        block = module.extract_transcript_block(body_text)
        self.assertIn("Chapter 1: Intro", block)
        self.assertNotIn("Suggested videos", block)

    def test_parse_browser_transcript_block_returns_segments_and_chapters(self) -> None:
        transcript_block = FIXTURE_PATH.read_text(encoding="utf-8")
        segments, chapters = module.parse_browser_transcript_block(transcript_block)

        self.assertEqual(2, len(chapters))
        self.assertEqual("Intro", chapters[0]["title"])
        self.assertEqual("00:00", chapters[0]["timestamp"])
        self.assertEqual("Main Topic", chapters[1]["title"])
        self.assertEqual(4, len(segments))
        self.assertEqual("Opening thought from the transcript panel.", segments[0]["text"])
        self.assertEqual(0.0, segments[0]["start"])
        self.assertEqual("Intro", segments[0]["chapter"])
        self.assertEqual("Main analysis starts here.", segments[2]["text"])
        self.assertEqual(21.0, segments[2]["start"])
        self.assertEqual("Main Topic", segments[2]["chapter"])

    def test_finalize_segments_preserves_existing_duration(self) -> None:
        segments = [
            {"text": "a", "start": 1.0, "duration": 2.5, "end": 3.5, "timestamp": "00:01"},
            {"text": "b", "start": 4.0, "timestamp": "00:04"},
        ]
        finalized = module.finalize_segments(segments)
        self.assertEqual(2.5, finalized[0]["duration"])
        self.assertEqual(3.5, finalized[0]["end"])
        self.assertEqual(0.0, finalized[1]["duration"])
        self.assertEqual(4.0, finalized[1]["end"])


if __name__ == "__main__":
    unittest.main()
