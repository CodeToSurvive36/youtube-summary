# Bilingual Usage-Only README Design

**Status:** Approved design awaiting written-spec review

**Date:** 2026-08-10

## Goal

Rewrite `README.md` as a concise, public, bilingual introduction that explains what `youtube-caption-summary` is and how a Codex user invokes it, without exposing its implementation.

## Non-Goals

- Change `SKILL.md`, scripts, dependencies, runtime behavior, output contracts, or tests unrelated to README content.
- Document installation, implementation architecture, provider selection, network behavior, validation evidence, or development workflows.
- Add a second README file, interactive JavaScript, or HTML tabs.

## Current Behavior

The README currently mixes user guidance with internal details, including dependency names and versions, provider order, script paths and options, internal artifact fields, media-processing tools, download behavior, and validation-oriented descriptions. It is English-first and has no clear language selector.

## Confirmed Public Behavior

### Audience And Trigger

- **Actor:** A GitHub visitor or Codex user who wants to understand and use the skill.
- **Trigger:** The visitor opens `README.md`.
- **Precondition:** The skill is available to Codex and the user has an individual YouTube video URL.
- **Action:** The user selects Chinese or English, reads the matching description, and invokes `$youtube-caption-summary` with a natural-language request and video URL.
- **Observable result:** The README provides enough information to request a transcript-grounded result without revealing how the skill implements caption acquisition or processing.

### Language Selection

The first visible navigation below the title must provide two clickable choices:

```markdown
[中文](#中文) | [English](#english)
```

The Chinese and English sections must contain equivalent user-facing information. Each section must provide a link back to the other language.

### Included Content

Each language section must include:

1. A short description of the skill.
2. A concise capability list covering transcript-grounded summaries, caption extraction, key points or outlines, mentioned items, timestamped navigation, Q&A or notes, requested response languages, optional visual-context requests, and explicit Xiaohongshu requests.
3. Natural-language usage examples based on `$youtube-caption-summary`, including a general summary, an explicitly selected response language, caption extraction, timestamped key points, and Xiaohongshu copy.
4. A short usage boundary explaining that the skill works with one YouTube video at a time, requires accessible captions for transcript-grounded output, and does not fabricate unavailable content.

### Excluded Content

The README must not identify or describe:

- Caption providers, provider order, dependencies, package versions, APIs, fallback orchestration, or network routes.
- Script filenames, command-line flags, subprocesses, installation paths, or internal module names.
- Internal schemas, artifact filenames, JSON fields, attempt records, quality scores, validation fixtures, or test counts.
- Download options, browser state, cookies, speech recognition, media tools, frame-selection algorithms, deduplication algorithms, storyboard behavior, or implementation limitations.

These details may remain in internal skill instructions, source code, plans, specifications, tests, and validation evidence; this change only removes them from the public README.

## Affected Files

- Modify: `README.md`
- Modify: `tests/test_fetch_youtube_transcript.py`

No other file or public runtime contract changes.

## Scenario Matrix

| Requirement | Boundary | Given | When | Then | Test level |
|---|---|---|---|---|---|
| Select either language | README | A visitor opens the README | The visitor uses the top navigation | Chinese and English anchors are available and reciprocal | Static documentation test |
| Learn how to use the skill | README | A visitor selects either language | The visitor reads usage examples | The section shows natural-language `$youtube-caption-summary` requests and a video URL placeholder | Static documentation test |
| Preserve equivalent bilingual guidance | README | Both language sections exist | Their required topics are inspected | Both sections cover the same capabilities, examples, and limits | Static documentation test |
| Hide implementation details | README | The README has been rewritten | Forbidden internal terms are scanned | No implementation-specific term or script command appears | Static documentation test |
| Preserve internal skill contract | `SKILL.md` and runtime tests | README no longer documents internals | Existing skill and runtime tests run | Runtime behavior and internal instructions remain unchanged | Regression test |

## Test Plan

1. Rewrite the existing documentation test so implementation assertions apply only to `SKILL.md`.
2. Add a failing README contract test for bilingual navigation, reciprocal links, required usage examples, and excluded implementation terms.
3. Run the focused test and confirm it fails against the existing README for the intended reasons.
4. Replace README content with the approved bilingual user guide.
5. Run the focused test, complete repository tests, skill validation, and `git diff --check`.

## Acceptance Criteria

- README begins with clickable Chinese and English choices.
- Chinese and English sections are complete and equivalent in scope.
- A user can invoke the skill by adapting natural-language examples without reading code or script documentation.
- README contains no implementation details listed in the exclusion contract.
- `SKILL.md`, scripts, runtime behavior, and historical validation evidence are unchanged.
- Focused and repository verification pass.
