# Direct YouTube Caption Fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the skill so caption acquisition has exactly one direct YouTube network implementation and prove it with 25 independently caption-confirmed, non-overlapping real videos.

**Architecture:** `scripts/fetch_youtube_transcript.py` will retain one `youtube-transcript-api` provider and the existing `caption.v2` artifact contract. Report generation will accept an explicit response language while preserving source-caption metadata. A standalone validation script will consume a page-audited manifest and invoke only the direct API provider; computer use remains outside the skill runtime and is never passed transcript text.

**Tech Stack:** Python 3 standard library, vendored `youtube-transcript-api==1.2.4`, JSON Schema, `unittest`, Codex CLI, direct YouTube HTTPS requests, and a manually prepared validation manifest.

## Global Constraints

- The caption runtime contains exactly one caption source: `youtube-transcript-api` direct network access to YouTube.
- The caption runtime must not invoke Playwright, `npx`, browser-control skills, computer-use skills, browser cookies, `yt-dlp` caption extraction, `faster-whisper`, `ffmpeg`, downloaded audio, or fabricated transcript content.
- YouTube-generated captions are valid and must preserve `is_generated: true`.
- Source-caption language and response language are independent; reports use the user's requested language.
- Existing `caption.v2` downstream consumers remain compatible.
- The 25-video acceptance set contains 25 unique public videos, five assigned to each approved group, and must finish at `25/25`.
- Existing uncommitted Xiaohongshu files belong to the user and must be updated in place when their caption CLI arguments depend on the removed provider options.
- Do not use a worktree for implementation because the approved change must integrate with the user's uncommitted Xiaohongshu files; preserve unrelated changes in all commits.

---

## File Map

- Modify: `scripts/fetch_youtube_transcript.py` — remove all non-API caption providers and expose a direct-only pipeline.
- Modify: `tests/test_fetch_youtube_transcript.py` — replace browser/provider tests with direct-only selection, failure, and forbidden-path tests.
- Modify: `scripts/youtube_to_chinese_report.py` — remove provider forwarding and add response-language-aware report generation.
- Modify: `scripts/chinese_report.schema.json` — rename `chinese_summary` to `summary`.
- Create: `tests/test_youtube_to_chinese_report.py` — cover response-language prompt, rendering, and direct-only fetch command construction.
- Modify: `scripts/youtube_to_xiaohongshu_post.py` — remove the deleted `--strategy` argument and stop forwarding it to the caption fetch step.
- Modify: `tests/test_youtube_to_xiaohongshu_post.py` — assert the Xiaohongshu wrapper invokes the sole API fetch path.
- Create: `scripts/validate_real_youtube_captions.py` — validate a page-audited manifest through the sole direct-only `run_caption_pipeline(...)` boundary.
- Create: `tests/test_validate_real_youtube_captions.py` — validate manifest uniqueness, group counts, page-audit preconditions, and result aggregation without network calls.
- Create: `docs/validation/2026-08-09-direct-youtube-caption-manifest.json` — 25 unique URLs with independently recorded page-caption confirmations.
- Create: `docs/validation/2026-08-09-direct-youtube-caption-results.json` — generated real-fetch evidence.
- Create: `docs/validation/2026-08-09-direct-youtube-caption-results.md` — generated human-readable evidence summary.
- Modify: `SKILL.md`, `README.md`, `agents/openai.yaml` — document direct-only caption acquisition and response-language behavior.

## Task 1: Establish Failing Direct-Only Tests

**Files:**

- Modify: `tests/test_fetch_youtube_transcript.py`
- Create: `tests/test_youtube_to_chinese_report.py`
- Create: `tests/test_validate_real_youtube_captions.py`

**Interfaces:**

- `run_caption_pipeline(video, requested_languages, translate_to, preserve_formatting, chunk_seconds)` remains the public internal boundary and returns `caption.v2`.
- `build_codex_prompt(transcript_path: Path, response_language: str)` returns a prompt containing the requested response language and the `summary` field.
- `validate_manifest(manifest)` returns validated unique entries grouped five per category.

- [ ] **Step 1: Add a direct-only pipeline test that fails against the current provider signature.**

