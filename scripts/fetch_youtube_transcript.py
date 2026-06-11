#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import html
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on some non-POSIX platforms
    fcntl = None

DEFAULT_LANGS = [
    "en",
    "en-US",
    "en-GB",
    "zh-Hans",
    "zh-CN",
    "zh-Hant",
    "zh-TW",
    "zh-HK",
    "zh",
]
DEFAULT_PROVIDERS = ["yt-dlp", "api", "browser", "asr"]
DEPENDENCIES = {
    "youtube_transcript_api": "youtube-transcript-api>=1.2.0,<2",
    "yt_dlp": "yt-dlp>=2025.1.0",
    "faster_whisper": "faster-whisper>=1.0.0",
}
VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"
INSTALL_LOCK_PATH = VENDOR_DIR.parent / ".vendor-install.lock"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
CHAPTER_RE = re.compile(r"^Chapter\s+\d+:\s+(.+)$")
VTT_TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
PLAYWRIGHT_CAPTURE_VAR = "__YOUTUBE_CAPTION_SUMMARY_CAPTURE"
PLAYWRIGHT_TIMEOUT_MS = 180_000
PLAYWRIGHT_CAPTURE_CODE = f"""
async page => {{
  const clickFirst = async locators => {{
    for (const locator of locators) {{
      try {{
        if (await locator.count()) {{
          const target = locator.first();
          try {{
            await target.scrollIntoViewIfNeeded();
          }} catch {{}}
          await target.click({{ timeout: 3000 }});
          return true;
        }}
      }} catch {{}}
    }}
    return false;
  }};

  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(1500);
  await clickFirst([page.getByRole("button", {{ name: "...more" }})]);
  await page.waitForTimeout(600);
  await clickFirst([page.getByRole("button", {{ name: /show transcript/i }})]);
  await page.waitForTimeout(900);
  await clickFirst([page.getByRole("tab", {{ name: /transcript/i }})]);
  await page.waitForTimeout(1500);

  const payload = await page.evaluate(() => JSON.stringify({{
    title: document.title.replace(/\\s*-\\s*YouTube$/, ""),
    bodyText: document.body.innerText || "",
    htmlLang: document.documentElement.lang || null,
    antiBot: (document.body.innerText || "").toLowerCase().includes("confirm that you're not a bot"),
  }}));
  await page.evaluate((value) => {{
    window.{PLAYWRIGHT_CAPTURE_VAR} = value;
  }}, payload);
}}
""".strip()


class UserError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch YouTube captions into a v2 caption artifact.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "video",
        help="A YouTube watch/share/embed/shorts URL, or a raw 11-character video ID.",
    )
    parser.add_argument(
        "--langs",
        default=",".join(DEFAULT_LANGS),
        help="Comma-separated preferred caption language codes.",
    )
    parser.add_argument(
        "--providers",
        default=",".join(DEFAULT_PROVIDERS),
        help="Comma-separated provider order: yt-dlp, api, browser, asr.",
    )
    parser.add_argument(
        "--strategy",
        choices=("auto", "api", "browser"),
        help="Backward-compatible alias for --providers.",
    )
    parser.add_argument(
        "--translate-to",
        help="Optional language code. Only the youtube-transcript-api provider can translate directly.",
    )
    parser.add_argument(
        "--chunk-seconds",
        type=float,
        default=90.0,
        help="Time window in seconds for chunk generation.",
    )
    parser.add_argument(
        "--asr-model",
        default="base",
        help="faster-whisper model name used when ASR fallback is required.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text", "segments", "chunks"),
        default="json",
        help="Output format.",
    )
    parser.add_argument(
        "--output",
        help="Optional output path. Defaults to stdout.",
    )
    parser.add_argument(
        "--preserve-formatting",
        action="store_true",
        help="Preserve formatting markers when the API transcript source exposes them.",
    )
    return parser.parse_args()


def parse_languages(raw: str) -> list[str]:
    langs = [item.strip() for item in raw.split(",") if item.strip()]
    return langs or DEFAULT_LANGS.copy()


