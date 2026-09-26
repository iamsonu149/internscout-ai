# InternScout AI

A personal internship discovery and tracking application for an IIT Madras BS Data Science student graduating in 2027. It prioritizes evidence and relevant opportunities over volume, runs locally without an LLM, and uses Google Sheets as its primary output.

**It never applies, submits recruitment forms, sends recruiter emails, or uploads resumes.** The dashboard links to the original application page; you make the final decision.

## Quick start

Use Python 3.13 (also selected for the Vercel runtime). Open a terminal in this folder:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.venv\Scripts\python main.py doctor
.venv\Scripts\python main.py dashboard
```

On macOS/Linux, use `.venv/bin/python` in place of `.venv\Scripts\python` and `cp .env.example .env`.

Open **http://127.0.0.1:8000**. The initial dashboard is intentionally empty: no fabricated jobs, scores, or verification labels are seeded. Stop the local server with Ctrl+C. The following commands assume your virtual environment is activated, or that you substitute its Python path.

Set the credentials described below, then run a small search:

```shell
python main.py search --max-queries 1 --max-scrapes 2
```

Refresh the dashboard after completion. The dashboard's Run a search link opens the exact CLI instructions, rather than starting a paid provider request from a browser click.

## Architecture

```text
Profile-derived rotating queries       Configured public ATS boards
              |                                     |
       Firecrawl Search                  Greenhouse / Lever / Ashby
              |                                     |
 Normalize URLs → deduplicate → known-domain filter  |
              |                                     |
   Bounded Firecrawl Scrape                          |
              |                                     |
     Single JobPosting JSON-LD ← normalized Job schema
                          |
              Evidence-based verification
                          |
            Deterministic eligibility gates
                          |
       Explainable matching → optional capped AI notes
                          |
                SQLite repository/history
                   /                 \
           Google Sheets         Local Flask dashboard
