set lock_timeout = '3s';
set statement_timeout = '20s';
create or replace function public.prism_ops_commit(
 p_hash text, p_team uuid, p_row jsonb, p_kind text, p_expected jsonb, p_payload jsonb,
 p_patch jsonb default null, p_feedback jsonb default null, p_extra jsonb default null
) returns boolean language plpgsql security invoker set search_path = '' as $$
declare r public.prism_contents; current_payload jsonb; extra_payload jsonb; k text; actor uuid;
begin
 if p_kind is distinct from 'ops_review_' || p_hash or p_hash !~ '^[0-9a-f]{16}$' or p_payload is null then
  raise exception 'invalid review record' using errcode='22023';
 end if;
 for k in select distinct x from unnest(array[p_kind,p_extra->>'kind']) keys(x) where x is not null order by x loop
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
   pg_catalog.jsonb_build_array('prism_reports', k, coalesce(p_team::text,''))::text,0));
 end loop;
 select * into r from public.prism_contents where hash=p_hash and team_id is not distinct from p_team for update;
 if not found then return false; end if;
 if pg_catalog.jsonb_build_object('service',r.service,'title',r.title,'subtitle',r.subtitle,'body',r.body,
    'source_fields',r.source_fields,'item_meta',r.item_meta,'quality_meta',r.quality_meta,'model',r.model,'version',r.version)
    is distinct from p_row then return false; end if;
 select payload into current_payload from public.prism_reports where kind=p_kind and team_key=coalesce(p_team::text,'') for update;
 if current_payload is distinct from nullif(p_expected,'null'::jsonb) then return false; end if;
 if p_extra is not null then
  if p_extra->>'kind' <> 'final_verdicts' then raise exception 'invalid auxiliary record'; end if;
  select payload into extra_payload from public.prism_reports where kind=p_extra->>'kind' and team_key=coalesce(p_team::text,'') for update;
  if extra_payload is distinct from nullif(p_extra->'expected','null'::jsonb) then return false; end if;
 end if;
 actor=(p_payload->'history'->-1->>'by')::uuid;
 if p_patch is not null then
  update public.prism_contents set item_meta=coalesce(r.item_meta,'{}'::jsonb)||coalesce(p_patch->'item_meta','{}'::jsonb),
   quality_meta=coalesce(r.quality_meta,'{}'::jsonb)||coalesce(p_patch->'quality_meta','{}'::jsonb),
   final_grade=coalesce(p_patch->'quality_meta'->>'finalGrade',r.final_grade)
   where hash=p_hash and team_id is not distinct from p_team;
  insert into public.prism_patch_log(team_id,content_hash,reviewer_id,element,before,after)
   values(p_team,p_hash,actor,coalesce((select pg_catalog.string_agg(key,',') from pg_catalog.jsonb_object_keys(p_patch->'item_meta') as keys(key)),'grade'),p_row,p_patch);
 end if;
 if p_feedback is not null then
  if p_feedback->>'verdict' in ('good','bad') then
   insert into public.prism_feedback(content_hash,reviewer_id,team_id,service,title,verdict,stage,note,element,ts)
    values(p_hash,(p_feedback->>'reviewer')::uuid,p_team,r.service,r.title,p_feedback->>'verdict','analyze',
           coalesce(p_feedback->>'note',''),coalesce(p_feedback->>'element',''),pg_catalog.now())
    on conflict(content_hash,reviewer_id) do update set verdict=excluded.verdict,stage=excluded.stage,note=excluded.note,
     element=excluded.element,ts=excluded.ts where public.prism_feedback.team_id is not distinct from p_team;
  else
   delete from public.prism_feedback where content_hash=p_hash and reviewer_id=(p_feedback->>'reviewer')::uuid and team_id is not distinct from p_team;
  end if;
 end if;
 insert into public.prism_reports(kind,team_key,payload) values(p_kind,coalesce(p_team::text,''),p_payload)
  on conflict(kind,team_key) do update set payload=excluded.payload,updated_at=pg_catalog.now();
 if p_extra is not null then
  insert into public.prism_reports(kind,team_key,payload) values(p_extra->>'kind',coalesce(p_team::text,''),p_extra->'payload')
   on conflict(kind,team_key) do update set payload=excluded.payload,updated_at=pg_catalog.now();
 end if;
 return true;
end;
$$;
revoke all on function public.prism_ops_commit(text,uuid,jsonb,text,jsonb,jsonb,jsonb,jsonb,jsonb) from public, anon, authenticated;
grant execute on function public.prism_ops_commit(text,uuid,jsonb,text,jsonb,jsonb,jsonb,jsonb,jsonb) to service_role;
notify pgrst, 'reload schema';

create or replace function public.prism_ops_plan(p_team uuid,p_kind text,p_expected jsonb,p_payload jsonb,p_guards jsonb)
returns boolean language plpgsql security invoker set search_path='' as $$
declare current_payload jsonb; e jsonb; r public.prism_contents;
begin
 if p_kind !~ '^ops_week_[0-9]{4}-[0-9]{2}-[0-9]{2}$' or jsonb_typeof(p_payload->'versions'->-1->'entries') is distinct from 'array' then
  raise exception 'invalid weekly plan' using errcode='22023';
 end if;
 perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(pg_catalog.jsonb_build_array('prism_reports',p_kind,coalesce(p_team::text,''))::text,0));
 select payload into current_payload from public.prism_reports where kind=p_kind and team_key=coalesce(p_team::text,'') for update;
 if current_payload is distinct from nullif(p_expected,'null'::jsonb) then return false; end if;
 for e in select value from pg_catalog.jsonb_array_elements(p_payload->'versions'->-1->'entries') order by value->>'hash' loop
  select * into r from public.prism_contents where hash=e->>'hash' and team_id is not distinct from p_team for update;
  if not found then return false; end if;
  if pg_catalog.jsonb_build_object('service',r.service,'title',r.title,'subtitle',r.subtitle,'body',r.body,
     'source_fields',r.source_fields,'item_meta',r.item_meta,'quality_meta',r.quality_meta,'model',r.model,'version',r.version)
     is distinct from p_guards->(e->>'hash') then return false; end if;
 end loop;
 for e in select value from pg_catalog.jsonb_array_elements(p_payload->'versions'->-1->'entries') loop
  delete from public.prism_assignments where content_hash=e->>'hash' and team_id is not distinct from p_team;
  insert into public.prism_assignments(content_hash,reviewer_id,team_id,min_reviewers) values(e->>'hash',(e->>'owner')::uuid,p_team,1);
 end loop;
 insert into public.prism_reports(kind,team_key,payload) values(p_kind,coalesce(p_team::text,''),p_payload)
  on conflict(kind,team_key) do update set payload=excluded.payload,updated_at=pg_catalog.now();
 return true;
end;
$$;
revoke all on function public.prism_ops_plan(uuid,text,jsonb,jsonb,jsonb) from public,anon,authenticated;
grant execute on function public.prism_ops_plan(uuid,text,jsonb,jsonb,jsonb) to service_role;
notify pgrst, 'reload schema';
