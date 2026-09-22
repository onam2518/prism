-- 콘텐츠 전역 hash PK 유지: 다른 팀 충돌은 배치 전체 거부, 같은 팀은 최신 운영값 보존.
-- 운영 미적용. 적용/실패/롤백 계약: SUPABASE_MIGRATION.md의 콘텐츠 저장 경계 절 참조.
begin;

create or replace function public.prism_sync_contents(p_rows jsonb)
returns void
language plpgsql
security invoker
set search_path = pg_catalog, public
as $$
declare
    written integer;
begin
    if jsonb_typeof(p_rows) is distinct from 'array' then
        raise exception using errcode = '22023', message = 'contents must be an array';
    end if;

    insert into public.prism_contents as current (
        hash, team_id, service, title, subtitle, body, source_url, image_urls,
        source, final_grade, item_meta, quality_meta, model, version, review
    )
    select hash, team_id, coalesce(service, ''), coalesce(title, ''), coalesce(subtitle, ''),
           coalesce(body, ''), coalesce(source_url, ''), coalesce(image_urls, '[]'::jsonb),
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
notify pgrst, 'reload schema';
commit;
