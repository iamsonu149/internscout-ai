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
insert into auth.users values ('00000000-0000-0000-0000-000000000001'),('00000000-0000-0000-0000-000000000002');
insert into public.profiles(user_id, document) values
('00000000-0000-0000-0000-000000000001','{"skills":["Alice"]}'),
('00000000-0000-0000-0000-000000000002','{"skills":["Bob"]}');
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
