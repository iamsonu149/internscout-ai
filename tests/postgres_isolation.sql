-- Local disposable PostgreSQL test only. Bootstrap minimal Supabase Auth roles.
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create schema auth;
create table auth.users(id uuid primary key);
create function auth.uid() returns uuid language sql stable as
$$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
grant usage on schema auth to authenticated;
grant execute on function auth.uid() to authenticated;
\i /tmp/workspaces.sql
\i /tmp/discovery_queue.sql
\i /tmp/schedule.sql
\i /tmp/daily_schedule.sql
do $$
declare day_offset integer;
begin
 for day_offset in 0..6 loop
  if public.discovery_schedule_due(timestamptz '2026-09-21 08:59:59+05:30' + day_offset*interval '1 day') then
   raise exception 'Schedule ran before 09:00 IST';
  end if;
  if not public.discovery_schedule_due(timestamptz '2026-09-21 09:00:00+05:30' + day_offset*interval '1 day') then
   raise exception 'Schedule excluded a weekday';
  end if;
 end loop;
end $$;
insert into auth.users values ('00000000-0000-0000-0000-000000000001'),('00000000-0000-0000-0000-000000000002');
insert into public.profiles(user_id, document) values
('00000000-0000-0000-0000-000000000001','{"skills":["Alice"]}'),
('00000000-0000-0000-0000-000000000002','{"skills":["Bob"]}');
do $$ begin
 if (select count(*) from public.discovery_settings where schedule_enabled and weekly_limit=100) != 2 then
  raise exception 'New profiles did not get daily defaults';
 end if;
end $$;
insert into public.opportunities(user_id,fingerprint,payload) values
('00000000-0000-0000-0000-000000000001','same-job','{}'),
('00000000-0000-0000-0000-000000000002','same-job','{}');
set role authenticated;
set request.jwt.claim.sub = '00000000-0000-0000-0000-000000000001';
do $$
declare n integer;
begin
  select count(*) into n from public.profiles;
  if n != 1 then raise exception 'Profile read isolation failed'; end if;
  select count(*) into n from public.opportunities;
  if n != 1 then raise exception 'Job read isolation failed'; end if;
  update public.profiles set document='{}' where user_id='00000000-0000-0000-0000-000000000002';
  get diagnostics n = row_count;
  if n != 0 then raise exception 'Cross-user profile write succeeded'; end if;
  update public.opportunities set status='SAVED' where user_id='00000000-0000-0000-0000-000000000002';
  get diagnostics n = row_count;
  if n != 0 then raise exception 'Cross-user tracking succeeded'; end if;
  update public.opportunities set status='SAVED' where user_id=auth.uid();
  get diagnostics n = row_count;
  if n != 1 then raise exception 'Owner tracking failed'; end if;
  begin
    update public.opportunities set payload='{"fake":"verified"}' where user_id=auth.uid();
    raise exception 'Evidence tampering succeeded';
  exception when insufficient_privilege then null; end;
  begin
    update public.profiles set user_id='00000000-0000-0000-0000-000000000003' where user_id=auth.uid();
    raise exception 'Owner reassignment succeeded';
  exception when insufficient_privilege then null; end;
end $$;
reset role;
set role anon;
do $$ begin
  begin
    perform * from public.profiles;
    raise exception 'Anonymous read succeeded';
  exception when insufficient_privilege then null; end;
end $$;
reset role;
select 'PostgreSQL ownership and column-permission checks passed' as result;

insert into public.provider_connections(user_id,provider,ciphertext,last_four) values
('00000000-0000-0000-0000-000000000001','firecrawl','test-encrypted','0001'),
('00000000-0000-0000-0000-000000000002','firecrawl','test-encrypted','0002');
set role authenticated;
set request.jwt.claim.sub = '00000000-0000-0000-0000-000000000001';
select public.enqueue_discovery('search',null) as queued_task \gset
select public.save_firecrawl_connection('updated-encrypted-credential','1111');
insert into public.discovery_settings(user_id,weekly_limit,schedule_enabled)
 values(auth.uid(),100,false) on conflict(user_id) do update
 set user_id=excluded.user_id,weekly_limit=excluded.weekly_limit,schedule_enabled=excluded.schedule_enabled;
do $$ begin
  begin
    perform ciphertext from public.provider_connections;
    raise exception 'Ciphertext read succeeded';
  exception when insufficient_privilege then null; end;
  begin
    perform public.claim_discovery();
    raise exception 'User claimed a worker task';
  exception when insufficient_privilege then null; end;
  begin
    perform public.enqueue_due_discovery();
    raise exception 'User triggered scheduler';
  exception when insufficient_privilege then null; end;
  begin
    perform public.enqueue_discovery('search',null);
    raise exception 'Duplicate queue submission succeeded';
  exception when raise_exception then
    if SQLERRM != 'Wait for the active task and cooldown' then raise; end if;
  end;
end $$;
reset role;
update public.discovery_settings set weekly_limit=2;
set role service_role;
select id as claimed_id,claim_token as claim from public.claim_discovery() \gset
select public.reserve_discovery_credit(:'claimed_id',:'claim','search',2) as reservation \gset
reset role;
select set_config('test.task', :'claimed_id', false);
select set_config('test.claim', :'claim', false);
do $$ begin
  begin
    perform public.reserve_discovery_credit(current_setting('test.task')::uuid,current_setting('test.claim')::uuid,'scrape',1);
    raise exception 'Overspending succeeded';
  exception when raise_exception then
    if SQLERRM != 'Weekly budget exhausted' then raise; end if;
  end;
  begin
    perform public.reserve_discovery_credit(current_setting('test.task')::uuid,gen_random_uuid(),'scrape',1);
    raise exception 'Forged lease spent credits';
  exception when raise_exception then
    if SQLERRM != 'Inactive task' then raise; end if;
  end;
end $$;
set role authenticated;
set request.jwt.claim.sub = '00000000-0000-0000-0000-000000000002';
do $$ declare n integer; begin
 select count(*) into n from public.credit_reservations;
 if n != 0 then raise exception 'Cross-user credit history read'; end if;
 select count(*) into n from public.discovery_tasks;
 if n != 0 then raise exception 'Cross-user task read'; end if;
end $$;
reset role;
select 'Queue access, duplicate submission and durable budget checks passed' as result;
update public.discovery_settings set weekly_limit=0 where user_id='00000000-0000-0000-0000-000000000002';
do $$ begin
 if public.enqueue_due_discovery()!=0 then raise exception 'Scheduled a paused or zero-budget user'; end if;
end $$;
update public.discovery_settings set weekly_limit=100 where user_id='00000000-0000-0000-0000-000000000002';
do $$ declare expected integer; begin
 expected := case when public.discovery_schedule_due(now()) then 1 else 0 end;
 if public.enqueue_due_discovery()!=expected then raise exception 'Daily eligible user was not scheduled'; end if;
 if public.enqueue_due_discovery()!=0 then raise exception 'Daily scheduler duplicated a task'; end if;
 if (select schedule_enabled from public.discovery_settings where user_id='00000000-0000-0000-0000-000000000001') then
  raise exception 'Explicit schedule opt-out overwritten';
 end if;
end $$;
select 'Daily schedule time boundary, defaults, opt-out and deduplication passed' as result;
