# Direct YouTube Caption Fetch Design

**Status:** Approved for implementation planning

**Date:** 2026-08-09

## Goal

Make `youtube-caption-summary` fetch captions exclusively from YouTube's direct network interfaces through `youtube-transcript-api`, then prove the implementation with 25 distinct public YouTube videos whose caption availability is independently confirmed on YouTube.

The final real-video validation result must be 25 successful direct-network fetches out of 25 independently caption-confirmed videos. No browser control, computer use, browser cookies, `yt-dlp` caption extraction, audio download, local speech recognition, or transcript fabrication may contribute to a successful fetch.

## Non-Goals

- Guaranteeing permanent success for every YouTube video regardless of deletion, privacy, region, authentication, age restriction, rate limiting, or future YouTube protocol changes.
- Fetching captions from private, deleted, region-blocked, login-required, or age-restricted videos.
- Generating captions from audio when YouTube exposes no caption track.
- Using a visible transcript panel as the runtime caption source.
- Using computer-use or browser-control capabilities inside the skill implementation.
- Using `yt-dlp` as a caption provider.
- Changing the existing video-frame extraction feature except where its documentation could imply that it is a caption source.
- Requiring source-caption language to match the user's response language.

## Actor And Trigger

- **Actor:** A Codex user requesting a transcript, summary, outline, mentioned items, notes, or answers grounded in one YouTube video.
- **Trigger:** The user supplies one supported YouTube URL or one raw 11-character YouTube video ID.
- **Preconditions:** The video is publicly and anonymously accessible from the current network, and YouTube exposes at least one caption track through its direct network interfaces.
- **Business action:** Resolve the video ID, list YouTube caption tracks, select one track deterministically, fetch the caption payload, normalize it into timestamped segments, and use that transcript as the sole evidence source.
- **Observable result:** A non-empty `caption.v2` artifact and, when requested, a response written in the user's language.
- **Side effects:** Optional output files requested by the user. Dependency installation remains limited to the existing local skill vendor directory.

## Current Behavior

### Confirmed From The Repository

- `scripts/fetch_youtube_transcript.py` currently supports four providers: `yt-dlp`, `api`, `browser`, and `asr`.
- The current default order is `yt-dlp`, `api`, `browser`, then `asr`, which contradicts `SKILL.md`.
- The `browser` provider invokes Playwright CLI and extracts text from the visible YouTube transcript panel.
- The `asr` provider downloads audio and generates a new transcript with `faster-whisper`.
- The `api` provider uses `youtube-transcript-api` to access YouTube's watch page, Innertube player data, caption tracks, and timed-text endpoint.
- Report generation is currently fixed to Chinese through `youtube_to_chinese_report.py` and the `chinese_summary` output field.

### Confirmed By Real Network Diagnostics On 2026-08-09

- The direct `youtube-transcript-api` path successfully fetched non-empty captions for 5 of 5 distinct public videos.
- The samples covered manual captions, YouTube-generated captions, English, Korean, and a transcript over 20,000 characters.
- The same direct path accepted standard watch, `youtu.be`, embed, `shorts`, and `youtube-nocookie.com` URL forms.
- A separate anonymous `yt-dlp` caption diagnostic failed for all 5 videos with HTTP 429 responses.
- No browser or computer-use capability contributed to the successful direct-network diagnostics.

### Assumptions Rejected By This Design

- A visible browser transcript panel is not required to fetch YouTube captions.
- A source-caption language mismatch is not a fetch failure.
- Audio transcription is not an acceptable substitute for an existing YouTube caption track.
- Passing unit tests alone is not sufficient evidence of real YouTube compatibility.

## Desired Business Behavior

### Direct Network Only

The caption module must have one runtime path:

1. Parse a supported YouTube URL or raw video ID.
2. Request the YouTube watch page through `youtube-transcript-api`.
3. Obtain the Innertube API key exposed by the watch page.
4. Request `youtubei/v1/player` for the video.
5. Read `captionTracks` from the player response.
6. Select one available track according to the confirmed selection rule.
7. Request that track's YouTube timed-text URL.
8. Normalize the returned caption snippets into `caption.v2`.

The implementation must not invoke a browser, control the computer, read browser cookies, download audio, run speech recognition, or invoke another caption provider.

### Caption Track Selection

1. Prefer a caption track matching the requested language order.
2. If no requested language exists, prefer the first available manually created track.
3. If no manual track exists, use the first available YouTube-generated track.
4. Set `used_language_fallback` to `true` when the selected track does not match the requested language order.
5. Preserve the selected track's actual `language_code`.
6. Preserve whether YouTube marks the selected track as generated in `is_generated`.
7. A generated caption is valid input and must not be treated as a failure.

### Response Language