def parse_providers(raw: str, strategy: str | None = None) -> list[str]:
    if strategy == "api":
        return ["api"]
    if strategy == "browser":
        return ["browser"]
    if strategy == "auto":
        return DEFAULT_PROVIDERS.copy()

    aliases = {
        "yt_dlp": "yt-dlp",
        "ytdlp": "yt-dlp",
        "youtube-transcript-api": "api",
        "youtube_transcript_api": "api",
        "playwright": "browser",
        "whisper": "asr",
        "faster-whisper": "asr",
    }
    providers = []
    for item in raw.split(","):
        provider = aliases.get(item.strip().lower(), item.strip().lower())
        if not provider:
            continue
        if provider not in DEFAULT_PROVIDERS:
            raise UserError(f"Unsupported provider '{provider}'.")
        providers.append(provider)
    return providers or DEFAULT_PROVIDERS.copy()


def extract_video_id(value: str) -> str:
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
        raise UserError("Expected a YouTube URL or a raw 11-character video ID.")

    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise UserError("Could not extract a valid YouTube video ID from the provided value.")
    return video_id


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def browser_url(source_url: str) -> str:
    parsed = urllib.parse.urlparse(source_url)
    query = urllib.parse.parse_qs(parsed.query)
    query["hl"] = ["en"]
    query["gl"] = ["US"]
    query["persist_hl"] = ["1"]
    query["persist_gl"] = ["1"]
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(query, doseq=True))
    )


def format_timestamp(seconds: float) -> str:
    total_seconds = max(int(seconds), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_timestamp(value: str) -> float:
    if "." in value:
        base, milliseconds = value.split(".", 1)
        return parse_timestamp(base) + float(f"0.{milliseconds}")
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes * 60 + seconds)
    hours, minutes, seconds = parts
    return float(hours * 3600 + minutes * 60 + seconds)


def is_duration_line(line: str) -> bool:
    cleaned = line.strip().lower()
    if not cleaned:
        return False
    if not any(unit in cleaned for unit in ("second", "minute", "hour")):
        return False
    return bool(
        re.fullmatch(
            r"(?:(?:\d+\s+hours?(?:,\s*)?)?(?:\d+\s+minutes?(?:,\s*)?)?(?:\d+\s+seconds?)?)",
            cleaned,
        )
    )


@contextlib.contextmanager
def vendor_install_lock(timeout_seconds: float = 120.0):
    if fcntl is None:
        yield
        return

    INSTALL_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INSTALL_LOCK_PATH.open("a+", encoding="utf-8") as handle:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() - start >= timeout_seconds:
                    raise UserError("Timed out waiting for the caption dependency install lock.")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def ensure_dependency(import_name: str, *, install: bool = True) -> Any:
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))

    try:
        return importlib.import_module(import_name)
    except ImportError:
        if not install:
            raise
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


def fetch_title(source_url: str) -> str | None:
    endpoint = (
        "https://www.youtube.com/oembed?url="
        + urllib.parse.quote(source_url, safe="")
        + "&format=json"
    )
    try:
        with urllib.request.urlopen(endpoint, timeout=20) as response:
            payload = json.load(response)
    except Exception:
        return None
    title = payload.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else None


def clean_caption_text(value: str) -> str:
    text = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", " ", value)
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\ufeff", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_segments(raw_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    previous_text: str | None = None
    for raw in sorted(raw_segments, key=lambda item: float(item.get("start", 0.0))):
        text = clean_caption_text(str(raw.get("text", "")))
        if not text or text == previous_text:
            continue
        start = round(float(raw.get("start", 0.0)), 3)
        if "end" in raw:
            end = round(float(raw.get("end", start)), 3)
            duration = max(round(end - start, 3), 0.0)
        else:
            duration = max(round(float(raw.get("duration", 0.0)), 3), 0.0)
            end = round(start + duration, 3)
        item = {
            "text": text,
            "start": start,
            "duration": duration,
            "end": end,
            "timestamp": format_timestamp(start),
        }
        for key, value in raw.items():
            if key not in item and key not in {"duration", "end"}:
                item[key] = value
        normalized.append(item)
        previous_text = text
    return finalize_segments(normalized)


