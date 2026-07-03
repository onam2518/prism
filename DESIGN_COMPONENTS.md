# Prism 컴포넌트 계약

화면에서 반복되는 컴포넌트의 용도·상태·사용 규칙을 계약으로 고정한 문서다.
형식은 Meta Astryx의 컴포넌트 상태 매트릭스 관례를 차용했고, 값의 원천은 코드다
(상태 셀렉터가 없는 항목은 전역 폴백을 따른다). 새 UI를 만들 때는 여기서 컴포넌트를
고르고, 없으면 이 문서에 계약을 추가한 뒤 구현한다.

공통 원칙

- 토큰 우선: 간격 `--ds-space-*`(4px 그리드) · 패널 좌우 `--ds-pad-panel-x` · 표/필터 인셋 `--ds-pad-inset` · radius 는 용도 별칭(`--ds-radius-card/control/chip`).
- 포커스는 `--ds-focus-ring` box-shadow 하나로 통일(outline 하드코딩 금지). 전역 폴백: `:where(button,a,[role=tab],select,summary):focus-visible`.
- 타이틀·소제목·스텝 배지·퀘스트·큰 숫자는 디스플레이 폰트(GmarketSans), 본문·표·입력은 Pretendard.
- 값 태그(등급·인텐트·카테고리·사유·엔티티)는 반드시 호버 정의(`data-tip` + `termDef`)를 단다.
- em-dash 문자 금지 · 빈 셀은 `·` · 위험(파괴) 버튼은 danger outline 단일 규격.
- 색은 토큰(`var(--ds-*)`) 경유가 기본. 상태 텍스트는 `--ds-success-deep`/`--ds-error-deep`(틴트 배경 위 가독 · 다크 자동 보정), 색 배경 위 글자는 `--ds-on-primary`(Primary) 또는 `--ds-text-static-white`(모드 무관).
- **고정 액센트 예외**(값 직접 사용 허용 · 감사 제외): 게임화 티어 5색(#1e84ff·#5c77ff·#ff9429·#a05cff·#ffb020), 배지 금장 #b8791f·잠금 오브 #9aa0a6·축하 그라디언트, A/B 'B' 슬롯 주황 #ff6a3d, `[data-theme='dark']` 보정 전용 값. `var(--x, #hex)` 폴백은 하드코딩이 아니다.

## 선택 컨트롤 3종 (혼용 금지)

| 컴포넌트 | 용도 | 기본 | hover | 선택/활성 | 비고 |
|---|---|---|---|---|---|
| `.srcfilter__chip` | **다중 토글 필터** (모델 필터·대상 콘텐츠·계열 선택 등) | 30px 필칩, 헤어라인 보더 | 보더 프라이머리 | `.sel` = 파랑 배경/보더 | 단일 선택 판정에는 쓰지 않는다 |
| `.selctl` | **단일 지정 선택** (기준 모델·용도·서비스 등 셀렉트 1개) | 태그 라벨 + 무보더 select 박스 | 박스 보더 프라이머리 | select 값 자체 | 내부 select 는 커스텀 셰브론 규격(`select.field`) |
| `.selctl--a/--b` + `.abslot` | **A/B 비교 슬롯** | A=파랑 태그, B=주황(#ff6a3d) 태그 | 동일 | 슬롯 값 | 비교 화면(결과 비교·모델별 비교) 전용 · 사이 `VS`(`.abvs`) |

## 판정 컨트롤

| 컴포넌트 | 용도 | 기본 | hover | 활성(`.is-on`) | 비활성 |
|---|---|---|---|---|---|
| `.verdictbtn--good` | 정확/채택 | 36px 필, 도트 회색 | -1px 리프트 + 보더 강조 | 초록 보더/틴트 + 도트 글로우 | (전역 폴백) |
| `.verdictbtn--bad` | 수정/탈락 | 동일 | 동일 | 빨강 보더/틴트 + 도트 글로우 | 〃 |

검수(정확/수정)와 평가 판정(채택/탈락)은 같은 컴포넌트를 쓴다(같은 행위 문법 = 같은 모양).

## 버튼 (`ds-components.css`)

| 변형 | 용도 | 비활성 |
|---|---|---|
| `.ds-btn--primary` | 화면당 1개의 주 행동(실행·저장·시작) | `--ds-interaction-disabled` 배경 + not-allowed |
| `.ds-btn--secondary` | 보조 행동(내보내기·새로고침 대체 등) | 〃 |
| `.ds-btn--outline` (+`--c-danger`) | 파괴·위험 행동은 반드시 danger outline | 〃 |
| `.ds-btn--ghost` / `.copybtn` | 인라인 저강도 행동(닫기·복사·이동 링크) | 〃 |
| `.ds-iconbtn--bordered` | 헤더 우측 아이콘 행동(새로고침·다운로드) · `data-tip` 필수 | 〃 |

크기는 `--s-sm(26~28px)/--s-md(36px)` 중 택1, 같은 행에서는 같은 크기.

## 카드·구획

| 컴포넌트 | 계약 |
|---|---|
| `.panel` | 카드 1구획. `--ds-radius-card` · 헤더 `.panel-hd`(타이틀 b + `.meta` 설명, 우측 액션은 `ml-auto`) · 본문 `.panel-bd`(`--ds-pad-inset`/`--ds-pad-panel-x`) |
| `.panel` 내 표/필터 | 표 박스와 `.filterbar` 는 좌우 `--ds-pad-inset` 정렬(단일 원천) |
| `.stepline` | 프로세스 스텝 헤더: `STEP N` 배지(디스플레이 폰트) + 타이틀 + meta |
| `.subhd` | 패널 내 소제목(디스플레이 폰트 13.5px) + `.meta` 설명 |
| `.tiles`/`.tile` | 지표 타일. 숫자 `.n`(tnum·lh 1) + 라벨 `.t`(11px·margin 6px) · 통계 용어는 `data-tip` 필수 |
| `.keyline` | 키·값 한 줄 행(이름 120px + 상태점 `.sdot` + 입력 flex + 버튼 + 메시지) · 행 사이 헤어라인 |

## 태그·표시

| 컴포넌트 | 계약 |
|---|---|
| `.ds-badge--intent/--category/--reason/--entity` | 값 태그. **호버 정의 필수**(`termDef`, '값 (건수)' 접미 자동 정규화) |
| `.ds-badge--success/--error/--warning/--neutral` | 상태 태그(G/R·오류 의심·교정 필요·평가용 등). 의미 고정: warning=주의·보류, error=오류·확정 실패 |
| `.abbar` | A/B 미니 막대(A 파랑/B 주황) + 우세 `▲`(`.abwin`) · 비교 표 수치 셀 전용 |
| `#tipfloat` (`data-tip`/`data-tip-pos`) | 전역 고정 툴팁. CSS 의사요소 툴팁 금지(오버플로 클리핑) |

## 진행·피드백

| 컴포넌트 | 계약 |
|---|---|
| `.ds-progress`(+`--indeterminate`) | 막대 진행. 라벨+% 헤드 구조 |
| `.onboard__busy` | 로그인/가입 진행: 캐릭터 펄스 링 + 점 3개 + 상태 문구 · 진행 중 폼/CTA 숨김 |
| 토스트(`liveToast`/`celebratePoints`) | 행동 즉시 보상·결과 알림. 실패는 `_err` 경로 |
| `.codeblock` | 프롬프트·계약 원문 표시(맥 도트 바 + 스테이지 라벨) · 읽기 전용은 `readonly` |
| `.polpal` + `.polfab` | 정책 팔레트(플로팅 도움말): 우하단 `?` 런처, 헤더 드래그 이동·위치는 localStorage. 탭 4종(인텐트/카테고리/품질 사유/등급) + 검색 + 현재 검수 항목 값 바로가기. 값 태그 클릭 = `polShow(kind, val)` 딥링크·`is-hl` 강조. 원천은 /dict(intentDefs·categoryCriteria·qualityMetas) 단일 |

## 게임화 표면

| 컴포넌트 | 계약 |
|---|---|
| `.charcard` | 내 캐릭터 카드: 레벨(커브 = store.level_of 와 동일) + XP 게이지 + 스탯 4종(한 줄 · 아이콘 없음 · 호버 정의) |
| `.charcard__mission` | 퀘스트 행: 도전과제형 축약 문구(디스플레이 폰트) + `· n/N` + 보상/도전 CTA |
| 배지 그리드 | 22종 래더(1만 건 완주 설계) · 달성 축하는 1회만 |
| `.squest` | 팀 퀘스트 카드(사이드바 · 에이전트 카드 아래 · 전 메뉴 노출): 유형 태그 + D-day(D-1 이하 `is-urgent` 경고색) + 제목 + 목표 + 진행 게이지(`is-done`=성공색)/카운트 + ⏳시한 + CTA. RPG 퀘스트 트래커 관례 차용 · 클릭 = 콘텐츠 검수 이동 · 호버 정의 필수 |
| `.squest--done` | 완료 잔상(목표 소진 후 72시간): `🏆 vN 반영 완료 · 새 퀘스트 대기 중`. 클릭 없음(cursor:default) · 퀘스트 표면은 사이드바 카드 단일(히어로에는 두지 않는다) |

## 변경 규칙

- 새 컴포넌트/상태 추가 시: 이 문서에 행 추가 → 구현 → 스모크에 렌더 마커 추가.
- 상태 색·간격을 바꿀 때는 컴포넌트 CSS 가 아니라 토큰(ds-theme)에서 바꾼다.
- 근거 참조: Astryx 컴포넌트 상태 매트릭스 관례(문서 형식만 차용 · React 계층 미도입).
