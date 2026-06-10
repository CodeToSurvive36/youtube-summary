# YouTube Caption Summary

A Codex skill for fetching accessible YouTube captions and using them as the grounded source for Chinese summaries, mentioned-item lists, outlines, notes, and Q&A.

The skill is designed to avoid guessing from titles or thumbnails. It summarizes only what is available in the video transcript.

## What It Does

- Fetches captions from an individual YouTube video URL or raw 11-character video ID.
- Prefers the YouTube transcript panel through a browser-based path.
- Falls back to `youtube-transcript-api` when the browser transcript panel is unavailable.
- Normalizes transcripts into JSON, plain text, or timestamped segments.
- Generates a default Chinese report with:
  - `中文摘要`
  - `提到的内容`

## Installation

Clone this repository into your Codex skills directory:

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/CodeToSurvive36/youtube-summary.git ~/.codex/skills/youtube-caption-summary
```

Restart Codex after installing so the skill is discovered.

If you already have the skill installed, update it with:

```bash
cd ~/.codex/skills/youtube-caption-summary
git pull
```

## Usage In Codex

Ask Codex to use the skill with a YouTube link:

```text
Use $youtube-caption-summary to summarize this video:
https://www.youtube.com/watch?v=VIDEO_ID
```

Default output shape:

```markdown
中文摘要
这里用一段中文概括视频核心内容。

提到的内容
- 主题 A
- 人物 B
- 产品 C
```

## Command-Line Usage

Run commands from the skill directory:

```bash
cd ~/.codex/skills/youtube-caption-summary
```

Generate the default Chinese Markdown report:

```bash
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

Fetch raw transcript JSON:

```bash
python3 scripts/fetch_youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --output /tmp/video-transcript.json
```

Print transcript text:

```bash
python3 scripts/fetch_youtube_transcript.py "https://youtu.be/VIDEO_ID" --format text
```

Force the direct API strategy:

```bash
python3 scripts/fetch_youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --strategy api
```

Force the browser transcript-panel strategy:

```bash
python3 scripts/fetch_youtube_transcript.py "https://www.youtube.com/watch?v=VIDEO_ID" --strategy browser
```

Save the generated report, structured JSON, and intermediate transcript:

```bash
python3 scripts/youtube_to_chinese_report.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --output /tmp/video-report.md \
  --json-output /tmp/video-report.json \
  --transcript-output /tmp/video-transcript.json
```

## Options

`scripts/fetch_youtube_transcript.py` supports:

- `--langs`: comma-separated preferred caption language codes.
- `--translate-to`: optional language code for transcript translation on the API path.
- `--strategy`: `auto`, `api`, or `browser`.
- `--format`: `json`, `text`, or `segments`.
- `--output`: write output to a file instead of stdout.
- `--preserve-formatting`: preserve formatting markers when the API source exposes them.

`scripts/youtube_to_chinese_report.py` supports:

- `--strategy`: transcript fetch strategy passed to the fetch script.
- `--langs`: preferred transcript languages.
- `--transcript-output`: save intermediate transcript JSON.
- `--json-output`: save the structured report JSON.
- `--output`: save final Markdown report.
- `--model`: optional Codex model override for report generation.
- `--keep-transcript`: keep the temporary transcript JSON.

Run either script with `--help` for the current CLI reference.

## Requirements

- Python 3.
- Codex CLI for `scripts/youtube_to_chinese_report.py`.
- `npx` plus a Playwright CLI path for the browser strategy.
- Network access to YouTube and any transcript APIs used by the selected strategy.

The direct API path auto-installs `youtube-transcript-api` into `scripts/_vendor/` on first use. No global Python package install is required for that dependency.

## Repository Layout

```text
.
|-- SKILL.md
|-- agents/
|   `-- openai.yaml
|-- scripts/
|   |-- chinese_report.schema.json
|   |-- fetch_youtube_transcript.py
|   `-- youtube_to_chinese_report.py
`-- tests/
    |-- fixtures/
    |   `-- browser_transcript_panel.txt
    `-- test_fetch_youtube_transcript.py
```

## Development

Run the test suite:

```bash
python3 -m unittest discover -s tests
```

Ignored local/generated files include:

- `scripts/_vendor/`
- `scripts/.vendor-install.lock`
- `__pycache__/`
- `*.pyc`

## Limits

- Works on individual YouTube videos, not channels or playlists.
- Requires accessible captions or subtitles.
- Does not analyze visual-only content.
- Browser extraction depends on YouTube exposing a transcript panel.
- Auto-generated captions can contain recognition errors; summaries should mention that caveat when metadata indicates generated captions.
