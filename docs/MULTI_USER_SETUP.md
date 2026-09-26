# Multi-user foundation

This opt-in mode uses Supabase PostgreSQL and email OTP accounts. It does not read
the existing owner's Sheets, SQLite database or profile.json. Production remains
in personal mode until the new project is configured and verified.

## Project and database

1. Create a Supabase Free project. Save its database password privately.
2. Open SQL Editor and run `supabase/migrations/202609260001_workspaces.sql` once.
3. Profiles are owned by `auth.users.id`. Opportunities have a per-user fingerprint
   constraint. RLS restricts reads and updates to the authenticated owner. Users
   may edit status/notes but cannot insert or change verified match evidence.
4. Test with two real accounts before activation. Confirm neither can read/edit
   the other's profile or opportunities, including through direct REST requests.

## Authentication

- Invite-only: create the initial accounts through Supabase Authentication > Users.
  Disable public new-user signups in Auth settings. The app's OTP call also sets
  `create_user=false`. Do not add public signups until abuse controls are tested.
- Enable email authentication. In its Magic Link email template include
  `<p>Your InternScout sign-in code: {{ .Token }}</p>`.
- Configure custom SMTP before inviting external users. Supabase's default email
  sender is intended for tests, sends only to organization team addresses and has
  very low limits. Do not give users Supabase team access to bypass this restriction.
- Configure OTP expiry and provider rate limits. App resend cooldown is only a UI
  convenience, not a distributed abuse limiter. CAPTCHA/edge limits are required
  before public signup.
- Tokens are held in encrypted HttpOnly/Secure cookies, separate from the signed
  CSRF session. The app verifies users with Auth, refreshes expired access tokens
  and sends the user's JWT to PostgREST. No service-role key is used in web routes.
- Cookies last up to 30 days and renew on token refresh. Sessions may still end
  when revoked or when refresh fails. Logout calls Auth and clears local cookies;
  already-issued JWTs can remain valid until their provider expiry.

## Deployment configuration

Use a separate preview/staging deployment first. Configure:

```
WORKSPACE_BACKEND=supabase
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_PUBLIC_KEY=YOUR_PUBLISHABLE_OR_ANON_KEY
DASHBOARD_SECRET_KEY=YOUR_EXISTING_STRONG_SIGNING_SECRET
WORKSPACE_COOKIE_KEY=GENERATED_FERNET_KEY
```

Generate the encryption key with `cryptography.fernet.Fernet.generate_key()` and
save it directly into a private local secret file / Vercel environment variable.
Do not post secrets in chat, commit them or use the service_role/secret key in
SUPABASE_PUBLIC_KEY. Project URL/public key are under the project's Connect dialog
or API settings. The database password is not needed by this app's REST adapter.

Redeploy after setting environment variables. Roll back to personal mode with
WORKSPACE_BACKEND=personal and another deployment; keep all previous personal
environment variables during the pilot. Do not remove Sheets yet.

## Scope and migration

Implemented: account sign-in, private profile read/write, JSON import validation,
explicit review/confirmation, manual entry, paginated personal opportunity list,
status and notes updates. No paid Gemini or Firecrawl calls in these flows.

Not yet enabled in multi-user mode: API-key connections, per-user search/import
workers, schedules, credit ledger and owner data migration. The existing shared
GitHub dispatcher is deliberately not registered in multi-user mode. Build a
PostgreSQL-backed job queue and encrypted provider-key storage before enabling
discovery, with per-user reservations and locks. Never use the owner's keys for
new users by default.

Keep the old data until owner-only migration is validated: map existing Sheet
records to the owner's authenticated UUID, retain status and notes, deduplicate
by the existing fingerprint function, then verify both tabs before cutover.

References:
- https://supabase.com/docs/guides/auth/auth-email-passwordless
- https://supabase.com/docs/guides/auth/auth-smtp
- https://supabase.com/docs/guides/database/postgres/row-level-security
