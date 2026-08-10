# API And yt-dlp Caption Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep `youtube-transcript-api` as the primary caption source and add a caption-only `yt-dlp` fallback that preserves the `caption.v2` contract and records every provider attempt.

**Architecture:** `scripts/fetch_youtube_transcript.py` remains the single caption module. It gains one `yt-dlp` provider that reads subtitle metadata and downloads only a selected VTT track; `run_caption_pipeline(...)` orchestrates the fixed `api -> yt-dlp` order. Downstream wrappers remain provider-agnostic, while the validation module accepts either approved provider without rewriting historical direct-only evidence.

**Tech Stack:** Python 3 standard library, `youtube-transcript-api==1.2.4`, `yt-dlp>=2025.1.0`, `unittest`, JSON, VTT caption text, and real YouTube HTTPS requests.

## Global Constraints

- Provider order is fixed as `api -> yt-dlp -> fail` and is not exposed as a command-line option.
- API success must not invoke `yt-dlp`.
- API error or empty content must invoke `yt-dlp` exactly once.
- `yt-dlp` may read `subtitles` and `automatic_captions` and download the selected caption URL only.
- Caption acquisition must not use Playwright, browser control, computer use, browser cookies, video download, audio download, `ffmpeg`, speech recognition, or fabricated transcript text.
- `caption.v2` remains the public artifact shape.
- `requested.providers`, `attempts`, `selected_result.provider`, `selection.provider`, and chunk `source_provider` must identify the actual two-provider workflow.
- The historical 25-video `api` result files remain unchanged.

---

## File Map

- Modify: `scripts/fetch_youtube_transcript.py` - add VTT parsing, `yt-dlp` track selection/provider logic, ordered orchestration, and provider-aware artifacts.
- Modify: `tests/test_fetch_youtube_transcript.py` - replace sole-provider assertions with confirmed primary/fallback behavior and caption-only safety assertions.
- Modify: `scripts/validate_real_youtube_captions.py` - accept `api` and `yt-dlp` success results while preserving the historical manifest rules.
- Modify: `tests/test_validate_real_youtube_captions.py` - prove both approved providers and resume behavior.
- Modify: `tests/test_youtube_to_chinese_report.py` - retain the no-provider-control wrapper contract.
- Modify: `tests/test_youtube_to_xiaohongshu_post.py` - retain the no-provider-control wrapper contract.
- Modify: `SKILL.md` - document API-primary and caption-only `yt-dlp` fallback behavior.
- Modify: `README.md` - document the dependency, provider evidence, and failure behavior.
- Create: `docs/validation/2026-08-10-yt-dlp-caption-smoke.json` - real fallback-provider evidence when network conditions permit.

---

### Task 1: Define The Ordered Provider Contract With Failing Tests

**Files:**

- Modify: `tests/test_fetch_youtube_transcript.py`

**Interfaces:**

- Consumes: Existing `run_caption_pipeline(video, requested_languages, translate_to, preserve_formatting, chunk_seconds)`.
- Produces: Executable business tests for API priority, fallback, attempt evidence, provider-aware chunks, and total failure.

- [ ] **Step 1: Replace the sole-provider success test with API-primary behavior.**

Add a reusable provider fixture and assert that `yt-dlp` is not called:

```python
def provider_result(provider: str, text: str = "hello") -> dict:
    segments = [{
        "text": text,
        "start": 0.0,
        "duration": 2.0,
        "end": 2.0,
        "timestamp": "00:00",
    }]
    return {
        "provider": provider,
        "video_id": "abc123def45",
        "source_url": "https://www.youtube.com/watch?v=abc123def45",
        "title": "Captions",
        "language_code": "en",
        "is_generated": False,
        "used_language_fallback": False,
        "translated_to": None,
        "source_format": "youtube_transcript_api" if provider == "api" else "vtt",
        "segment_count": 1,
        "duration_seconds": 2.0,
        "chapters": [],
        "text": text,
        "segments": segments,
        "notes": [],
        "raw_metadata": {},
    }

def test_pipeline_prefers_api_without_calling_yt_dlp(self):
    with patch.object(module, "fetch_title", return_value="Captions"):
        with patch.object(module, "fetch_via_api", return_value=provider_result("api")):
            with patch.object(module, "fetch_via_yt_dlp") as fallback:
                payload = module.run_caption_pipeline(
                    video="abc123def45",
                    requested_languages=["en"],
                    translate_to=None,
                    preserve_formatting=False,
                    chunk_seconds=90.0,
                )
    fallback.assert_not_called()
    self.assertEqual(["api", "yt-dlp"], payload["requested"]["providers"])
    self.assertEqual("api", payload["selected_result"]["provider"])
    self.assertEqual(["api"], [item["provider"] for item in payload["attempts"]])
```

