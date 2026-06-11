#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPT_PATH = SKILL_DIR / "scripts" / "extract_video_frames.py"

spec = importlib.util.spec_from_file_location("extract_video_frames", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class FrameExtractionTests(unittest.TestCase):
    def test_interval_timestamps_stop_before_duration(self) -> None:
        self.assertEqual(
            [float(value) for value in range(0, 60, 5)],
            module.interval_timestamps(60.0, 5.0),
        )

    def test_build_download_options_includes_cookies_and_player_client(self) -> None:
        options = module.build_download_options(
            output_template="/tmp/video.%(ext)s",
            format_selector="best",
            player_client="ios",
            cookies="/tmp/cookies.txt",
            cookies_from_browser="chrome",
        )

        self.assertEqual("best", options["format"])
        self.assertEqual("/tmp/cookies.txt", options["cookiefile"])
        self.assertEqual(("chrome",), options["cookiesfrombrowser"])
        self.assertEqual(["ios"], options["extractor_args"]["youtube"]["player_client"])

    def test_parse_scene_candidates_reads_timestamp_and_score(self) -> None:
        output = """
frame:0    pts:1024    pts_time:1.024
lavfi.scene_score=0.383650
[Parsed_showinfo_1 @ 0xabc] n:1 pts:2500 pts_time:2.500 pos:123
"""
        self.assertEqual(
            [
                {"start": 1.024, "source": "scene", "scene_score": 0.38365},
                {"start": 2.5, "source": "scene", "scene_score": None},
            ],
            module.parse_scene_candidates(output),
        )

    def test_limit_scene_candidates_prefers_high_scores_and_gap(self) -> None:
        candidates = [
            {"start": 1.0, "source": "scene", "scene_score": 0.2},
            {"start": 2.0, "source": "scene", "scene_score": 0.9},
            {"start": 3.0, "source": "scene", "scene_score": 0.8},
            {"start": 12.0, "source": "scene", "scene_score": 0.4},
            {"start": 65.0, "source": "scene", "scene_score": 0.3},
        ]
        limited = module.limit_scene_candidates(
            candidates,
            segment_seconds=60.0,
            min_gap_seconds=2.0,
            max_scene_frames_per_segment=2,
            duration_seconds=120.0,
        )
        self.assertEqual(
            [
                {"start": 2.0, "source": "scene", "scene_score": 0.9},
                {"start": 12.0, "source": "scene", "scene_score": 0.4},
                {"start": 65.0, "source": "scene", "scene_score": 0.3},
            ],
            limited,
        )

    def test_limit_scene_candidates_can_disable_scene_frames(self) -> None:
        self.assertEqual(
            [],
            module.limit_scene_candidates(
                [{"start": 1.0, "source": "scene", "scene_score": 0.9}],
                segment_seconds=60.0,
                min_gap_seconds=2.0,
                max_scene_frames_per_segment=0,
                duration_seconds=120.0,
            ),
        )

    def test_merge_candidates_sorts_and_merges_nearby_values(self) -> None:
        merged = module.merge_candidates(
            [0.0, 5.0, 10.0],
            [
                {"start": 5.4, "source": "scene", "scene_score": 0.7},
                {"start": 21.0, "source": "scene", "scene_score": 0.5},
            ],
            merge_seconds=1.0,
            duration_seconds=30.0,
        )
        self.assertEqual(
            [
                {"start": 0.0, "source": "interval", "scene_score": None},
                {"start": 5.0, "source": "interval+scene", "scene_score": 0.7},
                {"start": 10.0, "source": "interval", "scene_score": None},
                {"start": 21.0, "source": "scene", "scene_score": 0.5},
            ],
            merged,
        )

    def test_dedup_and_caps_apply_per_segment_and_global_limits(self) -> None:
        frames = [
            {"start": 0.0, "path": "a.jpg", "phash": "0000000000000000"},
            {"start": 5.0, "path": "b.jpg", "phash": "0000000000000000"},
            {"start": 10.0, "path": "c.jpg", "phash": "ffffffffffffffff"},
            {"start": 65.0, "path": "d.jpg", "phash": "0f0f0f0f0f0f0f0f"},
            {"start": 70.0, "path": "e.jpg", "phash": "f0f0f0f0f0f0f0f0"},
        ]
        module.apply_dedup_and_caps(
            frames,
            segment_seconds=60.0,
            phash_threshold=0,
            max_frames_per_segment=1,
            max_total_frames=2,
        )

        self.assertTrue(frames[0]["kept"])
        self.assertFalse(frames[1]["kept"])
        self.assertEqual("a.jpg", frames[1]["duplicate_of"])
        self.assertFalse(frames[2]["kept"])
        self.assertIsNone(frames[2]["duplicate_of"])
        self.assertTrue(frames[3]["kept"])
        self.assertFalse(frames[4]["kept"])

    def test_global_cap_is_spread_across_kept_frames(self) -> None:
        frames = [
            {"start": 0.0, "path": "a.jpg", "phash": "0000000000000000"},
            {"start": 60.0, "path": "b.jpg", "phash": "1111111111111111"},
            {"start": 120.0, "path": "c.jpg", "phash": "2222222222222222"},
            {"start": 180.0, "path": "d.jpg", "phash": "4444444444444444"},
            {"start": 240.0, "path": "e.jpg", "phash": "8888888888888888"},
        ]
        module.apply_dedup_and_caps(
            frames,
            segment_seconds=60.0,
            phash_threshold=0,
            max_frames_per_segment=2,
            max_total_frames=3,
        )

        self.assertEqual(
            ["a.jpg", "c.jpg", "e.jpg"],
            [frame["path"] for frame in frames if frame["kept"]],
        )

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
    def test_cli_smoke_with_local_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            video_path = temp_dir / "fixture.mp4"
            manifest_path = temp_dir / "frames_manifest.json"
            frames_dir = temp_dir / "frames"

            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=160x120:rate=10:duration=3",
                    "-pix_fmt",
                    "yuv420p",
                    "-y",
                    str(video_path),
                ],
                check=True,
            )

            completed = subprocess.run(
                [
                    "python3",
                    str(SCRIPT_PATH),
                    str(video_path),
                    "--output",
                    str(manifest_path),
                    "--frames-dir",
                    str(frames_dir),
                    "--frame-interval",
                    "1",
                    "--scene-threshold",
                    "0.9",
                    "--scene-min-gap-seconds",
                    "1",
                    "--max-scene-frames-per-segment",
                    "1",
                    "--max-frames-per-segment",
                    "3",
                    "--max-total-frames",
                    "5",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)

            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(str(video_path.resolve()), payload["source_url"])
            self.assertEqual(1.0, payload["settings"]["scene_min_gap_seconds"])
            self.assertEqual(1, payload["settings"]["max_scene_frames_per_segment"])
            self.assertGreaterEqual(len(payload["frames"]), 1)
            self.assertTrue(all("scene_score" in frame for frame in payload["frames"]))
            self.assertTrue(any(Path(frame["path"]).exists() for frame in payload["frames"]))


if __name__ == "__main__":
    unittest.main()