- Caption language and response language are separate contracts.
- The raw caption artifact preserves the actual source-caption language.
- Summaries, answers, outlines, and mentioned-item reports use the language explicitly requested by the user.
- If the user does not explicitly name a response language, the skill uses the current conversation language.
- Report generation must accept an explicit `--response-language` value.
- The structured report field is `summary`, not `chinese_summary`.
- Translation performed while writing the response must not overwrite source-caption metadata.

## Module Boundaries

### Caption Fetch Module

**Owned by:** `scripts/fetch_youtube_transcript.py`

**Inputs:**

- One supported YouTube URL or raw video ID.
- Ordered preferred caption language codes.
- Optional YouTube caption translation target already supported by the direct API.
- Caption chunk duration.
- Output format and path.

**Outputs:**

- A `caption.v2` object.
- Plain text, segments, or chunks derived only from that object when explicitly requested.

**Dependencies:**

- Python standard library.
- `youtube-transcript-api` installed into `scripts/_vendor/` by the existing dependency installer.
- YouTube watch, Innertube player, and timed-text network endpoints used by that package.

**Must not depend on:**

- Playwright, `npx`, browser-control skills, computer-use skills, browser cookies, `yt-dlp` caption extraction, `faster-whisper`, `ffmpeg`, or downloaded audio.

### Report Module

**Owned by:** `scripts/youtube_to_chinese_report.py`, `scripts/chinese_report.schema.json`

**Inputs:**

- A valid, non-empty `caption.v2` artifact.
- An explicit response language.

**Outputs:**

- A structured report containing `summary` and `mentioned_items`.
- Markdown rendered in the requested response language.

**Dependencies:**

- The caption fetch module for transcript acquisition.
- Codex CLI for evidence-grounded report generation.

The report module must never compensate for a caption fetch failure by using a title, description, audio, or visible browser transcript.

### Validation Boundary

Computer use is allowed only outside the skill runtime to inspect the public YouTube page and independently establish whether a validation candidate actually exposes captions. Browser-observed transcript text must not be copied into fixtures, command inputs, or expected transcript assertions.

The direct-network validation command must run independently after the page audit. Its success must be based only on the caption module's output.

## Public Contract Changes

- Remove `--providers`, `--strategy`, and `--asr-model` from the caption CLI.
- Remove provider aliases for `browser`, Playwright, `yt-dlp`, and local ASR.
- Keep the `caption.v2` artifact shape used by downstream frame, multimodal, and report modules.
- Keep `selected_result.provider` as `api` for compatibility and to identify the sole direct-network implementation.
- Keep `attempts` for `caption.v2` compatibility, and allow it to contain only the direct `api` attempt.
- Remove `strategy_used` references from documentation because that field does not exist.
- Add required `--response-language` input to report generation.
- Change report JSON from `chinese_summary` to `summary` and update its callers, schema, renderer, tests, and documentation together.

## Error Contract

The caption command must fail with a non-zero exit code and a specific user-facing reason for:

- Invalid or unsupported YouTube URL or video ID.
- Unavailable, deleted, or private video.
- Region-restricted, login-required, or age-restricted video.
- YouTube video with no caption tracks.
- YouTube request rejection or rate limiting.
- Unparseable watch-page or player-response data.
- A listed caption track whose timed-text payload cannot be downloaded.
- Empty or invalid caption content after normalization.

No product-level retry, alternate provider, browser path, cookie path, audio path, or fabricated transcript is approved. If a real validation sample fails, implementation work returns to evidence gathering and root-cause analysis rather than changing the source of truth.

## Test Strategy

### Unit Tests

- URL and video-ID parsing for every supported input form.
- Preferred-language selection.
- Manual-track selection when preferred languages are absent.
- Generated-track selection when no manual track exists.
- Accurate `used_language_fallback`, `language_code`, and `is_generated` metadata.
- Normalization of non-empty caption snippets into segments, text, and chunks.
- Rejection of empty caption payloads.
- Mapping of direct API failures to the confirmed error contract.
- Report rendering with an explicitly requested response language.
- Static source assertions that the caption module has no Playwright, browser-control, computer-use, `yt-dlp` caption, browser-cookie, audio-download, or ASR path.

### Integration Tests

- Mock the `youtube-transcript-api` public boundary, not its private implementation, to verify the direct fetch module's input and `caption.v2` output contract.
- Verify the report wrapper invokes the sole caption CLI without removed provider options.
- Verify downstream multimodal consumers still accept the preserved `caption.v2` shape.

### Real YouTube Validation

The real validation set contains 25 distinct public videos divided into 5 non-overlapping assigned groups of 5:

1. English manually created captions.
2. Non-English manually created captions.
3. YouTube-generated captions.
4. Caption text longer than 20,000 characters.
5. Video duration no longer than 5 minutes.

Language diversity is not an acceptance requirement because language does not change the transport method. A video's assigned group controls counting; no video ID may appear more than once in the 25-video manifest.

