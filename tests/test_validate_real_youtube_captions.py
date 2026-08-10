#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPT_PATH = SKILL_DIR / "scripts" / "validate_real_youtube_captions.py"

spec = importlib.util.spec_from_file_location("validate_real_youtube_captions", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


GROUPS = (
    "english_manual",
    "non_english_manual",
    "generated",
    "long",
    "short",
)


def build_manifest() -> dict:
    entries = []
    index = 0
    for group in GROUPS:
        for _ in range(5):
            video_id = f"v{index:010d}"
            entries.append(
                {
                    "video_id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "group": group,
                    "page_caption_confirmed": True,
                    "page_audit_method": "youtube_watch_page",
                    "page_audited_at": "2026-08-09T00:00:00+08:00",
                }
            )
            index += 1
    return {"schema_version": "youtube-caption-validation.v1", "entries": entries}


class ManifestValidationTests(unittest.TestCase):
    def test_manifest_requires_25_unique_videos_and_five_per_group(self) -> None:
        entries = module.validate_manifest(build_manifest())
        self.assertEqual(25, len(entries))
        self.assertEqual(25, len({entry["video_id"] for entry in entries}))

    def test_manifest_rejects_duplicate_video_ids(self) -> None:
        manifest = build_manifest()
        manifest["entries"][1]["video_id"] = manifest["entries"][0]["video_id"]
        manifest["entries"][1]["url"] = manifest["entries"][0]["url"]
        with self.assertRaisesRegex(module.UserError, "unique"):
            module.validate_manifest(manifest)

    def test_manifest_rejects_wrong_group_count(self) -> None:
        manifest = build_manifest()
        manifest["entries"][0]["group"] = "short"
        with self.assertRaisesRegex(module.UserError, "exactly 5"):
            module.validate_manifest(manifest)

    def test_manifest_requires_page_caption_confirmation(self) -> None:
        manifest = build_manifest()
        manifest["entries"][0]["page_caption_confirmed"] = False
        with self.assertRaisesRegex(module.UserError, "page-caption-confirmed"):
            module.validate_manifest(manifest)

    def test_validation_aggregates_direct_fetch_results(self) -> None:
        manifest = build_manifest()

        def fake_fetcher(**kwargs):
            video_id = module.fetch_module.extract_video_id(kwargs["video"])
            entry = next(item for item in manifest["entries"] if item["video_id"] == video_id)
            group = entry["group"]
            language = "fr" if group == "non_english_manual" else "en"
            is_generated = group == "generated"
            text = "x" * (20_001 if group == "long" else 500)
            duration = 240.0 if group == "short" else 600.0
            return {
                "schema_version": "caption.v2",
                "video": {"video_id": video_id, "duration_seconds": duration},
                "selected_result": {
                    "provider": "api",
                    "language_code": language,
                    "is_generated": is_generated,
                    "text": text,
                    "segment_count": 2,
                    "duration_seconds": duration,
                },
            }

        results = module.run_validation(manifest, fetcher=fake_fetcher)
        self.assertEqual(25, results["summary"]["passed"])
        self.assertEqual(0, results["summary"]["failed"])
        self.assertEqual("api", results["results"][0]["provider"])

    def test_category_mismatch_is_a_failed_result(self) -> None:
        manifest = build_manifest()

        def fake_fetcher(**kwargs):
            video_id = module.fetch_module.extract_video_id(kwargs["video"])
            return {
                "schema_version": "caption.v2",
                "video": {"video_id": video_id, "duration_seconds": 600.0},
                "selected_result": {
                    "provider": "api",
                    "language_code": "en",
                    "is_generated": False,
                    "text": "short",
                    "segment_count": 1,
                    "duration_seconds": 600.0,
                },
            }

        results = module.run_validation(manifest, fetcher=fake_fetcher)
        self.assertGreater(results["summary"]["failed"], 0)
        self.assertFalse(results["summary"]["all_passed"])

    def test_stop_on_failure_prevents_later_network_requests(self) -> None:
        manifest = build_manifest()
        calls = []

        def failing_fetcher(**kwargs):
            calls.append(kwargs["video"])
            raise RuntimeError("rate limited")

        results = module.run_validation(
            manifest,
            fetcher=failing_fetcher,
            stop_on_failure=True,
        )
        self.assertEqual(1, len(calls))
        self.assertEqual(1, results["summary"]["attempted"])
        self.assertEqual(24, results["summary"]["not_attempted"])

    def test_resume_reuses_only_previously_passed_direct_results(self) -> None:
        manifest = build_manifest()
        calls = []

        def fake_fetcher(**kwargs):
            calls.append(kwargs["video"])
            video_id = module.fetch_module.extract_video_id(kwargs["video"])
            entry = next(item for item in manifest["entries"] if item["video_id"] == video_id)
            group = entry["group"]
            return {
                "schema_version": "caption.v2",
                "video": {"video_id": video_id, "duration_seconds": 240.0},
                "selected_result": {
                    "provider": "api",
                    "language_code": "fr" if group == "non_english_manual" else "en",
                    "is_generated": group == "generated",
                    "text": "x" * (20_001 if group == "long" else 500),
                    "segment_count": 2,
                    "duration_seconds": 240.0,
                },
            }

        first = module.run_validation(manifest, fetcher=fake_fetcher)
        calls.clear()
        resumed = module.run_validation(
            manifest,
            fetcher=fake_fetcher,
            existing_results=[first["results"][0]],
        )
        self.assertEqual(24, len(calls))
        self.assertEqual(25, resumed["summary"]["passed"])


if __name__ == "__main__":
    unittest.main()
