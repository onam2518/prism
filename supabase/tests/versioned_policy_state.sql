-- Run after the migration with an administrative connection. All fixture writes roll back.
begin;
set local lock_timeout = '2s';
set local statement_timeout = '15s';
set local role service_role;
do $$
declare k text := 'cas_verify_' || gen_random_uuid()::text;
begin
  if has_function_privilege('anon', 'public.prism_compare_report(text,text,jsonb,jsonb,text,jsonb)', 'execute')
     or has_function_privilege('authenticated', 'public.prism_compare_report(text,text,jsonb,jsonb,text,jsonb)', 'execute')
     or not has_function_privilege('service_role', 'public.prism_compare_report(text,text,jsonb,jsonb,text,jsonb)', 'execute') then
    raise exception 'CAS role grants failed';
  end if;
  if not public.prism_compare_report(k, '', null, '{"revision":1}') then raise exception 'insert failed'; end if;
  if public.prism_compare_report(k, '', null, '{"revision":2}') then raise exception 'stale insert accepted'; end if;
  if public.prism_compare_report(k, '', '{"revision":1}', '{"revision":2}', k || '_control', '{}') then
    raise exception 'missing guard accepted';
  end if;
  if not public.prism_compare_report(k || '_control', '', null, '{"revision":1}') then raise exception 'guard insert failed'; end if;
  if not public.prism_compare_report(k, '', '{"revision":1}', '{"revision":2}', k || '_control', '{"revision":1}') then
    raise exception 'guarded update failed';
  end if;
  if public.prism_compare_report(k, '', '{"revision":1}', '{}') then raise exception 'stale update accepted'; end if;
  if not public.prism_compare_report(k, k, null, '{}') then raise exception 'team isolation failed'; end if;
end $$;
rollback;
