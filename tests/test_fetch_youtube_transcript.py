#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPT_PATH = SKILL_DIR / "scripts" / "fetch_youtube_transcript.py"

spec = importlib.util.spec_from_file_location("fetch_youtube_transcript", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class CaptionV2PipelineTests(unittest.TestCase):
    class _Errors:
        class NoTranscriptFound(Exception):
            pass

    class _Transcript:
        def __init__(self, language_code: str, is_generated: bool):
            self.language = language_code
            self.language_code = language_code
            self.is_generated = is_generated
            self.is_translatable = True

    class _TranscriptList(list):
        def __init__(self, items, found=None):
            super().__init__(items)
            self.found = found

        def find_transcript(self, languages):
            if self.found is None:
                raise CaptionV2PipelineTests._Errors.NoTranscriptFound()
            return self.found

    def tearDown(self) -> None:
        module._DIRECT_API_INSTANCE = None

    def test_direct_api_instance_is_reused_within_one_process(self) -> None:
        instances = []

        class FakeYta:
            class YouTubeTranscriptApi:
                def __init__(self):
                    instances.append(self)

        first = module.get_direct_api(FakeYta)
        second = module.get_direct_api(FakeYta)
        self.assertIs(first, second)
        self.assertEqual(1, len(instances))

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

    def test_pipeline_fetches_captions_through_the_single_direct_api(self) -> None:
        provider_result = {
            "provider": "api",
            "video_id": "abc123def45",
            "source_url": "https://www.youtube.com/watch?v=abc123def45",
            "title": "Direct captions",
            "language_code": "en",
            "is_generated": False,
            "used_language_fallback": False,
            "translated_to": None,
            "source_format": "youtube_transcript_api",
            "segment_count": 1,
            "duration_seconds": 2.0,
            "chapters": [],
            "text": "hello",
            "segments": [
                {
                    "text": "hello",
                    "start": 0.0,
                    "duration": 2.0,
                    "end": 2.0,
                    "timestamp": "00:00",
                }
            ],
            "notes": ["Caption was extracted with youtube-transcript-api."],
            "raw_metadata": {},
        }

        with patch.object(module, "fetch_title", return_value="Direct captions"):
            with patch.object(module, "fetch_via_api", return_value=provider_result):
                payload = module.run_caption_pipeline(
                    video="https://www.youtube.com/watch?v=abc123def45",
                    requested_languages=["en"],
                    translate_to=None,
                    preserve_formatting=False,
                    chunk_seconds=90.0,
                )

        self.assertEqual("caption.v2", payload["schema_version"])
        self.assertEqual("api", payload["selected_result"]["provider"])
        self.assertEqual(["api"], payload["requested"]["providers"])
        self.assertEqual("hello", payload["selected_result"]["text"])

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

    def test_choose_best_transcript_prefers_requested_language(self) -> None:
        requested = self._Transcript("fr", False)
        tracks = self._TranscriptList([requested, self._Transcript("en", False)], found=requested)
        chosen, used_fallback = module.choose_best_transcript(
            tracks, ["fr", "en"], self._Errors
        )
        self.assertIs(chosen, requested)
        self.assertFalse(used_fallback)

    def test_choose_best_transcript_falls_back_to_manual_language(self) -> None:
        manual = self._Transcript("de", False)
        generated = self._Transcript("en", True)
        tracks = self._TranscriptList([generated, manual])
        chosen, used_fallback = module.choose_best_transcript(tracks, ["fr"], self._Errors)
        self.assertIs(chosen, manual)
        self.assertTrue(used_fallback)

    def test_choose_best_transcript_uses_generated_when_no_manual_exists(self) -> None:
        generated = self._Transcript("ja", True)
        tracks = self._TranscriptList([generated])
        chosen, used_fallback = module.choose_best_transcript(tracks, ["en"], self._Errors)
        self.assertIs(chosen, generated)
        self.assertTrue(used_fallback)

    def test_pipeline_rejects_empty_caption_result(self) -> None:
        empty = {
            "provider": "api",
            "video_id": "abc123def45",
            "source_url": "https://www.youtube.com/watch?v=abc123def45",
            "title": "Empty",
            "segments": [],
            "text": "",
        }
        with patch.object(module, "fetch_title", return_value="Empty"):
            with patch.object(module, "fetch_via_api", return_value=empty):
                with self.assertRaisesRegex(module.UserError, "no usable caption text"):
                    module.run_caption_pipeline(
                        video="abc123def45",
                        requested_languages=["en"],
                        translate_to=None,
                        preserve_formatting=False,
                        chunk_seconds=90.0,
                    )

    def test_api_error_is_mapped_to_stable_user_error(self) -> None:
        with patch.object(module, "fetch_title", return_value=None):
            with patch.object(module, "fetch_via_api", side_effect=type("TranscriptsDisabled", (Exception,), {})()):
                with self.assertRaisesRegex(module.UserError, "no captions enabled"):
                    module.run_caption_pipeline(
                        video="abc123def45",
                        requested_languages=["en"],
                        translate_to=None,
                        preserve_formatting=False,
                        chunk_seconds=90.0,
                    )

    def test_caption_module_contains_no_alternate_caption_paths(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8").lower()
        for forbidden in (
            "playwright",
            "npx",
            "fetch_via_browser",
            "yt_dlp",
            "yt-dlp",
            "download_audio",
            "faster_whisper",
            "fetch_via_asr",
            "cookies-from-browser",
            "browser_cookie",
        ):
            self.assertNotIn(forbidden, source)

    def test_help_does_not_expose_removed_provider_options(self) -> None:
        completed = module.subprocess.run(
            [module.sys.executable, str(SCRIPT_PATH), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode)
        self.assertNotIn("--strategy", completed.stdout)
        self.assertNotIn("--providers", completed.stdout)
        self.assertNotIn("--asr-model", completed.stdout)

    def test_render_chunks_reads_v2_artifact_chunks(self) -> None:
        payload = {"chunks": [{"text": "chunk"}], "selected_result": {"text": "all", "segments": []}}

        rendered = module.render_output(payload, "chunks")

        self.assertIn('"text": "chunk"', rendered)


if __name__ == "__main__":
    unittest.main()
