# Multi-user foundation

This opt-in mode uses Supabase PostgreSQL and email OTP accounts. It does not read
the existing owner's Sheets, SQLite database or profile.json. Production remains
in personal mode until the new project is configured and verified.

## Project and database

1. Create a Supabase Free project. Save its database password privately.
2. Open SQL Editor and run all three SQL files under `supabase/migrations/` once,
   in filename order (workspaces, discovery_queue, schedule).
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
PROVIDER_ENCRYPTION_KEY=SEPARATE_GENERATED_FERNET_KEY
WORKSPACE_WORKER_ENABLED=off
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

Also implemented: encrypted user-owned Firecrawl connections, connection balance
check, per-user discovery queue, import/search tasks, PostgreSQL credit reservations,
and opt-in Monday/Wednesday/Friday/Sunday schedules after 09:00 Asia/Kolkata.
The existing shared GitHub dispatcher is not registered in multi-user mode.
No owner's API keys or personal profile are inherited by new users.

## Background worker

Run a separate background process with the repository and Python dependencies.
Its environment needs SUPABASE_URL, SUPABASE_WORKER_KEY (service-role JWT or secret
key), PROVIDER_ENCRYPTION_KEY (same value as web app), and WORKSPACE_WORKER_ENABLED=on.
Never place SUPABASE_WORKER_KEY into browser code or the web app's public-key field.

Use `python -m scripts.run_workspace_worker` from the repository root. This checks
the durable queue every minute while idle; stop with Ctrl+C. A process host must
keep it running for schedules to work. `python main.py worker-once` handles one
task for diagnostics. No worker has been provisioned or started in production yet.
Only after testing the worker should the web deployment also set
WORKSPACE_WORKER_ENABLED=on. Until then its submit/settings forms are disabled.

The worker reserves credits atomically before Firecrawl requests; budget failures
prevent paid calls. Ambiguous failures retain reservations. Credentials are bound
to owner/provider inside encrypted payloads. Only the worker can read ciphertext;
web writes use an RPC which derives ownership from auth.uid(). One active task per
user and a 15-minute submission cooldown are enforced by PostgreSQL. Claims expire
after 30 minutes; timed-out tasks fail rather than automatically repeating paid
work. Provider disconnect prevents future key loads, not an already-running task.
Each search is capped at 3 queries, 8 scrapes, 4 free closure checks and zero LLM
calls. This is an application reservation cap, not an account-wide billing cap.

Pilot constraints: users must confirm India work authorization, a single target
graduation year, skills and desired roles. Worldwide remote eligibility is also
checked. Do not infer authorization from residence. Confirm acceptance of
undisclosed compensation explicitly; unpaid roles remain excluded. Exact minimum
stipend filtering is not implemented, so profiles requesting it cannot start a
search. International country-specific eligibility is future work. Weekly targets
are reported but never guaranteed.

Still pending: remote Supabase/Auth/SMTP setup and smoke tests, worker hosting,
live two-account verification, owner-only migration and production cutover.

Keep the old data until owner-only migration is validated: map existing Sheet
records to the owner's authenticated UUID, retain status and notes, deduplicate
by the existing fingerprint function, then verify both tabs before cutover.

References:
- https://supabase.com/docs/guides/auth/auth-email-passwordless
- https://supabase.com/docs/guides/auth/auth-smtp
- https://supabase.com/docs/guides/database/postgres/row-level-security
