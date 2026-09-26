# Multi-user migration checkpoint

User authorized multi-user foundation: individual accounts, hosted PostgreSQL,
private dashboards and resume JSON import/manual profile entry. No paid Gemini
dependency. Later work: user-owned encrypted Firecrawl keys and independent jobs.

Branch: feature/multi-user-foundation. Existing production remains personal mode.
Provider proposal: Supabase PostgreSQL + Auth, invite-only initially. Awaiting
user response about creating a Supabase project. Never paste credentials in chat.

Implemented locally: opt-in Supabase app, RLS migration, email OTP login,
profile JSON validation/review, private dashboard and tracking. Use authenticated
user JWTs for database requests; no service-role key in web routes. Disable legacy
shared imports/search in multi-user mode until per-user queue/budget exists.

Tests: full 110-test suite passed, then the expanded 15-test workspace suite passed
(one extra HTML-escaping/nonfinite-JSON test). CI run 36220047496 passed both Python
and PostgreSQL jobs for foundation commit 63bc565. PostgreSQL 17 isolated Docker test passed:
owner-only reads/writes, rejected cross-user tracking, immutable evidence columns,
anonymous denial, forbidden owner reassignment. CI now repeats PostgreSQL checks.
Setup guide: docs/MULTI_USER_SETUP.md. User is creating first Supabase project;
project instructions already given. No remote database or email configured yet.

Continuation implemented user-owned encrypted Firecrawl connection, queue,
worker-once and polling worker, PostgreSQL budget reservations and four-day
schedule. Apply ALL THREE migrations in filename order. Web worker flag defaults
off; do not enable until separate worker is deployed and verified. New configuration:
PROVIDER_ENCRYPTION_KEY (web+worker), SUPABASE_WORKER_KEY (worker only),
WORKSPACE_WORKER_ENABLED=on (after verification). Tests include vault owner binding,
worker using only claimed user's key, personal profile conversion and connection
key never reflected. PostgreSQL checks include queue/service permissions and
budget cap. A manual 8-way concurrency test accepted exactly 1 available reservation.
Current full suite: 117 tests passed after final fixes. All three migrations and
expanded PostgreSQL security/budget SQL tests passed. Fixed encrypted-key upsert
permissions using an owner-derived RPC (no ciphertext read grant). Temporary
PostgreSQL test container was stopped/removed. Pilot supports India-authorized
users, technical roles and eligible worldwide remote. No paid API calls used.

Next: obtain project URL/public key through safe configuration, apply migration,
configure Auth email OTP template and test accounts (custom SMTP for outside users),
verify actual Supabase two-user isolation and staging UI. Production activation requires database project, migration,
Auth/email setup, env configuration, migration of owner data and live verification.

Resume by reading this file, git status and recent commits. No paid API calls are
required for development. Update this checkpoint before ending a work session.

Supabase project URL and publishable key received and saved outside repository in
LOCALAPPDATA/InternScout/secrets/supabase-workspace.json, with distinct generated
cookie/provider encryption keys. Auth settings endpoint returned HTTP 200;
profiles REST endpoint returned 404 PGRST205 (migration not installed). Combined
three-migration setup SQL prepared at LOCALAPPDATA/InternScout/supabase-setup.sql.
Next user step: run that SQL in Supabase SQL Editor, then configure Auth and staging.
No remote writes, paid calls or production changes in this connection check.

2026-09-26 continuation: user ran combined SQL successfully; all seven REST
endpoints now return 401/42501 for anonymous access (previously PGRST205).
User created their own confirmed Supabase Auth user. Current Supabase UI blocks
email template editing until custom SMTP is configured. User has no domain and
authorized continuing: switched pilot login to email/password for confirmed
accounts, avoiding email delivery. No password received or stored by agent.
119 tests and Ruff passed. Prior queue CI 36231548150 passed both jobs.
Preview deployed READY with per-deployment Supabase env and independent cookie,
provider encryption and signing keys saved in private supabase-workspace.json.
Preview: https://internscout-h8s4xl9ti-sonu-kumars-projects-69a7949b.vercel.app
Vercel preview protection remains enabled. CLI authenticated smoke check:
/login 200 with password form, / 303 to login, workspace.css 200.
Browser visual QA of same login on loopback port 8012 passed.
User asked via async prompt to privately test preview sign-in (Vercel login may
be needed first). Awaiting outcome. Production personal app remains unchanged.
Background worker is still off; no paid calls made. Need service credential
stored privately for worker, hosting, actual two-user isolation, owner data
migration and eventual cutover. Password reset email/public signup require
additional setup; pilot is manually provisioned only. No SMTP needed for login.