- [ ] **Step 2: Add the API-error fallback scenario.**

```python
def test_pipeline_falls_back_to_yt_dlp_after_api_error(self):
    with patch.object(module, "fetch_title", return_value="Captions"):
        with patch.object(module, "fetch_via_api", side_effect=RuntimeError("api blocked")):
            with patch.object(
                module, "fetch_via_yt_dlp", return_value=provider_result("yt-dlp")
            ) as fallback:
                payload = module.run_caption_pipeline(
                    video="abc123def45",
                    requested_languages=["en"],
                    translate_to=None,
                    preserve_formatting=False,
                    chunk_seconds=90.0,
                )
    fallback.assert_called_once()
    self.assertEqual("yt-dlp", payload["selected_result"]["provider"])
    self.assertEqual(["failed", "success"], [item["status"] for item in payload["attempts"]])
    self.assertEqual("yt-dlp", payload["chunks"][0]["source_provider"])
```

- [ ] **Step 3: Add the empty-API fallback and both-provider-failure scenarios.**

```python
def test_pipeline_falls_back_after_empty_api_result(self):
    empty = {"provider": "api", "segments": [], "text": ""}
    with patch.object(module, "fetch_title", return_value=None):
        with patch.object(module, "fetch_via_api", return_value=empty):
            with patch.object(module, "fetch_via_yt_dlp", return_value=provider_result("yt-dlp")):
                payload = module.run_caption_pipeline(
                    video="abc123def45",
                    requested_languages=["en"],
                    translate_to=None,
                    preserve_formatting=False,
                    chunk_seconds=90.0,
                )
    self.assertEqual("yt-dlp", payload["selection"]["provider"])

def test_pipeline_reports_both_provider_failures(self):
    with patch.object(module, "fetch_title", return_value=None):
        with patch.object(module, "fetch_via_api", side_effect=RuntimeError("api blocked")):
            with patch.object(module, "fetch_via_yt_dlp", side_effect=RuntimeError("yt-dlp blocked")):
                with self.assertRaisesRegex(
                    module.UserError, "api.*api blocked.*yt-dlp.*yt-dlp blocked"
                ):
                    module.run_caption_pipeline(
                        video="abc123def45",
                        requested_languages=["en"],
                        translate_to=None,
                        preserve_formatting=False,
                        chunk_seconds=90.0,
                    )
```

- [ ] **Step 4: Add `yt-dlp` track-selection scenarios.**

Cover requested manual, manual language fallback, requested generated, and no tracks:

```python
def test_choose_yt_dlp_caption_prefers_requested_manual_track(self):
    info = {
        "subtitles": {"en": [{"ext": "vtt", "url": "https://example.test/manual"}]},
        "automatic_captions": {"en": [{"ext": "vtt", "url": "https://example.test/auto"}]},
    }
    track, generated, language_fallback = module.choose_yt_dlp_caption(info, ["en"])
    self.assertEqual("https://example.test/manual", track["url"])
    self.assertFalse(generated)
    self.assertFalse(language_fallback)
```

The missing-track test must assert `UserError("No manual or automatic captions are available from yt-dlp.")`.

- [ ] **Step 5: Replace the obsolete static prohibition assertion.**

Allow `yt-dlp` only while continuing to reject forbidden caption paths:

```python
def test_caption_module_contains_no_browser_media_or_asr_caption_path(self):
    source = SCRIPT_PATH.read_text(encoding="utf-8").lower()
    for forbidden in (
        "playwright", "fetch_via_browser", "computer-use",
        "cookies-from-browser", "browser_cookie", "download_audio",
        "faster_whisper", "fetch_via_asr", "ffmpegextractaudio",
    ):
        self.assertNotIn(forbidden, source)
```

Add a provider-option test that asserts `skip_download is True`, `writesubtitles is True`, `writeautomaticsub is True`, and that `format`, `outtmpl`, and `postprocessors` are absent.

