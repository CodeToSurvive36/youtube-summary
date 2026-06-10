# YouTube Caption Summary

A Codex skill for turning accessible YouTube captions into transcript-grounded summaries.

## What This Skill Can Do

- Fetch captions or subtitles from an individual YouTube video.
- Summarize the video from transcript evidence instead of guessing from the title, thumbnail, or description.
- Extract key points, outlines, notes, Q&A source material, and mentioned topics.
- List people, products, tools, teams, concepts, or other named items mentioned in the transcript.
- Use timestamped transcript segments when the user asks where something was mentioned.
- Warn when captions are unavailable, incomplete, auto-generated, or language fallback was used.

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
