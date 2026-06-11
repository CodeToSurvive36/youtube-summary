# YouTube Caption Summary

A Codex skill for turning accessible YouTube captions into transcript-grounded summaries.

## What This Skill Can Do

- Fetch captions or subtitles from an individual YouTube video.
- Summarize the video from transcript evidence instead of guessing from the title, thumbnail, or description.
- Extract key points, outlines, notes, Q&A source material, and mentioned topics.
- List people, products, tools, teams, concepts, or other named items mentioned in the transcript.
- Use timestamped transcript segments when the user asks where something was mentioned.
- Warn when captions are unavailable, incomplete, auto-generated, or language fallback was used.
- Optionally extract representative video frames and merge them with transcript windows for later multimodal analysis.

## Visual Evidence Artifacts

The first visual MVP does not call a multimodal model. It produces artifacts that a later model step can consume:

```bash
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" \
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

## English / Chinese Output

The skill can answer in either English or Chinese. Choose the output language in your Codex request.

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

By default, the bundled report script is optimized for Chinese output. For English output, request English explicitly in Codex.
