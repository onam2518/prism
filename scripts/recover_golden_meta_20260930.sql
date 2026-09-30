-- 2026-09-30: recover only missing legacy golden fields from verified pre-loss evidence.
-- No model calls; do not rewrite evaluation results or frozen execution snapshots.
begin;
set local lock_timeout='3s';
set local statement_timeout='45s';
select pg_advisory_xact_lock(hashtextextended('prism_golden:7a73fecd-fb98-417c-8101-e26f938cfdf9',0));
lock table public.prism_golden in share row exclusive mode;
do $$
begin
  if exists(select 1 from public.prism_eval_runs where status='running')
     or exists(select 1 from public.prism_autopilot_runs where status='running') then
    raise exception 'An evaluation is active; recovery was not applied';
  end if;
end $$;

create temp table golden_recovery_plan on commit drop as
with base as (
  select g.id, g.content_hash, g.expected, g.content, g.source,
         c.item_meta, c.model, c.version, d.item_meta as draft_meta, d.created_at as draft_at,
         old.expected as old_expected, old.run_id as old_run
  from public.prism_golden g
  join public.prism_contents c on c.hash=g.content_hash and c.team_id=g.team_id
  join public.prism_drafts d on d.content_hash=g.content_hash and d.team_key=g.team_id::text
    and d.model=c.model and d.version=c.version and d.created_at<'2026-09-09T00:14:00Z'
  left join lateral (
    select e.expected,e.run_id from public.prism_eval_results e
    join public.prism_eval_runs r on r.id=e.run_id and r.team_id=g.team_id
    where e.content_hash=g.content_hash and r.created_at<'2026-09-09T00:14:00Z'
    order by r.created_at desc,e.created_at desc limit 1
  ) old on true
  where g.team_id='7a73fecd-fb98-417c-8101-e26f938cfdf9' and g.source='review'
    and coalesce(g.content->>'title','')=coalesce(c.title,'')
    and coalesce(g.content->>'body','')=coalesce(c.body,'')
), restored_fields as (
  select b.*, f.key,
    case when draft_meta ? f.key and draft_meta->f.key=item_meta->f.key then draft_meta->f.key
         when f.key='intent' and old_expected->'intent'=item_meta->'intent' then old_expected->'intent'
         else case when f.key='summary' then '""'::jsonb else '[]'::jsonb end end as value,
    case when draft_meta ? f.key and draft_meta->f.key=item_meta->f.key then 'primary_draft_before_loss'
         when f.key='intent' and old_expected->'intent'=item_meta->'intent' then 'prior_eval_expected'
         else 'legacy_rejected_empty_default' end as evidence
  from base b cross join unnest(array['intent','content_category','entities','summary']) f(key)
  where not(expected ? f.key) and (
    (draft_meta ? f.key and draft_meta->f.key=item_meta->f.key)
    or (f.key='intent' and old_expected ? 'intent' and old_expected->'intent'=item_meta->'intent')
    or (expected->>'finalGrade'='R' and not(draft_meta ? f.key) and not(item_meta ? f.key))
  )
), additions as (
  select id,content_hash,jsonb_object_agg(key,value) as values,
         jsonb_object_agg(key,jsonb_build_object('kind',evidence,'model',model,'version',version,'draft_at',draft_at,'eval_run',old_run)) as sources
  from restored_fields group by id,content_hash
)
select b.id,b.content_hash,b.expected as before,a.values,a.sources,
       b.expected || a.values || jsonb_build_object('meta_recovery_id','golden-meta-20260930-v1')
       || case when a.values ? 'intent' then jsonb_build_object('intent_review','needed','intent_dictionary_version','intent-common-68-2026-09-29') else '{}'::jsonb end as after
from base b join additions a on a.id=b.id;

do $$
begin
  if (select count(*) from golden_recovery_plan) <> 877 then
    raise exception 'Recovery scope changed; review the plan before applying';
  end if;
  if exists(select 1 from golden_recovery_plan where not(after @> before)) then
    raise exception 'Recovery would overwrite an existing value';
  end if;
  if exists(select 1 from public.prism_reports
            where kind='golden_meta_recovery_backup_20260930'
              and team_key='7a73fecd-fb98-417c-8101-e26f938cfdf9') then
    raise exception 'Recovery backup already exists; do not apply twice';
  end if;
end $$;

insert into public.prism_reports(kind,team_key,payload)
select 'golden_meta_recovery_backup_20260930','7a73fecd-fb98-417c-8101-e26f938cfdf9',
       jsonb_build_object('id','golden-meta-20260930-v1','at',now(),
         'rows',jsonb_agg(to_jsonb(p)),
         'evaluation_digest',(select md5(coalesce(string_agg(
           e.run_id::text||e.content_hash||e.expected::text||coalesce(e.got::text,'null'),'|' order by e.run_id,e.content_hash),''))
           from public.prism_eval_results e join public.prism_eval_runs r on r.id=e.run_id
           where r.team_id='7a73fecd-fb98-417c-8101-e26f938cfdf9'))
from golden_recovery_plan p;

update public.prism_golden g set expected=p.after
from golden_recovery_plan p where g.id=p.id and g.expected=p.before;

insert into public.prism_reports(kind,team_key,payload,updated_at)
select 'golden_meta_recovery','7a73fecd-fb98-417c-8101-e26f938cfdf9',
  jsonb_build_object('id','golden-meta-20260930-v1','at',now(),'items',
    jsonb_object_agg(content_hash,jsonb_build_object('values',values,'sources',sources))),now()
from golden_recovery_plan
on conflict(kind,team_key) do update set payload=excluded.payload,updated_at=excluded.updated_at;

do $$
begin
  if exists(select 1 from golden_recovery_plan p join public.prism_golden g on g.id=p.id
            where g.expected<>p.after) then
    raise exception 'Recovery readback mismatch';
  end if;
end $$;
commit;
select jsonb_build_object('recovery','golden-meta-20260930-v1',
  'total',count(*),'intent',count(*) filter(where expected ? 'intent'),
  'category',count(*) filter(where expected ? 'content_category'),
  'entities',count(*) filter(where expected ? 'entities'),
  'summary',count(*) filter(where expected ? 'summary')) as restored
from public.prism_golden where team_id='7a73fecd-fb98-417c-8101-e26f938cfdf9';
