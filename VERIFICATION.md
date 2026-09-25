# Implementation verification

Performed on 2026-09-24 in the provided Windows workspace with Python 3.13.5.

| Check | Result |
|---|---|
| Phase 1: discovery contract, rotation, bounded retries | 3 tests passed before extraction work |
| Phase 2: structured extraction, missing fields and invalid content | 5 cumulative tests passed |
| Phase 3: URL identity and unsafe URL filtering | 11 cumulative tests passed |
| Phase 4: eligibility and verification gates | 14 cumulative tests passed |
| Phase 5: explainable matching and repository dedup | 16 cumulative tests passed |
| Phase 6: sheet dedup and Status/Notes preservation | 18 cumulative tests passed |
| Phase 7: dashboard response and security | 19 cumulative tests passed |
| Phase 8: workflow parsing, schedule and persistence checks | Included in 23 cumulative passing tests |
| Phase 9: adapters, retries, stale jobs, tracking, applicant restrictions and failure isolation | 33 tests passed |
| Ruff lint and formatting | Passed |
| Live Greenhouse public API | One GET against the Stripe board; 695 returned postings normalized with descriptions. No jobs written to the user's sheet or production database by this probe. |
| Live Firecrawl | SKIPPED: FIRECRAWL_API_KEY missing |
| Live Google Sheets | SKIPPED: GOOGLE_SHEET_ID and authentication missing |
| Browser visual inspection | Blocked: browser inventory was empty; in-app browser unavailable |
| Local dashboard | Server started on 127.0.0.1:8000; HTTP/template and tracking tested via Flask client |
| GitHub-hosted execution | Not run; no remote repository/deployment configured |

Automated provider tests use mocks. They establish request/response handling, not live credentials, account quota, remote write permission, or current employer eligibility. The test count reflects this implementation; rerun the documented commands after any modifications.

## Follow-up: two-tab output (2026-09-25)

Added separate persistence and synchronization for partially matching excluded listings, explicit rejection reasons, promotion history, and preservation of user tracking columns. The expanded suite passes 37 tests, including cohort rejection, low-score retention, unrelated-role exclusion, missing application URLs, deduplication, promotion, and notes preservation. Google Sheets read access and initial Opportunities tab creation were successfully tested using the configured service account on 2026-09-25; the earlier missing-credentials result above describes the initial build.

## Live credentials check (2026-09-25)

After the user saved the Firecrawl key, a real pipeline run with one query and a maximum of two scrapes completed without errors. It discovered five search results, excluded all five in pre-scrape filtering, and inserted no jobs into either tab. Both tab syncs completed. A separate small integration probe returned one Firecrawl search result and successfully scraped its ATS page; that page had no single extractable JobPosting, so no job was inferred or exported. Google Sheets read access also passed. This establishes live search, scrape and sheet connectivity, but does not demonstrate a successfully verified job or a populated opportunity row yet.