~~~python
def test_pipeline_uses_only_api_provider(self):
    with patch.object(module, "fetch_via_api", return_value=provider_result):
        result = module.run_caption_pipeline(
            video="https://www.youtube.com/watch?v=abc123def45",
            requested_languages=["en"],
            translate_to=None,
            preserve_formatting=False,
            chunk_seconds=90.0,
        )
    self.assertEqual("api", result["selected_result"]["provider"])
    self.assertEqual(["api"], result["requested"]["providers"])
~~~

Run: `python3 -m unittest discover -s tests -p 'test_fetch_youtube_transcript.py' -v`

Expected: FAIL because the current pipeline requires a `providers` argument and exposes a multi-provider contract.

- [ ] **Step 2: Add selection and empty-result tests before implementation.**

Cover: requested manual track, manual fallback to another language, generated fallback, and an empty selected track raising `UserError` without creating a report.

Run: `python3 -m unittest discover -s tests -p 'test_fetch_youtube_transcript.py' -v`

Expected: FAIL in the new direct-only cases while existing unrelated tests remain the baseline reference.

- [ ] **Step 3: Add static forbidden-path tests.**

~~~python
def test_caption_module_has_no_non_network_caption_provider(self):
    source = FETCH_SCRIPT.read_text(encoding="utf-8").lower()
    for forbidden in (
        "playwright", "npx", "fetch_via_browser", "yt_dlp",
        "download_audio", "faster_whisper", "fetch_via_asr",
        "cookies-from-browser", "browser_cookie",
    ):
        self.assertNotIn(forbidden, source)
~~~

Run: `python3 -m unittest discover -s tests -p 'test_fetch_youtube_transcript.py' -v`

Expected: FAIL because the current source contains all of these paths.

- [ ] **Step 4: Add response-language tests.**

~~~python
def test_prompt_and_renderer_use_requested_language(self):
    prompt = module.build_codex_prompt(Path("/tmp/caption.json"), "English")
    self.assertIn("English", prompt)
    self.assertIn("summary", prompt)
    self.assertNotIn("chinese_summary", prompt)
~~~

Run: `python3 -m unittest discover -s tests -p 'test_youtube_to_chinese_report.py' -v`

Expected: FAIL because the current report is hard-coded to Chinese and uses `chinese_summary`.

- [ ] **Step 5: Add validation-manifest tests.**

Cover duplicate video IDs, wrong group counts, missing `page_caption_confirmed`, and successful aggregation using a mocked direct fetch function.

Run: `python3 -m unittest discover -s tests -p 'test_validate_real_youtube_captions.py' -v`

Expected: FAIL because the validation script does not yet exist.

- [ ] **Step 6: Commit only the test changes.**

~~~bash
git add tests/test_fetch_youtube_transcript.py tests/test_youtube_to_chinese_report.py tests/test_validate_real_youtube_captions.py
git commit -m "test: define direct-only YouTube caption contracts"
~~~

## Task 2: Reduce Caption Acquisition To One Direct Network Path

**Files:**

- Modify: `scripts/fetch_youtube_transcript.py:27-187,217-232,397-424,503-1239`
- Modify: `tests/test_fetch_youtube_transcript.py`

**Interfaces:**

- Keep `parse_languages(raw: str) -> list[str]`.
- Keep `extract_video_id(value: str) -> str` and `canonical_url(video_id: str) -> str`.
- Keep `choose_best_transcript(transcript_list, requested_languages, errors_module) -> tuple[Transcript, bool]`.
- Keep `fetch_via_api(...) -> dict[str, Any]` as the only provider implementation.
- Add `map_api_error(exc: Exception, errors_module: Any) -> UserError` for the approved public failure categories.
- Add `build_caption_v2_payload(*, video_id, source_url, title, requested_languages, translate_to, chunk_seconds, selected) -> dict[str, Any]` to preserve the existing artifact shape outside the network call.
- Change `run_caption_pipeline` to:

~~~python
def run_caption_pipeline(
    *,
    video: str,
    requested_languages: list[str],
    translate_to: str | None,
    preserve_formatting: bool,
    chunk_seconds: float,
) -> dict[str, Any]:
~~~

