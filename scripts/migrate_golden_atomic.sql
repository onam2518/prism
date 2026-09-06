-- 골든 업로드 원자성 · prism_golden(team_id, content_hash) 단일 쓰기 RPC (2026-09-06)
--
-- 적용: Supabase SQL Editor 또는 psql 로 이 파일을 1회 실행한 뒤 새 코드를 배포한다.
-- 계약: p_team_id 가 NULL 이거나 p_rows 형식/해시가 잘못되면 RPC 전체가 실패하고 기존
--       prism_golden 행은 바뀌지 않는다. p_replace=true + []만 의도적인 팀 전체 삭제다.
--       RPC가 없거나 실패한 애플리케이션은 DELETE/POST 폴백을 하지 않고 업로드를 거절한다.

do $$
declare
  team_att smallint;
  hash_att smallint;
begin
  select attnum into team_att from pg_attribute
  where attrelid = 'public.prism_golden'::regclass and attname = 'team_id' and not attisdropped;
  select attnum into hash_att from pg_attribute
  where attrelid = 'public.prism_golden'::regclass and attname = 'content_hash' and not attisdropped;
  if team_att is null or hash_att is null then
    raise exception 'prism_golden requires team_id and content_hash columns';
  end if;
  if exists (
    select 1 from public.prism_golden
    where team_id is not null
    group by team_id, content_hash having count(*) > 1
  ) then
    raise exception 'prism_golden has duplicate (team_id, content_hash) rows; deduplicate before migration';
  end if;

  if not exists (
    select 1 from pg_index
    where indrelid = 'public.prism_golden'::regclass
      and indisunique
      and indnkeyatts = 2
      and indkey::text = team_att::text || ' ' || hash_att::text
  ) then
    alter table public.prism_golden
      add constraint prism_golden_team_content_hash_key unique (team_id, content_hash);
  end if;
end $$;

create or replace function public.prism_write_golden(
  p_team_id uuid,
  p_rows jsonb,
  p_replace boolean default false,
  p_source text default 'manual'
) returns integer
language plpgsql
as $$
declare
  row_count integer;
begin
  if p_team_id is null then
    raise exception 'team_id is required';
  end if;
  if jsonb_typeof(p_rows) is distinct from 'array' then
    raise exception 'p_rows must be a JSON array';
  end if;
  if exists (
    select 1
    from jsonb_array_elements(p_rows) as e(value)
    where jsonb_typeof(e.value) <> 'object'
       or jsonb_typeof(e.value->'content') is distinct from 'object'
       or jsonb_typeof(e.value->'expected') is distinct from 'object'
       or coalesce(e.value->>'content_hash', '') !~ '^[0-9a-f]{16}$'
  ) then
    raise exception 'golden rows require object content/expected and a 16-character lowercase hex content_hash';
  end if;

  row_count := jsonb_array_length(p_rows);
  if p_replace then
    delete from public.prism_golden where team_id = p_team_id;
  end if;

  with incoming as (
    select value->>'content_hash' as content_hash,
           value->'content' as content,
           value->'expected' as expected,
           ord
    from jsonb_array_elements(p_rows) with ordinality as e(value, ord)
  ), latest as (
    select distinct on (content_hash) content_hash, content, expected
    from incoming order by content_hash, ord desc
  )
  insert into public.prism_golden (team_id, content_hash, content, expected, source)
  select p_team_id, content_hash, content, expected, coalesce(nullif(btrim(p_source), ''), 'manual')
  from latest
  on conflict (team_id, content_hash) do update
  set content = excluded.content,
      expected = excluded.expected,
      source = excluded.source,
      created_at = now();

  return row_count;
end;
$$;

revoke all on function public.prism_write_golden(uuid, jsonb, boolean, text) from public;
grant execute on function public.prism_write_golden(uuid, jsonb, boolean, text) to service_role;
