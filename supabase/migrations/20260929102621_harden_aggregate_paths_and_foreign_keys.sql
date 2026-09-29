begin;
set local lock_timeout = '2s';
set local statement_timeout = '15s';

-- 팀/사용자 삭제의 FK 검사와 이력 조회가 전체 표를 스캔하지 않게 한다.
create index if not exists ix_prism_history_team_recorded
    on public.prism_golden_history(team_id, recorded_at desc);
create index if not exists ix_prism_assignments_reviewer
    on public.prism_assignments(reviewer_id);
create index if not exists ix_prism_mcp_keys_user
    on public.prism_mcp_keys(user_id);
create index if not exists ix_prism_teams_created_by
    on public.prism_teams(created_by);

-- 기존 함수는 public 표를 한정하지 않고 참조한다. 해석 경로를 고정하되 본문·권한은 유지한다.
alter function public.prism_agg_assignment_load(text) set search_path = pg_catalog, public, pg_temp;
alter function public.prism_agg_feedback_stats(text) set search_path = pg_catalog, public, pg_temp;
alter function public.prism_agg_gold_stats(text) set search_path = pg_catalog, public, pg_temp;
commit;
