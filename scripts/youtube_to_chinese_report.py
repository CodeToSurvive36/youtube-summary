#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
FETCH_SCRIPT = SCRIPT_DIR / "fetch_youtube_transcript.py"
FRAMES_SCRIPT = SCRIPT_DIR / "extract_video_frames.py"
MULTIMODAL_SCRIPT = SCRIPT_DIR / "build_multimodal_segments.py"
TIMELINE_SCRIPT = SCRIPT_DIR / "render_multimodal_timeline.py"
REPORT_SCHEMA = SCRIPT_DIR / "chinese_report.schema.json"


class UserError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch a YouTube transcript and generate a Chinese summary report.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("video", help="A YouTube URL or raw 11-character video ID.")
    parser.add_argument(
        "--strategy",
        choices=("auto", "api", "browser"),
        default="auto",
        help="Transcript fetch strategy passed through to fetch_youtube_transcript.py.",
    )
    parser.add_argument(
        "--langs",
        default="en,en-US,en-GB,zh-Hans,zh-CN,zh-Hant,zh-TW,zh-HK,zh",
        help="Preferred transcript languages passed through to the fetch step.",
    )
    parser.add_argument(
        "--transcript-output",
        help="Optional path to save the intermediate transcript JSON.",
    )
    parser.add_argument(
        "--transcript-input",
        help="Optional existing transcript JSON path. When provided, the fetch step is skipped.",
    )
    parser.add_argument(
        "--json-output",
        help="Optional path to save the structured Chinese report JSON from Codex.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to save the final Markdown report. Defaults to stdout.",
    )
    parser.add_argument(
        "--model",
        help="Optional Codex model override for the report-generation step.",
    )
    parser.add_argument(
        "--keep-transcript",
        action="store_true",
        help="Keep the temporary transcript JSON when --transcript-output is not provided.",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Skip Codex report generation. Useful when only building frame or multimodal artifacts.",
    )
    parser.add_argument(
        "--with-frames",
        action="store_true",
        help="Also extract representative video frames and build multimodal segment JSON.",
    )
    parser.add_argument(
        "--frames-output",
        help="Optional path to save frames_manifest.json. Defaults to a temporary path.",
    )
    parser.add_argument(
        "--multimodal-output",
        help="Optional path to save multimodal_segments.json. Defaults to a temporary path.",
    )
    parser.add_argument(
        "--html-output",
        help="Optional path to save an HTML timeline for caption text and kept frames.",
    )
    parser.add_argument(
        "--keep-frames",
        action="store_true",
        help="Keep temporary frame outputs when --frames-output/--multimodal-output are not provided.",
    )
    parser.add_argument(
        "--cookies",
        help="Path to a Netscape-format cookies file for yt-dlp video downloads.",
    )
    parser.add_argument(
        "--download-format",
        help="Optional yt-dlp format selector override for video downloads.",
    )
    parser.add_argument(
        "--frame-source",
        choices=("auto", "video", "storyboard"),
        default="auto",
        help="Frame source strategy for --with-frames. storyboard skips full video download.",
    )
    parser.add_argument(
        "--cookies-from-browser",
        help="Browser name for yt-dlp cookie extraction, for example chrome, safari, or firefox.",
    )
    parser.add_argument(
        "--frame-interval",
        type=float,
        default=5.0,
        help="Timed frame interval in seconds for --with-frames.",
    )
    parser.add_argument(
        "--scene-threshold",
        type=float,
        default=0.35,
        help="ffmpeg scene-detection threshold for --with-frames.",
    )
    parser.add_argument(
        "--scene-min-gap-seconds",
        type=float,
        default=2.0,
        help="Minimum gap between scene-change candidate frames for --with-frames.",
    )
    parser.add_argument(
        "--segment-seconds",
        type=float,
        default=60.0,
        help="Segment size in seconds for multimodal segment output.",
    )
    parser.add_argument(
        "--max-scene-frames-per-segment",
        type=int,
        default=2,
        help="Maximum scene-change candidate frames per segment for --with-frames. Use 0 to disable scene-change frames.",
    )
    parser.add_argument(
        "--max-frames-per-segment",
        type=int,
        default=4,
        help="Maximum kept frames per segment for --with-frames.",
    )
    parser.add_argument(
        "--max-total-frames",
        type=int,
        default=100,
        help="Maximum kept frames in the whole video for --with-frames.",
    )
    return parser.parse_args()


