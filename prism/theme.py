"""생성 리포트(아이템 메타·토픽·사용자 메타) 공통 디자인 테마: 단일 소스.

현재 Prism 디자인 시스템(`vendor/ds-theme.css`, Anchor semantic 토큰)을 리포트에 이식한다.
각 템플릿은 `inject(html)` 로 `<head>`에 폰트, `</style>` 앞에 토큰+컴포넌트를 끼워 넣는다.

토큰 계층:
  1) `--ds-*` 시맨틱 토큰 — 라이트 기본(:root) + 다크 자동(prefers-color-scheme / [data-theme=dark]).
     값은 ds-theme.css 와 동기화(무채색 캔버스 + Blue Primary/Red Accent · Pretendard).
  2) 레거시 별칭(--bg/--card/--line/--ac/--fg …) → `--ds-*` 참조. 기존 템플릿 CSS 를
     통째로 고치지 않고도 팔레트·폰트·모드전환이 일괄 통일되게 한다(별칭이 --ds-* 를 가리켜
     라이트/다크에 따라 자동 swap). 신규 스타일은 `--ds-*` 를 직접 쓴다.

폰트:
  - 디스플레이(제목·큰 숫자·탭) GmarketSans → **@font-face data URI 로 인라인 임베드**.
    리포트는 서빙(앱 '전체 리포트')뿐 아니라 파일로 저장·공유·CLI 출력되므로, /vendor 링크만으론
    비서빙 컨텍스트에서 브랜드 디스플레이 폰트가 빠졌다(제목이 폴백 Pretendard 로 나옴).
    인라인이면 서빙/로컬파일/공유 어디서든 GmarketSans 가 항상 뜬다(외부 요청 0).
  - 본문 Pretendard → `/vendor/pretendard.css`(서빙 시) + 스택의 시스템 Pretendard/-apple-system
    폴백(비서빙 시). 본문은 널리 설치된 폰트라 인라인 없이도 자연스럽게 표시된다.
텍스트 원칙: em-dash 금지 · 값 태그엔 .hint 호버 정의.
"""

import base64
import os

_VENDOR_DIR = os.path.join(os.path.dirname(__file__), "vendor")

# <head> 폰트: 본문 Pretendard(서빙 시 /vendor · 비서빙 시 시스템 폴백). 디스플레이는 아래 인라인.
FONT_HEAD = '<link rel="stylesheet" href="/vendor/pretendard.css">'

_GMARKET_FACE = None


def _gmarket_fontface() -> str:
    """GmarketSans(Bold) woff2 를 base64 @font-face 로 인라인. 1회 로드 후 캐시.
    weight 범위 500~800 → 제목의 600·700 이 모두 이 페이스를 쓴다(별도 웨이트 불필요)."""
    global _GMARKET_FACE
    if _GMARKET_FACE is None:
        try:
            with open(os.path.join(_VENDOR_DIR, "GmarketSansBold.woff2"), "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            _GMARKET_FACE = (
                "@font-face{font-family:'GmarketSans';font-weight:500 800;font-style:normal;"
                "font-display:swap;src:url(data:font/woff2;base64,%s) format('woff2')}" % b64)
        except OSError:
            _GMARKET_FACE = ""
    return _GMARKET_FACE

