#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPT_PATH = SKILL_DIR / "scripts" / "build_multimodal_segments.py"

spec = importlib.util.spec_from_file_location("build_multimodal_segments", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class MultimodalSegmentTests(unittest.TestCase):
    def test_build_segments_keeps_transcript_only_and_frame_only_windows(self) -> None:
        transcript = {
            "video_id": "abc123",
            "source_url": "https://example.test/watch",
            "duration_seconds": 130.0,
            "segments": [
                {"start": 10.0, "end": 15.0, "text": "intro"},
                {"start": 70.0, "end": 75.0, "text": "middle"},
            ],
        }
        frames = {
            "duration_seconds": 130.0,
            "frames": [
                {"start": 20.0, "timestamp": "00:20", "path": "/tmp/a.jpg", "source": "interval", "kept": True},
                {
                    "start": 125.0,
                    "timestamp": "02:05",
                    "path": "/tmp/b.jpg",
                    "source": "scene",
                    "scene_score": 0.72,
                    "kept": True,
                },
                {"start": 126.0, "timestamp": "02:06", "path": "/tmp/c.jpg", "source": "scene", "kept": False},
            ],
        }

        payload = module.build_segments(transcript, frames, segment_seconds=60.0)

        self.assertEqual("abc123", payload["video_id"])
        self.assertEqual(3, len(payload["segments"]))
        self.assertEqual("intro", payload["segments"][0]["transcript_text"])
        self.assertEqual(1, payload["segments"][0]["frame_count"])
        self.assertEqual("middle", payload["segments"][1]["transcript_text"])
        self.assertEqual(0, payload["segments"][1]["frame_count"])
        self.assertEqual("", payload["segments"][2]["transcript_text"])
        self.assertEqual(1, payload["segments"][2]["frame_count"])
        self.assertEqual("/tmp/b.jpg", payload["segments"][2]["frames"][0]["path"])
        self.assertEqual(0.72, payload["segments"][2]["frames"][0]["scene_score"])

    def test_build_segments_accepts_caption_v2_payload(self) -> None:
        transcript = {
            "schema_version": "caption.v2",
            "video": {
                "video_id": "abc123",
                "source_url": "https://example.test/watch",
                "duration_seconds": 90.0,
            },
            "selected_result": {
                "duration_seconds": 90.0,
                "segments": [
                    {"start": 5.0, "end": 10.0, "text": "hello"},
                    {"start": 65.0, "end": 70.0, "text": "world"},
                ],
            },
        }
        frames = {
            "duration_seconds": 90.0,
            "frames": [
                {"start": 15.0, "timestamp": "00:15", "path": "/tmp/a.jpg", "source": "interval", "kept": True},
            ],
        }

        payload = module.build_segments(transcript, frames, segment_seconds=60.0)

        self.assertEqual("abc123", payload["video_id"])
        self.assertEqual("https://example.test/watch", payload["source_url"])
        self.assertEqual("hello", payload["segments"][0]["transcript_text"])
        self.assertEqual("world", payload["segments"][1]["transcript_text"])


if __name__ == "__main__":
    unittest.main()