- [ ] **Step 1: Remove provider selection and forbidden provider code.**

Delete `DEFAULT_PROVIDERS`, `parse_providers`, `browser_url`, `parse_vtt`, `is_quality_sufficient`, `choose_yt_dlp_caption`, `choose_yt_dlp_track`, `download_caption_text`, `fetch_via_yt_dlp`, all Playwright command/capture/parser functions, `download_audio`, `normalize_asr_segments`, `fetch_via_asr`, and `attempt_provider`. Remove their imports, dependency declarations, and constants. Retain `fetch_title`, API transcript selection, normalization, chunking, quality metadata, and output rendering.

- [ ] **Step 2: Replace the multi-provider pipeline with one API call.**

Implement the direct pipeline with this shape:

~~~python
def run_caption_pipeline(*, video, requested_languages, translate_to,
                         preserve_formatting, chunk_seconds):
    video_id = extract_video_id(video)
    source_url = canonical_url(video_id)
    title = fetch_title(source_url)
    result = fetch_via_api(
        video_id=video_id,
        source_url=source_url,
        title=title,
        requested_languages=requested_languages,
        translate_to=translate_to,
        preserve_formatting=preserve_formatting,
    )
    result["chunks"] = build_chunks(result["segments"], chunk_seconds, "api")
    return build_caption_v2_payload(
        video_id=video_id,
        source_url=source_url,
        title=title,
        requested_languages=requested_languages,
        translate_to=translate_to,
        chunk_seconds=chunk_seconds,
        selected=result,
    )
~~~

The resulting payload must contain `selected_result.provider == "api"`, `requested.providers == ["api"]`, and no browser or alternate-provider entries.

- [ ] **Step 3: Make empty caption content fail before writing output.**

After `fetch_via_api` returns, require at least one normalized segment and non-empty joined text. Raise `UserError("The selected YouTube caption track returned no usable caption text.")` before constructing or writing `caption.v2`.

- [ ] **Step 4: Map direct YouTube failures without another provider.**

Map `VideoUnavailable`, `TranscriptsDisabled`, `AgeRestricted`, `RequestBlocked`/`IpBlocked`, `YouTubeDataUnparsable`, `YouTubeRequestFailed`, and `NoTranscriptFound` to stable `UserError` messages. Add focused tests that each exception produces the approved category and that no second network provider is called.

- [ ] **Step 5: Update CLI parsing and the entry point.**

Remove `--providers`, `--strategy`, and `--asr-model`. The `main()` call must pass only `video`, `parse_languages(args.langs)`, `args.translate_to`, `args.preserve_formatting`, and `args.chunk_seconds` to `run_caption_pipeline`.

- [ ] **Step 6: Update direct-fetch tests and run the focused suite.**

Delete browser-panel behavior tests from the caption module. Keep URL, normalization, chunk, language selection, generated-caption, fallback metadata, and API error tests. Add an assertion that `--help` contains no removed provider options.

Run: `python3 -m unittest discover -s tests -p 'test_fetch_youtube_transcript.py' -v`

Expected: all focused caption tests pass and static forbidden-path tests pass.

- [ ] **Step 7: Commit the direct-only caption module.**

~~~bash
git add scripts/fetch_youtube_transcript.py tests/test_fetch_youtube_transcript.py
git commit -m "refactor: use only direct YouTube caption network access"
~~~

## Task 3: Generalize Report Language Without Changing Source Evidence

**Files:**

- Modify: `scripts/youtube_to_chinese_report.py:25-50,164-185,267-332,350-428`
- Modify: `scripts/chinese_report.schema.json`
- Modify: `tests/test_youtube_to_chinese_report.py`

**Interfaces:**

- `build_codex_prompt(transcript_path: Path, response_language: str) -> str`.
- `render_markdown_report(report_payload: dict[str, Any], response_language: str) -> str`.
- `--response-language` is required for standalone report generation.

- [ ] **Step 1: Add the failing schema and renderer tests.**

