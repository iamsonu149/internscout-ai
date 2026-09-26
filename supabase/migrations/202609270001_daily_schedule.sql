begin;

-- Existing explicit opt-outs and weekly budgets remain unchanged.
alter table public.discovery_settings alter column schedule_enabled set default true;

create or replace function public.initialize_discovery_settings() returns trigger
language plpgsql security definer set search_path='' as $$
begin
 insert into public.discovery_settings(user_id) values(new.user_id) on conflict do nothing;
 return new;
end $$;
revoke all on function public.initialize_discovery_settings() from public,anon,authenticated;
create trigger profile_discovery_defaults after insert on public.profiles
 for each row execute function public.initialize_discovery_settings();

insert into public.discovery_settings(user_id)
 select user_id from public.profiles on conflict do nothing;

create function public.discovery_schedule_due(at_time timestamptz) returns boolean
language sql stable set search_path='' as $$
 select (at_time at time zone 'Asia/Kolkata')::time >= time '09:00';
$$;
revoke all on function public.discovery_schedule_due(timestamptz) from public,anon,authenticated;
grant execute on function public.discovery_schedule_due(timestamptz) to service_role;

create or replace function public.enqueue_due_discovery() returns integer
language plpgsql security definer set search_path='' as $$
declare local_today date := (now() at time zone 'Asia/Kolkata')::date;
 candidate record; total integer := 0;
begin
 if not public.discovery_schedule_due(now()) then return 0; end if;
 for candidate in select s.user_id from public.discovery_settings s
 where s.schedule_enabled and s.weekly_limit>0
   and (s.last_scheduled_date is null or s.last_scheduled_date < local_today)
   and exists(select 1 from public.provider_connections c where c.user_id=s.user_id)
   and exists(select 1 from public.profiles p where p.user_id=s.user_id)
   and not exists(select 1 from public.discovery_tasks t where t.user_id=s.user_id and
      (t.state in ('QUEUED','RUNNING') or t.created_at>now()-interval '15 minutes'))
 order by s.user_id for update of s skip locked limit 100 loop
   insert into public.discovery_tasks(user_id,kind) values(candidate.user_id,'search')
     on conflict do nothing;
   if found then
     update public.discovery_settings set last_scheduled_date=local_today where user_id=candidate.user_id;
     total := total + 1;
   end if;
 end loop;
 return total;
end $$;
revoke all on function public.enqueue_due_discovery() from public,anon,authenticated;
grant execute on function public.enqueue_due_discovery() to service_role;
commit;
