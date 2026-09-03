"""Prism 앱 HTML 마크업(단일 페이지 · serve 에서 분리 · 라우트 분리 3차).

마크업은 화면 섹션 조각(prism/ui/NN-*.html)을 파일명 순으로 이어붙여 PAGE 로 합성한다
(마크업 분할 5차: 섹션 파일이 편집·충돌 단위 — 여러 세션이 서로 다른 화면을 동시 수정 가능.
새 화면 모듈은 새 조각 파일로 추가 · 조각 순서 = 파일명 숫자 접두).
앱 스크립트·스타일은 /vendor/app.js·app.css(분리 파일)를 참조한다.
정적 데모는 scripts/make_demo.py 가 이 마크업을 원천으로 재인라인·치환한다.

합성 중 `<x-modelpick …>` 은 모델 선택 드롭다운 마크업으로 펼쳐진다(_expand_modelpick).
같은 드롭다운이 10곳에 들어가는데 마크업을 10벌 복사하면 한 곳만 고쳐지는 사고가 나서,
조각에는 한 줄만 쓰고 펼치기는 여기 한 곳에서 한다. 데이터는 vendor/app-13-modelpick.js.
"""
import html
import os
import re

_UI_DIR = os.path.join(os.path.dirname(__file__), "ui")

_MPICK_RE = re.compile(r"<x-modelpick\b([^>]*?)/?>(?:\s*</x-modelpick>)?", re.S)
_ATTR_RE = re.compile(r"([a-zA-Z-]+)\s*=\s*\"(.*?)\"", re.S)


def _q(s: str) -> str:
    """Alpine 식 안에 넣을 작은따옴표 문자열 리터럴 이스케이프."""
    return str(s or "").replace("\\", "\\\\").replace("'", "\\'")


