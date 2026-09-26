-- Run once in a new Supabase project's SQL Editor. No personal data is copied.
begin;
create table public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  document jsonb not null default '{}'::jsonb check (jsonb_typeof(document) = 'object'),
  updated_at timestamptz not null default now()
);
create table public.opportunities (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  fingerprint text not null,
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  screened_out boolean not null default false,
  status text not null default 'NEW' check (status in ('NEW','SAVED','APPLIED','INTERVIEW','REJECTED','CLOSED')),
  notes text not null default '' check (length(notes) <= 5000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(user_id, fingerprint)
);
create index opportunities_owner_date on public.opportunities(user_id, created_at desc);
alter table public.profiles enable row level security;
alter table public.opportunities enable row level security;
create policy profiles_owner on public.profiles for all to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy opportunities_read on public.opportunities for select to authenticated
  using ((select auth.uid()) = user_id);
create policy opportunities_tracking on public.opportunities for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
revoke all on public.profiles, public.opportunities from anon, authenticated;
grant select, insert, update, delete on public.profiles to authenticated;
grant select on public.opportunities to authenticated;
grant update(status, notes) on public.opportunities to authenticated;
-- Only the future trusted worker may write match evidence or insert opportunities.
grant all on public.profiles, public.opportunities to service_role;
commit;
