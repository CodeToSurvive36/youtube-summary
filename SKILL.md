---
name: youtube-caption-summary
description: Use when a user provides an individual YouTube video URL or video ID and asks for a transcript-grounded summary, outline, topic list, notes, Q&A, caption extraction, key points, mentioned items, or requests such as "总结这个 YouTube 视频", "提取字幕", or "列出视频里提到的内容".
---

# YouTube Caption Summary

Use this skill when the user provides a YouTube video URL and wants an answer grounded in transcript data instead of guesswork.

## Quick Start

1. For the default user-facing result, run `python3 scripts/youtube_to_chinese_report.py "<youtube-url>" --response-language "<user-language>"`.
2. For raw transcript JSON, run `python3 scripts/fetch_youtube_transcript.py "<youtube-url>" --output /tmp/youtube-transcript.json`.
3. Use the report script when the user wants a summary plus mentioned items.
4. Use the fetch script when you need raw `segments`, `chapters`, or transcript metadata.
5. For visual evidence artifacts, run the report wrapper with `--with-frames`, or pass `--transcript-input` to reuse a transcript from a separate caption flow.
6. Use `python3 scripts/youtube_to_xiaohongshu_post.py "<youtube-url>"` only when the user explicitly asks for Xiaohongshu copy, 小红书文案, 小红书笔记, 小红书风格, or content for posting on 小红书.

## Direct Caption Workflow

### 1. Fetch directly from YouTube

- Run the bundled script from the skill folder.
- Caption acquisition uses the fixed order `api -> yt-dlp`: first try `youtube-transcript-api==1.2.4`, then try `yt-dlp>=2025.1.0` once only when the API fails or returns no usable caption text.
- The script auto-installs both dependencies into `scripts/_vendor/` on first use, so no global Python package install is required.
- The `yt-dlp` provider reads only `subtitles`, `automatic_captions`, and the selected caption-track URL. It sets `skip_download=True` and `download=False`; it does not select or download video or audio.
- Caption acquisition never uses a visible transcript panel, browser state, browser cookies, browser automation, `computer-use`, downloaded media, or speech recognition. It never invokes a third caption source.
- If both approved providers fail, the fetch fails with both reasons. Do not replace missing captions with a title, description, page text, audio transcription, or fabricated content.
- Do not add or use provider-selection arguments. The order is fixed and is not user-configurable.

Common commands:

```bash
python3 scripts/fetch_youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --output /tmp/video.json
python3 scripts/fetch_youtube_transcript.py "https://youtu.be/VIDEO_ID" --format text
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" --response-language Chinese
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" --response-language "<user-language>" --with-frames --frames-output /tmp/frames_manifest.json --multimodal-output /tmp/multimodal_segments.json
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" --transcript-input /tmp/video.json --with-frames --skip-report --frames-output /tmp/frames_manifest.json --multimodal-output /tmp/multimodal_segments.json
python3 scripts/render_multimodal_timeline.py --segments /tmp/multimodal_segments.json --output /tmp/video_timeline.html
```

### 2. Read the metadata before summarizing

- Require `schema_version` to be `caption.v2` and `selected_result.provider` to be `api` or `yt-dlp`.
- Read `attempts` to distinguish an API success from a successful `yt-dlp` fallback. When API succeeds, no `yt-dlp` attempt is made.
- Check `notes` for caption caveats.
- Report `is_generated` when it is available and `true`, because auto-captions can contain recognition errors.
- If `used_language_fallback` is `true`, mention that the script fell back to the best available caption track.
- If the transcript is short, noisy, or incomplete, warn that the summary may miss visual-only content or uncaptioned sections.
- If the script cannot fetch captions at all, say that directly instead of guessing from the title or description.

### 3. Reply in the user's language from transcript evidence

- Base claims on transcript text, not assumed visuals.
- Use the response language explicitly requested by the user. If none is specified, use the current conversation language.
- Source-caption language and response language are independent. Never overwrite `language_code` or `is_generated` when translating the response.
- The summary should be a short paragraph or concise bullets explaining the main argument, conclusion, or narrative.
- Mentioned items should be a flat list of topics, people, teams, products, tools, frameworks, or named items explicitly present in the transcript.
- Add timestamps only when the user asks for them or when they materially improve navigation.
- Prefer paraphrase over long quotation.

