begin;
create temporary table review_time_smoke (like public.prism_events including all);
do $$
declare fn text; rv uuid='00000000-0000-0000-0000-000000000123'; a text='00000000-0000-0000-0000-000000000001'; b text='00000000-0000-0000-0000-000000000002'; n integer;
begin
select pg_get_functiondef('public.prism_log_review_time(uuid,uuid,text,text)'::regprocedure) into fn;
fn:=replace(fn,'public.prism_log_review_time','pg_temp.prism_log_review_time');
fn:=replace(fn,'public.prism_events','pg_temp.review_time_smoke');
execute fn;
perform pg_temp.prism_log_review_time(rv,null,a,'{"hash":"test","recorded_at":1700000000}');
perform pg_temp.prism_log_review_time(rv,null,a,'{"hash":"test","recorded_at":1700000000}');
perform pg_temp.prism_log_review_time(rv,null,b,'{"hash":"test","recorded_at":1700000000}');
select count(*) into n from pg_temp.review_time_smoke;
if n<>2 then raise exception 'duplicate event saved';end if;
if (select count(distinct day) from pg_temp.review_time_smoke)<>2 then raise exception 'legacy unique key collision';end if;
if (select min(created_at) from pg_temp.review_time_smoke)<>to_timestamp(1700000000) then raise exception 'original timestamp lost';end if;
end $$;
select 'passed: idempotency, same-second sessions, original timestamp' result;
rollback;