```

Public ATS feeds are processed first so matching search URLs do not spend scrape credits. Search results are hints, never evidence of authenticity. No crawling is enabled by default because individual postings and public APIs are sufficient for V1.

```text
app/config/                     Environment configuration
app/models/                     Job and Match dataclasses
app/services/firecrawl_service.py Search, scrape, query rotation
app/services/job_sources/ats.py  Read-only public board adapters
app/services/job_extractor.py    JSON-LD normalization and explicit unknowns
app/services/deduplicator.py     Safe URL normalization and fingerprints
app/services/verifier.py        Evidence and source trust
app/services/eligibility.py     Hard exclusions and uncertainty
app/services/matcher.py         Weighted matching and optional AI interface
app/services/google_sheets.py   Authentication, schema, idempotent sync
app/services/pipeline.py        Orchestration, budgets, isolated failures
app/database/repository.py      SQLite persistence behind a repository
app/dashboard.py               Local-only dashboard and tracking
app/templates/, app/static/     Dashboard UI
scripts/smoke_integrations.py   Small opt-in live provider checks
tests/                         Offline contract and regression tests
.github/workflows/             Daily discovery and CI
```

## Profile and matching

`profile.json` contains the supplied education, CGPA, 2027 graduation, skills, experience, projects, and DSA achievements. Edit this file to change your targets. Project keyword associations are matching heuristics based on project names, not claims about an undocumented implementation. Creature Lab has no assumed stack. Resume upload/parsing is not part of V1; manually add evidence to this profile.

Queries rotate deterministically by day and derive role names, country, and graduation year from the profile. V1 eligibility geography is specifically India plus explicitly worldwide remote positions. If you change countries, update the location gate rather than assuming profile changes alone adapt it.

The score is **relevance, not an interview probability**. Its components are shown per job:

| Component | Maximum | Rule |
|---|---:|---|
| Role relevance | 30 | Primary target 30; secondary technical target 22 |
| Skills | 30 | Proportion of recognized job skills also in the profile |
| Eligibility | 20 | Passing gates with stated compatible graduation: 20; cohort unstated: 12 |
| Education | 10 | Recognized undergraduate/related-field requirement |
| Experience and projects | 10 | 5 for evidenced experience skill overlap; 5 for relevant project keywords |

Missing data receives no invented skill or education credit. All recognized skills in the description count, including preferred skills; the score is deliberately simple and may be conservative. Default export threshold is 60. Strong matches are fresh, trusted jobs scoring at least 80.

Eligibility rejects non-internship roles, unrelated/senior titles, unclear India eligibility, explicit incompatible graduation years, passed/unparseable deadlines, closed postings, and multiple required years of experience absent from the profile. Remote alone does **not** prove eligibility from India. Unknown cohort/deadline remains a visible concern. Natural-language requirements can be ambiguous; always read the original posting.

## Trust policy and deliberate coverage limits

| Status | Evidence | Sheet handling |
|---|---|---|
| `VERIFIED_OFFICIAL` | Successfully fetched single JobPosting, matching company/domain allowlist, description and trusted application destination | Eligible for export after matching |
| `VERIFIED_ATS` | Active job in a configured public board, or fetched single structured posting on an exact recognized ATS hostname | Eligible for export after matching |
| `LIKELY_GENUINE` | Reserved for a future audited manual-review adapter | Supported by export policy, never automatically assigned in V1 |
| `UNVERIFIED` | Missing ownership, content, application destination, or evidence | Never newly exported |
| `REJECTED` | Unsafe URL, suspicious fee claim, or known invalid/expired existing posting | Never newly exported |

Allowed ATS hostnames include Greenhouse, Lever, Ashby, and Workable. Public API adapters exist for the first three. Workable uses the Firecrawl/structured-page path. Hostnames are matched exactly, so `jobs.lever.co.evil.example` is not trusted. Source configuration is a trust root: verify a company's official career page links to its board before adding its mapping.

For website extraction, V1 requires exactly one `JobPosting` JSON-LD object. Pages with only prose, multiple postings, login walls, malformed data, or no application link/form are excluded. This is a deliberate false-negative tradeoff; it does **not** imply an excluded listing is fake. Add a public ATS board to improve coverage without extra Firecrawl scrapes.

No system can guarantee every live listing remains open or every employer is genuine. Verification records what was observed, not an endorsement. Old evidence is labelled stale in the dashboard; sync excludes stale jobs from new exports and changes existing stale sheet rows to UNVERIFIED. Explicit deadlines are rechecked at sync. Status remains yours, even when evidence changes. Deleted ATS posts become stale when not seen again; they are not automatically marked CLOSED merely because a feed temporarily omits them.

## Firecrawl setup and budgets

1. Obtain a Firecrawl API key in your own account.
2. Set `FIRECRAWL_API_KEY` in `.env` (never in Python files).
3. Start with `MAX_QUERIES=1`, `RESULTS_PER_QUERY=2`, and `MAX_SCRAPES=2`.
4. Run `python scripts/smoke_integrations.py --firecrawl` for one search and at most one scrape. This consumes your provider quota.
5. Run discovery with `python main.py search --no-sync` until satisfied with the output.

Local defaults cap each run at 3 searches and 8 scrapes, with 5 results per search. Scheduled runs use 8 searches (10 results each) and at most 24 scrapes. Paid Firecrawl requests are attempted once, without automatic retries. Search does not request automatic scraping. Document URLs are blocked; PDF parsers are explicitly disabled and proxies are restricted to basic. An opaque URL redirecting to a PDF can incur a base fetch charge, but no PDF page parsing is requested. Caps are **per run**, not per day. Read your Firecrawl account usage and reduce budgets or schedule frequency to stay within your allowance. Free-first does not guarantee unlimited free usage. The app never provisions paid infrastructure.

Firecrawl Search uses `/v2/search` with web results and no automatic scrape options. Scrape requests markdown and raw HTML, with a fresh fetch. API success and underlying page status are checked separately. Unknown/untrusted discovery domains are excluded before scraping. Successfully handled URLs are cached for `RECHECK_DAYS`; failed requests remain retryable.

## Public ATS sources and official domains

`sources.json` includes five live employer boards: Razorpay, Sarvam AI, Supabase, Deepgram and Browserbase. Each mapping records its official careers-page evidence and verification date. These direct API reads do not use Firecrawl credits. Add boards only after verifying them via the company's official website:

```json
{
  "boards": [
    {"type": "greenhouse", "slug": "YOUR_BOARD_TOKEN", "company": "Exact Company Name"},
    {"type": "lever", "slug": "YOUR_SITE_NAME", "company": "Another Company"},
    {"type": "ashby", "slug": "YOUR_BOARD_NAME", "company": "Third Company"}
  ],
  "official_domains": {
    "careers.your-company.example": "Exact Company Name"
  }
}
```

Replace placeholders; they are not working board identifiers. Remove unused entries. Map the exact career hostname and the employer name used by the job's structured data. These adapters use public GET endpoints only, never application submission endpoints. No API keys are needed for these board reads. Follow the provider's terms and rate limits. No authenticated/private job boards are bypassed.

## Google Sheets setup

The spreadsheet now has two output tabs:

- **Opportunities**: jobs that pass verification, eligibility, and the minimum match score.
- **Rejected Matches**: an audit of technical internships with at least one matching skill that were excluded. Includes **Rejection Reason**, **Screening Decision**, and **Last Evaluated (UTC)** in addition to the usual job and match columns. Examples: incompatible graduation cohort, unclear India eligibility, unverified source, expired listing, or a score below the threshold.

Rejected Matches is an audit, not a list of recommended or verified openings. The Verification Status column retains the actual evidence status. Unrelated jobs and unextractable search snippets are not inserted. Rejection means the app excluded the listing, not that the employer rejected an application. Status remains user-managed in both tabs. If a rejected listing later qualifies, it is added to Opportunities and its audit entry becomes **NOW MATCHED**; the historical rejection reason is retained. Existing tracked opportunities are not deleted if later excluded, preserving your application history.

Both `search` and `sync` synchronize both tabs. `GOOGLE_REJECTED_SHEET_TAB` changes the audit tab name (default `Rejected Matches`); it must differ from `GOOGLE_SHEET_TAB`. The additive `rejected_matches` SQLite table stores these records separately from accepted jobs. It does not require external AI or additional Firecrawl requests. Jobs excluded before this feature was enabled cannot be reconstructed from snippets; they appear after being processed again.

Create a dedicated spreadsheet, or use an existing spreadsheet with a dedicated empty tab. The default tab name is `Opportunities`. Copy the spreadsheet ID from the part between `/d/` and `/edit` in its URL into `GOOGLE_SHEET_ID`.

### Service account (recommended for scheduled execution)

1. In Google Cloud Console, select/create a project and enable **Google Sheets API**.
2. Create a service account. You do not need broad project Editor permissions for this app.
3. Create/download its JSON key and store it **outside the repository**, or under the ignored `secrets/` directory.
4. Share only the target spreadsheet with the service account's `client_email` as **Editor**.
5. Set `GOOGLE_APPLICATION_CREDENTIALS` to that JSON file's path. Alternatively, put its full JSON in `GOOGLE_CREDENTIALS_JSON`, suitable for a secret manager or GitHub Actions secret.
6. Set `GOOGLE_SHEET_ID`, and optionally `GOOGLE_SHEET_TAB`.
7. Run `python scripts/smoke_integrations.py --sheets`. This checks access **without writing**.
8. Run `python main.py sync` to initialize the tab and export qualified local jobs.

The app initializes 19 exact headers, freezes the header, adds a filter, and adds Status validation. It refuses a nonempty tab whose header differs. Choose a new tab instead of forcing it to overwrite another table.

### OAuth alternative

Service accounts are simpler for unattended use, but authorized-user OAuth credentials are supported:

1. Enable Sheets API and configure your Google OAuth consent screen and test user as appropriate.
2. Create a Desktop OAuth client; download its client configuration outside the repository.
3. In your own terminal, install `google-auth-oauthlib` for this **one-time setup** and run an InstalledAppFlow requesting only `https://www.googleapis.com/auth/spreadsheets`. Use `access_type='offline'` and `prompt='consent'` to obtain a refresh token:

```python
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file(
    "secrets/oauth-client.json",
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)
credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
Path("secrets/oauth-user.json").write_text(credentials.to_json(), encoding="utf-8")
```

4. Set `GOOGLE_OAUTH_FILE=secrets/oauth-user.json` and leave both service-account variables blank. The authenticated user must have edit access to the spreadsheet. AuthorizedSession refreshes credentials in memory from the refresh token. OAuth testing-mode tokens may expire or be revoked; use a properly configured OAuth application or the service-account route for stable scheduling.

Do not commit OAuth client files, refresh tokens, or service-account keys. Authentication precedence is inline service-account JSON, then service-account file, then OAuth file.

### Columns, deduplication and ownership

The sheet has: Date Found, Company, Job Title, Location, Remote, Internship Type, Match Score, Eligibility, Matching Skills, Missing Skills, Stipend/Salary, Deadline, Verification Status, Job URL, Status.

New jobs start with `NEW`. Supported tracking states: `NEW`, `SAVED`, `APPLIED`, `INTERVIEW`, `REJECTED`, `CLOSED`.

Job URLs and company/title/location fingerprints are checked against the sheet itself, including after a local database loss. Existing rows update **B:N only**. Date Found and Status are preserved. Sheet tracking values are mirrored locally during sync. Local dashboard tracking is useful before export; after export, edit tracking **in the sheet** because sheet values win on the next sync.

