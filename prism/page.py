"""Prism 앱 HTML 마크업(단일 페이지 · serve 에서 분리 · 라우트 분리 3차).

앱 스크립트·스타일은 /vendor/app.js·app.css(분리 파일)를 참조한다.
정적 데모는 scripts/make_demo.py 가 이 마크업을 원천으로 재인라인·치환한다.
"""

PAGE = """<!doctype html>
<html lang="ko" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prism</title>
<meta name="description" content="이미지·텍스트·엑셀에서 리드문·엔티티·인텐트·콘텐츠 카테고리를 추출하는 콘텐츠 메타 도구">
<link rel="icon" type="image/svg+xml" href="/vendor/prism-favicon.svg">
<link rel="apple-touch-icon" href="/vendor/prism-icon-180.png">
<link href="/vendor/pretendard.css" rel="stylesheet">
<link href="/vendor/gmarket.css" rel="stylesheet">
<link href="/vendor/ds-theme.css" rel="stylesheet">
<link href="/vendor/ds-components.css" rel="stylesheet">
<script>
  // 전역 호버 툴팁: [data-tip] 위임 · position:fixed 로 overflow/스택 컨텍스트에 안 잘림
  (function () {
    var tip = null;
    function box() {
      if (!tip) { tip = document.createElement('div'); tip.id = 'tipfloat'; document.body.appendChild(tip); }
      return tip;
    }
    function show(el) {
      var t = el.getAttribute('data-tip'); if (!t) return;
      var b = box(); b.textContent = t; b.style.display = 'block';
      var r = el.getBoundingClientRect(), pos = el.getAttribute('data-tip-pos') || 'top';
      var tw = b.offsetWidth, th = b.offsetHeight, x, y;
      if (pos === 'bottom') { x = r.left + r.width / 2 - tw / 2; y = r.bottom + 8; }
      else if (pos === 'left') { x = r.left - tw - 8; y = r.top + r.height / 2 - th / 2; }
      else if (pos === 'right') { x = r.right + 8; y = r.top + r.height / 2 - th / 2; }
      else { x = r.left + r.width / 2 - tw / 2; y = r.top - th - 8; }
      x = Math.max(8, Math.min(x, window.innerWidth - tw - 8));
      y = Math.max(8, Math.min(y, window.innerHeight - th - 8));
      b.style.left = x + 'px'; b.style.top = y + 'px';
    }
    function hide() { if (tip) tip.style.display = 'none'; }
    document.addEventListener('mouseover', function (e) {
      var el = e.target && e.target.closest ? e.target.closest('[data-tip]') : null;
      if (el) show(el); else hide();
    });
    document.addEventListener('scroll', hide, true);
    document.addEventListener('mousedown', hide, true);
  })();
</script>
<style>
  /* 앱 레벨 별칭 · 역사적 변수명(--ds-violet*·--ds-surface2·--ds-solar)을
     Anchor 토큰(ds-theme.css)으로 매핑. 고정 위젯 테두리는 Blue 틴트로 전환. */
  :root{
    --ds-violet: var(--ds-primary);
    --ds-violet-hover: var(--ds-primary-hover);
    --ds-violet-deep: var(--ds-primary-deep);
    --ds-violet-tint: var(--ds-primary-tint);
    --ds-surface2: var(--ds-surface-white);
    --ds-solar: var(--ds-success);
    --ds-hair: var(--ds-hairline);
  }
</style>
<script src="/vendor/app.js"></script>
<script defer src="/vendor/alpine.js"></script>
<link href="/vendor/app.css" rel="stylesheet">
<link href="/vendor/tw.css" rel="stylesheet">
</head>
<body class="antialiased">
<div class="ds-grain" aria-hidden="true"></div>
<div x-data="prismApp()" class="appshell" data-theme="light">

  <!-- ━━━━━ 상단 바: 로고 | 타이틀(고정) | 도구 ━━━━━ -->
  <header class="topbar">
    <div class="topbar__brand" x-on:click="selectMod('home')" style="cursor:pointer" role="button" aria-label="홈으로">
      <img class="topbar__logo topbar__logo--light" src="/vendor/prism-logo-tagline-light.png" alt="Prism · A lens on content & users">
      <img class="topbar__logo topbar__logo--dark" src="/vendor/prism-logo-tagline-dark.png" alt="Prism · A lens on content & users"></div>
    <div class="topbar__title">
      <div class="homehead__title" x-text="modLabel"></div>
      <div class="homehead__sub" x-text="modSub"></div>
    </div>
    <div class="topbar__tools">
      <!-- 사용자 식별(이름·캐릭터)은 사이드바 프로필 카드로 이관. 상단바엔 미표시 -->
      <!-- 연결 신호등: 모델별 표시 없이 단일 신호 dot (초록=연결·회색=미설정/서버관리·주황=MOCK). 상세는 호버 -->
      <button type="button" class="topbar__conn topbar__conn--dot" x-on:click="if (navVisible('sysadmin')) selectMod('system')"
        x-bind:data-tip="cfg.forcedMock ? 'MOCK (강제)' : (connCount ? '연결됨' : (cfg.keyManagedByServer ? '서버 관리 (연결됨)' : '키 미설정'))" data-tip-pos="bottom" aria-label="연결 상태">
        <span class="ds-statusdot" x-bind:class="cfg.forcedMock ? 'ds-statusdot--mock' : ((connCount || (cfg.keyManagedByServer && cfg.hasKey)) ? 'ds-statusdot--ok' : 'ds-statusdot--mock')"><span class="ds-statusdot__dot"></span></span>
      </button>
      <button type="button" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="openReport()" data-tip="전체 리포트 생성·보기" data-tip-pos="bottom" aria-label="전체 리포트"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M6 3h8l4 4v14H6z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M9 12h6M9 16h6M9 8h3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>
      <button type="button" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="toggleTheme()" x-bind:data-tip="theme === 'dark' ? '라이트 모드' : '다크 모드'" data-tip-pos="bottom" aria-label="테마 전환">
        <svg x-show="theme !== 'dark'" width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
        <svg x-show="theme === 'dark'" x-cloak width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.6"/><path d="M12 3v2M12 19v2M5 12H3M21 12h-2M6 6l1.4 1.4M16.6 16.6 18 18M18 6l-1.4 1.4M7.4 16.6 6 18" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
      </button>
      <button type="button" x-show="reviewer" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="logout()" data-tip="로그아웃" data-tip-pos="bottom" aria-label="로그아웃"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M15 12H4m0 0 4-4m-4 4 4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M10 4h8a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
      <div class="addmenu" x-bind:class="addMenuOpen ? 'open' : ''" x-on:click.outside="addMenuOpen = false">
        <template x-for="w in homeCatalog" x-bind:key="w.id">
          <button type="button" x-on:click="addWidget(w.id)" x-bind:disabled="hasWidget(w.id)" x-bind:style="hasWidget(w.id) ? 'opacity:.45;cursor:default' : ''">
            <span x-text="(hasWidget(w.id) ? '✓ ' : '＋ ') + w.label"></span>
          </button>
        </template>
      </div>
    </div>
  </header>

  <!-- 실시간 협업 토스트: 다른 검수자의 검수 활동 -->
  <div class="live-toast" x-show="liveMsg" x-cloak x-transition.opacity>
    <span class="live-toast__dot"></span><span x-text="liveMsg"></span>
  </div>

  <!-- 전역 에러 토스트(조용한 실패 노출 · A-2) -->
  <div class="err-toast" x-show="errMsg" x-cloak x-transition.opacity x-on:click="errMsg=''">
    <span class="err-toast__ic">⚠</span><span x-text="errMsg"></span>
  </div>

  <!-- 검수 완료 점수 상승(+PT) 리워드 토스트 · key 로 연속 검수 시 애니메이션 재시작 -->
  <template x-if="ptToast">
    <div class="pttoast" x-bind:key="ptToast.id">
      <span class="pttoast__spark">✨</span>
      <span class="pttoast__pt" x-text="'+' + ptToast.pts + ' PT'"></span>
      <span class="pttoast__lbl" x-show="ptToast.label" x-text="ptToast.label"></span>
    </div>
  </template>

  <!-- 배지 전체 보기 모달(목록 + 달성 여부·진행도) -->
  <div class="ds-dialog-backdrop" x-show="badgeModalOpen" x-cloak x-transition.opacity x-on:mousedown.self="badgeModalOpen=false" style="z-index:74">
    <div class="badgemodal" x-show="badgeModalOpen" x-transition>
      <div class="badgemodal__hd">
        <div class="badgemodal__ttl">배지 컬렉션</div>
        <template x-if="arenaMe">
          <div class="badgemodal__lvwrap">
            <span class="badgemodal__lv" x-text="'Lv.' + arenaMe.level"></span>
            <span class="badgemodal__count"><b x-text="badgeGot"></b> / <span x-text="badges().length"></span></span>
          </div>
        </template>
        <button type="button" class="ds-iconbtn" x-on:click="badgeModalOpen=false" aria-label="닫기"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
      </div>
      <template x-if="arenaMe">
        <div class="badgemodal__xp">
          <div class="charcard__xpbar"><div class="charcard__xpfill" x-bind:style="'width:' + xpPct(arenaMe) + '%'"></div></div>
          <div class="badgemodal__xptxt">다음 레벨까지 <b x-text="xpToNext(arenaMe) + 'pt'"></b> · 순위 #<span x-text="arenaMyRank"></span></div>
        </div>
      </template>
      <div class="badgemodal__grid">
        <template x-for="(bd, i) in badges()" x-bind:key="i">
          <div class="bmcard" x-bind:class="bd.got ? 'got' : 'locked'" x-bind:style="bd.got ? ('--bc:' + bd.color) : ''">
            <span class="bmcard__cat" x-text="bd.cat"></span>
            <span class="gbadge__orb bmcard__orb"><span class="gbadge__ic" x-text="bd.got ? bd.icon : '🔒'"></span></span>
            <span class="bmcard__label" x-text="bd.label"></span>
            <span class="bmcard__exp" x-text="'+' + bd.exp + ' EXP'"></span>
            <span class="bmcard__desc" x-text="bd.desc"></span>
            <span class="bmcard__foot" x-bind:class="bd.got ? 'is-got' : ''"
                  x-text="bd.got ? '✓ 달성 완료' : (Math.min(bd.cur, bd.target) + ' / ' + bd.target + ' ' + bd.unit)"></span>
          </div>
        </template>
      </div>
    </div>
  </div>

  <!-- 배지 달성 축하 오버레이(성취감) -->
  <div class="badgeburst" x-show="badgeToast" x-cloak x-transition.opacity x-on:click="badgeToast = null">
    <div class="badgeburst__card" x-bind:style="badgeToast ? ('--bc:' + badgeToast.color) : ''">
      <template x-for="i in 12" x-bind:key="i"><span class="badgeburst__spark" x-bind:style="'--i:' + i"></span></template>
      <div class="badgeburst__orb"><span class="badgeburst__ic" x-text="badgeToast && badgeToast.icon"></span></div>
      <div class="badgeburst__ttl">🎉 배지 달성!</div>
      <div class="badgeburst__label" x-text="badgeToast && badgeToast.label"></div>
      <div class="badgeburst__exp" x-text="badgeToast && ('+' + badgeToast.exp + ' EXP')"></div>
      <div class="badgeburst__hint" x-text="badgeToast && badgeToast.desc"></div>
    </div>
  </div>

  <!-- 검수자 등록 온보딩(딤드 + 중앙 모달). 첫 방문 시 자동, 칩 클릭 시 변경 -->
  <div class="onboard" x-show="reviewerEditing" x-cloak x-transition.opacity
       x-on:click.self="if (reviewer) reviewerEditing = false"
       x-on:keydown.escape.window="if (reviewer) reviewerEditing = false">
    <div class="onboard__card" x-transition>
      <div class="onboard__brand">
        <img class="onboard__logo onboard__logo--light" src="/vendor/prism-logo-tagline-light.png" alt="Prism · A lens on content & users">
        <img class="onboard__logo onboard__logo--dark" src="/vendor/prism-logo-tagline-dark.png" alt="Prism · A lens on content & users"></div>
      <h2 class="onboard__title" x-text="authBusy ? (authMode==='signup' ? '가입 중' : '로그인 중') : (authToken ? '검수자 정보 변경' : (authMode==='signup'?'가입하고 시작':'로그인'))"></h2>
      <p class="onboard__lead">팀이 함께 콘텐츠를 검수해 정확도를 끌어올립니다. 내 검수가 점수가 되고 캐릭터가 성장해요. 계정으로 로그인하면 <b>어느 기기에서나</b> 이어집니다.</p>

      <!-- 로그인 진행 애니메이션(정보 변경 화면 플래시 방지) -->
      <div x-show="authBusy" x-cloak class="onboard__busy">
        <span class="onboard__busy-ring"><img x-bind:src="charImg(reviewerChar)" alt=""></span>
        <div class="onboard__busy-dots"><i></i><i></i><i></i></div>
        <p x-text="authMode==='signup' ? '가입을 완료하고 있어요' : '프로필을 불러오고 있어요'"></p>
      </div>
      <!-- ① 로그인/가입 (비로그인) -->
      <div x-show="!authToken && !authBusy">
        <div class="onboard__authtabs">
          <button type="button" x-bind:class="authMode==='login'?'sel':''" x-on:click="authMode='login';authMsg=''">로그인</button>
          <button type="button" x-bind:class="authMode==='signup'?'sel':''" x-on:click="authMode='signup';authMsg=''">가입</button>
        </div>
        <!-- 그룹 A · 계정: 이메일·비밀번호(+확인)·닉네임·캐릭터 -->
        <div class="onboard__group">
          <div class="onboard__grouphd">계정</div>
          <label class="onboard__lbl">이메일</label>
          <input class="field onboard__name" type="email" placeholder="you@team.com" x-model="authEmail" style="margin-bottom:12px">
          <label class="onboard__lbl">비밀번호</label>
          <input class="field onboard__name" type="password" placeholder="••••••••" x-model="authPw" x-on:keydown.enter="saveReviewer()" x-bind:style="authMode==='signup' ? 'margin-bottom:12px' : 'margin-bottom:8px'">
          <label x-show="authMode==='login'" class="chk-inline" style="font-size:12px;color:var(--ds-muted)">
            <input type="checkbox" x-model="saveCred"> 아이디·비밀번호 저장 <span class="onboard__hint" style="margin:0">이 기기에만 저장됩니다</span></label>
          <template x-if="authMode==='signup'">
            <div>
              <label class="onboard__lbl">비밀번호 확인</label>
              <input class="field onboard__name" type="password" placeholder="비밀번호 다시 입력" x-model="authPw2" x-on:keydown.enter="saveReviewer()" style="margin-bottom:12px">
              <label class="onboard__lbl">닉네임 <span class="onboard__hint"> 리더보드·검수에 표시</span></label>
              <input class="field onboard__name" placeholder="예) 김검수" x-model="reviewer" x-on:keydown.enter="saveReviewer()" style="margin-bottom:12px">
              <label class="onboard__lbl">캐릭터 선택</label>
              <div class="onboard__chars" style="margin-bottom:0">
                <template x-for="c in charOptions" x-bind:key="c.id">
                  <button type="button" class="ochar" x-bind:class="reviewerChar===c.id ? 'sel' : ''" x-on:click="reviewerChar=c.id">
                    <span class="ochar__ring"><img x-bind:src="c.img" x-bind:alt="c.label"></span>
                    <b x-text="c.label"></b><small x-text="c.role"></small>
                  </button>
                </template>
              </div>
            </div>
          </template>
        </div>
        <div class="onboard__authmsg" x-show="authMsg" x-text="authMsg"></div>
        <!-- 그룹 B · 팀(가입 시): 코드 참가 또는 팀 없음. 팀 생성은 관리자 메뉴 -->
        <template x-if="authMode==='signup'">
          <div class="onboard__group onboard__group--b">
            <div class="onboard__grouphd" style="display:flex;align-items:center;justify-content:space-between">
              <span>팀 참가</span>
              <label style="display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:700;color:var(--ds-muted);cursor:pointer;text-transform:none;letter-spacing:0">
                <input type="checkbox" x-model="noTeam"> 팀 없음</label>
            </div>
            <input x-show="!noTeam" class="field onboard__name" placeholder="팀 초대 코드 (예: A1B2C3D4)" x-model="inviteCode" style="margin-bottom:6px;text-transform:uppercase;letter-spacing:.08em;font-weight:700" x-on:keydown.enter="saveReviewer()">
            <p class="onboard__hint" style="text-align:left;display:block;margin-bottom:0" x-text="noTeam ? '팀 없이 시작합니다. 나중에 관리자에게 코드를 받아 참가할 수 있어요.' : '관리자에게 받은 코드를 입력하면 같은 팀으로 참가합니다.'"></p>
          </div>
        </template>
      </div>

      <!-- ② 프로필 편집 (로그인 상태) · 닉네임·캐릭터만 -->
      <div x-show="authToken && !authBusy" class="onboard__group">
        <div class="onboard__grouphd">프로필</div>
        <label class="onboard__lbl">닉네임 <span class="onboard__hint"> 리더보드·검수에 표시</span></label>
        <input class="field onboard__name" placeholder="예) 김검수" x-model="reviewer" x-on:keydown.enter="saveReviewer()">
        <label class="onboard__lbl">캐릭터 선택</label>
        <div class="onboard__chars" style="margin-bottom:0">
          <template x-for="c in charOptions" x-bind:key="c.id">
            <button type="button" class="ochar" x-bind:class="reviewerChar===c.id ? 'sel' : ''" x-on:click="reviewerChar=c.id">
              <span class="ochar__ring"><img x-bind:src="c.img" x-bind:alt="c.label"></span>
              <b x-text="c.label"></b><small x-text="c.role"></small>
            </button>
          </template>
        </div>
      </div>

      <button type="button" class="ds-btn ds-btn--primary onboard__cta" x-show="!authBusy"
              x-bind:disabled="authToken ? !(reviewer||'').trim() : (!(authEmail||'').trim() || !authPw || (authMode==='signup' && (!authPw2 || !(reviewer||'').trim() || (!noTeam && !(inviteCode||'').trim()))))"
              x-on:click="saveReviewer()"
              x-text="authToken ? '저장하고 시작' : (authMode==='signup'?'가입하고 시작':'로그인하고 시작')"></button>
      <button type="button" class="onboard__skip" x-show="authToken && !authBusy" x-on:click="reviewerEditing=false">닫기</button>
    </div>
  </div>

  <div class="appbody">
  <!-- ━━━━━ 좌측 컬럼 · 내비 + 도우미 ━━━━━ -->
  <div class="leftcol">
    <aside class="ds-sidebar">
      <!-- 홈 = 그룹 밖 독립 최상단 -->
      <nav class="ds-navgroup" style="margin-bottom:6px;padding-bottom:var(--ds-space-2);border-bottom:1px solid var(--ds-hairline-soft)">
        <button type="button" class="ds-navitem" x-bind:class="mod === 'home' ? 'ds-navitem--active' : ''" x-on:click="selectMod('home')">
          <span class="ds-navitem__icon" x-html="navIcons.home"></span><span>홈</span>
        </button>
      </nav>
      <template x-for="grp in mods" x-bind:key="grp.g">
        <nav class="ds-navgroup" x-show="navVisible(grp.gcond)">
          <div class="ds-navgroup__label" x-text="grp.g"></div>
          <template x-for="it in grp.items" x-bind:key="it.id">
            <button type="button" class="ds-navitem" x-show="navVisible(it.cond)" x-bind:class="mod === it.id ? 'ds-navitem--active' : ''" x-on:click="selectMod(it.id)">
              <span class="ds-navitem__icon" x-html="navIcons[it.ic]"></span>
              <span x-text="it.label"></span>
            </button>
          </template>
        </nav>
      </template>
    </aside>
    <!-- 도우미 = 사이드 위젯 바로 아래(다크 박스 + 캐릭터) -->
    <div class="side-assistant">
      <!-- 사용자 프로필 카드(야구카드형): 내 캐릭터 + 이름. 누르면 '유저명 에이전트' 열림. 미설정 시 프로필 설정 -->
      <button type="button" class="side-profile" x-on:click="reviewer ? (chatOpen = !chatOpen) : (reviewerEditing = true)" x-bind:data-tier="(reviewer && arenaMe) ? levelTier(arenaMe.level) : 0" aria-label="내 프로필·에이전트">
        <span class="side-profile__glow"></span>
        <span class="side-profile__gain" x-show="reviewer && arenaMe && arenaMe.week_points>0" x-text="arenaMe && arenaMe.week_points ? ('+' + arenaMe.week_points + ' XP') : ''"></span>
        <span class="side-profile__avatar">
          <img x-bind:src="charImg(reviewer ? reviewerChar : 'boksil')" alt="">
          <span class="side-profile__lvl" x-show="reviewer && arenaMe" x-text="'Lv.' + (arenaMe ? arenaMe.level : 0)"></span>
        </span>
        <span class="side-profile__name">
          <span class="side-assistant__spin" x-show="loading || modBusy"></span>
          <span x-text="reviewer || '프로필 설정'"></span>
          <span class="side-profile__tag" x-show="reviewer && !(loading || modBusy)">에이전트</span>
        </span>
        <span class="side-profile__score" x-show="reviewer && arenaMe && !(loading || modBusy)"><b x-text="(arenaMe ? arenaMe.points : 0)"></b> pt</span>
        <span class="side-profile__sub" x-show="!(reviewer && arenaMe) || (loading || modBusy)" x-text="(loading || modBusy) ? '처리하고 있어요…' : (reviewer ? '내 에이전트 열기' : '이름·캐릭터를 설정하세요')"></span>
        <span class="side-profile__edit" x-show="reviewer" x-on:click.stop="reviewerEditing = true" role="button" aria-label="프로필 편집" data-tip="편집" data-tip-pos="left"><svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M4 20h4L19 9l-4-4L4 16v4Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg></span>
      </button>
      <!-- 팀 퀘스트 카드(게임형 · RPG 퀘스트 트래커 관례 차용): 유형 태그 + D-day + 목표 + 진행 게이지 + 시한 · 전 메뉴 노출 -->
      <div class="squest" x-show="arenaData && arenaData.next_batch_at" x-cloak x-on:click="selectMod('review')" role="button" tabindex="0"
           x-bind:data-tip="'다음 학습 반영 ' + fmtTs(arenaData?arenaData.next_batch_at:0) + ' · 그전까지의 검수 의견이 ' + ((arenaData&&arenaData.next_model)||'기준 모델') + ' v' + (arenaData?arenaData.next_version:'') + ' 프롬프트에 반영됩니다 · 눌러서 검수하러 가기'" data-tip-pos="right">
        <div class="squest__hd">
          <span class="squest__type">팀 퀘스트</span>
          <span class="squest__dday" x-bind:class="['D-DAY','D-1'].indexOf(ddayTxt(arenaData?arenaData.next_batch_at:0)) >= 0 ? 'is-urgent' : ''" x-text="ddayTxt(arenaData?arenaData.next_batch_at:0)"></span>
        </div>
        <div class="squest__title" x-text="'🏁 v' + (arenaData?arenaData.next_version:'') + ' 버전 마감'"></div>
        <div class="squest__model" x-show="arenaData && arenaData.next_model" x-text="(arenaData?arenaData.next_model:'') + ' 새 버전 학습'"></div>
        <div class="squest__obj" x-show="arenaData && questLeft()" x-text="'목표 · 검수 대상 ' + questTotal() + '건 전량 완주'"></div>
        <div class="squest__obj" x-show="arenaData && !questLeft()">목표 달성 · 반영을 기다리는 중</div>
        <div class="squest__bar"><div class="squest__fill" x-bind:class="arenaData && !questLeft() ? 'is-done' : ''" x-bind:style="'width:' + questPct() + '%'"></div></div>
        <div class="squest__cnt"><b class="tnum" x-text="questAvgLabel() + questDone() + ' / ' + questTotal()"></b><span class="tnum" x-text="questPct() + '%'"></span></div>
        <div class="squest__foot">
          <span>⏳ <span class="tnum" x-text="fmtTs(arenaData?arenaData.next_batch_at:0) + ' 반영'"></span></span>
          <span class="squest__go" x-show="arenaData && questLeft()" x-text="'남은 ' + questLeft() + '건 →'"></span>
          <span class="squest__go" x-show="arenaData && !questLeft()">✓ 완주</span>
        </div>
      </div>
      <!-- 완료 잔상(목표 소진 후 72시간): 다음 퀘스트 생성까지의 공백을 잇는 상태 카드 -->
      <div class="squest squest--done" x-show="questDoneRecent()" x-cloak data-tip="관리자가 검수 목표에서 새 퀘스트를 생성하면 다시 시작됩니다" data-tip-pos="right">
        <div class="squest__hd">
          <span class="squest__type">팀 퀘스트</span>
          <span class="squest__dday squest__dday--done">완료</span>
        </div>
        <div class="squest__title" x-text="'🏆 v' + (arenaData?arenaData.last_version:'') + ' 반영 완료'"></div>
        <div class="squest__obj">새 퀘스트 대기 중</div>
      </div>
    </div>
  </div>

  <!-- ━━━━━ 우측 = 상단 메뉴 위젯 + 캔버스 ━━━━━ -->
  <div class="home">
    <div class="canvas">
      <!-- ═══ 홈: 위젯 캔버스(실동작 위젯만 · 직접 배치) ═══ -->
      <div x-show="false" class="ds-widgetgrid" x-bind:class="editing ? 'ds-widgetgrid--edit' : ''" id="grid">
        <!-- 빈 상태: 배치 도우미(첫 방문) -->
        <div x-show="placed && placed.length === 0" x-cloak class="ds-empty" style="grid-column:1/-1">
          <span class="ds-character ds-character--bob" style="width:84px;height:84px"><img src="/vendor/boksil-catcher.svg" alt=""></span>
          <div class="ds-empty__title">홈을 직접 구성해 보세요</div>
          <div class="ds-empty__desc">필요한 위젯을 골라 나만의 콘솔을 만듭니다 추천 구성으로 빠르게 시작할 수 있어요</div>
          <div style="display:flex;gap:var(--ds-space-2);justify-content:center;margin-top:16px">
            <button type="button" class="ds-btn ds-btn--primary" x-on:click="useRecommended()">추천 구성 배치</button>
            <button type="button" class="ds-btn ds-btn--secondary" x-on:click="addMenuOpen = true">위젯 추가</button>
          </div>
        </div>

        <!-- 런처: 새 추출 → 추출 실행 -->
        <div class="ds-widget ds-widget--function ds-widget--launcher ds-widget--md" data-wid="launch-run" x-show="hasWidget('launch-run')" x-cloak role="button" tabindex="0" x-on:click="!editing && selectMod('run')">
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('launch-run')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <span class="ds-launcher__icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg></span>
          <span class="ds-launcher__body"><span class="ds-launcher__title">새 추출</span><span class="ds-launcher__sub">이미지·텍스트·엑셀 추출</span></span>
          <span class="ds-launcher__chev"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        </div>
        <!-- 런처: 배치 결과 → 대시보드 -->
        <div class="ds-widget ds-widget--function ds-widget--launcher ds-widget--md" data-wid="launch-batch" x-show="hasWidget('launch-batch')" x-cloak role="button" tabindex="0" x-on:click="!editing && selectMod('dash')">
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('launch-batch')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <span class="ds-launcher__icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" stroke="currentColor" stroke-width="1.6"/></svg></span>
          <span class="ds-launcher__body"><span class="ds-launcher__title">배치 결과</span><span class="ds-launcher__sub">집계·유통·분포 보기</span></span>
          <span class="ds-launcher__chev"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        </div>
        <!-- 런처: 사전·정책 → 전용 도구 -->
        <div class="ds-widget ds-widget--function ds-widget--launcher ds-widget--md" data-wid="launch-dict" x-show="hasWidget('launch-dict')" x-cloak role="button" tabindex="0" x-on:click="!editing && selectMod('dict')">
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('launch-dict')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <span class="ds-launcher__icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M4 5h16M4 12h16M4 19h10" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg></span>
          <span class="ds-launcher__body"><span class="ds-launcher__title">사전 · 정책 관리</span><span class="ds-launcher__sub">전용 도구로 이동 ↗</span></span>
          <span class="ds-launcher__chev"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        </div>

        <!-- 핵심 지표 (정보 · md) · /dashboard 집계 -->
        <div class="ds-widget ds-widget--info ds-widget--md" data-wid="metrics" x-show="hasWidget('metrics')" x-cloak>
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('metrics')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <div class="ds-widget__head"><div class="ds-widget__title"><span>핵심 지표</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body"><div class="ds-stat-grid" style="grid-template-columns:repeat(2,1fr)">
            <div class="ds-stat"><div class="ds-stat__value ds-stat__value--accent" x-text="dashData ? dashData.n : 0"></div><div class="ds-stat__label">추출 완료</div></div>
            <div class="ds-stat"><div class="ds-stat__value" x-text="(dashData ? dashData.gPct : 0) + '%'"></div><div class="ds-stat__label">유통 가능 G</div></div>
            <div class="ds-stat"><div class="ds-stat__value" x-text="dashData ? dashData.entities : 0"></div><div class="ds-stat__label">엔티티</div></div>
            <div class="ds-stat"><div class="ds-stat__value" x-text="dashData ? dashData.avgLead : 0"></div><div class="ds-stat__label">평균 리드문</div></div>
          </div></div>
        </div>

        <!-- 품질 점수 (정보 · sm) -->
        <div class="ds-widget ds-widget--info" data-wid="quality" x-show="hasWidget('quality')" x-cloak>
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('quality')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <div class="ds-widget__head"><div class="ds-widget__title"><span>품질 점수</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body center"><div class="ds-ring" x-bind:style="'--ds-ring-size:104px;--ds-ring-pct:' + (dashData ? dashData.gPct : 0)"><span class="ds-ring__label" x-text="(dashData ? dashData.gPct : 0) + '%'"></span></div></div>
        </div>

        <!-- 인텐트 분포 (정보 · md) -->
        <div class="ds-widget ds-widget--info ds-widget--md" data-wid="intents" x-show="hasWidget('intents')" x-cloak>
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('intents')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <div class="ds-widget__head"><div class="ds-widget__title"><span>인텐트 분포</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <template x-for="it in (dashData ? dashData.intents : [])" x-bind:key="it.k">
              <div class="ds-progress" style="margin:6px 0"><div class="ds-progress__head"><span class="ds-progress__label" x-text="it.k"></span><span class="ds-progress__pct" x-text="it.v"></span></div><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="'width:'+Math.max(it.pct,4)+'%'"></div></div></div>
            </template>
            <div x-show="!(dashData && dashData.intents && dashData.intents.length)" class="w-stat-l">추출하면 분포가 표시됩니다</div>
          </div>
        </div>

        <!-- 카테고리 분포 (정보 · md) -->
        <div class="ds-widget ds-widget--info ds-widget--md" data-wid="categories" x-show="hasWidget('categories')" x-cloak>
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('categories')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <div class="ds-widget__head"><div class="ds-widget__title"><span>카테고리 분포</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <template x-for="it in (dashData ? dashData.categories : [])" x-bind:key="it.k">
              <div class="ds-progress" style="margin:6px 0"><div class="ds-progress__head"><span class="ds-progress__label" x-text="it.k"></span><span class="ds-progress__pct" x-text="it.v"></span></div><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="'width:'+Math.max(it.pct,4)+'%'"></div></div></div>
            </template>
            <div x-show="!(dashData && dashData.categories && dashData.categories.length)" class="w-stat-l">추출하면 분포가 표시됩니다</div>
          </div>
        </div>

        <!-- 처리 프로세스 (정보 · tall) · 파이프라인 4단계 -->
        <div class="ds-widget ds-widget--info ds-widget--tall" data-wid="process" x-show="hasWidget('process')" x-cloak>
          <button class="ds-widget__remove" x-on:click.stop="removeWidget('process')" aria-label="제거"><svg width="11" height="11" viewBox="0 0 11 11"><path d="M3 3l5 5M8 3l-5 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>
          <div class="ds-widget__head"><div class="ds-widget__title"><span>처리 프로세스</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <div class="w-agent"><span class="w-agent__av"><img src="/vendor/daesik-batter.svg" alt=""></span><div><div class="w-agent__n">추출</div><div class="w-agent__r">대식 · 이미지→신호</div></div><span class="w-agent__s"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span></span></div>
            <div class="w-agent"><span class="w-agent__av"><img src="/vendor/yonghee-pitcher.svg" alt=""></span><div><div class="w-agent__n">분석</div><div class="w-agent__r">용희 · 메타 분류</div></div><span class="w-agent__s"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span></span></div>
            <div class="w-agent"><span class="w-agent__av"><img src="/vendor/boksil-catcher.svg" alt=""></span><div><div class="w-agent__n">검수</div><div class="w-agent__r">복실 · 품질 확인</div></div><span class="w-agent__s"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span></span></div>
            <div class="w-agent"><span class="w-agent__av"><img src="/vendor/ddakji-manager.svg" alt=""></span><div><div class="w-agent__n">판정 · 부여</div><div class="w-agent__r">딱지 · 유통 결정</div></div><span class="w-agent__s"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span></span></div>
          </div>
        </div>
      </div>

      <!-- ═══ 모듈: 자동 인입(파이프라인 소스 설정) · 관리자 전용 ═══ -->
      <!-- ═══ 모듈: 콘텐츠 관리(관리자) · 원페이지 STEP: 1 추가(수동/자동·용도) → 2 모델 실행 → 3 실행 큐 ═══ -->
      <div x-show="mod === 'content'" x-cloak class="w-full" style="margin-bottom:12px">
        <div class="stepline"><span class="stepline__no">STEP 1</span><b>콘텐츠 추가</b><span class="meta">수동·자동으로 콘텐츠를 모으고 용도를 지정합니다</span>
          <span class="ml-auto" style="display:flex;gap:6px;align-items:center">
            <span class="selctl" data-tip="검수용=검수·정답 축적 대상 · 평가용=검수 목록에서 제외되는 평가 전용 홀드아웃" data-tip-pos="bottom"><span class="selctl__lbl">추가 용도</span><select class="field" x-model="addPurpose"><option value="review">검수용</option><option value="eval">평가용</option></select></span>
            <button type="button" class="srcfilter__chip" x-bind:class="contentTab==='run'?'sel':''" x-on:click="contentTab='run'">수동 추가</button>
            <button type="button" class="srcfilter__chip" x-bind:class="contentTab==='auto'?'sel':''" x-on:click="contentTab='auto'; fetchIngestStatus()">자동 추가</button>
          </span>
        </div>
      </div>
      <div x-show="mod === 'content' && contentTab === 'auto'" x-cloak class="w-full space-y-4">
        <template x-if="!(backend === 'supabase' && adminData && adminData.isAdmin)">
          <ul class="ds-bullets hintbox" style="padding:14px 16px">
            <li>자동 인입은 <b>운영(팀) 관리자</b> 전용입니다.</li>
            <li>로컬 단독 실행에서는 <b>수동 추출</b>을 사용하세요.</li>
          </ul>
        </template>
        <template x-if="backend === 'supabase' && adminData && adminData.isAdmin">
        <div class="w-full space-y-4">
        <ul class="ds-bullets hintbox" style="padding:14px 16px">
          <li>콘텐츠를 <b>자동으로 추가</b>하는 수집 소스를 설정합니다.</li>
          <li>등록·활성화한 소스로 들어온 콘텐츠가 자동으로 추가되어 초안이 생성됩니다.</li>
          <li>일회성 처리는 <b>수동 추가</b>를 사용하세요.</li>
        </ul>
        <!-- 일회성 인입: 크롤러에서 수량 목표로 당겨오기(구 팀 관리 패널 이동) -->
        <section class="panel" data-fn><div class="panel-hd"><b>일회성 가져오기</b><span class="meta">크롤러에서 수량 목표로 당겨와 추가</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:10px"><li>크롤러 엔드포인트에서 <b>수량 목표</b>로 당겨와 추출 → 전건을 팀 <b>검수 대기</b>에 적재합니다.</li><li>실시간 스트리밍 부담 없이 배치로 처리합니다.</li></ul>
            <div style="display:flex;gap:var(--ds-space-2);flex-wrap:wrap;align-items:center">
              <input class="field" style="flex:1;min-width:240px" placeholder="크롤러 엔드포인트 · JSON 배열 반환 GET (예: https://my-crawler/items)" x-model="ingestEndpoint">
              <input class="field" type="number" style="width:96px" min="1" max="200" x-model.number="ingestN" placeholder="수량">
              <button type="button" class="ds-btn ds-btn--primary" x-bind:disabled="ingestBusy" x-on:click="ingestRun()" x-text="ingestBusy ? '가져오는 중…' : '가져오기 실행'"></button>
            </div>
            <span class="text-xs text-muted" style="display:block;margin-top:7px" x-text="ingestMsg"></span>
          </div>
        </section>
        <section class="panel" data-fn><div class="panel-hd"><b>인입 소스 추가</b></div>
          <div class="panel-bd" style="display:flex;flex-direction:column;gap:12px">
            <div style="display:flex;gap:10px;flex-wrap:wrap">
              <div style="flex:1;min-width:160px"><label class="lbl">유형</label>
                <select x-model="newSrc.type" class="field"><option value="api">REST API · 웹훅</option><option value="kafka">Kafka 브로커</option></select></div>
              <div style="flex:1;min-width:160px"><label class="lbl">이름</label><input x-model="newSrc.name" class="field" placeholder="예) 뉴스 수집 API"></div>
            </div>
            <template x-if="newSrc.type === 'api'">
              <div style="display:flex;flex-direction:column;gap:12px">
                <div><label class="lbl">엔드포인트 URL</label><input x-model="newSrc.endpoint" class="field" placeholder="https://… (폴링 GET 또는 웹훅 수신)"></div>
                <div style="display:flex;gap:10px;flex-wrap:wrap">
                  <div style="flex:1;min-width:120px"><label class="lbl">메서드</label><select x-model="newSrc.method" class="field"><option>GET</option><option>POST</option><option>WEBHOOK</option></select></div>
                  <div style="flex:2;min-width:180px"><label class="lbl">인증 헤더 (선택)</label><input x-model="newSrc.auth" class="field" placeholder="Authorization: Bearer …"></div>
                  <div style="flex:1;min-width:90px"><label class="lbl">폴링(초)</label><input x-model="newSrc.interval" class="field" placeholder="60"></div>
                </div>
              </div>
            </template>
            <template x-if="newSrc.type === 'kafka'">
              <div style="display:flex;flex-direction:column;gap:12px">
                <div><label class="lbl">브로커</label><input x-model="newSrc.brokers" class="field" placeholder="broker1:9092,broker2:9092"></div>
                <div style="display:flex;gap:10px;flex-wrap:wrap">
                  <div style="flex:1;min-width:140px"><label class="lbl">토픽</label><input x-model="newSrc.topic" class="field" placeholder="content.ingest"></div>
                  <div style="flex:1;min-width:140px"><label class="lbl">컨슈머 그룹</label><input x-model="newSrc.group" class="field" placeholder="prism-ingest"></div>
                </div>
              </div>
            </template>
            <div style="display:flex;align-items:center;gap:10px"><button type="button" x-on:click="addSource()" class="ds-btn ds-btn--primary">소스 추가</button><span class="text-xs" style="color:var(--ds-muted)" aria-live="polite" x-text="ingestMsg"></span></div>
          </div>
        </section>
        <section class="panel"><div class="panel-hd"><b>등록된 인입 소스</b><span class="meta" x-text="ingestSources.length + '개'"></span></div>
          <div class="panel-bd">
            <ul x-show="ingestSources.length" class="ds-bullets" style="margin-bottom:11px"><li><b>활성</b> 소스는 <b>폴링(초)</b> 주기마다 백그라운드로 자동 인입되고, <b>실행 큐</b>에 진행률이 표시됩니다.</li><li>즉시 한 번만 받으려면 <b>지금 인입</b>, 멈추려면 <b>중지</b>.</li></ul>
            <div x-show="!ingestSources.length" class="ds-empty" style="border:0;padding:16px 4px"><div class="ds-empty__desc">아직 등록된 소스가 없습니다 위에서 API · Kafka 소스를 추가하세요</div></div>
            <template x-for="s in ingestSources" x-bind:key="s.id">
              <div class="drow" style="grid-template-columns:1fr auto;align-items:center;border-bottom:1px solid var(--ds-hairline-soft)">
                <div>
                  <div style="display:flex;align-items:center;gap:var(--ds-space-2);flex-wrap:wrap">
                    <span class="ds-badge" x-bind:class="s.type === 'kafka' ? 'ds-badge--intent' : 'ds-badge--entity'" style="cursor:help" data-tip="자동 인입 소스 유형 · Kafka 스트림 또는 API 폴링" data-tip-pos="top" x-text="s.type === 'kafka' ? 'Kafka' : 'API'"></span>
                    <b class="text-ink" x-text="s.name"></b>
                    <span class="ds-badge" x-bind:class="s.enabled ? 'ds-badge--success' : 'ds-badge--neutral'"><span x-show="s.enabled" class="ds-badge__dot"></span><span x-text="s.enabled ? '활성' : '중지'"></span></span>
                  </div>
                  <div class="text-xs text-muted" style="margin-top:3px" x-text="s.type === 'kafka' ? (s.brokers + ' · ' + s.topic) : (s.method + ' ' + s.endpoint)"></div>
                  <div x-show="srcRunning(s) || ingestRunMsg[s.id] || (srcJob(s) && srcJob(s).last_msg)" class="text-xs" style="margin-top:5px" x-bind:style="(ingestRunMsg[s.id]||'').startsWith('오류') || (srcJob(s) && srcJob(s).last_ok === false) ? 'color:var(--ds-error)' : 'color:var(--ds-primary)'" x-text="srcRunning(s) ? ('인입 중 ' + (srcJob(s) ? (srcJob(s).done + (srcJob(s).total ? ('/' + srcJob(s).total) : '') + '건') : '…')) : (ingestRunMsg[s.id] || (srcJob(s) ? srcJob(s).last_msg : ''))"></div>
                </div>
                <div style="display:flex;gap:6px">
                  <button type="button" class="ds-btn ds-btn--primary" style="height:30px;padding:0 12px" x-show="s.type !== 'kafka'" x-bind:disabled="srcRunning(s)" x-on:click="ingestNow(s)" x-text="srcRunning(s) ? '인입 중…' : '지금 인입'"></button>
                  <button type="button" class="copybtn" x-on:click="toggleSource(s)" x-text="s.enabled ? '중지' : '활성'"></button>
                  <button type="button" class="copybtn" x-on:click="removeSource(s.id)" style="color:var(--ds-error);border-color:rgba(255,78,51,.3)">삭제</button>
                </div>
              </div>
            </template>
          </div>
        </section>
        </div>
        </template>
      </div>

      <!-- ═══ 모듈: 실행 · 추출 ═══ -->
      <div x-show="mod === 'content' && contentTab === 'run'" class="w-full space-y-4">

        <!-- 콘텐츠 추가 = 저장만(미실행 대기) · 초안 생성은 STEP 2 모델 실행이 담당 -->
        <section class="panel" data-fn>
          <div class="panel-hd"><b>콘텐츠 추가</b><span class="meta">추가는 저장만 합니다 · 초안 생성은 STEP 2 모델 실행에서 · 이미지·영상은 실험실 미디어 탭</span></div>
          <div class="panel-bd">
          <!-- 입력 방식 -->
          <div class="seg seg3 mb-5">
            <template x-for="t in tabItems" x-bind:key="t.id">
              <button type="button" x-on:click="selectTab(t.id)" x-bind:class="activeTabId === t.id ? 'on' : ''" x-text="t.label"></button>
            </template>
          </div>
          <!-- 콘텐츠 그룹: 입력 시 지정(이미지·텍스트 공용) 엑셀은 컬럼에서 자동 -->
          <div x-show="activeTabId !== 'excel'" x-cloak class="mb-4">
            <label class="lbl">콘텐츠 그룹</label>
            <select x-model="group" class="field">
              <template x-for="g in groups" x-bind:key="g"><option x-bind:value="g" x-text="g"></option></template>
            </select>
          </div>
          <!-- 이미지 입력은 실험실 · 미디어 탭으로 이관됨(실험실 전담) -->
          <!-- 텍스트 -->
          <div x-show="activeTabId === 'text'" x-cloak class="space-y-4">
            <div><label class="lbl">제목 (title)</label>
              <input x-model="txtTitle" class="field" placeholder="기사 제목"></div>
            <div><label class="lbl">본문 (body)</label>
              <textarea x-model="txtBody" rows="5" class="field" placeholder="본문 내용"></textarea></div>
            <div><label class="lbl">원문 링크 (선택)</label>
              <input x-model="txtUrl" class="field" placeholder="https:// 원문 주소 · 검수 화면에서 원문 페이지를 바로 볼 수 있습니다"></div>
          </div>
          <!-- 엑셀 -->
          <div x-show="activeTabId === 'excel'" x-cloak class="space-y-4">
            <div class="flex items-center justify-between gap-2 rounded-lg border border-black/[0.08] bg-black/[0.02] px-3 py-2.5">
              <div class="text-xs text-muted">컬럼 양식 · <span class="text-body">콘텐츠 그룹 · 제목 · 부제 · 본문 · 원문 링크</span> (제목·본문 필수)</div>
              <span class="inline-flex shrink-0 items-center gap-2">
              <a href="/template.xlsx" download
                class="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-black/[0.12] px-2.5 py-1 text-xs font-medium text-ink transition-colors hover:bg-black/[0.06]">
                <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>
                엑셀 템플릿
              </a>
              <a href="/template.csv" download class="text-xs text-muted hover:text-ink" style="text-decoration:underline" data-tip="같은 양식의 CSV(UTF-8 BOM)" data-tip-pos="top">CSV</a>
              </span>
            </div>
            <div>
              <label class="lbl">엑셀 / CSV (제목·본문 컬럼 자동 매핑)</label>
              <label class="dropzone" x-bind:class="xlsDrag ? 'drag' : ''"
                     x-on:dragover.prevent="xlsDrag = true" x-on:dragleave.prevent="xlsDrag = false"
                     x-on:drop.prevent="onDropExcel($event)">
                <span class="name" x-text="xlsDrag ? '여기에 놓기' : (excelLabel + ' · 끌어다 놓기 가능')"></span>
                <span class="pick">
                  <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>
                  파일 선택
                </span>
                <input type="file" accept=".xlsx,.csv,.tsv,.jsonl,.json" class="sr-only" x-on:change="onExcel($event)">
              </label>
              <p class="mt-1.5 text-xs text-muted">행마다 한 콘텐츠로 일괄 추출합니다 컬럼명이 제목/본문/서비스명과 달라도 자동 추론합니다 (최대 200행)</p>
            </div>
          </div>

          <div class="mt-5 flex items-center gap-3">
            <button type="button" x-on:click="run()" x-bind:disabled="loading"
              class="ds-btn ds-btn--primary ds-btn--s-lg disabled:opacity-50">
              <svg x-show="loading" x-cloak class="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z"/></svg>
              <span x-text="loading ? '추가 중' : '콘텐츠 추가'"></span>
            </button>
            <span aria-live="polite" class="ml-auto text-sm" style="color:var(--ds-error)" x-text="status"></span>
          </div>
          </div>
        </section>


        <!-- 엑셀 배치 결과 + 인포그래픽 -->
        <div x-show="batchResult" x-cloak x-transition.opacity.duration.250ms class="mt-6 space-y-4">
          <!-- 집계 인포그래픽 -->
          <section class="panel">
            <div class="panel-hd"><b>집계</b><span class="meta tnum" x-text="batchResult ? (batchStats.n + '건 분석') : ''"></span>
              <span x-show="batchResult && batchResult.mock" class="inline-flex items-center rounded-md bg-[#ff9429]/15 px-2 py-0.5 text-xs font-semibold text-[#ff9429]">MOCK</span>
              <button type="button" class="copybtn ml-auto" x-on:click="exportBatchCsv()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>
                CSV 내보내기
              </button>
            </div>
            <div class="panel-bd space-y-5">
              <div class="tiles">
                <div class="tile"><div class="n tnum" x-text="batchStats.n"></div><div class="t">총 건수</div></div>
                <div class="tile"><div class="n tnum" x-text="batchStats.g"></div><div class="t">유통가능 G</div></div>
                <div class="tile"><div class="n tnum" x-text="batchStats.ents"></div><div class="t">엔티티 수</div></div>
                <div class="tile"><div class="n tnum" x-text="batchStats.avgLen"></div><div class="t">평균 리드문(자)</div></div>
              </div>
              <div class="flex items-center gap-6">
                <div class="ring" x-bind:style="'background:conic-gradient(var(--ds-primary) ' + batchStats.gPct + '%, var(--ds-hairline-soft) 0)'">
                  <i><span class="pv tnum" x-text="batchStats.gPct + '%'"></span><span class="pl">유통가능</span></i>
                </div>
                <div class="min-w-0 flex-1 space-y-2.5">
                  <div class="lbl" style="margin-bottom:2px">인텐트 분포 (상위 5)</div>
                  <template x-for="it in batchStats.intents" x-bind:key="it.k">
                    <div class="bar">
                      <span class="lab" x-text="it.k"></span>
                      <span class="track"><span class="fill" x-bind:style="'width:' + Math.max(it.pct, 4) + '%'"></span></span>
                      <span class="pc tnum" x-text="it.v + '건'"></span>
                    </div>
                  </template>
                  <div x-show="!batchStats.intents.length" class="text-xs text-muted">인텐트 데이터 없음</div>
                </div>
              </div>
              <p x-show="batchResult && batchResult.mapping" class="text-xs text-muted" style="margin:0" x-text="batchResult && batchResult.mapping ? ('매핑: ' + Object.entries(batchResult.mapping).map(e=>e[0]+'←'+e[1]).join(' · ') + ' · 건별 결과는 아래 추가된 콘텐츠·검수 목록에서 확인합니다') : ''"></p>
            </div>
          </section>
        </div>

        <!-- 단건 처리 이력(마지막 수동 추출의 보정·비용·지연 · 관리자 추적용) -->
        <div x-show="result" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>처리 이력</b><span class="meta" x-text="tr.prompt_version || ''"></span><button type="button" class="copybtn" x-show="tr && tr.content_id !== undefined" x-on:click="exportEval()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>엑셀 다운로드</button></div><div class="panel-bd">
            <div class="drow"><div class="k">보정</div><div class="v flex flex-wrap gap-1.5">
              <template x-for="f in (tr.fallbacks || [])" x-bind:key="f"><span class="ds-badge ds-badge--category" style="cursor:help" data-tip="실행 중 발생한 폴백·재시도 기록" data-tip-pos="top" x-text="f"></span></template>
              <span x-show="!(tr.fallbacks||[]).length" class="text-xs text-muted">없음</span>
            </div></div>
            <div class="drow" x-show="tr.by_call && Object.keys(tr.by_call).length"><div class="k">콜별 비용</div><div class="v flex flex-wrap gap-1.5">
              <template x-for="[k,v] in Object.entries(tr.by_call || {})" x-bind:key="'bc'+k"><span class="ds-badge ds-badge--neutral tnum" style="cursor:help" x-bind:data-tip="'호출 ' + v.n + '회 · 토큰 in ' + v['in'] + ' / out ' + v.out + ' · ' + v.ms + 'ms'" data-tip-pos="top" x-text="k + ' $' + (v.cost||0).toFixed(4)"></span></template>
            </div></div>
            <div class="drow"><div class="k">검증 verdict</div><div class="v flex flex-wrap gap-1.5">
              <template x-for="(v,i) in (tr.agent_verdicts || [])" x-bind:key="i"><span class="ds-badge ds-badge--intent" style="cursor:help" data-tip="에이전트별 판정 근거(트레이스)" data-tip-pos="top" x-text="(typeof v==='string')?v:JSON.stringify(v)"></span></template>
              <span x-show="!(tr.agent_verdicts||[]).length" class="text-xs text-muted">없음</span>
            </div></div>
            <div class="drow"><div class="k">비용 · 토큰</div><div class="v text-sm text-body tnum" x-text="'$' + (tr.cost_usd||0).toFixed(4) + ' · ' + JSON.stringify(tr.tokens||{})"></div></div>
            <div class="drow"><div class="k">지연(ms)</div><div class="v text-sm text-body tnum" x-text="JSON.stringify(tr.latency_ms||{})"></div></div>
          </div></div>
        </div>
      </div>

      <!-- ═══ 모듈: 대시보드 (디자인 시스템: Stat · ProgressRing · ProgressBar) ═══ -->
      <div x-show="mod === 'content'" x-cloak class="w-full" style="margin:18px 0 12px">
        <div class="stepline"><span class="stepline__no">STEP 2</span><b>모델 실행</b><span class="meta">모은 콘텐츠에 모델을 실행해 초안을 만듭니다</span></div>
      </div>
      <div x-show="mod === 'content'" x-cloak class="w-full space-y-4">
        <ul class="ds-bullets hintbox" style="padding:14px 16px">
          <li>추가된 콘텐츠(자동·수동 불문)에 <b>모델을 실행</b>해 검수용 초안을 만듭니다 · 여기는 <b>수동 실행</b>입니다.</li>
          <li>실행 결과는 <b>버전 v(학습 반영 회차+1)</b> 로 기록됩니다 · 현재 다음 실행 버전: <b class="text-ink tnum" x-text="verTxt"></b></li>
        </ul>
        <!-- 사용 모델: 리드문·메타 추출과 단건·일괄 실행의 기본값(제공자 불문 전 모델 노출) -->
        <section class="panel" data-fn><div class="panel-hd"><b>사용 모델</b><span class="meta">기본 실행 모델 · 리드문·메타 추출과 실행의 기본값</span></div>
          <div class="panel-bd">
            <div class="filterbar" style="margin:0">
              <span class="selctl" data-tip="모델을 지정하지 않은 실행에 쓰이는 기본 모델 · 미연결 제공자 모델은 키 등록 후 선택 가능" data-tip-pos="bottom"><span class="selctl__lbl">사용 모델</span>
                <select class="field" style="min-width:260px" x-bind:value="textValue" x-on:change="onTextPick($event.target.value)">
                  <template x-for="g in textGroups" x-bind:key="g.label">
                    <optgroup x-bind:label="g.label + (g.on ? '' : ' (미연결)')">
                      <template x-for="it in g.items" x-bind:key="g.label + it.model">
                        <option x-bind:value="optVal(it.provider, it.model)" x-bind:disabled="!g.on" x-text="it.model || it.label"></option>
                      </template>
                    </optgroup>
                  </template>
                </select></span>
              <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-md" x-bind:disabled="cfgBusy" x-on:click="loadModels()">모델 새로고침</button>
              <button type="button" class="srcfilter__chip" x-bind:class="bulkScope==='pending' ? 'sel' : ''" x-on:click="bulkScope='pending'" x-text="'미실행만 (' + pendingCount + '건)'"></button>
              <button type="button" class="srcfilter__chip" x-bind:class="bulkScope==='all' ? 'sel' : ''" x-bind:disabled="questActive" x-bind:style="questActive ? 'opacity:.45;cursor:not-allowed' : ''" x-on:click="!questActive && (bulkScope='all')" x-bind:data-tip="questActive ? '퀘스트 진행 중에는 전체 재실행이 차단됩니다(검수 중 초안 교체 방지) · 반영 후 가능' : null" data-tip-pos="top" x-text="'전체 재실행 (' + ((dashData&&dashData.contents)||[]).length + '건)'"></button>
              <button type="button" class="ds-btn ds-btn--primary ds-btn--s-md" x-bind:disabled="bulkBusy || (bulkScope==='pending' && !pendingCount)" x-on:click="runBulk()" x-text="bulkBusy ? '실행 중…' : ('실행 (' + (bulkScope==='pending' ? pendingCount : ((dashData&&dashData.contents)||[]).length) + '건)')"></button>
              <span class="text-xs text-muted" x-text="bulkMsg || modelsMsg"></span>
            </div>
            <ul class="ds-bullets" style="margin:10px 0 0"><li><b>미실행만</b> = 추가만 된 콘텐츠(기본) · <b>전체 재실행</b> = 기존 초안을 이력에 남기고 덮어씁니다 · 건당 비용 발생 · 진행은 STEP 3 실행 큐.</li></ul>
          </div>
        </section>
        <!-- 처리 대기 화면: Processing(캐릭터) + Steps (디자인 시스템) -->
        <div x-show="(loading && activeTabId === 'image') || bulkBusy" x-cloak class="ds-pilot mt-6 panel" style="border-color:var(--ds-hairline)">
          <div class="panel-bd">
            <!-- Processing -->
            <div class="ds-processing">
              <span class="ds-processing__char"><span class="ds-character ds-character--bob" style="width:96px;height:96px"><img src="/vendor/yonghee-pitcher.svg" alt="용희 (투수)"></span></span>
              <div>
                <div class="ds-processing__title" x-text="bulkBusy ? '일괄 실행 중' : '메타데이터 추출 중'"></div>
                <div class="ds-processing__msg"><span class="ds-processing__dots" x-text="bulkBusy ? '콘텐츠마다 초안을 만들고 있어요 · 진행률은 STEP 3 실행 큐' : '이미지를 읽고 있어요'"></span></div>
              </div>
              <div style="width:100%;max-width:340px">
                <div class="ds-progress ds-progress--indeterminate"><div class="ds-progress__track" role="progressbar"><div class="ds-progress__fill ds-progress__fill--primary"></div></div></div>
              </div>
            </div>
            <!-- Steps -->
            <div class="ds-steps mt-5" style="max-width:420px">
              <div class="ds-step ds-step--done"><div class="ds-step__rail"><span class="ds-step__marker"><svg class="ds-step__check" viewBox="0 0 12 12" fill="none"><path d="M2.5 6.2 5 8.5 9.5 3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span class="ds-step__line"></span></div><div class="ds-step__body"><div class="ds-step__title">입력 수집</div><div class="ds-step__detail" x-text="bulkBusy ? '대상 콘텐츠 선별' : '콘텐츠 정규화'"></div></div></div>
              <div class="ds-step" x-bind:class="(!bulkBusy && activeTabId === 'image') ? 'ds-step--active' : 'ds-step--done'"><div class="ds-step__rail"><span class="ds-step__marker"></span><span class="ds-step__line"></span></div><div class="ds-step__body"><div class="ds-step__title">이미지 이해</div><div class="ds-step__detail" x-text="(!bulkBusy && activeTabId === 'image') ? '시각 모델로 읽는 중' : '텍스트는 건너뜀'"></div></div></div>
              <div class="ds-step ds-step--active"><div class="ds-step__rail"><span class="ds-step__marker"></span><span class="ds-step__line"></span></div><div class="ds-step__body"><div class="ds-step__title">메타 추출</div><div class="ds-step__detail">리드문 · 엔티티 · 인텐트 · 카테고리</div></div></div>
              <div class="ds-step ds-step--pending"><div class="ds-step__rail"><span class="ds-step__marker"></span><span class="ds-step__line"></span></div><div class="ds-step__body"><div class="ds-step__title">품질 판정</div><div class="ds-step__detail">G / R</div></div></div>
              <div class="ds-step ds-step--pending"><div class="ds-step__rail"><span class="ds-step__marker"></span></div><div class="ds-step__body"><div class="ds-step__title">완료</div></div></div>
            </div>
          </div>
        </div>


      </div>
      <!-- ═══ 모듈: 콘텐츠 검수(멤버) · 탭: 검수 대상 콘텐츠(기본) | 결과 비교 ═══ -->
      <div x-show="mod === 'create'" x-cloak class="w-full" style="margin-bottom:10px"><div class="evaltabs">
        <button type="button" x-bind:class="createTab==='raw'?'sel':''" x-on:click="createTab='raw'; loadRaw()">검수 대상 콘텐츠</button>
        <button type="button" x-bind:class="createTab==='edit'?'sel':''" x-on:click="createTab='edit'; loadModelStats(); loadRaw()">결과 비교</button>
      </div></div>
      <div x-show="mod === 'create' && createTab === 'edit'" x-cloak class="w-full">
        <div class="space-y-4">
          <!-- 요소 단위 모델별 결과 현황: 같은 정보요소를 모델 축으로 비교 -->
          <!-- A/B 선택: 비교할 모델과 버전 지정(별도 패널 · abslot 디자인) -->
          <section class="panel" data-fn><div class="panel-hd"><b>A/B 선택</b><span class="meta">비교할 모델과 버전을 A·B 슬롯에 지정</span>
            <button type="button" class="ds-iconbtn ds-iconbtn--bordered ml-auto" x-on:click="loadModelStats()" data-tip="새로고침" data-tip-pos="bottom" aria-label="모델·버전 목록 새로고침"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-.9 4.5M20 5v6h-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          </div>
            <div class="panel-bd">
              <div class="filterbar" style="margin:0 16px">
                <span class="selctl selctl--a abslot"><span class="selctl__tag">A</span>
                  <span class="selctl__lbl">모델</span>
                  <select class="field" x-model="abAm" x-on:change="abAv=String((msVers(abAm)[0]||''))"><template x-for="m in msModels" x-bind:key="'am'+m"><option x-bind:value="m" x-text="m"></option></template></select>
                  <span class="selctl__lbl">버전</span>
                  <select class="field" x-model="abAv"><template x-for="v in msVers(abAm)" x-bind:key="'av'+v"><option x-bind:value="String(v)" x-text="'v' + v"></option></template></select>
                </span>
                <span class="abvs">VS</span>
                <span class="selctl selctl--b abslot"><span class="selctl__tag">B</span>
                  <span class="selctl__lbl">모델</span>
                  <select class="field" x-model="abBm" x-on:change="abBv=String((msVers(abBm)[0]||''))"><template x-for="m in msModels" x-bind:key="'bm'+m"><option x-bind:value="m" x-text="m"></option></template></select>
                  <span class="selctl__lbl">버전</span>
                  <select class="field" x-model="abBv"><template x-for="v in msVers(abBm)" x-bind:key="'bv'+v"><option x-bind:value="String(v)" x-text="'v' + v"></option></template></select>
                </span>
              </div>
            </div>
          </section>
          <!-- 비교 결과: 요소별 현황 표 + 콘텐츠별 비교 표(상단 A/B 기준) -->
          <section class="panel" data-fn><div class="panel-hd"><b>비교 결과</b><span class="meta">상단 A/B 기준 · 요소별 현황과 콘텐츠별 비교</span></div>
            <div class="panel-bd">
              <div class="subhd" style="margin-top:4px">요소별 현황 <span class="meta">막대 = 유통 가능 비율 · ▲ = 우세</span></div>
              <template x-if="abCols.length">
                <div class="overflow-auto"><table class="ds-table"><thead><tr><th style="width:130px">항목</th>
                  <template x-for="(m,mi) in abCols" x-bind:key="'h'+mi"><th><span class="selctl__tag" x-bind:style="mi ? 'background:#ff6a3d' : 'background:var(--ds-violet,#1e84ff)'" x-text="mi ? 'B' : 'A'"></span> <span x-text="m.key"></span></th></template>
                </tr></thead><tbody>
                  <tr><td class="text-ink">유통 가능 G</td><template x-for="(m,mi) in abCols" x-bind:key="'g'+mi"><td>
                    <span class="abbar" x-bind:class="mi ? 'abbar--b' : ''">
                      <span class="abbar__track"><span class="abbar__fill" x-bind:style="'width:' + Math.max(m.gPct, 3) + '%'"></span></span>
                      <b class="tnum" x-text="m.gPct + '%'"></b><span class="abwin" x-show="abWin('gPct', mi)">▲</span>
                    </span></td></template></tr>
                  <tr><td class="text-ink">처리 건수</td><template x-for="(m,mi) in abCols" x-bind:key="'n'+mi"><td><b class="tnum" x-text="m.n"></b> <span class="abwin" x-show="abWin('n', mi)">▲</span></td></template></tr>
                  <tr><td class="text-ink">평균 리드문(자)</td><template x-for="(m,mi) in abCols" x-bind:key="'l'+mi"><td class="tnum" x-text="m.avgLead"></td></template></tr>
                  <tr><td class="text-ink">인텐트 상위</td><template x-for="(m,mi) in abCols" x-bind:key="'i'+mi"><td><template x-for="t in (m.intents||[])" x-bind:key="'it'+mi+t"><span class="ds-badge ds-badge--intent" style="margin:1px;cursor:help" x-bind:data-tip="termDef('intent', t)" data-tip-pos="top" x-text="t"></span></template><span x-show="!(m.intents||[]).length" class="text-xs text-muted">·</span></td></template></tr>
                  <tr><td class="text-ink">카테고리 상위</td><template x-for="(m,mi) in abCols" x-bind:key="'c'+mi"><td><template x-for="t in (m.categories||[])" x-bind:key="'ct'+mi+t"><span class="ds-badge ds-badge--category" style="margin:1px;cursor:help" x-bind:data-tip="termDef('category', t)" data-tip-pos="top" x-text="catKo(t)"></span></template><span x-show="!(m.categories||[]).length" class="text-xs text-muted">·</span></td></template></tr>
                  <tr><td class="text-ink">품질 사유 상위</td><template x-for="(m,mi) in abCols" x-bind:key="'r'+mi"><td><template x-for="t in (m.reasons||[])" x-bind:key="'rt'+mi+t"><span class="ds-badge ds-badge--reason" style="margin:1px;cursor:help" x-bind:data-tip="termDef('reason', t)" data-tip-pos="top" x-text="reasonBoth(t)"></span></template><span x-show="!(m.reasons||[]).length" class="text-xs text-muted">·</span></td></template></tr>
                </tbody></table></div>
              </template>
              <div x-show="!abCols.length" class="text-xs text-muted" style="margin:0 16px 10px">모델·버전 결과가 쌓이면 A/B 비교가 표시됩니다 · <b class="text-ink">콘텐츠 관리 · 모델 실행</b>으로 초안을 만들어 보세요</div>
              <div class="subhd">콘텐츠별 비교 <span class="meta">클릭 = 팝업에서 A/B 초안 나란히</span></div>
              <div class="overflow-auto" style="max-height:420px"><table class="ds-table"><thead><tr><th style="width:52px">등급</th><th>콘텐츠</th><th style="width:100px">서비스</th><th style="width:150px">현재 모델</th><th style="width:56px">버전</th></tr></thead><tbody>
                <template x-for="r in ((rawData||{}).items||[])" x-bind:key="'cmp'+r.hash">
                  <tr style="cursor:pointer" role="button" tabindex="0" x-on:click="openCmpModal(r)" x-on:keydown.enter="openCmpModal(r)" data-tip="초안 비교 팝업 열기" data-tip-pos="top">
                    <td><span class="ds-badge" x-bind:class="r.grade==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-text="r.grade||'·'"></span></td>
                    <td class="text-ink" x-text="r.title || '(제목 없음)'"></td>
                    <td class="text-muted" x-text="r.service"></td>
                    <td class="text-xs text-muted tnum" x-text="r.model || '·'"></td>
                    <td class="text-xs text-muted tnum" x-text="r.version != null ? ('v' + r.version) : '·'"></td>
                  </tr>
                </template>
              </tbody></table>
              <div x-show="!((rawData||{}).items||[]).length" class="text-xs text-muted" style="padding:10px">데이터가 없습니다 · <b class="text-ink">콘텐츠 관리</b>에서 콘텐츠를 추가하세요</div>
              </div>
            </div>
          </section>
        </div>
      </div>

      <!-- 콘텐츠별 초안 비교 팝업 -->
      <div class="ds-dialog-backdrop" x-show="cmpModalOpen" x-cloak x-transition.opacity x-on:mousedown.self="cmpModalOpen=false" style="z-index:74">
        <div class="badgemodal" x-show="cmpModalOpen" x-transition style="max-width:860px">
          <div class="panel-hd" style="padding:0 0 12px;margin-bottom:var(--ds-space-3);border-bottom:1px solid var(--ds-hairline,rgba(0,0,0,.08))"><b>모델별 콘텐츠 상세 비교</b><span class="meta" x-text="cmpTitle"></span>
            <button type="button" class="ds-iconbtn ml-auto" x-on:click="cmpModalOpen=false" aria-label="닫기"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
          </div>
          <!-- 상단(모델·버전별 결과 현황)에서 설정한 A/B 를 그대로 상속(공통 콘텐츠 대상) -->
          <div style="display:flex;gap:var(--ds-space-2);flex-wrap:wrap;align-items:center;margin-bottom:10px" x-show="draftsData && draftsData.items.length">
            <span class="selctl selctl--a"><span class="selctl__tag">A</span>
              <span class="text-xs text-ink" style="padding:0 4px" x-text="abAm ? (abAm + ' · v' + abAv) : '미설정'"></span>
              <span class="ds-badge ds-badge--warning" x-show="cmpAmiss" data-tip="이 콘텐츠에는 A 슬롯 초안이 없어 가장 가까운 초안을 표시합니다" data-tip-pos="top">초안 없음</span>
            </span>
            <span class="text-xs text-muted">vs</span>
            <span class="selctl selctl--b"><span class="selctl__tag">B</span>
              <span class="text-xs text-ink" style="padding:0 4px" x-text="abBm ? (abBm + ' · v' + abBv) : '미설정'"></span>
              <span class="ds-badge ds-badge--warning" x-show="cmpBmiss" data-tip="이 콘텐츠에는 B 슬롯 초안이 없어 가장 가까운 초안을 표시합니다" data-tip-pos="top">초안 없음</span>
            </span>
          </div>
          <template x-if="draftPair">
            <div class="overflow-auto" style="max-height:60vh"><table class="ds-table"><thead><tr><th style="width:100px">필드</th><th><span class="selctl__tag" style="background:var(--ds-violet,#1e84ff)">A</span> <span x-text="draftPair.l.label"></span></th><th><span class="selctl__tag" style="background:#ff6a3d">B</span> <span x-text="draftPair.r.label"></span></th></tr></thead><tbody>
              <template x-for="f in draftDiff" x-bind:key="'MF'+f.k">
                <tr x-bind:class="f.diff ? 'is-sel' : ''">
                  <td class="text-ink"><span x-text="f.k"></span> <span class="ds-badge ds-badge--error" x-show="f.diff" style="margin-left:4px">다름</span></td>
                  <td class="text-xs" x-text="f.l"></td>
                  <td class="text-xs" x-text="f.r"></td>
                </tr>
              </template>
            </tbody></table></div>
          </template>
          <div x-show="draftsData && draftsData.items.length < 2" class="text-xs text-muted" style="margin-top:8px">비교할 초안이 하나뿐입니다 · <b class="text-ink">콘텐츠 관리 · 다른 모델로 재실행</b>으로 다른 모델 초안을 만들어 보세요</div>
        </div>
      </div>

      <!-- ═══ 모듈: 품질 메타 ═══ -->
      <!-- ═══ 모듈: 실험실(관리자) · 지금 테스트하지 않는 탐구 요소 ═══ -->
      <div x-show="mod === 'lab'" x-cloak class="w-full" style="margin-bottom:10px">
        <div class="evaltabs">
          <button type="button" x-bind:class="labTab==='legal'?'sel':''" x-on:click="labTab='legal'">법령</button>
          <button type="button" x-bind:class="labTab==='user'?'sel':''" x-on:click="labTab='user'; loadUser()">사용자</button>
          <button type="button" x-bind:class="labTab==='media'?'sel':''" x-on:click="labTab='media'">미디어</button>
        </div>
        <ul class="ds-bullets hintbox" style="padding:var(--ds-space-3) var(--ds-space-4);margin-top:10px"><li>지금 테스트 대상이 아닌 <b>탐구 요소</b>를 모아둔 공간입니다 · 테스트 대상으로 확정되면 본 메뉴로 승격합니다.</li></ul>
      </div>
      <div x-show="mod === 'lab' && labTab === 'legal'" x-cloak class="w-full space-y-4">
        <div class="panel"><div class="panel-bd flex items-center justify-between gap-3">
          <ul class="ds-bullets"><li>법령 1차 필터(13종 위반 라우팅·스코어링)를 추출에 포함합니다.</li><li>켜면 다음 추출부터 적용됩니다(추가 호출).</li></ul>
          <label class="inline-flex cursor-pointer items-center gap-2 text-[13px] text-body">
            <input type="checkbox" x-model="legalEnabled" x-on:change="toggleLegal()" class="h-4 w-4 rounded border-black/15 bg-canvas text-violet">
            법령 필터 포함
          </label>
        </div></div>
        <div x-show="!result" class="empty"><b class="text-body">실행 · 추출</b>에서 단건 추출을 실행하면 그 콘텐츠의 품질·법령 판정 상세가 여기에 표시됩니다</div>
        <div x-show="result" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>유통 판정</b>
            <span x-show="qm.finalGrade === 'G'" class="ds-badge ds-badge--success"><span class="ds-badge__dot"></span>유통 가능 · G</span>
            <span x-show="qm.finalGrade === 'R'" class="ds-badge ds-badge--error"><span class="ds-badge__dot"></span>차단 · R</span>
            <span x-show="!qm.finalGrade" class="ds-badge ds-badge--reason" style="cursor:help" data-tip="품질 호출이 실패해 판정을 보류했습니다 · 재실행하면 다시 판정합니다" data-tip-pos="top"><span class="ds-badge__dot"></span>판정 보류 · 재실행 필요</span>
          </div><div class="panel-bd">
            <div class="drow"><div class="k">검수</div><div class="v text-sm text-body" x-text="(qm.review || 'auto') + (qm.confidence != null ? (' · conf ' + qm.confidence) : '')"></div></div>
            <div class="drow"><div class="k">품질 사유</div><div class="v flex flex-wrap gap-1.5">
              <template x-for="r in (qm.reasons || [])" x-bind:key="r"><span class="ds-badge ds-badge--reason" style="cursor:help" x-bind:data-tip="termDef('reason', r)" data-tip-pos="top" x-text="reasonBoth(r)"></span></template>
              <span x-show="!(qm.reasons || []).length" class="text-xs text-muted">없음(통과)</span>
            </div></div>
            <div class="drow"><div class="k">법령</div><div class="v">
              <span class="text-sm text-body" x-text="lm.enabled ? ('대표등급 ' + lm.representative_grade + ' · ' + lm.representative_score) : '법령 필터 비활성(옵션)'"></span>
              <div class="mt-1.5 flex flex-wrap gap-1.5"><template x-for="h in (lm.harm_types || [])" x-bind:key="h.code"><span class="ds-badge ds-badge--intent" style="cursor:help" x-bind:data-tip="'유해 유형 코드 ' + h.code + ' · 판정 등급 ' + h.grade" data-tip-pos="top" x-text="h.code + ' · ' + h.grade"></span></template></div>
            </div></div>
          </div></div>
        </div>
      </div>

      <!-- ═══ 모듈: 토픽 스튜디오 (클러스터링 체계를 자연어로 설계·실험) ═══ -->
      <!-- ═══ 모듈: 스튜디오 · 설계 도구 묶음(프롬프트 + 토픽 · 실험실에서 승격) ═══ -->
      <div x-show="mod === 'studio'" x-cloak class="w-full" style="margin-bottom:10px">
        <div class="evaltabs">
          <button type="button" x-bind:class="studioTab==='prompt'?'sel':''" x-on:click="studioTab='prompt'">프롬프트</button>
          <button type="button" x-bind:class="studioTab==='topic'?'sel':''" x-on:click="studioTab='topic'; loadTopics()">토픽</button>
        </div>
      </div>
      <div x-show="mod === 'studio' && studioTab === 'topic'" x-cloak class="w-full space-y-4">
        <ul class="ds-bullets hintbox" style="padding:var(--ds-space-3) var(--ds-space-4)">
          <li><b>토픽 스튜디오</b>는 클러스터링 체계(어떤 콘텐츠를 어떻게 묶을지)를 <b>자연어</b>로 정의하고 즉시 실험하는 공간입니다.</li>
          <li>원하는 묶음을 문장으로 설명하면 조건값이 자동으로 채워집니다 · 채워진 조건값은 직접 켜고 끌 수 있습니다.</li>
          <li>품질 통과(<b>G</b>) 콘텐츠만 토픽 대상이 됩니다<span x-show="qexN()"> · 현재 <b class="tnum" x-text="qexN()"></b>건이 품질 미달로 자동 제외</span> · 어울리지 않는 콘텐츠는 토픽을 열어 개별 <b>제외</b>할 수 있습니다.</li>
        </ul>
        <template x-if="!topicData || !topicData.n_contents"><div class="empty">아직 토픽을 만들 결과가 없습니다 <b class="text-body">실행 · 추출</b>에서 여러 건(엑셀 일괄)을 추출하세요</div></template>
        <div x-show="topicData && topicData.n_contents" class="space-y-4">
          <div class="tiles" style="grid-template-columns:repeat(4,1fr)">
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.custom||0):0"></div><div class="t">내 토픽</div></div>
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.single||0):0"></div><div class="t">엔티티형(자동)</div></div>
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.composite||0):0"></div><div class="t">사건형(자동)</div></div>
            <div class="tile"><div class="n tnum" x-text="topicData?(topicData.summary.filter||0):0"></div><div class="t">조건형(기본)</div></div>
          </div>

          <!-- 토픽 생성하기: 4단계 스텝(이름 → 자연어 → 조건 → 생성) -->
          <div class="panel">
            <!-- 타이틀은 정적 텍스트 유지(헤드 데코레이터가 b.textContent 를 복사하므로 x-text 는 빈 타이틀이 된다) -->
            <div class="panel-hd"><b>토픽 생성하기</b><span class="meta">이름 → 설명 → 조건 → 생성 · 4단계로 묶음을 정의합니다</span>
              <span class="ds-badge ds-badge--status" x-show="studio.editId" x-cloak>수정 중</span>
              <span class="meta tnum" style="margin-left:auto" x-text="tStepDone()+' / 4 단계'"></span>
              <span class="tprog"><i x-bind:style="'width:'+(tStepDone()/4*100)+'%'"></i></span>
            </div>
            <div class="panel-bd">
              <div class="tstepper">

                <!-- STEP 1 · 이름 -->
                <div class="tstep" x-bind:class="{'is-done': !!studio.name.trim(), 'is-active': tActive()===1}">
                  <div class="tstep__rail"><span class="tstep__dot"><span class="n">STEP 1</span><span class="c">✓</span></span></div>
                  <div class="tstep__body">
                    <div class="tstep__head"><b>이름 짓기</b><span class="tstep__guide">이 묶음을 뭐라고 부를까요?</span></div>
                    <input class="field" x-model="studio.name" placeholder="예) 경제 심층분석 큐레이션">
                  </div>
                </div>

                <!-- STEP 2 · 자연어 -->
                <div class="tstep" x-bind:class="{'is-done': !!studio.prompt.trim(), 'is-active': tActive()===2}">
                  <div class="tstep__rail"><span class="tstep__dot"><span class="n">STEP 2</span><span class="c">✓</span></span></div>
                  <div class="tstep__body">
                    <div class="tstep__head"><b>말로 설명하기</b><span class="tstep__guide">원하는 묶음을 문장으로 · ‘조건값 자동생성’을 누르면 아래 조건이 채워집니다</span></div>
                    <textarea class="field" rows="2" style="resize:vertical" x-model="studio.prompt" x-on:input="schedulePreview()" placeholder="예) 경제·산업 심층분석만 모으고 속보는 빼줘"></textarea>
                    <div class="tairow">
                      <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-on:click="studioSuggest()" x-bind:disabled="studioSuggesting || !studio.prompt.trim()" data-tip="설명을 읽고 아래 조건값을 자동으로 만듭니다" data-tip-pos="top" x-text="studioSuggesting ? '생성 중…' : '✨ 조건값 자동생성'"></button>
                      <span class="tmodel">
                        <span class="tmodel__lbl">모델</span>
                        <select class="field tmodel__sel" x-model="studioModel" data-tip="조건값 생성에 쓸 모델 · 직접(Solar)·라우터 선택 가능" data-tip-pos="top">
                          <option value="">기본 실행 모델</option>
                          <template x-for="m in studioModelList" x-bind:key="m.key"><option x-bind:value="m.value" x-bind:disabled="m.header" x-text="m.text"></option></template>
                        </select>
                        <button type="button" class="ds-iconbtn ds-iconbtn--bordered ds-iconbtn--sm tmodel__rf" x-bind:class="modelsBusy?'is-spin':''" x-on:click="refreshStudioModels()" x-bind:disabled="modelsBusy" data-tip="모델 목록 새로고침 (라우터 포함)" data-tip-pos="top" aria-label="모델 목록 새로고침">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6"/></svg>
                        </button>
                      </span>
                    </div>
                    <div class="text-xs" style="color:var(--ds-muted);margin-top:7px" aria-live="polite" x-text="studioMsg || modelsMsgStudio"></div>
                  </div>
                </div>

                <!-- STEP 3 · 조건 -->
                <div class="tstep" x-bind:class="{'is-done': !!(studio.cats.length||studio.intents.length||studio.keywords.length||studio.eattrs.length), 'is-active': tActive()===3}">
                  <div class="tstep__rail"><span class="tstep__dot"><span class="n">STEP 3</span><span class="c">✓</span></span></div>
                  <div class="tstep__body">
                    <div class="tstep__head"><b>조건 확인·조정</b><span class="tstep__guide">필수는 모든 묶음의 뼈대(반드시), 선택은 각각이 관련 묶음이 됩니다 · 칩을 누를수록 후보→선택→필수</span></div>
                    <div class="tfilter"><span class="tfilter__badge">✨ 묶음</span><span class="tfilter__text" x-html="bundleSummaryText()"></span></div>
                    <div class="tlegend"><span class="sw sw--off"></span>회색=후보 · <span class="sw sw--sel"></span>색=선택(관련 묶음) · <span class="sw sw--req"></span><b>★필수</b>=반드시(모든 묶음 공통) · <span class="sw sw--neg"></span><b>✖제외</b>=걸리면 탈락(모든 묶음) · <span class="tauto">✨</span>=자동 선택 · <span style="color:var(--ds-muted)">칩을 누를수록 후보→선택→필수→제외→후보</span></div>
                    <div class="tcond__g">
                      <div class="tcond__lbl">카테고리 <span x-text="'· 선택 '+studio.cats.length+(studio.req.cats.length?(' (필수 '+studio.req.cats.length+')'):'')"></span></div>
                      <div class="flex flex-wrap gap-1.5">
                        <template x-for="c in studioCatChips()" x-bind:key="'cat'+c.k"><span class="ds-badge" style="cursor:pointer" x-bind:class="chipCls('cats',c.k,'ds-badge--category')" x-on:click="studioCycle('cats',c.k)" data-tip="누를수록 후보→선택→필수→제외"><span x-show="studioState('cats',c.k)==='req'" class="treq">★필수</span><span x-show="studioState('cats',c.k)==='neg'" class="tneg">✖제외</span><span x-show="isAuto('cats',c.k)" class="tauto">✨</span><span x-text="catBoth(c.k)+' ('+c.v+')'"></span></span></template>
                        <span x-show="!studioCatChips().length" class="text-xs text-muted">데이터에 카테고리가 없습니다</span>
                      </div>
                    </div>
                    <div class="tcond__g">
                      <div class="tcond__lbl">인텐트 <span x-text="'· 선택 '+studio.intents.length+(studio.req.intents.length?(' (필수 '+studio.req.intents.length+')'):'')"></span></div>
                      <div class="flex flex-wrap gap-1.5">
                        <template x-for="c in studioIntentChips()" x-bind:key="'int'+c.k"><span class="ds-badge" style="cursor:pointer" x-bind:class="chipCls('intents',c.k,'ds-badge--intent')" x-on:click="studioCycle('intents',c.k)" data-tip="누를수록 후보→선택→필수→제외"><span x-show="studioState('intents',c.k)==='req'" class="treq">★필수</span><span x-show="studioState('intents',c.k)==='neg'" class="tneg">✖제외</span><span x-show="isAuto('intents',c.k)" class="tauto">✨</span><span x-text="c.k+' ('+c.v+')'"></span></span></template>
                        <span x-show="!studioIntentChips().length" class="text-xs text-muted">데이터에 인텐트가 없습니다</span>
                      </div>
                    </div>
                    <div class="tcond__g">
                      <div class="tcond__lbl">엔티티 키워드 <span x-text="'· 선택 '+studio.keywords.length+(studio.req.keywords.length?(' (필수 '+studio.req.keywords.length+')'):'')"></span></div>
                      <div style="display:flex;gap:8px">
                        <input class="field" style="flex:1" x-model="studio.kwInput" x-on:keydown.enter.prevent="studioAddKw()" placeholder="예) 삼성 (엔터로 추가)">
                        <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-sm" x-on:click="studioAddKw()">추가</button>
                      </div>
                      <div class="flex flex-wrap gap-1.5" style="margin-top:6px">
                        <template x-for="k in studioKwChips()" x-bind:key="'kw'+k"><span class="ds-badge ds-badge--entity" style="cursor:pointer" x-bind:class="chipCls('keywords',k,'ds-badge--entity')" x-on:click="studioCycle('keywords',k)" data-tip="누를수록 선택→필수→제외→제거"><span x-show="studioState('keywords',k)==='req'" class="treq">★필수</span><span x-show="studioState('keywords',k)==='neg'" class="tneg">✖제외</span><span x-show="isAuto('keywords',k)" class="tauto">✨</span><span x-text="k"></span></span></template>
                        <template x-for="k in topKw()" x-bind:key="'kwc'+k.k"><span class="ds-badge ds-badge--neutral" style="cursor:pointer" x-on:click="studioCycle('keywords',k.k)" data-tip="추가" x-text="'+ '+k.k"></span></template>
                      </div>
                    </div>
                    <!-- 엔티티 속성 조건: 개체 사전(타입·성별·직업·국적·소속) 축 · 같은 개체 AND · 항상 필수 -->
                    <div class="tcond__g">
                      <div class="tcond__lbl">엔티티 속성 <span x-text="'· 선택 '+studio.eattrs.length"></span><span class="meta" style="cursor:help" data-tip="개체 사전의 속성으로 매칭 · 조건 전부를 '한 개체'가 만족해야 합니다 (예: 성별=여성 ∧ 직업=스포츠인 → 여성 스포츠인) · 항상 필수 취급" data-tip-pos="top">개체 사전 기반</span></div>
                      <div style="display:flex;gap:8px">
                        <select class="field" style="width:auto" x-model="studio.eaKey">
                          <option value="type">타입</option><option value="gender">성별</option><option value="occupation">직업</option>
                          <option value="nationality">국적</option><option value="affiliation">소속</option><option value="country">국가</option>
                        </select>
                        <input class="field" style="flex:1" x-model="studio.eaVal" x-on:keydown.enter.prevent="studioAddEattr()" placeholder="예) 여성 · 스포츠인 · 대한민국 (엔터로 추가)">
                        <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-sm" x-on:click="studioAddEattr()">추가</button>
                      </div>
                      <div class="flex flex-wrap gap-1.5" style="margin-top:6px">
                        <template x-for="s in studio.eattrs" x-bind:key="'ea'+s"><span class="ds-badge ds-badge--category" style="cursor:pointer" x-on:click="studioDelEattr(s)" data-tip="클릭해 제거"><span class="treq">★필수</span><span x-text="eattrLabel(s)"></span></span></template>
                        <template x-for="c in eattrCandidates()" x-bind:key="'eac'+c.k"><span class="ds-badge ds-badge--neutral" style="cursor:pointer" x-on:click="studioAddEattr(c.k)" data-tip="추가" x-text="'+ '+c.label+' ('+c.v+')'"></span></template>
                        <span x-show="!studio.eattrs.length && !eattrCandidates().length" class="text-xs text-muted">엔티티 사전에 속성이 아직 없습니다 · 사전 · 정책의 엔티티 탭에서 색인·보강하세요</span>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- STEP 4 · 생성 -->
                <div class="tstep" x-bind:class="{'is-active': tActive()===4}">
                  <div class="tstep__rail"><span class="tstep__dot"><span class="n">STEP 4</span><span class="c">✓</span></span></div>
                  <div class="tstep__body">
                    <div class="tstep__head"><b>확인하고 생성</b><span class="tstep__guide">이 토픽이 아래 묶음들로 펼쳐집니다 · 저장 후 묶음마다 개별로 드릴다운돼요<span x-show="studioBusy"> · 계산 중…</span></span></div>
                    <div class="tpreview">
                      <template x-for="b in (studioPreview.bundles||[])" x-bind:key="'pv'+b.cluster_id">
                        <div class="tbundle" x-bind:class="b.kind">
                          <span class="tbundle__k" x-text="b.kind==='core'?'핵심':'관련'"></span>
                          <span class="tbundle__l" x-text="bundleLabel(b)"></span>
                          <span class="tbundle__n tnum" x-text="b.count+'건'"></span>
                        </div>
                      </template>
                      <div x-show="!(studioPreview.bundles||[]).length" class="text-xs text-muted">조건값 없음 = 전체 <span x-text="(studioPreview.n_total||topicData.n_contents)"></span>건이 한 묶음</div>
                      <div class="flex flex-wrap gap-1.5" style="margin-top:9px" x-show="coreSamples().length">
                        <template x-for="s in coreSamples()" x-bind:key="'sm'+s.title"><span class="ds-badge" x-bind:class="s.grade==='G'?'ds-badge--success':'ds-badge--neutral'" x-text="s.title"></span></template>
                      </div>
                    </div>
                    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px">
                      <button type="button" class="ds-btn ds-btn--primary" x-on:click="studioSave()" x-bind:disabled="!studio.name.trim() || studioSaving" x-text="studioSaving?'생성 중…':(studio.editId?'수정 저장':'토픽 생성')"></button>
                      <button type="button" class="ds-btn ds-btn--outline" x-on:click="studioReset()" x-show="studio.editId || studio.name || studio.cats.length || studio.intents.length || studio.keywords.length">초기화</button>
                    </div>
                  </div>
                </div>

              </div>
            </div>
          </div>

          <!-- 내 토픽: 각 토픽이 여러 묶음(핵심+관련)으로 펼쳐짐 -->
          <div class="panel"><div class="panel-hd"><b>내 토픽</b><span class="meta tnum" x-text="((topicData.custom||[]).length)+'개 · 묶음 '+(topicData.summary&&topicData.summary.custom_bundles||0)"></span></div>
            <div class="panel-bd space-y-3">
              <template x-if="!(topicData.custom||[]).length"><div class="text-sm text-muted">아직 만든 토픽이 없습니다 · 위에서 첫 토픽을 정의해 저장하세요.</div></template>
              <template x-for="g in (topicData.custom||[])" x-bind:key="g.id">
                <div class="tgroup">
                  <div class="tgroup__hd">
                    <b x-text="g.name"></b>
                    <span class="meta text-xs" x-show="g.prompt" x-text="'· '+g.prompt"></span>
                    <span style="margin-left:auto;white-space:nowrap"><button type="button" class="copybtn" x-on:click="studioEdit(g)">수정</button> <button type="button" class="copybtn" x-on:click="studioDelete(g)">삭제</button></span>
                  </div>
                  <div class="tgroup__must" x-show="g.must && g.must.length" x-text="'필수: '+g.must.map(m=>m.dim==='cats'?catBoth(m.v):m.v).join(' · ')"></div>
                  <div class="tgroup__must" x-show="g.neg && g.neg.length" style="color:var(--ds-error)" x-text="'제외: '+(g.neg||[]).map(m=>m.dim==='cats'?catBoth(m.v):m.v).join(' · ')"></div>
                  <div class="flex flex-wrap gap-1.5" style="margin-top:6px">
                    <template x-for="b in (g.bundles||[])" x-bind:key="b.cluster_id">
                      <span class="tbchip" x-bind:class="b.kind" style="cursor:pointer" role="button" tabindex="0" x-on:click="topicDrill(b)" x-on:keydown.enter="topicDrill(b)" data-tip="묶인 콘텐츠 보기" data-tip-pos="top"><span class="tbchip__k" x-text="b.kind==='core'?'핵심':'관련'"></span> <span x-text="bundleLabel(b)"></span> · <b x-text="b.count"></b><span class="text-xs" x-show="b.excluded_n" x-text="' (제외 '+b.excluded_n+')'" data-tip="운영자가 이 토픽에서 개별 제외한 콘텐츠 수"></span></span>
                    </template>
                  </div>
                </div>
              </template>
            </div>
          </div>

          <!-- 자동으로 묶기 설정: 쉬운 말 프리셋 -->
          <div class="panel"><div class="panel-hd"><b>자동으로 묶기 설정</b><span class="meta">엔티티형·사건형 자동 토픽이 얼마나 촘촘하게 묶일지</span></div>
            <div class="panel-bd space-y-4">
              <div>
                <label class="lbl">여러 기사를 ‘같은 사건’으로 묶는 기준</label>
                <div class="flex flex-wrap gap-1.5" style="margin-top:6px">
                  <span class="ds-badge" style="cursor:pointer" x-bind:class="settingsDraft.co_min<=1?'ds-badge--entity':'ds-badge--neutral'" x-on:click="settingsDraft.co_min=1">넓게 묶기</span>
                  <span class="ds-badge" style="cursor:pointer" x-bind:class="settingsDraft.co_min==2?'ds-badge--entity':'ds-badge--neutral'" x-on:click="settingsDraft.co_min=2">보통</span>
                  <span class="ds-badge" style="cursor:pointer" x-bind:class="settingsDraft.co_min>=3?'ds-badge--entity':'ds-badge--neutral'" x-on:click="settingsDraft.co_min=3">좁게 묶기</span>
                </div>
                <div class="text-xs text-muted" style="margin-top:4px" x-text="eventHint()"></div>
              </div>
              <div>
                <label class="lbl">인물·브랜드 하나를 토픽으로 만들 최소 기사 수</label>
                <div class="flex flex-wrap gap-1.5" style="margin-top:6px">
                  <span class="ds-badge" style="cursor:pointer" x-bind:class="settingsDraft.entity_min<=1?'ds-badge--entity':'ds-badge--neutral'" x-on:click="settingsDraft.entity_min=1">1건이라도</span>
                  <span class="ds-badge" style="cursor:pointer" x-bind:class="settingsDraft.entity_min==2?'ds-badge--entity':'ds-badge--neutral'" x-on:click="settingsDraft.entity_min=2">2건 이상</span>
                  <span class="ds-badge" style="cursor:pointer" x-bind:class="settingsDraft.entity_min>=3?'ds-badge--entity':'ds-badge--neutral'" x-on:click="settingsDraft.entity_min=3">3건 이상</span>
                </div>
                <div class="text-xs text-muted" style="margin-top:4px" x-text="entityHint()"></div>
              </div>
              <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <button type="button" class="ds-btn ds-btn--primary" x-on:click="saveTopicSettings()" x-bind:disabled="settingsSaving" x-text="settingsSaving?'적용 중…':'적용'"></button>
                <span class="text-xs" style="color:var(--ds-muted)" aria-live="polite" x-text="settingsMsg"></span>
              </div>
            </div>
          </div>

          <!-- 자동 생성 토픽 (참조) -->
          <div class="panel"><div class="panel-hd"><b>자동 생성 토픽</b><span class="meta tnum" x-text="topicData ? (topicData.n_contents + '건 기준') : ''"></span><button type="button" class="copybtn" x-on:click="exportTopics()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>엑셀 다운로드</button></div>
            <div class="overflow-auto"><table class="ds-table"><thead><tr><th>유형</th><th>클러스터</th><th>대표 엔티티</th><th>멤버</th></tr></thead><tbody>
              <template x-for="t in (topicData?topicData.single:[])" x-bind:key="t.cluster_id"><tr style="cursor:pointer" role="button" tabindex="0" x-on:click="topicDrill(t)" x-on:keydown.enter="topicDrill(t)" data-tip="묶인 콘텐츠 보기" data-tip-pos="left"><td>엔티티형</td><td class="text-ink" x-text="t.cluster_id"></td><td x-text="(t.entities||t.rep_entities||[]).join(' · ')"></td><td x-text="(t.n_contents || t.count || (t.content_ids?t.content_ids.length:0)) + (t.excluded_n?(' · 제외 '+t.excluded_n):'')"></td></tr></template>
              <template x-for="t in (topicData?topicData.composite:[])" x-bind:key="t.cluster_id"><tr style="cursor:pointer" role="button" tabindex="0" x-on:click="topicDrill(t)" x-on:keydown.enter="topicDrill(t)" data-tip="묶인 콘텐츠 보기" data-tip-pos="left"><td>사건형</td><td class="text-ink" x-text="t.cluster_id"></td><td x-text="(t.rep_entities||t.entities||[]).join(' · ')"></td><td x-text="(t.n_contents || t.count || (t.content_ids?t.content_ids.length:0)) + (t.excluded_n?(' · 제외 '+t.excluded_n):'')"></td></tr></template>
              <template x-if="!(topicData&&(topicData.single.length||topicData.composite.length))"><tr><td colspan="4" class="text-muted">엔티티 공유 클러스터 없음(데이터가 많을수록 · 임계값을 낮추면 더 형성)</td></tr></template>
            </tbody></table></div>
          </div>
          <div class="panel"><div class="panel-hd"><b>조건형 토픽 (기본 제공)</b><span class="meta">운영 기본 필터 · 관심사 × 소비 방식</span></div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="t in (topicData?topicData.filter:[])" x-bind:key="t.cluster_id"><span class="ds-badge ds-badge--neutral" style="cursor:pointer" role="button" tabindex="0" x-bind:class="t.active ? 'ds-badge--entity' : 'ds-badge--category'" x-on:click="topicDrill(t)" x-on:keydown.enter="topicDrill(t)" data-tip="묶인 콘텐츠 보기" x-text="(t.name||t.label) + (t.active?(' · '+(t.n_contents||t.count||'')):'')"></span></template>
          </div></div>

          <!-- 토픽에서 제외한 콘텐츠: 큐레이션 오버레이 관리(자동·사용자 토픽 공통) -->
          <div class="panel" x-show="exclusionRows().length">
            <div class="panel-hd"><b>토픽에서 제외한 콘텐츠</b><span class="meta tnum" x-text="exclusionRows().length+'건'"></span><span class="meta">매칭 조건은 그대로 · 개별 편집 판단으로 뺀 목록 · 복구하면 다시 묶입니다</span></div>
            <div class="overflow-auto"><table class="ds-table"><thead><tr><th>토픽</th><th>콘텐츠</th><th></th></tr></thead><tbody>
              <template x-for="e in exclusionRows()" x-bind:key="e.tid+e.h">
                <tr><td x-text="e.topic || e.tid"></td><td class="text-ink" x-text="e.title || e.h"></td>
                <td style="text-align:right"><button type="button" class="copybtn" x-show="topicAdmin" x-on:click="topicRestore(e.tid, e.h)">복구</button></td></tr>
              </template>
            </tbody></table></div>
          </div>
        </div>
      </div>

      <!-- ═══ 모듈: 사전 · 매핑 ═══ -->
      <!-- 사전 · 정책: 탭형 · 사전(인텐트·카테고리 · 추출이 참조하는 어휘) / 정책(품질·법령·처리 · 판정 기준) -->
      <div x-show="mod === 'dict'" x-cloak class="w-full" style="margin-bottom:10px">
        <div class="evaltabs">
          <button type="button" x-bind:class="dictTab==='intent'?'sel':''" x-on:click="dictTab='intent'">인텐트</button>
          <button type="button" x-bind:class="dictTab==='category'?'sel':''" x-on:click="dictTab='category'">카테고리</button>
          <button type="button" x-bind:class="dictTab==='entity'?'sel':''" x-on:click="dictTab='entity'; loadEntdict()">엔티티</button>
          <button type="button" x-bind:class="dictTab==='policy'?'sel':''" x-on:click="dictTab='policy'">정책</button>
        </div>
      </div>
      <div x-show="mod === 'dict'" x-cloak class="w-full space-y-4">
        <!-- 공통 안내·편집 초기화는 편집형 사전 탭에만 · 엔티티 탭은 자체 헤더(자동 등재·보강) 사용 -->
        <div class="panel" x-show="dictTab !== 'entity'"><div class="panel-bd flex items-center justify-between gap-3">
          <ul class="ds-bullets">
            <li>각 체계의 정책(사전·카테고리·법령)을 <b>직접 수정</b>할 수 있습니다.</li>
            <li>저장 시 즉시 추출에 반영되고 로컬에 영속됩니다.</li>
          </ul>
          <button type="button" x-on:click="resetDict()" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-sm">편집 초기화</button>
        </div></div>

        <!-- 편집은 팝업(편집 다이얼로그)에서 · 화면 하단 정의 -->

        <div x-show="dictData && dictTab === 'intent'" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>인텐트 · 범용 소비 방식(8)</b>
            <button type="button" class="copybtn" x-on:click="startEdit('intent_universal', null, dictData.intentUniversal, 'list', '인텐트 범용')">편집</button>
          </div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="i in (dictData?dictData.intentUniversal:[])" x-bind:key="i"><span class="ds-badge ds-badge--intent" style="cursor:help" x-bind:data-tip="termDef('intent', i)" data-tip-pos="top" x-text="i"></span></template>
          </div></div>
          <div class="panel"><div class="panel-hd"><b>인텐트 · 범용 형식·전달(8)</b><span class="meta">소비 방식과 교차 부여 가능 · 합산 0~2개 권장</span></div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="i in ((dictData&&dictData.intentForm)||[])" x-bind:key="'if'+i"><span class="ds-badge ds-badge--intent" style="cursor:help" x-bind:data-tip="termDef('intent', i)" data-tip-pos="top" x-text="i"></span></template>
          </div></div>
          <div class="panel"><div class="panel-hd"><b>인텐트 · 서비스별</b>
            <select x-model="dictGroup" class="field" style="width:auto;height:32px;padding:0 28px 0 10px">
              <template x-for="g in (dictData?dictData.serviceGroups:[])" x-bind:key="g"><option x-bind:value="g" x-text="g"></option></template>
            </select>
            <button type="button" class="copybtn" x-on:click="startEdit('intent_by_service', dictGroup, (dictData.intentByService[dictGroup]||[]), 'list', '인텐트 · ' + dictGroup)">편집</button>
          </div><div class="panel-bd flex flex-wrap gap-1.5">
            <template x-for="i in (dictData && dictData.intentByService[dictGroup] ? dictData.intentByService[dictGroup] : [])" x-bind:key="i"><span class="ds-badge ds-badge--intent" style="cursor:help" x-bind:data-tip="termDef('intent', i)" data-tip-pos="top" x-text="i"></span></template>
            <span x-show="!(dictData && dictData.intentByService[dictGroup] && dictData.intentByService[dictGroup].length)" class="text-xs text-muted">항목 없음</span>
          </div></div>
        </div>
        <div x-show="dictData && dictTab === 'category'" class="space-y-4">
          <div class="panel"><div class="panel-hd"><b>콘텐츠 카테고리 · Tier1 / Tier2</b><span class="meta" style="cursor:help" data-tip="IAB Tech Lab 은 어떤 언어로도 공식 번역을 배포하지 않습니다 · 한글은 자사 표시 기준" data-tip-pos="top">공식 표기는 영문(IAB Content Taxonomy 기반) · 한글은 화면 표시용 병기</span>
            <button type="button" class="copybtn ml-auto" x-on:click="startEdit('iab_tier1', null, dictData.iabTier1, 'list', 'Tier1 목록')">Tier1 편집</button>
          </div>
            <div class="overflow-auto" style="max-height:340px"><table class="ds-table"><thead><tr><th style="width:210px">Tier1</th><th>Tier2</th><th style="width:52px" class="tnum" x-text="dictData ? (dictData.iabTier1.length) : ''"></th></tr></thead><tbody>
              <template x-for="c in (dictData?dictData.iabTier1:[])" x-bind:key="c">
                <tr>
                  <td class="text-ink" style="font-weight:600;vertical-align:top" x-text="catBoth(c)"></td>
                  <td><div class="flex flex-wrap gap-1.5">
                    <template x-for="t2 in (dictData && dictData.tier2[c] ? dictData.tier2[c] : [])" x-bind:key="t2"><span class="ds-badge ds-badge--category" style="cursor:help" x-bind:data-tip="termDef('category', t2)" data-tip-pos="top" x-text="catBoth(t2)"></span></template>
                    <span x-show="!(dictData && dictData.tier2[c] && dictData.tier2[c].length)" class="text-xs text-muted">항목 없음</span>
                  </div></td>
                  <td style="vertical-align:top"><button type="button" class="copybtn" x-on:click="startEdit('tier2', c, (dictData.tier2[c]||[]), 'list', 'Tier2 · ' + c)">편집</button></td>
                </tr>
              </template>
            </tbody></table></div>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div class="panel"><div class="panel-hd"><b>도메인 그룹</b><span class="meta">Tier1 7묶음</span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th style="width:120px">그룹</th><th>포함 Tier1</th><th style="width:52px"></th></tr></thead><tbody>
                <template x-for="(ts,g) in (dictData?dictData.domainGroups:{})" x-bind:key="g">
                  <tr><td style="vertical-align:top"><span class="ds-badge ds-badge--entity" style="cursor:help" x-bind:data-tip="termDef('entity', g)" data-tip-pos="top" x-text="g"></span></td>
                    <td class="text-muted" x-text="ts.join(' · ')"></td>
                    <td style="vertical-align:top"><button type="button" class="copybtn" x-on:click="startEdit('domain_groups', g, ts, 'list', '도메인 그룹 · ' + g)">편집</button></td></tr>
                </template>
              </tbody></table></div></div>
            <div class="panel"><div class="panel-hd"><b>자사 ↔ IAB v3.0 매핑</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.iabMap).length+'건':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th>자사 경로</th><th>IAB 공식</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.iabMap:{})" x-bind:key="k"><tr><td class="text-ink" x-text="k"></td><td><div class="tbox" x-text="v"></div></td>
                  <td><button type="button" class="copybtn" x-on:click="startEdit('category_iab_map', k, v, 'text', '매핑 · ' + k)">편집</button></td></tr></template>
              </tbody></table></div></div>
          </div>
        </div>
        <div x-show="dictData && dictTab === 'policy'" class="space-y-4">
          <div class="grid grid-cols-2 gap-4">
            <div class="panel"><div class="panel-hd"><b>품질 메타</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.qualityMetas).length+'종':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th>ID</th><th>메타명 · 정의</th><th>적용</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.qualityMetas:{})" x-bind:key="k"><tr>
                  <td class="text-ink" x-text="k"></td>
                  <td><div class="tbox"><span class="nm" x-text="(dictData.qualityNames&&dictData.qualityNames[k])||''"></span><span x-text="v"></span></div></td>
                  <td><span class="ds-badge ds-badge--neutral" x-bind:class="(dictData.qualityApplies&&dictData.qualityApplies[k]==='ugc')?'ds-badge--intent':'ds-badge--category'" style="cursor:help" data-tip="적용 범위 · UGC=이용자 생성 콘텐츠에만, 전체=모든 서비스에 적용" data-tip-pos="top" x-text="(dictData.qualityApplies&&dictData.qualityApplies[k]==='ugc')?'UGC':'전체'"></span></td>
                  <td><button type="button" class="copybtn" x-on:click="startEdit('quality_metas', k, v, 'text', '품질 · ' + k)">편집</button></td>
                </tr></template>
              </tbody></table></div></div>
            <div class="panel"><div class="panel-hd"><b>법령 위반 유형</b><span class="meta tnum" x-text="dictData?Object.keys(dictData.legalTypes).length+'종':''"></span></div>
              <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th>코드</th><th>유형</th><th>근거</th><th></th></tr></thead><tbody>
                <template x-for="(v,k) in (dictData?dictData.legalTypes:{})" x-bind:key="k"><tr><td class="text-ink" x-text="k"></td><td><div class="tbox" x-text="v.label"></div></td><td class="text-muted" x-text="v.article"></td><td><button type="button" class="copybtn" x-on:click="startEditLegal(k, v)">편집</button></td></tr></template>
              </tbody></table></div></div>
          </div>
        </div>
      </div>

      <!-- ═══ 모듈: 엔티티 사전 · 개체 고유키·타입(NER 6종)·속성 관리 (사전·정책 · 엔티티 탭) ═══ -->
      <div x-show="mod === 'dict' && dictTab === 'entity'" x-cloak class="w-full space-y-4">
        <div class="panel"><div class="panel-bd flex items-center justify-between gap-3 flex-wrap">
          <ul class="ds-bullets">
            <li>콘텐츠 적재 시 엔티티가 <b>개체 사전에 자동 등재</b>됩니다 · 개체당 고유키 1개, 타입·속성은 등록 시 1회 부여(재판정 없음).</li>
            <li>타입·속성(성별·국적·직업·소속 등)은 <b>위키데이터로 자동 보강</b>하고, 사람이 <b>직접 수정(확정)</b>할 수 있습니다 · 확정 필드는 재보강이 덮어쓰지 않습니다.</li>
            <li>속성은 토픽 스튜디오의 <b>엔티티 속성 조건</b>이 됩니다 · 예) 성별=여성 ∧ 직업=스포츠인 → "여성 스포츠인" 토픽.</li>
          </ul>
          <div class="flex items-center gap-2">
            <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-sm" x-on:click="entBackfill()" data-tip="적재된 콘텐츠의 엔티티를 사전에 색인(기존 데이터 소급)" data-tip-pos="top">기존 콘텐츠 색인</button>
            <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-on:click="entEnrichAll()" data-tip="위키데이터 미조회 개체를 백그라운드로 일괄 보강" data-tip-pos="top">미조회 일괄 보강</button>
          </div>
        </div></div>

        <div class="panel" x-show="entData"><div class="panel-bd"><div class="ds-stat-grid" style="grid-template-columns:repeat(5,1fr)">
          <div class="ds-stat"><div class="ds-stat__value ds-stat__value--accent tnum" x-text="(entData && entData.stats.total) || 0"></div><div class="ds-stat__label">총 개체</div></div>
          <div class="ds-stat"><div class="ds-stat__value tnum" x-text="entData ? ((entData.stats.total || 0) - (((entData.stats.byType || {})['(보류)']) || 0)) : 0"></div><div class="ds-stat__label">타입 부여</div></div>
          <div class="ds-stat"><div class="ds-stat__value tnum" x-text="(entData && entData.stats.pending) || 0"></div><div class="ds-stat__label">보류(타입 미부여)</div></div>
          <div class="ds-stat"><div class="ds-stat__value tnum" x-text="(entData && entData.stats.enriched) || 0"></div><div class="ds-stat__label">위키데이터 매칭</div></div>
          <div class="ds-stat"><div class="ds-stat__value tnum" x-text="(entData && entData.stats.links) || 0"></div><div class="ds-stat__label">콘텐츠 링크</div></div>
        </div></div></div>

        <div class="panel">
          <div class="panel-hd"><b>개체 목록</b>
            <span class="meta" aria-live="polite" x-text="entMsg"></span>
          </div>
          <div class="panel-bd">
            <div class="flex items-center gap-2 flex-wrap" style="margin-bottom:10px">
              <input type="text" class="field" style="width:220px" placeholder="이름·별칭 검색" x-model="entQ" x-on:keydown.enter="loadEntdict()">
              <select class="field" style="width:auto" x-model="entType" x-on:change="loadEntdict()">
                <option value="">타입 전체</option>
                <template x-for="(ko,t) in (entData ? entData.meta.types : {})" x-bind:key="t"><option x-bind:value="t" x-text="ko + ' (' + t + ')'"></option></template>
              </select>
              <select class="field" style="width:auto" x-model="entStatus" x-on:change="loadEntdict()">
                <option value="">상태 전체</option><option value="active">확정(active)</option><option value="pending">보류(pending)</option>
              </select>
              <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-sm" x-on:click="loadEntdict()">검색</button>
              <span style="flex:1"></span>
              <input type="text" class="field" style="width:180px" placeholder="수동 등재 · 개체 이름" x-model="entAddName" x-on:keydown.enter="entAdd()">
              <button type="button" class="ds-btn ds-btn--outline ds-btn--s-sm" x-on:click="entAdd()">등재</button>
            </div>
            <div class="overflow-auto" style="max-height:560px"><table class="ds-table"><thead><tr>
              <th style="width:160px">개체</th><th style="width:120px">타입</th><th>속성</th>
              <th style="width:100px">위키데이터</th><th style="width:64px" class="tnum">콘텐츠</th><th style="width:170px"></th>
            </tr></thead><tbody>
              <template x-for="e in ((entData && entData.items) || [])" x-bind:key="e.entity_id">
                <tr>
                  <td class="text-ink" style="font-weight:600" x-text="e.name"></td>
                  <td><span class="ds-badge" x-bind:class="e.type ? 'ds-badge--entity' : 'ds-badge--neutral'" x-text="entTypeLabel(e.type)"></span></td>
                  <td class="text-muted" x-text="entAttrSummary(e) || '—'"></td>
                  <td><a x-show="e.external_ids && e.external_ids.wikidata" x-bind:href="'https://www.wikidata.org/wiki/' + (e.external_ids && e.external_ids.wikidata)" target="_blank" rel="noopener" class="text-violet" x-text="e.external_ids && e.external_ids.wikidata"></a><span x-show="!(e.external_ids && e.external_ids.wikidata)" class="text-xs text-muted" x-text="(e.attr_meta && e.attr_meta._enrich && e.attr_meta._enrich.result === 'miss') ? '미등재' : '미조회'"></span></td>
                  <td class="tnum" x-text="e.n_contents || 0"></td>
                  <td><div class="flex items-center gap-1.5">
                    <button type="button" class="copybtn" x-on:click="openEntEdit(e)">편집</button>
                    <button type="button" class="copybtn" x-on:click="entEnrich(e)" data-tip="위키데이터 조회로 타입·속성 채우기" data-tip-pos="top">보강</button>
                    <button type="button" class="copybtn" style="color:var(--ds-danger)" x-on:click="entDelete(e)">삭제</button>
                  </div></td>
                </tr>
              </template>
              <tr x-show="!((entData && entData.items) || []).length"><td colspan="6" class="text-xs text-muted" style="text-align:center;padding:24px">등재된 개체가 없습니다 · 콘텐츠를 적재하거나 "기존 콘텐츠 색인"을 실행하세요</td></tr>
            </tbody></table></div>
          </div>
        </div>
      </div>

      <!-- ═══ 모듈: 사용자 메타 · 프로필 + 행동 로그 → 페르소나 능동 생성 ═══ -->
      <div x-show="mod === 'lab' && labTab === 'user'" x-cloak class="w-full space-y-4">
        <div class="panel"><div class="panel-bd">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <ul class="ds-bullets">
              <li>사용자 정보(프로필)와 행동 로그(TIARA형)가 <b>모두 모이면</b> 사용자마다 페르소나를 <b>자동 생성</b>합니다 · 별도 실행 버튼 없음.</li>
              <li>행동 로그는 추출 콘텐츠와 조인해 <b>소비 형태 · 강도 · 선호</b>를 산출 · <code class="text-violet">content_id</code> = 추출 순서(0부터).</li>
              <li>실명 · 연락처 등 식별 정보는 입력하지 마세요 · 프로필은 페르소나 생성 시 외부 AI로 전달됩니다.</li>
            </ul>
            <div class="flex items-center gap-2 flex-wrap" x-show="userData">
              <span class="ds-badge ds-badge--category tnum" x-text="'프로필 ' + ((userData && userData.profiles_n) || 0) + '명'"></span>
              <span class="ds-badge ds-badge--intent tnum" x-text="'로그 사용자 ' + ((userData && userData.users) ? userData.users.length : 0) + '명'"></span>
              <span class="ds-badge ds-badge--success tnum"><span class="ds-badge__dot"></span><span x-text="'생성 페르소나 ' + ((userData && userData.generated_n) || 0) + '개'"></span></span>
              <span x-show="userData && userData.aggregate && userData.aggregate.persona_coherence && userData.aggregate.persona_coherence.agree_rate !== null" class="ds-badge ds-badge--neutral tnum" style="cursor:help" data-tip="배정 정합성 · 소비 프로필이 가장 비슷한 이웃과 페르소나가 일치하는 비율 (낮으면 배정 기준 점검 필요)" data-tip-pos="top" x-text="'배정 정합성 ' + Math.round(((userData && userData.aggregate && userData.aggregate.persona_coherence && userData.aggregate.persona_coherence.agree_rate) || 0) * 100) + '%'"></span>
            </div>
          </div>
        </div></div>

        <!-- 입력: 프로필(신규) + 행동 로그(기존) 2열 · 두 재료가 모이는 순간 서버가 생성 -->
        <div class="grid grid-cols-2 gap-4">
          <div class="panel"><div class="panel-hd"><b>사용자 정보 (프로필)</b><span class="meta">저장 즉시 재료 확인 · 모이면 자동 생성</span></div>
            <div class="panel-bd">
              <div class="flex items-center gap-2" style="margin-bottom:10px">
                <a href="/usermeta-profile-template.csv" download class="ds-btn ds-btn--secondary ds-btn--s-sm">템플릿</a>
                <label class="ds-btn ds-btn--secondary ds-btn--s-sm" style="cursor:pointer">CSV 업로드
                  <input type="file" accept=".csv,.tsv,.jsonl,.json" class="sr-only" x-on:change="uploadProfiles($event)"></label>
              </div>
              <div class="flex flex-wrap gap-2">
                <div style="flex:1;min-width:110px"><label class="lbl">user_id</label><input x-model="pf.user_id" class="field" placeholder="u1"></div>
                <div style="flex:1;min-width:100px"><label class="lbl">연령대</label><select x-model="pf.age_band" class="field"><option value="">선택</option><template x-for="a in ((userData && userData.profile_fields) ? userData.profile_fields.age_bands : [])" x-bind:key="a"><option x-bind:value="a" x-text="a"></option></template></select></div>
                <div style="flex:1;min-width:110px"><label class="lbl">주 이용 시간대</label><select x-model="pf.day_part" class="field"><option value="">선택</option><template x-for="dp in ((userData && userData.profile_fields) ? userData.profile_fields.day_parts : [])" x-bind:key="dp"><option x-bind:value="dp" x-text="dp"></option></template></select></div>
              </div>
              <div style="margin-top:8px"><label class="lbl">관심 선언 (쉼표 구분)</label><input x-model="pf.interests" class="field" placeholder="재테크, 야구"></div>
              <div class="flex items-center gap-2" style="margin-top:10px">
                <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-bind:disabled="modBusy" x-on:click="saveProfile()" x-text="modBusy ? '처리 중…' : '프로필 저장'"></button>
                <span class="text-xs text-muted" x-text="pfMsg"></span>
              </div>
            </div>
          </div>
          <div class="panel"><div class="panel-hd"><b>행동 로그</b><span class="meta">노출 · 클릭 · 체류 · 업로드분은 저장되어 재방문 시 유지</span></div>
            <div class="panel-bd">
              <div class="flex items-center gap-2" style="margin-bottom:10px">
                <a href="/usermeta-template.csv" download class="ds-btn ds-btn--secondary ds-btn--s-sm">템플릿</a>
                <label class="ds-btn ds-btn--primary ds-btn--s-sm" style="cursor:pointer">행동 로그 업로드
                  <input type="file" accept=".csv,.tsv,.jsonl,.json" class="sr-only" x-on:change="uploadUserLog($event)"></label>
              </div>
              <div class="text-xs text-muted" x-show="userData && userData.source" x-text="userData ? userData.source : ''"></div>
            </div>
          </div>
        </div>

        <!-- 실데이터 사용자 · 생성 페르소나가 있으면 카드에 병행 표시 -->
        <div x-show="userData && userData.users && userData.users.length" class="space-y-3">
          <template x-for="u in (userData?userData.users:[])" x-bind:key="u.user_id">
            <div class="panel"><div class="panel-hd">
              <b x-text="u.user_id"></b>
              <span class="ds-badge ds-badge--entity" style="cursor:help" x-bind:data-tip="'기본 8종 판별 · ' + u.persona + (u.persona_conf ? (' · 신뢰도 ' + u.persona_conf) : '') + (u.persona_second ? (' · 2순위 ' + u.persona_second) : '')" data-tip-pos="top" x-text="u.persona + (u.persona_conf ? ' · ' + u.persona_conf : '')"></span>
              <span x-show="u.persona_provisional" class="ds-badge ds-badge--warning" style="cursor:help" data-tip="로그가 부족해 선언 프로필로 잠정 배정 · 로그가 쌓이면 자동 재산출" data-tip-pos="top">잠정</span>
              <span x-show="u.gen_persona" class="ds-badge ds-badge--success" style="cursor:help" x-bind:data-tip="u.gen_persona ? ('이 사용자의 메타 + 소비로 생성한 전용 페르소나 · ' + u.gen_persona.desc) : ''" data-tip-pos="top"><span class="ds-badge__dot"></span><span x-text="u.gen_persona ? (u.gen_persona.name + ' · 생성됨') : ''"></span></span>
              <span class="meta tnum ml-auto" x-text="'조회 ' + u.engagement.views + ' · 클릭률 ' + u.engagement.click_rate + ' · 평균체류 ' + u.engagement.avg_dwell_sec + 's'"></span>
            </div><div class="panel-bd">
              <div class="drow" x-show="u.profile"><div class="k">프로필</div><div class="v text-sm text-body" x-text="u.profile ? [u.profile.age_band, (u.profile.interests||[]).join(' · '), u.profile.day_part].filter(Boolean).join('  ·  ') : ''"></div></div>
              <div class="drow" x-show="u.gen_persona"><div class="k">생성 페르소나</div><div class="v">
                <div class="text-sm"><b class="text-ink" x-text="u.gen_persona ? (u.gen_persona.full || u.gen_persona.name) : ''"></b><span class="text-muted" x-text="u.gen_persona ? (' · ' + u.gen_persona.desc) : ''"></span></div>
                <div class="mt-1.5 flex flex-wrap gap-1.5"><template x-for="(b, bi) in ((u.gen_persona && u.gen_persona.basis) || [])" x-bind:key="'gb'+bi"><span class="ds-badge ds-badge--neutral" style="cursor:help" data-tip="판단 근거 · 행동 로그와 프로필에서 도출" data-tip-pos="top" x-text="b"></span></template></div>
              </div></div>
              <div class="drow"><div class="k">소비 형태</div><div class="v text-sm text-body" x-text="Object.entries(u.form).map(e=>e[0]+':'+e[1]).join(' · ')"></div></div>
              <div class="drow"><div class="k">소비 강도</div><div class="v flex flex-wrap gap-1.5">
                <template x-for="(v,k) in u.intensity" x-bind:key="k"><span class="ds-badge ds-badge--neutral" style="cursor:help" x-bind:class="v==='고'?'ds-badge--entity':(v==='중'?'ds-badge--intent':'ds-badge--category')" x-bind:data-tip="'맥락(인텐트) ' + k + ' 소비 강도 ' + v + ' · 체류·클릭 가중 상대 등급'" data-tip-pos="top" x-text="k + ' (' + v + ')'"></span></template>
              </div></div>
              <div class="drow"><div class="k">선호 엔티티</div><div class="v flex flex-wrap gap-1.5">
                <template x-for="e in (u.affinity_entities||[])" x-bind:key="e[0]"><span class="ds-badge ds-badge--entity" style="cursor:help" x-bind:data-tip="termDef('entity', e[0])" data-tip-pos="top" x-text="e[0]"></span></template>
                <span x-show="!(u.affinity_entities||[]).length" class="text-xs text-muted">·</span>
              </div></div>
              <div class="drow" x-show="(u.similar_users||[]).length"><div class="k">비슷한 사용자</div><div class="v flex flex-wrap gap-1.5">
                <template x-for="s in (u.similar_users||[])" x-bind:key="s[0]"><span class="ds-badge ds-badge--neutral tnum" style="cursor:help" x-bind:data-tip="'소비 프로필(관심 카테고리·맥락) 코사인 유사도 ' + s[1]" data-tip-pos="top" x-text="s[0] + ' (' + s[1] + ')'"></span></template>
              </div></div>
            </div></div>
          </template>
        </div>

        <!-- 엔티티 × 페르소나 친화도 · 타겟팅/능동 추천의 실계산 근거 -->
        <div class="panel" x-show="userData && userData.aggregate && userData.aggregate.entity_persona && userData.aggregate.entity_persona.rows && userData.aggregate.entity_persona.rows.length">
          <div class="panel-hd"><b>엔티티 × 페르소나 친화도</b><span class="meta">체류·클릭 가중 합산 · 이 엔티티를 어떤 페르소나가 소비하나 (타겟팅 근거)</span></div>
          <div class="overflow-auto"><table class="ds-table"><thead><tr><th>엔티티</th><th>주 소비 페르소나</th>
            <template x-for="pn in ((userData && userData.aggregate && userData.aggregate.entity_persona) ? userData.aggregate.entity_persona.personas : [])" x-bind:key="'eph'+pn"><th class="tnum" x-text="pn"></th></template>
          </tr></thead><tbody>
            <template x-for="row in ((userData && userData.aggregate && userData.aggregate.entity_persona) ? userData.aggregate.entity_persona.rows : [])" x-bind:key="'ep'+row[0]">
              <tr><td class="text-ink" x-text="row[0]"></td><td><span class="ds-badge ds-badge--entity" x-text="row[1]"></span></td>
                <template x-for="(w, wi) in row[2]" x-bind:key="'epw'+row[0]+wi"><td class="tnum" x-bind:class="w ? '' : 'text-muted'" x-text="w || '·'"></td></template></tr>
            </template>
          </tbody></table></div>
        </div>

        <!-- 명세(페르소나 정의·공식) · 기본 8종 + 생성분 병행 -->
        <div class="panel"><div class="panel-hd"><b>페르소나 정의</b><span class="meta" x-text="'기본 8종' + ((userData && userData.generated_n) ? (' + 생성 ' + userData.generated_n) : '') + ' · 형태 + 맥락별 강도 시그니처'"></span><button type="button" class="copybtn" x-show="userData && userData.users && userData.users.length" x-on:click="exportUsers()"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>엑셀 다운로드</button></div>
          <div class="overflow-auto"><table class="ds-table"><thead><tr><th>페르소나</th><th>설명</th><th>형태(깊이·체류)</th></tr></thead><tbody>
            <template x-for="p in (userData?userData.personas_def:[])" x-bind:key="p.id">
              <tr><td class="text-ink"><span x-text="p.full || p.name"></span><span x-show="p.generated" class="ds-badge ds-badge--success" style="margin-left:6px;cursor:help" x-bind:data-tip="p.generated ? ('사용자 ' + p.user_id + ' 전용 생성 페르소나') : ''" data-tip-pos="top">생성됨</span></td><td><div class="tbox" x-text="p.desc"></div></td><td x-text="(p.form['깊이']||'') + ' · ' + (p.form['체류·완주']||'')"></td></tr>
            </template>
            <template x-if="!(userData&&userData.personas_def&&userData.personas_def.length)"><tr><td colspan="3" class="text-muted">먼저 [실행·추출]에서 콘텐츠를 추출하세요</td></tr></template>
          </tbody></table></div>
          <div class="text-xs text-muted" style="margin:10px 16px 14px" x-show="userData && userData.formula" x-text="userData ? userData.formula : ''"></div>
        </div>
      </div>

      <!-- ═══ 모듈: 미디어 메타 파이프라인 (포토·영상 텍스트화 → 메타추출 설계·실험) ═══ -->
      <div x-show="mod === 'lab' && labTab === 'media'" x-cloak class="w-full space-y-4">
        <ul class="ds-bullets hintbox" style="padding:var(--ds-space-3) var(--ds-space-4)">
          <li><b>포토·영상 메타 추출 파이프라인 v1</b> — Gemini Flash 기반 <b>텍스트화 트랙</b>(자막·오디오·비주얼)으로 통합 원고를 만들고, 텍스트 추론 모델이 <b>아이템 메타</b>(리드문·인텐트·엔티티·IAB)를 뽑습니다.</li>
          <li>텍스트화는 Flash 고정, <b>메타추출 모델은 인터페이스로 종속을 차단한 선정 대기 슬롯</b>입니다 · 로컬 미디어 디코딩(ffmpeg) 미사용 → 입력은 이미 분해된 미디어(자막·오디오·프레임).</li>
        </ul>

        <!-- 파이프라인 트랙 현황 -->
        <div class="tiles" style="grid-template-columns:repeat(3,1fr)">
          <div class="tile"><div class="t" style="font-weight:600">T1 자막 파싱</div><div class="text-xs text-muted" style="margin-top:4px">SRT/VTT → 타임스탬프 원고 · 룰(모델 0건)</div><span class="ds-badge ds-badge--success" style="margin-top:8px"><span class="ds-badge__dot"></span>실동작</span></div>
          <div class="tile"><div class="t" style="font-weight:600">T2 오디오 전사</div><div class="text-xs text-muted" style="margin-top:4px">라우터 input_audio → 구조화 전사(Gemini Flash)</div><span class="ds-badge ds-badge--neutral" style="margin-top:8px"><span class="ds-badge__dot"></span>라우터 연결 대기</span></div>
          <div class="tile"><div class="t" style="font-weight:600">T3 비주얼 묘사</div><div class="text-xs text-muted" style="margin-top:4px">프레임 k장 단일 호출 → 묘사+화면 텍스트</div><span class="ds-badge ds-badge--neutral" style="margin-top:8px"><span class="ds-badge__dot"></span>라우터 연결 대기</span></div>
        </div>

        <!-- T1 자막 파싱 실험기 (모델·ffmpeg 불필요 · 보유율 실측 = 비용 계획 기준점) -->
        <div class="panel"><div class="panel-hd"><b>T1 · 자막 파싱 실험기</b><span class="meta">SRT/VTT 붙여넣기 → 타임스탬프 원고 · 자막 보유율이 비용 계획의 기준점</span></div>
          <div class="panel-bd space-y-4">
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
              <div style="flex:1;min-width:260px"><label class="lbl">자막 원문 (SRT · VTT)</label>
                <textarea class="field" style="min-height:120px;font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:12px" x-model="mediaSub.raw" placeholder="1&#10;00:00:01,000 --> 00:00:04,000&#10;안녕하세요 여러분"></textarea>
              </div>
              <div style="display:flex;flex-direction:column;gap:8px">
                <div><label class="lbl">형식</label>
                  <select class="field" x-model="mediaSub.fmt" style="width:120px">
                    <option value="">자동 판별</option><option value="srt">SRT</option><option value="vtt">VTT</option>
                  </select>
                </div>
                <button type="button" class="ds-btn ds-btn--primary" x-on:click="mediaParse()" x-bind:disabled="mediaBusy||!mediaSub.raw.trim()">파싱</button>
              </div>
            </div>
            <div x-show="mediaMsg" class="text-xs text-muted" x-text="mediaMsg"></div>
            <div x-show="mediaRes" class="space-y-3">
              <div class="tiles" style="grid-template-columns:repeat(2,1fr)">
                <div class="tile"><div class="n tnum" x-text="mediaRes?mediaRes.cue_count:0"></div><div class="t">파싱된 큐</div></div>
                <div class="tile"><div class="n tnum" style="text-transform:uppercase" x-text="mediaRes?mediaRes.format:''"></div><div class="t">판별 형식</div></div>
              </div>
              <div><label class="lbl">타임스탬프 원고</label>
                <pre class="field" style="max-height:220px;overflow:auto;white-space:pre-wrap;font-family:ui-monospace,'SF Mono',Consolas,monospace;font-size:12px" x-text="mediaRes?mediaRes.transcript:''"></pre>
              </div>
            </div>
          </div>
        </div>

        <!-- 포토·이미지 실험 (업로드 → 시각 이해 → 합성 Content → 기존 추출 ItemMeta · 실험·미저장 · 콘텐츠 관리에서 이관) -->
        <div class="panel"><div class="panel-hd"><b>포토 · 이미지 실험</b><span class="meta">이미지 업로드(여러 장 = 하나로 통합) → 시각 이해 → 합성 Content → 기존 추출(ItemMeta) · 실험이라 저장 안 함</span></div>
          <div class="panel-bd space-y-4">
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
              <div style="flex:1;min-width:240px"><label class="lbl">이미지 파일 (여러 장 가능)</label>
                <input type="file" accept="image/*" multiple class="field" x-on:change="mediaImgPick($event)">
              </div>
              <div style="flex:1;min-width:200px"><label class="lbl">캡션 (선택)</label>
                <input class="field" x-model="mediaImg.caption" placeholder="사진 설명이 있으면 함께 참조">
              </div>
              <button type="button" class="ds-btn ds-btn--primary" x-on:click="mediaImgRun()" x-bind:disabled="mediaImgBusy||!mediaImg.files.length">실행</button>
            </div>
            <div x-show="mediaImg.files.length" class="text-xs text-muted" x-text="mediaImg.files.length + '장 선택됨'"></div>
            <div x-show="mediaImgMsg" class="text-xs text-muted" x-text="mediaImgMsg"></div>
            <div x-show="mediaImgRes" class="space-y-3">
              <div x-show="mediaImgRes && mediaImgRes.mock"><span class="ds-badge ds-badge--neutral"><span class="ds-badge__dot"></span>mock · 비전 미연결(키 없음)</span></div>
              <div><label class="lbl">합성 Content (본문)</label>
                <pre class="field" style="max-height:150px;overflow:auto;white-space:pre-wrap;font-size:12px" x-text="mediaImgRes ? (mediaImgRes.content.body || '') : ''"></pre></div>
              <div class="panel" style="margin:0"><div class="panel-hd"><b>아이템 메타 (기존 추출 재사용)</b><span class="meta">실험 · 미저장</span></div>
                <div class="panel-bd space-y-2">
                  <div class="drow"><div class="k">리드문</div><div class="v text-sm text-body" x-text="mediaImgRes ? ((mediaImgRes.output.item_meta||{}).summary || '—') : ''"></div></div>
                  <div class="drow"><div class="k">인텐트</div><div class="v flex flex-wrap gap-1.5"><template x-for="it in (mediaImgRes ? ((mediaImgRes.output.item_meta||{}).intent||[]) : [])" x-bind:key="it"><span class="ds-badge ds-badge--intent" x-text="it"></span></template><span x-show="mediaImgRes && !((mediaImgRes.output.item_meta||{}).intent||[]).length" class="text-xs text-muted">—</span></div></div>
                  <div class="drow"><div class="k">엔티티</div><div class="v flex flex-wrap gap-1.5"><template x-for="e in (mediaImgRes ? ((mediaImgRes.output.item_meta||{}).entities||[]) : [])" x-bind:key="e"><span class="ds-badge ds-badge--entity" x-text="e"></span></template><span x-show="mediaImgRes && !((mediaImgRes.output.item_meta||{}).entities||[]).length" class="text-xs text-muted">—</span></div></div>
                  <div class="drow"><div class="k">카테고리</div><div class="v flex flex-wrap gap-1.5"><template x-for="c in (mediaImgRes ? ((mediaImgRes.output.item_meta||{}).content_category||[]) : [])" x-bind:key="c"><span class="ds-badge ds-badge--category" x-text="c"></span></template><span x-show="mediaImgRes && !((mediaImgRes.output.item_meta||{}).content_category||[]).length" class="text-xs text-muted">—</span></div></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- T4 네이티브 비디오 실험 (영상 통짜 → 라우터 위임 → 병합 → 합성 Content → 기존 추출 ItemMeta · 실험·미저장) -->
        <div class="panel"><div class="panel-hd"><b>T4 · 네이티브 비디오 실험</b><span class="meta">영상 업로드 → 라우터 위임(프레임+오디오 단일 호출) → 병합 → 합성 Content → 기존 추출(ItemMeta) · 실험이라 저장 안 함</span></div>
          <div class="panel-bd space-y-4">
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
              <div style="flex:1;min-width:240px"><label class="lbl">영상 파일</label>
                <input type="file" accept="video/*" class="field" x-on:change="mediaVidPick($event)">
              </div>
              <div style="flex:1;min-width:200px"><label class="lbl">캡션·설명 (선택)</label>
                <input class="field" x-model="mediaVid.caption" placeholder="기존 텍스트 메타(제목·설명)와 결합">
              </div>
              <button type="button" class="ds-btn ds-btn--primary" x-on:click="mediaNative()" x-bind:disabled="mediaVidBusy||!mediaVid.file">실행</button>
            </div>
            <div x-show="mediaVidMsg" class="text-xs text-muted" x-text="mediaVidMsg"></div>
            <div x-show="mediaVidRes" class="space-y-3">
              <div x-show="mediaVidRes && mediaVidRes.mock"><span class="ds-badge ds-badge--neutral"><span class="ds-badge__dot"></span>mock · 라우터 미연결(키 없음)</span></div>
              <div><label class="lbl">발화 전사 (오디오 트랙)</label>
                <pre class="field" style="max-height:150px;overflow:auto;white-space:pre-wrap;font-size:12px" x-text="mediaVidRes ? (mediaVidRes.native.audio.transcript || '(발화 없음)') : ''"></pre></div>
              <div><label class="lbl">비주얼 묘사</label>
                <div class="text-sm text-body" x-text="mediaVidRes ? mediaVidRes.native.visual.description : ''"></div>
                <div class="text-xs text-muted" x-show="mediaVidRes && mediaVidRes.native.visual.on_screen_text" x-text="'[화면 텍스트] ' + (mediaVidRes ? mediaVidRes.native.visual.on_screen_text : '')"></div></div>
              <div><label class="lbl">통합 원고 (S4 병합)</label>
                <pre class="field" style="max-height:150px;overflow:auto;white-space:pre-wrap;font-size:12px" x-text="mediaVidRes ? mediaVidRes.merged.transcript : ''"></pre></div>
              <div class="panel" style="margin:0"><div class="panel-hd"><b>아이템 메타 (S5 · 기존 추출 재사용)</b><span class="meta">실험 · 미저장</span></div>
                <div class="panel-bd space-y-2">
                  <div class="drow"><div class="k">리드문</div><div class="v text-sm text-body" x-text="mediaVidRes ? ((mediaVidRes.output.item_meta||{}).summary || '—') : ''"></div></div>
                  <div class="drow"><div class="k">인텐트</div><div class="v flex flex-wrap gap-1.5"><template x-for="it in (mediaVidRes ? ((mediaVidRes.output.item_meta||{}).intent||[]) : [])" x-bind:key="it"><span class="ds-badge ds-badge--intent" x-text="it"></span></template><span x-show="mediaVidRes && !((mediaVidRes.output.item_meta||{}).intent||[]).length" class="text-xs text-muted">—</span></div></div>
                  <div class="drow"><div class="k">엔티티</div><div class="v flex flex-wrap gap-1.5"><template x-for="e in (mediaVidRes ? ((mediaVidRes.output.item_meta||{}).entities||[]) : [])" x-bind:key="e"><span class="ds-badge ds-badge--entity" x-text="e"></span></template><span x-show="mediaVidRes && !((mediaVidRes.output.item_meta||{}).entities||[]).length" class="text-xs text-muted">—</span></div></div>
                  <div class="drow"><div class="k">카테고리</div><div class="v flex flex-wrap gap-1.5"><template x-for="c in (mediaVidRes ? ((mediaVidRes.output.item_meta||{}).content_category||[]) : [])" x-bind:key="c"><span class="ds-badge ds-badge--category" x-text="c"></span></template><span x-show="mediaVidRes && !((mediaVidRes.output.item_meta||{}).content_category||[]).length" class="text-xs text-muted">—</span></div></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- S5 · 메타추출 모델 A/B (같은 통합 원고 → 후보 모델별 ItemMeta 나란히 · 실험·미저장) -->
        <div class="panel"><div class="panel-hd"><b>S5 · 메타추출 모델 A/B</b><span class="meta">같은 통합 원고를 후보 모델에 태워 리드문·인텐트·엔티티·IAB 나란히 비교 · 모델 교체 자유 실측(인터페이스 종속 차단 증명)</span></div>
          <div class="panel-bd space-y-4">
            <div><label class="lbl">통합 원고 (S4 병합 결과 붙여넣기 또는 임의 콘텐츠 본문)</label>
              <textarea class="field" style="min-height:100px;font-size:12px" x-model="mediaS5.text" placeholder="영상/이미지 통합 원고 또는 콘텐츠 본문"></textarea>
            </div>
            <div><label class="lbl">후보 모델 <span class="meta" x-text="'· 선택 ' + mediaS5.models.length"></span></label>
              <div class="flex flex-wrap gap-1.5">
                <template x-for="m in availableModels" x-bind:key="m">
                  <span class="ds-badge" style="cursor:pointer" x-bind:class="mediaS5.models.includes(m) ? 'ds-badge--intent' : 'ds-badge--neutral'" x-on:click="mediaS5Toggle(m)" x-text="m"></span>
                </template>
                <span x-show="!availableModels.length" class="text-xs text-muted">사용 가능 모델이 없습니다 · 설정에서 모델을 지정하세요</span>
              </div>
            </div>
            <button type="button" class="ds-btn ds-btn--primary" x-on:click="mediaS5Run()" x-bind:disabled="mediaS5Busy||!mediaS5.text.trim()||!mediaS5.models.length">A/B 실행</button>
            <div x-show="mediaS5Msg" class="text-xs text-muted" x-text="mediaS5Msg"></div>
            <div x-show="mediaS5Res" style="overflow-x:auto">
              <div style="display:flex;gap:12px;min-width:min-content">
                <template x-for="r in (mediaS5Res ? mediaS5Res.results : [])" x-bind:key="r.model">
                  <div class="panel" style="margin:0;min-width:230px;flex:1">
                    <div class="panel-hd"><b x-text="r.model"></b>
                      <span x-show="r.mock" class="ds-badge ds-badge--neutral"><span class="ds-badge__dot"></span>mock</span>
                      <span x-show="!r.mock && !r.error && r.empty" class="ds-badge ds-badge--error"><span class="ds-badge__dot"></span>빈 산출</span>
                    </div>
                    <div class="panel-bd space-y-2">
                      <template x-if="r.error"><div class="text-xs" style="color:var(--ds-error,#c0392b)" x-text="r.error"></div></template>
                      <!-- 빈 산출 진단: trace.fails 의 fail_kind 노출(예: parse_empty=침묵 빈응답) -->
                      <template x-if="!r.error && !r.mock && r.empty">
                        <div class="text-xs text-muted">진단: <span x-text="(r.fails&&r.fails.length) ? r.fails.map(f=>f.tag+'·'+f.kind).join(', ') : '실호출됐으나 메타 미산출(응답 공백 추정)'"></span></div>
                      </template>
                      <template x-if="!r.error">
                        <div class="space-y-2">
                          <div><div class="lbl">리드문</div><div class="text-sm text-body" x-text="(r.item_meta||{}).summary || '—'"></div></div>
                          <div><div class="lbl">인텐트</div><div class="flex flex-wrap gap-1"><template x-for="it in ((r.item_meta||{}).intent||[])" x-bind:key="it"><span class="ds-badge ds-badge--intent" x-text="it"></span></template></div></div>
                          <div><div class="lbl">엔티티</div><div class="flex flex-wrap gap-1"><template x-for="e in ((r.item_meta||{}).entities||[])" x-bind:key="e"><span class="ds-badge ds-badge--entity" x-text="e"></span></template></div></div>
                          <div><div class="lbl">카테고리</div><div class="flex flex-wrap gap-1"><template x-for="c in ((r.item_meta||{}).content_category||[])" x-bind:key="c"><span class="ds-badge ds-badge--category" x-text="c"></span></template></div></div>
                        </div>
                      </template>
                    </div>
                  </div>
                </template>
              </div>
            </div>
            <div class="text-xs text-muted">키 미연결 시 모든 모델이 route=mock 로 동일 산출됩니다 · 라우터/Upstage 키 연결 시 모델별로 갈립니다.</div>
          </div>
        </div>

        <!-- 후속 슬롯 안내 -->
        <div class="panel"><div class="panel-hd"><b>후속 슬롯</b><span class="meta">진행 현황</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets">
              <li><b>raw 영상 분해</b> — <b>네이티브 비디오(라우터 위임)로 결정</b>. 영상 통짜를 라우터로 보내 프레임+오디오 동시 토큰화(로컬 ffmpeg 미사용). <span class="ds-badge ds-badge--success"><span class="ds-badge__dot"></span>결정</span></li>
              <li><b>이미지 이관</b> — 콘텐츠 관리 이미지 처리를 이 미디어 탭으로 이관 완료(실험실 전담). <span class="ds-badge ds-badge--success"><span class="ds-badge__dot"></span>완료</span></li>
              <li><b>S5 메타추출 모델</b> — 위 A/B 하네스로 후보 나란히 비교(인터페이스 고정: json_schema · 한국어 · 32K+). 라우터/Upstage 실연결 후 실측 선정. <span class="ds-badge ds-badge--neutral"><span class="ds-badge__dot"></span>실연결 후 선정</span></li>
            </ul>
          </div>
        </div>
      </div>

      <!-- ═══ 모듈: 게시판 · 기능개선 제안 + 오류 제보(팀 스코프) ═══ -->
      <div x-show="mod === 'board'" x-cloak class="w-full space-y-4">
        <div class="panel"><div class="panel-hd"><b>새 글 등록</b><span class="meta">기능개선 제안 · 오류 제보 · 우리 팀에만 공개</span></div>
          <div class="panel-bd">
            <div class="flex flex-wrap gap-2">
              <div style="min-width:130px"><label class="lbl">유형</label>
                <select x-model="boardForm.kind" class="field"><option value="bug">오류</option><option value="feature">기능개선</option></select></div>
              <div style="flex:1;min-width:220px"><label class="lbl">제목</label><input x-model="boardForm.title" class="field" maxlength="80" placeholder="예) 검수 저장 시 화면이 멈춰요" x-on:keydown.enter="boardSubmit()"></div>
            </div>
            <div style="margin-top:8px"><label class="lbl">내용 (선택)</label>
              <textarea x-model="boardForm.body" class="field" rows="3" maxlength="2000" placeholder="오류: 재현 방법 · 기대 동작 / 기능개선: 배경 · 제안 내용" style="height:auto;padding-top:8px"></textarea></div>
            <div class="flex items-center gap-2" style="margin-top:10px">
              <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-bind:disabled="boardBusy" x-on:click="boardSubmit()" x-text="boardBusy ? '등록 중…' : '등록'"></button>
              <span class="text-xs text-muted" x-text="boardMsg"></span>
            </div>
          </div></div>
        <div class="panel"><div class="panel-hd"><b>접수 목록</b><span class="meta tnum" x-text="boardData ? (boardData.n + '건 · 최신순') : ''"></span></div>
          <div class="overflow-auto"><table class="ds-table"><thead><tr><th style="width:86px">유형</th><th>제목 · 내용</th><th style="width:100px">작성자</th><th style="width:120px">상태</th><th style="width:130px"></th></tr></thead><tbody>
            <template x-for="b in (boardData ? boardData.items : [])" x-bind:key="b.id">
              <tr>
                <td><span class="ds-badge" x-bind:class="b.kind==='bug' ? 'ds-badge--error' : 'ds-badge--intent'" x-text="b.kind==='bug' ? '오류' : '기능개선'"></span></td>
                <td><div class="text-ink" style="font-weight:600" x-text="b.title"></div><div class="tbox" x-show="b.body" x-text="b.body"></div></td>
                <td class="text-body" x-text="b.author"></td>
                <td>
                  <template x-if="boardAdmin">
                    <select class="field" style="height:30px;padding:0 26px 0 9px;width:auto" x-bind:value="b.status" x-on:change="boardStatus(b, $event.target.value)" data-tip="관리자: 처리 상태를 팀에 공유" data-tip-pos="top">
                      <option value="open">접수</option><option value="doing">처리 중</option><option value="done">완료</option></select>
                  </template>
                  <template x-if="!boardAdmin">
                    <span class="ds-badge" x-bind:class="b.status==='done' ? 'ds-badge--success' : (b.status==='doing' ? 'ds-badge--intent' : 'ds-badge--neutral')"><span class="ds-badge__dot"></span><span x-text="b.status==='done' ? '완료' : (b.status==='doing' ? '처리 중' : '접수')"></span></span>
                  </template>
                </td>
                <td><span class="text-xs text-muted tnum" x-text="fmtTs(b.ts)"></span>
                  <button type="button" class="copybtn" x-show="b.mine || boardAdmin" x-on:click="boardDelete(b)" style="margin-left:6px">삭제</button></td>
              </tr>
            </template>
            <template x-if="!(boardData && boardData.items && boardData.items.length)"><tr><td colspan="5" class="text-muted">아직 글이 없습니다 · 불편한 점이나 아이디어를 첫 글로 남겨보세요</td></tr></template>
          </tbody></table></div>
        </div>
      </div>

      <!-- ═══ 모듈: 테스트셋 생성 · 골든/검수/원본 뷰 ═══ -->
      <!-- ═══ 모듈: 평가 · 테스트셋(골든) 기준 정합성 수치화 + 모델별 비교(단일 페이지) ═══ -->
      <div x-show="mod === 'evaluate'" x-cloak class="w-full space-y-4">
          <section class="panel" data-fn><div class="panel-hd"><b>평가 기준</b><span class="meta">어떤 모델·버전을 어떤 콘텐츠로 잴지 먼저 정합니다</span></div>
            <div class="panel-bd">
              <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
                <span class="selctl" data-tip="정답셋과 비교할 대상 모델 · 비워두면 현재 설정 모델" data-tip-pos="bottom"><span class="selctl__lbl">기준 모델</span>
                  <select class="field" x-model="evalModel"><option value="">현재 설정 모델</option><template x-for="m in availableModels" x-bind:key="'ev'+m"><option x-bind:value="m" x-text="m"></option></template></select></span>
                <span class="selctl" data-tip="프롬프트 버전 = 학습 반영 회차 + 1 · 평가는 항상 현재 버전으로 실행됩니다" data-tip-pos="bottom"><span class="selctl__lbl">프롬프트 버전</span>
                  <b class="tnum" style="padding:0 8px;font-size:13px;height:30px;display:inline-flex;align-items:center" x-text="verTxt"></b></span>
                <span style="display:flex;gap:6px;align-items:center;margin-left:6px"><span class="selctl__lbl">대상 콘텐츠</span>
                  <button type="button" class="srcfilter__chip" x-bind:class="evalScope==='all' ? 'sel' : ''" x-on:click="evalScope='all'">전체 정답셋</button>
                  <button type="button" class="srcfilter__chip" x-bind:class="evalScope==='eval' ? 'sel' : ''" x-on:click="evalScope='eval'">평가용만</button>
                </span>
              </div>
              <ul class="ds-bullets" style="margin:11px 0 0">
                <li><b>평가용</b> 콘텐츠는 <b>콘텐츠 관리 · STEP 1</b>에서 지정합니다 · 검수 목록에서 제외되어 오염 없이 평가에만 쓰입니다.</li>
                <li>평가는 항상 <b>현재 프롬프트 버전</b>으로 실행됩니다 · 버전 간 추이는 학습 반영을 거듭하며 재평가로 비교하세요.</li>
              </ul>
            </div>
          </section>
          <section class="panel"><div class="panel-hd"><b>평가 실행</b><span class="meta">위 기준으로 정답셋과 비교 · 요약과 건별 판정</span></div>
            <div class="panel-bd">
              <ul class="ds-bullets" style="margin-bottom:11px"><li>검수 합의로 쌓인 <b>정답셋(테스트셋)</b>과 모델 결과를 비교해 요약 수치와 <b>불일치 목록</b>을 만듭니다.</li><li>불일치 건은 검수처럼 <b>건별 판정</b>합니다 · <b>모델이 맞음</b>=정답을 고칠 후보로 표시 · <b>정답 유지</b>=모델 오답으로 확정.</li><li>평가 건수가 적으면 오차가 큽니다 · 신뢰구간이 겹치면 우열 판단을 미룹니다.</li></ul>
              <button type="button" class="ds-btn ds-btn--primary" x-bind:disabled="goldenBusy" x-on:click="runGolden()" x-text="goldenBusy ? '평가 중… (전건 추출)' : '평가 실행'"></button>
              <span class="text-xs text-muted" style="margin-left:10px" x-show="goldenResult && !goldenResult.ok" x-text="goldenResult ? goldenResult.error : ''"></span>
              <template x-if="goldenResult && goldenResult.ok">
                <div>
                  <!-- 결과 카드화: 산개한 숫자·막대를 타일과 박스로 묶어 시선 고정 -->
                  <div class="text-xs text-muted" style="margin-top:12px" x-show="goldenResult.basis">기준: <b class="text-ink" x-text="goldenResult.basis ? (goldenResult.basis.model || '현재 설정 모델') : ''"></b> · <span class="tnum" x-text="goldenResult.basis ? ('v' + goldenResult.basis.version) : ''"></span> · <span x-text="goldenResult.basis && goldenResult.basis.scope === 'eval' ? '평가용 콘텐츠' : '전체 정답셋'"></span></div>
                  <div class="tiles" style="grid-template-columns:repeat(4,1fr);margin-top:10px">
                    <div class="tile tile--hero"><div class="n tnum" x-text="Math.round((goldenResult.grade_accuracy||0)*100)+'%'"></div><div class="t">등급 일치율<span class="tnum" x-text="goldenResult.grade_ci ? (' · 신뢰구간 ' + pctTxt(goldenResult.grade_ci.lo) + '~' + pctTxt(goldenResult.grade_ci.hi)) : ''"></span></div></div>
                    <div class="tile"><div class="n tnum" x-text="Math.round((goldenResult.reason_jaccard||0)*100)+'%'"></div><div class="t">사유 일치</div></div>
                    <div class="tile"><div class="n tnum" x-text="Math.round((goldenResult.harm_miss_rate||0)*100)+'%'"></div><div class="t">유해 놓침</div></div>
                    <div class="tile"><div class="n tnum" x-text="goldenResult.evaluated"></div><div class="t">평가 건수</div></div>
                  </div>
                  <div class="tile" style="margin-top:10px">
                    <div class="t" style="margin:0 0 8px">유형별 일치율 · 어디가 약한지(수정 우선순위)</div>
                    <template x-for="(v,k) in (goldenResult.by_reason_bucket||{})" x-bind:key="k">
                      <div class="ds-progress" style="margin:7px 0"><div class="ds-progress__head"><span class="ds-progress__label"><span class="ds-badge ds-badge--reason" style="cursor:help" x-bind:data-tip="k==='normal' ? '문제 사유 없는 일반 콘텐츠' : termDef('reason', k)" data-tip-pos="top" x-text="reasonBoth(k)"></span> <span class="tnum text-muted" x-text="'('+v.n+')'"></span></span><span class="ds-progress__pct tnum" x-text="Math.round(v.grade_acc*100)+'%'"></span></div><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="'width:'+Math.max(v.grade_acc*100,3)+'%'"></div></div></div>
                    </template>
                  </div>
                  <div class="subhd" style="margin:16px 0 8px">평가 상세 · 불일치 건별 판정 <span class="meta">모델이 맞음=정답 교정 후보 · 정답 유지=모델 오답 확정</span></div>
                  <template x-if="(goldenResult.detail||[]).length">
                    <div class="overflow-auto" style="max-height:340px"><table class="ds-table"><thead><tr><th>콘텐츠</th><th style="width:64px">정답</th><th style="width:84px">모델 결과</th><th style="width:160px">판정</th><th style="width:190px">합의</th></tr></thead><tbody>
                      <template x-for="d in goldenResult.detail" x-bind:key="'ej'+d.hash">
                        <tr>
                          <td class="text-ink" x-text="d.title || '(제목 없음)'"></td>
                          <td><span class="ds-badge" style="cursor:help" x-bind:class="d.expected==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-bind:data-tip="termDef('grade', d.expected)" data-tip-pos="top" x-text="d.expected"></span></td>
                          <td><span class="ds-badge" style="cursor:help" x-bind:class="d.got==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-bind:data-tip="termDef('grade', d.got)" data-tip-pos="top" x-text="d.got"></span></td>
                          <td><span style="display:inline-flex;gap:6px">
                            <button type="button" class="verdictbtn verdictbtn--good" style="height:26px;padding:0 10px;font-size:11px" x-bind:class="myEvalVote(d)==='adopt' ? 'is-on' : ''" data-tip="모델 결과가 맞아요 · 정답을 고칠 후보로 올립니다" data-tip-pos="top" x-on:click="evalJudge(d, 'adopt')">모델이 맞음</button>
                            <button type="button" class="verdictbtn verdictbtn--bad" style="height:26px;padding:0 10px;font-size:11px" x-bind:class="myEvalVote(d)==='reject' ? 'is-on' : ''" data-tip="기존 정답이 맞아요 · 모델 오답으로 확정합니다" data-tip-pos="top" x-on:click="evalJudge(d, 'reject')">정답 유지</button>
                          </span></td>
                          <td><span class="text-xs text-muted tnum" x-text="'모델이 맞음 ' + ((d.judge&&d.judge.adopt)||0) + ' · 정답 유지 ' + ((d.judge&&d.judge.reject)||0)"></span>
                            <span class="ds-badge ds-badge--warning" style="cursor:help;margin-left:6px" x-show="evalConsensus(d)==='adopt'" data-tip="'모델이 맞음' 합의 · 정답 교정 필요(정답셋 관리 · 정답셋 목록에 '교정 필요'로 표시)" data-tip-pos="top">교정 필요</span>
                            <span class="ds-badge ds-badge--error" style="cursor:help;margin-left:6px" x-show="evalConsensus(d)==='reject'" data-tip="'정답 유지' 합의 · 모델 오답 확정(프롬프트 개선 우선순위 근거)" data-tip-pos="top">모델 오답</span>
                          </td>
                        </tr>
                      </template>
                    </tbody></table></div>
                  </template>
                  <div x-show="!(goldenResult.detail||[]).length" class="text-xs text-muted">불일치 없음 · 건별 판정할 항목이 없습니다</div>
                  <button type="button" class="ds-btn ds-btn--secondary" style="margin-top:12px" x-on:click="selectMod('prompt')">원천 프롬프트 수정하러 가기 →</button>
                </div>
              </template>
            </div>
          </section>
          <!-- 모델별 비교: 같은 정답셋을 여러 모델에 실호출 -->
          <section class="panel"><div class="panel-hd"><b>모델별 비교</b><span class="meta">같은 정답셋으로 A·B 모델을 실호출 비교</span></div>
            <div class="panel-bd">
              <div class="filterbar" style="margin:0 0 12px">
                <span class="selctl selctl--a abslot"><span class="selctl__tag">A</span><span class="selctl__lbl">모델</span>
                  <select class="field" x-model="cmpA"><option value="">선택…</option><template x-for="m in availableModels" x-bind:key="'ca'+m"><option x-bind:value="m" x-text="m"></option></template></select></span>
                <span class="abvs">VS</span>
                <span class="selctl selctl--b abslot"><span class="selctl__tag">B</span><span class="selctl__lbl">모델</span>
                  <select class="field" x-model="cmpB"><option value="">선택…</option><template x-for="m in availableModels" x-bind:key="'cb'+m"><option x-bind:value="m" x-text="m"></option></template></select></span>
                <button type="button" class="ds-btn ds-btn--primary ds-btn--s-md" x-bind:disabled="cmpBusy || !cmpA || !cmpB || cmpA===cmpB" x-on:click="runCompare()" x-text="cmpBusy ? '비교 중… (모델별 전건 추출)' : '비교 실행'"></button>
                <span class="text-xs text-muted" x-show="cmpResult && !cmpResult.ok" x-text="cmpResult ? cmpResult.error : ''"></span>
              </div>
              <template x-if="cmpResult && cmpResult.ok && cmpCols.length">
                <div>
                  <div class="overflow-auto"><table class="ds-table"><thead><tr><th style="width:150px">항목</th>
                    <template x-for="(m,mi) in cmpCols" x-bind:key="'ch'+mi"><th><span class="selctl__tag" x-bind:style="mi ? 'background:#ff6a3d' : 'background:var(--ds-violet,#1e84ff)'" x-text="mi ? 'B' : 'A'"></span> <span x-text="m.model"></span> <span class="ds-badge ds-badge--success" x-show="m.model===cmpResult.best">★ best</span></th></template>
                  </tr></thead><tbody>
                    <tr><td class="text-ink">호출</td><template x-for="(m,mi) in cmpCols" x-bind:key="'cr'+mi"><td><span class="ds-badge" x-bind:class="m.real ? 'ds-badge--neutral' : 'ds-badge--warning'" x-bind:data-tip="m.real ? '실제 API 호출 결과' : '모의 응답 · API 키를 설정하면 실호출됩니다'" data-tip-pos="top" style="cursor:help" x-text="m.real ? (m.route||'실호출') : 'mock'"></span></td></template></tr>
                    <tr><td class="text-ink">등급 일치율 <span class="text-xs text-muted">(신뢰구간)</span></td><template x-for="(m,mi) in cmpCols" x-bind:key="'cg'+mi"><td>
                      <span class="abbar" x-bind:class="mi ? 'abbar--b' : ''">
                        <span class="abbar__track"><span class="abbar__fill" x-bind:style="'width:' + Math.max((m.grade_accuracy||0)*100, 3) + '%'"></span></span>
                        <b class="tnum" x-text="pctTxt(m.grade_accuracy)"></b><span class="abwin" x-show="cmpWin('grade_accuracy', mi)">▲</span>
                      </span>
                      <div class="text-xs text-muted tnum" style="margin-top:3px" x-text="'(' + ciOf(m.grade_accuracy, m.n) + ')'"></div></td></template></tr>
                    <tr><td class="text-ink">사유 일치</td><template x-for="(m,mi) in cmpCols" x-bind:key="'cj'+mi"><td><b class="tnum" x-text="pctTxt(m.reason_jaccard)"></b> <span class="abwin" x-show="cmpWin('reason_jaccard', mi)">▲</span></td></template></tr>
                    <tr><td class="text-ink">빈 결과</td><template x-for="(m,mi) in cmpCols" x-bind:key="'ce'+mi"><td class="tnum" x-text="pctTxt(m.empty_rate)"></td></template></tr>
                    <tr><td class="text-ink">비용($)</td><template x-for="(m,mi) in cmpCols" x-bind:key="'cc'+mi"><td class="tnum" x-text="m.cost_usd!=null ? ('$'+(Math.round(m.cost_usd*10000)/10000)) : '·'"></td></template></tr>
                  </tbody></table></div>
                  <div class="text-xs text-muted" style="margin-top:8px" x-show="(cmpResult.skipped||[]).length">비교 제외: <span x-text="(cmpResult.skipped||[]).map(s => s.model + ' (' + s.reason + ')').join(' · ')"></span></div>
                  <div class="text-xs text-muted" style="margin-top:4px">신뢰구간이 겹치면 우열 판단 보류 · 정답셋이 쌓일수록 오차가 줄어듭니다</div>
                </div>
              </template>
            </div>
          </section>
        </div>

      <!-- ═══ 모듈: 정답셋 관리(관리자) · 탭 바 + 정답셋 목록 + 학습 데이터 + 분석 ═══ -->
      <div x-show="mod === 'testset'" x-cloak class="w-full" style="margin-bottom:10px"><div class="evaltabs">
        <button type="button" x-bind:class="testTab==='status'?'sel':''" x-on:click="testTab='status'; loadGoldenStatus(); loadLearnReport()">현황 · 학습 반영</button>
        <button type="button" x-bind:class="testTab==='golden'?'sel':''" x-on:click="testTab='golden'; loadGoldenList()">정답셋 목록</button>
        <button type="button" x-bind:class="testTab==='data'?'sel':''" x-on:click="testTab='data'; loadLearnData()">학습 데이터</button>
      </div></div>
      <!-- 정답셋 관리 · 현황 탭: 정답 축적 현황 + 학습 반영(지금 실행 = 관리자) -->
      <div x-show="mod === 'testset' && testTab === 'status'" x-cloak class="w-full space-y-4">
          <!-- 골든 생성 현황: 누적·최근 배치·분류 필요 -->
          <section class="panel" data-fn x-init="loadGoldenStatus()"><div class="panel-hd"><b>테스트셋 현황</b><span class="meta">검수에서 '정확' 합의가 정답으로 쌓입니다 · 현재 프롬프트 <span class="tnum" x-text="verTxt"></span></span>
            <button type="button" class="ds-iconbtn ds-iconbtn--bordered ml-auto" x-on:click="loadGoldenStatus()" data-tip="새로고침" data-tip-pos="bottom" aria-label="골든 현황 새로고침"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-.9 4.5M20 5v6h-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          </div>
            <div class="panel-bd">
              <template x-if="goldenStatus">
                <div>
                  <div class="tiles" style="grid-template-columns:repeat(6,1fr);margin-bottom:12px">
                    <div class="tile"><div class="n tnum" x-text="(goldenStatus.reviewed&&goldenStatus.reviewed.contents)||0"></div><div class="t">검수 완료</div></div>
                    <div class="tile"><div class="n tnum" x-text="goldenStatus.total"></div><div class="t">정답 누적</div></div>
                    <div class="tile"><div class="n tnum" x-text="(goldenStatus.source_counts&&goldenStatus.source_counts.review)||0"></div><div class="t">검수로 확정</div></div>
                    <div class="tile"><div class="n tnum" x-text="(goldenStatus.source_counts&&goldenStatus.source_counts.manual)||0"></div><div class="t">직접 등록</div></div>
                    <div class="tile"><div class="n tnum" x-text="(goldenStatus.last_batch&&goldenStatus.last_batch.new)||0"></div><div class="t">신규 승격</div></div>
                    <div class="tile"><div class="n tnum" x-text="(goldenStatus.last_batch&&goldenStatus.last_batch.need_category)||0"></div><div class="t">분류 필요</div></div>
                  </div>
                  <!-- 반영 전 안내: 검수는 쌓였는데 정답이 0이면 "표시가 안 된다"로 오해 · 골든은 학습 반영 시 확정 -->
                  <div class="text-xs text-muted" style="margin-bottom:10px" x-show="!goldenStatus.total && goldenStatus.reviewed && goldenStatus.reviewed.contents">
                    검수 <b class="text-ink tnum" x-text="goldenStatus.reviewed.contents"></b>건이 쌓였고 아직 학습 반영 전입니다 · 정답 확정은 학습 반영 때 이뤄집니다(검수 목표 카드의 ⚡ 즉시 반영 또는 목표 일시 도달 시)
                  </div>
                  <div x-show="(goldenStatus.need_list||[]).length">
                    <div class="subhd" style="margin:4px 0 8px">분류 필요 <span class="meta">카테고리를 채우면 다음 학습 반영 때 정답으로 승격 (+5pt·미션)</span></div>
                    <div class="overflow-auto" style="max-height:180px"><table class="ds-table"><thead><tr><th>콘텐츠</th><th style="width:120px">서비스</th></tr></thead><tbody>
                      <template x-for="ng in goldenStatus.need_list" x-bind:key="ng.hash">
                        <tr><td x-text="ng.title || '(제목 없음)'"></td><td class="text-muted" x-text="ng.service"></td></tr>
                      </template>
                    </tbody></table></div>
                    <ul class="ds-bullets" style="margin-top:8px">
                      <li><b>콘텐츠 검수 · 검수 대상 콘텐츠</b>에서 해당 콘텐츠 상세를 열어 분류를 골라 주세요. <button type="button" class="copybtn" x-on:click="selectMod('create')">콘텐츠 검수 열기 →</button></li>
                    </ul>
                  </div>
                </div>
              </template>
              <div x-show="!goldenStatus" class="text-xs text-muted">검수가 쌓이고 학습 반영이 돌면 현황이 표시됩니다(아래 검수 목표 카드의 ⚡ 즉시 반영으로 바로 반영 가능)</div>
            </div>
          </section>
          <!-- 검수 목표 / 퀘스트 생성: 관리자가 지정한 일시(모델 버전 시한)에 학습 반영 1회 · 홈·사이드바 팀 퀘스트와 같은 원천 -->
          <section class="panel" data-fn x-init="loadLearnReport()"><div class="panel-hd"><b>검수 목표 / 퀘스트 생성</b><span class="meta">지정한 일시까지 모인 검수 의견이 새 버전 프롬프트에 반영됩니다</span>
            <button type="button" class="ds-btn ds-btn--primary ml-auto" style="height:30px;padding:0 12px" x-bind:disabled="learnBusy" x-on:click="runLearnBatch()" x-text="learnBusy ? '실행 중…' : '⚡ 즉시 반영'"></button>
          </div>
            <div class="panel-bd">
              <div style="display:flex;align-items:center;gap:var(--ds-space-4);flex-wrap:wrap">
                <span class="selctl" data-tip="모델 버전 시한: 이 일시에 검수 의견을 모아 반영하고 버전이 올라갑니다 · 도달 후에는 새 목표를 다시 생성" data-tip-pos="top"><span class="selctl__lbl">반영 일시</span>
                  <input type="datetime-local" class="field" x-model="learnNextAt" x-bind:disabled="!schedEditing" x-bind:min="new Date(Date.now()+60000).toISOString().slice(0,16)" style="min-width:190px">
                </span>
                <span class="selctl" data-tip="이 인원 이상이 '정확'으로 합의해야 정답셋으로 확정됩니다 · 팀 규모에 맞게 조정" data-tip-pos="top"><span class="selctl__lbl">확정 최소 인원</span>
                  <select class="field" x-model="goldenMinGood" x-bind:disabled="!schedEditing">
                    <template x-for="n in [1,2,3,4,5]" x-bind:key="n"><option x-bind:value="n" x-text="n + '명'" x-bind:selected="parseInt(goldenMinGood,10)===n"></option></template>
                  </select>
                </span>
                <span class="ds-badge ds-badge--intent tnum" x-show="nextBatchAt" style="cursor:help" data-tip="홈·사이드바의 팀 퀘스트(vN 마감 D-day)와 같은 일정입니다" data-tip-pos="top" x-text="'퀘스트 진행 중 · ' + fmtTs(nextBatchAt) + ' 반영'"></span>
                <span class="ds-badge ds-badge--neutral" x-show="!nextBatchAt" style="cursor:help" data-tip="목표 일시를 지정하면 홈·사이드바에 팀 퀘스트(D-day)가 생깁니다" data-tip-pos="top">목표 미설정 · 퀘스트를 생성하세요</span>
              </div>
              <div style="display:flex;align-items:center;gap:var(--ds-space-2);margin-top:12px;flex-wrap:wrap">
                <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-sm" x-show="!schedEditing" x-on:click="schedEdit()" x-text="nextBatchAt ? '수정' : '퀘스트 생성'"></button>
                <button type="button" class="ds-btn ds-btn--ghost ds-btn--s-sm" x-show="!schedEditing && nextBatchAt" x-on:click="deleteQuest()" data-tip="반영 예약을 해제합니다 · 검수 의견과 점수는 그대로 남습니다" data-tip-pos="top">퀘스트 삭제</button>
                <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-show="schedEditing" x-on:click="saveLearnSched()">저장</button>
                <span class="text-xs" style="color:var(--ds-success)" x-text="learnSchedMsg"></span>
                <span x-show="learnReport && learnReport.ts" style="width:1px;height:14px;background:var(--ds-hairline)" aria-hidden="true"></span>
                <template x-if="learnReport && learnReport.ts">
                  <span class="text-xs text-muted">최근 반영: 정답 확정 <b class="text-ink tnum" x-text="(learnReport.golden&&learnReport.golden.confirmed)||0"></b>
                    · 신규 <b class="text-ink tnum" x-text="'+' + ((learnReport.golden&&learnReport.golden.new)||0)"></b>
                    · 분류 필요 <b class="text-ink tnum" x-text="(learnReport.golden&&learnReport.golden.need_category)||0"></b>
                    · 의견 갈림 <b class="text-ink tnum" x-text="(learnReport.golden&&learnReport.golden.disagree)||0"></b>
                    <span x-show="learnReport.golden && learnReport.golden.demoted"> · 정답 제외 <b class="text-ink tnum" x-text="learnReport.golden.demoted"></b></span>
                    · 정답 일치율 <b class="text-ink tnum" x-text="pctTxt(learnReport.grade_accuracy)"></b>
                    <span x-show="learnReport.improve_delta != null && !(learnReport.improve && learnReport.improve.reverted)" class="tnum" x-bind:style="(learnReport.improve_delta||0) >= 0 ? 'color:var(--ds-success-deep)' : 'color:var(--ds-error-deep)'" x-text="' · 보정 효과 ' + deltaTxt(learnReport.improve_delta)"></span>
                    <span x-show="learnReport.improve && learnReport.improve.reverted" class="ds-badge ds-badge--warning" style="cursor:help;margin-left:4px" x-bind:data-tip="(learnReport.improve && learnReport.improve.revert_reason) || '정합성 악화로 이번 보정을 반영하지 않았습니다'" data-tip-pos="top">보정 미반영</span>
                    <span x-show="learnReport.eval && learnReport.eval.grade_ci" class="tnum" x-text="learnReport.eval && learnReport.eval.grade_ci ? (' (신뢰구간 ' + pctTxt(learnReport.eval.grade_ci.lo) + '~' + pctTxt(learnReport.eval.grade_ci.hi) + ' · 표본 ' + learnReport.eval.grade_ci.n + '건)') : ''"></span>
                  </span>
                </template>
              </div>
              <ul class="ds-bullets" style="margin-top:10px">
                <li>의견을 모아 <b>지정한 일시(모델 버전 시한)</b>에 한 번에 반영해 결과가 흔들리지 않게 합니다 · 반영 후 목표는 소진되고 새 퀘스트를 생성합니다.</li>
                <li>'정확' 합의는 <b>정답셋</b>으로 쌓이고, 바뀐 프롬프트는 정답셋으로 다시 평가합니다.</li>
                <li>'⚡ 즉시 반영'은 목표 일시와 무관하게 지금까지 모인 의견을 바로 반영합니다.</li>
              </ul>
              <!-- 학습 보정 지시(단계별 · 중복 정리) -->
              <div class="subhd" style="margin:12px 0 8px" x-show="metaResults && ['extract','analyze','review','judge'].some(st => metaResults[st] && (metaResults[st].directive || (metaResults[st].ambiguities||[]).length))">학습 보정 지시 <span class="meta">검수 피드백을 정리해 각 단계 프롬프트에 병기합니다</span></div>
              <template x-for="stage in ['extract','analyze','review','judge']" x-bind:key="stage">
                <div x-show="metaResults && metaResults[stage] && (metaResults[stage].directive || (metaResults[stage].ambiguities||[]).length)" class="metarow">
                  <span class="ds-badge ds-badge--neutral" style="cursor:help" x-bind:data-tip="({extract:'① 리드문·엔티티 호출에 병기', analyze:'③ 인텐트·④ 카테고리 호출에 병기', review:'품질 판정 프롬프트에 병기', judge:'법령·유통 판정 프롬프트에 병기'})[stage]" data-tip-pos="top" x-text="({extract:'추출',analyze:'분석',review:'검수',judge:'판정'})[stage]"></span>
                  <div style="flex:1;min-width:0">
                    <div class="metarow__dir" x-text="metaResults&&metaResults[stage]?metaResults[stage].directive:''"></div>
                    <template x-for="a in (metaResults&&metaResults[stage]?metaResults[stage].ambiguities:[])" x-bind:key="a">
                      <div class="metarow__amb">⚠ 의견 갈림(가이드 명확화 필요): <span x-text="a"></span></div>
                    </template>
                  </div>
                </div>
              </template>
            </div>
          </section>
      </div><!-- /정답셋 관리 · 현황 -->


      <div x-show="mod === 'testset' && testTab === 'golden'" x-cloak class="w-full space-y-4">
        <section class="panel" x-show="backend !== 'supabase' || (adminData && adminData.isAdmin)" x-init="loadGoldenList()"><div class="panel-hd"><b>정답셋(골든) 목록</b>
          <span class="meta" x-text="goldenList ? (goldenList.total + '건 · 검수로 확정 ' + ((goldenList.source_counts&&goldenList.source_counts.review)||0) + ' · 직접 등록 ' + ((goldenList.source_counts&&goldenList.source_counts.manual)||0)) : ((adminData&&adminData.goldenCount?adminData.goldenCount+'건 등록됨':'미등록'))"></span>
          <span class="ml-auto" style="display:flex;gap:6px">
          <button type="button" class="ds-iconbtn ds-iconbtn--bordered" x-show="goldenList && goldenList.items && goldenList.items.length" x-on:click="exportGolden()" data-tip="엑셀 다운로드 (CSV)" data-tip-pos="bottom" aria-label="정답셋 엑셀 다운로드"><svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg></button>
          <button type="button" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="loadGoldenList()" data-tip="새로고침" data-tip-pos="bottom" aria-label="정답셋 목록 새로고침"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-.9 4.5M20 5v6h-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          </span>
        </div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:10px"><li>정답은 <b>검수 '정확' 합의</b>가 학습 반영 때 누적 승격됩니다.</li><li>평가와 어긋나 <b>오류 의심 · 교정 필요</b> 표시된 항목은 확인 후 제거하세요(정답 오류는 모델 순위를 뒤집습니다).</li></ul>
            <template x-if="goldenList && goldenList.items && goldenList.items.length">
              <div style="margin-top:12px">
              <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
                <span class="selctl__lbl" data-tip="정답이 확정될 당시의 초안 모델 · 정답 자체는 모델과 무관한 사람 확정값" data-tip-pos="top">유래 모델</span>
                <button type="button" class="srcfilter__chip" x-bind:class="goldenModel==='' ? 'sel' : ''" x-on:click="goldenModel=''">전체</button>
                <template x-for="m in goldenModelList" x-bind:key="'gm'+m"><button type="button" class="srcfilter__chip" x-bind:class="goldenModel===m ? 'sel' : ''" x-on:click="goldenModel=m" x-text="m || '(모델 미기록)'"></button></template>
              </div>
              <div class="overflow-auto" style="max-height:300px"><table class="ds-table"><thead><tr><th>콘텐츠</th><th style="width:56px">등급</th><th>카테고리</th><th style="width:150px">유래 모델</th><th style="width:56px">버전</th><th style="width:74px">출처</th><th style="width:60px"></th></tr></thead><tbody>
                <template x-for="g in filteredGolden" x-bind:key="g.hash">
                  <tr>
                    <td><span x-text="g.title || '(제목 없음)'"></span> <span class="ds-badge ds-badge--error" style="cursor:help" x-show="g.flagged && !g.fix_needed" data-tip="최근 평가에서 모델과 불일치 · 정답 오류 후보" data-tip-pos="top">오류 의심</span> <span class="ds-badge ds-badge--warning" style="cursor:help" x-show="g.fix_needed" data-tip="평가 판정에서 '모델이 맞음' 합의 · 모델 결과가 맞다고 확정된 정답(제거 후 재등록 또는 검수 재확정 필요)" data-tip-pos="top">교정 필요</span></td>
                    <td><span class="ds-badge" style="cursor:help" x-bind:class="g.grade==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-bind:data-tip="termDef('grade', g.grade)" data-tip-pos="top" x-text="g.grade || '·'"></span></td>
                    <td><template x-for="c in (g.category||[])" x-bind:key="c"><span class="ds-badge ds-badge--category" style="cursor:help;margin:1px" x-bind:data-tip="termDef('category', c)" data-tip-pos="top" x-text="catKo(c)"></span></template></td>
                    <td class="text-muted" x-text="g.model || '·'"></td>
                    <td class="tnum" x-text="g.version ? ('v' + g.version) : '·'"></td>
                    <td><span class="ds-badge ds-badge--neutral" x-text="g.source === 'manual' ? '직접' : '검수'"></span></td>
                    <td><button type="button" class="copybtn" x-on:click="removeGolden(g.hash)">제거</button></td>
                  </tr>
                </template>
              </tbody></table></div>
              </div>
            </template>
          </div>
        </section>
      </div><!-- /정답셋 목록 -->
      <!-- 학습 데이터: 항목별 카드(요약 | 내보내기 | 소요 산정 | 커버리지·신뢰도 | 오류 후보) · 용어는 호버 정의 -->
      <div x-show="mod === 'testset' && testTab === 'data'" x-cloak class="w-full">
        <div x-show="backend !== 'supabase' || (adminData && adminData.isAdmin)" x-init="loadLearnData()" class="space-y-4">
        <section class="panel" data-fn>
            <div class="panel-hd"><b>학습 데이터 현황</b><span class="meta">특화 LLM 학습데이터 요약 · 용어는 항목에 마우스를 올리면 설명됩니다</span>
              <button type="button" class="ds-btn ds-btn--secondary ml-auto" style="height:30px;padding:0 12px" x-bind:disabled="learnDataBusy" x-on:click="loadLearnData()" x-text="learnDataBusy ? '집계 중…' : '새로고침'"></button>
            </div>
            <div class="panel-bd">
              <template x-if="learnData">
                <div>
                  <div class="tiles" style="grid-template-columns:repeat(5,1fr)">
                    <div class="tile" style="cursor:help" data-tip="사람 검수 합의로 확정된 정답 데이터 · 평가와 학습의 기준" data-tip-pos="top"><div class="n tnum" x-text="learnData.golden_n"></div><div class="t">골든(정답)</div></div>
                    <div class="tile" style="cursor:help" data-tip="카테고리(클래스)별 목표 8건(SetFit 2022)을 채운 클래스 수" data-tip-pos="top"><div class="n tnum" x-text="learnData.covered + '/' + learnData.class_total"></div><div class="t">클래스 충족</div></div>
                    <div class="tile" style="cursor:help" data-tip="검수자 간 판정 일치도(Krippendorff's alpha) · 참고 지표(임계값 기계 적용 금지 · Artstein & Poesio 2008)" data-tip-pos="top"><div class="n tnum" x-text="learnData.alpha == null ? '·' : learnData.alpha"></div><div class="t">일치도 α</div></div>
                    <div class="tile" style="cursor:help" data-tip="최근 골든 평가에서 모델과 정답이 어긋난 건(기계 플래그 → 사람 확정 · Northcutt 2021)" data-tip-pos="top"><div class="n tnum" x-text="(learnData.label_flags||[]).length"></div><div class="t">오류 의심</div></div>
                    <div class="tile" style="cursor:help" data-tip="같은 콘텐츠에 검수자 판정이 갈린 건 · 재검토 우선 대상" data-tip-pos="top"><div class="n tnum" x-text="learnData.split_n"></div><div class="t">의견 불일치</div></div>
                  </div>
                  <div class="text-xs text-muted" style="margin-top:12px" x-show="learnData.acc_ci">골든 정합성 <b class="text-ink" style="cursor:help" data-tip="정답셋과 현재 모델의 등급 일치율 · 95% 신뢰구간(Miller 2024): 표본이 적을수록 구간이 넓어집니다" data-tip-pos="top" x-text="ciTxt(learnData.acc_ci)"></b> · 신뢰구간이 겹치는 비교는 판정 보류</div>
                </div>
              </template>
              <div x-show="!learnData" class="text-xs text-muted">집계를 불러오는 중이거나, 관리자 권한이 필요합니다</div>
            </div>
        </section>
        <template x-if="learnData">
        <section class="panel" data-fn>
            <div class="panel-hd"><b>데이터셋 내보내기</b><span class="meta">검수 결과를 학습용 JSONL·소요서·핸드오프 번들로</span></div>
            <div class="panel-bd">
              <div style="display:flex;gap:var(--ds-space-2);flex-wrap:wrap">
                <button type="button" class="ds-btn ds-btn--secondary" style="height:32px" data-tip="지도 미세조정(Supervised Fine-Tuning) 학습쌍 · 콘텐츠 → 확정 메타" data-tip-pos="top" x-on:click="exportLearn('sft')">SFT 내보내기 <span class="tnum" x-text="'(' + learnData.extractable.sft + ')'"></span></button>
                <button type="button" class="ds-btn ds-btn--secondary" style="height:32px" data-tip="선호 학습(DPO)용 교정 전/후 쌍 · 상세 화면에서 교정할수록 쌓입니다" data-tip-pos="top" x-on:click="exportLearn('dpo')">선호쌍(DPO) 내보내기 <span class="tnum" x-text="'(' + learnData.extractable.dpo + ')'"></span></button>
                <button type="button" class="ds-btn ds-btn--secondary" style="height:32px" data-tip="판정 이유(rationale) 데이터 · 근거 증류 학습(Distilling Step-by-Step)용" data-tip-pos="top" x-on:click="exportLearn('rationale')">판단근거 내보내기 <span class="tnum" x-text="'(' + learnData.extractable.rationale + ')'"></span></button>
                <button type="button" class="ds-btn ds-btn--secondary" style="height:32px" data-tip="골든마다 사람 판단의 궤적(판정 노트·REAP 사유·교정 전/후·합의)을 결속 · '왜 이 정답인가' 검증용" data-tip-pos="top" x-on:click="exportLearn('knowhow')">노하우 내보내기 <span class="tnum" x-text="'(' + (learnData.extractable.knowhow == null ? '·' : learnData.extractable.knowhow) + ')'"></span></button>
                <button type="button" class="ds-btn ds-btn--secondary" style="height:32px" x-on:click="exportSpec()" data-tip="현재 수치·기준치·권장 스펙을 한 문서로(파인튜닝 소요서 .md)" data-tip-pos="top">소요서(.md) 생성</button>
                <button type="button" class="ds-btn ds-btn--primary" style="height:32px" x-on:click="exportHandoff()" data-tip="모델러 전달용 한 파일(.zip): 학습데이터 3종 + 노하우·백로그 + 소요서 + 프롬프트·사전 스냅샷 + manifest(건수·체크섬·직전 발행 대비 증분)" data-tip-pos="top">핸드오프 번들(.zip)</button>
              </div>
              <div class="text-xs text-muted" style="margin-top:8px" x-show="learnData.knowhow">노하우 결속 <b class="text-ink tnum" x-text="(learnData.knowhow ? learnData.knowhow.n : 0) + '/' + learnData.golden_n + '건'"></b> · 골든에 사람 판단 사유(검수 노트·교정 이력)가 연결된 비율 · 결속률이 낮으면 검수 노트 작성을 독려하세요</div>
            </div>
        </section>
        </template>
        <template x-if="learnData">
        <section class="panel" data-fn>
            <div class="panel-hd"><b>소요 산정</b><span class="meta">용도별 목표 대비 보유 · 기준치는 논문 근거</span></div>
            <div class="overflow-auto"><table class="ds-table"><thead><tr><th>용도</th>
              <th style="cursor:help" data-tip="논문 근거 목표 건수" data-tip-pos="top">기준</th>
              <th style="cursor:help" data-tip="현재 확보한 건수" data-tip-pos="top">보유</th>
              <th style="cursor:help" data-tip="목표까지 남은 건수" data-tip-pos="top">부족</th>
              <th style="cursor:help" data-tip="기준치의 출처 논문 · 전체 서지는 LEARNING_DESIGN.md" data-tip-pos="top">근거</th></tr></thead><tbody>
              <template x-for="r in learnData.requirements" x-bind:key="r.kind">
                <tr><td class="text-ink" x-text="r.kind"></td><td class="tnum" x-text="r.target"></td><td class="tnum" x-text="r.have"></td>
                  <td class="tnum" x-bind:class="r.lack > 0 ? 'text-ink' : ''" x-text="r.lack"></td>
                  <td class="text-xs text-muted" x-text="r.basis"></td></tr>
              </template>
            </tbody></table></div>
            <ul class="ds-bullets" style="margin:10px 16px 14px">
              <li>기준치 출처: 클래스당 8(SetFit 2022) · SFT 1k(LIMA 2023) · 운영급 13.5k(Llama Guard 2023) · 선호쌍 33k(InstructGPT 2022 참고 상한) · 평가셋 100(tinyBenchmarks 2024).</li>
            </ul>
        </section>
        </template>
        <template x-if="learnData">
        <div class="grid grid-cols-2 gap-4">
          <section class="panel" data-fn style="margin:0">
            <div class="panel-hd"><b>클래스 커버리지</b><span class="meta" style="cursor:help" data-tip="클래스당 8건이면 분류 부트스트랩이 가능(SetFit 2022)" data-tip-pos="top">목표 <b class="text-ink" x-text="learnData.per_class_target + '건/클래스'"></b></span></div>
            <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th style="cursor:help" data-tip="콘텐츠 카테고리 대분류" data-tip-pos="top">Tier1</th><th>보유</th><th>부족</th></tr></thead><tbody>
              <template x-for="c in [...learnData.coverage].sort((a,b)=>b.lack-a.lack)" x-bind:key="c.cls">
                <tr><td x-text="catBoth(c.cls)"></td><td class="tnum" x-text="c.have"></td><td class="tnum" x-bind:class="c.lack>0?'text-ink':''" x-text="c.lack"></td></tr>
              </template>
            </tbody></table></div>
          </section>
          <section class="panel" data-fn style="margin:0">
            <div class="panel-hd"><b>검수자 신뢰도</b><span class="meta">합의 일치 + 골드 정확도 + 통계 추정</span></div>
            <div class="overflow-auto" style="max-height:280px"><table class="ds-table"><thead><tr><th>검수자</th><th style="cursor:help" data-tip="검수한 콘텐츠 수" data-tip-pos="top">검수</th>
              <th style="cursor:help" data-tip="다수 의견과 같은 판정을 낸 비율" data-tip-pos="top">합의 일치</th>
              <th style="cursor:help" data-tip="정답을 아는 검증 문항의 정확도 · 문항 5개 이상일 때 표시(점수 배율에 반영)" data-tip-pos="top">골드</th>
              <th style="cursor:help" data-tip="통계 모델(Dawid-Skene 1979)이 추정한 검수자 오류율 · 참고 지표" data-tip-pos="top">EM 오류율</th></tr></thead><tbody>
              <template x-for="r in learnData.reviewers" x-bind:key="r.reviewer">
                <tr><td class="text-ink" x-text="r.reviewer"></td><td class="tnum" x-text="r.n"></td>
                  <td class="tnum" x-text="r.agree_rate == null ? '·' : pctTxt(r.agree_rate)"></td>
                  <td class="tnum" x-text="r.gold_n >= 5 ? (pctTxt(r.gold_acc) + ' (' + r.gold_n + ')') : ('· (' + (r.gold_n||0) + ')')"></td>
                  <td class="tnum" x-text="r.ds_error == null ? '·' : pctTxt(r.ds_error)"></td></tr>
              </template>
              <template x-if="!learnData.reviewers.length"><tr><td colspan="5" class="text-muted">검수 데이터가 쌓이면 표시됩니다</td></tr></template>
            </tbody></table></div>
          </section>
        </div>
        </template>
        <template x-if="learnData && learnData.dict_gap && (((learnData.dict_gap.intent)||[]).length || ((learnData.dict_gap.category)||[]).length)">
          <section class="panel"><div class="panel-hd"><b>사전 갭</b><span class="meta">모델 산출이 사전과 안 맞아 드롭된 값 · 사전 별칭 추가 또는 프롬프트 보정 후보</span></div>
            <div class="panel-bd">
              <div class="flex flex-wrap gap-1" style="align-items:center" x-show="((learnData.dict_gap.intent)||[]).length">
                <span class="text-xs text-muted" style="width:72px">인텐트</span>
                <template x-for="g in (learnData.dict_gap.intent||[])" x-bind:key="'gi'+g[0]"><span class="ds-badge ds-badge--intent" style="cursor:help" x-bind:data-tip="'사전에 없는 산출값 · 드롭 ' + g[1] + '회'" data-tip-pos="top" x-text="g[0] + ' (' + g[1] + ')'"></span></template>
              </div>
              <div class="flex flex-wrap gap-1" style="align-items:center;margin-top:6px" x-show="((learnData.dict_gap.category)||[]).length">
                <span class="text-xs text-muted" style="width:72px">카테고리</span>
                <template x-for="g in (learnData.dict_gap.category||[])" x-bind:key="'gc'+g[0]"><span class="ds-badge ds-badge--category" style="cursor:help" x-bind:data-tip="'사전 스냅 실패 산출값 · 드롭 ' + g[1] + '회'" data-tip-pos="top" x-text="g[0] + ' (' + g[1] + ')'"></span></template>
              </div>
              <div class="text-xs text-muted" style="margin-top:8px" x-show="learnData.dict_gap.retries && (learnData.dict_gap.retries.intent || learnData.dict_gap.retries.category)" x-text="'전량 드롭 재요청 · 인텐트 ' + ((learnData.dict_gap.retries&&learnData.dict_gap.retries.intent)||0) + '회 · 카테고리 ' + ((learnData.dict_gap.retries&&learnData.dict_gap.retries.category)||0) + '회'"></div>
            </div>
          </section>
        </template>
        <template x-if="learnData && (learnData.label_flags||[]).length">
        <section class="panel" data-fn>
            <div class="panel-hd"><b>정답 오류 후보</b><span class="meta">최근 평가에서 모델·정답 불일치 · 확인 후 오답이면 제거</span></div>
            <div class="overflow-auto" style="max-height:220px"><table class="ds-table"><thead><tr><th>콘텐츠</th><th>정답</th><th>모델</th><th style="width:60px"></th></tr></thead><tbody>
              <template x-for="f in learnData.label_flags" x-bind:key="f.hash">
                <tr><td x-text="f.title || f.hash"></td><td class="tnum" x-text="f.expected"></td><td class="tnum" x-text="f.got"></td>
                  <td><button type="button" class="copybtn" x-on:click="removeGolden(f.hash)">제거</button></td></tr>
              </template>
            </tbody></table></div>
        </section>
        </template>
        </div>
      </div><!-- /학습 데이터 -->

      <!-- 콘텐츠 검수 · 원본 목록: 결과 원본을 가공 없이 빠르게 -->
      <div x-show="mod === 'create' && createTab === 'raw'" x-cloak class="w-full space-y-4">
        <!-- 모델 선택(별도 영역 · selctl 정책): 아래 목록의 기준 모델 지정 -->
        <section class="panel" data-fn><div class="panel-hd"><b>모델 선택</b><span class="meta">아래 목록의 기준 모델 지정</span></div>
          <div class="panel-bd">
            <div class="filterbar" style="margin:0 16px">
              <span class="selctl abslot"><span class="selctl__tag">모델</span>
                <select class="field" x-model="rawModel">
                  <option value="">전체</option>
                  <template x-for="m in rawModels" x-bind:key="'rm'+m"><option x-bind:value="m" x-text="m"></option></template>
                </select>
              </span>
            </div>
            <ul class="ds-bullets" style="margin:10px 16px 0">
              <li>선택한 모델이 <b>초기 판정한 초안</b>만 목록에 표시됩니다(누적 · 모델별 정답셋의 재료).</li>
              <li>검수 합의는 <b>이 모델의 정답셋</b>으로 쌓입니다.</li>
              <li>버전 간 비교는 <b>결과 비교</b> 탭에서 합니다.</li>
            </ul>
          </div>
        </section>
        <section class="panel" data-fn><div class="panel-hd"><b>검수 대상 콘텐츠</b><span class="meta tnum" x-text="rawData ? (rawData.n + '건 · 최근순') : ''"></span>
          <span class="ml-auto" style="display:flex;gap:var(--ds-space-2);align-items:center">
            <button type="button" class="copybtn" x-on:click="exportDash()" data-tip="전체 결과 CSV 다운로드" data-tip-pos="bottom"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12m-4-4 4 4 4-4M5 21h14"/></svg>CSV</button>
            <button type="button" class="copybtn" x-on:click="openReport()" data-tip="브라우저용 HTML 리포트 열기" data-tip-pos="bottom"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3h7v7M21 3l-9 9M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>리포트</button>
            <button type="button" class="copybtn" x-show="opsAdmin" x-on:click="openBulk()" data-tip="여러 콘텐츠에 검수 담당자를 한 번에 배정" data-tip-pos="bottom"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6M15 11a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5M3 20v-1a5 5 0 0 1 5-5h2a5 5 0 0 1 5 5v1M17 14a4 4 0 0 1 4 4v2"/></svg>일괄 배정</button>
            <button type="button" class="ds-iconbtn ds-iconbtn--bordered" x-on:click="loadRaw()" data-tip="새로고침" data-tip-pos="bottom" aria-label="원본 목록 새로고침"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-.9 4.5M20 5v6h-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          </span>
        </div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:11px"><li>행 클릭 = <b>검수 상세</b>(판정·교정) · 상세에서 A/S/←→ 단축키와 자동 다음 이동을 쓸 수 있습니다 · JSON 원문은 행 우측 <b>{ }</b>.</li><li x-show="rawModel">현재 <b class="text-ink" x-text="rawModel"></b> 초안만 표시 중입니다.</li></ul>
            <!-- 필터: 검색 + 등급/서비스/검수 상태 -->
            <div class="filterbar">
              <input class="field" placeholder="제목·카테고리·사유 검색" x-model="rawQ">
              <select class="field" style="width:auto" x-model="rawGrade"><option value="">등급 전체</option><option value="G">G</option><option value="R">R</option></select>
              <select class="field" style="width:auto" x-model="rawSvc"><option value="">서비스 전체</option><template x-for="sv in rawSvcs" x-bind:key="sv"><option x-bind:value="sv" x-text="sv"></option></template></select>
              <select class="field" style="width:auto" x-model="rawRev"><option value="">검수 전체</option><option value="todo">미검수</option><option value="done">검수 완료</option></select>
              <span class="text-xs text-muted tnum" x-text="rawFiltered.length + ' / ' + ((rawData&&rawData.n)||0) + '건'"></span>
              <span class="ds-badge ds-badge--neutral" style="cursor:help" data-tip="정렬 기준 · 최근 실행순. 의견 갈림(불일치)·YELLOW 는 행 배지로 표시됩니다" data-tip-pos="top">최근순</span>
            </div>
            <div class="overflow-auto" style="max-height:420px"><table class="ds-table"><thead><tr><th style="width:52px">등급</th><th>콘텐츠</th><th style="width:100px">서비스</th><th>카테고리</th><th>사유</th><th style="width:130px">검수</th><th style="width:150px" x-show="assignAdmin" data-tip="검수 담당자 배정 · 배정 시 담당자에게만 노출됩니다" data-tip-pos="top">담당</th></tr></thead><tbody>
              <template x-for="r in rawFiltered" x-bind:key="r.hash">
                <tr style="cursor:pointer" role="button" tabindex="0" x-bind:class="rawSel && rawSel.hash === r.hash ? 'is-sel' : ''" x-on:click="openRawDetail(r)" x-on:keydown.enter="openRawDetail(r)">
                  <td><span class="ds-badge" style="cursor:help" x-bind:class="r.grade==='G' ? 'ds-badge--success' : 'ds-badge--neutral'" x-bind:data-tip="termDef('grade', r.grade)" data-tip-pos="right" x-text="r.grade||'·'"></span></td>
                  <td class="text-ink"><span x-text="r.title || '(제목 없음)'"></span>
                    <span class="ds-badge ds-badge--yellow" style="cursor:help;margin-left:4px" x-show="r.review==='yellow'" data-tip="AI 확신이 낮아 사람 확인이 필요한 콘텐츠" data-tip-pos="top">YELLOW</span>
                    <span class="ds-badge ds-badge--reason" style="margin-left:4px" x-show="r.split" data-tip="검수자 의견이 갈려 재검토가 필요합니다 · 우선 검수 대상" data-tip-pos="top">재검토 필요</span>
                  </td>
                  <td class="text-muted" x-text="r.service"></td>
                  <td><template x-for="c in (r.category||[])" x-bind:key="c"><span class="ds-badge ds-badge--category" style="cursor:help;margin:1px" x-bind:data-tip="termDef('category', c)" data-tip-pos="top" x-text="catKo(c)"></span></template><span x-show="!(r.category||[]).length" class="text-xs text-muted">·</span></td>
                  <td><template x-for="c in (r.reasons||[])" x-bind:key="c"><span class="ds-badge ds-badge--reason" style="cursor:help;margin:1px" x-bind:data-tip="termDef('reason', c)" data-tip-pos="top" x-text="reasonBoth(c)"></span></template><span x-show="!(r.reasons||[]).length" class="text-xs text-muted">·</span></td>
                  <td x-on:click.stop>
                    <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-show="!myVerdict(r.fb)" x-on:click="openRawDetail(r)">검수하기</button>
                    <span class="text-xs text-muted tnum" x-show="myVerdict(r.fb)" style="cursor:pointer" x-on:click="openRawDetail(r)" data-tip="완료 · 클릭하면 상세에서 수정" data-tip-pos="top" x-text="'✓ ' + (r.fb && r.fb.ts ? fmtTs(r.fb.ts) : '완료')"></span>
                    <button type="button" class="copybtn" style="margin-left:6px" x-on:click="rawSel = (rawSel && rawSel.hash === r.hash) ? null : r" data-tip="JSON 원문 보기(표 아래 펼침)" data-tip-pos="top">{ }</button>
                  </td>
                  <td x-show="assignAdmin" x-on:click.stop>
                    <template x-for="nm in assigneeNames(r)" x-bind:key="nm"><span class="ds-badge ds-badge--intent" style="margin:1px" x-text="nm"></span></template>
                    <span x-show="(r.assignees||[]).length" class="text-xs text-muted tnum" style="margin-left:2px" x-text="'· 최소 ' + (r.min_reviewers||1) + '명'"></span>
                    <span x-show="!(r.assignees||[]).length" class="text-xs text-muted">미배정</span>
                    <button type="button" class="copybtn" style="margin-left:6px" x-on:click="openAssign(r)" x-text="(assignSel && assignSel.hash===r.hash) ? '닫기' : '지정'"></button>
                  </td>
                </tr>
              </template>
            </tbody></table>
            <div x-show="modBusy && !(rawData && rawData.items && rawData.items.length)" class="text-xs text-muted" style="padding:10px">목록을 불러오는 중…</div>
            <div x-show="!modBusy && !(rawData && rawData.items && rawData.items.length)" class="text-xs text-muted" style="padding:10px">데이터가 없습니다 · <b class="text-ink">콘텐츠 관리</b>에서 콘텐츠를 추가하세요</div>
            </div>
            <div x-show="rawSel" style="margin-top:10px">
              <div class="text-xs text-muted" style="margin-bottom:6px">JSON 원문 · <b class="text-ink" x-text="rawSel ? (rawSel.title || rawSel.hash) : ''"></b></div>
              <pre class="tbox" style="white-space:pre-wrap;font-size:11px;max-height:280px;overflow:auto" x-text="rawSel ? JSON.stringify({item_meta: rawSel.item_meta, quality_meta: rawSel.quality_meta}, null, 2) : ''"></pre>
            </div>
            <!-- 검수 담당자 배정 편집(관리자) · 배정 시 담당자에게만 검수 큐 노출(배타적) -->
            <div x-show="assignSel && assignAdmin" class="tbox" style="margin-top:10px;padding:12px">
              <div class="text-xs text-muted" style="margin-bottom:8px">검수 담당자 배정 · <b class="text-ink" x-text="assignSel ? (assignSel.title || assignSel.hash) : ''"></b></div>
              <div x-show="!assignMembers.length" class="text-xs text-muted" style="padding:4px 0">배정 가능한 팀원이 없습니다 · <b class="text-ink">팀 관리</b>에서 멤버를 초대하세요</div>
              <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px">
                <template x-for="m in assignMembers" x-bind:key="'asg'+m.id">
                  <label class="pickchip" x-bind:class="assignPick.includes(m.id) ? 'on' : ''">
                    <input type="checkbox" x-bind:checked="assignPick.includes(m.id)" x-on:change="toggleAssign(m.id)">
                    <span x-text="m.name"></span>
                  </label>
                </template>
              </div>
              <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <label class="text-xs text-muted" style="display:inline-flex;align-items:center;gap:6px">최소 검수인원
                  <input type="number" class="field" style="width:70px" min="1" x-bind:max="Math.max(1, assignPick.length)" x-model.number="assignMin">
                  <span class="tnum" x-text="'/ 배정 ' + assignPick.length + '명'"></span>
                </label>
                <span class="ml-auto" style="display:flex;gap:8px">
                  <button type="button" class="ds-btn ds-btn--outline ds-btn--s-sm" x-on:click="assignSel=null">취소</button>
                  <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-bind:disabled="assignBusy" x-on:click="saveAssign()" x-text="assignBusy ? '저장 중…' : (assignPick.length ? '배정 저장' : '배정 해제')"></button>
                </span>
              </div>
              <p class="text-xs text-muted" style="margin-top:8px;line-height:1.5">배정하면 이 콘텐츠는 <b class="text-ink">담당자에게만</b> 검수 큐에 노출됩니다. 최소 검수인원 N명이 검수하면 통과로 집계됩니다. 아무도 선택하지 않고 저장하면 배정이 해제되어 전체 공개(오픈 큐)로 돌아갑니다.</p>
            </div>
          </div>
        </section>
      </div><!-- /콘텐츠 검수 · 원본 목록 -->

      <!-- ═══ 모듈: 팀 관리 (멀티테넌시 · 팀 모드 전용) ═══ -->
      <div x-show="mod === 'admin'" x-cloak class="w-full space-y-4">
        <section class="panel" x-show="backend !== 'supabase'"><div class="panel-hd"><b>팀 관리</b><span class="meta">로컬 단독 실행</span></div>
          <div class="panel-bd"><ul class="ds-bullets">
            <li>팀 기능(팀 생성 · 초대 코드 · 멤버 관리)은 <b>팀 모드(공유 서버)</b>에서 동작합니다.</li>
            <li>지금은 로컬 단독 실행이라 팀 없이 <b>개인 검수</b>로 진행됩니다 · 검수·평가·정답셋 등 나머지 기능은 동일하게 사용할 수 있습니다.</li>
          </ul></div>
        </section>
        <template x-if="backend === 'supabase'">
        <div class="space-y-4">
        <section class="panel" data-fn><div class="panel-hd"><b>새 팀 만들기</b><span class="ds-badge ds-badge--neutral">관리자</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:11px">
              <li>새 팀을 만들면 <b>초대 코드</b>가 발급됩니다.</li>
              <li>팀원은 가입 시 이 코드로 참가합니다(팀 생성은 관리자만).</li>
            </ul>
            <div style="display:flex;gap:var(--ds-space-2);flex-wrap:wrap;align-items:center">
              <input class="field" style="flex:1;min-width:200px;height:38px" placeholder="팀 이름 (예: 콘텐츠검수팀)" x-model="newTeamName" x-on:keydown.enter="createTeam()">
              <button type="button" class="ds-btn ds-btn--primary ds-btn--s-md" x-on:click="createTeam()" x-bind:disabled="!(newTeamName||'').trim()">만들기</button>
              <span class="text-xs" style="color:var(--ds-success)" aria-live="polite" x-text="teamMsg"></span>
            </div>
          </div>
        </section>
        <section class="panel"><div class="panel-hd"><b>팀 정보</b><span class="meta" x-text="adminData&&adminData.team ? adminData.team.name : ''"></span></div>
          <div class="panel-bd">
            <div class="invite">
              <div><div class="text-xs text-muted" style="margin-bottom:4px">초대 코드 · 팀원에게 공유하면 같은 팀으로 참가합니다</div>
                <div class="invite__code" x-text="adminData&&adminData.team ? adminData.team.invite_code : '·'"></div></div>
              <button type="button" class="ds-btn ds-btn--secondary" x-on:click="copyInvite()" x-text="inviteCopied ? '복사됨 ✓' : '복사'"></button>
            </div>
          </div>
        </section>
        <section class="panel"><div class="panel-hd"><b>멤버</b><span class="meta" x-text="(adminData&&adminData.members?adminData.members.length:0)+'명'"></span></div>
          <div class="panel-bd">
            <template x-for="m in (adminData?adminData.members:[])" x-bind:key="m.id">
              <div class="lb-row">
                <span class="lb-av" data-tier="0"><img x-bind:src="charImg(m.avatar)" alt=""></span>
                <span class="lb-name"><span x-text="m.name"></span>
                  <span x-show="adminData.team && m.id===adminData.team.created_by" class="ds-badge ds-badge--status" style="margin-left:6px">생성자</span>
                  <span x-show="m.super_admin && !(adminData.team && m.id===adminData.team.created_by)" class="ds-badge ds-badge--category" style="margin-left:6px;cursor:help" data-tip="생성자가 부여한 슈퍼관리자 · 운영 작업 메뉴 전체 사용 가능(시스템 설정 제외)" data-tip-pos="top">슈퍼관리자</span>
                  <span x-show="m.is_admin && !m.super_admin && !(adminData.team && m.id===adminData.team.created_by)" class="ds-badge ds-badge--intent" style="margin-left:6px;cursor:help" data-tip="위임된 팀 관리자 · 팀 관리 메뉴 사용 가능" data-tip-pos="top">관리자</span>
                </span>
                <template x-if="adminData&&adminData.team && m.id!==adminData.team.created_by">
                  <span style="display:flex;gap:6px">
                    <!-- 권한 지정은 팀 생성자 전용(서버도 동일 게이트) · 부여받은 관리자에게는 미표시 -->
                    <button type="button" x-show="adminData.isCreator" class="ds-btn ds-btn--outline ds-btn--c-primary ds-btn--s-sm" x-on:click="adminAct(m.super_admin ? 'unset_super' : 'set_super', m.id)" x-text="m.super_admin ? '슈퍼관리자 해제' : '슈퍼관리자 지정'"></button>
                    <button type="button" x-show="adminData.isCreator" class="ds-btn ds-btn--outline ds-btn--c-neutral ds-btn--s-sm" x-on:click="adminAct(m.is_admin ? 'unset_admin' : 'set_admin', m.id)" x-text="m.is_admin ? '관리자 해제' : '관리자 지정'"></button>
                    <button type="button" x-show="adminData.isAdmin" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-sm" x-on:click="adminAct('remove_member', m.id)">제거</button>
                  </span>
                </template>
              </div>
            </template>
            <div x-show="!(adminData&&adminData.members&&adminData.members.length)" class="text-xs text-muted" style="padding:8px">멤버가 없습니다</div>
            <p class="text-xs text-muted" style="padding:8px 8px 0;line-height:1.5">🎖 <b class="text-ink">검수 마스터</b>(레벨 10) 멤버에게 관리자 권한을 위임해 골든셋·정책 관리를 함께 맡길 수 있습니다.</p>
          </div>
        </section>
        <div x-show="adminData && !adminData.isAdmin" class="text-xs text-muted" style="padding:4px">멤버 관리는 팀 관리자(생성자·위임)만 가능합니다.</div>
        </div>
        </template>
      </div>

      <!-- ═══ 모듈: 시스템 설정(운영 관리자) · 데이터 관리(상단) + API 키·모델(하단) ═══ -->
      <div x-show="mod === 'system'" x-cloak class="w-full space-y-4">
        <section class="panel"><div class="panel-hd"><b>데이터 관리</b><span class="meta">삭제는 되돌릴 수 없습니다 · 우리 팀 데이터만 영향</span><span class="ds-badge ds-badge--neutral ml-auto">운영 관리자</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:12px"><li>삭제는 <b>되돌릴 수 없습니다</b> · 우리 팀 데이터만 영향합니다.</li><li>평가 피드백을 삭제해도 <b>게임 점수·레벨은 자동 보존</b>됩니다(적립 전환) · 점수를 비우려면 <b>게임 점수 초기화</b>를 쓰세요.</li><li>로컬 적재 데이터 초기화는 <b>이 기기</b>의 SQLite 에만 영향합니다.</li></ul>
            <div style="display:flex;gap:10px;flex-wrap:wrap">
              <button type="button" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-md" x-on:click="adminAct('clear_feedback')">평가 피드백 전체 삭제</button>
              <button type="button" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-md" x-on:click="adminAct('clear_contents')">검토 콘텐츠 전체 삭제</button>
              <button type="button" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-md" x-on:click="adminAct('clear_golden')">정답셋 전체 삭제</button>
              <button type="button" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-md" x-on:click="adminAct('reset_scores')" data-tip="팀 전원의 리더보드 점수·레벨을 0부터 다시 시작합니다 · 검수 데이터·배지·정답셋은 그대로" data-tip-pos="top">게임 점수 초기화</button>
              <button type="button" x-show="backend !== 'supabase'" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-md" x-on:click="clearStore()">로컬 적재 데이터 초기화 <span class="tnum" x-text="'(' + (cfg.storedCount || 0) + '건)'"></span></button>
            </div>
            <div x-show="backend === 'supabase' && adminData && adminData.team" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--ds-hairline-soft,rgba(0,0,0,.06))">
              <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <div style="flex:1;min-width:220px">
                  <b class="text-ink" style="font-size:12.5px">팀 삭제</b>
                  <div class="text-xs text-muted" style="margin-top:3px">팀과 멤버 소속이 해제됩니다 · 콘텐츠·피드백 등 팀 데이터는 위 버튼으로 먼저 삭제하세요.</div>
                </div>
                <button type="button" class="ds-btn ds-btn--outline ds-btn--c-danger" x-on:click="adminAct('delete_team')">팀 삭제</button>
              </div>
            </div>
          </div>
        </section>
        <section class="panel" data-fn><div class="panel-hd"><b>원문 링크 백필</b><span class="meta">기존 콘텐츠에 원문 링크만 채웁니다 · 초안·검수 판정 불변</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:12px">
              <li>링크 없이 적재된 과거 콘텐츠에 <b>원문 링크만</b> 채웁니다 · 초안·검수 판정·적재 시각은 바뀌지 않습니다.</li>
              <li>매핑 파일(xlsx/csv/tsv/jsonl)에 <b>URL(링크) 컬럼</b>과 <b>해시 또는 제목 컬럼</b>이 있으면 됩니다 · 해시가 있으면 해시 우선, 제목은 정확히 일치하는 1건에만 적용합니다(동일 제목 다건은 건너뜀).</li>
            </ul>
            <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <input type="file" accept=".xlsx,.csv,.tsv,.jsonl,.json" x-ref="bfFile" class="text-[13px]">
              <button type="button" class="ds-btn ds-btn--primary ds-btn--s-md disabled:opacity-50" x-bind:disabled="bfBusy" x-on:click="backfillUrls()" x-text="bfBusy ? '적용 중…' : '링크 백필 실행'"></button>
            </div>
            <div class="text-xs text-muted" style="margin-top:8px" aria-live="polite" x-text="bfMsg"></div>
            <ul x-show="bfMisses.length" x-cloak class="ds-bullets" style="margin-top:6px">
              <template x-for="m in bfMisses" x-bind:key="m"><li x-text="m"></li></template>
            </ul>
          </div>
        </section>
        <section class="panel" data-fn><div class="panel-hd"><b>API 키</b><span class="meta">추출 호출 키 · 팀원은 입력 없이 사용</span></div>
          <div class="panel-bd">
        <!-- 운영(공유 서버): 키는 서버에서 관리 → 팀원은 입력 불필요 -->
        <!-- 팀원(비관리자): 키는 서버(관리자) 관리 · 입력 불필요. 관리자는 아래 입력으로 설정 -->
        <div x-show="cfg.keyManagedByServer && !(adminData && adminData.isAdmin)" class="keymanaged">
          <b class="text-ink">🔒 API 키는 서버에서 관리됩니다</b>
          <p>공유 서버 모드입니다. 추출 키는 <b>관리자가</b> 설정하고, 팀원은 따로 키를 넣지 않아도 바로 사용합니다.
            <span x-text="cfg.hasKey ? '· 현재 연결됨 ✓' : '· 서버에 키 미설정(관리자 확인 필요)'"></span></p>
        </div>
        <div x-show="!cfg.keyManagedByServer || (adminData && adminData.isAdmin)">
              <div class="subhd" style="margin:2px 0 6px">API 키</div>
              <ul class="ds-bullets" style="margin-bottom:6px">
                <li x-show="keyTarget !== 'solar'">통합 라우터 · 한 키로 <b>여러 모델</b>(OpenAI · Anthropic · Google · Solar 등)을 호출합니다 · 키 관리가 단순해 권장.</li>
                <li x-show="keyTarget === 'solar'" x-cloak>직접 호출 · Upstage 키로 Solar 모델을 직접 호출합니다 · 통합 라우터와 함께 등록해도 됩니다.</li>
              </ul>
              <div class="keyline">
                <label class="selctl"><span class="selctl__tag">대상</span>
                  <select x-model="keyTarget" class="bg-transparent text-[13px]" style="border:none;outline:none">
                    <template x-for="s in ['bizrouter', 'timely', 'solar']" x-bind:key="s">
                      <option x-bind:value="s" x-text="keyDefs[s].label.replace(' 키', '') + (s === 'solar' ? ' · 직접' : ' · 라우터') + (keyState(s) ? ' ✓' : '')"></option>
                    </template>
                  </select></label>
                <span class="sdot" x-bind:class="keyState(keyTarget) ? 'ok' : 'off'" style="cursor:help" x-bind:data-tip="keyState(keyTarget) ? '연결됨' : '미연결 · 키를 저장하면 연결됩니다'" data-tip-pos="top"></span>
                <div class="keyin" style="flex:1;min-width:220px;margin:0">
                  <input x-bind:type="keyShow[keyTarget] ? 'text' : 'password'" x-model="keyInputs[keyTarget]" x-bind:placeholder="keyDefs[keyTarget].ph" class="field" autocomplete="off">
                  <button type="button" class="eye" x-on:click="keyShow[keyTarget] = !keyShow[keyTarget]" aria-label="키 보기">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                  </button>
                </div>
                <button type="button" x-on:click="saveKey(keyTarget)" x-bind:disabled="cfgBusy" class="ds-btn ds-btn--primary ds-btn--s-sm disabled:opacity-50" x-text="keyState(keyTarget) ? '변경' : '저장'"></button>
                <button type="button" x-show="keyState(keyTarget)" x-on:click="testConn(keyTarget)" x-bind:disabled="cfgBusy" class="ds-btn ds-btn--secondary ds-btn--s-sm disabled:opacity-50">연결 테스트</button>
                <button type="button" x-show="keyPersisted(keyTarget)" x-on:click="forgetKey(keyTarget)" class="ds-btn ds-btn--outline ds-btn--c-danger ds-btn--s-sm">삭제</button>
              </div>
              <div class="text-xs text-muted" style="margin-top:6px" aria-live="polite" x-text="keyMsgs[keyTarget] || keySummary"></div>
              <div style="margin-top:var(--ds-space-4);padding-top:14px;border-top:1px solid var(--ds-hairline-soft,rgba(0,0,0,.06))">
                <label class="flex cursor-pointer items-center gap-2 text-[13px] text-body" style="margin:0"><input type="checkbox" x-model="cfgPersist" class="h-4 w-4 rounded border-black/15 bg-canvas text-violet"> 이 기기에 저장 (재시작 후에도 유지)</label>
                <ul class="ds-bullets" style="margin-top:8px"><li>키 저장 시 연결을 확인합니다 · 기본 실행 모델은 <b>콘텐츠 관리 · 모델 실행 · 사용 모델</b>에서 선택합니다.</li></ul>
              </div>
              <div style="margin-top:var(--ds-space-4);padding-top:14px;border-top:1px solid var(--ds-hairline-soft,rgba(0,0,0,.06))">
                <b class="text-[13px]">팀 가이드 링크</b>
                <p class="text-xs text-muted" style="margin:4px 0 10px">시작하기 카드의 '상세 가이드' 바로가기 주소입니다 · 대상을 고르고 URL 을 넣어 저장하면 팀 전체에 공유됩니다.</p>
                <div class="flex items-center gap-2" style="flex-wrap:wrap">
                  <label class="selctl"><span class="selctl__tag">대상</span>
                    <select x-model="tlTarget" class="bg-transparent text-[13px]" style="border:none;outline:none">
                      <option value="guide">개요</option>
                      <option value="guide_user">사용자 가이드</option>
                      <option value="guide_admin">관리자 가이드</option>
                    </select></label>
                  <label class="selctl" style="flex:1;min-width:240px"><span class="selctl__tag">URL</span><input type="url" x-model="teamLinks[tlTarget]" placeholder="https://…" class="w-full bg-transparent text-[13px]" style="border:none;outline:none;min-width:0"></label>
                  <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-on:click="saveTeamLinks()">저장</button>
                  <span class="text-xs text-muted" aria-live="polite" x-text="tlMsg || tlStatus"></span>
                </div>
              </div>
        </div>
          </div>
        </section>

      </div>

      <!-- ═══ 모듈: 평가 아레나 (게임화) · 팀 정확도 협동 스코어 + 리더보드 ═══ -->
      <div x-show="mod === 'home' || mod === 'arena'" x-cloak class="w-full space-y-4" style="order:-1">
        <!-- 시작하기(빈 홈 온보딩): 권한별 3분할 카드 · 구성은 팀 가이드(사용자/관리자) STEP 순서 기준 -->
        <section class="panel" x-show="starterVisible" x-cloak>
          <div class="panel-hd"><b>시작하기</b>
            <span class="meta" x-text="(adminData && adminData.isAdmin) ? '아직 콘텐츠가 없습니다 · 3단계면 검수 루프가 돌기 시작합니다' : '처음이신가요 · 3단계면 첫 검수까지 끝낼 수 있습니다'"></span>
            <span class="starter__actions">
              <a class="copybtn" style="text-decoration:none" x-show="starterGuide" x-cloak x-bind:href="starterGuide" target="_blank" rel="noreferrer">상세 가이드 ↗</a>
              <button type="button" class="starter__hide" x-on:click="hideStarter()" data-tip="이 카드를 다시 표시하지 않습니다" data-tip-pos="bottom">다음부터 표시 안 함 ×</button>
            </span>
          </div>
          <div class="panel-bd">
            <!-- 관리자: 관리자 가이드 STEP 1(콘텐츠) → 모델 실행 → STEP 2(퀘스트) -->
            <template x-if="adminData && adminData.isAdmin">
              <div class="starter__grid">
                <div class="starter__card">
                  <span class="starter__no">STEP 1</span>
                  <span class="starter__ico starter__ico--a"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M14 3v5h5"/><path d="M12 11v6M9 14h6"/></svg></span>
                  <b>콘텐츠 넣기</b>
                  <p>텍스트·엑셀·이미지·자동 인입으로 검수할 콘텐츠를 넣습니다. 용도(검수용/평가용)를 먼저 고릅니다.</p>
                  <button type="button" class="copybtn starter__cta" x-on:click="selectMod('content')">콘텐츠 관리 열기 →</button>
                </div>
                <div class="starter__card">
                  <span class="starter__no">STEP 2</span>
                  <span class="starter__ico starter__ico--b"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12z"/></svg></span>
                  <b>모델 실행</b>
                  <p>같은 화면 STEP 2에서 초안을 생성합니다. 키가 없어도 모의 모드로 전 과정을 체험할 수 있습니다.</p>
                </div>
                <div class="starter__card">
                  <span class="starter__no">STEP 3</span>
                  <span class="starter__ico starter__ico--c"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 22V4"/><path d="M4 4h13l-2 4 2 4H4"/></svg></span>
                  <b>검수 목표(퀘스트) 생성</b>
                  <p>반영 일시를 정해 저장하면 팀 퀘스트가 시작되고, 전 팀원의 홈에 D-day가 나타납니다.</p>
                  <button type="button" class="copybtn starter__cta" x-on:click="selectMod('testset')">정답셋 관리 열기 →</button>
                </div>
              </div>
            </template>
            <!-- 멤버: 사용자 가이드 STEP 3(검수) → STEP 4(교정) → STEP 6(점수와 성장) -->
            <template x-if="!(adminData && adminData.isAdmin)">
              <div class="starter__grid">
                <div class="starter__card">
                  <span class="starter__no">STEP 1</span>
                  <span class="starter__ico starter__ico--a"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/></svg></span>
                  <b>검수하기</b>
                  <p>목록의 행을 클릭해 원문과 AI 초안을 비교하고 정확/수정을 판정합니다.</p>
                  <span class="starter__kbd"><kbd>A</kbd> 정확 <kbd>S</kbd> 수정 <kbd>←</kbd><kbd>→</kbd> 이동</span>
                  <button type="button" class="copybtn starter__cta" x-on:click="selectMod('create')">콘텐츠 검수 열기 →</button>
                </div>
                <div class="starter__card">
                  <span class="starter__no">STEP 2</span>
                  <span class="starter__ico starter__ico--b"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/><path d="m15 5 4 4"/></svg></span>
                  <b>틀린 초안 고치기</b>
                  <p>수정 판정 후 틀린 요소를 골라 바르게 고칩니다. 교정 하나하나가 팀의 학습 데이터가 됩니다.</p>
                </div>
                <div class="starter__card">
                  <span class="starter__no">STEP 3</span>
                  <span class="starter__ico starter__ico--c"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 21h8M12 17v4"/><path d="M7 4h10v6a5 5 0 0 1-10 0z"/><path d="M17 5h3a2 2 0 0 1-2 4h-1M7 5H4a2 2 0 0 0 2 4h1"/></svg></span>
                  <b>점수와 성장</b>
                  <p>판정마다 점수가 오르고 퀘스트·배지·레벨이 자랍니다. 팀 퀘스트 D-day까지 함께 완주해요.</p>
                </div>
              </div>
            </template>
          </div>
        </section>
        <!-- 히어로: 팀 정확도 게이지(협동) -->
        <section class="arena-hero">
          <div class="arena-hero__head">
            <div><div class="arena-hero__eyebrow">내 검수 진척율 · 함께 끝까지</div>
              <div class="arena-hero__big"><span x-text="myProgressPct"></span><span class="arena-hero__pct">%</span></div>
            </div>
            <div class="arena-hero__target">팀 평균 <b x-text="teamProgressPct + '%'"></b></div>
          </div>
          <!-- 게이지: 내 진척 채움 + 팀 평균 마커 -->
          <div class="arena-gauge">
            <div class="arena-gauge__fill" x-bind:style="'width:' + myProgressPct + '%'"></div>
            <div class="arena-gauge__target" x-bind:style="'left:' + teamProgressPct + '%'" title="팀 평균"></div>
          </div>
          <div class="arena-hero__foot">
            <span>검수 대상 <b class="text-ink" x-text="reviewTargets"></b>건 중 내가 <b class="text-ink" x-text="(arenaMe?arenaMe.reviews:0)"></b>건 검수</span>
          </div>
        </section>

        <!-- 주간 리그(D-9): 이번 주 점수 승급/강등 -->
        <section class="panel" data-fn x-show="arenaData && arenaData.leaderboard && arenaData.leaderboard.length"><div class="panel-hd"><b>주간 리그 · 승급/강등</b>
          <span class="meta">최근 7일 · <b class="text-ink" x-text="leagueActive()"></b>명 활동</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:10px"><li>이번 주 획득 점수로 매기는 순위입니다.</li><li>상위 <b>승급권</b>은 지위 보상, 하위 <b>강등권</b>은 분발 신호입니다(지난주 대비 이동 표시).</li></ul>
            <template x-for="r in weeklyLeague()" x-bind:key="r.reviewer">
              <div class="lb-row lb-row--league" x-show="r.wp>0 || leagueActive()===0" x-bind:class="r.reviewer===reviewer ? 'lb-row--me' : ''">
                <span class="lb-rank" x-text="rankMedal(r.rank-1)"></span>
                <span class="lb-av" x-bind:data-tier="levelTier(r.level)"><img x-bind:src="charImg(r.char)" alt=""></span>
                <span class="lb-name"><span x-text="r.reviewer + (r.reviewer===reviewer ? ' (나)' : '')"></span>
                  <small class="lb-title" x-text="leagueZoneKr(r.zone)"></small></span>
                <span class="ds-badge lb-zone" x-bind:class="leagueZoneClass(r.zone)" x-text="leagueZoneLabel(r.zone)"></span>
                <span class="lb-streak lb-delta" x-bind:class="r.delta>=0?'':'down'" x-text="r.delta ? ((r.delta>=0?'▲ +':'▼ ')+Math.abs(r.delta)) : '·'"></span>
                <span class="lb-pts tnum" x-text="r.wp + 'pt'"></span>
              </div>
            </template>
            <div x-show="leagueActive()===0" class="text-xs text-muted" style="padding:12px">이번 주 검수 활동이 아직 없습니다 · <b class="text-ink">검수 대기</b>에서 점수를 쌓아 승급권에 드세요</div>
          </div>
        </section>
        <div class="arena-cols">
          <!-- 리더보드 -->
          <section class="panel"><div class="panel-hd"><b>검수 리더보드</b><span class="meta" x-text="(arenaData&&arenaData.leaderboard?arenaData.leaderboard.length:0) + '명'"></span></div>
            <div class="panel-bd">
              <template x-for="(r, i) in (arenaData?arenaData.leaderboard:[])" x-bind:key="r.reviewer">
                <div class="lb-row" x-bind:class="r.reviewer===reviewer ? 'lb-row--me' : ''">
                  <span class="lb-rank" x-text="rankMedal(i)"></span>
                  <span class="lb-av" x-bind:data-tier="levelTier(r.level)"><img x-bind:src="charImg(r.char)" alt=""></span>
                  <span class="lb-name"><span x-text="r.reviewer + (r.reviewer===reviewer ? ' (나)' : '')"></span>
                    <small class="lb-title" x-text="levelEmoji(r.level)+' '+levelTitle(r.level)"></small></span>
                  <span class="lb-streak" x-show="r.streak>0" x-text="'🔥' + r.streak"></span>
                  <span class="ds-badge ds-badge--neutral" x-text="'Lv.' + r.level"></span>
                  <span class="lb-pts tnum" x-text="r.points + 'pt'"></span>
                </div>
              </template>
              <div x-show="!(arenaData&&arenaData.leaderboard&&arenaData.leaderboard.length)" class="text-xs text-muted" style="padding:12px">아직 검수 기록이 없습니다 · <b class="text-ink">검수 대기</b>에서 첫 검수를 해보세요</div>
            </div>
          </section>
          <!-- 내 검수 캐릭터 (육성) · 상단 히어로 -->
          <section class="panel arena-charpanel"><div class="panel-hd"><b>내 검수 캐릭터</b><span class="meta" x-text="reviewer ? reviewer : '이름 미설정'"></span>
              <button type="button" class="copybtn" x-show="reviewer" x-on:click="nickEdit = !nickEdit; nickNew = reviewer; nickMsg = ''" data-tip="표시 이름(닉네임)만 바꿉니다 · 팀·캐릭터·검수 이력은 유지" data-tip-pos="top">닉네임 변경</button>
            </div>
            <div class="panel-bd">
              <div x-show="nickEdit" x-cloak class="flex flex-wrap items-center gap-2" style="padding-bottom:10px">
                <input x-model="nickNew" class="field" style="max-width:200px;height:32px" maxlength="20" placeholder="새 닉네임" x-on:keydown.enter="saveNick()">
                <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-bind:disabled="nickBusy" x-on:click="saveNick()" x-text="nickBusy ? '저장 중…' : '저장'"></button>
                <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-sm" x-on:click="nickEdit = false; nickMsg = ''">취소</button>
                <span class="text-xs text-muted" x-text="nickMsg"></span>
              </div>
              <div x-show="!reviewer" class="text-xs text-muted" style="padding:8px">로그인하면 나만의 <b class="text-ink">검수 캐릭터</b>가 생깁니다</div>
              <template x-if="reviewer && arenaMe">
                <div class="charcard charcard--split" x-bind:data-tier="levelTier(arenaMe.level)">
                  <!-- 좌: 선수 카드(캐릭터·레벨·스탯·검수하기) -->
                  <div class="charcard__player">
                    <div class="charcard__avatar">
                      <span class="charcard__glow"></span>
                      <img x-bind:src="charImg(arenaMe.char || reviewerChar)" alt="검수 캐릭터">
                      <span class="charcard__lvl" x-text="'Lv.' + arenaMe.level"></span>
                    </div>
                    <div class="charcard__info">
                      <div class="charcard__title"><span x-text="levelEmoji(arenaMe.level)"></span> <span x-text="levelTitle(arenaMe.level)"></span> <span class="charcard__flow" x-text="flowStageKr(arenaMe.level)"></span></div>
                      <div class="charcard__xpwrap">
                        <div class="charcard__xpbar"><div class="charcard__xpfill" x-bind:style="'width:' + xpPct(arenaMe) + '%'"></div></div>
                        <div class="charcard__xptxt">다음 레벨까지 <b x-text="xpToNext(arenaMe) + 'pt'"></b> · 순위 #<span x-text="arenaMyRank"></span></div>
                      </div>
                      <div class="charcard__stats">
                        <div style="cursor:help" data-tip="내가 판정한 콘텐츠 수" data-tip-pos="top"><b class="tnum" x-text="arenaMe.reviews"></b><span>검수</span></div>
                        <div style="cursor:help" data-tip="교정(수정 제안) 제출 수" data-tip-pos="top"><b class="tnum" x-text="arenaMe.corrections"></b><span>개선</span></div>
                        <div style="cursor:help" data-tip="연속 검수 일수" data-tip-pos="top"><b class="tnum" x-text="(arenaMe.streak||0)+'일'"></b><span>스트릭</span></div>
                        <div style="cursor:help" data-tip="골드 문항(정답 알려진 검증 문항) 정확도 · 점수 배율에 반영" data-tip-pos="top"><b class="tnum" x-text="(arenaMe.gold_n||0) >= 5 ? pctTxt(arenaMe.gold_acc) : '·'"></b><span>골드</span></div>
                      </div>
                      <!-- 오늘의 미션: 서버 판정·보상(달성 시 보너스 1회 지급) -->
                      <template x-if="missionList.length">
                        <div class="charcard__missions">
                          <template x-for="ms in missionList" x-bind:key="ms.id">
                            <div class="charcard__mission" x-bind:class="ms.completed ? 'is-done' : ''" x-on:click="selectMod('review')">
                              <span class="charcard__mission-ic" x-text="ms.completed ? '✅' : '🎯'"></span>
                              <span class="charcard__mission-tx" x-text="ms.label + ' · ' + ms.done + '/' + ms.total"></span>
                              <span class="charcard__mission-cta" x-text="ms.completed ? ('+' + ms.bonus + 'pt') : '도전 →'"></span>
                            </div>
                          </template>
                        </div>
                      </template>
                      <template x-if="!missionList.length">
                        <div class="charcard__mission" x-on:click="selectMod(todayMission.to)">
                          <span class="charcard__mission-ic">🎯</span>
                          <span class="charcard__mission-tx" x-text="todayMission.txt"></span>
                          <span class="charcard__mission-cta" x-text="todayMission.cta + ' →'"></span>
                        </div>
                      </template>
                    </div>
                  </div>
                  <!-- 우: 모은 배지 컬렉션 -->
                  <div class="charcard__collection">
                    <div class="charcard__badges-hd">배지 컬렉션 <b x-text="badgeGot + ' / ' + badges().length"></b>
                      <button type="button" class="ds-btn ds-btn--ghost ds-btn--s-sm charcard__more" x-on:click="badgeModalOpen=true">전체 보기 →</button></div>
                    <div class="charcard__badges">
                      <template x-for="(bd, i) in badges()" x-bind:key="i">
                        <div class="gbadge" x-bind:class="bd.got ? 'got' : 'locked'" x-bind:style="bd.got ? ('--bc:' + bd.color) : ''" x-bind:data-tip="bd.desc + ' · +' + bd.exp + ' EXP'" data-tip-pos="top">
                          <span class="gbadge__orb"><span class="gbadge__ic" x-text="bd.got ? bd.icon : '🔒'"></span></span>
                          <span class="gbadge__label" x-text="bd.label"></span>
                          <span class="gbadge__cat" x-text="bd.cat"></span>
                        </div>
                      </template>
                    </div>
                  </div>
                </div>
              </template>
              <div x-show="reviewer && !arenaMe" class="charcard charcard--egg" data-tier="0">
                <div class="charcard__avatar"><img x-bind:src="charImg(reviewerChar)" alt="" style="opacity:.5;filter:grayscale(1)"><span class="charcard__lvl">Lv.0</span></div>
                <div class="charcard__title">🥚 검수 새싹</div>
                <div class="charcard__hint"><b class="text-ink" x-text="reviewer"></b> 의 첫 검수로 캐릭터를 깨워요 · <span class="arena-quest" x-on:click="selectMod('review')">검수하러 가기 →</span></div>
              </div>
            </div>
          </section>
        </div>
      </div>

      <!-- ═══ 모듈: 검수 대기 (팀 실시간 협업) · YELLOW 대기열 + 다중 의견 ═══ -->
      <!-- ═══ 모듈: 실행 큐 (단일 위젯) · 실제 실행 상태 ═══ -->
      <div x-show="mod === 'content'" x-cloak class="w-full" style="margin:18px 0 12px">
        <div class="stepline"><span class="stepline__no">STEP 3</span><b>실행 큐 · 이력</b><span class="meta">진행 중 작업의 진척도와 완료 이력 · 완료 작업을 누르면 해당 콘텐츠를 봅니다</span></div>
      </div>
      <div x-show="mod === 'content'" x-cloak class="w-full">
        <section class="panel"><div class="panel-hd"><b>실행 큐 · 이력</b><span class="meta" x-text="(runningCount ? (runningCount + ' 실행중') : '대기 없음')"></span>
          <!-- 자동/수동 구분 필터 -->
          <span class="ml-auto" style="display:flex;gap:6px">
            <button type="button" class="srcfilter__chip" x-bind:class="queueTrig==='' ? 'sel' : ''" x-on:click="queueTrig=''">전체</button>
            <button type="button" class="srcfilter__chip" x-bind:class="queueTrig==='auto' ? 'sel' : ''" x-on:click="queueTrig='auto'">자동</button>
            <button type="button" class="srcfilter__chip" x-bind:class="queueTrig==='manual' ? 'sel' : ''" x-on:click="queueTrig='manual'">수동</button>
          </span>
        </div>
          <div class="panel-bd">
            <div x-show="loading && activeTabId === 'image' && queueTrig !== 'auto'">
              <div class="w-run"><span class="w-run__av"><img src="/vendor/yonghee-pitcher.svg" alt=""></span><div><div class="w-run__t">이미지 메타 추출 중 · 수동</div><div class="ds-progress ds-progress--indeterminate" style="margin-top:5px"><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary"></div></div></div></div></div>
            </div>
            <template x-for="j in filteredJobs" x-bind:key="j.id">
              <div class="w-run" x-bind:style="(j.hashes||[]).length ? 'cursor:pointer' : ''" x-on:click="jobContents(j)" x-bind:data-tip="(j.hashes||[]).length ? '해당 작업의 콘텐츠 보기' : null" data-tip-pos="top"><span class="w-run__av"><img src="/vendor/daesik-batter.svg" alt=""></span><div style="flex:1;min-width:0">
                <div class="w-run__t"><b class="text-ink" x-text="j.name"></b> · <span x-text="(j.kind || '자동 인입') + (j.running ? ' 중' : ' 완료')"></span> <span class="ds-badge" x-bind:class="j.trigger==='auto' ? 'ds-badge--intent' : 'ds-badge--entity'" style="cursor:help" data-tip="트리거 · 자동=스케줄 폴링, 수동=관리자 실행" data-tip-pos="top" x-text="j.trigger==='auto' ? '자동' : '수동'"></span>
                  <span class="text-xs text-muted tnum" x-show="j.total" x-text="j.done + ' / ' + j.total + '건' + (j.total ? (' · ' + Math.round((j.done/j.total)*100) + '%') : '')"></span>
                  <span class="text-xs text-muted tnum" x-show="j.running && j.eta_s != null" x-text="'· 남은 예상 ' + fmtEta(j.eta_s) + ' (건당 ' + ((j.per_item_ms||0)/1000).toFixed(1) + '초)'"></span>
                  <span class="ds-badge ds-badge--error" x-show="j.failed" x-text="'실패 ' + j.failed"></span></div>
                <div class="text-xs text-muted" x-text="j.last_msg || j.endpoint"></div>
                <div class="ds-progress" x-show="j.running" x-bind:class="j.total ? '' : 'ds-progress--indeterminate'" style="margin-top:5px"><div class="ds-progress__track"><div class="ds-progress__fill ds-progress__fill--primary" x-bind:style="j.total ? ('width:' + Math.round((j.done/j.total)*100) + '%') : ''"></div></div></div>
              </div></div>
            </template>
            <div x-show="!runningCount" class="ds-empty" style="border:0;padding:20px 8px">
              <div class="ds-empty__desc">진행 중인 작업이 없습니다 · STEP 1에서 추가하고 <b class="text-ink">STEP 2 실행</b>을 누르면 여기에 진척도가 표시되고, 완료 작업은 이력으로 남습니다</div>
            </div>
          </div>
        </section>
      </div>
      <!-- 추가된 콘텐츠 · 용도: 실행 큐 아래(맥락: 추가 → 실행 → 큐 → 결과 용도 관리) -->
      <div x-show="mod === 'content'" x-cloak class="w-full space-y-4" style="margin-top:14px">
        <section class="panel" data-fn id="added-contents"><div class="panel-hd"><b>추가된 콘텐츠 · 용도</b><span class="meta">평가용은 검수 목록에서 제외되어 평가 전용으로 보존됩니다</span>
          <button type="button" class="srcfilter__chip sel" x-show="jobFilter" x-cloak x-on:click="jobFilter = null" x-text="jobFilter ? (jobFilter.name + ' 결과 ' + contentRows.length + '건 · 전체 보기 ×') : ''"></button>
          <button type="button" class="ds-iconbtn ds-iconbtn--bordered ml-auto" x-on:click="loadDash()" data-tip="새로고침" data-tip-pos="bottom" aria-label="콘텐츠 목록 새로고침"><svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M20 11a8 8 0 1 0-.9 4.5M20 5v6h-6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
        </div>
          <div class="overflow-auto" style="max-height:320px"><table class="ds-table"><thead><tr><th>콘텐츠</th><th style="width:110px">서비스</th><th style="width:170px">모델</th><th style="width:70px">버전</th><th style="width:90px">용도</th><th style="width:170px"></th></tr></thead><tbody>
            <template x-for="c in contentRows" x-bind:key="'pp'+c.hash">
              <tr>
                <td class="text-ink" x-text="c.title || '(제목 없음)'"></td>
                <td class="text-muted" x-text="c.service || '·'"></td>
                <td class="text-muted" x-text="c.model || '·'"></td>
                <td><span x-show="c.model" class="tnum" x-text="'v' + (c.version || 1)"></span><span x-show="!c.model" class="ds-badge ds-badge--warning" style="cursor:help" data-tip="STEP 2 모델 실행에서 초안을 생성하세요" data-tip-pos="top">미실행</span></td>
                <td><span class="ds-badge" x-bind:class="c.purpose === 'eval' ? 'ds-badge--warning' : 'ds-badge--neutral'" x-text="c.purpose === 'eval' ? '평가용' : '검수용'"></span></td>
                <td><div class="flex items-center gap-1.5">
                  <button type="button" class="ds-btn ds-btn--outline" style="height:26px;padding:0 10px;font-size:11px" x-on:click="togglePurpose(c)" x-text="c.purpose === 'eval' ? '검수용 전환' : '평가용 전환'"></button>
                  <button type="button" class="ds-btn ds-btn--outline" x-bind:class="delArm === c.hash ? 'ds-btn--c-danger' : ''" style="height:26px;padding:0 10px;font-size:11px" x-on:click="removeContent(c)" x-text="delArm === c.hash ? '삭제 확인' : '삭제'" data-tip="검수 목록·결과 비교(초안)·피드백까지 함께 삭제됩니다" data-tip-pos="left"></button>
                </div></td>
              </tr>
            </template>
          </tbody></table></div>
          <div x-show="!contentRows.length" class="text-xs text-muted" style="margin:0 16px 14px" x-text="jobFilter ? '이 작업의 콘텐츠가 목록에 없습니다(삭제되었을 수 있음)' : '아직 추가된 콘텐츠가 없습니다 · 위에서 수동·자동으로 추가하세요'"></div>
        </section>
      </div>

      <!-- ═══ 모듈: 프롬프트 스튜디오 (전용 도구) · 추출 단계별 프롬프트 ═══ -->
      <div x-show="mod === 'studio' && studioTab === 'prompt'" x-cloak class="w-full space-y-4">
        <ul class="ds-bullets hintbox" style="padding:14px 16px">
          <li>아이템 메타(리드문·엔티티·인텐트·카테고리)의 <b>코어 규칙·예시는 계약</b>(읽기 전용)이고, 수정은 <b>모델별 쿡북 래퍼</b> 단위로만 합니다.</li>
          <li>검수·판정 단계의 원천 프롬프트는 아래 코드블록에서 직접 수정합니다(관리자 전용) · 모델 지정 시 <b>모델별 분기 저장</b>.</li>
          <li>프롬프트는 <b>학습 반영 회차(버전)</b>마다 보정됩니다 · 현재 프롬프트 버전 <b class="text-ink tnum" x-text="verTxt"></b>.</li>
          <li>보완은 <b>콘텐츠 검수</b>의 교정 피드백이 자동 반영됩니다.</li>
        </ul>
        <datalist id="modelopts"><template x-for="m in availableModels" x-bind:key="m"><option x-bind:value="m"></option></template></datalist>
        <!-- 아이템 메타(추출·분석) = 분리형 4호출 계약. 코어 규칙·예시는 읽기 전용, 수정은 모델 계열 쿡북 래퍼 단위 -->
        <section class="panel" data-fn><div class="panel-hd"><b>기준 계약 · 아이템 메타 4호출</b><span class="meta">코어 규칙·골드 예시는 계약(읽기 전용) · 수정은 기준 문서 개정으로</span>
          <span class="ds-badge ds-badge--success ml-auto" x-show="learnedStages.extract || learnedStages.analyze" style="cursor:help" data-tip="배치 결과 피드백이 각 호출 프롬프트에 자동 병기 중">학습 보정 반영</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:10px">
              <li>추출은 <b>분리형 순차 4호출</b>입니다: ① 리드문 → ② 엔티티 → ③ 인텐트(사전: 범용①·② + 서비스 분기) → ④ 콘텐츠 카테고리(사전: Tier1/Tier2 + 구분 기준). 리드문이 비면 후속 호출을 생략합니다.</li>
              <li>호출 사이 <b>기계 검증</b>: 사전 불일치 값 드롭 · 전량 드롭 시 1회 재요청 · 엔티티 1~3개 강제.</li>
              <li>프롬프트는 <b>학습 반영 회차(버전)</b>마다 보정됩니다 · 현재 프롬프트 버전 <b class="text-ink tnum" x-text="verTxt"></b>.</li>
            </ul>
            <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
              <template x-for="cl in (cfg.metaCalls||[])" x-bind:key="'ct'+cl">
                <button type="button" class="srcfilter__chip" x-bind:class="contractCall===cl ? 'sel' : ''" x-on:click="contractCall=cl" x-text="callLabel(cl)"></button>
              </template>
              <button type="button" class="srcfilter__chip" x-bind:class="contractCall==='examples' ? 'sel' : ''" x-on:click="contractCall='examples'">골드 예시</button>
            </div>
            <div class="codeblock"><div class="codeblock__bar"><span class="codeblock__dots"><i></i><i></i><i></i></span>계약 원문 · 읽기 전용<span class="codeblock__stage" x-text="contractCall==='examples' ? '골드 예시' : callLabel(contractCall)"></span></div><textarea readonly spellcheck="false" x-bind:value="contractText"></textarea></div>
          </div></section>
        <section class="panel" data-fn><div class="panel-hd"><b>모델별 쿡북 래퍼</b><span class="meta">수정 단위는 계열 래퍼만 · 코어 규칙·사전·예시는 자동 삽입</span></div>
          <div class="panel-bd">
            <ul class="ds-bullets" style="margin-bottom:10px">
              <li>계열별 쿡북 관례(GPT 출력 계약 · Gemini 스키마 재명시 · Claude 배경 제공 · Solar CRITICAL+자가 검증)를 이 래퍼가 담당합니다.</li>
              <li>플레이스홀더 <b>{ROLE} {SCHEMA} {RULES} {EXAMPLES} {SELF_CHECK} {LEARNED}</b> 위치에 계약 요소가 삽입됩니다 · 비우고 저장하면 기본 래퍼로 복원됩니다.</li>
            </ul>
            <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">
              <template x-for="f in ['gpt','gemini','claude','solar']" x-bind:key="'fw'+f">
                <button type="button" class="srcfilter__chip" x-bind:class="wrapFam===f ? 'sel' : ''" x-on:click="wrapFam=f; syncWrapDraft()" x-text="f"></button>
              </template>
            </div>
            <div class="codeblock"><div class="codeblock__bar"><span class="codeblock__dots"><i></i><i></i><i></i></span>계열 래퍼 템플릿<span class="codeblock__stage" x-text="wrapFam + ((cfg.familyWrappers||{})[wrapFam] ? ' · 수정됨' : ' · 기본')"></span></div><textarea x-model="wrapDraft" spellcheck="false" placeholder="이 계열의 래퍼 템플릿을 수정하세요"></textarea></div>
            <div style="display:flex;align-items:center;gap:10px;margin-top:10px">
              <button type="button" class="ds-btn ds-btn--primary" x-on:click="saveWrapper()">저장</button>
              <button type="button" class="ds-btn ds-btn--secondary" x-on:click="restoreWrapper()">기본값 복원</button>
              <span class="text-xs" style="color:var(--ds-success)" aria-live="polite" x-text="wrapMsg"></span>
            </div>
          </div></section>
        <section class="panel" data-fn><div class="panel-hd"><b>최종 프롬프트 미리보기</b><span class="meta">호출×모델×서비스 조합의 실제 합성 결과</span></div>
          <div class="panel-bd">
            <div class="filterbar" style="margin:0 0 10px">
              <span class="selctl"><span class="selctl__lbl">모델</span>
                <select class="field" x-model="pvModel" x-on:change="loadPreview()"><template x-for="m in availableModels" x-bind:key="'pv'+m"><option x-bind:value="m" x-text="m"></option></template></select></span>
              <span class="selctl"><span class="selctl__lbl">호출</span>
                <select class="field" x-model="pvCall" x-on:change="loadPreview()">
                  <template x-for="cl in (cfg.metaCalls||[])" x-bind:key="'pc'+cl"><option x-bind:value="cl" x-text="callLabel(cl)"></option></template>
                </select></span>
              <span class="selctl"><span class="selctl__lbl">서비스</span>
                <select class="field" x-model="pvService" x-on:change="loadPreview()"><template x-for="g in groups" x-bind:key="'pg'+g"><option x-bind:value="g" x-text="g"></option></template></select></span>
              <span class="text-xs text-muted" x-text="pvData ? ('계열 ' + pvData.family) : ''"></span>
            </div>
            <div class="codeblock"><div class="codeblock__bar"><span class="codeblock__dots"><i></i><i></i><i></i></span>system<span class="codeblock__stage" x-text="pvModel"></span></div><textarea readonly spellcheck="false" x-bind:value="pvData ? pvData.system : '모델·호출을 선택하면 합성 결과가 표시됩니다'"></textarea></div>
            <div class="codeblock" style="margin-top:10px"><div class="codeblock__bar"><span class="codeblock__dots"><i></i><i></i><i></i></span>user<span class="codeblock__stage">입력 템플릿</span></div><textarea readonly spellcheck="false" style="min-height:90px" x-bind:value="pvData ? pvData.user : ''"></textarea></div>
          </div></section>
        <section class="panel"><div class="panel-hd"><b>추론 강도</b><span class="meta">전 단계 공통 · 선택 시 즉시 저장</span></div>
          <div class="panel-bd">
            <div class="ds-segmented" style="max-width:300px">
              <template x-for="o in reasoningOpts" x-bind:key="o.id"><button type="button" class="ds-segmented__item" x-bind:aria-pressed="reasoning === o.id ? 'true' : 'false'" x-on:click="setReasoning(o.id)" x-text="o.label"></button></template>
            </div>
          </div></section>
      </div>

      <!-- ═══ 모듈: 인입 정책 (전용 도구) ═══ -->
      <!-- 수집(인입) 정책: 정책 표 성격 → 사전·정책 메뉴에 통합 렌더 -->
      <div x-show="mod === 'dict' && dictTab === 'policy'" x-cloak class="w-full space-y-4" style="margin-top:16px">
        <div class="ds-widget ds-widget--info" style="--w-accent:var(--ds-primary)">
          <div class="ds-widget__head"><div class="ds-widget__title"><span class="ds-widget__icon-chip"><svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></span><span>ITEM TYPE 처리 정책</span></div><div class="ds-widget__actions"><span class="ds-badge ds-badge--neutral">131</span><span class="text-xs text-muted">직접 수정 가능</span><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <div class="overflow-auto"><table class="ds-table"><thead><tr><th>ITEM TYPE</th><th>필터 대상</th><th>처리 방식</th><th>상태</th><th></th></tr></thead><tbody>
              <template x-for="(row, t) in (dictData ? dictData.intakePolicy : {})" x-bind:key="t">
                <tr>
                  <td class="text-ink" x-text="t"></td>
                  <td x-text="row.filter"></td>
                  <td><div class="tbox" x-text="row.method"></div></td>
                  <td><span class="ds-badge" x-bind:class="row.status === '구현' ? 'ds-badge--success' : (row.status === 'PoC' ? 'ds-badge--intent' : 'ds-badge--neutral')" style="cursor:help" data-tip="구현 상태 · 구현=운영 반영, PoC=검증 단계, 설계=문서 단계" data-tip-pos="top"><span x-show="row.status==='구현'" class="ds-badge__dot"></span><span x-text="row.status"></span></span></td>
                  <td><button type="button" class="copybtn" x-on:click="startEditIntake(t, row)">편집</button></td>
                </tr>
              </template>
              <template x-if="!dictData || !dictData.intakePolicy"><tr><td colspan="5" class="text-muted">불러오는 중…</td></tr></template>
            </tbody></table></div>
          </div>
        </div>
        <div class="ds-widget ds-widget--info" style="--w-accent:#a05cff;min-height:auto">
          <div class="ds-widget__head"><div class="ds-widget__title"><span class="ds-widget__icon-chip"><svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg></span><span>콘텐츠 출처 분류</span></div><div class="ds-widget__actions"><span class="ds-widget__kind ds-widget__kind--info">정보</span></div></div>
          <div class="ds-widget__body">
            <ul class="ds-bullets" style="margin-bottom:11px">
              <li><b>식별 표준</b>: C2PA(자격 증명) · SynthID(워터마크).</li>
              <li>발행자 정보로 <b>PGC/UGC 1차 식별</b>.</li>
            </ul>
            <div class="flex flex-wrap gap-1.5"><span class="ds-badge ds-badge--category" style="cursor:help" data-tip="기존 미디어(언론·방송)가 제작한 콘텐츠" data-tip-pos="top">PGC 기존 미디어</span><span class="ds-badge ds-badge--category" style="cursor:help" data-tip="일반 사용자가 만든 콘텐츠" data-tip-pos="top">UGC 사용자 생성</span><span class="ds-badge ds-badge--category" style="cursor:help" data-tip="AI 가 생성한 콘텐츠" data-tip-pos="top">AIGC AI 생성</span><span class="ds-badge ds-badge--category" style="cursor:help" data-tip="AI 로 보정·편집된 콘텐츠" data-tip-pos="top">AIEC AI 보정</span></div>
          </div>
        </div>
      </div>

    </div>
  </div>
  </div><!-- /.appbody -->

  <!-- ✎ 편집 팝업 · 편집 버튼 클릭 시 바로 수정(사전·정책 등) -->
  <div class="ds-dialog-backdrop" x-show="editT" x-cloak x-on:mousedown.self="cancelEdit()" style="z-index:75">
    <div class="ds-dialog" role="dialog" aria-modal="true" aria-label="편집" style="max-width:520px">
      <h2 class="ds-dialog__title" x-text="'편집 · ' + editTitle"></h2>
      <div class="ds-dialog__body" style="display:flex;flex-direction:column;gap:12px">
        <div x-show="editT === 'intake_policy'" x-cloak>
          <label class="lbl">필터 대상</label>
          <select x-model="editFilter" class="field"><option value="O">O (필터)</option><option value="△">△ (부분)</option><option value="X">X (미적용)</option></select>
        </div>
        <div>
          <label class="lbl" x-text="editKind === 'list' ? '항목 (한 줄에 하나씩)' : (editT === 'intake_policy' ? '처리 방식' : '값')"></label>
          <textarea x-model="editVal" x-bind:rows="editT === 'intake_policy' ? 3 : 8" class="field" x-bind:style="editT === 'intake_policy' ? '' : 'min-height:180px'" x-on:keydown.escape="cancelEdit()"></textarea>
        </div>
        <div x-show="editT === 'legal_types'" x-cloak>
          <label class="lbl">근거 법령(조항)</label>
          <input x-model="editExtra" class="field" placeholder="예) 정보통신망법 제44조의7">
        </div>
        <div x-show="editT === 'intake_policy'" x-cloak>
          <label class="lbl">상태</label>
          <input x-model="editExtra" class="field" placeholder="예) 구현 / PoC / 계획">
        </div>
        <span class="text-xs" style="color:var(--ds-muted)" aria-live="polite" x-text="editMsg"></span>
      </div>
      <div class="ds-dialog__footer">
        <button type="button" class="ds-btn ds-btn--ghost" x-on:click="cancelEdit()">취소</button>
        <button type="button" class="ds-btn ds-btn--primary" x-on:click="saveEdit()">저장</button>
      </div>
    </div>
  </div>

  <!-- 엔티티 사전 편집 팝업: 타입·속성 수동 확정(사람 결정 우선 · 재보강이 덮어쓰지 않음) -->
  <div class="ds-dialog-backdrop" x-show="entEdit" x-cloak x-on:mousedown.self="entEdit=null" x-on:keydown.escape.window="entEdit && (entEdit=null)" style="z-index:76">
    <div class="ds-dialog" role="dialog" aria-modal="true" aria-label="개체 편집" style="max-width:560px;display:flex;flex-direction:column;max-height:88vh">
      <h2 class="ds-dialog__title" style="display:flex;align-items:baseline;gap:10px"><span x-text="'개체 편집 · ' + (entEdit ? entEdit.name : '')"></span>
        <span class="text-xs text-muted" style="font-weight:400" x-text="entEdit ? entEdit.entity_id : ''"></span></h2>
      <div class="ds-dialog__body" style="overflow:auto;display:flex;flex-direction:column;gap:12px">
        <div>
          <label class="lbl">타입 (NER 6종 · 개체당 1개 · 비우면 보류)</label>
          <select class="field" x-bind:value="entEdit ? entEdit.type : ''" x-on:change="entEdit.type=$event.target.value">
            <option value="">(보류 · 타입 미부여)</option>
            <template x-for="(ko,t) in (entData ? entData.meta.types : {})" x-bind:key="'et'+t"><option x-bind:value="t" x-text="ko + ' (' + t + ')'"></option></template>
          </select>
        </div>
        <div x-show="entEdit && entEdit.type" x-cloak>
          <label class="lbl">속성 (저장 시 수동 확정 · 위키데이터 재보강이 덮어쓰지 않음)</label>
          <div class="space-y-2">
            <template x-for="f in entAttrFields()" x-bind:key="'ef'+f[0]">
              <div class="flex items-center gap-2">
                <span class="text-xs text-muted" style="width:110px;flex:none" x-text="f[1]"></span>
                <select x-show="f[0] === 'occupation'" class="field" style="flex:1" x-on:change="entEdit.attrs[f[0]]=$event.target.value">
                  <option value="" x-bind:selected="!(entEdit && entEdit.attrs[f[0]])">(미지정)</option>
                  <template x-for="g in (entData ? entData.meta.occupationGroups : [])" x-bind:key="'og'+g"><option x-bind:value="g" x-bind:selected="entEdit && entEdit.attrs[f[0]] === g" x-text="g"></option></template>
                </select>
                <input x-show="f[0] !== 'occupation'" type="text" class="field" style="flex:1" x-bind:value="(entEdit && entEdit.attrs[f[0]]) || ''" x-on:input="entEdit.attrs[f[0]]=$event.target.value">
              </div>
            </template>
          </div>
        </div>
        <div>
          <label class="lbl">별칭 (이형 표기 · 다음 적재부터 같은 개체로 흡수)</label>
          <div class="flex flex-wrap gap-1.5" style="margin-bottom:6px">
            <template x-for="a in entEditAliases" x-bind:key="'al'+a"><span class="ds-badge ds-badge--neutral" x-text="a"></span></template>
          </div>
          <input type="text" class="field" placeholder="별칭 추가 (저장 시 반영)" x-model="entAliasInput">
        </div>
        <div x-show="entEditContents.length">
          <label class="lbl" x-text="'등장 콘텐츠 · ' + entEditContents.length + '건'"></label>
          <div class="overflow-auto" style="max-height:140px"><table class="ds-table"><tbody>
            <template x-for="(c,i) in entEditContents" x-bind:key="'ec'+i"><tr>
              <td class="text-muted" x-text="c.title || c.hash"></td>
              <td style="width:40px"><span class="ds-badge ds-badge--neutral" x-text="c.grade || '-'"></span></td>
            </tr></template>
          </tbody></table></div>
        </div>
        <span class="text-xs" style="color:var(--ds-muted)" aria-live="polite" x-text="entEditMsg"></span>
      </div>
      <div class="ds-dialog__footer">
        <button type="button" class="ds-btn ds-btn--ghost" x-on:click="entEdit=null">취소</button>
        <button type="button" class="ds-btn ds-btn--primary" x-on:click="saveEntEdit()">저장(확정)</button>
      </div>
    </div>
  </div>

  <!-- 검수자 일괄 배정(슈퍼관리자 이상): ① 콘텐츠 선택 → ② 담당자 지정 → ③ 확인 · 배정 -->
  <div class="ds-dialog-backdrop" x-show="bulkOpen" x-cloak x-transition.opacity x-on:mousedown.self="bulkOpen=false" x-on:keydown.escape.window="bulkOpen && (bulkOpen=false)" style="z-index:78">
    <div class="ds-dialog" role="dialog" aria-modal="true" aria-label="검수자 일괄 배정" style="max-width:720px;display:flex;flex-direction:column;max-height:88vh">
      <h2 class="ds-dialog__title" style="display:flex;align-items:baseline;gap:10px;font-family:var(--ds-font-display)">검수자 일괄 배정
        <span class="text-xs text-muted" style="font-family:var(--ds-font-body);font-weight:400">여러 콘텐츠에 한 번에 지정합니다 · 슈퍼관리자 이상만 가능합니다</span></h2>
      <div class="ds-dialog__body" style="overflow:auto;padding:4px 0 2px">

        <!-- ① 콘텐츠 선택 -->
        <div class="stepcard">
          <div class="stepcard__hd">
            <span class="stepcard__no">STEP 1</span>
            <span class="stepcard__ttl">콘텐츠 선택<span class="stepcard__sub">배정할 대상을 고르세요</span></span>
            <span class="ds-badge ds-badge--status stepcard__badge tnum" x-text="'선택 ' + bulkSelHashes.length + ' / ' + bulkFiltered.length + '건'"></span>
          </div>
          <div class="filterbar" style="margin-bottom:12px">
            <input class="field" placeholder="제목·카테고리·사유 검색" x-model="bulkQ">
            <select class="field" style="width:auto" x-model="bulkGrade"><option value="">등급 전체</option><option value="G">G</option><option value="R">R</option></select>
            <select class="field" style="width:auto" x-model="bulkSvc"><option value="">서비스 전체</option><template x-for="sv in rawSvcs" x-bind:key="'b'+sv"><option x-bind:value="sv" x-text="sv"></option></template></select>
            <select class="field" style="width:auto" x-model="bulkRev"><option value="">검수 전체</option><option value="todo">미검수</option><option value="done">검수 완료</option></select>
            <select class="field" style="width:auto" x-model="bulkAsg"><option value="">배정 전체</option><option value="unassigned">미배정만</option><option value="assigned">배정됨만</option></select>
          </div>
          <!-- 무작위 수량 자동 선택: 현재 필터 결과 중 N건 랜덤 추출 -->
          <div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap;padding:10px 12px;margin-bottom:12px;background:var(--ds-tint-bg);border-radius:var(--ds-radius-md)">
            <b class="text-xs" style="color:var(--ds-primary)">⚄ 무작위 자동 선택</b>
            <input type="number" class="field" style="width:78px" min="1" x-model.number="bulkRandN">
            <span class="text-xs text-muted">건</span>
            <button type="button" class="ds-btn ds-btn--outline ds-btn--c-primary ds-btn--s-sm" x-on:click="bulkRandom()">랜덤 선택</button>
            <span class="text-xs text-muted">필터 결과 중 무작위로 · 다시 누르면 재추출</span>
          </div>
          <div class="overflow-auto" style="max-height:252px;border:1px solid var(--ds-hairline);border-radius:var(--ds-radius-md)">
            <table class="ds-table" style="table-layout:fixed;width:100%;margin:0">
              <thead><tr>
                <th style="width:42px;text-align:center"><input type="checkbox" class="bulkcb" x-bind:checked="bulkAllOn" x-on:change="bulkToggleAll()" aria-label="전체 선택"></th>
                <th>제목</th>
                <th style="width:96px">서비스</th>
                <th style="width:112px">배정</th>
              </tr></thead>
              <tbody>
                <template x-for="r in bulkFiltered" x-bind:key="'bk'+r.hash">
                  <tr style="cursor:pointer" x-bind:class="bulkChecked[r.hash] ? 'is-sel' : ''" x-on:click="bulkToggle(r.hash)">
                    <td style="text-align:center"><input type="checkbox" class="bulkcb" x-bind:checked="!!bulkChecked[r.hash]" tabindex="-1" style="pointer-events:none"></td>
                    <td class="text-ink" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                      <span x-text="r.title || '(제목 없음)'"></span>
                      <span class="ds-badge ds-badge--yellow" x-show="r.review==='yellow'" style="margin-left:5px">YELLOW</span>
                    </td>
                    <td class="text-muted" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" x-text="r.service || '·'"></td>
                    <td style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                      <span class="ds-badge ds-badge--intent" x-show="(r.assignees||[]).length" data-tip="이미 배정됨 · 저장 시 덮어씀" data-tip-pos="left" x-text="assigneeNames(r).join(', ')"></span>
                      <span x-show="!(r.assignees||[]).length" class="text-muted">·</span>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
            <div x-show="!bulkFiltered.length" class="text-xs text-muted" style="padding:16px;text-align:center">조건에 맞는 콘텐츠가 없습니다</div>
          </div>
        </div>

        <!-- ② 담당자 지정 -->
        <div class="stepcard">
          <div class="stepcard__hd">
            <span class="stepcard__no">STEP 2</span>
            <span class="stepcard__ttl">담당자 지정<span class="stepcard__sub">선택한 콘텐츠를 검수할 팀원</span></span>
            <span class="ds-badge ds-badge--status stepcard__badge" x-show="bulkPick.length" x-text="bulkPick.length + '명 선택'"></span>
          </div>
          <div x-show="!assignMembers.length" class="text-xs text-muted" style="padding:2px 0 10px">배정 가능한 팀원이 없습니다 · <b class="text-ink">팀 관리</b>에서 멤버를 초대하세요</div>
          <div style="display:flex;flex-wrap:wrap;gap:9px;margin-bottom:14px">
            <template x-for="m in assignMembers" x-bind:key="'bpk'+m.id">
              <label class="pickchip" x-bind:class="bulkPick.includes(m.id) ? 'on' : ''">
                <input type="checkbox" x-bind:checked="bulkPick.includes(m.id)" x-on:change="bulkPickToggle(m.id)"><span x-text="m.name"></span>
              </label>
            </template>
          </div>
          <label class="text-xs text-muted" style="display:inline-flex;align-items:center;gap:8px">최소 검수인원
            <input type="number" class="field" style="width:72px" min="1" x-bind:max="Math.max(1, bulkPick.length)" x-model.number="bulkMin">
            <span class="tnum" x-text="'/ 배정 ' + bulkPick.length + '명 · N명 검수 시 통과'"></span></label>
        </div>

        <!-- ③ 확인 -->
        <div class="stepcard">
          <div class="stepcard__hd">
            <span class="stepcard__no">STEP 3</span>
            <span class="stepcard__ttl">확인<span class="stepcard__sub">배정 내용을 확인하고 실행하세요</span></span>
          </div>
          <div class="tbox" style="padding:14px 16px">
            <div class="text-ink" style="font-weight:650;margin-bottom:8px" x-text="'콘텐츠 ' + bulkSelHashes.length + '건을 ' + (bulkPick.map(id => (assignMembers.find(m=>m.id===id)||{}).name || id).join(', ') || '(담당자 미선택)') + '에게 배정'"></div>
            <div class="text-xs text-muted" style="line-height:1.7">
              <div>· 최소 검수인원 <b class="text-ink" x-text="Math.min(bulkMin, Math.max(1, bulkPick.length))"></b>명</div>
              <div>· 배정 후 <b class="text-ink">담당자에게만</b> 검수 큐에 표시(배타적)</div>
            </div>
            <div class="ds-badge ds-badge--warning" x-show="bulkOverwrite" style="margin-top:10px" x-text="'⚠ 선택 중 ' + bulkOverwrite + '건은 이미 배정돼 있습니다 · 저장 시 덮어씁니다'"></div>
          </div>
        </div>

      </div>
      <div class="ds-dialog__footer" style="margin-top:16px">
        <button type="button" class="ds-btn ds-btn--ghost ds-btn--s-md" x-on:click="bulkOpen=false">취소</button>
        <button type="button" class="ds-btn ds-btn--primary ds-btn--s-md" x-bind:disabled="bulkBusy || !bulkSelHashes.length || !bulkPick.length" x-on:click="saveBulk()" x-text="bulkBusy ? '배정 중…' : (bulkSelHashes.length + '건 배정 실행 →')"></button>
      </div>
    </div>
  </div>

  <!-- 대시보드 드릴다운: 분포 항목 → 판정된 콘텐츠 목록 -->
  <div class="ds-dialog-backdrop" x-show="drillOpen" x-cloak x-on:mousedown.self="drillOpen=false" style="z-index:72">
    <div class="ds-dialog" role="dialog" aria-modal="true" aria-label="콘텐츠 목록" style="max-width:620px">
      <h2 class="ds-dialog__title" style="display:flex;align-items:center;gap:10px"><span x-text="drillData ? (drillKindKr(drillData.kind) + ' · ' + drillData.value) : ''"></span><span class="ds-badge ds-badge--neutral" x-text="drillData ? (drillData.n + '건') : ''"></span></h2>
      <div class="ds-dialog__body" style="max-height:64vh;overflow:auto;margin-top:6px">
        <div x-show="drillBusy" class="text-xs text-muted" style="padding:14px">불러오는 중…</div>
        <table class="ds-table" x-show="!drillBusy && drillData && drillData.items.length"><thead><tr><th>서비스</th><th>제목</th><th>등급</th><th>검수</th><th x-show="drillData && drillData.kind==='topic' && drillData.topic_id && topicAdmin"></th></tr></thead><tbody>
          <template x-for="(c,i) in (drillData?drillData.items:[])" x-bind:key="i"><tr style="cursor:pointer" role="button" tabindex="0" x-on:click="openDetail(c)" x-on:keydown.enter="openDetail(c)" data-tip="상세·검수 열기" data-tip-pos="left">
            <td x-text="c.service || '·'"></td>
            <td class="text-ink" x-text="c.title || c.summary || '·'"></td>
            <td><span class="ds-badge" x-bind:class="c.grade==='G'?'ds-badge--success':'ds-badge--error'"><span class="ds-badge__dot"></span><span x-text="c.grade || '·'"></span></span></td>
            <td><span class="ds-badge" x-show="c.fb && c.fb.verdict" x-bind:class="c.fb && c.fb.verdict==='good' ? 'ds-badge--success' : (c.fb && c.fb.verdict==='split' ? 'ds-badge--reason' : 'ds-badge--error')" x-text="c.fb && c.fb.verdict==='good' ? '✓ 완료' : (c.fb && c.fb.verdict==='split' ? '✓ 재검토' : '✓ 수정')"></span><span class="text-xs text-muted" x-show="!(c.fb && c.fb.verdict)">·</span></td>
            <td x-show="drillData && drillData.kind==='topic' && drillData.topic_id && topicAdmin"><button type="button" class="copybtn" x-on:click.stop="topicExclude(c)" data-tip="이 토픽에서 이 콘텐츠만 뺍니다 (조건은 그대로 · 복구 가능)" data-tip-pos="left">제외</button></td>
          </tr></template>
        </tbody></table>
        <div x-show="!drillBusy && drillData && !drillData.items.length" class="text-xs text-muted" style="padding:14px">해당 콘텐츠가 없습니다</div>
      </div>
      <div style="text-align:right;margin-top:14px"><button type="button" class="ds-btn ds-btn--outline ds-btn--c-neutral ds-btn--s-md" x-on:click="drillOpen=false">닫기</button></div>
    </div>
  </div>

  <!-- 새 버전 배너: 배포 감지(서버 부팅 ID 변화) · 분기 없이 새로고침 단일 유도(세션 만료면 새로고침 후 로그인 화면) -->
  <div class="updatebar" x-show="updateAvail" x-cloak role="status">
    <span>새 버전이 배포되었습니다 · 새로고침 후 이용해 주세요</span>
    <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-on:click="location.reload()">새로고침</button>
  </div>
  <!-- 확인 모달(공통): 네이티브 confirm 대체 · 자동화(CDP) 렌더러 블로킹 방지 + DS 일관 -->
  <div class="ds-dialog-backdrop" x-show="confirmOpen" x-cloak x-on:mousedown.self="confirmAnswer(false)" x-on:keydown.escape.window="confirmOpen && confirmAnswer(false)" style="z-index:80">
    <div class="ds-dialog" role="alertdialog" aria-modal="true" aria-label="확인" style="max-width:440px">
      <h2 class="ds-dialog__title" x-text="confirmTitle"></h2>
      <div class="ds-dialog__body"><p style="margin:0;white-space:pre-line" x-text="confirmMsg"></p></div>
      <div class="ds-dialog__footer">
        <button type="button" class="ds-btn ds-btn--ghost" x-on:click="confirmAnswer(false)">취소</button>
        <button type="button" class="ds-btn" x-bind:class="confirmDanger ? 'ds-btn--solid ds-btn--c-danger' : 'ds-btn--primary'" x-on:click="confirmAnswer(true)" x-text="confirmOk"></button>
      </div>
    </div>
  </div>
  <!-- 콘텐츠 상세 스플릿뷰(공통 컴포넌트): 좌 추출 원문 렌더 · 우 평가 -->
  <!-- 정책 팔레트(플로팅 도움말): 검수 중 사전·정책 기준 참조 · 드래그 이동 · 위치 기억 -->
  <button type="button" class="polfab" x-show="!polOpen" x-cloak x-on:click="polToggle()" data-tip="정책 도움말 · 인텐트/카테고리/품질 사유 기준" data-tip-pos="left" aria-label="정책 도움말 열기">?</button>
  <div class="polpal" x-show="polOpen" x-cloak x-ref="polpal" x-bind:style="polStyle()">
    <div class="polpal__hd" x-on:pointerdown="polDragStart($event)">
      <b>정책 도움말</b><span class="meta">드래그로 이동</span>
      <button type="button" class="ds-iconbtn ds-iconbtn--sm ml-auto" x-on:click="polOpen=false; polSave()" aria-label="정책 도움말 닫기"><svg width="14" height="14" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>
    </div>
    <div class="polpal__bd">
      <div class="polpal__ctx" x-show="polCtx().length">
        <span class="text-xs text-muted" style="width:100%">현재 검수 항목의 값 · 눌러서 기준 보기</span>
        <template x-for="(x,xi) in polCtx()" x-bind:key="'pc'+xi+x.v">
          <button type="button" class="ds-badge" x-bind:class="{intent:'ds-badge--intent',category:'ds-badge--category',reason:'ds-badge--reason',grade:(x.v==='G'?'ds-badge--success':'ds-badge--error')}[x.kind]" style="cursor:pointer" x-bind:data-tip="termDef(x.kind, x.v) + ' · 눌러서 기준 보기'" data-tip-pos="top" x-on:click="polShow(x.kind, x.v)" x-text="polCtxLabel(x)"></button>
        </template>
      </div>
      <span class="srcfilter" style="display:flex;gap:var(--ds-space-1)">
        <button type="button" class="srcfilter__chip" x-bind:class="polTab==='intent'?'sel':''" x-on:click="polTab='intent'; polHl=''; polSave()">인텐트</button>
        <button type="button" class="srcfilter__chip" x-bind:class="polTab==='category'?'sel':''" x-on:click="polTab='category'; polHl=''; polSave()">카테고리</button>
        <button type="button" class="srcfilter__chip" x-bind:class="polTab==='quality'?'sel':''" x-on:click="polTab='quality'; polHl=''; polSave()">품질 사유</button>
        <button type="button" class="srcfilter__chip" x-bind:class="polTab==='grade'?'sel':''" x-on:click="polTab='grade'; polHl=''; polSave()">등급</button>
      </span>
      <input type="text" class="field" placeholder="정책 검색 (값·기준 텍스트)" x-model="polQ">
      <div class="polpal__list">
        <!-- 카테고리: Tier1 그룹 → (Tier2 | 정의 | 예시) 표 + 분기 규칙 접힘 -->
        <template x-if="polTab==='category'">
          <div style="display:flex;flex-direction:column;gap:10px">
            <div class="text-xs text-muted">공식 표기는 영문(IAB Content Taxonomy 기반) · 한글은 표시용 병기 · 예시는 초안(팀 확정 대상)</div>
            <template x-for="g in polCatGroups()" x-bind:key="'pg'+g.t1">
              <div class="polsec">
                <div class="polsec__hd" x-bind:data-pol="g.t1" x-bind:class="polHl===g.t1 ? 'is-hl' : ''"><b x-text="catBoth(g.t1)"></b></div>
                <table class="poltbl">
                  <thead><tr><th style="width:172px">Tier2</th><th>정의</th><th style="width:186px">예시</th></tr></thead>
                  <tbody><template x-for="r in g.rows" x-bind:key="'pr'+r.k">
                    <tr x-bind:data-pol="r.k" x-bind:class="polHl===r.k ? 'is-hl' : ''">
                      <td><span class="ds-badge ds-badge--category" x-text="catBoth(r.k)"></span></td>
                      <td x-text="r.def || '·'"></td>
                      <td class="text-muted" x-text="r.ex || '·'"></td>
                    </tr>
                  </template></tbody>
                </table>
                <details class="polrule" x-show="g.rule"><summary>분기 규칙 · 경합 시 우선순위</summary><div x-text="g.rule"></div></details>
              </div>
            </template>
            <div x-show="!polCatGroups().length" class="text-xs text-muted">검색 결과가 없습니다 · 다른 탭도 확인해 보세요</div>
          </div>
        </template>
        <!-- 인텐트: 인텐트 | 정의 | 예시 -->
        <template x-if="polTab==='intent'">
          <div>
            <table class="poltbl">
              <thead><tr><th style="width:158px">인텐트</th><th>정의</th><th style="width:178px">예시 (초안)</th></tr></thead>
              <tbody><template x-for="e in polRows()" x-bind:key="'pi'+e.k">
                <tr x-bind:data-pol="e.k" x-bind:class="polHl===e.k ? 'is-hl' : ''">
                  <td><span class="ds-badge ds-badge--intent" x-text="e.t"></span></td>
                  <td x-text="e.d || '·'"></td>
                  <td class="text-muted" x-text="e.ex || '·'"></td>
                </tr>
              </template></tbody>
            </table>
            <div x-show="!polRows().length" class="text-xs text-muted" style="margin-top:6px">검색 결과가 없습니다 · 다른 탭도 확인해 보세요</div>
          </div>
        </template>
        <!-- 품질 사유: 사유(병기) | 정의·판정 기준 | 적용 -->
        <template x-if="polTab==='quality'">
          <div>
            <table class="poltbl">
              <thead><tr><th style="width:186px">사유</th><th>정의 · 판정 기준</th><th style="width:52px">적용</th></tr></thead>
              <tbody><template x-for="e in polRows()" x-bind:key="'pq'+e.k">
                <tr x-bind:data-pol="e.k" x-bind:class="polHl===e.k ? 'is-hl' : ''">
                  <td><span class="ds-badge ds-badge--reason" x-text="e.t"></span></td>
                  <td x-text="e.d || '·'"></td>
                  <td x-text="e.ex"></td>
                </tr>
              </template></tbody>
            </table>
            <div x-show="!polRows().length" class="text-xs text-muted" style="margin-top:6px">검색 결과가 없습니다 · 다른 탭도 확인해 보세요</div>
          </div>
        </template>
        <!-- 등급: 등급 | 판정 기준 -->
        <template x-if="polTab==='grade'">
          <div>
            <table class="poltbl">
              <thead><tr><th style="width:186px">등급</th><th>판정 기준</th></tr></thead>
              <tbody><template x-for="e in polRows()" x-bind:key="'pgd'+e.k">
                <tr x-bind:data-pol="e.k" x-bind:class="polHl===e.k ? 'is-hl' : ''">
                  <td><span class="ds-badge" x-bind:class="e.k==='G'?'ds-badge--success':(e.k==='R'?'ds-badge--error':'ds-badge--neutral')" x-text="e.t"></span></td>
                  <td x-text="e.d || '·'"></td>
                </tr>
              </template></tbody>
            </table>
          </div>
        </template>
      </div>
    </div>
  </div>
  <div class="ds-dialog-backdrop" x-show="detailOpen" x-cloak x-on:mousedown.self="detailOpen=false" style="z-index:74">
    <div class="detailview" x-bind:class="dvcView()==='web' ? 'detailview--wide' : ''" role="dialog" aria-modal="true" aria-label="콘텐츠 상세">
      <div class="detailview__hd">
        <span style="display:flex;align-items:center;gap:8px">
          <button type="button" class="ds-iconbtn ds-iconbtn--sm" x-show="detailBack" x-on:click="detailOpen=false; drillOpen=true" data-tip="목록으로" data-tip-pos="bottom" aria-label="목록으로 뒤로가기"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          <b>콘텐츠 상세 · 검수</b>
          <span class="text-xs text-muted tnum" x-show="detailNav" x-text="detailNav ? ((detailNav.idx + 1) + ' / ' + detailNav.list.length) : ''"></span>
        </span>
        <span style="display:flex;align-items:center;gap:6px">
          <span class="text-xs text-muted" x-show="detailNav" style="cursor:help" data-tip="단축키 · A 정확 · S 수정 · ← → 이전/다음 · Esc 닫기" data-tip-pos="bottom">⌨ 단축키</span>
          <button type="button" class="ds-iconbtn ds-iconbtn--sm" x-show="detailNav" x-bind:disabled="!detailNav || detailNav.idx <= 0" x-on:click="detailGo(-1)" data-tip="이전 항목 (←)" data-tip-pos="bottom" aria-label="이전 항목"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          <button type="button" class="ds-iconbtn ds-iconbtn--sm" x-show="detailNav" x-bind:disabled="!detailNav || detailNav.idx >= detailNav.list.length - 1" x-on:click="detailGo(1)" data-tip="다음 항목 (→)" data-tip-pos="bottom" aria-label="다음 항목"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M9 5l7 7-7 7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
          <button type="button" class="ds-iconbtn ds-iconbtn--sm" x-on:click="detailOpen=false" aria-label="닫기"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button>
        </span>
      </div>
      <div class="detailview__body">
        <div class="detailview__content">
          <!-- 원문 링크가 있으면 좌측 패널을 '추출 텍스트 ↔ 원문 페이지' 탭으로 전환(검수자 대조 편의) -->
          <div class="dvc__viewtabs" x-show="detail && detail.url" x-cloak>
            <span class="srcfilter" style="display:inline-flex;gap:var(--ds-space-1)">
              <button type="button" class="srcfilter__chip" x-bind:class="dvcView()==='text'?'sel':''" x-on:click="setDvcView('text')">추출 텍스트</button>
              <button type="button" class="srcfilter__chip" x-bind:class="dvcView()==='web'?'sel':''" x-on:click="setDvcView('web')">원문 페이지</button>
            </span>
            <!-- 배율 3단계: 작게(50)·보통(75)·크게(100) · 축소하면 한 화면에 더 담긴다 · 선택은 기억 -->
            <span class="srcfilter" style="display:inline-flex;gap:var(--ds-space-1);margin-left:auto" x-show="dvcView()==='web'" x-cloak>
              <template x-for="o in [{z:50,t:'작게'},{z:75,t:'보통'},{z:100,t:'크게'}]" x-bind:key="'dz'+o.z">
                <button type="button" class="srcfilter__chip" x-bind:class="dvcZoom===o.z?'sel':''" x-on:click="setDvcZoom(o.z)" x-bind:data-tip="o.z+'%'" data-tip-pos="top" x-text="o.t"></button>
              </template>
            </span>
          </div>
          <div class="dvc__service" x-text="detail && (detail.service || '·')"></div>
          <h2 class="dvc__title" x-text="detail && (detail.title || '(제목 없음)')"></h2>
          <div class="dvc__subtitle" x-show="detail && detail.subtitle" x-text="detail && detail.subtitle"></div>
          <template x-if="dvcView()==='text'">
            <div>
              <div class="dvc__bodytext" x-show="detail && detail.body" x-text="detail && detail.body"></div>
              <div class="dvc__note" x-show="detail && !detail.body">전체 본문은 저장되지 않습니다 · 리드문·메타 기준으로 검수하세요</div>
            </div>
          </template>
          <template x-if="dvcView()==='web'">
            <div class="dvc__webwrap">
              <div class="dvc__webbox">
                <iframe class="dvc__web" x-bind:src="detail && detail.url" x-bind:style="'transform:scale('+(dvcZoom/100)+');width:'+(10000/dvcZoom)+'%;height:'+(10000/dvcZoom)+'%'" sandbox="allow-scripts allow-same-origin allow-popups allow-forms" referrerpolicy="no-referrer" loading="lazy" title="원문 페이지"></iframe>
              </div>
              <div class="dvc__note" style="margin-top:var(--ds-space-2)">화면이 비어 보이면 이 사이트가 내장 표시를 차단한 것입니다</div>
            </div>
          </template>
        </div>
        <div class="detailview__eval">
          <div x-show="detail && (detail.grade || detail.model)" class="flex flex-wrap items-center gap-1.5"><span class="ds-badge" x-bind:class="detail && detail.grade==='G'?'ds-badge--success':(detail && detail.grade==='R'?'ds-badge--error':'ds-badge--reason')" x-bind:data-tip="detail && !detail.grade ? '품질 호출이 실패해 판정을 보류했습니다 · 재실행하면 다시 판정합니다' : ''" data-tip-pos="top"><span class="ds-badge__dot"></span><span x-text="detail && (detail.grade==='G'?'유통 가능 · G':(detail.grade==='R'?'차단 · R':'판정 보류 · 재실행 필요'))"></span></span><span class="ds-badge ds-badge--intent" style="cursor:help" x-show="detail && detail.model" data-tip="이 결과 초안을 만든 모델 · 교정 피드백이 이 모델 프롬프트로 귀속됩니다" data-tip-pos="top" x-text="detail ? detail.model : ''"></span></div>
          <!-- 리드문 즉시 확인: 좌측이 원문 페이지 탭일 때도 우측에서 초안 리드문을 대조(회의 소요) -->
          <div class="dve__sec" x-show="detail && detail.summary"><div class="dve__lbl">리드문</div><div class="dve__lead" x-text="detail && detail.summary"></div></div>
          <div class="dve__sec"><div class="dve__lbl">엔티티</div><div class="flex flex-wrap gap-1"><template x-for="e in (detail?detail.entities:[])" x-bind:key="e"><span class="ds-badge ds-badge--entity" style="cursor:help" x-bind:data-tip="termDef('entity', e)" data-tip-pos="top" x-text="e"></span></template><span x-show="detail && !detail.entities.length" class="text-xs text-muted">·</span></div></div>
          <div class="dve__sec"><div class="dve__lbl">인텐트</div><div class="flex flex-wrap gap-1"><template x-for="e in (detail?detail.intent:[])" x-bind:key="e"><span class="ds-badge ds-badge--intent" style="cursor:pointer" x-bind:data-tip="termDef('intent', e) + ' · 눌러서 기준 보기'" data-tip-pos="right" x-on:click="polShow('intent', e)" x-text="e"></span></template><span x-show="detail && !detail.intent.length" class="text-xs text-muted">·</span></div></div>
          <div class="dve__sec"><div class="dve__lbl">카테고리</div>
            <div class="flex flex-wrap gap-1 items-center">
              <template x-for="e in (detail?detail.category:[])" x-bind:key="e"><span class="ds-badge ds-badge--category" style="cursor:pointer" x-bind:data-tip="termDef('category', e) + ' · 눌러서 기준 보기'" data-tip-pos="right" x-on:click="polShow('category', e)" x-text="catKo(e)"></span></template>
              <!-- 빈칸 감지 → 분류 필요 + 구조화 채우기(IAB Tier1/2) -->
              <template x-if="detail && !detail.category.length">
                <span style="display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap">
                  <span class="ds-badge ds-badge--error">미분류 · 분류 필요</span>
                  <select class="field" style="height:30px;width:auto;padding:0 24px 0 8px;font-size:12px" x-on:change="fillCategory(detail, $event.target.value); $event.target.value=''">
                    <option value="">분류 선택…</option>
                    <template x-for="opt in categoryOptions" x-bind:key="opt"><option x-bind:value="opt" x-text="catKo(opt)"></option></template>
                  </select>
                </span>
              </template>
            </div>
          </div>
          <div class="dve__sec" x-show="detail && detail.reasons && detail.reasons.length"><div class="dve__lbl">품질 사유</div><div class="flex flex-wrap gap-1"><template x-for="e in (detail?detail.reasons:[])" x-bind:key="e"><span class="ds-badge ds-badge--reason" style="cursor:pointer" x-bind:data-tip="termDef('reason', e) + ' · 눌러서 기준 보기'" data-tip-pos="right" x-on:click="polShow('reason', e)" x-text="reasonBoth(e)"></span></template></div></div>
          <!-- 작업 이력: 판정·교정·재실행 타임라인(접이식 · 회의 소요) · 운영은 팀 생성자·슈퍼관리자 전용 -->
          <div class="dve__sec" x-show="backend !== 'supabase' || (adminData && adminData.isSuperAdmin)" x-cloak>
            <button type="button" class="copybtn" x-on:click="toggleHistory()" x-text="histOpen ? '작업 이력 닫기' : '작업 이력 보기'"></button>
            <div x-show="histOpen" x-cloak class="histbox">
              <div class="text-xs text-muted" x-show="histBusy">불러오는 중…</div>
              <template x-for="(h,hi) in histItems" x-bind:key="hi">
                <div class="histrow">
                  <span class="histrow__ts tnum" x-text="histWhen(h.ts)"></span>
                  <span class="histrow__who" x-show="h.who" x-text="h.who"></span>
                  <span class="histrow__label" x-bind:class="h.kind==='verdict' ? '' : 'is-sub'" x-text="h.label"></span>
                  <span class="histrow__note" x-show="h.note" x-bind:data-tip="h.note" data-tip-pos="left">메모</span>
                </div>
              </template>
              <div x-show="!histBusy && !histItems.length" class="text-xs text-muted">아직 기록이 없습니다</div>
            </div>
          </div>
          <div class="dve__verdict">
            <div class="dve__lbl" style="display:flex;align-items:center;gap:8px">검수 판정
              <label x-show="detailNav" class="chk-inline" style="font-size:11px;font-weight:400;color:var(--ds-muted);margin-left:auto">
                <input type="checkbox" x-model="autoNext" x-on:change="saveAutoNext()">저장 후 다음 미검수로
              </label>
            </div>
            <!-- 검수 완료 = '내 표(myVerdict)' 기준(목록·다음 미검수 이동과 동일 · 2026-07-10):
                 남이 검수한 콘텐츠도 내가 안 했으면 아래 판정 UI 가 뜬다. 완료 시 팀 합의 3상태 + 내 판정 병기 + 추가 수정 -->
            <template x-if="detail && myVerdict(detail.fb) && !editVerdict">
              <div>
                <!-- 팀 표 2개 이상이면 주어를 명시(팀 판정)하고 내 판정을 분리 표기 · 단독 판정은 기존 표기 -->
                <span class="ds-badge" x-bind:class="detail.fb.verdict==='good' ? 'ds-badge--success' : (detail.fb.verdict==='split' ? 'ds-badge--reason' : 'ds-badge--error')" x-bind:data-tip="detail.fb.verdict==='split' ? '정확과 수정 필요로 의견이 갈렸습니다 · 재검토 대상' : ''" data-tip-pos="top"><span class="ds-badge__dot"></span><span x-text="detail.fb.n > 1 ? (detail.fb.verdict==='good' ? '팀 판정 · 정확' : (detail.fb.verdict==='split' ? '팀 판정 · 의견 갈림' : '팀 판정 · 수정 필요')) : (detail.fb.verdict==='good' ? '검수 완료 · 정확' : '검수 완료 · 수정 필요')"></span></span>
                <span class="text-xs text-muted" x-show="detail.fb.ts" x-text="'· 최종 수정 ' + fmtTs(detail.fb.ts)" style="margin-left:6px"></span>
                <div class="dve__mine" x-show="detail.fb.n > 1" x-cloak>
                  <span class="dve__mine-lbl">내 판정</span>
                  <span class="ds-badge" x-bind:class="detail.fb.mine==='good' ? 'ds-badge--success' : (detail.fb.mine==='bad' ? 'ds-badge--error' : 'ds-badge--neutral')" x-text="detail.fb.mine==='good' ? '정확' : (detail.fb.mine==='bad' ? '수정 필요' : '아직 없음')"></span>
                  <span class="text-xs text-muted" x-text="'팀 의견 · 정확 ' + (detail.fb.good||0) + '개 · 수정 필요 ' + (detail.fb.bad||0) + '개'"></span>
                </div>
                <div class="tbox" x-show="myVerdict(detail.fb)==='bad' && detail.fb.note" style="margin-top:8px" x-text="detail.fb.note"></div>
                <div style="display:flex;gap:var(--ds-space-2);margin-top:10px">
                  <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-sm" x-on:click="openEditVerdict()">추가 수정</button>
                  <button type="button" class="ds-btn ds-btn--outline ds-btn--s-sm" x-show="detail.fb.mine || (backend !== 'supabase' && detail.fb.n === 1)" x-cloak x-on:click="undoVerdict()" data-tip="내 표만 취소합니다 · 다른 검수자의 판정은 그대로 유지됩니다" data-tip-pos="top">내 판정 취소</button>
                </div>
              </div>
            </template>
            <!-- 미검수(내 표 없음) 또는 추가 수정 중 -->
            <template x-if="detail && (!myVerdict(detail.fb) || editVerdict)">
              <div>
                <div class="text-xs text-muted" x-show="detail.fb && detail.fb.n && !myVerdict(detail.fb)" x-cloak style="margin-bottom:8px" x-text="'팀 의견 · 정확 ' + (detail.fb.good||0) + '개 · 수정 필요 ' + (detail.fb.bad||0) + '개 · 내 판정을 남겨주세요'"></div>
                <div style="display:flex;gap:8px">
                  <button type="button" class="verdictbtn verdictbtn--good" x-bind:class="!pendingBad && detail && myVerdict(detail.fb)==='good' ? 'is-on' : ''" x-on:click="reviewGood()"><span class="verdictbtn__dot"></span>정확</button>
                  <button type="button" class="verdictbtn verdictbtn--bad" x-bind:class="pendingBad ? 'is-on' : ''" x-on:click="pendingBad=true"><span class="verdictbtn__dot"></span>수정 필요</button>
                </div>
                <!-- 수정 필요: 요소·사유 입력 후 '완료 처리' 로만 확정 -->
                <template x-if="pendingBad">
                  <div style="margin-top:10px">
                    <div class="dve__lbl" style="margin-bottom:6px">어떤 요소를 고칠까요?</div>
                    <div class="fixelems">
                      <template x-for="fe in FIX_ELEMENTS" x-bind:key="fe.id">
                        <button type="button" class="fixelem" x-bind:class="fbElems(detail.fb).includes(fe.id) ? 'sel' : ''" x-on:click="toggleFixElem(detail.fb, fe.id)" x-text="fe.label"></button>
                      </template>
                    </div>
                    <textarea x-model="detail.fb.note" rows="3" class="field" style="margin-top:8px" x-bind:placeholder="fbElems(detail.fb).map((e)=>elemLabel(e)).join('·') + ' 이(가) 왜 잘못됐는지 · 요소를 여러 개 고르면 각 단계로 나눠 반영됩니다'"></textarea>
                    <div style="display:flex;gap:var(--ds-space-2);margin-top:8px">
                      <button type="button" class="ds-btn ds-btn--primary ds-btn--s-sm" x-on:click="reviewBadComplete()" x-bind:disabled="!(detail.fb.note||'').trim()" data-tip="선택한 요소와 메모를 '수정 필요' 판정으로 저장합니다" data-tip-pos="top">교정 저장</button>
                      <button type="button" class="ds-btn ds-btn--secondary ds-btn--s-sm" x-on:click="pendingBad=false; if(!myVerdict(detail.fb)) editVerdict=false">취소</button>
                    </div>
                  </div>
                </template>
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ⚙ 설정 = 고정 팝업(Dialog) · 위젯 아님(§9.5) -->
  <!-- 플로팅 도우미(채널톡 스타일) · 모든 기능 허브 -->
  <div class="ds-chat" x-show="chatOpen" x-cloak>
    <div class="ds-chat__head">
      <span class="ds-chat__av"><img x-bind:src="charImg(reviewer ? reviewerChar : 'boksil')" alt=""></span>
      <div><div class="ds-chat__title" x-text="(reviewer || 'Prism') + ' 에이전트'"></div><div class="ds-chat__sub"><span class="ds-statusdot ds-statusdot--ok"><span class="ds-statusdot__dot"></span></span>보통 1분 내 응답</div></div>
      <span style="margin-left:auto"><button type="button" class="ds-iconbtn ds-iconbtn--sm" x-on:click="chatOpen = false" aria-label="닫기"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></button></span>
    </div>
    <div class="ds-chat__body">
      <template x-for="(m, i) in chatMsgs" x-bind:key="i"><div class="ds-chat__msg" x-bind:class="m.from === 'me' ? 'ds-chat__msg--me' : 'ds-chat__msg--bot'" x-text="m.text"></div></template>
    </div>
    <div class="ds-chat__quick">
      <button type="button" class="chatchip" x-on:click="chatAct('extract')">＋ 새 추출</button>
      <button type="button" class="chatchip" x-on:click="chatAct('dict')">사전 편집</button>
      <button type="button" class="chatchip" x-on:click="chatAct('settings')">설정</button>
    </div>
    <div class="ds-chat__foot"><textarea class="ds-chat__input" x-model="chatDraft" rows="1" placeholder="작업을 지시하세요…" x-on:keydown.enter.prevent="chatSend()"></textarea><button type="button" class="ds-chat__send" x-on:click="chatSend()" aria-label="보내기"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 8h10M8 4l4 4-4 4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button></div>
  </div>

  <!-- 복사 토스트 -->
  <div x-show="copyMsg" x-cloak x-transition.opacity class="toast" x-text="copyMsg" aria-live="polite"></div>

</div>

<!-- 위젯 홈 인터랙션(편집·리사이즈·드래그·틸트) · SERVICE_DESIGN §4.3 규격 -->
<script>
  // ── 캐릭터 사용 정책(공통 규칙) ──────────────────────────────────────
  // 캐릭터 = 추출 파이프라인 4단계 역할 위젯/뷰는 자신이 속한 단계의 캐릭터만 쓴다
  //   추출 대식 · 분석 용희 · 검수 복실 · 판정·부여 딱지
  // ① 모듈(뷰) = 그 모듈의 파이프라인 역할  ② 프롬프트 스튜디오 = 단계별 패널이 그 단계
  // ③ 홈 위젯 = 위젯 역할(data-wid)  ④ 도우미·빈 상태 = 복실(안내)
  var CHAR = { extract: 'daesik-batter', analyze: 'yonghee-pitcher', review: 'boksil-catcher', judge: 'ddakji-manager' };
  var MOD_STAGE = { auto: 'extract', run: 'extract', queue: 'extract', intake: 'extract',
                    dash: 'analyze', user: 'analyze', topic: 'analyze',
                    quality: 'review', eval: 'review', dict: 'judge', prompt: null };
  var WID_STAGE = { 'launch-run': 'extract', 'launch-batch': 'analyze', 'launch-dict': 'judge',
                    metrics: 'extract', quality: 'review', intents: 'analyze', categories: 'analyze', process: 'analyze' };
  function stageChar(st) { return CHAR[st] || 'yonghee-pitcher'; }
  function panelStage(t) {
    t = t || '';
    if (/추출/.test(t)) return 'extract';
    if (/분석/.test(t)) return 'analyze';
    if (/검수|품질/.test(t)) return 'review';
    if (/판정|부여|법령/.test(t)) return 'judge';
    return 'analyze';
  }
  function viewMod(el) { var v = el.closest('[x-show]'); if (!v) return ''; var m = (v.getAttribute('x-show') || '').match(/mod === '(\\w+)'/); return m ? m[1] : ''; }
  function prismCharForPanel(h, titleText) {
    var mod = viewMod(h);
    if (mod === 'studio') return stageChar(panelStage(titleText));   // 단계별
    var st = MOD_STAGE[mod];
    return st ? stageChar(st) : stageChar(panelStage(titleText));
  }
  // 카드 속성(기능/정보) = 캐릭터+칩 고정 클러스터. 종류별 캐릭터 통일 · 항상 헤드 우측 끝.
  var KIND_CHAR = { fn: 'boksil-catcher', info: 'yonghee-pitcher' };
  function prismCardType(isFn) {
    var kind = isFn ? 'fn' : 'info';
    var wrap = document.createElement('span'); wrap.className = 'ds-cardtype';
    wrap.innerHTML = '<span class="wz-char wz-char--sm"><img src="/vendor/' + KIND_CHAR[kind] + '.svg" alt=""></span>'
      + '<span class="ds-widget__kind ds-widget__kind--' + kind + '">' + (isFn ? '기능' : '정보') + '</span>';
    return wrap;
  }
  // 헤더 액션: 정보(메타·라벨)와 컨트롤(버튼) 경계에 구분선 자동 삽입(전 카드 공통). 카드속성 구분선과 동일 규칙.
  function insertHdDivider(actions) {
    var kids = Array.prototype.slice.call(actions.children);
    for (var i = 1; i < kids.length; i++) {
      var el = kids[i];
      var isCtrl = el.matches && el.matches('button, a.ds-btn, .ds-btn, .copybtn, .ds-iconbtn');
      if (isCtrl && !(kids[i - 1].classList && kids[i - 1].classList.contains('hd-divider'))) {
        var d = document.createElement('span'); d.className = 'hd-divider'; d.setAttribute('aria-hidden', 'true');
        actions.insertBefore(d, el);
        break;                                      // 정보|컨트롤 경계 1곳
      }
    }
  }
  (function () {
    var grid = document.getElementById('grid'); if (!grid) return;
    var ORDER = ['', 'ds-widget--md', 'ds-widget--lg', 'ds-widget--tall', 'ds-widget--wide', 'ds-widget--xl'];
    function editing() { return grid.classList.contains('ds-widgetgrid--edit'); }
    // 선택 + 8핸들(우하단 리사이즈)
    var SELH = '<div class="ds-widget__sel" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>';
    var selected = null;
    function deselect() { if (selected) { selected.classList.remove('ds-widget--selected'); var s = selected.querySelector('.ds-widget__sel'); if (s) s.remove(); selected = null; } }
    function select(w) { deselect(); w.classList.add('ds-widget--selected'); w.insertAdjacentHTML('beforeend', SELH); selected = w; }
    function cycleSize(w) {
      var cur = 0; ORDER.forEach(function (c, i) { if (c && w.classList.contains(c)) cur = i; });
      ORDER.forEach(function (c) { if (c) w.classList.remove(c); });
      var nx = ORDER[(cur + 1) % ORDER.length]; if (nx) w.classList.add(nx);
    }
    grid.addEventListener('click', function (e) {
      if (!editing()) return;
      if (e.target.closest('.ds-widget__remove')) return;   // 제거는 Alpine removeWidget 가 처리
      var rs = e.target.closest('.ds-widget__sel i:nth-child(5)');
      if (rs && selected) { cycleSize(selected); return; }
      var w = e.target.closest('.ds-widget');
      if (w) { if (w !== selected) select(w); } else deselect();
    });
    // 드래그 재배치
    var dragEl = null;
    grid.addEventListener('dragstart', function (e) { if (!editing()) return; dragEl = e.target.closest('.ds-widget'); if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'; });
    grid.addEventListener('dragover', function (e) { if (!editing() || !dragEl) return; e.preventDefault(); var t = e.target.closest('.ds-widget'); if (t && t !== dragEl) { var r = t.getBoundingClientRect(); var after = (e.clientY - r.top) / r.height > 0.5; grid.insertBefore(dragEl, after ? t.nextSibling : t); } });
    grid.addEventListener('drop', function (e) { e.preventDefault(); dragEl = null; });
    new MutationObserver(function () {
      var on = editing();
      grid.querySelectorAll('.ds-widget').forEach(function (w) { w.setAttribute('draggable', on ? 'true' : 'false'); });
      if (!on) deselect();
    }).observe(grid, { attributes: true, attributeFilter: ['class'] });
    // 카드 3D 틸트 + 포일
    document.querySelectorAll('.ds-personacard').forEach(function (card) {
      card.addEventListener('pointermove', function (e) { var r = card.getBoundingClientRect(), px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height; card.style.setProperty('--ry', ((px - .5) * 16) + 'deg'); card.style.setProperty('--rx', ((.5 - py) * 16) + 'deg'); card.style.setProperty('--mx', (px * 100) + '%'); card.style.setProperty('--my', (py * 100) + '%'); });
      card.addEventListener('pointerleave', function () { card.style.setProperty('--ry', '0deg'); card.style.setProperty('--rx', '0deg'); card.style.setProperty('--mx', '50%'); card.style.setProperty('--my', '30%'); });
    });
    // 홈 위젯: 왼쪽 아이콘 제거(타이틀 왼쪽선 = 본문 정렬) + 캐릭터를 헤드 오른쪽으로
    grid.querySelectorAll('.ds-widget__head').forEach(function (head) {
      var t = head.querySelector('.ds-widget__title'); if (!t) return;
      var chip = t.querySelector('.ds-widget__icon-chip');
      var w = head.closest('.ds-widget'); var wid = w ? (w.getAttribute('data-wid') || '') : '';
      if (chip) chip.remove();
      var actions = head.querySelector('.ds-widget__actions');
      if (!actions) { actions = document.createElement('div'); actions.className = 'ds-widget__actions'; head.appendChild(actions); }
      var ek = actions.querySelector('.ds-widget__kind');            // 기존 인라인 칩 → 통일 클러스터로 교체
      var isFn = ek ? ek.classList.contains('ds-widget__kind--fn') : false;
      if (ek) ek.remove();
      insertHdDivider(actions);                                       // 정보|컨트롤 구분선
      actions.appendChild(prismCardType(isFn));                       // 캐릭터+칩 고정(우측 끝)
    });
  })();

  // ── 출력 패널(.panel) → 카탈로그 위젯 헤드로 변환(아이콘칩 + 타이틀 + 정보 칩) ──
  // 정적 패널은 즉시, Alpine x-for 로 생성되는 동적 패널은 MutationObserver 로 덮는다
  (function () {
    function decorate(root) {
      (root || document).querySelectorAll('.panel-hd:not([data-wz])').forEach(function (h) {
        h.setAttribute('data-wz', '1');
        var b = h.querySelector('b');
        var titleText = b ? b.textContent : '';
        // 타이틀 = 텍스트만(왼쪽선을 본문과 정렬) 캐릭터는 헤드 오른쪽으로
        var title = document.createElement('div'); title.className = 'ds-widget__title';
        var span = document.createElement('span');
        span.textContent = titleText; if (b) b.remove();
        title.appendChild(span);
        var actions = document.createElement('div'); actions.className = 'ds-widget__actions';
        while (h.firstChild) actions.appendChild(h.firstChild);   // 남은 메타·버튼 → actions
        insertHdDivider(actions);                                 // 정보|컨트롤 구분선(전 카드 공통)
        actions.appendChild(prismCardType(!!h.closest('[data-fn]')));   // 캐릭터+칩 고정 클러스터(우측 끝)
        h.appendChild(title); h.appendChild(actions);
      });
    }
    decorate(document);
    var host = document.querySelector('.home') || document.body;
    var pending = false;
    new MutationObserver(function () {
      if (pending) return; pending = true;
      requestAnimationFrame(function () { pending = false; decorate(document); });
    }).observe(host, { childList: true, subtree: true });
  })();
</script>
</body>
</html>"""