def finalize_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, segment in enumerate(segments):
        if "duration" not in segment or "end" not in segment:
            if index + 1 < len(segments):
                duration = max(segments[index + 1]["start"] - segment["start"], 0.0)
            else:
                duration = 0.0
            segment["duration"] = round(duration, 3)
            segment["end"] = round(segment["start"] + segment["duration"], 3)
    return segments


def join_transcript_text(segments: list[dict[str, Any]]) -> str:
    return "\n".join(segment["text"] for segment in segments)


def parse_vtt(vtt_text: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    lines = vtt_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        timestamp_match = VTT_TIMESTAMP_RE.search(line)
        if not timestamp_match:
            index += 1
            continue

        start = parse_timestamp(timestamp_match.group("start"))
        end = parse_timestamp(timestamp_match.group("end"))
        index += 1
        text_parts: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_parts.append(lines[index].strip())
            index += 1
        text = clean_caption_text(" ".join(text_parts))
        if text:
            segments.append({"text": text, "start": start, "end": end})
        index += 1

    if not segments:
        raise UserError("VTT caption file did not contain any parseable segments.")
    return normalize_segments(segments)


def build_chunks(
    segments: list[dict[str, Any]],
    chunk_seconds: float,
    source_provider: str,
) -> list[dict[str, Any]]:
    if chunk_seconds <= 0:
        raise UserError("--chunk-seconds must be greater than zero.")

    chunks: list[dict[str, Any]] = []
    current_index: int | None = None
    current_items: list[dict[str, Any]] = []

    def flush() -> None:
        if current_index is None or not current_items:
            return
        start = round(current_index * chunk_seconds, 3)
        end = round((current_index + 1) * chunk_seconds, 3)
        text = "\n".join(item["text"] for item in current_items if item.get("text"))
        if not text.strip():
            return
        chunks.append(
            {
                "start": start,
                "end": end,
                "timestamp": format_timestamp(start),
                "text": text,
                "segment_count": len(current_items),
                "source_provider": source_provider,
            }
        )

    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        segment_index = int(float(segment.get("start", 0.0)) // chunk_seconds)
        if current_index is None:
            current_index = segment_index
        if segment_index != current_index:
            flush()
            current_index = segment_index
            current_items = []
        current_items.append(segment)
    flush()
    return chunks


def quality_summary(segments: list[dict[str, Any]], is_generated: bool | None = None) -> dict[str, Any]:
    duration_seconds = round(max((float(segment.get("end", 0.0)) for segment in segments), default=0.0), 3)
    text_length = sum(len(str(segment.get("text", ""))) for segment in segments)
    score = 0
    reasons: list[str] = []
    if segments:
        score += 40
        reasons.append("segments_present")
    if len(segments) >= 3:
        score += 20
        reasons.append("multiple_segments")
    if duration_seconds >= 30:
        score += 20
        reasons.append("duration_covered")
    if text_length >= 200:
        score += 10
        reasons.append("substantial_text")
    if is_generated is False:
        score += 10
        reasons.append("manual_caption")
    if is_generated is True:
        reasons.append("automatic_or_asr_caption")
    return {
        "score": min(score, 100),
        "reasons": reasons,
        "segment_count": len(segments),
        "duration_seconds": duration_seconds,
        "text_length": text_length,
    }


def is_quality_sufficient(result: dict[str, Any]) -> bool:
    quality = quality_summary(result.get("segments", []), result.get("is_generated"))
    return bool(result.get("segments")) and quality["score"] >= 50


def list_available_transcripts(transcript_list: Any) -> list[dict[str, Any]]:
    return [
        {
            "language": transcript.language,
            "language_code": transcript.language_code,
            "is_generated": transcript.is_generated,
            "is_translatable": transcript.is_translatable,
        }
        for transcript in transcript_list
    ]


def choose_best_transcript(
    transcript_list: Any,
    requested_languages: list[str],
    errors_module: Any,
) -> tuple[Any, bool]:
    try:
        return transcript_list.find_transcript(requested_languages), False
    except errors_module.NoTranscriptFound:
        transcripts = list(transcript_list)
        if not transcripts:
            raise UserError("No transcript tracks are available for this video.")

        manual = [item for item in transcripts if not item.is_generated]
        return (manual or transcripts)[0], True


def fetch_with_optional_translation(
    transcript: Any,
    translate_to: str | None,
    preserve_formatting: bool,
) -> Any:
    if translate_to and transcript.language_code != translate_to:
        if not transcript.is_translatable:
            raise UserError(
                f"Transcript language '{transcript.language_code}' cannot be translated to '{translate_to}'."
            )
        available_targets = {
            item.language_code for item in getattr(transcript, "translation_languages", [])
        }
        if available_targets and translate_to not in available_targets:
            raise UserError(
                f"Translation to '{translate_to}' is not available for transcript '{transcript.language_code}'."
            )
        transcript = transcript.translate(translate_to)
    return transcript.fetch(preserve_formatting=preserve_formatting)


def choose_yt_dlp_caption(
    info: dict[str, Any],
    requested_languages: list[str],
) -> tuple[dict[str, Any], bool, bool]:
    subtitles = info.get("subtitles") or {}
    automatic_captions = info.get("automatic_captions") or {}

    for captions, is_generated in ((subtitles, False), (automatic_captions, True)):
        for language in requested_languages:
            tracks = captions.get(language)
            if tracks:
                track = choose_yt_dlp_track(tracks)
                track["language_code"] = language
                return track, is_generated, False

        for language, tracks in captions.items():
            if tracks:
                track = choose_yt_dlp_track(tracks)
                track["language_code"] = language
                return track, is_generated, True

    raise UserError("No manual or automatic captions are available from yt-dlp.")


def choose_yt_dlp_track(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(tracks, list) or not tracks:
        raise UserError("Caption track list is empty.")
    preferred_exts = ["vtt", "webvtt", "srv3", "ttml", "json3"]
    for ext in preferred_exts:
        for track in tracks:
            if str(track.get("ext", "")).lower() == ext and track.get("url"):
                return dict(track)
    for track in tracks:
        if track.get("url"):
            return dict(track)
    raise UserError("Caption track did not contain a downloadable URL.")


def download_caption_text(track: dict[str, Any]) -> str:
    url = track.get("url")
    if not isinstance(url, str) or not url:
        raise UserError("Caption track did not contain a URL.")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_via_yt_dlp(
    *,
    video_id: str,
    source_url: str,
    title: str | None,
    requested_languages: list[str],
    translate_to: str | None,
    preserve_formatting: bool,
) -> dict[str, Any]:
    del translate_to, preserve_formatting
    yt_dlp = ensure_dependency("yt_dlp")
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(source_url, download=False)
    track, is_generated, used_language_fallback = choose_yt_dlp_caption(info, requested_languages)
    ext = str(track.get("ext", "")).lower()
    if ext not in {"vtt", "webvtt"}:
        raise UserError(f"yt-dlp selected caption format '{ext}', but only VTT is supported now.")
    segments = parse_vtt(download_caption_text(track))
    return build_provider_result(
        provider="yt-dlp",
        video_id=video_id,
        source_url=source_url,
        title=info.get("title") or title,
        segments=segments,
        chapters=[],
        language_code=track.get("language_code"),
        is_generated=is_generated,
        used_language_fallback=used_language_fallback,
        source_format=ext,
        notes=["Caption was extracted from yt-dlp metadata subtitles."],
        raw_metadata={
            "format": ext,
            "name": track.get("name"),
            "duration": info.get("duration"),
        },
    )


def fetch_via_api(
    *,
    video_id: str,
    source_url: str,
    title: str | None,
    requested_languages: list[str],
    translate_to: str | None,
    preserve_formatting: bool,
) -> dict[str, Any]:
    yta = ensure_dependency("youtube_transcript_api")
    api = yta.YouTubeTranscriptApi()
    transcript_list = api.list(video_id)
    available_transcripts = list_available_transcripts(transcript_list)
    chosen_transcript, used_language_fallback = choose_best_transcript(
        transcript_list,
        requested_languages,
        yta,
    )
    fetched_transcript = fetch_with_optional_translation(
        chosen_transcript,
        translate_to,
        preserve_formatting,
    )
    return build_provider_result(
        provider="api",
        video_id=video_id,
        source_url=source_url,
        title=title,
        segments=normalize_segments(fetched_transcript.to_raw_data()),
        chapters=[],
        language_code=fetched_transcript.language_code,
        is_generated=fetched_transcript.is_generated,
        used_language_fallback=used_language_fallback,
        translated_to=translate_to,
        source_format="youtube_transcript_api",
        notes=["Caption was extracted with youtube-transcript-api."],
        raw_metadata={
            "available_transcripts": available_transcripts,
            "language": fetched_transcript.language,
        },
    )


def playwright_cli_command() -> list[str]:
    skill_root = Path(__file__).resolve().parents[2]
    candidates = [
        skill_root / "playwright" / "scripts" / "playwright_cli.sh",
        Path.home() / ".codex" / "skills" / "playwright" / "scripts" / "playwright_cli.sh",
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate)]
    if shutil.which("npx"):
        return ["npx", "--yes", "--package", "@playwright/cli", "playwright-cli"]
    raise UserError(
        "Browser transcript extraction requires Playwright CLI or at least `npx` on PATH."
    )


def extract_playwright_error(output: str) -> str:
    match = re.search(r"### Error\n(.*?)(?:\n### |\Z)", output, re.DOTALL)
    if match:
        return match.group(1).strip()
    return output.strip() or "unknown Playwright error"


def parse_playwright_result(output: str) -> Any:
    match = re.search(r"### Result\n(.*?)(?:\n### |\Z)", output, re.DOTALL)
    if not match:
        raise UserError("Playwright eval did not return a result.")
    raw = match.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def run_playwright(
    command: list[str],
    session: str,
    *args: str,
    timeout_ms: int = PLAYWRIGHT_TIMEOUT_MS,
) -> str:
    env = os.environ.copy()
    env["PLAYWRIGHT_CLI_SESSION"] = session
    completed = subprocess.run(
        command + list(args),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_ms / 1000,
        env=env,
    )
    output = completed.stdout
    if completed.stderr:
        output = f"{output}\n{completed.stderr}" if output else completed.stderr
    if completed.returncode != 0 or "### Error" in output:
        raise UserError(extract_playwright_error(output))
    return output


def extract_transcript_block(body_text: str) -> str:
    normalized = body_text.replace("\r\n", "\n").replace("\r", "\n")
    start = normalized.find("In this video\nChapters\nTranscript\n")
    if start == -1:
        start = normalized.find("Chapter 1:")
    if start == -1:
        raise UserError("Browser transcript extraction could not find the transcript panel in the page text.")

    end_markers = [
        marker
        for marker in ("\nSync to video time", "\nAutoplay\n", "\nUp next\n", "\nSuggested videos")
        if marker in normalized[start:]
    ]
    if end_markers:
        end = min(normalized.find(marker, start) for marker in end_markers)
    else:
        end = len(normalized)
    return normalized[start:end].strip()


def parse_browser_transcript_block(
    transcript_block: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lines = [line.strip() for line in transcript_block.splitlines() if line.strip()]
    lines = [
        line
        for line in lines
        if line
        not in {
            "In this video",
            "Chapters",
            "Transcript",
            "Follow along using the transcript.",
            "Sync to video time",
        }
    ]

    chapters: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    pending_chapter: str | None = None
    current_chapter: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        chapter_match = CHAPTER_RE.match(line)
        if chapter_match:
            pending_chapter = chapter_match.group(1).strip()
            index += 1
            continue

        if not TIMESTAMP_RE.match(line):
            index += 1
            continue

        start_seconds = parse_timestamp(line)
        timestamp = format_timestamp(start_seconds)
        if pending_chapter is not None:
            current_chapter = pending_chapter
            chapters.append(
                {
                    "title": current_chapter,
                    "start": start_seconds,
                    "timestamp": timestamp,
                }
            )
            pending_chapter = None

        index += 1
        if index < len(lines) and is_duration_line(lines[index]):
            index += 1

        text_parts: list[str] = []
        while index < len(lines):
            probe = lines[index]
            if TIMESTAMP_RE.match(probe) or CHAPTER_RE.match(probe):
                break
            if not is_duration_line(probe):
                text_parts.append(probe)
            index += 1

        text = " ".join(text_parts).strip()
        if text:
            segments.append(
                {
                    "text": text,
                    "start": start_seconds,
                    "timestamp": timestamp,
                    "chapter": current_chapter,
                }
            )

    if not segments:
        raise UserError(
            "Browser transcript extraction opened the transcript panel but could not parse any segments."
        )

    return normalize_segments(segments), chapters


def fetch_via_browser(
    *,
    video_id: str,
    source_url: str,
    title: str | None,
    requested_languages: list[str],
    translate_to: str | None,
    preserve_formatting: bool,
) -> dict[str, Any]:
    del requested_languages, preserve_formatting
    command = playwright_cli_command()
    notes: list[str] = [
        "Transcript was extracted from the YouTube transcript panel in a real browser."
    ]
    if translate_to:
        notes.append(
            "Browser transcript extraction returns the original transcript track; translate during summarization if needed."
        )

    payload: Any = None
    last_error: Exception | None = None

    for _ in range(3):
        session = f"ycs-{uuid.uuid4().hex[:8]}"
        try:
            try:
                run_playwright(command, session, "close", timeout_ms=10_000)
            except Exception:
                pass
            run_playwright(command, session, "open", browser_url(source_url))
            run_playwright(command, session, "run-code", PLAYWRIGHT_CAPTURE_CODE)
            captured = run_playwright(command, session, "eval", f"window.{PLAYWRIGHT_CAPTURE_VAR}")
            payload = parse_playwright_result(captured)
            break
        except Exception as exc:
            last_error = exc
            if "EADDRINUSE" not in describe_error(exc):
                raise
        finally:
            try:
                run_playwright(command, session, "close", timeout_ms=30_000)
            except Exception:
                pass

    if payload is None:
        if last_error is not None:
            raise UserError(describe_error(last_error))
        raise UserError("Browser transcript extraction did not return a payload.")

    if not isinstance(payload, str):
        raise UserError("Browser transcript extraction returned an unexpected payload shape.")
    capture = json.loads(payload)
    transcript_block = extract_transcript_block(capture.get("bodyText", ""))
    segments, chapters = parse_browser_transcript_block(transcript_block)

    if capture.get("antiBot"):
        notes.append("The watch page showed a bot-check banner, but the transcript panel was still accessible.")

    return build_provider_result(
        provider="browser",
        video_id=video_id,
        source_url=source_url,
        title=title or capture.get("title"),
        segments=segments,
        chapters=chapters,
        language_code=capture.get("htmlLang"),
        is_generated=None,
        used_language_fallback=False,
        source_format="transcript_panel",
        notes=notes,
        raw_metadata={"page_language_code": capture.get("htmlLang")},
    )


def download_audio(source_url: str, work_dir: Path) -> Path:
    yt_dlp = ensure_dependency("yt_dlp")
    output_template = str(work_dir / "audio.%(ext)s")
    options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }
        ],
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([source_url])
    candidates = sorted(work_dir.glob("audio.*"))
    if not candidates:
        raise UserError("yt-dlp finished but no downloaded audio file was found.")
    return candidates[0]


def normalize_asr_segments(raw_segments: Any) -> list[dict[str, Any]]:
    raw_items: list[dict[str, Any]] = []
    for segment in raw_segments:
        raw_items.append(
            {
                "text": getattr(segment, "text", ""),
                "start": float(getattr(segment, "start", 0.0)),
                "end": float(getattr(segment, "end", 0.0)),
            }
        )
    return normalize_segments(raw_items)


def fetch_via_asr(
    *,
    video_id: str,
    source_url: str,
    title: str | None,
    requested_languages: list[str],
    translate_to: str | None,
    preserve_formatting: bool,
    asr_model: str,
) -> dict[str, Any]:
    del preserve_formatting
    faster_whisper = ensure_dependency("faster_whisper", install=False)
    language_hint = requested_languages[0].split("-")[0] if requested_languages else None
    with tempfile.TemporaryDirectory(prefix="youtube-summary-asr-") as temp_name:
        audio_path = download_audio(source_url, Path(temp_name))
        model = faster_whisper.WhisperModel(asr_model)
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=language_hint,
            task="translate" if translate_to else "transcribe",
        )
        segments = normalize_asr_segments(list(segments_iter))

    detected_language = getattr(info, "language", None)
    notes = [
        "Caption was generated by local faster-whisper ASR and may contain recognition errors.",
    ]
    return build_provider_result(
        provider="asr",
        video_id=video_id,
        source_url=source_url,
        title=title,
        segments=segments,
        chapters=[],
        language_code=detected_language or language_hint,
        is_generated=True,
        used_language_fallback=False,
        translated_to=translate_to,
        source_format="faster_whisper",
        notes=notes,
        raw_metadata={
            "asr_model": asr_model,
            "language_probability": getattr(info, "language_probability", None),
        },
    )


