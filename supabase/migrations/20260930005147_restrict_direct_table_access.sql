-- Prism's browser uses the authenticated Python API; only its service role accesses tables.
-- Legacy direct-client policies allowed cross-team reads/writes and profile role changes.
-- Keep RLS enabled and remove that unused access path without rewriting application data.
set lock_timeout = '2s';
set statement_timeout = '15s';
do $$
declare target record;
begin
  for target in select schemaname, tablename from pg_catalog.pg_tables
    where schemaname = 'public' and left(tablename, 6) = 'prism_' order by tablename loop
    execute format('alter table %I.%I enable row level security', target.schemaname, target.tablename);
    execute format('revoke all on table %I.%I from public, anon, authenticated', target.schemaname, target.tablename);
    execute format('grant all on table %I.%I to service_role', target.schemaname, target.tablename);
  end loop;
  for target in select schemaname, tablename, policyname from pg_catalog.pg_policies
    where schemaname = 'public' and left(tablename, 6) = 'prism_' order by tablename, policyname loop
    execute format('drop policy %I on %I.%I', target.policyname, target.schemaname, target.tablename);
  end loop;
  for target in select schemaname, sequencename from pg_catalog.pg_sequences
    where schemaname = 'public' and left(sequencename, 6) = 'prism_' loop
    execute format('revoke all on sequence %I.%I from public, anon, authenticated', target.schemaname, target.sequencename);
    execute format('grant all on sequence %I.%I to service_role', target.schemaname, target.sequencename);
  end loop;
end $$;
revoke all on function public.prism_my_team() from public, anon, authenticated;
grant execute on function public.prism_my_team() to service_role;
notify pgrst, 'reload schema';