- [ ] **Step 6: Run the focused test and confirm the red phase.**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_fetch_youtube_transcript.py' -v
```

Expected: FAIL because `fetch_via_yt_dlp`, `choose_yt_dlp_caption`, and fallback orchestration are absent, while unrelated existing tests remain green.

---

### Task 2: Implement Caption-Only yt-dlp And Provider Orchestration

**Files:**

- Modify: `scripts/fetch_youtube_transcript.py`
- Test: `tests/test_fetch_youtube_transcript.py`

**Interfaces:**

- Consumes: Task 1 tests and the existing dependency installer, normalization helpers, and `build_provider_result(...)`.
- Produces: `choose_yt_dlp_track(...)`, `choose_yt_dlp_caption(...)`, `parse_vtt(...)`, `fetch_via_yt_dlp(...)`, provider-aware attempts, and provider-aware `caption.v2` output.

- [ ] **Step 1: Register the `yt-dlp` dependency and VTT timestamp parser.**

```python
DEPENDENCIES = {
    "youtube_transcript_api": "youtube-transcript-api==1.2.4",
    "yt_dlp": "yt-dlp>=2025.1.0",
}
VTT_TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
)
```

- [ ] **Step 2: Implement deterministic track selection.**

Implement:

```python
def choose_yt_dlp_track(tracks: list[dict[str, Any]]) -> dict[str, Any]:
    for extension in ("vtt", "webvtt"):
        for track in tracks:
            if str(track.get("ext", "")).lower() == extension and track.get("url"):
                return dict(track)
    raise UserError("yt-dlp did not expose a supported VTT caption track.")

def choose_yt_dlp_caption(
    info: dict[str, Any], requested_languages: list[str]
) -> tuple[dict[str, Any], bool, bool]:
    # Requested manual, any manual, requested generated, any generated.
```

The returned track must include `language_code`.

- [ ] **Step 3: Implement VTT parsing and caption URL download.**

`parse_vtt(raw: str) -> list[dict[str, Any]]` must split cue blocks, parse start/end timestamps, remove WebVTT settings and markup, normalize HTML entities, and return `normalize_segments(...)` output. `download_caption_text(track)` must request only `track["url"]` with a bounded timeout.

- [ ] **Step 4: Implement `fetch_via_yt_dlp(...)`.**

```python
def fetch_via_yt_dlp(*, video_id, source_url, title, requested_languages,
                     translate_to, preserve_formatting):
    del translate_to, preserve_formatting
    yt_dlp = ensure_dependency("yt_dlp")
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(source_url, download=False)
    track, generated, language_fallback = choose_yt_dlp_caption(
        info, requested_languages
    )
    segments = parse_vtt(download_caption_text(track))
    return build_provider_result(
        provider="yt-dlp",
        video_id=video_id,
        source_url=source_url,
        title=info.get("title") or title,
        segments=segments,
        chapters=[],
        language_code=track["language_code"],
        is_generated=generated,
        used_language_fallback=language_fallback,
        translated_to=None,
        source_format=str(track["ext"]).lower(),
        notes=["Caption was extracted from yt-dlp subtitle metadata."],
        raw_metadata={"format": track.get("ext"), "name": track.get("name")},
    )
```

- [ ] **Step 5: Generalize `build_caption_v2_payload(...)`.**

Change the function to accept `attempts: list[dict[str, Any]]`, derive `provider = selected["provider"]`, build chunks with that provider, set `requested.providers` to `["api", "yt-dlp"]`, and set `selection.provider` to the derived provider.

- [ ] **Step 6: Implement fixed provider orchestration.**

Use one provider list inside `run_caption_pipeline(...)`:

```python
providers = (("api", fetch_via_api), ("yt-dlp", fetch_via_yt_dlp))
attempts = []
selected = None
for provider, fetcher in providers:
    started = time.monotonic()
    try:
        candidate = fetcher(...)
        if not candidate.get("segments") or not str(candidate.get("text", "")).strip():
            raise UserError(f"{provider} did not return usable caption text.")
        selected = candidate
        attempts.append(success_attempt(provider, candidate, started))
        break
    except Exception as exc:
        error = map_api_error(exc) if provider == "api" else UserError(describe_error(exc))
        attempts.append(failed_attempt(provider, error, started))
if selected is None:
    raise UserError(format_provider_failures(attempts))
```

Do not add provider CLI parameters.

- [ ] **Step 7: Run focused tests and refactor only after green.**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_fetch_youtube_transcript.py' -v
```

Expected: all caption module tests pass.

---

### Task 3: Update Validation And Downstream Contracts

**Files:**

- Modify: `scripts/validate_real_youtube_captions.py`
- Modify: `tests/test_validate_real_youtube_captions.py`
- Modify: `tests/test_youtube_to_chinese_report.py`
- Modify: `tests/test_youtube_to_xiaohongshu_post.py`

**Interfaces:**

- Consumes: Provider values `api` and `yt-dlp` in a valid `caption.v2` artifact.
- Produces: Provider-neutral validation and unchanged downstream wrapper commands.

- [ ] **Step 1: Add a failing validator test for `yt-dlp` success and resume.**