def build_provider_result(
    *,
    provider: str,
    video_id: str,
    source_url: str,
    title: str | None,
    segments: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    language_code: str | None,
    is_generated: bool | None,
    used_language_fallback: bool,
    source_format: str,
    notes: list[str],
    raw_metadata: dict[str, Any],
    translated_to: str | None = None,
) -> dict[str, Any]:
    segments = normalize_segments(segments)
    if not segments:
        raise UserError(f"{provider} did not return any caption segments.")
    duration_seconds = round(max((float(segment["end"]) for segment in segments), default=0.0), 3)
    return {
        "provider": provider,
        "video_id": video_id,
        "source_url": source_url,
        "title": title,
        "language_code": language_code,
        "is_generated": is_generated,
        "used_language_fallback": used_language_fallback,
        "translated_to": translated_to,
        "source_format": source_format,
        "segment_count": len(segments),
        "duration_seconds": duration_seconds,
        "chapters": chapters,
        "text": join_transcript_text(segments),
        "segments": segments,
        "notes": notes,
        "raw_metadata": raw_metadata,
    }


PROVIDER_FUNCTIONS = {
    "yt-dlp": fetch_via_yt_dlp,
    "api": fetch_via_api,
    "browser": fetch_via_browser,
}


def attempt_provider(
    provider: str,
    *,
    video_id: str,
    source_url: str,
    title: str | None,
    requested_languages: list[str],
    translate_to: str | None,
    preserve_formatting: bool,
    asr_model: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    started = time.monotonic()
    try:
        if provider == "asr":
            result = fetch_via_asr(
                video_id=video_id,
                source_url=source_url,
                title=title,
                requested_languages=requested_languages,
                translate_to=translate_to,
                preserve_formatting=preserve_formatting,
                asr_model=asr_model,
            )
        else:
            result = PROVIDER_FUNCTIONS[provider](
                video_id=video_id,
                source_url=source_url,
                title=title,
                requested_languages=requested_languages,
                translate_to=translate_to,
                preserve_formatting=preserve_formatting,
            )
        quality = quality_summary(result["segments"], result.get("is_generated"))
        attempt = {
            "provider": provider,
            "status": "success",
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "quality": quality,
            "language_code": result.get("language_code"),
            "is_generated": result.get("is_generated"),
            "source_format": result.get("source_format"),
        }
        return attempt, result
    except Exception as exc:
        return (
            {
                "provider": provider,
                "status": "failed",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": describe_error(exc),
            },
            None,
        )


def run_caption_pipeline(
    *,
    video: str,
    requested_languages: list[str],
    providers: list[str],
    translate_to: str | None,
    preserve_formatting: bool,
    chunk_seconds: float,
    asr_model: str,
) -> dict[str, Any]:
    video_id = extract_video_id(video)
    source_url = canonical_url(video_id)
    title = fetch_title(source_url)
    attempts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    selection_reason = ""

    for provider in providers:
        attempt, result = attempt_provider(
            provider,
            video_id=video_id,
            source_url=source_url,
            title=title,
            requested_languages=requested_languages,
            translate_to=translate_to,
            preserve_formatting=preserve_formatting,
            asr_model=asr_model,
        )
        attempts.append(attempt)
        if result is None:
            continue
        candidates.append(result)
        if is_quality_sufficient(result):
            selected = result
            selection_reason = f"Selected {provider} because it produced sufficient caption coverage."
            break

    if selected is None and candidates:
        selected = max(
            candidates,
            key=lambda item: quality_summary(item["segments"], item.get("is_generated"))["score"],
        )
        selection_reason = f"Selected {selected['provider']} as the best available caption result."

    if selected is None:
        raise UserError("All caption providers failed: " + "; ".join(
            f"{attempt['provider']}: {attempt.get('error', 'unknown error')}"
            for attempt in attempts
        ))

    quality = quality_summary(selected["segments"], selected.get("is_generated"))
    chunks = build_chunks(selected["segments"], chunk_seconds, selected["provider"])
    notes = list(selected.get("notes", []))
    if selected.get("is_generated"):
        notes.append("Selected caption is generated or ASR-derived; review important claims carefully.")
    if selected.get("used_language_fallback"):
        notes.append("Requested caption language was unavailable, so a fallback language was selected.")

    return {
        "schema_version": "caption.v2",
        "video": {
            "video_id": video_id,
            "source_url": source_url,
            "title": selected.get("title") or title,
            "duration_seconds": selected.get("duration_seconds"),
        },
        "requested": {
            "languages": requested_languages,
            "translate_to": translate_to,
            "providers": providers,
            "chunk_seconds": chunk_seconds,
        },
        "attempts": attempts,
        "selected_result": {
            key: selected[key]
            for key in (
                "provider",
                "language_code",
                "is_generated",
                "used_language_fallback",
                "translated_to",
                "source_format",
                "segment_count",
                "duration_seconds",
                "chapters",
                "text",
                "segments",
                "raw_metadata",
            )
            if key in selected
        },
        "chunks": chunks,
        "selection": {
            "provider": selected["provider"],
            "reason": selection_reason,
            "quality": quality,
        },
        "notes": notes,
    }


def render_output(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "text":
        return payload["selected_result"]["text"]
    if output_format == "segments":
        return json.dumps(payload["selected_result"]["segments"], ensure_ascii=False, indent=2)
    if output_format == "chunks":
        return json.dumps(payload["chunks"], ensure_ascii=False, indent=2)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def write_output(rendered: str, output_path: str | None) -> None:
    if output_path:
        Path(output_path).expanduser().write_text(rendered + "\n", encoding="utf-8")
        return
    print(rendered)


def describe_error(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


def main() -> int:
    args = parse_args()
    try:
        payload = run_caption_pipeline(
            video=args.video,
            requested_languages=parse_languages(args.langs),
            providers=parse_providers(args.providers, args.strategy),
            translate_to=args.translate_to,
            preserve_formatting=args.preserve_formatting,
            chunk_seconds=args.chunk_seconds,
            asr_model=args.asr_model,
        )
        write_output(render_output(payload, args.format), args.output)
        return 0
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"Error: Failed to install caption dependency ({exc}).", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("Error: Caption fetch timed out.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {describe_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
