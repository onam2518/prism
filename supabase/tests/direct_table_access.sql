-- Privilege checks and denied statements only. No user data is changed.
begin;
set local statement_timeout = '15s';
do $$
declare target record; actor text; privilege text;
begin
  for target in select c.oid, c.relname, c.relrowsecurity from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and left(c.relname, 6) = 'prism_' and c.relkind in ('r','p') loop
    if not target.relrowsecurity then raise exception 'RLS disabled: %', target.relname; end if;
    foreach actor in array array['anon','authenticated'] loop
      foreach privilege in array array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'] loop
        if has_table_privilege(actor, target.oid, privilege) then
          raise exception 'unexpected table privilege: % % %', actor, target.relname, privilege;
        end if;
      end loop;
    end loop;
    if not has_table_privilege('service_role', target.oid, 'SELECT,INSERT,UPDATE,DELETE') then
      raise exception 'service role access missing: %', target.relname;
    end if;
  end loop;
end $$;
set local role authenticated;
do $$
begin
  begin
    perform count(*) from public.prism_contents;
    raise exception 'direct contents read was allowed';
  exception when insufficient_privilege then null; end;
  begin
    update public.prism_reviewers set is_admin = true where false;
    raise exception 'direct profile privilege change was allowed';
  exception when insufficient_privilege then null; end;
end $$;
reset role;
set local role service_role;
select count(*) as contents from public.prism_contents;
rollback;
