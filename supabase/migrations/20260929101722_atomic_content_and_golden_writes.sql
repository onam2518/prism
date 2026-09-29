-- 콘텐츠 전역 hash PK 유지: 다른 팀 충돌은 배치 전체 거부, 같은 팀은 최신 운영값 보존.
-- 운영 migration 사본. 설치·검증: docs/RUNTIME_AUDIT_20260929.md.
begin;
set local lock_timeout = '2s';
set local statement_timeout = '15s';

create or replace function public.prism_sync_contents(p_rows jsonb)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
declare
    written integer;
begin
    if jsonb_typeof(p_rows) is distinct from 'array' then
        raise exception using errcode = '22023', message = 'contents must be an array';
    end if;

    insert into public.prism_contents as current (
        hash, team_id, service, title, subtitle, body, source_url, image_urls, source_fields,
        source, final_grade, item_meta, quality_meta, model, version, review
    )
    select hash, team_id, coalesce(service, ''), coalesce(title, ''), coalesce(subtitle, ''),
           coalesce(body, ''), coalesce(source_url, ''), coalesce(image_urls, '[]'::jsonb), coalesce(source_fields, '{}'::jsonb),
           coalesce(source, ''), coalesce(final_grade, ''), item_meta,
           coalesce(quality_meta, '{}'::jsonb), coalesce(model, ''), coalesce(version, 1), coalesce(review, '')
      from jsonb_populate_recordset(null::public.prism_contents, p_rows)
     order by hash
    on conflict (hash) do update set
        service = excluded.service,
        title = excluded.title,
        subtitle = excluded.subtitle,
        body = excluded.body,
        source_url = excluded.source_url,
        image_urls = excluded.image_urls,
        source_fields = excluded.source_fields,
        source = case when coalesce(btrim(current.source), '') <> ''
                      then current.source else excluded.source end,
        final_grade = excluded.final_grade,
        item_meta = excluded.item_meta,
        quality_meta = (coalesce(excluded.quality_meta, '{}'::jsonb) - 'ops_hold' - 'source_status')
                       || (select coalesce(jsonb_object_agg(key, value), '{}'::jsonb)
                             from jsonb_each(coalesce(current.quality_meta, '{}'::jsonb))
                            where key in ('ops_hold', 'source_status')),
        model = excluded.model,
        version = excluded.version,
        review = excluded.review
    -- ON CONFLICT가 잠근 최신 행 기준으로 팀 비교. NULL 팀 경계도 이동 불가.
    where current.team_id is not distinct from excluded.team_id;

    get diagnostics written = row_count;
    if written <> jsonb_array_length(p_rows) then
        -- 한 팀 충돌이면 이번 RPC에서 먼저 삽입/갱신한 다른 행도 전부 롤백.
        raise exception using errcode = '23505', message = 'content belongs to another team';
    end if;
end;
$$;

revoke all on function public.prism_sync_contents(jsonb) from public, anon, authenticated;
grant execute on function public.prism_sync_contents(jsonb) to service_role;
-- 교체·병합은 팀별로 직렬화한다. 기존 중복 행이나 정답을 migration에서 재작성하지 않는다.
create or replace function public.prism_write_golden(
  p_team_id uuid, p_rows jsonb, p_replace boolean default false, p_source text default 'manual'
) returns integer
language plpgsql security invoker set search_path = ''
as $$
declare
  written integer;
begin
  if jsonb_typeof(p_rows) is distinct from 'array' then
    raise exception using errcode = '22023', message = 'golden rows must be an array';
  end if;
  if jsonb_array_length(p_rows) = 0 then
    return 0;
  end if;
  if exists (
    select 1 from jsonb_array_elements(p_rows) as e(value)
    where jsonb_typeof(e.value) is distinct from 'object'
       or jsonb_typeof(e.value->'content') is distinct from 'object'
       or jsonb_typeof(e.value->'expected') is distinct from 'object'
       or coalesce(e.value->>'content_hash', '') !~ '^[0-9a-f]{16}$'
  ) then
    raise exception using errcode = '22023', message = 'invalid golden row';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('prism_golden:' || coalesce(p_team_id::text, '<null>'), 0));
  -- 삭제와 삽입이 한 트랜잭션이다. 기존 정답은 앞선 migration의 이력 트리거가 보존한다.
  delete from public.prism_golden
    where team_id is not distinct from p_team_id
      and (p_replace or content_hash in (select value->>'content_hash' from jsonb_array_elements(p_rows)));
  insert into public.prism_golden(team_id, content_hash, content, expected, source)
    select p_team_id, content_hash, content, expected, coalesce(nullif(btrim(p_source), ''), 'manual')
    from (
      select distinct on (value->>'content_hash') value->>'content_hash' as content_hash,
             value->'content' as content, value->'expected' as expected
      from jsonb_array_elements(p_rows) with ordinality as e(value, ord)
      order by value->>'content_hash', ord desc
    ) as latest;
  get diagnostics written = row_count;
  return written;
end;
$$;
revoke all on function public.prism_write_golden(uuid,jsonb,boolean,text) from public, anon, authenticated;
grant execute on function public.prism_write_golden(uuid,jsonb,boolean,text) to service_role;
notify pgrst, 'reload schema';
commit;
