#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import warnings
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on some non-POSIX platforms
    fcntl = None

SCRIPT_DIR = Path(__file__).resolve().parent
VENDOR_DIR = SCRIPT_DIR / "_vendor"
INSTALL_LOCK_PATH = SCRIPT_DIR.parent / ".vendor-install.lock"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
PTS_TIME_RE = re.compile(r"pts_time[:=]([0-9]+(?:\.[0-9]+)?)")
SCENE_SCORE_RE = re.compile(r"lavfi\.scene_score=([0-9]+(?:\.[0-9]+)?)")
DEPENDENCIES = {
    "yt_dlp": "yt-dlp>=2025.1.0",
    "PIL": "Pillow>=10.0.0",
    "imagehash": "ImageHash>=4.3.1",
}
DOWNLOAD_ATTEMPTS = [
    {
        "name": "web_safari hls low",
        "format": "best[protocol*=m3u8][height<=480]/best[height<=480]/best",
        "player_client": "web_safari",
    },
    {
        "name": "tv low",
        "format": "best[height<=480]/best",
        "player_client": "tv",
    },
    {
        "name": "web low mp4",
        "format": "best[ext=mp4][height<=480]/best[height<=480]/best",
        "player_client": "web",
    },
    {
        "name": "ios low mp4",
        "format": "best[ext=mp4][height<=480]/best[height<=480]/best",
        "player_client": "ios",
    },
    {
        "name": "android low mp4",
        "format": "best[ext=mp4][height<=480]/best[height<=480]/best",
        "player_client": "android",
    },
    {
        "name": "web merged low",
        "format": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
        "player_client": "web",
    },
    {
        "name": "web_safari best",
        "format": "best",
        "player_client": "web_safari",
    },
]
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"


class UserError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download a video, extract representative frames, and write a frames manifest.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("video", help="A YouTube URL/video ID, or a local video path for testing.")
    parser.add_argument("--output", required=True, help="Path to write frames_manifest.json.")
    parser.add_argument("--frames-dir", help="Directory for extracted JPG frames.")
    parser.add_argument(
        "--frame-source",
        choices=("auto", "video", "storyboard"),
        default="auto",
        help="Frame source strategy. storyboard skips full video download and uses YouTube storyboard thumbnails.",
    )
    parser.add_argument(
        "--download-format",
        help="Optional yt-dlp format selector override for video downloads.",
    )
    parser.add_argument("--cookies", help="Path to a Netscape-format cookies file for yt-dlp.")
    parser.add_argument(
        "--cookies-from-browser",
        help="Browser name for yt-dlp cookie extraction, for example chrome, safari, or firefox.",
    )
    parser.add_argument("--frame-interval", type=float, default=5.0, help="Timed frame interval in seconds.")
    parser.add_argument("--scene-threshold", type=float, default=0.35, help="ffmpeg scene-detection threshold.")
    parser.add_argument(
        "--scene-min-gap-seconds",
        type=float,
        default=2.0,
        help="Minimum gap between scene-change candidate frames.",
    )
    parser.add_argument(
        "--timestamp-merge-seconds",
        type=float,
        default=1.0,
        help="Merge candidate frame timestamps that are this close.",
    )
    parser.add_argument("--image-width", type=int, default=640, help="Extracted frame width in pixels.")
    parser.add_argument("--segment-seconds", type=float, default=60.0, help="Segment size used for per-segment frame caps.")
    parser.add_argument("--phash-threshold", type=int, default=8, help="Maximum pHash distance treated as duplicate.")
    parser.add_argument(
        "--max-scene-frames-per-segment",
        type=int,
        default=2,
        help="Maximum scene-change candidate frames per segment. Use 0 to disable scene-change frames.",
    )
    parser.add_argument("--max-frames-per-segment", type=int, default=4, help="Maximum kept frames per segment.")
    parser.add_argument("--max-total-frames", type=int, default=100, help="Maximum kept frames in the whole video.")
    parser.add_argument(
        "--keep-video",
        action="store_true",
        help="Keep the downloaded temporary video next to the manifest for debugging.",
    )
    return parser.parse_args()


def ensure_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise UserError(f"`{name}` is required but was not found on PATH.")
    return path


