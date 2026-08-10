# API And yt-dlp Caption Fallback Design

**Status:** Approved design awaiting written-spec review

**Date:** 2026-08-10

**Supersedes:** The sole-provider runtime constraints in `2026-08-09-direct-youtube-caption-fetch-design.md`. The prior document and its 25-video results remain historical evidence for the direct `api` implementation.

## Goal

Allow `youtube-caption-summary` to obtain existing YouTube caption tracks through two ordered implementations:

1. Try `youtube-transcript-api` first.
2. If that attempt fails or returns no usable caption text, try `yt-dlp` as the only fallback.

Both implementations must produce the existing `caption.v2` artifact. The selected provider and every attempted provider must remain observable in the artifact.

## Non-Goals

- Letting users reorder or force providers through new command-line options.
- Using a visible transcript panel, browser automation, computer-use capabilities, or browser cookies to obtain captions.
- Downloading video or audio as part of caption acquisition.
- Generating a transcript through local or remote speech recognition.
- Adding a third caption provider.
- Treating a title, description, page text, or fabricated content as a transcript.
- Revalidating or rewriting the historical 25-video direct-API evidence as though `yt-dlp` produced it.
- Changing the optional frame-extraction module, whose media behavior remains outside caption acquisition.

## Confirmed Business Contract

### Actor And Trigger

- **Actor:** A Codex user requesting captions or a transcript-grounded result from one YouTube video.
- **Trigger:** The user supplies a supported YouTube URL or raw 11-character video ID.
- **Preconditions:** The input identifies a public video and YouTube exposes at least one usable manual or generated caption track to one of the two approved implementations.
- **Business action:** Try the primary API provider, use the `yt-dlp` caption provider only when the primary attempt fails or returns empty content, normalize the selected caption track, and preserve attempt evidence.
- **Observable result:** A non-empty `caption.v2` artifact whose selected provider is `api` or `yt-dlp`.
- **Side effects:** Optional output files and dependency installation into the existing `scripts/_vendor/` directory. Caption acquisition must not create video or audio files.

### Superseded Behavior

The following confirmed behavior replaces the previous direct-only requirement:

- `youtube-transcript-api` remains the primary provider but is no longer the only provider.
- `yt-dlp` caption metadata and caption-track downloads are now approved as the sole fallback.
- A primary-provider failure is recorded and followed by one `yt-dlp` attempt.
- The final artifact reports the provider that actually supplied the caption.

The following earlier prohibitions remain in force:

- No browser or visible transcript-panel caption source.
- No computer-use capability in caption acquisition.
- No browser Cookie source.
- No video or audio download in caption acquisition.
- No speech recognition or transcript fabrication.

## Current Behavior

### Confirmed From The Repository

- `scripts/fetch_youtube_transcript.py` currently installs only `youtube-transcript-api==1.2.4` for captions.
- `run_caption_pipeline(...)` calls only `fetch_via_api(...)`.
- `caption.v2.requested.providers` is fixed to `["api"]`.
- `caption.v2.attempts` contains one successful API attempt or no artifact is produced.
- `caption.v2.selection.provider` and `selected_result.provider` are fixed to `api`.
- Static tests currently reject every `yt-dlp` reference from the caption module.
- The frame-extraction module already uses `yt-dlp>=2025.1.0`, but that dependency is not part of the caption runtime.

### Historical Evidence

- The direct API provider passed 25 of 25 page-audited public videos across five categories.
- The stored validation results identify every selected provider as `api`.
- Those results remain valid evidence for the API provider only.
- Earlier anonymous `yt-dlp` diagnostics encountered YouTube `HTTP 429` responses in the then-current network environment; this is not evidence that the implementation contract is invalid, but it remains an external risk.

## Desired Behavior

### Provider Order

The pipeline order is fixed and is not user-configurable:

```text
api -> yt-dlp -> fail
```

The pipeline must not call `yt-dlp` when the API provider returns a non-empty valid caption result.

The pipeline must call `yt-dlp` exactly once when the API provider:

- Raises a dependency, network, YouTube, parsing, selection, or normalization error; or
- Returns a result with no segments or no non-whitespace transcript text.

