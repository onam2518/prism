begin;
set local statement_timeout = '20s';
set local lock_timeout = '2s';
create temp table ops_test_contents (like public.prism_contents including all);
create temp table ops_test_reports (like public.prism_reports including all);
create temp table ops_test_feedback (like public.prism_feedback including all);
create temp table ops_test_patch_log (like public.prism_patch_log including all);
create temp table ops_test_results (scenario text, passed boolean);
do $setup$
declare definition text;
begin
 select pg_get_functiondef('public.prism_ops_commit(text,uuid,jsonb,text,jsonb,jsonb,jsonb,jsonb,jsonb)'::regprocedure) into definition;
 definition := replace(definition,'public.prism_ops_commit','pg_temp.ops_test_commit');
 definition := replace(definition,'public.prism_contents','pg_temp.ops_test_contents');
 definition := replace(definition,'public.prism_reports','pg_temp.ops_test_reports');
 definition := replace(definition,'public.prism_feedback','pg_temp.ops_test_feedback');
 definition := replace(definition,'public.prism_patch_log','pg_temp.ops_test_patch_log');
 if position('public.prism_' in definition)>0 then raise exception 'unmapped production reference'; end if;
 execute definition;
end $setup$;
create function pg_temp.ops_test_fail() returns trigger language plpgsql as $$ begin raise exception 'isolated report failure'; end $$;
do $test$
declare
 h text := 'f0e1d2c3b4a59687'; t uuid := '00000000-0000-4000-8000-000000000001';
 actor uuid := '00000000-0000-4000-8000-000000000002'; k text := 'ops_review_'||h;
 r jsonb; p jsonb; held jsonb; failed boolean := false;
begin
 insert into pg_temp.ops_test_contents(hash,team_id,service,title,body,item_meta,quality_meta,model,version)
 values(h,t,'TEST','isolated test','synthetic body','{"summary":"original"}','{"finalGrade":"G"}','test',1);
 select jsonb_build_object('service',service,'title',title,'subtitle',subtitle,'body',body,'source_fields',source_fields,'item_meta',item_meta,'quality_meta',quality_meta,'model',model,'version',version)
 into r from pg_temp.ops_test_contents where hash=h;
 p := jsonb_build_object('revision',1,'history',jsonb_build_array(jsonb_build_object('by',actor)),'reviews',jsonb_build_array(jsonb_build_object('axes',jsonb_build_object('summary',jsonb_build_object('status','needs_fix','reason','','proposal','correct date')))));
 assert pg_temp.ops_test_commit(h,t,r,k,null,p,null,jsonb_build_object('reviewer',actor,'verdict','bad','note','correct date','element','summary'),null);
 assert (select payload=p from pg_temp.ops_test_reports where kind=k);
 assert (select note='correct date' from pg_temp.ops_test_feedback where content_hash=h);
 insert into ops_test_results values('report and feedback persisted together',true);
 assert not pg_temp.ops_test_commit(h,t,r,k,null,p,null,null,null);
 insert into ops_test_results values('stale expected revision rejected',true);
 assert not pg_temp.ops_test_commit(h,'00000000-0000-4000-8000-000000000099',r,k,p,p,null,null,null);
 assert not pg_temp.ops_test_commit(h,t,r||'{"title":"stale"}',k,p,p,null,null,null);
 insert into ops_test_results values('wrong team and stale content rejected',true);
 insert into pg_temp.ops_test_feedback(content_hash,reviewer_id,team_id,verdict) values(h,'00000000-0000-4000-8000-000000000003',t,'good');
 held := p||'{"revision":2}';
 assert pg_temp.ops_test_commit(h,t,r,k,p,held,null,jsonb_build_object('reviewer',actor,'verdict','hold'),null);
 assert not exists(select 1 from pg_temp.ops_test_feedback where reviewer_id=actor);
 assert (select count(*)=1 from pg_temp.ops_test_feedback);
 insert into ops_test_results values('hold removes only own feedback',true);
 execute 'create trigger ops_test_failure before insert or update on pg_temp.ops_test_reports for each row execute function pg_temp.ops_test_fail()';
 begin
  perform pg_temp.ops_test_commit(h,t,r,k,held,p,'{"item_meta":{"summary":"changed"}}',jsonb_build_object('reviewer',actor,'verdict','bad','note','should rollback'),null);
 exception when raise_exception then
  if sqlerrm <> 'isolated report failure' then raise; end if;
  failed := true;
 end;
 assert failed;
 assert (select item_meta->>'summary'='original' from pg_temp.ops_test_contents where hash=h);
 assert (select count(*)=0 from pg_temp.ops_test_patch_log);
 assert not exists(select 1 from pg_temp.ops_test_feedback where reviewer_id=actor);
 assert (select payload=held from pg_temp.ops_test_reports where kind=k);
 insert into ops_test_results values('report failure rolls back content patch audit and feedback',true);
end $test$;
select * from ops_test_results;
rollback;
