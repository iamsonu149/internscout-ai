begin;
create table public.provider_connections (
 user_id uuid not null references auth.users(id) on delete cascade,
 provider text not null check(provider = 'firecrawl'),
 ciphertext text not null check(length(ciphertext) <= 8192),
 last_four text not null check(length(last_four) <= 4),
 updated_at timestamptz not null default now(),
 primary key(user_id, provider)
);
create table public.discovery_settings (
 user_id uuid primary key references auth.users(id) on delete cascade,
 weekly_limit integer not null default 100 check(weekly_limit between 0 and 10000),
 schedule_enabled boolean not null default false
);
create table public.discovery_tasks (
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references auth.users(id) on delete cascade,
 kind text not null check(kind in ('search','import')),
 job_url text check(length(job_url) <= 2048),
 state text not null default 'QUEUED' check(state in ('QUEUED','RUNNING','COMPLETED','FAILED','PARTIAL')),
 created_at timestamptz not null default now(),
 started_at timestamptz,
 finished_at timestamptz,
 result jsonb not null default '{}'::jsonb,
 claim_token uuid
);
create unique index one_active_task_per_user on public.discovery_tasks(user_id) where state in ('QUEUED','RUNNING');
create index task_queue_order on public.discovery_tasks(created_at) where state='QUEUED';
create table public.credit_reservations (
 id uuid primary key default gen_random_uuid(),
 user_id uuid not null references auth.users(id) on delete cascade,
 task_id uuid not null references public.discovery_tasks(id),
 endpoint text not null check(endpoint in ('search','scrape')),
 reserved integer not null check(reserved between 1 and 100),
 reported integer check(reported >= 0),
 request_id text,
 created_at timestamptz not null default now()
);
create index credit_owner_date on public.credit_reservations(user_id,created_at);
create table public.discovery_sources (
 user_id uuid not null references auth.users(id) on delete cascade,
 url text not null,
 checked_at timestamptz not null default now(),
 outcome text not null,
 primary key(user_id,url)
);

alter table public.provider_connections enable row level security;
alter table public.discovery_settings enable row level security;
alter table public.discovery_tasks enable row level security;
alter table public.credit_reservations enable row level security;
alter table public.discovery_sources enable row level security;
create policy connection_owner on public.provider_connections for all to authenticated
 using((select auth.uid())=user_id) with check((select auth.uid())=user_id);
create policy settings_owner on public.discovery_settings for all to authenticated
 using((select auth.uid())=user_id) with check((select auth.uid())=user_id);
create policy tasks_read on public.discovery_tasks for select to authenticated using((select auth.uid())=user_id);
create policy credits_read on public.credit_reservations for select to authenticated using((select auth.uid())=user_id);
create policy sources_read on public.discovery_sources for select to authenticated using((select auth.uid())=user_id);
revoke all on public.provider_connections,public.discovery_settings,public.discovery_tasks,
 public.credit_reservations,public.discovery_sources from anon, authenticated;
grant select(user_id,provider,last_four,updated_at),delete on public.provider_connections to authenticated;
grant select,insert,update on public.discovery_settings to authenticated;
grant select(id,user_id,kind,job_url,state,created_at,started_at,finished_at,result) on public.discovery_tasks to authenticated;
grant select on public.credit_reservations,public.discovery_sources to authenticated;
grant all on public.provider_connections,public.discovery_settings,public.discovery_tasks,
 public.credit_reservations,public.discovery_sources to service_role;

create function public.enqueue_discovery(p_kind text, p_url text default null)
returns uuid language plpgsql security definer set search_path='' as $$
declare owner_id uuid := auth.uid(); task uuid;
begin
 if owner_id is null then raise exception 'Sign in required'; end if;
 perform pg_advisory_xact_lock(hashtextextended(owner_id::text,0));
 if p_kind not in ('search','import') then raise exception 'Invalid task'; end if;
 if p_kind='import' and (p_url is null or length(p_url)>2048) then raise exception 'Invalid URL'; end if;
 if not exists(select 1 from public.profiles where user_id=owner_id)
   or not exists(select 1 from public.provider_connections where user_id=owner_id) then
   raise exception 'Complete profile and Firecrawl connection first'; end if;
 if exists(select 1 from public.discovery_tasks where user_id=owner_id and
   (state in ('QUEUED','RUNNING') or created_at > now()-interval '15 minutes')) then
   raise exception 'Wait for the active task and cooldown'; end if;
 insert into public.discovery_settings(user_id) values(owner_id) on conflict do nothing;
 insert into public.discovery_tasks(user_id,kind,job_url) values(owner_id,p_kind,p_url) returning id into task;
 return task;
end $$;

create function public.save_firecrawl_connection(p_ciphertext text,p_last_four text)
returns void language plpgsql security definer set search_path='' as $$
begin
 if auth.uid() is null then raise exception 'Sign in required'; end if;
 insert into public.provider_connections(user_id,provider,ciphertext,last_four)
 values(auth.uid(),'firecrawl',p_ciphertext,p_last_four)
 on conflict(user_id,provider) do update set ciphertext=excluded.ciphertext,
 last_four=excluded.last_four,updated_at=now();
end $$;
revoke all on function public.save_firecrawl_connection(text,text) from public,anon,authenticated;
grant execute on function public.save_firecrawl_connection(text,text) to authenticated;

create function public.claim_discovery() returns setof public.discovery_tasks
language plpgsql security definer set search_path='' as $$
declare selected uuid;
begin
 -- A lost worker is failed, never silently retried after possibly paid requests.
 update public.discovery_tasks set state='FAILED', finished_at=now(),
 result='{"outcome":"Worker timed out. Existing credit reservations were retained."}'
 where state='RUNNING' and started_at<now()-interval '30 minutes';
 select id into selected from public.discovery_tasks where state='QUEUED'
 order by created_at for update skip locked limit 1;
 return query update public.discovery_tasks set state='RUNNING',started_at=now(),claim_token=gen_random_uuid()
 where id=selected returning *;
end $$;

create function public.reserve_discovery_credit(p_task uuid,p_claim uuid,p_endpoint text,p_cost integer)
returns uuid language plpgsql security definer set search_path='' as $$
declare owner_id uuid; used bigint; cap integer; reservation uuid;
begin
 select user_id into owner_id from public.discovery_tasks where id=p_task and claim_token=p_claim
 and state='RUNNING' and started_at>now()-interval '30 minutes';
 if owner_id is null then raise exception 'Inactive task'; end if;
 perform pg_advisory_xact_lock(hashtextextended(owner_id::text,0));
 select weekly_limit into cap from public.discovery_settings where user_id=owner_id;
 if cap is null then raise exception 'Missing budget'; end if;
 select coalesce(sum(greatest(reserved,coalesce(reported,0))),0) into used
 from public.credit_reservations where user_id=owner_id and created_at>=now()-interval '7 days';
 if p_cost < 1 or used+p_cost>cap then raise exception 'Weekly budget exhausted'; end if;
 insert into public.credit_reservations(user_id,task_id,endpoint,reserved)
 values(owner_id,p_task,p_endpoint,p_cost) returning id into reservation;
 return reservation;
end $$;
revoke all on function public.enqueue_discovery(text,text),public.claim_discovery(),
 public.reserve_discovery_credit(uuid,uuid,text,integer) from public, anon, authenticated;
grant execute on function public.enqueue_discovery(text,text) to authenticated;
grant execute on function public.claim_discovery(),public.reserve_discovery_credit(uuid,uuid,text,integer) to service_role;
commit;
