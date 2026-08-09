# 디자인 시스템

`template.html` 에 이미 들어 있다. 새로 쓰지 말고 골라 쓴다.

## 색

```
--paper #FFFDF7   슬라이드 바탕
--card  #FFFFFF   카드 · 표 · 각주 패널 안쪽
--ink   #17181C   본문 · 테두리
--ink2  #4E5560   보조 문장
--muted #8A8F98   라벨 · 캡션
--line  #E8E4DA   옅은 구분선
--hl    #FFE27A   형광펜
빨강 #FF5F5B · 파랑 #2E6BFF · 초록 #0AA05F · 노랑 #F5B800 · 주황 #D4721C
연한 배경 --red-soft #FFECEA · --blue-soft #E9EFFF · --green-soft #E3F4EB · --yellow-soft #FFF3D1
일러스트 배경 #FAF2EA
```

색은 의미를 담을 때만 쓴다. 빨강은 문제·위험, 파랑은 MCP 쪽·해법, 초록은 확인·안전, 노랑은 규격·주의.

## 글꼴

- 제목 · 숫자 · 라벨 = **Gmarket Sans Bold**
- 본문 = **Pretendard Variable**
- 코드·원문 표기 = `ui-monospace`

`build.py` 가 저장소 전체 파일을 심는다. 서브셋 재사용 금지.

## 뼈대

```html
<section class="slide">
  <div class="inner">
    <span class="tag p1"><span class="dotc"></span>1-1 · 꼭지명</span>
    <h2>헤드라인 <span class="hl">강조</span></h2>
    …본문…
    <ul class="tlist has-terms">…</ul>
    <div class="terms">…</div>
  </div>
</section>
```

- `.slide` 는 화면 전체를 덮고 좌우 방향키로 넘어간다.
- `.inner` 최대 폭 1060px.
- 태그 점 색: `p1` 빨강 · `p2` 파랑 · `p3` 초록 · `p4` 노랑.

## 컴포넌트

| 이름 | 클래스 | 쓰는 자리 |
| --- | --- | --- |
| 카드 | `.card` + `.t-red/.t-blue/.t-green/.t-yellow` | 2~4개 병렬 설명 |
| 카드 머리 | `.chead` + `.ibadge` + `.kicker` | 아이콘 배지와 라벨 |
| 격자 | `.grid.g2` `.grid.g3` `.grid.n4` | 2열 · 3열 · 2×2 |
| 표 | `.tblwrap > table.dense` | 6행 이상 비교 |
| 출처 표 | `table.dense.src` | 부록 |
| 숫자 타일 | `.statrow > .stat` | 현황 수치 4개 |
| 체크리스트 | `ol.check` | 순서 있는 절차 |
| 목차 | `ol.agenda` | 부 목록 |
| 말풍선 | `.say.br/.bb/.bg/.by` | 캐릭터 코멘트 |
| 결론 행 | `.decide > .row` | 마무리 3줄 |
| 각주 패널 | `ul.tlist` | 모든 슬라이드 하단 |
| 용어 범례 | `.terms > .tm` | 용어 첫 등장 슬라이드 |
| 그림 | `figure.fig > .box` / `.box.illu` | 다이어그램 / 일러스트 |

## 카드 개수별 배치

- 2개 → `.grid.g2`
- 3개 → `.grid.g3`
- 4개 → `.grid.n4` (2×2 · 글자 크게)
- 5개 이상 → 표로 바꾼다

## 아이콘 스프라이트

`#ic-window #ic-chat #ic-db #ic-doc #ic-hash #ic-cal #ic-server #ic-folder #ic-cloud
#ic-plug #ic-person #ic-list #ic-cursor #ic-shield #ic-play #ic-spark #ic-search
#ic-book #ic-plus #ic-check #ic-x #ic-link #ic-arrows #ic-code #ic-shapes #ic-bolt
#ic-lock #ic-clock #ic-hand`

```html
<span class="ibadge b"><svg><use href="#ic-link"></use></svg></span>   <!-- 카드 배지 -->
<svg class="ico"><use href="#ic-db"></use></svg>                        <!-- 표 셀 앞 -->
<use class="u" href="#ic-server" x="10" y="10" width="24" height="24"/> <!-- 그림 안 -->
```

그림 안 아이콘 색: `.u` 회색 · `.ub` 파랑 · `.uy` 노랑 · `.ug` 초록 · `.ur` 빨강.

## 캐릭터

`assets/` 에 4종. 표지 그리드, 부 디바이더, 말풍선, 마무리 스트립에 쓴다.

| 파일 | 이름 | 말풍선 색 |
| --- | --- | --- |
| `ddakji.svg` | 딱지 · 감독 | `.say.br` 빨강 |
| `daesik.svg` | 대식 · 타자 | `.say.bb` 파랑 |
| `boksil.svg` | 복실 · 포수 | `.say.by` 노랑 |
| `yonghee.svg` | 용희 · 투수 | `.say.bg` 초록 |

한 슬라이드에 하나까지. 말풍선은 사람이 할 법한 한마디를 담는다. 장식으로 쓰지 않는다.

## 하단 각주 패널

```html
<ul class="tlist has-terms">
  <li class="lead">결론 한 줄. 굵게.</li>
  <li class="sub">근거 한 줄. 들여쓰기.</li>
</ul>
<div class="terms"><span class="tlbl">용어</span>
  <span class="tm"><b>무상태</b><i>stateless</i>세션을 붙잡지 않는 구조</span>
</div>
```

- 용어 범례가 없으면 `has-terms` 를 빼고 `.terms` 도 넣지 않는다.
- 리드는 슬라이드당 1~2개까지. 나머지는 전부 `sub`.
