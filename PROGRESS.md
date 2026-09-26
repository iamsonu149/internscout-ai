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

Tests: 110 application tests pass. PostgreSQL 17 isolated Docker test passed:
owner-only reads/writes, rejected cross-user tracking, immutable evidence columns,
anonymous denial, forbidden owner reassignment. CI now repeats PostgreSQL checks.
Setup guide: docs/MULTI_USER_SETUP.md. User is creating first Supabase project;
project instructions already given. No remote database or email configured yet.

Next: obtain project URL/public key through safe configuration, apply migration,
configure Auth email OTP template and test accounts (custom SMTP for outside users),
verify actual Supabase two-user isolation and staging UI. Production activation requires database project, migration,
Auth/email setup, env configuration, migration of owner data and live verification.

Resume by reading this file, git status and recent commits. No paid API calls are
required for development. Update this checkpoint before ending a work session.
