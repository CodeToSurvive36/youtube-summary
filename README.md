# YouTube Caption Summary

A Codex skill for turning accessible YouTube captions into transcript-grounded summaries.

## Direct Caption Acquisition

Caption acquisition uses the fixed order `api -> yt-dlp`. It first calls `youtube-transcript-api==1.2.4`; only after an API error or empty result does it call `yt-dlp>=2025.1.0` once. The fallback reads only YouTube's `subtitles` or `automatic_captions` metadata and the selected caption-track URL. Its options include `skip_download=True` and `download=False`, with no video or audio format selection.

The caption command does not use a visible transcript panel, browser state, browser cookies, browser automation, `computer-use`, downloaded media, speech recognition, or a third caption source. If both approved providers fail, it reports both failures instead of substituting a title, description, page text, audio transcription, or fabricated content. The provider order cannot be selected or reordered through command-line arguments.

```bash
python3 scripts/fetch_youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --output /tmp/youtube-transcript.json
```

The resulting `caption.v2` artifact records `requested.providers` as `["api", "yt-dlp"]`, every provider attempt in `attempts`, and the actual successful provider in `selected_result.provider`, `selection.provider`, and each chunk's `source_provider`. It preserves the actual `language_code` and records YouTube-generated captions with `is_generated: true`.

## What This Skill Can Do

- Fetch captions or subtitles from an individual YouTube video.
- Summarize the video from transcript evidence instead of guessing from the title, thumbnail, or description.
- Extract key points, outlines, notes, Q&A source material, and mentioned topics.
- List people, products, tools, teams, concepts, or other named items mentioned in the transcript.
- Use timestamped transcript segments when the user asks where something was mentioned.
- Warn when captions are unavailable, incomplete, auto-generated, or language fallback was used.
- Optionally extract representative video frames and merge them with transcript windows for later multimodal analysis.

## Xiaohongshu Markdown Output

Use the Xiaohongshu flow only when you explicitly want 小红书文案 / 小红书笔记 / Xiaohongshu-style posting copy. Ordinary YouTube summaries still use the default summary flow.

```bash
python3 scripts/youtube_to_xiaohongshu_post.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --output /tmp/xiaohongshu_post.md \
  --structured-summary-output /tmp/structured_summary.json \
  --json-output /tmp/xiaohongshu_post.json
```

The final Markdown includes:

- `【标题】`
- `【封面文案】`
- `【正文】`
- `【标签】`

The script first extracts transcript structure, then rewrites that structured summary into a publishable Xiaohongshu note. Titles are grouped into pain-point, counterintuitive, audience, and viewpoint styles. The final rewrite uses an objective editor voice, not first-person reactions, and the body includes a source note such as `以下内容基于 YouTube 视频《...》的字幕整理与翻译，不是逐字稿`. The final rewrite step does not directly use the full transcript.

The JSON output still includes `fact_check` for internal quality review. The Markdown output intentionally does not include a fact-check table, because it is meant to be posting copy rather than a report.

## Visual Evidence Artifacts

The first visual MVP does not call a multimodal model. It produces artifacts that a later model step can consume:

```bash
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --response-language Chinese \
  --with-frames \
  --frames-output /tmp/frames_manifest.json \
  --multimodal-output /tmp/multimodal_segments.json \
  --html-output /tmp/video_timeline.html
```

To decouple caption fetching from frame extraction, pass an existing transcript and skip report generation:

```bash
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --transcript-input /tmp/transcript.json \
  --with-frames \
  --skip-report \
  --frames-output /tmp/frames_manifest.json \
  --multimodal-output /tmp/multimodal_segments.json
```

Frame extraction uses timed frames, ffmpeg scene detection, pHash deduplication, and per-segment frame caps. Scene-change frames include `scene_score` in `frames_manifest.json`, and can be controlled with `--scene-min-gap-seconds` and `--max-scene-frames-per-segment`. `ffmpeg` and `ffprobe` must be available on PATH. Python dependencies are installed into `scripts/_vendor/` on first use.
If YouTube blocks media download but exposes storyboards, the frame extractor falls back to storyboard thumbnails and marks `settings.extraction_method` as `storyboard`.

If YouTube blocks anonymous video download with `HTTP Error 403: Forbidden`, pass browser cookies:

```bash
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --with-frames \
  --skip-report \
  --cookies-from-browser chrome \
  --frames-output /tmp/frames_manifest.json \
  --multimodal-output /tmp/multimodal_segments.json \
  --html-output /tmp/video_timeline.html
```

To render an HTML timeline from an existing multimodal segment file:

```bash
python3 scripts/render_multimodal_timeline.py \
  --segments /tmp/multimodal_segments.json \
  --output /tmp/video_timeline.html
```

## Response Language

Caption language and response language are independent. Ask for any response language in the Codex request, or pass it explicitly to the report wrapper:

```bash
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --response-language English
```

Chinese:

```text
Use $youtube-caption-summary to summarize this video in Chinese:
https://www.youtube.com/watch?v=VIDEO_ID
```

中文：

```text
使用 $youtube-caption-summary 总结这个视频，用中文输出：
https://www.youtube.com/watch?v=VIDEO_ID
```

English:

```text
Use $youtube-caption-summary to summarize this video in English:
https://www.youtube.com/watch?v=VIDEO_ID
```

英文：

```text
使用 $youtube-caption-summary 总结这个视频，用英文输出：
https://www.youtube.com/watch?v=VIDEO_ID
```

When no response language is specified in a Codex request, the skill uses the current conversation language. The report wrapper defaults to Chinese for command-line compatibility and accepts any explicit `--response-language` value.
