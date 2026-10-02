-- 정답 메타 보존 트리거 검증. 실제 행 하나로 확인하고 마지막 예외로 전부 되돌린다(데이터 변경 없음).
do $$
declare t uuid; h text; c jsonb; before jsonb; got jsonb;
  axes text[] := array['finalGrade','intent','content_category','summary','entities'];
begin
  select team_id, content_hash, content, expected into t, h, c, before from public.prism_golden
   where team_id is not null and coalesce(expected->>'summary', '') <> '' and expected ? 'intent' limit 1;
  if h is null then raise exception 'no golden row with meta to test'; end if;

  -- 2026-09-29 패턴: RPC 로 {finalGrade, reasons} 만 기록
  perform public.prism_write_golden(t, jsonb_build_array(jsonb_build_object('content_hash', h, 'content', c,
    'expected', '{"finalGrade":"G","reasons":[]}'::jsonb)), false, 'review');
  select expected into got from public.prism_golden where team_id = t and content_hash = h;
  if exists (select 1 from unnest(axes) k where before ? k and got->k is distinct from before->k) then
    raise exception 'rpc rewrite dropped meta: %', got;
  end if;

  -- 구버전 앱 패턴: 직접 DELETE 후 INSERT
  delete from public.prism_golden where team_id = t and content_hash = h;
  insert into public.prism_golden(team_id, content_hash, content, expected, source)
    values (t, h, c, '{"finalGrade":"G","reasons":[]}', 'review');
  select expected into got from public.prism_golden where team_id = t and content_hash = h;
  if exists (select 1 from unnest(axes) k where before ? k and got->k is distinct from before->k) then
    raise exception 'delete+insert dropped meta: %', got;
  end if;

  -- 빈 값 UPDATE 는 막고, 값 변경은 허용
  update public.prism_golden set expected = expected || '{"intent":[],"summary":""}' where team_id = t and content_hash = h;
  select expected into got from public.prism_golden where team_id = t and content_hash = h;
  if got->'intent' is distinct from before->'intent' or got->'summary' is distinct from before->'summary' then
    raise exception 'update emptied meta: %', got;
  end if;
  update public.prism_golden set expected = expected || '{"intent":["실용 정보"]}' where team_id = t and content_hash = h;
  select expected into got from public.prism_golden where team_id = t and content_hash = h;
  if got->'intent' is distinct from '["실용 정보"]'::jsonb then raise exception 'value change blocked: %', got; end if;

  -- 명시적으로 허용한 트랜잭션에서는 비울 수 있다
  perform set_config('prism.golden_allow_meta_clear', 'on', true);
  update public.prism_golden set expected = expected || '{"summary":""}' where team_id = t and content_hash = h;
  select expected into got from public.prism_golden where team_id = t and content_hash = h;
  if got->>'summary' <> '' then raise exception 'explicit clear blocked: %', got; end if;

  raise exception 'golden_meta_guard: ok (rolled back)';
end $$;