@contextlib.contextmanager
def vendor_install_lock(timeout_seconds: float = 120.0):
    if fcntl is None:
        yield
        return

    import time

    INSTALL_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INSTALL_LOCK_PATH.open("a+", encoding="utf-8") as handle:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - start >= timeout_seconds:
                    raise UserError("Timed out waiting for the frame dependency install lock.")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def ensure_dependency(import_name: str) -> Any:
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))

    try:
        return importlib.import_module(import_name)
    except ImportError:
        package_spec = DEPENDENCIES[import_name]
        with vendor_install_lock():
            try:
                importlib.invalidate_caches()
                return importlib.import_module(import_name)
            except ImportError:
                VENDOR_DIR.mkdir(parents=True, exist_ok=True)
                print(f"Installing {package_spec} into scripts/_vendor ...", file=sys.stderr)
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "--quiet",
                        "--target",
                        str(VENDOR_DIR),
                        package_spec,
                    ],
                    check=True,
                )
                importlib.invalidate_caches()
                if str(VENDOR_DIR) not in sys.path:
                    sys.path.insert(0, str(VENDOR_DIR))
                return importlib.import_module(import_name)


def extract_video_id(value: str) -> str | None:
    candidate = value.strip()
    if VIDEO_ID_PATTERN.fullmatch(candidate):
        return candidate

    parsed = urllib.parse.urlparse(candidate)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")

    if host in {"youtu.be", "www.youtu.be"} and path:
        video_id = path.split("/")[0]
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        if parsed.path == "/watch":
            video_id = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        elif path.startswith(("shorts/", "live/", "embed/")):
            parts = path.split("/")
            video_id = parts[1] if len(parts) > 1 else ""
        else:
            video_id = ""
    else:
        return None

    return video_id if VIDEO_ID_PATTERN.fullmatch(video_id) else None


def canonical_url(video_id: str | None, source: str) -> str:
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return str(Path(source).expanduser().resolve())


