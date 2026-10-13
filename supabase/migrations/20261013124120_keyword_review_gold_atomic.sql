set lock_timeout = '2s';
set statement_timeout = '15s';
create or replace function public.prism_compare_report_pair(
  p_kind text, p_team_key text, p_expected jsonb, p_payload jsonb,
  p_guard_kind text, p_guard_expected jsonb, p_guard_payload jsonb
) returns boolean language plpgsql security invoker set search_path = '' as $$
begin
  if p_guard_kind is null or p_guard_kind = p_kind or p_guard_payload is null then
    raise exception 'two distinct report keys and payloads required' using errcode = '22023';
  end if;
  if not public.prism_compare_report(p_kind, p_team_key, p_expected, p_payload,
                                    p_guard_kind, p_guard_expected) then return false; end if;
  insert into public.prism_reports(kind,team_key,payload) values(p_guard_kind,p_team_key,p_guard_payload)
    on conflict(kind,team_key) do update set payload=excluded.payload, updated_at=now();
  return true;
end;
$$;
revoke all on function public.prism_compare_report_pair(text,text,jsonb,jsonb,text,jsonb,jsonb) from public, anon, authenticated;
grant execute on function public.prism_compare_report_pair(text,text,jsonb,jsonb,text,jsonb,jsonb) to service_role;
notify pgrst, 'reload schema';