# ── 1) --ds-* 시맨틱 토큰(라이트 :root) + 2) 레거시 별칭 → --ds-* ──────────────
# 템플릿의 :root 보다 뒤에 주입되어 별칭 값이 통일된다.
TOKENS = r""":root{
--ds-primary:#1e84ff;--ds-primary-hover:#0066db;--ds-primary-deep:#004fad;--ds-primary-tint:rgba(30,132,255,.16);
--ds-ink:#000;--ds-canvas:#f4f5f7;--ds-surface:#fff;--ds-surface-white:#fff;--ds-surface-on:#f4f5f7;
--ds-body:rgba(0,0,0,.88);--ds-muted:rgba(0,0,0,.48);--ds-placeholder:rgba(0,0,0,.32);
--ds-hairline:rgba(0,0,0,.08);--ds-hairline-soft:rgba(0,0,0,.04);--ds-divider-inline:rgba(0,0,0,.16);
--ds-success:#18ba45;--ds-success-deep:#0f8f36;--ds-error:#ff4e33;--ds-error-deep:#d63a20;
--ds-warning:#ff9429;--ds-info:#1e84ff;--ds-on-primary:#fff;--ds-text-static-white:#fff;
--ds-text-secondary:rgba(0,0,0,.88);--ds-text-link:#004bcc;
--ds-state-hover:rgba(0,0,0,.04);--ds-border-input-hover:rgba(0,0,0,.32);--ds-border-focus:#1e84ff;
--ds-cat-news:#1e84ff;--ds-cat-news-text:#004fad;
--ds-cat-shopping:#ff4e33;--ds-cat-shopping-text:#bf2610;
--ds-cat-sports:#5c77ff;--ds-cat-sports-text:#3550d8;
--ds-cat-entertainment:#a05cff;--ds-cat-entertainment-text:#7533cc;
--ds-cat-cafe:#ff5c66;--ds-cat-cafe-text:#cc303d;
--ds-cat-interest:#ff9429;--ds-cat-interest-text:#cc6a0a;
--ds-cat-community:#5e47eb;--ds-cat-community-text:#3c2bb8;
--ds-font-sans:'Pretendard Variable','Pretendard',-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif;
--ds-font-body:var(--ds-font-sans);
--ds-font-display:'GmarketSans','Pretendard Variable','Pretendard',-apple-system,'Apple SD Gothic Neo',sans-serif;
--ds-radius-md:12px;--ds-radius-lg:16px;--ds-radius-chip:9999px;
--ds-shadow-low:0 0 4px 0 rgba(0,0,0,.04);--ds-shadow-medium:0 1px 10px 0 rgba(0,0,0,.08);--ds-shadow-high:0 2px 16px 0 rgba(0,0,0,.16);
--ds-focus-ring:0 0 0 3px rgba(30,132,255,.4);
--ds-space-2:8px;--ds-space-3:12px;--ds-space-4:16px;
--ds-ease-standard:cubic-bezier(.4,0,.2,1);
/* ── 레거시 별칭 → --ds-* (모드 자동 swap) ── */
--bg:var(--ds-canvas);--surface:var(--ds-surface);--card:var(--ds-surface);
--s2:var(--ds-surface-on);--s3:var(--ds-surface-on);
--line:var(--ds-hairline);--line2:var(--ds-divider-inline);
--mut:var(--ds-muted);--faint:var(--ds-placeholder);
--fg:var(--ds-ink);--ink:var(--ds-ink);--fg2:var(--ds-body);--ink2:var(--ds-body);
--pri:var(--ds-primary);--pri2:var(--ds-primary);--ac:var(--ds-primary);--prihov:var(--ds-primary-hover);
--sky:var(--ds-cat-news);
--ent:var(--ds-warning);--warn:var(--ds-warning);--orange:var(--ds-warning);
--int:var(--ds-cat-sports);--teal:var(--ds-cat-sports);
--cat:var(--ds-cat-entertainment);--purple:var(--ds-cat-community);
--green:var(--ds-success);--g:var(--ds-success);--pink:var(--ds-cat-cafe);
--sh:var(--ds-shadow-medium);--sh-hi:var(--ds-shadow-high);
--radius:var(--ds-radius-lg);--r:var(--ds-radius-md);
--font:var(--ds-font-body);--disp:var(--ds-font-display)}
/* ── 다크 자동(뷰어 OS 기준) · --ds-* 만 swap → 별칭은 그대로 따라감 ── */
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--ds-primary:#1e84ff;--ds-primary-hover:#66a8ff;--ds-primary-deep:#66a8ff;--ds-primary-tint:rgba(30,132,255,.24);
--ds-ink:#fff;--ds-canvas:#161718;--ds-surface:#202122;--ds-surface-white:#202122;--ds-surface-on:#303233;
--ds-body:rgba(255,255,255,.88);--ds-muted:rgba(255,255,255,.48);--ds-placeholder:rgba(255,255,255,.32);
--ds-hairline:rgba(255,255,255,.08);--ds-hairline-soft:rgba(255,255,255,.04);--ds-divider-inline:rgba(255,255,255,.16);
--ds-success-deep:#5fe08a;--ds-error-deep:#ff8a75;--ds-text-secondary:rgba(255,255,255,.88);--ds-text-link:#5796e1;
--ds-state-hover:rgba(255,255,255,.04);--ds-border-input-hover:rgba(255,255,255,.32);
--ds-cat-news:#66a8ff;--ds-cat-news-text:#8094ff;--ds-cat-shopping-text:#ff7880;
--ds-cat-sports-text:#8094ff;--ds-cat-entertainment:#b078ff;--ds-cat-entertainment-text:#b078ff;
--ds-cat-cafe-text:#ff7880;--ds-cat-interest-text:#ffa245;--ds-cat-community-text:rgba(255,255,255,.88);
--ds-shadow-low:0 0 4px 0 rgba(0,0,0,.08);--ds-shadow-medium:0 1px 10px 0 rgba(0,0,0,.16);--ds-shadow-high:0 2px 16px 0 rgba(0,0,0,.32)}}
/* 명시적 다크 지정(리포트가 data-theme=dark 를 달 때) */
[data-theme=dark]{
--ds-primary:#1e84ff;--ds-primary-hover:#66a8ff;--ds-primary-deep:#66a8ff;--ds-primary-tint:rgba(30,132,255,.24);
--ds-ink:#fff;--ds-canvas:#161718;--ds-surface:#202122;--ds-surface-white:#202122;--ds-surface-on:#303233;
--ds-body:rgba(255,255,255,.88);--ds-muted:rgba(255,255,255,.48);--ds-placeholder:rgba(255,255,255,.32);
--ds-hairline:rgba(255,255,255,.08);--ds-hairline-soft:rgba(255,255,255,.04);--ds-divider-inline:rgba(255,255,255,.16);
--ds-success-deep:#5fe08a;--ds-error-deep:#ff8a75;--ds-text-secondary:rgba(255,255,255,.88);--ds-text-link:#5796e1;
--ds-state-hover:rgba(255,255,255,.04);--ds-border-input-hover:rgba(255,255,255,.32);
--ds-cat-news:#66a8ff;--ds-cat-news-text:#8094ff;--ds-cat-shopping-text:#ff7880;
--ds-cat-sports-text:#8094ff;--ds-cat-entertainment:#b078ff;--ds-cat-entertainment-text:#b078ff;
--ds-cat-cafe-text:#ff7880;--ds-cat-interest-text:#ffa245;--ds-cat-community-text:rgba(255,255,255,.88);
--ds-shadow-low:0 0 4px 0 rgba(0,0,0,.08);--ds-shadow-medium:0 1px 10px 0 rgba(0,0,0,.16);--ds-shadow-high:0 2px 16px 0 rgba(0,0,0,.32)}"""

