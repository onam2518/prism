-- 정답 메타(등급·인텐트·분류·요약·엔티티) 유실 방지.
-- 2026-09-29 검수 재생성이 정답을 {finalGrade, reasons} 로 다시 써서 메타 4축이 빠졌다.
-- 앱 버전과 무관하게 DB 에서 막는다: 값이 있던 축은 쓰기에서 빠지거나 빈 값이 되어도 이전 값을 유지한다.
--   UPDATE: OLD.expected 기준
--   INSERT: 같은 팀·해시의 최근 이력(prism_golden_history) 기준 · 삭제 후 재삽입(prism_write_golden, 구버전 DELETE+POST) 포함
-- 의도적으로 비우려면 같은 트랜잭션에서 set local prism.golden_allow_meta_clear = 'on'.

create index if not exists ix_prism_golden_history_team_hash
  on public.prism_golden_history (team_id, content_hash, recorded_at desc);

create or replace function public.prism_golden_meta_keep()
returns trigger
language plpgsql
set search_path to ''
as $function$
declare
  prev jsonb;
  k text;
begin
  if coalesce(current_setting('prism.golden_allow_meta_clear', true), '') = 'on'
     or jsonb_typeof(new.expected) is distinct from 'object' then
    return new;
  end if;
  if tg_op = 'UPDATE' then
    prev := old.expected;
  else
    select h.expected into prev
      from public.prism_golden_history h
     where h.team_id is not distinct from new.team_id and h.content_hash = new.content_hash
     order by h.recorded_at desc, h.id desc
     limit 1;
  end if;
  if jsonb_typeof(prev) is distinct from 'object' then
    return new;
  end if;
  foreach k in array array['finalGrade', 'intent', 'content_category', 'summary', 'entities'] loop
    if prev ? k and prev -> k not in ('null'::jsonb, '""'::jsonb, '[]'::jsonb, '{}'::jsonb)
       and (not new.expected ? k or new.expected -> k in ('null'::jsonb, '""'::jsonb, '[]'::jsonb, '{}'::jsonb)) then
      new.expected := jsonb_set(new.expected, array[k], prev -> k);
    end if;
  end loop;
  return new;
end;
$function$;

revoke all on function public.prism_golden_meta_keep() from public, anon, authenticated;

drop trigger if exists prism_golden_meta_keep_insert on public.prism_golden;
create trigger prism_golden_meta_keep_insert
  before insert on public.prism_golden
  for each row execute function public.prism_golden_meta_keep();

drop trigger if exists prism_golden_meta_keep_update on public.prism_golden;
create trigger prism_golden_meta_keep_update
  before update of expected on public.prism_golden
  for each row execute function public.prism_golden_meta_keep();