Invalid YouTube input fails before provider execution and therefore does not trigger a fallback.

### yt-dlp Caption Acquisition

The `yt-dlp` provider must:

1. Install or import `yt-dlp>=2025.1.0` through the existing vendor dependency mechanism.
2. Call `YoutubeDL.extract_info(source_url, download=False)`.
3. Configure `skip_download=True`, `writesubtitles=True`, and `writeautomaticsub=True`.
4. Read caption tracks only from `info["subtitles"]` and `info["automatic_captions"]`.
5. Never request a video or audio format, invoke an audio postprocessor, or create media files.
6. Download only the selected caption-track URL.
7. Prefer `vtt` or `webvtt`; reject a selected format the current parser cannot normalize faithfully.
8. Normalize the caption into the same segment, text, duration, chunk, and metadata contract as the API provider.

### yt-dlp Track Selection

Track selection is deterministic:

1. First requested-language manual track.
2. First available manual track, with `used_language_fallback: true`.
3. First requested-language generated track.
4. First available generated track, with `used_language_fallback: true`.
5. Fail if neither collection contains a usable track URL.

For tracks with several encodings, prefer `vtt`, then `webvtt`. The provider records the selected language, format, name, and generated status.

`--translate-to` remains a capability of the API provider. The `yt-dlp` fallback does not invent translation support: it preserves the fetched source language and leaves `translated_to` unset. User-facing response translation remains the report module's responsibility.

### Artifact Contract

The public `caption.v2` shape remains stable. Provider-related values change as follows:

```json
{
  "requested": {
    "providers": ["api", "yt-dlp"]
  },
  "attempts": [
    {
      "provider": "api",
      "status": "failed",
      "error": "..."
    },
    {
      "provider": "yt-dlp",
      "status": "success"
    }
  ],
  "selected_result": {
    "provider": "yt-dlp"
  },
  "selection": {
    "provider": "yt-dlp"
  }
}
```

When the API succeeds, `attempts` contains only the successful API attempt. When both providers fail, no caption artifact is produced.

Chunks must record the selected provider in `source_provider`; they must not remain hard-coded to `api`.

### Error Contract

- Each failed attempt records the mapped provider error without discarding the original cause.
- An API failure by itself is not returned to the user when `yt-dlp` succeeds.
- When both providers fail, the command exits non-zero and reports both provider names and both failure reasons.
- An empty provider result is treated as that provider's failed attempt.
- No failure may trigger a browser, Cookie, media, speech-recognition, or fabricated transcript path.

## Module Boundaries

### Caption Fetch Module

**Owner:** `scripts/fetch_youtube_transcript.py`

**Inputs:** One video URL or ID, ordered language codes, optional API translation target, formatting choice, and chunk duration.

**Outputs:** One non-empty `caption.v2` artifact, or a non-zero error after both providers fail.

**Dependencies:** Python standard library, `youtube-transcript-api==1.2.4`, and `yt-dlp>=2025.1.0`.

**Forbidden dependencies and behaviors:** Playwright, browser-control skills, computer-use skills, browser Cookie extraction, media format selection, video download, audio download, `ffmpeg`, and speech recognition.

### Report And Xiaohongshu Modules

**Owners:** `scripts/youtube_to_chinese_report.py` and `scripts/youtube_to_xiaohongshu_post.py`.

These modules continue to call the caption CLI without provider-selection arguments. They consume whichever valid provider the caption module selected and must not compensate for a total caption failure.

### Real Validation Module

**Owner:** `scripts/validate_real_youtube_captions.py`.

The validator may accept successful `api` or `yt-dlp` results after this change. Historical direct-only result files remain unchanged. A new real `yt-dlp` smoke result must identify itself as separate evidence rather than altering the historical `25/25` direct-API claim.

## Affected Files

- `scripts/fetch_youtube_transcript.py`
- `tests/test_fetch_youtube_transcript.py`
- `scripts/validate_real_youtube_captions.py`
- `tests/test_validate_real_youtube_captions.py`
- `tests/test_youtube_to_chinese_report.py`
- `tests/test_youtube_to_xiaohongshu_post.py`
- `SKILL.md`
- `README.md`
- `agents/openai.yaml`, only if its interface description becomes inaccurate
- New real fallback smoke evidence under `docs/validation/`, if the environment permits a real `yt-dlp` caption fetch

