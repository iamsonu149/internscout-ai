begin;
alter table public.discovery_settings add column last_scheduled_date date;
revoke update on public.discovery_settings from authenticated;
grant update(user_id,weekly_limit,schedule_enabled) on public.discovery_settings to authenticated;

create function public.enqueue_due_discovery() returns integer
language plpgsql security definer set search_path='' as $$
declare local_now timestamp := now() at time zone 'Asia/Kolkata'; candidate record; total integer := 0;
begin
 if extract(dow from local_now) not in (0,1,3,5) or local_now::time < time '09:00' then return 0; end if;
 for candidate in select user_id from public.discovery_settings
 where schedule_enabled and weekly_limit>0 and (last_scheduled_date is null or last_scheduled_date < local_now::date)
 order by user_id for update skip locked limit 100 loop
   if exists(select 1 from public.provider_connections where user_id=candidate.user_id)
     and exists(select 1 from public.profiles where user_id=candidate.user_id)
     and not exists(select 1 from public.discovery_tasks where user_id=candidate.user_id and
       (state in ('QUEUED','RUNNING') or created_at>now()-interval '15 minutes')) then
     insert into public.discovery_tasks(user_id,kind) values(candidate.user_id,'search')
       on conflict do nothing;
     if found then
       update public.discovery_settings set last_scheduled_date=local_now::date where user_id=candidate.user_id;
       total := total + 1;
     end if;
   end if;
 end loop;
 return total;
end $$;
revoke all on function public.enqueue_due_discovery() from public,anon,authenticated;
grant execute on function public.enqueue_due_discovery() to service_role;
commit;
