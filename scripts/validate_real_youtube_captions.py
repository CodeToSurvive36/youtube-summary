#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
FETCH_SCRIPT = SCRIPT_DIR / "fetch_youtube_transcript.py"
GROUPS = (
    "english_manual",
    "non_english_manual",
    "generated",
    "long",
    "short",
)
EXPECTED_PER_GROUP = 5
EXPECTED_TOTAL = len(GROUPS) * EXPECTED_PER_GROUP
APPROVED_PROVIDERS = {"api", "yt-dlp"}

spec = importlib.util.spec_from_file_location("direct_caption_fetch", FETCH_SCRIPT)
fetch_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(fetch_module)


class UserError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate 25 page-audited YouTube videos through the direct caption network path.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("manifest", help="Page-audited validation manifest JSON.")
    parser.add_argument("--output", required=True, help="Path for machine-readable result JSON.")
    parser.add_argument("--markdown-output", help="Optional path for a Markdown result table.")
    parser.add_argument(
        "--langs",
        default="en,en-US,en-GB,zh-Hans,zh-CN,zh-Hant,zh-TW,zh-HK,zh",
        help="Preferred caption language order passed to the direct caption fetcher.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=15.0,
        help="Delay between distinct validation videos to avoid burst traffic; no retries are performed.",
    )
    parser.add_argument(
        "--continue-on-failure",
        action="store_true",
        help="Continue after a failed video. By default validation stops immediately to avoid compounding rate limits.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse only previously passed results from --output and fetch the remaining videos.",
    )
    return parser.parse_args()


def validate_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("schema_version") != "youtube-caption-validation.v1":
        raise UserError("Manifest schema_version must be youtube-caption-validation.v1.")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or len(entries) != EXPECTED_TOTAL:
        raise UserError(f"Manifest must contain exactly {EXPECTED_TOTAL} entries.")

    normalized: list[dict[str, Any]] = []
    video_ids: list[str] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise UserError(f"Manifest entry {index + 1} must be an object.")
        group = raw.get("group")
        if group not in GROUPS:
            raise UserError(f"Manifest entry {index + 1} has unsupported group '{group}'.")
        if raw.get("page_caption_confirmed") is not True:
            raise UserError(
                f"Manifest entry {index + 1} must be page-caption-confirmed before direct validation."
            )
        if raw.get("page_audit_method") != "youtube_watch_page":
            raise UserError(
                f"Manifest entry {index + 1} must record page_audit_method youtube_watch_page."
            )
        if not str(raw.get("page_audited_at", "")).strip():
            raise UserError(f"Manifest entry {index + 1} must record page_audited_at.")
        url = str(raw.get("url", "")).strip()
        video_id = fetch_module.extract_video_id(url)
        declared_id = str(raw.get("video_id", "")).strip()
        if declared_id != video_id:
            raise UserError(f"Manifest entry {index + 1} video_id does not match its URL.")
        normalized.append(dict(raw))
        video_ids.append(video_id)

    if len(set(video_ids)) != EXPECTED_TOTAL:
        raise UserError(f"All {EXPECTED_TOTAL} validation video IDs must be unique.")
    counts = Counter(entry["group"] for entry in normalized)
    for group in GROUPS:
        if counts[group] != EXPECTED_PER_GROUP:
            raise UserError(f"Validation group '{group}' must contain exactly 5 entries.")
    return normalized


def category_error(group: str, selected: dict[str, Any]) -> str | None:
    provider = selected.get("provider")
    language_code = str(selected.get("language_code") or "").lower()
    is_generated = selected.get("is_generated")
    text = str(selected.get("text") or "")
    segment_count = int(selected.get("segment_count") or 0)
    duration = float(selected.get("duration_seconds") or 0.0)

    if provider not in APPROVED_PROVIDERS:
        return "selected provider was not an approved direct caption provider"
    if not text.strip() or segment_count <= 0:
        return "direct caption result was empty"
    if group == "english_manual" and (is_generated is not False or not language_code.startswith("en")):
        return "result was not an English manually created caption track"
    if group == "non_english_manual" and (
        is_generated is not False or not language_code or language_code.startswith("en")
    ):
        return "result was not a non-English manually created caption track"
    if group == "generated" and is_generated is not True:
        return "result was not marked as YouTube-generated"
    if group == "long" and len(text) <= 20_000:
        return "caption text did not exceed 20,000 characters"
    if group == "short" and (duration <= 0 or duration > 300):
        return "caption duration was not within five minutes"
    return None