def format_timestamp(seconds: float) -> str:
    total_seconds = max(int(seconds), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def probe_duration(video_path: Path, ffprobe_path: str) -> float:
    completed = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise UserError(detail or "ffprobe failed to read video duration.")
    try:
        duration = float(completed.stdout.strip())
    except ValueError as exc:
        raise UserError("ffprobe returned an invalid video duration.") from exc
    if duration <= 0:
        raise UserError("Video duration must be greater than zero.")
    return duration


def download_video(
    source: str,
    work_dir: Path,
    download_format: str | None = None,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> Path:
    local_path = Path(source).expanduser()
    if local_path.exists():
        return local_path.resolve()

    warnings.filterwarnings(
        "ignore",
        message="urllib3 v2 only supports OpenSSL",
    )
    yt_dlp = ensure_dependency("yt_dlp")
    failures: list[str] = []

    attempts = DOWNLOAD_ATTEMPTS
    if download_format:
        attempts = [
            {
                "name": "manual format",
                "format": download_format,
                "player_client": "web_safari",
            },
            *DOWNLOAD_ATTEMPTS,
        ]

    for attempt_index, attempt in enumerate(attempts):
        for existing in work_dir.glob("video.*"):
            existing.unlink(missing_ok=True)

        output_template = str(work_dir / "video.%(ext)s")
        options = build_download_options(
            output_template=output_template,
            format_selector=str(attempt["format"]),
            player_client=str(attempt["player_client"]),
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
        )
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([source])
        except Exception as exc:
            failures.append(f"{attempt['name']}: {str(exc).strip() or exc.__class__.__name__}")
            continue

        candidates = [
            candidate
            for candidate in sorted(work_dir.glob("video.*"))
            if candidate.is_file() and candidate.stat().st_size > 0
        ]
        if candidates:
            return candidates[0]
        failures.append(f"{attempt['name']}: downloaded file was empty or missing")

        if attempt_index < len(attempts) - 1:
            continue

    hint = "Try `--cookies-from-browser chrome`, `--cookies-from-browser safari`, or `--cookies /path/to/cookies.txt`."
    if cookies or cookies_from_browser:
        hint = "The provided cookies were accepted but video download still failed; try another browser cookies source or update yt-dlp."
    detail = "\n".join(failures[-4:])
    raise UserError(f"yt-dlp could not download usable video data. {hint}\n{detail}")


def extract_video_info(
    source: str,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> dict[str, Any]:
    warnings.filterwarnings(
        "ignore",
        message="urllib3 v2 only supports OpenSSL",
    )
    yt_dlp = ensure_dependency("yt_dlp")
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
    }
    if cookies:
        options["cookiefile"] = str(Path(cookies).expanduser())
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser,)
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(source, download=False)


def select_storyboard_format(info: dict[str, Any]) -> dict[str, Any] | None:
    storyboards = [
        item
        for item in info.get("formats", [])
        if isinstance(item, dict)
        and str(item.get("format_id", "")).startswith("sb")
        and item.get("ext") == "mhtml"
        and item.get("fragments")
        and item.get("width")
        and item.get("height")
    ]
    if not storyboards:
        return None
    return max(storyboards, key=lambda item: int(item.get("width") or 0))


def download_binary(url: str, output_path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        output_path.write_bytes(response.read())


def storyboard_tiles(info: dict[str, Any], work_dir: Path) -> list[dict[str, Any]]:
    storyboard = select_storyboard_format(info)
    if storyboard is None:
        return []

    ensure_dependency("PIL")
    image_module = importlib.import_module("PIL.Image")
    width = int(storyboard["width"])
    height = int(storyboard["height"])
    rows = int(storyboard.get("rows") or 1)
    columns = int(storyboard.get("columns") or 1)
    fps = float(storyboard.get("fps") or 0.0)
    default_tile_seconds = 1.0 / fps if fps > 0 else 1.0

    tiles: list[dict[str, Any]] = []
    elapsed = 0.0
    for fragment_index, fragment in enumerate(storyboard.get("fragments", [])):
        fragment_url = fragment.get("url")
        if not fragment_url:
            continue
        sheet_path = work_dir / f"storyboard_{fragment_index:04d}.jpg"
        download_binary(str(fragment_url), sheet_path)
        with image_module.open(sheet_path) as image:
            tile_count = rows * columns
            fragment_duration = float(fragment.get("duration") or (tile_count * default_tile_seconds))
            tile_seconds = fragment_duration / tile_count if tile_count else default_tile_seconds
            for tile_index in range(tile_count):
                start = elapsed + tile_index * tile_seconds
                if start >= float(info.get("duration") or start + 1):
                    break
                row, column = divmod(tile_index, columns)
                tiles.append(
                    {
                        "start": round(start, 3),
                        "image": image.copy(),
                        "box": (
                            column * width,
                            row * height,
                            (column + 1) * width,
                            (row + 1) * height,
                        ),
                    }
                )
        elapsed += float(fragment.get("duration") or 0.0)

    return tiles


def nearest_storyboard_tile(
    tiles: list[dict[str, Any]],
    timestamp: float,
    used_indexes: set[int],
) -> tuple[int, dict[str, Any]] | None:
    available = [
        (index, tile)
        for index, tile in enumerate(tiles)
        if index not in used_indexes
    ]
    if not available:
        return None
    return min(available, key=lambda item: abs(float(item[1]["start"]) - timestamp))


def build_storyboard_frames(
    source: str,
    frames_dir: Path,
    frame_interval: float,
    image_width: int,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    info = extract_video_info(source, cookies=cookies, cookies_from_browser=cookies_from_browser)
    duration_seconds = round(float(info.get("duration") or 0.0), 3)
    if duration_seconds <= 0:
        raise UserError("Could not determine video duration for storyboard fallback.")

    with tempfile.TemporaryDirectory(prefix="youtube-summary-storyboard-") as temp_name:
        tiles = storyboard_tiles(info, Path(temp_name))
        if not tiles:
            raise UserError("Video download failed and no storyboard frames were available.")

        image_module = importlib.import_module("PIL.Image")
        frames: list[dict[str, Any]] = []
        used_indexes: set[int] = set()
        for index, timestamp in enumerate(interval_timestamps(duration_seconds, frame_interval)):
            selected = nearest_storyboard_tile(tiles, timestamp, used_indexes)
            if selected is None:
                break
            tile_index, tile = selected
            used_indexes.add(tile_index)
            crop = tile["image"].crop(tile["box"])
            if image_width > 0 and crop.width != image_width:
                height = max(int(round(crop.height * image_width / crop.width)), 1)
                crop = crop.resize((image_width, height), image_module.Resampling.LANCZOS)
            start_seconds = float(tile["start"])
            frame_path = frames_dir / f"{int(round(start_seconds * 1000)):09d}_{index:04d}.jpg"
            frame_path.parent.mkdir(parents=True, exist_ok=True)
            crop.save(frame_path, "JPEG", quality=88)
            frames.append(
                {
                    "timestamp": format_timestamp(start_seconds),
                    "start": round(start_seconds, 3),
                    "path": str(frame_path.resolve()),
                    "source": "storyboard",
                    "scene_score": None,
                    "phash": hash_frame(frame_path),
                    "kept": True,
                    "duplicate_of": None,
                }
            )

    return duration_seconds, frames


def build_download_options(
    output_template: str,
    format_selector: str,
    player_client: str,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "format": format_selector,
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_args": {"youtube": {"player_client": [player_client]}},
    }
    if cookies:
        options["cookiefile"] = str(Path(cookies).expanduser())
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser,)
    return options


def interval_timestamps(duration_seconds: float, frame_interval: float) -> list[float]:
    if frame_interval <= 0:
        raise UserError("--frame-interval must be greater than zero.")
    count = max(int(math.ceil(duration_seconds / frame_interval)), 1)
    return [round(index * frame_interval, 3) for index in range(count) if index * frame_interval < duration_seconds]


def parse_scene_candidates(output: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    pending_start: float | None = None
    pending_score: float | None = None

    def flush() -> None:
        nonlocal pending_start, pending_score
        if pending_start is None:
            return
        candidates.append(
            {
                "start": round(pending_start, 3),
                "source": "scene",
                "scene_score": round(pending_score, 6) if pending_score is not None else None,
            }
        )
        pending_start = None
        pending_score = None

    for line in output.splitlines():
        timestamp_match = PTS_TIME_RE.search(line)
        if timestamp_match:
            flush()
            pending_start = float(timestamp_match.group(1))

        score_match = SCENE_SCORE_RE.search(line)
        if score_match:
            pending_score = float(score_match.group(1))
            if pending_start is not None:
                flush()

    flush()

    unique: dict[float, dict[str, Any]] = {}
    for candidate in candidates:
        start = float(candidate["start"])
        current = unique.get(start)
        if current is None:
            unique[start] = candidate
            continue
        current_score = current.get("scene_score")
        candidate_score = candidate.get("scene_score")
        if current_score is None or (
            candidate_score is not None and candidate_score > current_score
        ):
            unique[start] = candidate

    return [unique[start] for start in sorted(unique)]


def scene_candidates(video_path: Path, ffmpeg_path: str, scene_threshold: float) -> list[dict[str, Any]]:
    if scene_threshold <= 0:
        return []
    expression = f"select='gt(scene,{scene_threshold})',metadata=print,showinfo"
    completed = subprocess.run(
        [ffmpeg_path, "-hide_banner", "-i", str(video_path), "-vf", expression, "-f", "null", "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    output = "\n".join(item for item in (completed.stdout, completed.stderr) if item)
    if completed.returncode != 0:
        raise UserError(output.strip() or "ffmpeg scene detection failed.")
    return parse_scene_candidates(output)


def limit_scene_candidates(
    candidates: list[dict[str, Any]],
    segment_seconds: float,
    min_gap_seconds: float,
    max_scene_frames_per_segment: int,
    duration_seconds: float,
) -> list[dict[str, Any]]:
    if max_scene_frames_per_segment <= 0:
        return []

    by_segment: dict[int, list[dict[str, Any]]] = {}
    for candidate in candidates:
        start = float(candidate.get("start", -1.0))
        if not 0 <= start < duration_seconds:
            continue
        segment_index = int(start // segment_seconds)
        by_segment.setdefault(segment_index, []).append(candidate)

    selected: list[dict[str, Any]] = []
    for segment_candidates in by_segment.values():
        ranked = sorted(
            segment_candidates,
            key=lambda item: (
                item.get("scene_score") is not None,
                float(item.get("scene_score") or 0.0),
                -float(item["start"]),
            ),
            reverse=True,
        )
        segment_selected: list[dict[str, Any]] = []
        for candidate in ranked:
            start = float(candidate["start"])
            if min_gap_seconds > 0 and any(
                abs(start - float(prior["start"])) < min_gap_seconds
                for prior in segment_selected
            ):
                continue
            segment_selected.append(candidate)
            if len(segment_selected) >= max_scene_frames_per_segment:
                break
        selected.extend(segment_selected)

    return sorted(selected, key=lambda item: float(item["start"]))


def merge_candidates(
    interval_values: list[float],
    scene_values: list[dict[str, Any]],
    merge_seconds: float,
    duration_seconds: float,
) -> list[dict[str, Any]]:
    candidates = [
        {"start": value, "sources": {"interval"}, "scene_score": None}
        for value in interval_values
        if 0 <= value < duration_seconds
    ] + [
        {
            "start": float(value["start"]),
            "sources": {"scene"},
            "scene_score": value.get("scene_score"),
        }
        for value in scene_values
        if 0 <= float(value["start"]) < duration_seconds
    ]
    candidates.sort(key=lambda item: item["start"])

    merged: list[dict[str, Any]] = []
    for candidate in candidates:
        if merged and candidate["start"] - merged[-1]["start"] <= merge_seconds:
            merged[-1]["sources"].update(candidate["sources"])
            if candidate.get("scene_score") is not None:
                current_score = merged[-1].get("scene_score")
                if current_score is None or candidate["scene_score"] > current_score:
                    merged[-1]["scene_score"] = candidate["scene_score"]
            if "interval" not in merged[-1]["sources"] and "interval" in candidate["sources"]:
                merged[-1]["start"] = candidate["start"]
            continue
        merged.append(candidate)

    return [
        {
            "start": round(item["start"], 3),
            "source": "+".join(sorted(item["sources"])),
            "scene_score": item.get("scene_score"),
        }
        for item in merged
    ]


def extract_frame(video_path: Path, frame_path: Path, start_seconds: float, ffmpeg_path: str, image_width: int) -> None:
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    scale = f"scale={image_width}:-2"
    completed = subprocess.run(
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start_seconds:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            scale,
            "-q:v",
            "3",
            "-y",
            str(frame_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not frame_path.exists():
        detail = (completed.stderr or completed.stdout).strip()
        raise UserError(detail or f"Failed to extract frame at {start_seconds:.3f}s.")


def hash_frame(frame_path: Path) -> str:
    ensure_dependency("PIL")
    image_module = importlib.import_module("PIL.Image")
    imagehash = ensure_dependency("imagehash")
    with image_module.open(frame_path) as image:
        return str(imagehash.phash(image))


def hash_distance(left: str, right: str) -> int:
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def apply_dedup_and_caps(
    frames: list[dict[str, Any]],
    segment_seconds: float,
    phash_threshold: int,
    max_frames_per_segment: int,
    max_total_frames: int,
) -> None:
    kept_by_segment: dict[int, list[dict[str, Any]]] = {}

    for frame in frames:
        segment_index = int(frame["start"] // segment_seconds)
        segment_kept = kept_by_segment.setdefault(segment_index, [])

        duplicate = next(
            (
                prior
                for prior in segment_kept
                if hash_distance(frame["phash"], prior["phash"]) <= phash_threshold
            ),
            None,
        )
        if duplicate is not None:
            frame["kept"] = False
            frame["duplicate_of"] = duplicate["path"]
            continue

        if len(segment_kept) >= max_frames_per_segment:
            frame["kept"] = False
            frame["duplicate_of"] = None
            continue

        frame["kept"] = True
        frame["duplicate_of"] = None
        segment_kept.append(frame)

    kept_global: list[dict[str, Any]] = []
    for frame in frames:
        if not frame["kept"]:
            continue
        duplicate = next(
            (
                prior
                for prior in kept_global
                if hash_distance(frame["phash"], prior["phash"]) <= phash_threshold
            ),
            None,
        )
        if duplicate is not None:
            frame["kept"] = False
            frame["duplicate_of"] = duplicate["path"]
        else:
            kept_global.append(frame)

    if len(kept_global) <= max_total_frames:
        return

    selected_indexes = {
        round(index * (len(kept_global) - 1) / (max_total_frames - 1))
        for index in range(max_total_frames)
    } if max_total_frames > 1 else {0}
    for index, frame in enumerate(kept_global):
        if index not in selected_indexes:
            frame["kept"] = False
            frame["duplicate_of"] = None


def build_manifest(args: argparse.Namespace, output_path: Path, frames_dir: Path) -> dict[str, Any]:
    ffmpeg_path = ensure_tool("ffmpeg")
    ffprobe_path = ensure_tool("ffprobe")
    video_id = extract_video_id(args.video)
    extraction_method = "video"
    download_error: str | None = None

    with tempfile.TemporaryDirectory(prefix="youtube-summary-video-") as temp_name:
        work_dir = Path(temp_name)
        if args.frame_source == "storyboard":
            extraction_method = "storyboard"
            duration_seconds, frames = build_storyboard_frames(
                args.video,
                frames_dir=frames_dir,
                frame_interval=args.frame_interval,
                image_width=args.image_width,
                cookies=args.cookies,
                cookies_from_browser=args.cookies_from_browser,
            )
        else:
            try:
                video_path = download_video(
                    args.video,
                    work_dir,
                    download_format=args.download_format,
                    cookies=args.cookies,
                    cookies_from_browser=args.cookies_from_browser,
                )
                if args.keep_video:
                    kept_video = output_path.with_suffix(video_path.suffix)
                    shutil.copy2(video_path, kept_video)

                duration_seconds = round(probe_duration(video_path, ffprobe_path), 3)
                interval_values = interval_timestamps(duration_seconds, args.frame_interval)
                scene_values = limit_scene_candidates(
                    scene_candidates(video_path, ffmpeg_path, args.scene_threshold),
                    segment_seconds=args.segment_seconds,
                    min_gap_seconds=args.scene_min_gap_seconds,
                    max_scene_frames_per_segment=args.max_scene_frames_per_segment,
                    duration_seconds=duration_seconds,
                )
                candidates = merge_candidates(
                    interval_values,
                    scene_values,
                    args.timestamp_merge_seconds,
                    duration_seconds,
                )

                frames = []
                for index, candidate in enumerate(candidates):
                    start_seconds = float(candidate["start"])
                    frame_path = frames_dir / f"{int(round(start_seconds * 1000)):09d}_{index:04d}.jpg"
                    extract_frame(video_path, frame_path, start_seconds, ffmpeg_path, args.image_width)
                    frames.append(
                        {
                            "timestamp": format_timestamp(start_seconds),
                            "start": round(start_seconds, 3),
                            "path": str(frame_path.resolve()),
                            "source": candidate["source"],
                            "scene_score": candidate.get("scene_score"),
                            "phash": hash_frame(frame_path),
                            "kept": True,
                            "duplicate_of": None,
                        }
                    )
            except UserError as exc:
                if args.frame_source == "video":
                    raise
                extraction_method = "storyboard"
                download_error = str(exc)
                duration_seconds, frames = build_storyboard_frames(
                    args.video,
                    frames_dir=frames_dir,
                    frame_interval=args.frame_interval,
                    image_width=args.image_width,
                    cookies=args.cookies,
                    cookies_from_browser=args.cookies_from_browser,
                )

    apply_dedup_and_caps(
        frames,
        segment_seconds=args.segment_seconds,
        phash_threshold=args.phash_threshold,
        max_frames_per_segment=args.max_frames_per_segment,
        max_total_frames=args.max_total_frames,
    )

    return {
        "video_id": video_id,
        "source_url": canonical_url(video_id, args.video),
        "duration_seconds": duration_seconds,
        "settings": {
            "frame_interval_seconds": args.frame_interval,
            "scene_threshold": args.scene_threshold,
            "scene_min_gap_seconds": args.scene_min_gap_seconds,
            "max_scene_frames_per_segment": args.max_scene_frames_per_segment,
            "extraction_method": extraction_method,
            "frame_source": args.frame_source,
            "phash_threshold": args.phash_threshold,
            "max_frames_per_segment": args.max_frames_per_segment,
            "max_total_frames": args.max_total_frames,
        },
        "notes": [f"Video media download failed; used YouTube storyboard fallback. {download_error}"] if download_error else [],
        "frames": frames,
    }


def main() -> int:
    args = parse_args()

    try:
        if args.segment_seconds <= 0:
            raise UserError("--segment-seconds must be greater than zero.")
        if args.scene_min_gap_seconds < 0:
            raise UserError("--scene-min-gap-seconds must be zero or greater.")
        if args.max_scene_frames_per_segment < 0:
            raise UserError("--max-scene-frames-per-segment must be zero or greater.")
        if args.max_frames_per_segment <= 0:
            raise UserError("--max-frames-per-segment must be greater than zero.")
        if args.max_total_frames <= 0:
            raise UserError("--max-total-frames must be greater than zero.")

        output_path = Path(args.output).expanduser().resolve()
        frames_dir = (
            Path(args.frames_dir).expanduser().resolve()
            if args.frames_dir
            else output_path.with_suffix("").parent / f"{output_path.stem}_frames"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        frames_dir.mkdir(parents=True, exist_ok=True)

        manifest = build_manifest(args, output_path, frames_dir)
        output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"Error: Failed to install frame dependency ({exc}).", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {str(exc).strip() or exc.__class__.__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
