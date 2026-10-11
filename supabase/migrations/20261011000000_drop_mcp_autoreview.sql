-- MCP 파트너 키·호출 기록 · AI 초안 판정 테이블 삭제(기능 코드는 2026-10-09 #650 제거)
-- 삭제 전 행 전체를 prism_reports 에 백업(kind backup_dropped_tables_20261011 · 무팀)
insert into public.prism_reports(kind, team_key, payload)
select 'backup_dropped_tables_20261011', '', jsonb_build_object(
  'prism_mcp_keys',  (select coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb) from public.prism_mcp_keys t),
  'prism_mcp_calls', (select coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb) from public.prism_mcp_calls t),
  'prism_autoreview',(select coalesce(jsonb_agg(to_jsonb(t)), '[]'::jsonb) from public.prism_autoreview t))
on conflict (kind, team_key) do nothing;

drop table if exists public.prism_mcp_calls;
drop table if exists public.prism_mcp_keys;
drop table if exists public.prism_autoreview;

-- 키워드 실험 기록 삭제가 행을 비우기만 하던 시절의 빈 행(이후 delete_report 로 행 삭제)
delete from public.prism_reports where kind like 'keyword_lab_run_%' and (payload is null or payload = '{}'::jsonb);
