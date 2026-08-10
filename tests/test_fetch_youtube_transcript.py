#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPT_PATH = SKILL_DIR / "scripts" / "fetch_youtube_transcript.py"
SKILL_PATH = SKILL_DIR / "SKILL.md"
README_PATH = SKILL_DIR / "README.md"

spec = importlib.util.spec_from_file_location("fetch_youtube_transcript", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def provider_result(provider: str, text: str = "hello") -> dict:
    segments = [
        {
            "text": text,
            "start": 0.0,
            "duration": 2.0,
            "end": 2.0,
            "timestamp": "00:00",
        }
    ]
    return {
        "provider": provider,
        "video_id": "abc123def45",
        "source_url": "https://www.youtube.com/watch?v=abc123def45",
        "title": "Captions",
        "language_code": "en",
        "is_generated": False,
        "used_language_fallback": False,
        "translated_to": None,
        "source_format": "youtube_transcript_api" if provider == "api" else "vtt",
        "segment_count": 1,
        "duration_seconds": 2.0,
        "chapters": [],
        "text": text,
        "segments": segments,
        "notes": [],
        "raw_metadata": {},
    }


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

    def test_pipeline_prefers_api_without_calling_yt_dlp(self) -> None:
        with patch.object(module, "fetch_title", return_value="Captions"):
            with patch.object(module, "fetch_via_api", return_value=provider_result("api")):
                with patch.object(module, "fetch_via_yt_dlp") as fallback:
                    payload = module.run_caption_pipeline(
                        video="https://www.youtube.com/watch?v=abc123def45",
                        requested_languages=["en"],
                        translate_to=None,
                        preserve_formatting=False,
                        chunk_seconds=90.0,
                    )

        fallback.assert_not_called()
        self.assertEqual("caption.v2", payload["schema_version"])
        self.assertEqual("api", payload["selected_result"]["provider"])
        self.assertEqual(["api", "yt-dlp"], payload["requested"]["providers"])
        self.assertEqual(["api"], [item["provider"] for item in payload["attempts"]])
        self.assertEqual("hello", payload["selected_result"]["text"])

    def test_pipeline_falls_back_to_yt_dlp_after_api_error(self) -> None:
        with patch.object(module, "fetch_title", return_value="Captions"):
            with patch.object(module, "fetch_via_api", side_effect=RuntimeError("api blocked")):
                with patch.object(
                    module,
                    "fetch_via_yt_dlp",
                    return_value=provider_result("yt-dlp"),
                ) as fallback:
                    payload = module.run_caption_pipeline(
                        video="abc123def45",
                        requested_languages=["en"],
                        translate_to=None,
                        preserve_formatting=False,
                        chunk_seconds=90.0,
                    )

        fallback.assert_called_once()
        self.assertEqual("yt-dlp", payload["selected_result"]["provider"])
        self.assertEqual(
            ["failed", "success"],
            [item["status"] for item in payload["attempts"]],
        )
        self.assertEqual(
            ["api", "yt-dlp"],
            [item["provider"] for item in payload["attempts"]],
        )
        self.assertEqual("yt-dlp", payload["chunks"][0]["source_provider"])

    def test_pipeline_falls_back_after_empty_api_result(self) -> None:
        empty = {"provider": "api", "segments": [], "text": ""}
        with patch.object(module, "fetch_title", return_value=None):
            with patch.object(module, "fetch_via_api", return_value=empty):
                with patch.object(
                    module,
                    "fetch_via_yt_dlp",
                    return_value=provider_result("yt-dlp"),
                ):
                    payload = module.run_caption_pipeline(
                        video="abc123def45",
                        requested_languages=["en"],
                        translate_to=None,
                        preserve_formatting=False,
                        chunk_seconds=90.0,
                    )

        self.assertEqual("yt-dlp", payload["selection"]["provider"])
        self.assertEqual("failed", payload["attempts"][0]["status"])

    def test_pipeline_reports_both_provider_failures(self) -> None:
        with patch.object(module, "fetch_title", return_value=None):
            with patch.object(module, "fetch_via_api", side_effect=RuntimeError("api blocked")):
                with patch.object(
                    module,
                    "fetch_via_yt_dlp",
                    side_effect=RuntimeError("yt-dlp blocked"),
                ):
                    with self.assertRaisesRegex(
                        module.UserError,
                        "api.*api blocked.*yt-dlp.*yt-dlp blocked",
                    ):
                        module.run_caption_pipeline(
                            video="abc123def45",
                            requested_languages=["en"],
                            translate_to=None,
                            preserve_formatting=False,
                            chunk_seconds=90.0,
                        )

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

    def test_choose_yt_dlp_caption_prefers_requested_manual_track(self) -> None:
        info = {
            "subtitles": {
                "en": [{"ext": "vtt", "url": "https://example.test/manual"}],
            },
            "automatic_captions": {
                "en": [{"ext": "vtt", "url": "https://example.test/auto"}],
            },
        }
        track, generated, language_fallback = module.choose_yt_dlp_caption(info, ["en"])

        self.assertEqual("https://example.test/manual", track["url"])
        self.assertEqual("en", track["language_code"])
        self.assertFalse(generated)
        self.assertFalse(language_fallback)

    def test_choose_yt_dlp_caption_falls_back_to_other_manual_language(self) -> None:
        info = {
            "subtitles": {
                "fr": [{"ext": "vtt", "url": "https://example.test/manual-fr"}],
            },
            "automatic_captions": {
                "en": [{"ext": "vtt", "url": "https://example.test/auto-en"}],
            },
        }
        track, generated, language_fallback = module.choose_yt_dlp_caption(info, ["en"])

        self.assertEqual("fr", track["language_code"])
        self.assertFalse(generated)
        self.assertTrue(language_fallback)

    def test_choose_yt_dlp_caption_uses_requested_generated_track(self) -> None:
        info = {
            "subtitles": {},
            "automatic_captions": {
                "en": [{"ext": "vtt", "url": "https://example.test/auto-en"}],
            },
        }
        track, generated, language_fallback = module.choose_yt_dlp_caption(info, ["en"])

        self.assertEqual("en", track["language_code"])
        self.assertTrue(generated)
        self.assertFalse(language_fallback)

    def test_choose_yt_dlp_caption_rejects_missing_tracks(self) -> None:
        with self.assertRaisesRegex(
            module.UserError,
            "No manual or automatic captions are available from yt-dlp",
        ):
            module.choose_yt_dlp_caption(
                {"subtitles": {}, "automatic_captions": {}},
                ["en"],
            )

    def test_parse_vtt_normalizes_timestamped_cues(self) -> None:
        raw_vtt = """WEBVTT

00:00:00.000 --> 00:00:02.500 align:start
<c>Hello</c> &amp; welcome

00:00:02.500 --> 00:00:05.000
Next line
"""
        segments = module.parse_vtt(raw_vtt)

        self.assertEqual(2, len(segments))
        self.assertEqual("Hello & welcome", segments[0]["text"])
        self.assertEqual(0.0, segments[0]["start"])
        self.assertEqual(2.5, segments[0]["duration"])

    def test_yt_dlp_provider_uses_subtitle_only_options(self) -> None:
        captured = {}

        class FakeYoutubeDL:
            def __init__(self, options):
                captured["options"] = options

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def extract_info(self, source_url, download):
                captured["source_url"] = source_url
                captured["download"] = download
                return {
                    "title": "Captions",
                    "duration": 5.0,
                    "subtitles": {
                        "en": [
                            {
                                "ext": "vtt",
                                "url": "https://example.test/manual",
                                "name": "English",
                            }
                        ]
                    },
                    "automatic_captions": {},
                }

        class FakeYtDlp:
            YoutubeDL = FakeYoutubeDL

        with patch.object(module, "ensure_dependency", return_value=FakeYtDlp):
            with patch.object(
                module,
                "download_caption_text",
                return_value="WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello\n",
            ):
                result = module.fetch_via_yt_dlp(
                    video_id="abc123def45",
                    source_url="https://www.youtube.com/watch?v=abc123def45",
                    title=None,
                    requested_languages=["en"],
                    translate_to="zh-Hans",
                    preserve_formatting=False,
                )

        options = captured["options"]
        self.assertIs(options["skip_download"], True)
        self.assertIs(options["writesubtitles"], True)
        self.assertIs(options["writeautomaticsub"], True)
        self.assertNotIn("format", options)
        self.assertNotIn("outtmpl", options)
        self.assertNotIn("postprocessors", options)
        self.assertIs(captured["download"], False)
        self.assertEqual("yt-dlp", result["provider"])
        self.assertIsNone(result["translated_to"])

    def test_caption_module_contains_no_browser_media_or_asr_caption_path(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8").lower()
        for forbidden in (
            "playwright",
            "fetch_via_browser",
            "computer-use",
            "download_audio",
            "faster_whisper",
            "fetch_via_asr",
            "cookies-from-browser",
            "browser_cookie",
            "ffmpegextractaudio",
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

    def test_skill_docs_define_only_api_then_yt_dlp_caption_acquisition(self) -> None:
        source = SKILL_PATH.read_text(encoding="utf-8").lower()
        self.assertIn("api -> yt-dlp", source)
        self.assertIn("skip_download=true", source)
        self.assertIn("computer-use", source)
        self.assertIn("browser cookies", source)
        self.assertIn("speech recognition", source)

    def test_readme_is_bilingual_and_usage_only(self) -> None:
        source = README_PATH.read_text(encoding="utf-8")
        lowered = source.lower()

        self.assertIn("[中文](#中文)", source)
        self.assertIn("[English](#english)", source)
        self.assertIn("## 中文", source)
        self.assertIn("## English", source)
        self.assertIn("回复语言", source)
        self.assertIn("response language", lowered)
        self.assertGreaterEqual(source.count("$youtube-caption-summary"), 6)

        for forbidden in (
            "youtube-transcript-api",
            "yt-dlp",
            "skip_download",
            "automatic_captions",
            "caption.v2",
            "scripts/",
            "ffmpeg",
            "cookies-from-browser",
            "computer-use",
            "speech recognition",
            "provider",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_render_chunks_reads_v2_artifact_chunks(self) -> None:
        payload = {"chunks": [{"text": "chunk"}], "selected_result": {"text": "all", "segments": []}}

        rendered = module.render_output(payload, "chunks")

        self.assertIn('"text": "chunk"', rendered)


if __name__ == "__main__":
    unittest.main()