~~~python
def test_renderer_uses_summary_and_requested_language(self):
    rendered = module.render_markdown_report(
        {"summary": "Résumé", "mentioned_items": ["Produit"]},
        "Français",
    )
    self.assertIn("Résumé", rendered)
    self.assertNotIn("chinese_summary", rendered)
~~~

Add a test that `parse_args()` requires `--response-language`, and a test that the Codex prompt instructs the model to write only in that language while preserving `caption.v2` source metadata.

Run: `python3 -m unittest discover -s tests -p 'test_youtube_to_chinese_report.py' -v`

Expected: FAIL against the current Chinese-only schema and renderer.

- [ ] **Step 2: Change the report schema.**

Rename the required property and all required references:

~~~json
{
  "type": "object",
  "properties": {
    "summary": {"type": "string", "minLength": 1},
    "mentioned_items": {
      "type": "array",
      "items": {"type": "string", "minLength": 1},
      "maxItems": 30
    }
  },
  "required": ["summary", "mentioned_items"],
  "additionalProperties": false
}
~~~

- [ ] **Step 3: Add explicit response-language plumbing.**

Add the required argument:

~~~python
parser.add_argument(
    "--response-language",
    required=True,
    help="Language for the generated summary and mentioned items.",
)
~~~

Pass it to `build_codex_prompt`, tell Codex to write `summary` and `mentioned_items` only in that language, and render the heading plus content without changing source-caption metadata.

- [ ] **Step 4: Remove strategy forwarding.**

Change `run_fetch_step` to invoke:

~~~python
command = [
    sys.executable,
    str(FETCH_SCRIPT),
    args.video,
    "--langs", args.langs,
    "--output", str(transcript_path),
]
~~~

Remove the report CLI's `--strategy` argument. Leave frame-only `yt-dlp` options untouched because they are outside the caption source boundary.

- [ ] **Step 5: Run focused report tests and commit.**

Run: `python3 -m unittest discover -s tests -p 'test_youtube_to_chinese_report.py' -v`

Expected: all report-language and direct-fetch-command tests pass.

~~~bash
git add scripts/youtube_to_chinese_report.py scripts/chinese_report.schema.json tests/test_youtube_to_chinese_report.py
git commit -m "feat: generate reports in the requested response language"
~~~

## Task 4: Update The Xiaohongshu Wrapper To The Direct Caption Contract

**Files:**

- Modify: `scripts/youtube_to_xiaohongshu_post.py:28-44,194-211`
- Modify: `tests/test_youtube_to_xiaohongshu_post.py`

**Interfaces:**

- The Xiaohongshu wrapper continues to receive source captions from `caption.v2`.
- Its `run_fetch_step` invokes the caption script without `--strategy`.

- [ ] **Step 1: Add the failing command-construction assertion.**

Mock `subprocess.run`, call `run_fetch_step`, and assert the command contains `--langs` and `--output`, but not `--strategy`, `--providers`, `browser`, `yt-dlp`, or `asr`.

Run: `python3 -m unittest discover -s tests -p 'test_youtube_to_xiaohongshu_post.py' -v`

Expected: FAIL because the uncommitted wrapper currently parses and forwards `--strategy`.

- [ ] **Step 2: Remove the wrapper's strategy argument and forwarding.**

Delete the parser option and the two command elements that append `args.strategy`. Leave all other Xiaohongshu generation and artifact logic unchanged.

- [ ] **Step 3: Run focused tests and preserve the user's uncommitted wrapper files.**

Run: `python3 -m unittest discover -s tests -p 'test_youtube_to_xiaohongshu_post.py' -v`

Expected: all existing Xiaohongshu tests and the new command-boundary assertion pass.

Do not stage or commit `scripts/youtube_to_xiaohongshu_post.py` or `tests/test_youtube_to_xiaohongshu_post.py`; both were untracked user files before this task. Record the exact compatibility hunks in the final summary.

## Task 5: Add A Reproducible 25-Video Validation Harness

**Files:**

- Create: `scripts/validate_real_youtube_captions.py`
- Modify: `tests/test_validate_real_youtube_captions.py`
- Create: `docs/validation/2026-08-09-direct-youtube-caption-manifest.json`

**Interfaces:**

