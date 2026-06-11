#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPT_PATH = SKILL_DIR / "scripts" / "render_multimodal_timeline.py"

spec = importlib.util.spec_from_file_location("render_multimodal_timeline", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class TimelineRenderTests(unittest.TestCase):
    def test_render_html_shows_caption_and_kept_frames(self) -> None:
        payload = {
            "video_id": "abc123",
            "source_url": "https://example.test/watch",
            "segments": [
                {
                    "start": 0.0,
                    "end": 60.0,
                    "timestamp": "00:00",
                    "transcript_text": "hello <world>",
                    "frames": [
                        {
                            "timestamp": "00:05",
                            "start": 5.0,
                            "path": "/tmp/frame 1.jpg",
                            "source": "interval+scene",
                            "scene_score": 0.81234,
                        }
                    ],
                },
                {
                    "start": 60.0,
                    "end": 120.0,
                    "timestamp": "01:00",
                    "transcript_text": "",
                    "frames": [],
                },
            ],
        }

        rendered = module.render_html(payload, "Timeline")

        self.assertIn('<div class="timeline">', rendered)
        self.assertIn("hello &lt;world&gt;", rendered)
        self.assertIn("file:///tmp/frame%201.jpg", rendered)
        self.assertIn("interval+scene · scene 0.812", rendered)
        self.assertIn("No caption text in this window.", rendered)


if __name__ == "__main__":
    unittest.main()
