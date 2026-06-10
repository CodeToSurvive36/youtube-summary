#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
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
PACKAGE_SPEC = "youtube-transcript-api>=1.2.0,<2"
VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"
INSTALL_LOCK_PATH = VENDOR_DIR.parent / ".vendor-install.lock"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
CHAPTER_RE = re.compile(r"^Chapter\s+\d+:\s+(.+)$")
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
        description="Fetch a YouTube transcript and normalize it into text or JSON.",
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
        help="Optional language code to translate the fetched transcript into.",
    )
    parser.add_argument(
        "--strategy",
        choices=("auto", "api", "browser"),
        default="auto",
        help="Transcript fetch strategy. 'auto' prefers the browser transcript panel first, then falls back to the direct API path.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text", "segments"),
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


def ensure_dependency() -> Any:
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))

    try:
        return importlib.import_module("youtube_transcript_api")
    except ImportError:
        with vendor_install_lock():
            try:
                importlib.invalidate_caches()
                return importlib.import_module("youtube_transcript_api")
            except ImportError:
                VENDOR_DIR.mkdir(parents=True, exist_ok=True)
                print(
                    "Installing youtube-transcript-api into scripts/_vendor ...",
                    file=sys.stderr,
                )
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
                        PACKAGE_SPEC,
                    ],
                    check=True,
                )
                importlib.invalidate_caches()
                if str(VENDOR_DIR) not in sys.path:
                    sys.path.insert(0, str(VENDOR_DIR))
                return importlib.import_module("youtube_transcript_api")


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
                    raise UserError(
                        "Timed out waiting for the transcript dependency install lock."
                    )
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


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


