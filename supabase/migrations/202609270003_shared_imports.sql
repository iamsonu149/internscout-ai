begin;
-- Fixed product policy. The ledger and prior reservations are preserved.
update public.discovery_settings set weekly_limit=250,schedule_enabled=true;
revoke all on public.discovery_settings from authenticated;
revoke update(weekly_limit,schedule_enabled) on public.discovery_settings from authenticated;
alter table public.discovery_settings add constraint fixed_budget check(weekly_limit=250);
alter table public.discovery_settings add constraint automatic_schedule check(schedule_enabled);

create table public.job_previews (
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references auth.users(id) on delete cascade,
 task_id uuid not null unique references public.discovery_tasks(id),
 fingerprint text not null,
 payload jsonb not null check(jsonb_typeof(payload)='object'),
 verdict text not null check(verdict in ('VERIFIED','UNVERIFIED','SUSPICIOUS')),
 warnings jsonb not null default '[]'::jsonb,
 created_at timestamptz not null default now(),
 saved_at timestamptz
);
create table public.shared_jobs (
 id uuid primary key default gen_random_uuid(),
 fingerprint text not null unique,
 payload jsonb not null check(jsonb_typeof(payload)='object'),
 updated_at timestamptz not null default now()
);
alter table public.job_previews enable row level security;
alter table public.shared_jobs enable row level security;
revoke all on public.job_previews,public.shared_jobs from anon,authenticated;
grant select on public.job_previews,public.shared_jobs to authenticated;
grant all on public.job_previews,public.shared_jobs to service_role;
create policy preview_owner on public.job_previews for select to authenticated using(auth.uid()=user_id);
create policy shared_read on public.shared_jobs for select to authenticated using(true);

-- Central allowlist prevents any personal matching/tracking data reaching the catalog.
create function public.public_job_fields(p jsonb) returns jsonb
language sql immutable set search_path='' as $$
 select coalesce(jsonb_object_agg(key,value),'{}'::jsonb) from jsonb_each(p)
 where key in ('title','company','source_url','application_url','location','remote',
 'employment_type','description','requirements','preferred_skills','graduation_requirement',
 'experience_requirement','salary_or_stipend','deadline','posted_date','verification_status','verification_reasons');
$$;
revoke all on function public.public_job_fields(jsonb) from public,anon,authenticated;

create function public.accept_job_preview(p_preview uuid,p_acknowledge boolean default false) returns uuid
language plpgsql security definer set search_path='' as $$
declare item public.job_previews; result uuid; clean jsonb;
begin
 if auth.uid() is null then raise exception 'Sign in required'; end if;
 select * into item from public.job_previews where id=p_preview and user_id=auth.uid() for update;
 if not found then raise exception 'Preview unavailable'; end if;
 if item.created_at<now()-interval '24 hours' then raise exception 'Preview expired. Import again.'; end if;
 if item.verdict!='VERIFIED' and not p_acknowledge then raise exception 'Acknowledge warning first'; end if;
 clean := public.public_job_fields(item.payload) || jsonb_build_object('company_trust',item.verdict);
 insert into public.opportunities(user_id,fingerprint,payload,status,screened_out)
 values(auth.uid(),item.fingerprint,clean,'SAVED',false)
 on conflict(user_id,fingerprint) do update set payload=excluded.payload,updated_at=now()
 returning id into result;
 if item.verdict='VERIFIED' then
  insert into public.shared_jobs(fingerprint,payload) values(item.fingerprint,clean)
  on conflict(fingerprint) do update set payload=excluded.payload,updated_at=now();
 else
  -- Withdraw previously shared evidence if later inspection raises concern.
  delete from public.shared_jobs where fingerprint=item.fingerprint;
 end if;
 update public.job_previews set saved_at=now() where id=item.id;
 return result;
end $$;
create function public.save_shared_job(p_job uuid) returns uuid
language plpgsql security definer set search_path='' as $$
declare item public.shared_jobs; result uuid;
begin
 if auth.uid() is null then raise exception 'Sign in required'; end if;
 select * into item from public.shared_jobs where id=p_job;
 if not found then raise exception 'Job unavailable'; end if;
 insert into public.opportunities(user_id,fingerprint,payload,status,screened_out)
 values(auth.uid(),item.fingerprint,item.payload,'SAVED',false)
 on conflict(user_id,fingerprint) do update set updated_at=now()
 returning id into result;
 return result;
end $$;
revoke all on function public.accept_job_preview(uuid,boolean),public.save_shared_job(uuid) from public,anon,authenticated;
grant execute on function public.accept_job_preview(uuid,boolean),public.save_shared_job(uuid) to authenticated;

-- Browser clients may request a link import only; discovery is scheduled server-side.
create or replace function public.enqueue_discovery(p_kind text,p_url text default null)
returns uuid language plpgsql security definer set search_path='' as $$
declare owner_id uuid := auth.uid(); task uuid;
begin
 if owner_id is null then raise exception 'Sign in required'; end if;
 perform pg_advisory_xact_lock(hashtextextended(owner_id::text,0));
 if p_kind!='import' then raise exception 'Search is automatic'; end if;
 if p_url is null or length(p_url)>2048 or p_url !~ '^https://' then raise exception 'Invalid URL'; end if;
 if not exists(select 1 from public.profiles where user_id=owner_id)
 or not exists(select 1 from public.provider_connections where user_id=owner_id) then
 raise exception 'Complete profile and Firecrawl connection first'; end if;
 if exists(select 1 from public.discovery_tasks where user_id=owner_id and
 (state in ('QUEUED','RUNNING') or created_at>now()-interval '15 minutes')) then
 raise exception 'Wait for active task and cooldown'; end if;
 insert into public.discovery_settings(user_id) values(owner_id) on conflict do nothing;
 insert into public.discovery_tasks(user_id,kind,job_url) values(owner_id,p_kind,p_url) returning id into task;
 return task;
end $$;
commit;
