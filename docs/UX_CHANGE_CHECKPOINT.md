# Approved user changes ? September 27

User authorized all held changes, with work in small chunks to conserve usage.

## Completed in profile chunk
- AI prompt requests exactly one pretty-printed JSON fenced code block.
- AI paste area starts empty and says Paste your AI output here.
- Fill my profile validates imports and populates ordinary editable fields beside it.
- All schema fields (education, skills, projects, experience and preferences) can be edited without JSON.
- Required review/verification checkbox before saving, enforced server-side.
- Existing saved profiles populate editable fields; no profile is copied between users.
- 138 tests and Ruff pass, including imported data round trip and confirmation enforcement.

## Still authorized and pending
- Remove manual search card/button and all budget/schedule controls.
- Enforce fixed 250 credits per rolling seven days server-side AND database-side, not merely hidden inputs. Existing user values must migrate consistently, with reservation history preserved.
- Daily automatic search from 09:00 IST after complete profile and Firecrawl connection. Existing daily database schedule present but worker is OFF/unconfigured. Need durable hosting and private service credential before claiming active automation. Avoid duplicate old personal schedule on cutover.
- Rename link importer Save a job; extract then preview before save.
- Shared catalog for verified job facts visible to all signed-in app users. Keep submitter identity, notes, status, match scores and profile private.
- Suspicious/unverified jobs: warning before explicit save, private to submitting user. No claim of fraud-free verification.
- Existing import only permits known career hosts and saves directly through Pipeline. Must implement staged import/review and safe URL handling before broadening supported links.
- Shared jobs require migration/RLS and cross-user leakage tests; do NOT expose existing private opportunity payloads wholesale.
- Ship next chunks to preview and verify before production cutover.