### 4. Use chapters when helpful

- The payload keeps a `chapters` field for artifact compatibility; direct caption tracks may leave it empty.
- Use `chapters` to organize long summaries, timelines, or sectioned notes.
- Search `segments` when the user wants where a topic was mentioned.
- Use the `timestamp` field from each segment for concise citations such as `03:42`.

## Output Shape

When the user does not ask for a custom format, use this structure translated into the response language:

1. `摘要`
2. `提到的内容`

Example shape:

```markdown
摘要
这里用用户的语言概括视频核心结论。

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

## Xiaohongshu Markdown Output

Use the Xiaohongshu flow only when the user explicitly asks for Xiaohongshu / 小红书 output. Do not use it for ordinary YouTube summaries.

Run:

```bash
python3 scripts/youtube_to_xiaohongshu_post.py "https://www.youtube.com/watch?v=VIDEO_ID" --output /tmp/xiaohongshu_post.md
```

The Xiaohongshu script uses a two-stage generation flow:

1. Extract transcript structure into `structured_summary`.
2. Rewrite only from `structured_summary` into a Markdown 小红书笔记.

The final Markdown contains:

- `【标题】`
- `【封面文案】`
- `【正文】`
- `【标签】`

The final Markdown is meant to be publishable Xiaohongshu copy, not a transcript summary report or personal reaction. Titles are grouped into pain-point, counterintuitive, audience, and viewpoint styles. The rewrite step must use an objective editor voice, include a source note such as "以下内容基于 YouTube 视频《...》的字幕整理与翻译，不是逐字稿", avoid first-person wording, use reader-facing scenarios, translate abstract ideas into everyday work language, and avoid repeatedly saying "the video says" or "the guest thinks".

The final rewrite step must not read the full transcript directly. It must not invent facts, exaggerate claims, manufacture authority, use heavy clickbait language, or add forced engagement prompts such as asking users to like, save, follow, or comment. The JSON output still includes `fact_check` for internal quality control, but the final Markdown does not display a fact-check table.

## Limits

- This skill works on individual YouTube video URLs, not channels or playlists.
- If the video has no accessible captions, do not fabricate a summary.
- Visual evidence mode extracts frames and builds multimodal segment artifacts, but it does not yet make visual claims in the final report unless a later multimodal model step is added.
- `--translate-to` uses only translation exposed through the API provider. The `yt-dlp` fallback preserves its selected source-caption language. Response translation normally happens during report generation, so always answer in the user's requested language or the current conversation language.
- Xiaohongshu output is Markdown-first and should be used only for explicit 小红书文案 or 小红书笔记 requests.

## Scripts

- `scripts/fetch_youtube_transcript.py`: Fetch captions in the fixed `api -> yt-dlp` order, then normalize the selected caption track into plain text, timestamped segments, and chunks.
- `scripts/extract_video_frames.py`: Download or read a video, extract timed and scene-change frames with `scene_score`, pHash deduplicate them, and write `frames_manifest.json`.
- `scripts/build_multimodal_segments.py`: Merge transcript windows and kept frame references into `multimodal_segments.json`.
- `scripts/render_multimodal_timeline.py`: Render `multimodal_segments.json` as a local HTML timeline with captions and kept frames.
- `scripts/youtube_to_chinese_report.py`: One-command wrapper that fetches the transcript and generates `summary` plus `mentioned_items` in `--response-language`.
- `scripts/validate_real_youtube_captions.py`: Validate a 25-video page-audited manifest through the same fixed two-provider caption entry point.
- `scripts/youtube_to_xiaohongshu_post.py`: Explicit Xiaohongshu-only wrapper that fetches or reuses a transcript, saves optional raw/cleaned/structured artifacts, uses two Codex schema-constrained steps, and renders the final 小红书 Markdown post.