- Manifest input: a JSON object with `validation_date`, `environment`, and 25 `videos`.
- Each video row has `group`, `url`, `page_caption_confirmed`, `audit_method`, and `audited_at`.
- `validate_manifest(manifest) -> list[dict[str, Any]]` validates five rows in each group, unique video IDs, supported URLs, and confirmed page audits.
- `run_validation(manifest, output_path, markdown_path) -> dict[str, Any]` invokes only the direct-only `run_caption_pipeline(...)` and writes per-video evidence.

- [ ] **Step 1: Add manifest-validation failure tests.**

Create a fixture builder that produces 25 rows, then test duplicates, wrong group counts, missing `page_caption_confirmed`, and unsupported URLs.

Run: `python3 -m unittest discover -s tests -p 'test_validate_real_youtube_captions.py' -v`

Expected: FAIL because the harness does not exist.

- [ ] **Step 2: Implement manifest validation without browser imports.**

The validator must call `fetch_youtube_transcript.extract_video_id` for canonical IDs and reject duplicates before any network call. It must require exactly these group names:

~~~python
REQUIRED_GROUPS = (
    "english_manual",
    "non_english_manual",
    "generated",
    "long_transcript",
    "short_video",
)
~~~

Each group must contain exactly 5 unique videos, and every row must have `page_caption_confirmed is True`.

- [ ] **Step 3: Implement direct-only result collection.**

For every manifest row, call only:

~~~python
fetcher.run_caption_pipeline(
    video=row["url"],
    requested_languages=fetcher.DEFAULT_LANGS,
    translate_to=None,
    preserve_formatting=False,
    chunk_seconds=90.0,
)
~~~

Record the selected language, generated flag, fallback flag, segment count, text character count, elapsed time, and error details. The harness must not import Playwright, `computer-use`, `yt-dlp`, or ASR packages.

- [ ] **Step 4: Implement result aggregation and the 25/25 gate.**

The JSON result must include `total`, `passed`, `failed`, `pass_rate`, `validation_date`, and one row per video. The Markdown report must say `25/25` only when `passed == 25` and `failed == 0`; otherwise it must state the actual counts and list every failed row.

- [ ] **Step 5: Run harness unit tests and commit the harness.**

Run:

~~~bash
python3 -m unittest discover -s tests -p 'test_validate_real_youtube_captions.py' -v
~~~

Expected: all manifest and aggregation tests pass without network access.

~~~bash
git add scripts/validate_real_youtube_captions.py tests/test_validate_real_youtube_captions.py
git commit -m "test: add direct-only real YouTube validation harness"
~~~

- [ ] **Step 6: Independently audit and record 25 real videos.**

Use computer use only to open each candidate YouTube page and confirm that the page exposes a caption control or transcript entry. Do not copy caption text. Write only the approved audit metadata into `docs/validation/2026-08-09-direct-youtube-caption-manifest.json`, ensure no video ID repeats, and assign exactly five rows to each group.

- [ ] **Step 7: Run the real direct-network validation.**

Run:

~~~bash
python3 scripts/validate_real_youtube_captions.py \
  --manifest docs/validation/2026-08-09-direct-youtube-caption-manifest.json \
  --output docs/validation/2026-08-09-direct-youtube-caption-results.json \
  --markdown-output docs/validation/2026-08-09-direct-youtube-caption-results.md
~~~

Expected: `total=25`, `passed=25`, `failed=0`, `pass_rate=1.0`. If any row fails, classify the direct-network root cause, add or adjust only the implementation/test needed for that root cause, rerun focused tests, and rerun all 25 rows. Do not use another caption source or silently replace a failed row.

- [ ] **Step 8: Commit the independently audited validation evidence.**

~~~bash
git add docs/validation/2026-08-09-direct-youtube-caption-manifest.json \
  docs/validation/2026-08-09-direct-youtube-caption-results.json \
  docs/validation/2026-08-09-direct-youtube-caption-results.md
git commit -m "test: record 25 direct YouTube caption validations"
~~~

## Task 6: Align Documentation And Static Constraints

**Files:**

- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `agents/openai.yaml`
- Modify: `tests/test_fetch_youtube_transcript.py`

