#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
FETCH_SCRIPT = SCRIPT_DIR / "fetch_youtube_transcript.py"
STRUCTURED_SCHEMA = SCRIPT_DIR / "structured_summary.schema.json"
POST_SCHEMA = SCRIPT_DIR / "xiaohongshu_post.schema.json"
DEFAULT_LANGS = "en,en-US,en-GB,zh-Hans,zh-CN,zh-Hant,zh-TW,zh-HK,zh"
BUNDLED_CODEX_CANDIDATES = [
    Path("/Applications/Codex.app/Contents/Resources/codex"),
]


class UserError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch a YouTube transcript and generate a Xiaohongshu Markdown post.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("video", help="A YouTube URL or raw 11-character video ID.")
    parser.add_argument(
        "--langs",
        default=DEFAULT_LANGS,
        help="Preferred transcript languages passed through to the fetch step.",
    )
    parser.add_argument(
        "--transcript-input",
        help="Optional existing caption.v2 transcript JSON path. When provided, fetch is skipped.",
    )
    parser.add_argument(
        "--raw-transcript-output",
        help="Optional path to save the raw caption.v2 transcript JSON.",
    )
    parser.add_argument(
        "--cleaned-transcript-output",
        help="Optional path to save the cleaned transcript artifact JSON.",
    )
    parser.add_argument(
        "--structured-summary-output",
        help="Optional path to save the structured summary JSON.",
    )
    parser.add_argument(
        "--chunk-summaries-output",
        help="Optional path to save per-chunk structured summaries for long transcripts.",
    )
    parser.add_argument(
        "--json-output",
        help="Optional path to save the final Xiaohongshu post JSON.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to save the final Xiaohongshu Markdown post. Defaults to stdout.",
    )
    parser.add_argument(
        "--model",
        help="Optional Codex model override for generation steps.",
    )
    parser.add_argument(
        "--reasoning-effort",
        default="low",
        help="Codex model_reasoning_effort override for generation steps.",
    )
    parser.add_argument(
        "--long-transcript-threshold",
        type=int,
        default=20_000,
        help="Use chunked extraction when cleaned transcript text exceeds this many characters.",
    )
    parser.add_argument(
        "--chunk-group-seconds",
        type=float,
        default=600.0,
        help="Maximum transcript seconds to combine into one long-video extraction chunk.",
    )
    parser.add_argument(
        "--chunk-group-max-chars",
        type=int,
        default=20_000,
        help="Maximum transcript characters to combine into one long-video extraction chunk.",
    )
    parser.add_argument(
        "--merge-batch-size",
        type=int,
        default=2,
        help="Maximum number of chunk summaries to merge in one Codex step.",
    )
    return parser.parse_args()


