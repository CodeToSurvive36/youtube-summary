# Xiaohongshu Post Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit Xiaohongshu Markdown post generator for YouTube transcript summaries.

**Architecture:** Keep the existing default Chinese summary flow unchanged. Add a separate `youtube_to_xiaohongshu_post.py` CLI that reuses `caption.v2` transcript artifacts, runs a two-stage Codex generation flow, saves intermediate artifacts, and renders final Markdown.

**Tech Stack:** Python 3 standard library, Codex CLI `exec --output-schema`, JSON Schema files, `unittest`.

---

## File Structure

- Create `scripts/youtube_to_xiaohongshu_post.py`: dedicated CLI, prompt builders, artifact helpers, Codex step orchestration, Markdown renderer.
- Create `scripts/structured_summary.schema.json`: output contract for transcript structure extraction.
- Create `scripts/xiaohongshu_post.schema.json`: output contract for final Xiaohongshu post JSON.
- Create `tests/test_youtube_to_xiaohongshu_post.py`: TDD coverage for trigger documentation, prompt separation, long transcript routing, artifact cleaning, schema shape, and Markdown rendering.
- Modify `SKILL.md`: document that Xiaohongshu generation only applies when the user explicitly asks for Xiaohongshu copy/post/note output.
- Modify `README.md`: add concise Xiaohongshu Markdown output usage.

## Tasks

### Task 1: Tests First

- [x] Add failing tests in `tests/test_youtube_to_xiaohongshu_post.py` for:
  - final rewrite prompt uses `structured_summary` and does not include full transcript text;
  - cleaned transcript artifact contains video metadata, chapters, text, chunks, and notes;
  - long transcript threshold selects chunked extraction;
  - Markdown output contains `【标题候选】`, `【封面文案】`, `【正文】`, `【适合标签】`, `【事实检查】`;
  - JSON schemas encode fixed/min/max counts for titles, cover lines, tags, and fact checks.
- [x] Run the new test file and confirm failures are caused by the missing implementation.

### Task 2: Schemas

- [x] Add `structured_summary.schema.json` with fields for one-sentence summary, main question, viewpoints, concepts, facts, outline, takeaways, and insufficient evidence.
- [x] Add `xiaohongshu_post.schema.json` with fields for 5 titles, 3 cover lines, 500-800 character body, 8-12 tags, and 5-8 fact-check rows.
- [x] Run schema-related tests and confirm they pass.

### Task 3: Xiaohongshu CLI

- [x] Add `scripts/youtube_to_xiaohongshu_post.py`.
- [x] Reuse `fetch_youtube_transcript.py` when `--transcript-input` is absent.
- [x] Save optional artifacts for raw transcript, cleaned transcript, structured summary, final JSON, and final Markdown.
- [x] Implement two-stage prompt builders:
  - transcript or chunk extraction to structured summary;
  - structured summary to Xiaohongshu post.
- [x] Implement chunked extraction when cleaned transcript length exceeds `--long-transcript-threshold`.
- [x] Render the final Markdown output.
- [x] Run all tests.

### Task 4: Docs

- [x] Update `SKILL.md` so Xiaohongshu flow is used only for explicit Xiaohongshu requests.
- [x] Update `README.md` with the Markdown output command and explicit-trigger behavior.
- [x] Run all tests and `git diff --check`.
