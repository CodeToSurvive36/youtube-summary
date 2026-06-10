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
    codex_path = ensure_codex_cli()
    transcript_temp: Path | None = None

    try:
        if args.transcript_output:
            transcript_path = Path(args.transcript_output).expanduser()
        else:
            with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
                transcript_path = Path(handle.name)
            transcript_temp = transcript_path

        transcript_payload = run_fetch_step(args, transcript_path)
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


if __name__ == "__main__":
    raise SystemExit(main())
