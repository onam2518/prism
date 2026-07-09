"""모바일 검수 전용 페이지(/m) 마크업 · 검수만 덜어낸 카드 스택 UI.

디자인은 운영 벤더 CSS(ds-theme·ds-components·app.css)를 그대로 로드하고
DS 컴포넌트 클래스(ds-badge·verdictbtn·field·ds-btn)를 재사용한다 → PC 와 동일 언어,
PC 팔레트·컴포넌트 변경을 자동 추종. 모바일 셸(.m-*)만 mobile.css 가 담당한다.
상태·로직은 vendor/mobile.js(기존 API 계약만 사용 · 서버 로직 무변).
"""

MOBILE_PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f4f5f7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#161718" media="(prefers-color-scheme: dark)">
<title>Prism 검수</title>
<link rel="icon" href="/vendor/prism-favicon.svg">
<link rel="apple-touch-icon" href="/vendor/prism-icon-180.png">
<link href="/vendor/pretendard.css" rel="stylesheet">
<link href="/vendor/ds-theme.css" rel="stylesheet">
<link href="/vendor/ds-components.css" rel="stylesheet">
<link href="/vendor/app.css" rel="stylesheet">
<link href="/vendor/mobile.css" rel="stylesheet">
</head>
<body class="mbody">
<div class="mshell" x-data="mreview()" x-init="init()" x-cloak>

  <!-- ━━ 로그인 ━━ -->
  <section class="m-login" x-show="view === 'login'">
    <img class="m-login__mark" src="/vendor/prism-icon-180.png" alt="Prism">
    <h1>프리즘 검수</h1>
    <p class="m-login__sub">팀 계정으로 로그인하면<br>이동 중에도 검수를 이어갑니다</p>
    <template x-if="backend === 'supabase'">
      <div>
        <input class="field" type="email" inputmode="email" autocomplete="username" placeholder="이메일" x-model="email">
        <input class="field" type="password" autocomplete="current-password" placeholder="비밀번호" x-model="pw" x-on:keydown.enter="login()">
        <button type="button" class="ds-btn ds-btn--primary m-cta" x-bind:disabled="busy" x-on:click="login()" x-text="busy ? '로그인 중…' : '로그인하고 시작'"></button>
      </div>
    </template>
    <template x-if="backend && backend !== 'supabase'">
      <div>
        <input class="field" placeholder="닉네임" x-model="nick" x-on:keydown.enter="nickStart()">
        <button type="button" class="ds-btn ds-btn--primary m-cta" x-on:click="nickStart()">시작하기</button>
      </div>
    </template>
    <div class="m-login__hint">계정은 데스크탑과 동일 · 가입은 데스크탑에서</div>
    <div class="m-err" x-show="err" x-text="err"></div>
  </section>

  <!-- ━━ 검수 카드(메인) ━━ -->
  <section class="m-main" x-show="view === 'list' || view === 'card'">
    <header class="m-top">
      <button type="button" class="m-back" x-show="view === 'card'" x-on:click="back()" aria-label="목록으로">←</button>
      <img class="m-top__logo" x-show="view !== 'card'" src="/vendor/prism-favicon.svg" alt="">
      <b>PRISM</b>
      <span class="m-top__me" x-text="name + (points !== null ? (' · ' + points + 'pt') : '')"></span>
      <button type="button" class="m-help" x-on:click="sheet = 'help'" aria-label="도움말">?</button>
    </header>
    <div class="m-prog">
      <div class="m-prog__row"><span>팀 검수 진행</span><b class="tnum" x-text="(items.length - unreviewedCount()) + ' / ' + items.length + '건'"></b></div>
      <div class="m-bar"><i x-bind:style="'width:' + progPct() + '%'"></i></div>
    </div>

    <!-- 목록: 전체 콘텐츠 · 미검수 배지 · 탭하면 카드로 -->
    <div class="m-listwrap" x-show="view === 'list'">
      <button type="button" class="ds-btn ds-btn--primary m-start" x-show="unreviewedCount()" x-on:click="startReview()" x-text="'검수 시작 · 미검수 ' + unreviewedCount() + '건'"></button>
      <div class="m-rows">
        <template x-for="(it, i) in items" x-bind:key="it.hash">
          <button type="button" class="m-row" x-bind:class="reviewed(it) ? 'is-done' : ''" x-on:click="open(i)">
            <span class="m-row__dot" x-bind:class="it.grade === 'G' ? 'g' : (it.grade === 'R' ? 'r' : 'h')"></span>
            <span class="m-row__main">
              <span class="m-row__title" x-text="it.title"></span>
              <span class="m-row__meta" x-text="it.service + (it.model ? ' · ' + it.model : '')"></span>
            </span>
            <span class="ds-badge ds-badge--success" x-show="reviewed(it)">검수함</span>
            <span class="ds-badge ds-badge--neutral" x-show="!reviewed(it)">미검수</span>
          </button>
        </template>
      </div>
      <div class="m-login__hint" x-show="!items.length" style="margin-top:40px">지금은 검수할 콘텐츠가 없어요</div>
    </div>

    <template x-if="view === 'card' && cur()">
      <article class="m-card">
        <div class="m-card__scroll">
          <div class="m-card__meta">
            <span class="ds-badge ds-badge--category" x-show="cur().service" x-text="cur().service"></span>
            <span class="ds-badge" x-bind:class="gradeClass(cur().grade)"><span class="ds-badge__dot"></span><span x-text="gradeLabel(cur().grade)"></span></span>
            <span class="ds-badge ds-badge--neutral" x-show="cur().model" x-text="cur().model + (cur().version > 1 ? ' · v' + cur().version : '')"></span>
          </div>
          <h2 class="m-card__title" x-text="cur().title"></h2>
          <div class="m-lead" x-show="cur().summary"><em>리드문 (초안)</em><span x-text="cur().summary"></span></div>
          <div class="m-chips">
            <template x-for="e in (cur().entities || [])" x-bind:key="'e' + e"><span class="m-chip" x-text="e"></span></template>
            <template x-for="i in (cur().intent || [])" x-bind:key="'i' + i"><button type="button" class="m-chip m-chip--tap" x-on:click="intentDef(i)" x-text="i"></button></template>
            <template x-for="c in (cur().category || [])" x-bind:key="'c' + c"><span class="m-chip" x-text="catKo(c)"></span></template>
          </div>
          <div class="m-body" x-text="cur().body || '본문이 저장되지 않은 콘텐츠입니다 · 데스크탑에서 원문 링크로 확인하세요'"></div>
        </div>
        <div class="m-team" x-show="cur().fb && cur().fb.n" x-text="cur().fb ? teamLine(cur().fb) : ''"></div>
      </article>
    </template>
    <footer class="m-actions" x-show="view === 'card'">
      <button type="button" class="verdictbtn verdictbtn--bad" x-on:click="openFix()"><span class="verdictbtn__dot"></span>수정 필요</button>
      <button type="button" class="verdictbtn verdictbtn--good" x-on:click="good()"><span class="verdictbtn__dot"></span>정확</button>
    </footer>
  </section>

  <!-- ━━ 완료 ━━ -->
  <section class="m-done" x-show="view === 'done'">
    <div class="m-done__big">🎉</div>
    <h2 x-text="done ? '오늘 미검수를 모두 끝냈어요!' : '지금은 검수할 콘텐츠가 없어요'"></h2>
    <p x-show="done" x-text="'판정 ' + done + '건 · 교정 ' + fixed + '건'"></p>
    <p x-show="!done">새 배치가 준비되면 여기서 이어집니다</p>
    <div class="m-stats" x-show="done">
      <div class="m-stat"><b class="tnum" x-text="'+' + (done * 10 + fixed * 15)"></b><span>이번에 획득 PT</span></div>
      <div class="m-stat"><b class="tnum" x-text="done"></b><span>판정</span></div>
      <div class="m-stat"><b class="tnum" x-text="fixed"></b><span>교정</span></div>
    </div>
    <button type="button" class="ds-btn ds-btn--primary" x-on:click="view = 'list'">목록으로</button>
    <button type="button" class="ds-btn ds-btn--secondary" x-on:click="loadItems().then(() => view = 'list')">새 콘텐츠 확인</button>
    <button type="button" class="m-logout" x-on:click="logout()">로그아웃</button>
  </section>

  <!-- ━━ 바텀시트: 교정 / 도움말 / 용어 ━━ -->
  <div class="m-dim" x-show="sheet" x-on:click="sheet = ''"></div>

  <div class="m-sheet" x-show="sheet === 'fix'">
    <div class="m-grab"></div>
    <h3>어떤 요소를 고칠까요?</h3>
    <div class="m-elems">
      <template x-for="fe in FIX_ELEMENTS" x-bind:key="fe.id">
        <button type="button" class="m-elem" x-bind:class="fix.elems.includes(fe.id) ? 'sel' : ''" x-on:click="toggleElem(fe.id)" x-text="fe.label"></button>
      </template>
    </div>
    <textarea class="field m-memo" rows="3" x-model="fix.note" x-bind:placeholder="fix.elems.map((e) => elemLabel(e)).join('·') + ' 이(가) 왜 잘못됐는지 · 여러 요소를 고르면 각 단계로 나눠 반영됩니다'"></textarea>
    <div class="m-sheet__row">
      <button type="button" class="ds-btn ds-btn--secondary" x-on:click="sheet = ''">취소</button>
      <button type="button" class="ds-btn ds-btn--primary" x-bind:disabled="!(fix.note || '').trim()" x-on:click="saveFix()">교정 저장</button>
    </div>
  </div>

  <div class="m-sheet" x-show="sheet === 'help'">
    <div class="m-grab"></div>
    <h3>검수 도움말</h3>
    <div class="m-hsec">
      <h4>판정 기준</h4>
      <div class="m-hrow"><span class="ds-badge ds-badge--success"><span class="ds-badge__dot"></span>정확</span><span>초안(리드문·엔티티·인텐트·카테고리)이 본문과 맞으면</span></div>
      <div class="m-hrow"><span class="ds-badge ds-badge--error"><span class="ds-badge__dot"></span>수정 필요</span><span>틀린 요소를 골라 왜 틀렸는지 한 줄이면 충분해요 · 팀 학습 데이터가 됩니다</span></div>
    </div>
    <div class="m-hsec">
      <h4>용어가 낯설면</h4>
      <div class="m-hrow">카드의 파란 테두리 칩(인텐트)을 탭하면 그 값의 정의를 보여줍니다</div>
    </div>
    <a class="m-guide" x-show="guideUrl" x-bind:href="guideUrl" target="_blank" rel="noopener">📖 상세 검수 가이드 열기</a>
    <button type="button" class="m-logout" x-on:click="logout()">로그아웃</button>
  </div>

  <div class="m-sheet" x-show="sheet === 'def'">
    <div class="m-grab"></div>
    <h3 x-text="defTitle"></h3>
    <p class="m-def" x-text="defBody"></p>
  </div>

  <div class="m-toast" x-show="toast" x-text="toast"></div>
</div>
<script src="/vendor/mobile.js"></script>
<script defer src="/vendor/alpine.js"></script>
</body>
</html>
"""