def _mpick_markup(a: dict) -> str:
    """<x-modelpick> 속성 → Alpine 드롭다운 마크업.

    key    : 인스턴스 구분 접두(x-for 키 충돌 방지)
    value  : 현재 값을 주는 Alpine 식(예: rawModel · textValue)
    set    : 고를 때 실행할 식 · $v 가 고른 값으로 치환된다(예: rawModel=$v)
    options: 문자열 배열 식(availableModels 등) · groups 와 택일
    groups : 제공자 그룹 배열 식(textGroups) · options 와 택일
    first-label / first-value : 맨 위 고정 항목(전체·현재 설정 모델 등 · 선택)
    suffix : 이름 뒤 꼬리(예: ' 전용' · 선택)
    width  : 버튼 최소 너비(기본 200px) · tip / tip-pos : 버튼 툴팁(선택)
    """
    key = a.get("key") or "mp"
    src = a.get("groups") or a.get("options") or "[]"
    setter = (a.get("set") or "").replace("$v", "row.value")
    width = a.get("width") or "200px"
    opts = []
    if "first-label" in a:
        opts.append("first:{label:'%s',value:'%s'}" % (_q(a["first-label"]), _q(a.get("first-value", ""))))
    if a.get("suffix"):
        opts.append("suffix:'%s'" % _q(a["suffix"]))
    o = "{" + ",".join(opts) + "}"
    tip = a.get("tip") or ""
    tip_attr = (' data-tip="%s" data-tip-pos="%s"'
                % (html.escape(tip, quote=True), a.get("tip-pos") or "bottom")) if tip else ""
    return (
        '<div class="mpick" x-data="{ mpOpen:false, mpBox:{} }" x-on:keydown.escape="mpOpen=false"'
        ' x-on:click.outside="mpOpen=false" x-on:scroll.window="mpOpen=false"'
        ' x-on:resize.window="mpOpen=false"%(tip)s>'
        '<button type="button" class="field mpick__btn" style="min-width:%(w)s"'
        ' x-on:click="mpOpen=!mpOpen; if(mpOpen) mpBox=mpMenuBox($el)"'
        ' x-bind:aria-expanded="mpOpen ? \'true\' : \'false\'" aria-haspopup="listbox">'
        '<span class="mpick__ico" x-bind:data-fam="mpCurFamily(%(src)s,%(val)s,%(o)s)"'
        ' x-text="mpIcon(mpCurFamily(%(src)s,%(val)s,%(o)s))"></span>'
        '<span class="mpick__cur" x-text="mpCurLabel(%(src)s,%(val)s,%(o)s)"></span>'
        '<svg class="mpick__caret" viewBox="0 0 24 24" fill="none" aria-hidden="true">'
        '<path d="m6 9 6 6 6-6" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"'
        ' stroke-linejoin="round"/></svg>'
        '</button>'
        # 규칙(2026-09-03): 모델 픽커가 있는 곳엔 무조건 '모델 새로고침'이 붙는다 — 픽커 합성 단계에서
        # 자동으로 넣어 새 화면도 빠지지 않게 한다. 호출 가능한 모델 실목록(Solar 실조회 + 라우터)을 다시 받는다.
        '<button type="button" class="mpick__refresh" x-bind:disabled="cfgBusy"'
        ' x-on:click.stop="loadModels()" x-bind:title="cfgBusy ? \'불러오는 중…\' : \'모델 새로고침 · 호출 가능한 모델 목록을 다시 불러옵니다\'"'
        ' aria-label="모델 새로고침">'
        '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true" x-bind:class="cfgBusy ? \'is-spin\' : \'\'">'
        '<path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5" stroke="currentColor" stroke-width="1.9"'
        ' stroke-linecap="round" stroke-linejoin="round"/></svg></button>'
        # 메뉴는 body 로 텔레포트한다: 패널이 overflow:hidden 인 데다 :hover 에 transform 이
        # 걸려 있어(카드 살짝 뜨는 효과) fixed 로도 패널 안에 갇힌다 — 조상에서 빼내야 안 잘린다.
        # click.stop 은 메뉴 안 클릭이 루트의 click.outside 를 건드리지 않게 한다.
        # x-transition 은 쓰지 않는다 — 텔레포트된 요소에서는 떠날 때 전환이 끝나지 않아
        # 메뉴가 display:block 으로 남는다(Alpine 3.14 확인). x-show 만으로 여닫는다.
        '<template x-teleport="body">'
        '<div class="mpick__menu" x-show="mpOpen" x-cloak x-bind:style="mpBox" x-on:click.stop'
        ' role="listbox">'
        '<template x-for="(row, ri) in mpRows(%(src)s,%(o)s)" x-bind:key="\'%(key)s\'+ri">'
        '<div class="mpick__row">'
        '<div class="mpick__grp" x-show="row.head"><span x-text="row.label"></span>'
        '<span class="mpick__off" x-show="row.head && !row.on">미연결</span></div>'
        '<button type="button" class="mpick__opt" x-show="!row.head" x-bind:disabled="!row.on"'
        ' x-bind:class="(%(val)s)===row.value ? \'is-on\' : \'\'" x-bind:title="row.tip" role="option"'
        ' x-on:click="%(set)s; mpOpen=false">'
        '<span class="mpick__ico" x-bind:data-fam="row.family" x-text="row.icon"></span>'
        '<span class="mpick__nm" x-text="row.label"></span>'
        '<span class="mpick__tier" x-show="row.tierLabel" x-bind:data-tier="row.tier" x-text="row.tierLabel"></span>'
        '<span class="mpick__avg" x-show="row.avgText" x-text="row.avgText"></span>'
        '</button></div></template>'
        '</div></template></div>'
    ) % {"key": key, "src": src, "val": a.get("value") or "''", "set": setter, "o": o,
         "w": width, "tip": tip_attr}


def _expand_modelpick(markup: str) -> str:
    return _MPICK_RE.sub(lambda m: _mpick_markup(dict(_ATTR_RE.findall(m.group(1) or ""))), markup)


def _compose() -> str:
    names = sorted(n for n in os.listdir(_UI_DIR) if n.endswith(".html"))
    parts = []
    for n in names:
        with open(os.path.join(_UI_DIR, n), encoding="utf-8") as f:
            parts.append(f.read())
    return _expand_modelpick("".join(parts))


PAGE = _compose()