## Scenario Matrix

| Requirement | Module boundary | Given | When | Then | Test level |
|---|---|---|---|---|---|
| Prefer API | Caption pipeline | API returns a non-empty caption | The pipeline runs | API is selected and `yt-dlp` is not called | Unit |
| Fall back after API error | Caption pipeline | API raises a mapped failure and `yt-dlp` returns captions | The pipeline runs | `yt-dlp` is selected and both attempts are recorded | Unit and integration |
| Fall back after empty API result | Caption pipeline | API returns no usable text and `yt-dlp` returns captions | The pipeline runs | Empty API attempt fails and `yt-dlp` succeeds | Unit |
| Select manual requested language | yt-dlp provider | Manual and generated tracks include a requested language | Track selection runs | Requested manual track is selected | Unit |
| Select manual language fallback | yt-dlp provider | No requested track exists but another manual track exists | Track selection runs | Manual track is selected and language fallback is true | Unit |
| Select generated requested language | yt-dlp provider | No manual track exists and a requested generated track exists | Track selection runs | Generated track is selected and marked generated | Unit |
| Reject missing tracks | yt-dlp provider | Both caption collections are empty | Provider runs | Provider fails without media download | Unit |
| Report both failures | Caption pipeline | API and `yt-dlp` both fail | The pipeline runs | Command fails with both provider reasons and no artifact | Unit and command integration |
| Preserve downstream contract | Report and Xiaohongshu wrappers | Either approved provider returns `caption.v2` | A wrapper consumes it | Existing transcript-grounded output continues | Integration |
| Prohibit other sources | Caption source | Repository source is inspected | Static constraint test runs | No browser, Cookie, media-download, audio, or ASR caption path exists | Static |
| Prove real yt-dlp captions | yt-dlp provider | A public page-audited captioned video and usable network route | Real smoke fetch runs | Non-empty captions report provider `yt-dlp` | Real smoke |

## Test Plan

1. Replace direct-only pipeline assertions with API-primary and `yt-dlp`-fallback scenarios.
2. Run the new focused test and confirm it fails because `fetch_via_yt_dlp` and fallback orchestration do not exist.
3. Add deterministic `yt-dlp` track-selection tests before provider implementation.
4. Implement only the subtitle-metadata and caption-track path.
5. Verify provider attempt recording, selected-provider propagation, and chunk source propagation.
6. Update validator tests so successful approved providers can be resumed without weakening the 25-video uniqueness and category rules.
7. Run report and Xiaohongshu wrapper tests to prove they do not expose or forward provider-selection controls.
8. Run a real `yt-dlp` caption smoke fetch separately from the historical direct-only validation when network conditions allow.
9. Run the complete test suite, syntax compilation, static forbidden-path scan, skill validation, and `git diff --check`.

## Acceptance Criteria

- API success never invokes `yt-dlp`.
- API failure or empty content invokes `yt-dlp` exactly once.
- `yt-dlp` fetches only an existing manual or generated caption track and does not download media.
- A successful artifact reports the actual provider consistently in selection, result, attempts, and chunks.
- Both-provider failure reports both causes and produces no transcript artifact.
- Browser, computer-use, browser Cookie, media download, audio download, and speech-recognition caption paths remain absent.
- Unit, integration, downstream, syntax, static, and skill-validation checks pass.
- At least one real public video returns a non-empty `yt-dlp` provider result, or the final report states the exact environmental blocker without claiming real fallback success.

## Risks And Rollback

- `yt-dlp` is a broad media dependency; tests must lock caption options so a later change cannot silently enable media download.
- YouTube may rate-limit both providers because they can ultimately reach related caption endpoints. Fallback improves implementation diversity but does not guarantee a different network reputation.
- YouTube may expose only formats the current VTT parser does not support. The implementation must fail accurately rather than parse them heuristically.
- Provider attempt details enlarge the artifact but preserve its existing top-level shape.
- Rollback consists of reverting the fallback implementation commit and restoring the previous direct-only documentation and tests. Historical 25-video API evidence requires no rollback.