def run_validation(
    manifest: dict[str, Any],
    *,
    fetcher: Callable[..., dict[str, Any]] | None = None,
    requested_languages: list[str] | None = None,
    delay_seconds: float = 0.0,
    stop_on_failure: bool = False,
    progress: Callable[[int, dict[str, Any]], None] | None = None,
    existing_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entries = validate_manifest(manifest)
    direct_fetcher = fetcher or fetch_module.run_caption_pipeline
    languages = requested_languages or fetch_module.DEFAULT_LANGS.copy()
    results: list[dict[str, Any]] = []
    reusable_results = {
        result.get("video_id"): result
        for result in (existing_results or [])
        if isinstance(result, dict)
        and result.get("status") == "passed"
        and result.get("provider") in APPROVED_PROVIDERS
    }
    network_attempts = 0
    reused = 0

    for index, entry in enumerate(entries):
        previous = reusable_results.get(entry["video_id"])
        if (
            previous is not None
            and previous.get("group") == entry["group"]
            and previous.get("page_caption_confirmed") is True
        ):
            results.append({**previous, "resumed": True})
            reused += 1
            continue
        if network_attempts and delay_seconds > 0:
            time.sleep(delay_seconds)
        network_attempts += 1
        entry_languages = entry.get("languages")
        if entry_languages is None:
            entry_languages = languages
        elif not isinstance(entry_languages, list) or not all(
            isinstance(item, str) and item.strip() for item in entry_languages
        ):
            raise UserError(f"Manifest entry {entry['video_id']} languages must be a non-empty string list.")
        base = {
            "video_id": entry["video_id"],
            "url": entry["url"],
            "group": entry["group"],
            "page_caption_confirmed": True,
            "page_audited_at": entry["page_audited_at"],
        }
        try:
            payload = direct_fetcher(
                video=entry["url"],
                requested_languages=entry_languages,
                translate_to=None,
                preserve_formatting=False,
                chunk_seconds=90.0,
            )
            selected = payload.get("selected_result") or {}
            mismatch = category_error(entry["group"], selected)
            results.append(
                {
                    **base,
                    "status": "failed" if mismatch else "passed",
                    "error": mismatch,
                    "provider": selected.get("provider"),
                    "language_code": selected.get("language_code"),
                    "is_generated": selected.get("is_generated"),
                    "segment_count": selected.get("segment_count"),
                    "caption_character_count": len(str(selected.get("text") or "")),
                    "duration_seconds": selected.get("duration_seconds"),
                }
            )
        except Exception as exc:
            results.append(
                {
                    **base,
                    "status": "failed",
                    "error": str(exc).strip() or exc.__class__.__name__,
                    "provider": "api",
                }
            )
        if progress is not None:
            progress(index + 1, results[-1])
        if stop_on_failure and results[-1]["status"] == "failed":
            break

    passed = sum(result["status"] == "passed" for result in results)
    failed = sum(result["status"] == "failed" for result in results)
    return {
        "schema_version": "youtube-caption-validation-results.v1",
        "validated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "caption_source": "direct YouTube caption access through api then yt-dlp",
        "summary": {
            "total": EXPECTED_TOTAL,
            "attempted": len(results),
            "network_attempted": network_attempts,
            "resumed": reused,
            "passed": passed,
            "failed": failed,
            "not_attempted": EXPECTED_TOTAL - len(results),
            "all_passed": passed == EXPECTED_TOTAL,
        },
        "results": results,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Direct YouTube Caption Validation",
        "",
        f"Result: {summary['passed']}/{summary['total']} passed",
        "",
        "| Group | Video ID | Page audit | Direct fetch | Language | Generated | Characters | Duration |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for result in payload["results"]:
        lines.append(
            "| {group} | {video_id} | confirmed | {status} | {language} | {generated} | {characters} | {duration} |".format(
                group=result["group"],
                video_id=result["video_id"],
                status=result["status"],
                language=result.get("language_code") or "",
                generated=result.get("is_generated"),
                characters=result.get("caption_character_count", ""),
                duration=result.get("duration_seconds", ""),
            )
        )
        if result.get("error"):
            lines.append(f"\nFailure for `{result['video_id']}`: {result['error']}\n")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    try:
        manifest = json.loads(Path(args.manifest).expanduser().read_text(encoding="utf-8"))
        output_path = Path(args.output).expanduser()
        existing_results = None
        if args.resume and output_path.exists():
            existing_payload = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(existing_payload, dict):
                candidate_results = existing_payload.get("results")
                if isinstance(candidate_results, list):
                    existing_results = candidate_results

        payload = run_validation(
            manifest,
            requested_languages=fetch_module.parse_languages(args.langs),
            delay_seconds=args.delay_seconds,
            stop_on_failure=not args.continue_on_failure,
            progress=lambda index, result: print(
                f"[{index}/{EXPECTED_TOTAL}] {result['video_id']}: {result['status']}",
                flush=True,
            ),
            existing_results=existing_results,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if args.markdown_output:
            markdown_path = Path(args.markdown_output).expanduser()
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render_markdown(payload), encoding="utf-8")
        summary = payload["summary"]
        print(f"Direct caption validation: {summary['passed']}/{summary['total']} passed")
        return 0 if summary["all_passed"] else 1
    except (OSError, json.JSONDecodeError, UserError) as exc:
        print(f"Error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
