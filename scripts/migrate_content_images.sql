-- 수집 이미지 URL · prism_contents.image_urls 컬럼 추가 (2026-07-22 · 게시판 #9)
--
-- 카페 등 회원 전용 원문은 비회원 열람이 막혀 사진 확인이 안 된다 · 수집 피드가
-- 전달한 이미지 URL 을 참조용으로 저장해 검수 상세에서 표시한다(추출 입력 아님).
-- 안전: 추가형 · not null default '[]' · idempotent(if not exists).
-- 배포 순서: 이 SQL 을 먼저 적용한 뒤 새 코드를 배포한다. 새 코드는 이 컬럼을
--   select/upsert 에 포함하므로 컬럼이 없으면 콘텐츠 동기화·목록 조회가 실패한다.
--   (SQL 선적용은 기존 배포 코드에 무해: 기존 코드는 이 컬럼을 참조하지 않는다)
--
-- 적용: Supabase SQL Editor 또는 psql 로 1회 실행.

alter table public.prism_contents
  add column if not exists image_urls jsonb not null default '[]'::jsonb;
