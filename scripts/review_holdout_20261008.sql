begin;
create temporary table review_holdout_plan on commit drop as with eligible as (
select g.team_id,g.content_hash,c.service,g.expected->>'finalGrade' grade,
 coalesce(g.expected->'content_category'->>0,'') category,c.purpose
from public.prism_golden g join public.prism_contents c on c.hash=g.content_hash and c.team_id is not distinct from g.team_id
where coalesce(c.purpose,'review')='review' and g.expected->>'finalGrade' in ('G','R')
and not exists(select 1 from public.prism_feedback f where f.content_hash=g.content_hash and f.team_id is not distinct from g.team_id and f.verdict='bad')
and not exists(select 1 from public.prism_feedback_routes r where r.content_hash=g.content_hash and r.team_id is not distinct from g.team_id)
), ranked as (
select *,row_number() over(partition by team_id,service,grade,category order by md5('review-holdout-20261008:'||content_hash)) rn,
count(*) over(partition by team_id,service,grade,category) bucket_n from eligible
), chosen as (select * from ranked where bucket_n>=2 and rn<=greatest(1,floor(bucket_n*.2)))
select * from chosen where not exists (
select 1 from public.prism_reports r where r.kind='holdout_split_20261008' and r.team_key=coalesce(chosen.team_id::text,''));
do $$ begin if (select count(*) from review_holdout_plan) not in (0,107) then raise exception 'holdout candidate count changed';end if;end $$;
insert into public.prism_reports(kind,team_key,payload)
select 'holdout_split_20261008',coalesce(team_id::text,''),jsonb_build_object(
 'created_at',now(),'seed','review-holdout-20261008','scope','prospective_validation',
 'note','Historical evaluation exposure is not erased. Excluded from future feedback compilation and training exports.',
 'items',jsonb_agg(jsonb_build_object('hash',content_hash,'previous_purpose',purpose,'service',service,'grade',grade,'category',category)))
from review_holdout_plan group by team_id;
update public.prism_contents c set purpose='eval' from review_holdout_plan p
where c.hash=p.content_hash and c.team_id is not distinct from p.team_id;
select count(*) selected,count(distinct service) services,count(distinct category) categories from review_holdout_plan;
commit;
