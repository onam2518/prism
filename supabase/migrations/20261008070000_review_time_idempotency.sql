alter table public.prism_events add column if not exists event_id text;
create unique index if not exists ux_prism_review_time_event on public.prism_events(reviewer_id,event_id);

create or replace function public.prism_log_review_time(p_reviewer uuid,p_team uuid,p_event_id text,p_meta text)
returns boolean language plpgsql security invoker set search_path='' as $$
declare next_day integer;
begin
 if p_reviewer is null or p_event_id is null or p_event_id !~ '^[0-9a-f-]{36}$' then
  raise exception 'invalid review time identity';
 end if;
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('review_time:' || p_reviewer::text,0));
 if exists(select 1 from public.prism_events where reviewer_id=p_reviewer and event_id=p_event_id) then
  return true;
 end if;
 select greatest(extract(epoch from pg_catalog.now())::integer,coalesce(max(day)+1,0)) into next_day
  from public.prism_events where reviewer_id=p_reviewer and kind='review_time';
 insert into public.prism_events(team_id,reviewer_id,kind,day,bonus,meta,event_id,created_at)
  values(p_team,p_reviewer,'review_time',next_day,0,p_meta,p_event_id,coalesce(pg_catalog.to_timestamp((p_meta::jsonb->>'recorded_at')::double precision),pg_catalog.now()));
 return true;
end;
$$;
revoke all on function public.prism_log_review_time(uuid,uuid,text,text) from public,anon,authenticated;
grant execute on function public.prism_log_review_time(uuid,uuid,text,text) to service_role;
notify pgrst,'reload schema';
