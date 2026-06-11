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


class CaptionV2PipelineTests(unittest.TestCase):
    def test_choose_yt_dlp_caption_prefers_manual_requested_language(self) -> None:
        info = {
            "subtitles": {
                "en": [{"ext": "vtt", "url": "https://example.test/manual.vtt"}],
            },
            "automatic_captions": {
                "en": [{"ext": "vtt", "url": "https://example.test/auto.vtt"}],
            },
        }

        track, is_generated, used_fallback = module.choose_yt_dlp_caption(info, ["en"])

        self.assertFalse(is_generated)
        self.assertFalse(used_fallback)
        self.assertEqual("https://example.test/manual.vtt", track["url"])
        self.assertEqual("en", track["language_code"])

    def test_choose_yt_dlp_caption_uses_automatic_when_manual_missing(self) -> None:
        info = {
            "subtitles": {},
            "automatic_captions": {
                "en": [{"ext": "vtt", "url": "https://example.test/auto.vtt"}],
            },
        }

        track, is_generated, used_fallback = module.choose_yt_dlp_caption(info, ["en"])

        self.assertTrue(is_generated)
        self.assertFalse(used_fallback)
        self.assertEqual("https://example.test/auto.vtt", track["url"])

    def test_choose_yt_dlp_caption_records_language_fallback(self) -> None:
        info = {
            "subtitles": {
                "fr": [{"ext": "vtt", "url": "https://example.test/fr.vtt"}],
            },
            "automatic_captions": {},
        }

        track, is_generated, used_fallback = module.choose_yt_dlp_caption(info, ["en"])

        self.assertFalse(is_generated)
        self.assertTrue(used_fallback)
        self.assertEqual("fr", track["language_code"])

    def test_parse_vtt_cleans_tags_and_deduplicates_adjacent_text(self) -> None:
        vtt = """WEBVTT

00:00:00.000 --> 00:00:02.000
<c>Hello</c> &amp; welcome

00:00:02.000 --> 00:00:04.000
<00:00:02.500>Hello</00:00:02.500> &amp; welcome

00:00:04.000 --> 00:00:05.500
Next line
"""

        segments = module.parse_vtt(vtt)

        self.assertEqual(2, len(segments))
        self.assertEqual("Hello & welcome", segments[0]["text"])
        self.assertEqual(0.0, segments[0]["start"])
        self.assertEqual(2.0, segments[0]["duration"])
        self.assertEqual("Next line", segments[1]["text"])

    def test_build_chunks_uses_time_windows_and_skips_empty_text(self) -> None:
        segments = [
            {"text": "first", "start": 0.0, "end": 10.0, "duration": 10.0},
            {"text": "", "start": 20.0, "end": 21.0, "duration": 1.0},
            {"text": "second", "start": 95.0, "end": 100.0, "duration": 5.0},
            {"text": "third", "start": 100.0, "end": 105.0, "duration": 5.0},
        ]

        chunks = module.build_chunks(segments, 90.0, "yt-dlp")

        self.assertEqual(2, len(chunks))
        self.assertEqual(0.0, chunks[0]["start"])
        self.assertEqual(1, chunks[0]["segment_count"])
        self.assertEqual("first", chunks[0]["text"])
        self.assertEqual(90.0, chunks[1]["start"])
        self.assertEqual(2, chunks[1]["segment_count"])
        self.assertEqual("second\nthird", chunks[1]["text"])
        self.assertEqual("yt-dlp", chunks[1]["source_provider"])

    def test_render_chunks_reads_v2_artifact_chunks(self) -> None:
        payload = {"chunks": [{"text": "chunk"}], "selected_result": {"text": "all", "segments": []}}

        rendered = module.render_output(payload, "chunks")

        self.assertIn('"text": "chunk"', rendered)


if __name__ == "__main__":
    unittest.main()