def codex_candidate_works(candidate: str | Path) -> bool:
    try:
        completed = subprocess.run(
            [str(candidate), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def ensure_codex_cli() -> str:
    candidates: list[Path] = []
    path_candidate = shutil.which("codex")
    if path_candidate:
        candidates.append(Path(path_candidate))
    candidates.extend(BUNDLED_CODEX_CANDIDATES)

    checked: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        expanded = Path(candidate).expanduser()
        key = str(expanded)
        if key in seen:
            continue
        seen.add(key)
        checked.append(key)
        if codex_candidate_works(expanded):
            return key

    checked_detail = ", ".join(checked) if checked else "none"
    raise UserError(
        "`codex` CLI is required for Xiaohongshu generation but no working CLI was found. "
        f"Checked: {checked_detail}."
    )


def numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UserError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UserError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise UserError(f"Expected a JSON object in {path}.")
    return payload


def write_json(path: str | Path | None, payload: dict[str, Any] | list[Any]) -> None:
    if not path:
        return
    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_text(path: str | Path | None, text: str) -> None:
    if path:
        output_path = Path(path).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


def run_fetch_step(args: argparse.Namespace, transcript_path: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(FETCH_SCRIPT),
        args.video,
        "--langs",
        args.langs,
        "--output",
        str(transcript_path),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise UserError(message or "Transcript fetch step failed.")
    return load_json(transcript_path)


def compact_text(value: str) -> str:
    lines = []
    for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def selected_result(caption_payload: dict[str, Any]) -> dict[str, Any]:
    selected = caption_payload.get("selected_result")
    if isinstance(selected, dict):
        return selected
    return caption_payload


def metadata_value(*sources: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def text_from_segments(segments: Any) -> str:
    if not isinstance(segments, list):
        return ""
    return "\n".join(
        str(segment.get("text", "")).strip()
        for segment in segments
        if isinstance(segment, dict) and str(segment.get("text", "")).strip()
    )


def normalize_chunks(chunks: Any) -> list[dict[str, Any]]:
    if not isinstance(chunks, list):
        return []
    normalized = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        text = compact_text(str(chunk.get("text", "")))
        if not text:
            continue
        item = dict(chunk)
        item["text"] = text
        normalized.append(item)
    return normalized


def build_cleaned_transcript_artifact(caption_payload: dict[str, Any]) -> dict[str, Any]:
    video = caption_payload.get("video") if isinstance(caption_payload.get("video"), dict) else {}
    selected = selected_result(caption_payload)
    raw_metadata = selected.get("raw_metadata") if isinstance(selected.get("raw_metadata"), dict) else {}

    transcript = selected.get("text")
    if not isinstance(transcript, str) or not transcript.strip():
        transcript = text_from_segments(selected.get("segments"))

    return {
        "schema_version": "xiaohongshu.cleaned_transcript.v1",
        "video_id": metadata_value(video, caption_payload, keys=("video_id",)),
        "video_title": metadata_value(video, selected, caption_payload, keys=("title",)) or "",
        "video_url": metadata_value(video, selected, caption_payload, keys=("source_url", "url")) or "",
        "channel_name": metadata_value(
            raw_metadata,
            video,
            selected,
            keys=("channel", "channel_name", "uploader", "author"),
        ),
        "video_description": metadata_value(
            raw_metadata,
            video,
            selected,
            keys=("description", "full_description", "video_description"),
        ),
        "chapters": selected.get("chapters") if isinstance(selected.get("chapters"), list) else [],
        "transcript": compact_text(str(transcript or "")),
        "chunks": normalize_chunks(caption_payload.get("chunks")),
        "notes": caption_payload.get("notes") if isinstance(caption_payload.get("notes"), list) else [],
    }


def should_chunk_transcript(transcript: str, threshold: int) -> bool:
    return len(transcript) > threshold


def group_transcript_chunks(
    chunks: list[dict[str, Any]],
    *,
    max_seconds: float,
    max_chars: int,
) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_start: float | None = None
    current_end: float | None = None
    current_chars = 0

    def flush_current() -> None:
        nonlocal current, current_start, current_end, current_chars
        if not current:
            return
        texts = [str(chunk["text"]).strip() for chunk in current if str(chunk.get("text", "")).strip()]
        first = current[0]
        grouped.append(
            {
                "start": current_start,
                "end": current_end,
                "timestamp": first.get("timestamp"),
                "text": "\n".join(texts),
                "chunk_count": len(current),
            }
        )
        current = []
        current_start = None
        current_end = None
        current_chars = 0

    for raw_chunk in chunks:
        text = compact_text(str(raw_chunk.get("text", "")))
        if not text:
            continue
        chunk = dict(raw_chunk)
        chunk["text"] = text
        start = numeric_value(chunk.get("start"))
        end = numeric_value(chunk.get("end"))
        if start is None:
            start = current_end if current_end is not None else 0.0
        if end is None or end < start:
            end = start

        next_chars = current_chars + len(text) + (1 if current else 0)
        exceeds_seconds = bool(current and current_start is not None and end - current_start > max_seconds)
        exceeds_chars = bool(current and next_chars > max_chars)
        if exceeds_seconds or exceeds_chars:
            flush_current()

        current.append(chunk)
        if current_start is None:
            current_start = start
        current_end = end if current_end is None else max(current_end, end)
        current_chars += len(text) + (1 if len(current) > 1 else 0)

    flush_current()
    return grouped


def build_structured_summary_prompt(cleaned_transcript_path: Path) -> str:
    return (
        "你是一个视频内容分析助手。请读取这个 cleaned transcript JSON 文件："
        f'"{cleaned_transcript_path}"。'
        "只根据其中的视频标题、视频链接、频道名、描述、章节、字幕和 notes 提取结构化信息。"
        "要求：1. 只基于输入内容，不得补充外部知识。"
        "2. 删除寒暄、口误、重复表达。"
        "3. 保留核心观点、论证过程、案例、数据、结论。"
        "4. 如果信息不确定或证据不足，写入 `insufficient_evidence`，不要猜测。"
        "5. 返回严格匹配输出 schema 的 JSON。"
    )


def build_chunk_summary_prompt(chunk_path: Path) -> str:
    return (
        "你是一个视频内容分析助手。请读取这个单段字幕 JSON 文件："
        f'"{chunk_path}"。'
        "请总结这一段字幕，只提取这一段中的核心观点、例子、数据和结论。"
        "不要写最终文案，不要补充外部信息。"
        "如果这一段证据不足，把不适合确定表达的内容写入 `insufficient_evidence`。"
        "返回严格匹配输出 schema 的 JSON。"
    )


def build_merge_summary_prompt(chunk_summaries_path: Path) -> str:
    return (
        "你是一个视频内容分析助手。请读取这个分段结构化摘要 JSON 文件："
        f'"{chunk_summaries_path}"。'
        "把所有分段摘要合并成一个完整视频的结构化摘要。"
        "合并时去重相同观点，保留关键论据、例子、数据、结构大纲和 3 个 takeaway。"
        "不得新增分段摘要里没有的信息。"
        "返回严格匹配输出 schema 的 JSON。"
    )


def merge_chunk_summaries(
    *,
    codex_path: str,
    chunk_summaries: list[dict[str, Any]],
    temp_dir: Path,
    model: str | None,
    merge_batch_size: int,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    if not chunk_summaries:
        raise UserError("Cannot merge an empty chunk summary list.")
    if merge_batch_size < 2:
        raise UserError("--merge-batch-size must be at least 2.")
    if len(chunk_summaries) == 1:
        return chunk_summaries[0]

    level = chunk_summaries
    level_index = 1
    while len(level) > 1:
        if len(level) <= merge_batch_size:
            merge_path = temp_dir / f"merge-level-{level_index:02d}-final.json"
            write_json(merge_path, level)
            return run_codex_json_step(
                codex_path=codex_path,
                prompt=build_merge_summary_prompt(merge_path),
                schema_path=STRUCTURED_SCHEMA,
                readable_paths=[merge_path],
                model=model,
                reasoning_effort=reasoning_effort,
            )

        next_level = []
        for batch_index, start in enumerate(range(0, len(level), merge_batch_size), start=1):
            batch = level[start : start + merge_batch_size]
            if len(batch) == 1:
                next_level.append(batch[0])
                continue
            merge_path = temp_dir / f"merge-level-{level_index:02d}-batch-{batch_index:03d}.json"
            write_json(merge_path, batch)
            next_level.append(
                run_codex_json_step(
                    codex_path=codex_path,
                    prompt=build_merge_summary_prompt(merge_path),
                    schema_path=STRUCTURED_SCHEMA,
                    readable_paths=[merge_path],
                    model=model,
                    reasoning_effort=reasoning_effort,
                )
            )
        level = next_level
        level_index += 1

    return level[0]


def build_xiaohongshu_prompt(structured_summary_path: Path) -> str:
    return (
        "你是一个中文内容编辑，擅长把英文 YouTube 访谈/播客内容整理成适合小红书发布的中文知识笔记。"
        "请读取这个 structured_summary JSON 文件："
        f'"{structured_summary_path}"。'
        "任务：根据视频标题、频道、嘉宾、描述、章节和字幕摘要，生成一篇客观风格、可直接发布的小红书图文笔记。"
        "内容定位：这不是个人观后感，不使用“我”。"
        "这是一篇基于原视频字幕的翻译、整理和客观解读。"
        "读者可能是产品经理、设计师、工程师、创业者、知识工作者。"
        "他们关心的是：这条视频里的观点，对自己的工作方式和职业能力有什么启发。"
        "必须遵守：1. 正文不得使用“我”“我们”“个人觉得”“看完之后”等第一人称表达。"
        "2. 必须在正文开头或结尾说明来源，使用类似表达："
        "“以下内容基于 YouTube 视频《视频标题》的字幕整理与翻译，不是逐字稿。”"
        "3. 正文中必须出现 2-4 个原视频锚点，例如视频标题、嘉宾姓名、公司名、具体案例、关键概念或说法。"
        "4. 只能基于输入内容写作，不得编造原视频没有的信息，不得新增结构化摘要里没有的信息。"
        "5. 不要写成机械摘要，也不要写成个人心得。"
        "6. 不要频繁写或重复“视频提到”“嘉宾认为”，但每个关键判断要能看出来自原视频。"
        "7. 可以做中文语境下的解释，但必须保持客观表达，例如“可以理解为……”“这意味着……”"
        "“对应到产品和设计工作中……”。"
        "8. 不使用夸张标题党，不写“封神、必看、全网最全、狠狠收藏”等表达。"
        "9. 不写强互动引导。10. 最终文案不输出事实检查表。"
        "负面示例约束：不要生成这种风格："
        "“我看完最大的感受是……”"
        "“个人觉得这期视频最打动我的地方是……”"
        "“这期视频聊的是：AI 进入产品和知识工作之后……”"
        "这类写法太像摘要报告，不像小红书笔记。"
        "应该改成类似："
        "“以下内容基于 YouTube 视频《视频标题》的字幕整理与翻译，不是逐字稿。”"
        "“原视频讨论的是；其中的概念可以理解为；对应到中文职场语境中意味着……”"
        "输出必须是严格 JSON 对象，只能包含这些字段名："
        "`title_groups`、`cover_lines`、`body`、`tags`、`fact_check`。"
        "`title_groups` 必须包含 `pain_point`、`counterintuitive`、`audience`、`viewpoint` 四组；"
        "它们分别对应痛点型、反常识型、人群型、观点型，每组 2 个标题，每个标题不超过 24 个中文字符。"
        "`cover_lines` 必须是 5 个封面文案，每个不超过 12 个中文字符，要求直接、具体、有冲突感。"
        "`body` 必须是 600-700 个中文字符正文，宁可短，不要超过 700；"
        "正文固定写成：来源句 + 4 个小标题段 + 结尾句。"
        "来源句 60-90 字，4 个小标题段每段 70-110 字，结尾句 60-90 字。"
        "每个小标题下解释一个核心观点；结尾总结这期视频最值得带走的判断。"
        "正文不使用第一人称，多用“可以理解为……”“这意味着……”“对应到产品和设计工作中……”。"
        "`tags` 必须是 8-12 个标签；"
        "`fact_check` 是内部质量校验字段，必须是 5-8 个对象，不能是字符串数组，但最终 Markdown 不会展示它；"
        "每个对象只能包含 `claim`、`source_type`、`source_detail` 三个字段。"
        "`claim` 写文案使用的信息点，`source_type` 只能是 字幕/标题/描述/章节/结构化摘要，"
        "`source_detail` 写对应来源说明。"
    )


def run_codex_json_step(
    *,
    codex_path: str,
    prompt: str,
    schema_path: Path,
    readable_paths: list[Path],
    model: str | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
        output_path = Path(handle.name)

    add_dirs = []
    for path in readable_paths:
        parent = str(path.expanduser().resolve().parent)
        if parent not in add_dirs:
            add_dirs.append(parent)

    command = [
        codex_path,
        "exec",
        "--ephemeral",
        "--color",
        "never",
        "--skip-git-repo-check",
        "-C",
        str(Path.cwd()),
    ]
    for directory in add_dirs:
        command.extend(["--add-dir", directory])
    if reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    command.extend(["--output-schema", str(schema_path), "-o", str(output_path)])
    if model:
        command.extend(["--model", model])
    command.append(prompt)

    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise UserError(detail or "Codex generation step failed.")

    try:
        return load_json(output_path)
    finally:
        output_path.unlink(missing_ok=True)


def generate_structured_summary(
    *,
    codex_path: str,
    cleaned: dict[str, Any],
    cleaned_path: Path,
    threshold: int,
    model: str | None,
    chunk_summaries_output: str | None,
    chunk_group_seconds: float,
    chunk_group_max_chars: int,
    merge_batch_size: int,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    transcript = str(cleaned.get("transcript", ""))
    chunks = cleaned.get("chunks") if isinstance(cleaned.get("chunks"), list) else []
    if not should_chunk_transcript(transcript, threshold) or not chunks:
        return run_codex_json_step(
            codex_path=codex_path,
            prompt=build_structured_summary_prompt(cleaned_path),
            schema_path=STRUCTURED_SCHEMA,
            readable_paths=[cleaned_path],
            model=model,
            reasoning_effort=reasoning_effort,
        )

    with tempfile.TemporaryDirectory(prefix="xiaohongshu-chunks-") as temp_name:
        temp_dir = Path(temp_name)
        chunk_summaries = []
        chunk_groups = group_transcript_chunks(
            chunks,
            max_seconds=chunk_group_seconds,
            max_chars=chunk_group_max_chars,
        )
        for index, chunk in enumerate(chunk_groups, start=1):
            chunk_payload = {
                "video_title": cleaned.get("video_title"),
                "video_url": cleaned.get("video_url"),
                "channel_name": cleaned.get("channel_name"),
                "video_description": cleaned.get("video_description"),
                "chunk_index": index,
                "chunk_count": len(chunk_groups),
                "chunk": chunk,
                "notes": cleaned.get("notes", []),
            }
            chunk_path = temp_dir / f"chunk-{index:03d}.json"
            write_json(chunk_path, chunk_payload)
            chunk_summaries.append(
                run_codex_json_step(
                    codex_path=codex_path,
                    prompt=build_chunk_summary_prompt(chunk_path),
                    schema_path=STRUCTURED_SCHEMA,
                    readable_paths=[chunk_path],
                    model=model,
                    reasoning_effort=reasoning_effort,
                )
            )

        write_json(chunk_summaries_output, chunk_summaries)
        return merge_chunk_summaries(
            codex_path=codex_path,
            chunk_summaries=chunk_summaries,
            temp_dir=temp_dir,
            model=model,
            merge_batch_size=merge_batch_size,
            reasoning_effort=reasoning_effort,
        )


def render_markdown_post(post_payload: dict[str, Any]) -> str:
    required_fields = ("title_groups", "cover_lines", "body", "tags", "fact_check")
    missing_fields = [field for field in required_fields if field not in post_payload]
    if missing_fields:
        raise UserError(f"Final Xiaohongshu JSON is missing required fields: {', '.join(missing_fields)}")

    title_groups = post_payload["title_groups"]
    if not isinstance(title_groups, dict):
        raise UserError("Final Xiaohongshu JSON field `title_groups` must be an object.")
    title_group_labels = [
        ("pain_point", "痛点型"),
        ("counterintuitive", "反常识型"),
        ("audience", "人群型"),
        ("viewpoint", "观点型"),
    ]
    for key, _label in title_group_labels:
        values = title_groups.get(key)
        if not isinstance(values, list) or len(values) != 2:
            raise UserError(f"Final Xiaohongshu JSON field `title_groups.{key}` must contain 2 titles.")
        for title in values:
            title_text = str(title).strip()
            if not title_text or len(title_text) > 24:
                raise UserError(f"Final Xiaohongshu JSON field `title_groups.{key}` has an invalid title.")

    cover_lines = post_payload["cover_lines"]
    if not isinstance(cover_lines, list) or len(cover_lines) != 5:
        raise UserError("Final Xiaohongshu JSON field `cover_lines` must contain 5 lines.")
    for cover_line in cover_lines:
        cover_text = str(cover_line).strip()
        if not cover_text or len(cover_text) > 12:
            raise UserError("Final Xiaohongshu JSON field `cover_lines` has an invalid line.")

    body = post_payload["body"]
    if not isinstance(body, str):
        raise UserError("Final Xiaohongshu JSON field `body` must be a string.")
    body_length = len(body.strip())
    if body_length < 600 or body_length > 900:
        raise UserError(
            "Final Xiaohongshu JSON field `body` must be 600-900 characters; "
            f"got {body_length}."
        )
    if "以下内容基于 YouTube 视频" not in body or "不是逐字稿" not in body:
        raise UserError(
            "Final Xiaohongshu JSON field `body` must include an objective source note."
        )
    first_person_markers = ("我", "我们", "个人觉得", "看完之后")
    if any(marker in body for marker in first_person_markers):
        raise UserError("Final Xiaohongshu JSON field `body` must not use first-person wording.")

    if not isinstance(post_payload["fact_check"], list):
        raise UserError("Final Xiaohongshu JSON field `fact_check` must be a list of objects.")
    for index, item in enumerate(post_payload["fact_check"], start=1):
        if not isinstance(item, dict):
            raise UserError(f"Final Xiaohongshu JSON field `fact_check[{index}]` must be an object.")
        missing_fact_fields = [
            field for field in ("claim", "source_type", "source_detail") if field not in item
        ]
        if missing_fact_fields:
            raise UserError(
                f"Final Xiaohongshu JSON field `fact_check[{index}]` is missing: "
                f"{', '.join(missing_fact_fields)}"
            )

    lines = ["【标题】"]
    for key, label in title_group_labels:
        lines.append(label)
        for index, title in enumerate(title_groups[key], start=1):
            lines.append(f"{index}. {str(title).strip()}")

    lines.extend(["", "【封面文案】"])
    for index, cover_line in enumerate(cover_lines, start=1):
        lines.append(f"{index}. {str(cover_line).strip()}")

    lines.extend(["", "【正文】", str(post_payload["body"]).strip(), "", "【标签】"])
    lines.append(" ".join(f"#{str(tag).strip().lstrip('#')}" for tag in post_payload["tags"]))

    return "\n".join(lines).strip() + "\n"


def main() -> int:
    args = parse_args()
    codex_path = ensure_codex_cli()
    temp_paths: list[Path] = []

    try:
        if args.transcript_input:
            raw_path = Path(args.transcript_input).expanduser()
            raw_transcript = load_json(raw_path)
            write_json(args.raw_transcript_output, raw_transcript)
        elif args.raw_transcript_output:
            raw_path = Path(args.raw_transcript_output).expanduser()
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_transcript = run_fetch_step(args, raw_path)
        else:
            with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
                raw_path = Path(handle.name)
            temp_paths.append(raw_path)
            raw_transcript = run_fetch_step(args, raw_path)

        cleaned = build_cleaned_transcript_artifact(raw_transcript)
        if args.cleaned_transcript_output:
            cleaned_path = Path(args.cleaned_transcript_output).expanduser()
        else:
            with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
                cleaned_path = Path(handle.name)
            temp_paths.append(cleaned_path)
        write_json(cleaned_path, cleaned)

        structured_summary = generate_structured_summary(
            codex_path=codex_path,
            cleaned=cleaned,
            cleaned_path=cleaned_path,
            threshold=args.long_transcript_threshold,
            model=args.model,
            chunk_summaries_output=args.chunk_summaries_output,
            chunk_group_seconds=args.chunk_group_seconds,
            chunk_group_max_chars=args.chunk_group_max_chars,
            merge_batch_size=args.merge_batch_size,
            reasoning_effort=args.reasoning_effort,
        )

        if args.structured_summary_output:
            structured_path = Path(args.structured_summary_output).expanduser()
        else:
            with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as handle:
                structured_path = Path(handle.name)
            temp_paths.append(structured_path)
        write_json(structured_path, structured_summary)

        post_payload = run_codex_json_step(
            codex_path=codex_path,
            prompt=build_xiaohongshu_prompt(structured_path),
            schema_path=POST_SCHEMA,
            readable_paths=[structured_path],
            model=args.model,
            reasoning_effort=args.reasoning_effort,
        )
        markdown = render_markdown_post(post_payload)

        write_json(args.json_output, post_payload)
        write_text(args.output, markdown)
        return 0
    except UserError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {str(exc).strip() or exc.__class__.__name__}", file=sys.stderr)
        return 1
    finally:
        for path in temp_paths:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
