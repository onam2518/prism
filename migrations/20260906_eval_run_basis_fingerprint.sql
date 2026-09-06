-- 평가 런 기준 고정 + 중복 시작 원자화 (2026-09-06).
-- backward compatible: 기존 런의 basis_fingerprint=''은 읽을 수 있지만 재개하지 않고
-- 앱이 안전한 새 시작을 요구한다. 이 파일을 Supabase Database Migrations에서 적용한 뒤
-- PRISM_BACKEND=supabase 평가 시작을 활성화한다.

alter table public.prism_eval_runs
  add column if not exists basis_fingerprint text not null default '';

-- 같은 advisory key의 시작만 직렬화한다. 새 실험은 의도적으로 기존 running 런을 재사용하지 않는다.
create or replace function public.prism_eval_run_start_or_reuse(
  p_team uuid,
  p_model text,
  p_scope text,
  p_total integer,
  p_created_by text,
  p_basis_fingerprint text,
  p_new_experiment boolean default false
) returns jsonb
language plpgsql
as $$
declare
  v_id bigint;
begin
  if coalesce(p_basis_fingerprint, '') = '' then
    raise exception 'basis fingerprint is required';
  end if;
  if p_total < 0 then
    raise exception 'total must not be negative';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    coalesce(p_team::text, '') || E'\x1f' || coalesce(p_model, '') || E'\x1f' ||
    coalesce(p_scope, '') || E'\x1f' || p_basis_fingerprint, 0));
  if not p_new_experiment then
    select id into v_id from public.prism_eval_runs
      where team_id is not distinct from p_team
        and model = coalesce(p_model, '')
        and scope = coalesce(p_scope, 'all')
        and basis_fingerprint = p_basis_fingerprint
        and status = 'running'
      order by id desc limit 1;
    if found then
      return jsonb_build_object('id', v_id, 'reused', true);
    end if;
  end if;
  insert into public.prism_eval_runs
    (team_id, model, scope, status, cursor, total, created_by, basis_fingerprint)
  values
    (p_team, coalesce(p_model, ''), coalesce(p_scope, 'all'), 'running', 0, p_total,
     coalesce(p_created_by, ''), p_basis_fingerprint)
  returning id into v_id;
  return jsonb_build_object('id', v_id, 'reused', false);
end;
$$;

revoke all on function public.prism_eval_run_start_or_reuse(uuid, text, text, integer, text, text, boolean)
  from public, anon, authenticated;
grant execute on function public.prism_eval_run_start_or_reuse(uuid, text, text, integer, text, text, boolean)
  to service_role;
