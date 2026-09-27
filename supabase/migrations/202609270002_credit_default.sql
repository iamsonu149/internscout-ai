begin;
-- Applies to new settings; never silently increases another user's budget.
alter table public.discovery_settings alter column weekly_limit set default 250;
commit;
