# Google sign-in and registration

The app implements server-side Supabase OAuth with PKCE. Credentials belong in
Supabase's Google provider settings, not in chat, the repository, or browser code.
Google sign-in needs no Gemini API key and sends no email through our SMTP server.

## Google Cloud

Use the existing **InternScout AI** project at Google Cloud.

1. Open **Google Auth Platform** and complete **Branding / Get started** if needed.
   Use InternScout for the app name and your email for support/contact.
2. Set **Audience** to External. Keep Testing while verifying the integration and
   add your own Google email as a test user.
3. In **Data Access**, request only `openid`, `userinfo.email`, and `userinfo.profile`.
4. In **Clients**, create an OAuth client of type **Web application**.
5. Authorized JavaScript origin:
   `https://internscout-ai-preview.vercel.app`
6. Authorized redirect URI (this is Supabase's callback, not our app callback):
   `https://fdkhluwzilyqnvhhmqaw.supabase.co/auth/v1/callback`
7. Copy the client ID and client secret directly into Supabase as described below.

## Supabase

1. Open Authentication > Sign In / Providers > Google. Enable Google and save the
   OAuth client ID and secret. Leave nonce checks enabled.
2. In Authentication > URL Configuration, use this preview Site URL:
   `https://internscout-ai-preview.vercel.app`
3. Add this allowed Redirect URL:
   `https://internscout-ai-preview.vercel.app/auth/callback**`
   The suffix permits the browser-bound `flow` query parameter. Do not use a
   wildcard allowing arbitrary preview hosts.
4. To allow Google to create accounts, enable new-user signups. While piloting,
   keep Google Audience in Testing with only intended testers. Do not disable
   email confirmation to enable public password registration; password signup
   is not exposed by this app.

## Deployment and verification

Only after provider configuration, set:

```
WORKSPACE_GOOGLE_ENABLED=on
WORKSPACE_ORIGIN=https://internscout-ai-preview.vercel.app
```

Redeploy and update the preview alias. The Google button stays hidden until the
flag is enabled; /signup explains the setup status instead of offering a broken
button. The Google client secret is not needed in the app's Vercel environment.

Test consent, cancellation, login, first-time registration, refresh, logout and
two-user data isolation. Never auto-copy the owner's profile to new users. A new
Google signup goes to the profile form and starts no paid API work. Search stays
disabled until the separate worker is configured.

The app stores the PKCE verifier encrypted in an HttpOnly, Secure, SameSite=Lax
cookie session, checks a random browser-bound state with a ten-minute expiry,
and exchanges the one-time code on the server. It saves only the Supabase session
tokens, discarding Google provider tokens. Redirect destinations are allowlisted.

For the public launch, finish Google's production/audience requirements, review
abuse controls and legal pages, and change the callback allowlist and app origin
to the chosen production hostname. Vercel's protection currently restricts preview
access to the owner/team; it is separate from InternScout's user authentication.

References:
- https://supabase.com/docs/guides/auth/social-login/auth-google
- https://supabase.com/docs/guides/auth/redirect-urls
