#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import html
import importlib
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
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
DEPENDENCIES = {
    "youtube_transcript_api": "youtube-transcript-api==1.2.4",
    "yt_dlp": "yt-dlp>=2025.1.0",
}
VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"
INSTALL_LOCK_PATH = VENDOR_DIR.parent / ".vendor-install.lock"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
VTT_TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
_DIRECT_API_INSTANCE: Any | None = None


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


def get_direct_api(yta: Any) -> Any:
    global _DIRECT_API_INSTANCE
    if _DIRECT_API_INSTANCE is None:
        _DIRECT_API_INSTANCE = yta.YouTubeTranscriptApi()
    return _DIRECT_API_INSTANCE


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
        timestamp_match = VTT_TIMESTAMP_RE.search(lines[index].strip())
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
        reasons.append("automatic_caption")
    return {
        "score": min(score, 100),
        "reasons": reasons,
        "segment_count": len(segments),
        "duration_seconds": duration_seconds,
        "text_length": text_length,
    }


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


def choose_yt_dlp_track(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(tracks, list):
        raise UserError("yt-dlp caption tracks must be a list.")
    for extension in ("vtt", "webvtt"):
        for track in tracks:
            if str(track.get("ext", "")).lower() == extension and track.get("url"):
                return dict(track)
    raise UserError("yt-dlp did not expose a supported VTT caption track.")


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


def download_caption_text(track: dict[str, Any]) -> str:
    url = track.get("url")
    if not isinstance(url, str) or not url:
        raise UserError("yt-dlp caption track did not contain a URL.")
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
    if not isinstance(info, dict):
        raise UserError("yt-dlp did not return video caption metadata.")

    track, is_generated, used_language_fallback = choose_yt_dlp_caption(
        info,
        requested_languages,
    )
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
        translated_to=None,
        source_format=str(track.get("ext", "")).lower(),
        notes=["Caption was extracted from a subtitle track exposed by yt-dlp."],
        raw_metadata={
            "format": track.get("ext"),
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
    api = get_direct_api(yta)
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


def map_api_error(exc: Exception) -> UserError:
    error_name = exc.__class__.__name__
    messages = {
        "VideoUnavailable": "YouTube video is unavailable or deleted.",
        "AgeRestricted": "YouTube video requires age verification and cannot be fetched anonymously.",
        "TranscriptsDisabled": "YouTube has no captions enabled for this video.",
        "NoTranscriptFound": "YouTube has no caption track matching the requested languages or any fallback language.",
        "RequestBlocked": "YouTube blocked the direct caption request.",
        "IpBlocked": "YouTube rate-limited or blocked the direct caption request.",
        "YouTubeDataUnparsable": "YouTube returned an unparseable player response.",
        "YouTubeRequestFailed": "The direct YouTube caption request failed.",
        "VideoUnplayable": "YouTube reported that the video cannot be played anonymously.",
        "FailedToCreateConsentCookie": "YouTube consent could not be established through the direct request.",
        "PoTokenRequired": "YouTube requires a token that is unavailable to the direct caption request.",
    }
    if isinstance(exc, UserError):
        return exc
    return UserError(messages.get(error_name, describe_error(exc)))


def build_caption_v2_payload(
    *,
    video_id: str,
    source_url: str,
    title: str | None,
    requested_languages: list[str],
    translate_to: str | None,
    chunk_seconds: float,
    selected: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    quality = quality_summary(selected["segments"], selected.get("is_generated"))
    selected_provider = selected["provider"]
    chunks = build_chunks(selected["segments"], chunk_seconds, selected_provider)
    notes = list(selected.get("notes", []))
    if selected.get("is_generated"):
        notes.append("Selected caption is generated by YouTube; review important claims carefully.")
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
            "providers": ["api", "yt-dlp"],
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
            "provider": selected_provider,
            "reason": f"Selected {selected_provider} as the first provider with usable captions.",
            "quality": quality,
        },
        "notes": notes,
    }


def run_caption_pipeline(
    *,
    video: str,
    requested_languages: list[str],
    translate_to: str | None,
    preserve_formatting: bool,
    chunk_seconds: float,
) -> dict[str, Any]:
    video_id = extract_video_id(video)
    source_url = canonical_url(video_id)
    title = fetch_title(source_url)
    attempts: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    providers = (("api", fetch_via_api), ("yt-dlp", fetch_via_yt_dlp))

    for provider, fetcher in providers:
        started = time.monotonic()
        try:
            result = fetcher(
                video_id=video_id,
                source_url=source_url,
                title=title,
                requested_languages=requested_languages,
                translate_to=translate_to,
                preserve_formatting=preserve_formatting,
            )
            if not result.get("segments") or not str(result.get("text", "")).strip():
                raise UserError(f"{provider} returned no usable caption text.")
        except Exception as exc:
            mapped = map_api_error(exc) if provider == "api" else exc
            attempts.append(
                {
                    "provider": provider,
                    "status": "failed",
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "error": describe_error(mapped),
                }
            )
            continue

        selected = result
        attempts.append(
            {
                "provider": provider,
                "status": "success",
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "quality": quality_summary(result["segments"], result.get("is_generated")),
                "language_code": result.get("language_code"),
                "is_generated": result.get("is_generated"),
                "source_format": result.get("source_format"),
            }
        )
        break

    if selected is None:
        failures = "; ".join(
            f"{attempt['provider']}: {attempt.get('error', 'unknown error')}"
            for attempt in attempts
        )
        raise UserError(f"All caption providers failed: {failures}")

    return build_caption_v2_payload(
        video_id=video_id,
        source_url=source_url,
        title=title,
        requested_languages=requested_languages,
        translate_to=translate_to,
        chunk_seconds=chunk_seconds,
        selected=selected,
        attempts=attempts,
    )


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
            translate_to=args.translate_to,
            preserve_formatting=args.preserve_formatting,
            chunk_seconds=args.chunk_seconds,
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