Supported URL forms must be distributed across the set: standard watch URL, `youtu.be`, embed URL, `shorts` URL, and `youtube-nocookie.com` embed URL.

## Scenario Matrix

| Requirement | Module boundary | Given | When | Then | Test level | Count |
|---|---|---|---|---|---|---:|
| Parse supported video inputs | Caption fetch | A supported watch, short, embed, or raw-ID input | The command resolves the video | It produces the correct canonical video ID and URL | Unit and real | At least 5 forms |
| Fetch English manual captions | Caption fetch | A page-audited public video with English manual captions | Direct fetch runs | A non-empty `caption.v2` artifact reports a manual English track | Real | 5 |
| Fetch non-English manual captions | Caption fetch | A page-audited public video with non-English manual captions | Direct fetch runs | A non-empty `caption.v2` artifact reports the actual manual track language | Real | 5 |
| Fetch generated captions | Caption fetch | A page-audited public video with only or selected YouTube-generated captions | Direct fetch runs | A non-empty artifact reports `is_generated: true` | Unit and real | 5 |
| Fetch long captions | Caption fetch | A page-audited video whose fetched text exceeds 20,000 characters | Direct fetch runs | Full text and chunks are non-empty and retain all normalized segments | Unit and real | 5 |
| Fetch short-video captions | Caption fetch | A page-audited captioned video no longer than 5 minutes | Direct fetch runs | A non-empty caption artifact is returned | Real | 5 |
| Select another language | Caption selection | Requested languages are absent but another manual track exists | Track selection runs | The manual track is selected and `used_language_fallback` is true | Unit |
| Select generated track | Caption selection | No manual track exists but a generated track exists | Track selection runs | The generated track is selected and marked generated | Unit |
| Reject no-caption video | Caption fetch | YouTube returns no caption tracks | Direct fetch runs | The command fails specifically and produces no transcript | Unit and diagnostic real sample outside 25-video success set |
| Reject empty timed text | Caption normalization | A selected track returns no usable snippets | Normalization runs | The command fails and produces no transcript or report | Unit |
| Reply in user language | Report | A valid source-language transcript and requested response language | Report generation runs | `summary` and `mentioned_items` use the requested response language while source metadata remains unchanged | Unit and integration |
| Prohibit alternate sources | Caption fetch | The repository source and command interface | Constraint verification runs | No forbidden provider, browser, cookie, audio, or ASR caption path exists | Static and integration |

## Real Validation Evidence

The validation deliverable must record, for every one of the 25 distinct video IDs:

- Assigned validation group.
- Input URL and canonical video ID.
- Video title.
- Page-audit confirmation that captions are available.
- Whether the page indicates manual or generated captions when distinguishable.
- Direct-fetch exit code.
- Selected caption language.
- `is_generated` and `used_language_fallback`.
- Segment count and normalized text character count.
- Direct-fetch elapsed time.
- Pass or fail result.

The summary must report `25/25` only when every row passes. Any failed row prevents a 100% claim until the root cause is identified and the direct-network implementation is fixed or the page audit proves that the candidate no longer meets the caption-availability precondition. Replacing a failed candidate merely to improve the percentage is not allowed unless the page audit establishes that it was incorrectly classified or changed externally; such replacement must be documented.

## Acceptance Criteria

- The caption runtime contains exactly one caption source: `youtube-transcript-api` direct network access to YouTube.
- The caption runtime contains no browser, computer-use, browser-cookie, `yt-dlp` caption, audio, or ASR path.
- All affected unit and integration tests pass.
- Static forbidden-path checks pass.
- Existing `caption.v2` downstream consumers pass their tests.
- Response-language tests prove that final reports follow the user's language independently of source-caption language.
- 25 distinct, page-audited, captioned public videos all return non-empty direct-network caption artifacts.
- The real-video validation report records 25 passes out of 25 and identifies the verification date and environment.
- No confirmed scenario is reported as covered without an executable test or recorded real validation row.

## Risks And Failure Considerations

- YouTube can change watch-page, Innertube, or timed-text behavior without notice. The dependency version and validation date must be recorded.
- YouTube can block an IP or rate-limit requests. This is reported as an external direct-network failure; it must not trigger an alternate source.
- A video's caption availability can change after page audit. The audit and network fetch should occur in the same validation session, and any change must be documented.
- The report JSON field change from `chinese_summary` to `summary` requires all local callers and tests to change together.
- The working tree already contains unrelated or partially completed Xiaohongshu changes. Implementation must preserve them and avoid mixing them into caption-fetch commits.

## Rollback

Implementation commits must be scoped so the direct-caption refactor and report-language generalization can be reverted independently. Rollback may restore the previous code for investigation, but the browser, computer-use, cookie, audio, and ASR paths must not be presented as acceptable production behavior under this approved specification.