def normalize_segments(raw_segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in raw_segments:
        text = str(raw.get("text", "")).strip()
        start = float(raw.get("start", 0.0))
        duration = float(raw.get("duration", 0.0))
        if not text:
            continue
        normalized.append(
            {
                "text": text,
                "start": start,
                "duration": duration,
                "end": round(start + duration, 2),
                "timestamp": format_timestamp(start),
            }
        )
    return normalized


def finalize_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index, segment in enumerate(segments):
        if "duration" not in segment or "end" not in segment:
            if index + 1 < len(segments):
                duration = max(segments[index + 1]["start"] - segment["start"], 0.0)
            else:
                duration = 0.0
            segment["duration"] = round(duration, 2)
            segment["end"] = round(segment["start"] + segment["duration"], 2)
    return segments


def join_transcript_text(segments: list[dict[str, Any]]) -> str:
    return "\n".join(segment["text"] for segment in segments)


def build_result(
    *,
    video_id: str,
    source_url: str,
    title: str | None,
    requested_languages: list[str],
    segments: list[dict[str, Any]],
    transcript_language: str | None,
    transcript_language_code: str | None,
    is_generated: bool | None,
    used_language_fallback: bool,
    translated_to: str | None,
    available_transcripts: list[dict[str, Any]],
    strategy_used: str,
    chapters: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
    page_language_code: str | None = None,
) -> dict[str, Any]:
    segments = finalize_segments(segments)
    duration_seconds = round(
        max((segment["end"] for segment in segments), default=0.0),
        2,
    )
    return {
        "video_id": video_id,
        "source_url": source_url,
        "title": title,
        "requested_languages": requested_languages,
        "transcript_language": transcript_language,
        "transcript_language_code": transcript_language_code,
        "page_language_code": page_language_code,
        "is_generated": is_generated,
        "used_language_fallback": used_language_fallback,
        "translated_to": translated_to,
        "segment_count": len(segments),
        "duration_seconds": duration_seconds,
        "available_transcripts": available_transcripts,
        "strategy_used": strategy_used,
        "chapters": chapters or [],
        "notes": notes or [],
        "text": join_transcript_text(segments),
        "segments": segments,
    }


def fetch_via_api(
    *,
    video_id: str,
    source_url: str,
    title: str | None,
    requested_languages: list[str],
    translate_to: str | None,
    preserve_formatting: bool,
    prior_error: Exception | None = None,
) -> dict[str, Any]:
    yta = ensure_dependency()
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
    notes: list[str] = []
    if prior_error is not None:
        notes.append(summarize_fetch_error("Browser transcript extraction", prior_error))
    return build_result(
        video_id=video_id,
        source_url=source_url,
        title=title,
        requested_languages=requested_languages,
        segments=normalize_segments(fetched_transcript.to_raw_data()),
        transcript_language=fetched_transcript.language,
        transcript_language_code=fetched_transcript.language_code,
        is_generated=fetched_transcript.is_generated,
        used_language_fallback=used_language_fallback,
        translated_to=translate_to,
        available_transcripts=available_transcripts,
        strategy_used="youtube_transcript_api",
        notes=notes,
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
        for marker in ("\nSync to video time", "\nAutoplay\n", "\nUp next\n")
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

    return segments, chapters


def fetch_via_browser(
    *,
    video_id: str,
    source_url: str,
    title: str | None,
    requested_languages: list[str],
    translate_to: str | None,
    prior_error: Exception | None,
) -> dict[str, Any]:
    command = playwright_cli_command()
    notes: list[str] = [
        "Transcript was extracted from the YouTube transcript panel in a real browser."
    ]
    if prior_error is not None:
        notes.append(summarize_fetch_error("Direct API transcript fetch", prior_error))
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

    resolved_title = title or capture.get("title")
    return build_result(
        video_id=video_id,
        source_url=source_url,
        title=resolved_title,
        requested_languages=requested_languages,
        segments=segments,
        transcript_language=None,
        transcript_language_code=None,
        page_language_code=capture.get("htmlLang"),
        is_generated=None,
        used_language_fallback=False,
        translated_to=None,
        available_transcripts=[],
        strategy_used="playwright-transcript-panel",
        chapters=chapters,
        notes=notes,
    )


def render_output(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "text":
        return payload["text"]
    if output_format == "segments":
        return json.dumps(payload["segments"], ensure_ascii=False, indent=2)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def write_output(rendered: str, output_path: str | None) -> None:
    if output_path:
        Path(output_path).expanduser().write_text(rendered + "\n", encoding="utf-8")
        return
    print(rendered)


def describe_error(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__


def summarize_fetch_error(prefix: str, exc: Exception) -> str:
    message = describe_error(exc)
    first_line = next((line.strip() for line in message.splitlines() if line.strip()), message)
    return f"{prefix} failed first: {first_line}"


def main() -> int:
    args = parse_args()
    requested_languages = parse_languages(args.langs)

    try:
        video_id = extract_video_id(args.video)
        source_url = canonical_url(video_id)
        title = fetch_title(source_url)

        payload: dict[str, Any]
        browser_error: Exception | None = None
        api_error: Exception | None = None

        if args.strategy in {"auto", "browser"}:
            try:
                payload = fetch_via_browser(
                    video_id=video_id,
                    source_url=source_url,
                    title=title,
                    requested_languages=requested_languages,
                    translate_to=args.translate_to,
                    prior_error=None,
                )
                write_output(render_output(payload, args.format), args.output)
                return 0
            except Exception as exc:
                browser_error = exc
                if args.strategy == "browser":
                    raise

        if args.strategy in {"auto", "api"}:
            try:
                payload = fetch_via_api(
                    video_id=video_id,
                    source_url=source_url,
                    title=title,
                    requested_languages=requested_languages,
                    translate_to=args.translate_to,
                    preserve_formatting=args.preserve_formatting,
                    prior_error=browser_error,
                )
                write_output(render_output(payload, args.format), args.output)
                return 0
            except Exception as exc:
                api_error = exc
                if args.strategy == "api":
                    raise

        if browser_error is not None:
            raise browser_error
        if api_error is not None:
            raise api_error
        raise UserError("Transcript fetch failed before any strategy could return a result.")

    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(
            f"Error: Failed to install transcript dependency ({exc}).",
            file=sys.stderr,
        )
        return 1
    except subprocess.TimeoutExpired:
        print("Error: Transcript fetch timed out.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {describe_error(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