**Interfaces:**

- Documentation describes one direct `youtube-transcript-api` path and no browser-first or multi-provider strategy.
- Agent metadata tells Codex to answer in the user's language.

- [ ] **Step 1: Add documentation assertions before editing.**

Extend tests to assert that `SKILL.md` contains `youtube-transcript-api`, direct-network wording, `response-language`, `is_generated`, and `used_language_fallback`, and does not contain Playwright, browser transcript extraction, `--strategy browser`, or `strategy_used`.

- [ ] **Step 2: Update the skill and README.**

Replace the current reliability workflow with the eight-step direct network flow. Document that automatic captions are valid, source language is preserved, response language follows the user, and no caption fallback to a browser, audio, or ASR path exists. Keep frame-only `yt-dlp` options clearly outside caption acquisition.

- [ ] **Step 3: Update agent metadata.**

Change `agents/openai.yaml` to describe “字幕摘要和提到的内容，使用用户指定的语言” and remove the fixed Chinese-only default wording.

- [ ] **Step 4: Run documentation and static tests, then commit.**

Run:

~~~bash
python3 -m unittest discover -s tests -p 'test_fetch_youtube_transcript.py' -v
git diff --check
~~~

Expected: focused tests pass and no whitespace errors remain.

Commit only the clean tracked files for this task:

~~~bash
git add agents/openai.yaml tests/test_fetch_youtube_transcript.py
git commit -m "docs: document direct-only caption acquisition"
~~~

Do not stage or commit `SKILL.md` or `README.md`; both already contain user changes. Their direct-only documentation edits remain visible in the working tree and are reported separately.

## Task 7: Full Verification And Handoff

**Files:**

- No new implementation files; inspect all changed files and generated validation evidence.

- [ ] **Step 1: Run the full repository test suite.**

~~~bash
python3 -m unittest discover -s tests -v
~~~

Expected: zero failures. Report any skipped tests and their concrete prerequisite.

- [ ] **Step 2: Run syntax and diff verification.**

~~~bash
python3 -m compileall -q scripts tests
git diff --check
~~~

Expected: both commands exit successfully.

- [ ] **Step 3: Run CLI contract checks.**

~~~bash
python3 scripts/fetch_youtube_transcript.py --help
python3 scripts/youtube_to_chinese_report.py --help
python3 scripts/youtube_to_xiaohongshu_post.py --help
~~~

Expected: caption help exposes no provider, browser, ASR, or computer-use options; report help exposes required `--response-language`; Xiaohongshu help contains no removed `--strategy` option.

- [ ] **Step 4: Verify the validation evidence.**

~~~bash
python3 - <<'PY'
import json
from pathlib import Path

result = json.loads(Path("docs/validation/2026-08-09-direct-youtube-caption-results.json").read_text())
assert result["total"] == 25
assert result["passed"] == 25
assert result["failed"] == 0
assert result["pass_rate"] == 1.0
PY
~~~

Expected: the command exits successfully and the report states `25/25`.

- [ ] **Step 5: Inspect the final Git state and summarize residual risk.**

~~~bash
git status --short --branch
git log --oneline -8
~~~

Confirm that unrelated user changes remain untouched, every changed module has an executable test, and the final response lists any confirmed scenarios without tests or real evidence.

## Plan Self-Review

- Spec coverage: direct-only source, track selection, generated captions, response language, error contract, static forbidden paths, 25-video audit, and 25/25 acceptance each have explicit tasks.
- Placeholder scan: complete; every implementation branch and expected command result is explicit.
- Type consistency: the plan uses `caption.v2`, `run_caption_pipeline` without provider arguments, `build_codex_prompt(path, response_language)`, `render_markdown_report(payload, response_language)`, and `validate_manifest` consistently across tasks.
- Scope: frame extraction remains outside caption acquisition; Xiaohongshu is updated only where it forwards removed caption options.

## Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-09-direct-youtube-caption-fetch.md`. Execution must proceed inline in the current worktree because the approved change must integrate with the user's uncommitted Xiaohongshu files. Before implementation, apply the `test-driven-development` skill and begin Task 1 with the failing tests.
