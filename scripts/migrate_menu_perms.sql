-- 메뉴별 권한(생성자 설정) · prism_teams.menu_perms 컬럼 추가 (2026-07-15)
--
-- 매트릭스 형태: {"content":{"super":true,"admin":false}, ...} (menu_id → 역할별 표시 여부).
-- 안전: 추가형 · nullable default '{}' · idempotent(if not exists).
-- 배포 순서 무관: 코드는 컬럼 미존재/미설정 시 기본 매트릭스(현재 동작)로 폴백한다.
--   → 코드 먼저 배포돼도 무해, 이 SQL 적용 후부터 생성자 설정이 영속된다.
--
-- 적용: Supabase SQL Editor 또는 psql 로 1회 실행.

alter table public.prism_teams
  add column if not exists menu_perms jsonb not null default '{}'::jsonb;