# ── 공통 컴포넌트 · 토큰 기반(라이트/다크 자동). display 폰트, 카드, eyebrow, 툴팁, 타일. ──
COMPONENTS = r"""
body{-webkit-font-smoothing:antialiased;word-break:keep-all;overflow-wrap:break-word}
h1{font-family:var(--ds-font-display);letter-spacing:-.022em}
h2,h3{font-family:var(--ds-font-display)}
a{color:var(--ds-text-link)}
::selection{background:var(--ds-primary);color:var(--ds-on-primary)}
:where(button,a,[role=tab],select,summary):focus-visible{outline:none;box-shadow:var(--ds-focus-ring)}
header{background:radial-gradient(120% 140% at 12% -10%,var(--ds-primary-tint),transparent 60%),
radial-gradient(90% 120% at 100% 0%,rgba(92,119,255,.08),transparent 55%)}
.card{background:var(--ds-surface);border:1px solid var(--ds-hairline);box-shadow:var(--ds-shadow-medium)}
.eyebrow{display:inline-block;font-family:var(--ds-font-display);font-size:10.5px;font-weight:700;text-transform:uppercase;
letter-spacing:.14em;color:var(--ds-muted);border:1px solid var(--ds-hairline);border-radius:var(--ds-radius-chip);padding:3px 10px;margin-bottom:11px}
.hint{position:relative;display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;
border:1px solid var(--ds-hairline);color:var(--ds-muted);font-size:10px;font-weight:700;font-style:normal;cursor:help;
vertical-align:middle;transition:color .15s,border-color .15s;flex:none}
.hint:hover{color:var(--ds-ink);border-color:var(--ds-border-input-hover)}
.hint::after{content:attr(data-tip);position:absolute;bottom:calc(100% + 9px);left:50%;
transform:translateX(-50%) translateY(4px);width:max-content;max-width:300px;
background:var(--ds-surface);border:1px solid var(--ds-hairline);border-radius:10px;padding:11px 13px;
font-family:var(--ds-font-body);font-size:12px;font-weight:400;line-height:1.6;color:var(--ds-body);
white-space:pre-line;text-align:left;letter-spacing:0;text-transform:none;
opacity:0;pointer-events:none;transition:opacity .16s,transform .16s;box-shadow:var(--ds-shadow-high);z-index:30}
.hint:hover::after{opacity:1;transform:translateX(-50%) translateY(0)}
h2 .hint,h3 .hint{font-size:10px}
.lc{font-family:var(--ds-font-display);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
color:var(--ds-muted);background:var(--ds-state-hover);border:1px solid var(--ds-hairline);border-radius:6px;padding:2px 7px;white-space:nowrap}
.st{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;border-radius:6px;padding:2px 8px;
background:var(--ds-primary-tint);color:var(--ds-cat-sports-text);white-space:nowrap}
.st i{width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 6px currentColor}
.st.no{background:var(--ds-state-hover);color:var(--ds-muted)}.st.no i{box-shadow:none}
.tile{flex:1;background:var(--ds-surface-on);border:1px solid var(--ds-hairline);border-radius:10px;padding:9px 11px}
.tile b{font-family:var(--ds-font-display);font-size:18px;font-weight:700;display:block;line-height:1.15;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.tile span{font-size:10px;color:var(--ds-muted);text-transform:uppercase;letter-spacing:.04em}
.cnt{font-family:var(--ds-font-display);font-size:11px;font-weight:700;color:var(--ds-body);background:var(--ds-surface-on);border:1px solid var(--ds-hairline);
border-radius:6px;padding:1px 8px;vertical-align:middle;margin-left:4px;letter-spacing:0;text-transform:none}
"""


def inject(html: str) -> str:
    """템플릿 HTML에 공통 폰트(+head)와 토큰/컴포넌트(+style 말미)를 끼워 넣는다.

    - 폰트는 첫 `<style>` 앞에
    - 토큰+컴포넌트는 첫 `</style>` 앞에(템플릿 :root 보다 뒤 → 값 통일)
    """
    html = html.replace("<style>", FONT_HEAD + "<style>", 1)
    html = html.replace("</style>", _gmarket_fontface() + TOKENS + COMPONENTS + "</style>", 1)
    return html
