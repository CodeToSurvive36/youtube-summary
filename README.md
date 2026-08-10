# YouTube Caption Summary

[中文](#中文) | [English](#english)

## 中文

[English](#english)

### 简介

`youtube-caption-summary` 是一个面向单个 YouTube 视频的 Codex Skill。它根据视频字幕生成有依据的摘要、要点、笔记和问答内容，并按照用户指定的语言回复。

### 可以做什么

- 总结视频的主题、论点、结论或叙事脉络。
- 提取完整字幕或带时间戳的字幕片段。
- 整理关键要点、章节式大纲、学习笔记和问答材料。
- 列出字幕中明确提到的人物、产品、工具、团队、概念和其他名称。
- 标出某个主题在视频中出现的位置，方便快速跳转。
- 按用户指定的语言生成结果，不受原字幕语言限制。
- 在用户明确要求时，结合视频画面补充上下文。

### 如何使用

在 Codex 中提供一个 YouTube 视频链接，并在请求中使用 `$youtube-caption-summary`。

总结视频：

```text
使用 $youtube-caption-summary 总结这个视频，并列出其中提到的人物、产品和主题：
https://www.youtube.com/watch?v=VIDEO_ID
```

指定回复语言：

```text
使用 $youtube-caption-summary 总结这个视频，用日语回复：
https://www.youtube.com/watch?v=VIDEO_ID
```

提取字幕：

```text
使用 $youtube-caption-summary 提取这个视频的字幕，并保留时间戳：
https://www.youtube.com/watch?v=VIDEO_ID
```

整理带时间戳的要点：

```text
使用 $youtube-caption-summary 列出这个视频的关键结论，并标明对应时间：
https://www.youtube.com/watch?v=VIDEO_ID
```

### 回复语言

可以在请求中指定任意回复语言。如果没有指定，Skill 会使用当前对话语言。原字幕语言与最终回复语言相互独立。

### 适用范围

- 每次处理一个 YouTube 视频链接，不处理频道或播放列表。
- 视频需要具有可访问的字幕，才能生成基于字幕的结果。
- 如果字幕不可访问，Skill 会明确说明，不会根据标题、缩略图或描述编造视频内容。
- 主要结论来自字幕；仅存在于画面且未在字幕中表达的信息可能不会出现在普通摘要中。

[返回顶部](#youtube-caption-summary) | [English](#english)

## English

[中文](#中文)

### Introduction

`youtube-caption-summary` is a Codex Skill for individual YouTube videos. It creates transcript-grounded summaries, key points, notes, and Q&A material, then responds in the language requested by the user.

### What It Can Do

- Summarize a video's topics, arguments, conclusions, or narrative.
- Extract full captions or timestamped caption segments.
- Produce key points, structured outlines, study notes, and Q&A material.
- List people, products, tools, teams, concepts, and other named items explicitly mentioned in the captions.
- Identify when a topic appears so the user can navigate to the relevant moment.
- Return results in the requested language, independently of the source-caption language.
- Include visual context when the user explicitly requests it.

### How to Use

Provide one YouTube video link in Codex and include `$youtube-caption-summary` in the request.

Summarize a video:

```text
Use $youtube-caption-summary to summarize this video and list the people, products, and topics it mentions:
https://www.youtube.com/watch?v=VIDEO_ID
```

Choose the response language:

```text
Use $youtube-caption-summary to summarize this video and respond in Japanese:
https://www.youtube.com/watch?v=VIDEO_ID
```

Extract captions:

```text
Use $youtube-caption-summary to extract the captions from this video and keep the timestamps:
https://www.youtube.com/watch?v=VIDEO_ID
```

Create timestamped key points:

```text
Use $youtube-caption-summary to list the key conclusions from this video with their timestamps:
https://www.youtube.com/watch?v=VIDEO_ID
```

### Response Language

The request can specify any response language. If none is specified, the Skill uses the current conversation language. The source-caption language and the final response language are independent.

### Scope

- Each request handles one YouTube video link, not a channel or playlist.
- Accessible captions are required for transcript-grounded results.
- If captions are unavailable, the Skill says so instead of inventing content from the title, thumbnail, or description.
- The main result is grounded in captions; information shown only on screen may not appear in a standard summary.

[Back to top](#youtube-caption-summary) | [中文](#中文)
