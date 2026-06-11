---
name: youtube-caption-summary
description: Fetch captions or subtitles from a YouTube video URL and turn them into a grounded summary, outline, topic list, notes, or Q&A source. Use when the user shares a YouTube link and asks to summarize the video, extract captions/subtitles, list what was mentioned, identify key points, answer questions from the spoken content, or requests like "总结这个 YouTube 视频", "提取字幕", or "列出视频里提到的内容".
---

# YouTube Caption Summary

Use this skill when the user provides a YouTube video URL and wants an answer grounded in transcript data instead of guesswork.

## Quick Start

1. For the default user-facing result, run `python3 scripts/youtube_to_chinese_report.py "<youtube-url>"`.
2. For raw transcript JSON, run `python3 scripts/fetch_youtube_transcript.py "<youtube-url>" --output /tmp/youtube-transcript.json`.
3. Use the report script when the user wants `中文摘要 + 提到的内容`.
4. Use the fetch script when you need raw `segments`, `chapters`, or transcript metadata.
5. For visual evidence artifacts, run the report wrapper with `--with-frames`, or pass `--transcript-input` to reuse a transcript from a separate caption flow.

## Reliability-First Workflow

### 1. Fetch with automatic strategy selection

- Run the bundled script from the skill folder.
- The default `--strategy auto` flow is the preferred path.
- The script first tries a real browser session and extracts the transcript from the YouTube transcript panel.
- If browser transcript extraction is unavailable or fails, the script falls back to `youtube-transcript-api`.
- The script auto-installs `youtube-transcript-api` into `scripts/_vendor/` on first use, so no global Python package install is required.
- Browser transcript extraction requires `npx` on PATH plus the Playwright CLI wrapper from `~/.codex/skills/playwright/`, or the ability to run `npx --package @playwright/cli playwright-cli`.

Common commands:

```bash
python3 scripts/fetch_youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --output /tmp/video.json
python3 scripts/fetch_youtube_transcript.py "https://youtu.be/VIDEO_ID" --format text
python3 scripts/fetch_youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --strategy api --output /tmp/video.json
python3 scripts/fetch_youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --strategy browser --output /tmp/video.json
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID"
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" --with-frames --frames-output /tmp/frames_manifest.json --multimodal-output /tmp/multimodal_segments.json
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" --with-frames --skip-report --cookies-from-browser chrome --frames-output /tmp/frames_manifest.json --multimodal-output /tmp/multimodal_segments.json --html-output /tmp/video_timeline.html
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" --transcript-input /tmp/video.json --with-frames --skip-report --frames-output /tmp/frames_manifest.json --multimodal-output /tmp/multimodal_segments.json
python3 scripts/render_multimodal_timeline.py --segments /tmp/multimodal_segments.json --output /tmp/video_timeline.html
```

### 2. Read the metadata before summarizing

- Check `strategy_used` to see whether the result came from the browser transcript panel or the direct API path.
- Check `notes` for fetch caveats, especially when the secondary path had to be used.
- Report `is_generated` when it is available and `true`, because auto-captions can contain recognition errors.
- If `used_language_fallback` is `true`, mention that the script fell back to the best available caption track.
- If the transcript is short, noisy, or incomplete, warn that the summary may miss visual-only content or uncaptioned sections.
- If the script cannot fetch captions at all, say that directly instead of guessing from the title or description.

### 3. Summarize only from transcript evidence

- Base claims on transcript text, not assumed visuals.
- Default deliverable for this skill:
  - `中文摘要`
  - `提到的内容`
- `中文摘要` should be a short paragraph or a few concise bullets that explain the main argument, conclusion, or narrative of the video.
- `提到的内容` should be a flat list of topics, people, teams, products, tools, frameworks, or named items explicitly mentioned in the transcript.
- If the transcript is long and clearly sectioned, use `chapters` to keep the Chinese summary organized, but keep the top-level output shape the same.
- Add timestamps only when the user asks for them or when they materially improve navigation.
- Prefer paraphrase over long quotation.

### 4. Use chapters when helpful

- The payload may include `chapters` when the transcript panel exposes chapter labels.
- Use `chapters` to organize long summaries, timelines, or sectioned notes.
- Search `segments` when the user wants where a topic was mentioned.
- Use the `timestamp` field from each segment for concise citations such as `03:42`.

## Output Shape

When the user does not ask for a custom format, use this exact structure:

1. `中文摘要`
2. `提到的内容`

Example shape:

```markdown
中文摘要
这里用一段中文概括视频核心结论。

提到的内容
- 主题 A
- 人物 B
- 产品 C
```

When the user asks for more detail, expand with:

- `时间线`
- `关键结论`
- `有时间戳的要点`
- `值得追问的问题`

## Limits

- This skill works on individual YouTube video URLs, not channels or playlists.
- The primary browser path is transcript-panel based, so it still depends on YouTube exposing a transcript in the watch page.
- If the video has no accessible captions, do not fabricate a summary.
- Visual evidence mode extracts frames and builds multimodal segment artifacts, but it does not yet make visual claims in the final report unless a later multimodal model step is added.
- `--translate-to` is best-effort and only fully supported on the direct API path. If the browser path is used, translate during summarization instead.

## Scripts

- `scripts/fetch_youtube_transcript.py`: Fetch captions, normalize them into plain text plus timestamped segments, prefer browser transcript extraction in the main flow, and use the direct API path as the secondary strategy.
- `scripts/extract_video_frames.py`: Download or read a video, extract timed and scene-change frames with `scene_score`, pHash deduplicate them, and write `frames_manifest.json`.
- `scripts/build_multimodal_segments.py`: Merge transcript windows and kept frame references into `multimodal_segments.json`.
- `scripts/render_multimodal_timeline.py`: Render `multimodal_segments.json` as a local HTML timeline with captions and kept frames.
- `scripts/youtube_to_chinese_report.py`: One-command wrapper that fetches the transcript and uses `codex exec` with a JSON schema to generate `中文摘要` plus `提到的内容`.
