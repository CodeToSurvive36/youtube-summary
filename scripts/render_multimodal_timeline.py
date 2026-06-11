#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class UserError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render multimodal_segments.json as a scrollable HTML timeline.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--segments", required=True, help="Path to multimodal_segments.json.")
    parser.add_argument("--output", required=True, help="Path to write the HTML timeline.")
    parser.add_argument("--title", default="Video Timeline", help="HTML page title.")
    return parser.parse_args()


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


def image_src(path_value: str) -> str:
    parsed = urlparse(path_value)
    if parsed.scheme in {"http", "https", "file", "data"}:
        return path_value

    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path.as_uri()
    return path_value


def source_label(frame: dict[str, Any]) -> str:
    source = str(frame.get("source") or "unknown")
    score = frame.get("scene_score")
    if score is None:
        return source
    try:
        return f"{source} · scene {float(score):.3f}"
    except (TypeError, ValueError):
        return source


def render_frame(frame: dict[str, Any]) -> str:
    path = str(frame.get("path") or "")
    timestamp = html.escape(str(frame.get("timestamp") or ""))
    source = html.escape(source_label(frame))
    src = html.escape(image_src(path), quote=True)
    alt = html.escape(f"Frame at {timestamp}")
    return f"""
          <figure class="frame">
            <img src="{src}" alt="{alt}" loading="lazy">
            <figcaption>
              <span>{timestamp}</span>
              <span>{source}</span>
            </figcaption>
          </figure>"""


def render_segment(segment: dict[str, Any]) -> str:
    timestamp = html.escape(str(segment.get("timestamp") or ""))
    start = html.escape(str(segment.get("start") or ""))
    end = html.escape(str(segment.get("end") or ""))
    transcript = html.escape(str(segment.get("transcript_text") or "").strip())
    frames = [frame for frame in segment.get("frames", []) if isinstance(frame, dict)]
    frame_html = "\n".join(render_frame(frame) for frame in frames) if frames else '<p class="empty">No kept frames in this window.</p>'
    transcript_html = (
        f'<p class="caption">{transcript}</p>'
        if transcript
        else '<p class="empty">No caption text in this window.</p>'
    )
    return f"""
      <section class="segment">
        <div class="marker" aria-hidden="true"></div>
        <div class="segment-body">
          <header class="segment-header">
            <h2>{timestamp}</h2>
            <span>{start}s - {end}s</span>
          </header>
          <div class="content-grid">
            <div class="caption-panel">
              <h3>Caption</h3>
              {transcript_html}
            </div>
            <div class="frames-panel">
              <h3>Kept Frames ({len(frames)})</h3>
              <div class="frames-grid">
                {frame_html}
              </div>
            </div>
          </div>
        </div>
      </section>"""


def render_html(payload: dict[str, Any], title: str) -> str:
    segments = [segment for segment in payload.get("segments", []) if isinstance(segment, dict)]
    source_url = str(payload.get("source_url") or "")
    video_id = str(payload.get("video_id") or "")
    page_title = html.escape(title)
    source_html = html.escape(source_url)
    video_html = html.escape(video_id)
    segment_html = "\n".join(render_segment(segment) for segment in segments)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{page_title}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --ink: #202124;
      --muted: #646a73;
      --line: #d7d9d2;
      --accent: #2f6f73;
      --accent-soft: #dceceb;
      --border: #e3e2dc;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}

    header.page-header {{
      padding: 28px clamp(18px, 4vw, 48px) 18px;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
      position: sticky;
      top: 0;
      z-index: 2;
    }}

    h1 {{
      margin: 0 0 8px;
      font-size: clamp(24px, 3vw, 36px);
      font-weight: 720;
      letter-spacing: 0;
    }}

    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
      color: var(--muted);
      font-size: 14px;
    }}

    main {{
      width: min(1180px, 100%);
      margin: 0 auto;
      padding: 28px clamp(16px, 3vw, 36px) 48px;
    }}

    .timeline {{
      position: relative;
    }}

    .timeline::before {{
      content: "";
      position: absolute;
      left: 10px;
      top: 0;
      bottom: 0;
      width: 2px;
      background: var(--line);
    }}

    .segment {{
      position: relative;
      display: grid;
      grid-template-columns: 24px minmax(0, 1fr);
      gap: 18px;
      margin-bottom: 22px;
    }}

    .marker {{
      width: 22px;
      height: 22px;
      border: 3px solid var(--accent);
      background: var(--bg);
      border-radius: 50%;
      margin-top: 21px;
      z-index: 1;
    }}

    .segment-body {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }}

    .segment-header {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 16px;
      padding: 16px 18px;
      background: var(--accent-soft);
      border-bottom: 1px solid var(--border);
    }}

    .segment-header h2 {{
      margin: 0;
      font-size: 20px;
      letter-spacing: 0;
    }}

    .segment-header span {{
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }}

    .content-grid {{
      display: grid;
      grid-template-columns: minmax(260px, 0.9fr) minmax(320px, 1.5fr);
      gap: 0;
    }}

    .caption-panel,
    .frames-panel {{
      padding: 18px;
    }}

    .caption-panel {{
      border-right: 1px solid var(--border);
    }}

    h3 {{
      margin: 0 0 10px;
      font-size: 13px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0;
    }}

    .caption {{
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: 15px;
    }}

    .empty {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
    }}

    .frames-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }}

    .frame {{
      margin: 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      background: #fbfbf9;
    }}

    .frame img {{
      display: block;
      width: 100%;
      aspect-ratio: 16 / 9;
      object-fit: contain;
      background: #111;
    }}

    .frame figcaption {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 8px 10px;
      color: var(--muted);
      font-size: 12px;
    }}

    @media (max-width: 760px) {{
      header.page-header {{
        position: static;
      }}

      .content-grid {{
        grid-template-columns: 1fr;
      }}

      .caption-panel {{
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }}

      .segment-header {{
        align-items: flex-start;
        flex-direction: column;
      }}
    }}
  </style>
</head>
<body>
  <header class="page-header">
    <h1>{page_title}</h1>
    <div class="meta">
      <span>Segments: {len(segments)}</span>
      <span>Video ID: {video_html or "N/A"}</span>
      <span>Source: {source_html or "N/A"}</span>
    </div>
  </header>
  <main>
    <div class="timeline">
      {segment_html}
    </div>
  </main>
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    try:
        payload = load_json(args.segments)
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(render_html(payload, args.title), encoding="utf-8")
        return 0
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {str(exc).strip() or exc.__class__.__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