Change the fake result provider to `yt-dlp` for at least one entry and assert it passes. Add an existing passed `yt-dlp` result and assert `--resume` reuses it.

- [ ] **Step 2: Run the validator test and confirm the red phase.**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_validate_real_youtube_captions.py' -v
```

Expected: FAIL because `category_error(...)` and resume currently require `api`.

- [ ] **Step 3: Generalize validation provider checks.**

Add:

```python
APPROVED_PROVIDERS = {"api", "yt-dlp"}
```

Require `selected.provider in APPROVED_PROVIDERS`, reuse passed results from either approved provider, preserve actual provider on exception only when available, and change `caption_source` to describe the ordered dual-provider workflow.

- [ ] **Step 4: Preserve wrapper provider opacity.**

Keep existing tests that the report and Xiaohongshu commands do not forward `--strategy` or `--providers`. Add `self.assertNotIn("--provider", command)` so provider choice remains internal.

- [ ] **Step 5: Run affected integration tests.**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_validate_real_youtube_captions.py' -v
python3 -m unittest discover -s tests -p 'test_youtube_to_chinese_report.py' -v
python3 -m unittest discover -s tests -p 'test_youtube_to_xiaohongshu_post.py' -v
```

Expected: all affected tests pass.

---

### Task 4: Update Skill Guidance And Produce Real Fallback Evidence

**Files:**

- Modify: `SKILL.md`
- Modify: `README.md`
- Create: `docs/validation/2026-08-10-yt-dlp-caption-smoke.json`

**Interfaces:**

- Consumes: Final provider behavior and a public captioned video.
- Produces: Accurate skill instructions and real `yt-dlp` caption evidence independent of the historical 25-video API report.

- [ ] **Step 1: Update the skill contract.**

Document:

```text
Caption acquisition first uses youtube-transcript-api. If that attempt fails
or returns empty text, the same caption module tries yt-dlp subtitle metadata.
The fallback uses skip_download and never obtains captions from browser state,
cookies, downloaded media, or speech recognition.
```

Require consumers to accept `selected_result.provider` equal to `api` or `yt-dlp` and inspect `attempts` for fallback context.

- [ ] **Step 2: Update README commands and limitations.**

Explain dependency auto-installation, fixed provider order, generated-caption warnings, both-provider errors, and the fact that both providers can be affected by YouTube IP rate limiting.

- [ ] **Step 3: Run a real yt-dlp provider smoke fetch.**

Temporarily use the previously approved working network route, then invoke only the internal `fetch_via_yt_dlp(...)` boundary for one page-audited public video. Record only metadata, not the transcript text:

```json
{
  "schema_version": "youtube-caption-provider-smoke.v1",
  "provider": "yt-dlp",
  "video_id": "...",
  "status": "passed",
  "language_code": "...",
  "is_generated": false,
  "segment_count": 1,
  "caption_character_count": 1,
  "duration_seconds": 1.0
}
```

Restore the original route after the single request. If the network blocks the request, record the exact failure outside the acceptance file and do not claim real fallback success.

- [ ] **Step 4: Run complete verification.**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
PYTHONPATH='/Users/changchen/Documents/Codex/2026-08-09/s/work/ycs-skill-validation-deps' \
  python3 /Users/changchen/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /Users/changchen/.codex/skills/youtube-caption-summary
git diff --check
```

Expected: all non-environment-dependent tests pass, the existing `ffmpeg/ffprobe` test may remain skipped, skill validation reports `Skill is valid!`, and the Git diff check is clean.

- [ ] **Step 5: Commit the implementation.**

```bash
git add \
  scripts/fetch_youtube_transcript.py \
  tests/test_fetch_youtube_transcript.py \
  scripts/validate_real_youtube_captions.py \
  tests/test_validate_real_youtube_captions.py \
  tests/test_youtube_to_chinese_report.py \
  tests/test_youtube_to_xiaohongshu_post.py \
  SKILL.md README.md \
  docs/validation/2026-08-10-yt-dlp-caption-smoke.json
git commit -m "feat: add yt-dlp caption fallback"
```

---

## Plan Self-Review

- Spec coverage: Every confirmed scenario maps to Tasks 1-4.
- Scope: No provider-selection CLI, browser source, Cookie source, media download, ASR, or third provider is introduced.
- Type consistency: Provider names are exactly `api` and `yt-dlp`; the public entry point remains `run_caption_pipeline(...)`; provider functions return the existing provider-result dictionary consumed by `build_caption_v2_payload(...)`.
- Evidence integrity: Historical API-only validation files are not modified; new `yt-dlp` evidence is separate.
