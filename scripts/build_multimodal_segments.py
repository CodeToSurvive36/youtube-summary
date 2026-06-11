#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


class UserError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge transcript segments and extracted frames into multimodal time segments.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--transcript", required=True, help="Path to transcript JSON.")
    parser.add_argument("--frames", required=True, help="Path to frames_manifest.json.")
    parser.add_argument("--output", required=True, help="Path to write multimodal_segments.json.")
    parser.add_argument("--segment-seconds", type=float, default=60.0, help="Segment size in seconds.")
    return parser.parse_args()


def format_timestamp(seconds: float) -> str:
    total_seconds = max(int(seconds), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def load_json(path: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UserError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UserError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise UserError(f"Expected a JSON object in {path}.")
    return payload


def normalize_transcript_payload(transcript: dict[str, Any]) -> dict[str, Any]:
    if isinstance(transcript.get("segments"), list):
        return transcript

    selected = transcript.get("selected_result")
    if not isinstance(selected, dict):
        raise UserError("Transcript JSON must contain `segments` or `selected_result.segments`.")

    segments = selected.get("segments", [])
    if not isinstance(segments, list):
        raise UserError("Transcript JSON must contain a `segments` array.")

    video = transcript.get("video", {})
    if video is not None and not isinstance(video, dict):
        raise UserError("Transcript JSON field `video` must be an object when present.")

    return {
        "video_id": transcript.get("video_id") or video.get("video_id"),
        "source_url": transcript.get("source_url") or video.get("source_url"),
        "duration_seconds": transcript.get("duration_seconds") or video.get("duration_seconds") or selected.get("duration_seconds"),
        "segments": segments,
    }


def transcript_duration(transcript: dict[str, Any]) -> float:
    explicit = transcript.get("duration_seconds")
    if isinstance(explicit, (int, float)) and explicit > 0:
        return float(explicit)
    return max((float(segment.get("end", segment.get("start", 0.0))) for segment in transcript.get("segments", [])), default=0.0)


def build_segments(
    transcript: dict[str, Any],
    frames_manifest: dict[str, Any],
    segment_seconds: float,
) -> dict[str, Any]:
    if segment_seconds <= 0:
        raise UserError("--segment-seconds must be greater than zero.")

    transcript = normalize_transcript_payload(transcript)
    transcript_segments = transcript.get("segments", [])
    if not isinstance(transcript_segments, list):
        raise UserError("Transcript JSON must contain a `segments` array.")

    frames = [
        frame
        for frame in frames_manifest.get("frames", [])
        if isinstance(frame, dict) and frame.get("kept", True)
    ]

    duration = max(
        transcript_duration(transcript),
        float(frames_manifest.get("duration_seconds") or 0.0),
        max((float(frame.get("start", 0.0)) for frame in frames), default=0.0),
    )
    segment_count = max(int(math.ceil(duration / segment_seconds)), 1)
    segments: list[dict[str, Any]] = []

    for index in range(segment_count):
        start = round(index * segment_seconds, 3)
        end = round((index + 1) * segment_seconds, 3)
        transcript_items = [
            item
            for item in transcript_segments
            if start <= float(item.get("start", 0.0)) < end
        ]
        frame_items = [
            {
                "timestamp": frame.get("timestamp", format_timestamp(float(frame.get("start", 0.0)))),
                "start": float(frame.get("start", 0.0)),
                "path": frame["path"],
                "source": frame.get("source", "unknown"),
                "scene_score": frame.get("scene_score"),
            }
            for frame in frames
            if start <= float(frame.get("start", 0.0)) < end and frame.get("path")
        ]
        if not transcript_items and not frame_items:
            continue

        segments.append(
            {
                "start": start,
                "end": end,
                "timestamp": format_timestamp(start),
                "transcript_text": "\n".join(str(item.get("text", "")).strip() for item in transcript_items if str(item.get("text", "")).strip()),
                "frame_count": len(frame_items),
                "frames": frame_items,
            }
        )

    return {
        "video_id": transcript.get("video_id") or frames_manifest.get("video_id"),
        "source_url": transcript.get("source_url") or frames_manifest.get("source_url"),
        "segment_seconds": segment_seconds,
        "segments": segments,
    }


def main() -> int:
    args = parse_args()
    try:
        payload = build_segments(
            load_json(args.transcript),
            load_json(args.frames),
            args.segment_seconds,
        )
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {str(exc).strip() or exc.__class__.__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