Writes use `RAW` values, so text starting with `=` is not executed as a spreadsheet formula. Fixed row ranges make transport retries idempotent. A local file lock prevents simultaneous local search/sync processes; GitHub concurrency prevents overlapping scheduled runs. **Do not run a local writer and GitHub writer simultaneously against the same tab**, and avoid sorting/inserting/deleting rows while sync runs. There is no distributed lock or Google Sheets transactional compare-and-swap in V1. Existing duplicate rows are preserved rather than deleted.

## Environment reference

See `.env.example` for defaults.

| Variable | Purpose |
|---|---|
| `FIRECRAWL_API_KEY` | Search/scrape authentication; optional when only public boards are used |
| `DATABASE_PATH` | SQLite location; default `data/internscout.db` |
| `PROFILE_PATH`, `SOURCES_PATH` | Profile and trusted source JSON files |
| `MAX_QUERIES`, `RESULTS_PER_QUERY`, `MAX_SCRAPES` | Per-run Firecrawl budgets |
| `MIN_MATCH_SCORE` | Minimum score stored/exported; default 60 |
| `RECHECK_DAYS` | Discovery URL cache and freshness window; default 7 |
| `GOOGLE_SHEET_ID`, `GOOGLE_SHEET_TAB` | Primary output spreadsheet and tab |
| `GOOGLE_APPLICATION_CREDENTIALS` | Service-account JSON path |
| `GOOGLE_CREDENTIALS_JSON` | Service-account JSON environment secret |
| `GOOGLE_OAUTH_FILE` | Authorized-user JSON with refresh token |
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` | Optional OpenAI-compatible provider; base ends at API prefix, e.g. `/v1` |
| `MAX_LLM_CALLS` | Maximum optional notes calls per run; default 0, hard maximum 30 |

External AI is off by default. When explicitly configured, only already verified, eligible jobs above the score threshold are sent. It receives the job description and matching explanation, not a resume file. The provider may receive project names in that explanation. AI notes are advisory and cannot change scores, trust, or eligibility. AI failures do not discard deterministic matches. No specific provider is required.

## Commands and scheduling

### Dashboard connected to Google Sheets

When `GOOGLE_SHEET_ID` and Google credentials are configured, the dashboard reads Opportunities and Rejected Matches directly, including cloud-discovered jobs and user-edited Status. **Screened out** shows the rejection audit separately from your own application statuses. **Refresh from Sheets** bypasses the one-minute in-memory cache; ordinary navigation reuses it. Reading the dashboard never initializes tabs, exports jobs, calls Firecrawl, or changes sheet cells. Edit status using its Google Sheets link. A failed refresh displays a warning and retains the last loaded snapshot; it does not silently substitute an empty local database. The dashboard shows sheet load time, not an invented cloud search/verification timestamp. Full per-component score details and cloud run metrics are not present in the sheet and are not reconstructed.

Without a configured spreadsheet, the local SQLite view and local tracking forms remain available. Google credentials stay server-side. Public dashboard deployment is optional and separate from scheduled discovery: the existing GitHub automation already runs without your laptop. This server is still loopback-only; opening the dashboard from another device would require a separately secured deployment with authentication, HTTPS and server-side secrets.

```shell
python main.py doctor                         # Configuration presence, never secret values
python main.py search                         # Discovery + sync when Sheet ID is configured
python main.py search --no-sync               # Local-only output
python main.py search --max-queries 1 --max-scrapes 2
python main.py sync                           # Retry export without repeating discovery
python main.py dashboard --port 8000
```

If no source succeeds, the run is FAILED and exits nonzero. A source/job/Sheets failure can produce PARTIAL while retaining successfully processed jobs. Zero discovered jobs from a successful source can legitimately be COMPLETED. A missing Sheet ID means local-only mode; the scheduled workflow separately requires Sheets secrets.

### GitHub Actions: no laptop required

1. Create a preferably **private** GitHub repository, review `profile.json` and your source mappings, and push this project yourself. Never push `.env`, `data/`, or secret files.
2. Add repository Actions secrets: `FIRECRAWL_API_KEY` (unless using public boards only), `GOOGLE_CREDENTIALS_JSON` (the entire service-account JSON), and `GOOGLE_SHEET_ID`.
3. Optional Actions variables: `GOOGLE_SHEET_TAB`, `MAX_QUERIES`, `MAX_SCRAPES`, `RESULTS_PER_QUERY`.
4. Run **Daily internship discovery → Run workflow** manually and inspect its logs and sheet output.
5. `.github/workflows/job_search.yml` runs Monday, Wednesday, Friday and Sunday at `30 3 * * 0,1,3,5`: **03:30 UTC / 09:00 IST**. Edit the cron in that file to change the schedule. GitHub schedule events use UTC, run from the default branch, and can be delayed; this is not an exact-time SLA.

The workflow installs dependencies, restores SQLite history, runs discovery/sync, and saves history even after partial failures. It grants read-only repository permissions. The separate tests workflow runs on pushes and PRs without integration secrets. GitHub cache is best-effort and may be evicted; it is not a database backup. Sheet-level dedup still prevents ordinary reinsertion if history is lost. Keep independent backups of local data if its history matters. Do not enable workflows for untrusted branches with your secrets. No deployment or repository push is performed by the application.

GitHub caches may be readable by code with access to the repository's workflows. Use a private repository for personal profile/history. Usage must stay within your account's current Actions and provider allowances. n8n and Telegram are not required or implemented in V1.

## Database and recovery

SQLite uses WAL and parameterized queries. `jobs` holds canonical posting JSON and tracking; `job_matches` holds the score breakdown; `sources` caches URL attempts/outcomes; `search_runs` holds timestamps, state, and counts. `job_urls` preserves canonical URL aliases. Matching URL/fingerprint updates preserve discovery date and personal tracking. Stronger source evidence can replace weaker evidence.

The repository is the persistence boundary for a later PostgreSQL/Supabase adapter. This project does not claim a zero-code database migration. Stop active writers before backing up or replacing SQLite. Keep its WAL alongside the database when taking a live filesystem copy, or use SQLite's backup API. CLI and dashboard must use the same `DATABASE_PATH`.

## Tests and verification

```shell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python scripts/smoke_integrations.py --firecrawl --sheets
```

The offline suite exercises API response contracts, page-status failures, retries, extraction, URL safety, duplicate handling, expiry/eligibility, explainable matching, persistence, per-job failure isolation, sheet tracking preservation, formula safety, stale exports, dashboard security and tracking, and workflow structure. It does not require real credentials or write to a real sheet.

Live smoke checks are opt-in and explicitly print SKIPPED when credentials are absent. A passing Sheets smoke check proves read access only; run `sync` and inspect the real sheet to verify write permission. Automated end-to-end tests use injected provider fakes; they are not evidence of live authenticated integration. See `VERIFICATION.md` for checks actually run during this implementation.

## Troubleshooting

- **No jobs:** check `doctor`, run logs and source mappings. Conservative extraction, unknown remote eligibility, or a high score threshold can legitimately return no jobs. Search snippets are never promoted to jobs. Add reviewed public ATS boards for coverage.
- **Firecrawl 401/403:** check the key/account; **429:** reduce budgets or frequency. Bounded retry/backoff handles temporary throttling, not exhausted credits.
- **Sheet 403:** enable Sheets API and share the spreadsheet with the service account email, or check OAuth scopes/user permissions.
- **Sheet 404:** check the ID and the authenticated principal's access.
- **Header mismatch:** use an empty dedicated tab with `GOOGLE_SHEET_TAB`; do not rename/reorder required columns.
- **PARTIAL run:** check sanitized structured events by stage, fix the failing source, then rerun. Use `sync` to retry only Sheets.
- **Locked database/run:** another search/sync process is active. Allow it to finish. Do not delete a lock while a writer is running.
- **Stale evidence:** use the original URL, wait for the recheck window, or run the configured public board again. Never assume a cached verified label guarantees a role is still open.
- **Only generic error types in logs:** credentials/provider response bodies are intentionally not logged. Check configuration and provider dashboards; do not paste secret-bearing tracebacks into public issues.
- **Dashboard inaccessible:** it binds only to `127.0.0.1`. It is for the local machine, not a public web service. Production discovery runs through the CLI/Actions; the Flask development server is not an Internet deployment.

## Extending sources and trust

Implement an adapter with `fetch(board) -> list[Job]`, normalize unknowns to `None`, and supply auditable evidence. Wire it into the pipeline's public-source stage (or extend `PublicATS` for another documented public API). Add fixtures for malformed, expired, duplicate, foreign-location and active postings before adding a trust rule. Do not automatically mark a new host or a search snippet verified. Additional web extraction formats need evidence-preserving parsers, not guessed company/title fields. If you implement human-reviewed LIKELY_GENUINE, persist the reviewer, timestamp and evidence before allowing export.

Security defaults include ignored secrets/data, loopback binding, trusted Host checks, per-session CSRF tokens, Jinja escaping, CSP, normalized HTTP(S) links, no arbitrary application submissions, sanitized logs, and no AI tool execution. File paths and source JSON are trusted local configuration. Never expose this personal dashboard publicly without adding proper authentication and a production server.

## Provider contracts referenced

### Private Vercel dashboard

`wsgi.py` is the hosted entrypoint, configured via `tool.vercel.entrypoint`. The build copies dashboard and login CSS plus the password-toggle script into `public/static/`; `.vercelignore` excludes local secrets, databases and development files. Configure these Vercel production environment variables before deploying:

- `DASHBOARD_MODE=hosted`
- `DASHBOARD_USERNAME=internscout`
- `DASHBOARD_PASSWORD`: a randomly generated secret of at least 24 characters
- `DASHBOARD_SECRET_KEY`: a stable random value of at least 32 characters
- `GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS_JSON`
- `GOOGLE_SHEET_TAB=Opportunities`, `GOOGLE_REJECTED_SHEET_TAB=Rejected Matches`

Run `vercel --prod` using Vercel CLI 48.2.10 or later. The hosted app fails closed without the required login secrets. Private routes redirect to the styled `/login` page; login assets are public and contain no private data. Credentials have no automatic expiration. With **Keep me signed in** checked, a Secure, HttpOnly session cookie lasts 365 days and renews on activity. Without it, the cookie lasts for the browser session. Sessions survive redeployments while the configured credentials and signing key remain unchanged. Sign out or clear cookies to remove the browser's session; on shared devices, leave the checkbox unchecked and sign out when finished.

The app accepts Vercel hostnames; configure explicit trusted hosts before adding a custom domain. It uses no SQLite database on Vercel. Firecrawl keys are not needed or uploaded for the dashboard. GitHub Actions remains responsible for scheduled discovery and sheet updates.

Rotate the dashboard password or signing key through Vercel environment settings and redeploy to invalidate existing sessions. In-memory cached data may be discarded on serverless cold starts; Google Sheets remains the source of truth. This personal single-user login is not a multi-user account system.

- [Firecrawl Search](https://docs.firecrawl.dev/api-reference/endpoint/search) and [Scrape](https://docs.firecrawl.dev/api-reference/endpoint/scrape)
- [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html)
- [Lever Postings API](https://github.com/lever/postings-api)
- [Ashby public job postings API](https://developers.ashbyhq.com/docs/public-job-posting-api)
- [Google Sheets values.batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/batchUpdate)

### Weekly quality target

The scheduled workflow requires a match score of at least 80 alongside source verification, geography, graduation, expiry and experience checks. It targets 20 new strong matches per rolling seven days, not a guaranteed quota. Logs and the GitHub run summary report the count and shortfall; the target never weakens filters or triggers unlimited paid searches. Unknown graduation wording remains an explicit caveat. Existing sheet rows are retained, not silently deleted.

Four scheduled runs allow at most 32 search requests (320 result slots) and 96 scrape requests per week, also subject to the rolling credit limit. The rolling 400-credit guard covers requests using the same restored InternScout database. Other apps and a separate laptop database are not covered by the cloud ledger. Review provider usage before increasing limits.

PDF and proxy safeguards follow the [Firecrawl scraping guide](https://github.com/firecrawl/firecrawl-docs/blob/main/advanced-scraping-guide.mdx) and [proxy documentation](https://docs.firecrawl.dev/features/stealth-mode).

### Compact spreadsheet columns

Both tabs omit Relevant Projects, Source, Application URL and Notes. Job URL remains the posting link and deduplication key; the hosted dashboard opens this URL. Status remains user-managed and is preserved by synchronization. Last Evaluated (UTC) uses `YYYY-MM-DD HH:MM`, without seconds. Internal application URLs are still required to verify destinations, but are not exported as a separate column. Existing user notes are not collected from the sheet. The hosted reader accepts both old and compact header layouts during deployment; exports require the exact compact headers.

### Compensation policy

Internships with undisclosed compensation are accepted if all other quality checks pass, with a visible pay-unconfirmed caveat. Explicitly unpaid, zero-pay or conditional-only compensation remains excluded. Benefits and company reputation do not prove pay. The dashboard includes historic unknown-pay listings with the same caveat; confirmed stipend information is preserved when available. The target remains 20 technical matches per rolling week, with score 80 and the existing four-day schedule and credit limits unchanged.

### Coverage, eligibility, closure checks and spending audit

The profile records full-time availability, up to 10 hours/day, no duration limit, and availability from 2026-09-25. Location remains India or remote with established India eligibility; overseas work authorization is not assumed. Explicit BTech/BE-only wording is rejected for the BS degree; CS-specific wording without related-field language is flagged for confirmation. Start dates and hours are compared only where explicit and parseable. Ambiguous wording remains a caveat, not invented eligibility.

Required skill mentions carry twice the weight of general mentions; preferred mentions carry half the weight. Missing required skills appear as review concerns because postings may offer alternatives. The score remains a heuristic, not a hiring prediction. Eligibility concerns are exported in the existing Eligibility column.

Up to eight saved postings per run receive bounded direct HTML availability checks at allowed hosts, without Firecrawl. Explicit closure text or HTTP 404/410 invalidates verification while preserving the user's Status/history. Timeouts, blocking, redirects to unrelated hosts, PDFs and generic careers pages remain inconclusive. Listings returned by a direct board are refreshed through the normal pipeline.

The SQLite credit ledger atomically reserves credits before each paid request. Normal web searches reserve two credits per ten requested results (rounded up); basic HTML scrapes reserve one. These rates were checked against [Firecrawl's search documentation](https://docs.firecrawl.dev/features/search). Reported charges are stored separately, and the larger of reservation/reported cost counts toward the rolling seven-day ceiling. Missing responses/timeouts retain reservations. Historic pre-ledger runs receive conservative estimates; these are not invoices. A changed provider rate can exceed the reservation for the current request; any reported overage reduces further available budget.

An authenticated [account credit-balance check](https://docs.firecrawl.dev/api-reference/endpoint/credit-usage) must succeed before the first paid request. Missing history in GitHub Actions blocks discovery rather than resetting the budget. The cache must be retained: deleting it, independent machines, other Firecrawl tools, or a process crash before cloud cache persistence are outside the ledger's guarantees. No API keys or provider response bodies are written to the ledger or report.

Each cloud run includes a weekly summary and a downloadable `weekly-report.json` artifact: unique strong matches, confirmed/undisclosed pay, target shortfall, rejection reasons, closure checks, conservative budget usage and provider-reported charges. Paid budget exhaustion does not prevent direct company feeds or Sheet synchronization. A failed source is reported as partial coverage.

Instabase remains in an unavailable-board audit entry because its officially linked Greenhouse API returned 404. Firecrawl 401/402/403/429 responses stop further paid requests for that run. ATS application-page aliases are normalized to the job-detail URL, board indexes are excluded, and clearly foreign-only search snippets are screened before scraping.

### Signed-in link imports

The hosted dashboard includes **Import internship from a link**. Both GET and POST require a signed-in hosted session; POST additionally validates CSRF and a single-use submission nonce. Local loopback access cannot dispatch imports.

Configure `GITHUB_IMPORT_TOKEN` in Vercel (Production), then redeploy. Use a fine-grained GitHub personal access token restricted to `iamsonu149/internscout-ai`, with repository **Actions: read and write**. Keep it out of source control and chat. The website uses it only to check workflow activity and dispatch the existing worker; Firecrawl and Google credentials stay in GitHub Actions.

Imports accept individual supported ATS postings (Greenhouse, Lever, Ashby, Workable) or configured official domains. They reject documents and unsafe/unverified hosts before requesting Firecrawl. Exactly one scrape is attempted, with the same durable rolling credit ledger and concurrency group as discovery. No crawl, search, or PDF parsing is requested. Structured JobPosting content is required; unsupported pages fail without inventing fields. Existing saved URLs reuse evidence and retry sheet sync without another scrape. The usual verification, eligibility, compensation, matching and deduplication rules determine output tabs.

The confirmation links to private GitHub import history. Select the run matching the displayed reference to read its result. Refresh the dashboard after completion. While a worker is active, new imports are refused to avoid replacing pending work. Concurrent requests can still race at dispatch; check run history if a submission is cancelled. Imports do not guarantee a row when extraction or screening fails.
