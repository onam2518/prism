-- Compare-and-swap for versioned topic and policy state. No existing rows are rewritten.
set lock_timeout = '2s';
set statement_timeout = '15s';
create or replace function public.prism_compare_report(
  p_kind text, p_team_key text, p_expected jsonb, p_payload jsonb,
  p_guard_kind text default null, p_guard_expected jsonb default null
) returns boolean language plpgsql security invoker set search_path = '' as $$
declare current_payload jsonb; guard_payload jsonb; lock_kind text;
begin
  if p_kind is null or p_team_key is null or p_payload is null then
    raise exception 'report key and payload required' using errcode = '22023';
  end if;
  -- Stable ordering protects a state update and its control snapshot together.
  for lock_kind in select distinct k from unnest(array[p_kind,p_guard_kind]) as keys(k)
    where k is not null order by k loop
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
      pg_catalog.jsonb_build_array('prism_reports', lock_kind, p_team_key)::text, 0));
  end loop;
  if p_guard_kind is not null then
    select payload into guard_payload from public.prism_reports
      where kind = p_guard_kind and team_key = p_team_key for share;
    if guard_payload is distinct from nullif(p_guard_expected, 'null'::jsonb) then return false; end if;
  end if;
  select payload into current_payload from public.prism_reports
    where kind = p_kind and team_key = p_team_key for update;
  if current_payload is distinct from nullif(p_expected, 'null'::jsonb) then return false; end if;
  insert into public.prism_reports(kind,team_key,payload) values(p_kind,p_team_key,p_payload)
    on conflict(kind,team_key) do update set payload = excluded.payload, updated_at = now();
  return true;
end;
$$;
revoke all on function public.prism_compare_report(text,text,jsonb,jsonb,text,jsonb) from public, anon, authenticated;
grant execute on function public.prism_compare_report(text,text,jsonb,jsonb,text,jsonb) to service_role;
notify pgrst, 'reload schema';
