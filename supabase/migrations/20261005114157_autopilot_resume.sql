-- 오토파일럿 재개: 서버 재시작(배포)으로 끊긴 런을 끊긴 라운드부터 이어서 실행한다.
-- model = 시작 시 고른 라운드 평가 모델(재개 때 같은 모델로) · resumes = 자동 재개 횟수(반복 장애 시 중단 판단)
alter table public.prism_autopilot_runs add column if not exists model text not null default '';
alter table public.prism_autopilot_runs add column if not exists resumes integer not null default 0;
