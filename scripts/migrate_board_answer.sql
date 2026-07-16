-- 게시판 문의 답변 · prism_board.answer / answered_at 컬럼 추가 (2026-07-16)
--
-- 안전: 추가형 · nullable · idempotent(if not exists).
-- 배포 순서: 이 SQL 을 먼저 적용하면 현 배포 코드(answer 미조회)엔 무해하고,
--   answer 를 조회하는 새 코드가 배포된 뒤부터 답변이 표시된다.
--
-- 적용: Supabase SQL Editor 또는 psql 로 1회 실행.

alter table public.prism_board
  add column if not exists answer text;
alter table public.prism_board
  add column if not exists answered_at timestamptz;
