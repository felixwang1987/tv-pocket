# Self-Service Source Submission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the repository owner submit a public source or source page from the website, then include it in the existing daily verified-source pipeline.

**Architecture:** A static form pre-fills a GitHub Issue form. The existing update workflow accepts owner-authored source Issues, validates and persists their fields, then runs the same collection and Pages deployment. The collector supports bounded generic HTML extraction alongside the existing Discuz parser.

**Tech Stack:** Static HTML/CSS/JavaScript, Python 3.12 standard library, GitHub Issue Forms and Actions, unittest.

**Spec:** `docs/superpowers/specs/2026-09-26-self-service-source-submission-design.md`

## Global Constraints

- Keep the fixed `checked/routes.json` and `checked/live.m3u` URLs and existing real-frame verification gate.
- Never put a GitHub personal token or user credential in HTML, config, logs, or shared memory.
- Only owner-authored Issues may change config. Reject private/local destinations, credentials, and credential-bearing query parameters.
- Keep 500 KB/10 second page fetch bounds, 30 links per generic page, 30 source pages, 100 direct sources, and 360 daily candidates.
- Preserve the existing Discuz/GBK extractor and ordinary/adult classification.

## Review Focus

- Owner name casing: compare GitHub logins case-insensitively; a case-only mismatch must still accept the owner.
- Issue body manipulation: reject missing, duplicate, or extra machine fields instead of accepting ambiguous values.
- Query secrets: reject encoded or mixed-case `token`, `pwd`, `key`, `auth`, `sign`, `session`, and related keys.
- HTML links outside content: ignore scripts/styles/navigation and non-source file extensions.
- First-check failure: show a newly pinned direct source as failed in the catalog without promoting it to either verified import file.

---

### Task 1: Validated, idempotent Issue intake

**Files:** Create `scripts/source_intake.py`, `tests/test_source_intake.py`, `.github/ISSUE_TEMPLATE/source.yml`; modify `sources.config.json` only at runtime.

**Interfaces:** `parse_submission(event: dict, owner: str) -> dict | None`; `apply_submission(config: dict, submission: dict) -> tuple[dict, str]`; CLI reads `GITHUB_EVENT_PATH`, `GITHUB_REPOSITORY_OWNER`, writes config and a small result to `GITHUB_OUTPUT`.

- [ ] Write tests for owner/non-owner, all four fields, duplicate/extra/missing fields, public/private/credential URLs, encoded query keys, name/category/type lengths, duplicate URL, and 100/30 item limits.
- [ ] Run `python3 -m unittest tests.test_source_intake -v`; expect failures for missing module.
- [ ] Implement parser/validator and deterministic atomic config update. Use existing collector URL normalization and public DNS validation; use issue event JSON only as data.
- [ ] Run targeted tests to pass; add the Issue form with stable field IDs `source_name`, `source_url`, `source_type`, `source_category`.
- [ ] Commit Task 1.

### Task 2: Bounded collection and failed-source visibility

**Files:** Modify `scripts/collector.py`, `tests/test_collector.py`, `README.md`.

**Interfaces:** `extract_generic_page_links(html: str, base: str) -> list[dict]`; `discover_source_pages` chooses `parser='links'` for self-added pages and preserves existing `post_id_prefix` behavior.

- [ ] Write tests for ordinary/adult provenance, valid static anchors and plain visible URLs, no navigation/script/style/image/credential links, GBK Discuz regression, 30-page/30-link caps, and first-check failure retained for pinned seeds.
- [ ] Run `python3 -m unittest tests.test_collector -v`; expect new tests to fail.
- [ ] Implement extraction, bounds, classification propagation, and failed-seed retention without changing verified playlist/route predicates.
- [ ] Run targeted tests to pass and commit Task 2.

### Task 3: Static submission UI

**Files:** Modify `index.html`, `tests/test_build.py`, `README.md`.

**Interfaces:** Form submits no network request itself; builds `https://github.com/felixwang1987/tv-pocket/issues/new?template=source.yml&...` with the four Issue form field IDs as URL parameters.

- [ ] Write an HTML/build test for the form fields, safe URL construction, public-issue warning, mobile layout, and unchanged fixed import URLs.
- [ ] Run `python3 -m unittest tests.test_build -v`; expect the new test to fail.
- [ ] Add the form and concise explanation. Validate HTTP(S), reject obvious credentials, then navigate to the prefilled GitHub form. Never label this as a completed addition.
- [ ] Run targeted tests to pass; inspect desktop/mobile rendering; commit Task 3.

### Task 4: Issue-triggered collection and feedback

**Files:** Modify `.github/workflows/update.yml`, `README.md`; add workflow assertions to `tests/test_build.py` or `tests/test_source_intake.py`.

**Interfaces:** Add `issues: {types: [opened]}` to the existing workflow; conditional owner/title gate; intake result controls whether the collector runs; final Issue comment reports accepted/duplicate/invalid and points to the run or site.

- [ ] Write workflow tests for owner gate, event-path parsing, permissions, current schedule, single-run collection/deployment, and no unsafe Issue-body shell interpolation.
- [ ] Run targeted tests to confirm failure.
- [ ] Extend the workflow with intake, outcome handling, and Issue feedback. Keep one concurrency group and ensure accepted Issue runs complete collection/deployment in that same run, since `GITHUB_TOKEN` pushes do not retrigger `push` workflows.
- [ ] Run full `python3 -m unittest discover -s tests -v`, `python3 scripts/build.py --site`, syntax/format checks, and commit Task 4.

### Task 5: Publish and verify

**Files:** Update `QA.md`; synchronize local OneDrive project and full ZIP from Git-tracked files.

- [ ] Push completed commits to `main` after pulling any newer data snapshot; wait for the triggered Actions run and Pages deploy to finish.
- [ ] Confirm the live form and both fixed import URLs, check catalog/route/live output and Actions results; submit a harmless owner test Issue only if needed to prove the Issue path, then inspect its result.
- [ ] Update `QA.md` with observed facts, commit and push docs, synchronize the local project and rebuild/test the ZIP.
- [ ] Save a concise dated shared-memory handoff with tested outcomes and remaining limitations.