def ensure_codex_cli() -> str:
    codex = shutil.which("codex")
    if not codex:
        raise UserError("`codex` CLI is required for report generation but was not found on PATH.")
    return codex


def run_fetch_step(args: argparse.Namespace, transcript_path: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(FETCH_SCRIPT),
        args.video,
        "--strategy",
        args.strategy,
        "--langs",
        args.langs,
        "--output",
        str(transcript_path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise UserError(message or "Transcript fetch step failed.")
    return json.loads(transcript_path.read_text(encoding="utf-8"))


def run_frames_step(args: argparse.Namespace, frames_path: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(FRAMES_SCRIPT),
        args.video,
        "--output",
        str(frames_path),
        "--frame-source",
        args.frame_source,
        "--frame-interval",
        str(args.frame_interval),
        "--scene-threshold",
        str(args.scene_threshold),
        "--scene-min-gap-seconds",
        str(args.scene_min_gap_seconds),
        "--segment-seconds",
        str(args.segment_seconds),
        "--max-scene-frames-per-segment",
        str(args.max_scene_frames_per_segment),
        "--max-frames-per-segment",
        str(args.max_frames_per_segment),
        "--max-total-frames",
        str(args.max_total_frames),
    ]
    if args.cookies:
        command.extend(["--cookies", args.cookies])
    if args.cookies_from_browser:
        command.extend(["--cookies-from-browser", args.cookies_from_browser])
    if args.download_format:
        command.extend(["--download-format", args.download_format])
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise UserError(message or "Frame extraction step failed.")
    return json.loads(frames_path.read_text(encoding="utf-8"))


def run_multimodal_step(
    args: argparse.Namespace,
    transcript_path: Path,
    frames_path: Path,
    multimodal_path: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(MULTIMODAL_SCRIPT),
        "--transcript",
        str(transcript_path),
        "--frames",
        str(frames_path),
        "--output",
        str(multimodal_path),
        "--segment-seconds",
        str(args.segment_seconds),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise UserError(message or "Multimodal segment build step failed.")
    return json.loads(multimodal_path.read_text(encoding="utf-8"))


def run_timeline_step(multimodal_path: Path, html_path: Path) -> None:
    command = [
        sys.executable,
        str(TIMELINE_SCRIPT),
        "--segments",
        str(multimodal_path),
        "--output",
        str(html_path),
        "--title",
        "Video Caption and Frame Timeline",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise UserError(message or "HTML timeline render step failed.")


def build_codex_prompt(transcript_path: Path) -> str:
    return (
        "Read the YouTube transcript JSON at "
        f'"{transcript_path}". '
        "Return a JSON object that matches the provided schema. "
        "Requirements: "
        "1. Base every claim only on the transcript JSON and its metadata. "
        "2. Write `chinese_summary` in Chinese as one concise paragraph or a few compact sentences. "
        "3. If the transcript metadata contains caveats in `notes`, incorporate the important reliability caveat briefly into the Chinese summary when it matters. "
        "4. Write `mentioned_items` as a flat deduplicated list of the most important items explicitly mentioned in the transcript, keeping it within the schema limit. "
        "5. Normalize obvious transcript or ASR misspellings for well-known proper nouns when the intended identity is clear from context. "
        "6. Use Chinese when natural, but keep proper nouns such as people, teams, products, and tools in their original form when that is clearer. "
        "7. Do not invent visuals, facts, or conclusions not grounded in the transcript."
    )


def run_codex_report_step(
    codex_path: str,
    transcript_path: Path,
    schema_path: Path,
    model: str | None,
) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
        output_path = Path(handle.name)

    command = [
        codex_path,
        "exec",
        "--ephemeral",
        "--color",
        "never",
        "--skip-git-repo-check",
        "-C",
        str(Path.cwd()),
        "--add-dir",
        str(transcript_path.parent),
        "--output-schema",
        str(schema_path),
        "-o",
        str(output_path),
    ]
    if model:
        command.extend(["--model", model])
    command.append(build_codex_prompt(transcript_path))

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise UserError(detail or "Codex report generation failed.")

    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    finally:
        output_path.unlink(missing_ok=True)


def render_markdown_report(report_payload: dict[str, Any]) -> str:
    lines = ["中文摘要", report_payload["chinese_summary"].strip(), "", "提到的内容"]
    for item in report_payload["mentioned_items"]:
        lines.append(f"- {item.strip()}")
    return "\n".join(lines).strip() + "\n"


def write_optional_json(path: str | None, payload: dict[str, Any]) -> None:
    if path:
        Path(path).expanduser().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def write_output(path: str | None, text: str) -> None:
    if path:
        Path(path).expanduser().write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def main() -> int:
    args = parse_args()
    codex_path = None if args.skip_report else ensure_codex_cli()
    transcript_temp: Path | None = None
    frame_temp_dir: tempfile.TemporaryDirectory[str] | None = None

    try:
        if args.skip_report and not args.with_frames:
            raise UserError("--skip-report requires --with-frames so there is an artifact to build.")

        if args.transcript_input:
            transcript_path = Path(args.transcript_input).expanduser()
            json.loads(transcript_path.read_text(encoding="utf-8"))
        elif args.transcript_output:
            transcript_path = Path(args.transcript_output).expanduser()
            run_fetch_step(args, transcript_path)
        else:
            with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
                transcript_path = Path(handle.name)
            transcript_temp = transcript_path
            run_fetch_step(args, transcript_path)

        if args.with_frames:
            if args.frames_output:
                frames_path = Path(args.frames_output).expanduser()
            elif args.keep_frames:
                frames_path = Path(tempfile.mkdtemp(prefix="youtube-summary-frames-")) / "frames_manifest.json"
            else:
                frame_temp_dir = tempfile.TemporaryDirectory(prefix="youtube-summary-frames-")
                frames_path = Path(frame_temp_dir.name) / "frames_manifest.json"

            if args.multimodal_output:
                multimodal_path = Path(args.multimodal_output).expanduser()
            elif args.keep_frames and args.frames_output:
                multimodal_path = Path(args.frames_output).expanduser().with_name("multimodal_segments.json")
            elif args.keep_frames:
                multimodal_path = frames_path.with_name("multimodal_segments.json")
            elif frame_temp_dir is not None:
                multimodal_path = Path(frame_temp_dir.name) / "multimodal_segments.json"
            else:
                multimodal_path = Path("multimodal_segments.json")

            run_frames_step(args, frames_path)
            run_multimodal_step(args, transcript_path, frames_path, multimodal_path)
            if args.html_output:
                run_timeline_step(multimodal_path, Path(args.html_output).expanduser())
            if not args.frames_output and args.keep_frames:
                print(f"Frames manifest: {frames_path}", file=sys.stderr)
            if not args.multimodal_output and args.keep_frames:
                print(f"Multimodal segments: {multimodal_path}", file=sys.stderr)

        if args.skip_report:
            return 0

        assert codex_path is not None
        report_payload = run_codex_report_step(
            codex_path=codex_path,
            transcript_path=transcript_path,
            schema_path=REPORT_SCHEMA,
            model=args.model,
        )
        markdown = render_markdown_report(report_payload)

        write_optional_json(args.json_output, report_payload)
        write_output(args.output, markdown)
        return 0
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        if transcript_temp and not args.keep_transcript and not args.transcript_output:
            transcript_temp.unlink(missing_ok=True)
        if frame_temp_dir and not args.keep_frames:
            frame_temp_dir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
