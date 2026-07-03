"""세 탭(아이템 메타·토픽·사용자 메타) 공통 디자인 테마: 단일 소스.

토큰(색·폰트·그림자) + 공통 컴포넌트(eyebrow·호버 툴팁·배지·스탯 타일)를 한 곳에서 관리.
각 템플릿은 `inject(html)` 로 `<head>`에 폰트, `</style>` 앞에 토큰+컴포넌트를 끼워 넣는다.

토큰은 별칭(--ink/--ink2/--card/--ac …)을 함께 정의해, 기존 템플릿 CSS를 거의 수정하지
않고도 팔레트·폰트·그림자가 일괄 통일되도록 한다(별칭이 새 값을 가리킴).

디자인 원칙: taste-skill(redesign-existing-projects + high-end-visual-design) 기반.
텍스트 원칙: 마침표 금지 · 문장 단위 불릿 · 정의·설명은 .hint 호버 툴팁으로.
"""

# <head> 폰트(+preconnect). 온라인 최적, 오프라인은 시스템 폰트로 graceful fallback.
FONT_HEAD = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?'
    'family=Space+Grotesk:wght@400;500;600;700&'
    'family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">'
)

# 공통 토큰 · 템플릿의 :root 보다 뒤에 주입되어 값이 통일된다(별칭 포함).
TOKENS = r""":root{
--bg:#08090c;--surface:#101216;--s2:#15181d;--s3:#1b1f25;--line:#23262e;--line2:#34343a;
--mut:#8b909b;--faint:#666b76;--fg:#f6f7f9;--fg2:#cfd4de;
--pri:#6872d6;--pri2:#6872d6;--prihov:#828fff;--ac:#6872d6;
--ent:#e0a648;--int:#46bda9;--cat:#ab8ee8;--warn:#e0a648;
--ink:#f6f7f9;--ink2:#cfd4de;--card:#101216;
--sh:0 1px 2px rgba(0,0,0,.45),0 10px 28px -16px rgba(0,0,0,.7);
--sh-hi:0 2px 6px rgba(0,0,0,.5),0 18px 44px -18px rgba(6,8,16,.85);
--radius:14px;
--font:"Plus Jakarta Sans",-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",Pretendard,sans-serif;
--disp:"Space Grotesk","Plus Jakarta Sans",-apple-system,sans-serif}"""

# 공통 컴포넌트 · 헤딩 display 폰트, 카드 그림자, eyebrow, 호버 툴팁, 배지/타일.
COMPONENTS = r"""
h1{font-family:var(--disp);letter-spacing:-.022em}
h2,h3{font-family:var(--disp)}
header{background:radial-gradient(120% 140% at 12% -10%,rgba(104,114,214,.10),transparent 60%),
radial-gradient(90% 120% at 100% 0%,rgba(70,189,169,.06),transparent 55%)}
.card{box-shadow:var(--sh),inset 0 1px 0 rgba(255,255,255,.028)}
.eyebrow{display:inline-block;font-family:var(--disp);font-size:10.5px;font-weight:600;text-transform:uppercase;
letter-spacing:.18em;color:var(--mut);border:1px solid var(--line);border-radius:999px;padding:3px 10px;margin-bottom:11px}
.hint{position:relative;display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:50%;
border:1px solid var(--line);color:var(--mut);font-size:10px;font-weight:600;font-style:normal;cursor:help;
vertical-align:middle;transition:color .15s,border-color .15s;flex:none}
.hint:hover{color:var(--fg);border-color:#3a3e48}
.hint::after{content:attr(data-tip);position:absolute;bottom:calc(100% + 9px);left:50%;
transform:translateX(-50%) translateY(4px);width:max-content;max-width:300px;
background:#15181d;border:1px solid #2c2f37;border-radius:10px;padding:11px 13px;
font-family:var(--font);font-size:12px;font-weight:400;line-height:1.6;color:var(--fg2);
white-space:pre-line;text-align:left;letter-spacing:0;text-transform:none;
opacity:0;pointer-events:none;transition:opacity .16s,transform .16s;box-shadow:var(--sh-hi);z-index:30}
.hint:hover::after{opacity:1;transform:translateX(-50%) translateY(0)}
h2 .hint,h3 .hint{font-size:10px}
.lc{font-family:var(--disp);font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;
color:var(--mut);background:rgba(255,255,255,.05);border:1px solid var(--line);border-radius:6px;padding:2px 7px;white-space:nowrap}
.st{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:600;border-radius:6px;padding:2px 8px;
background:rgba(70,189,169,.13);color:var(--int);white-space:nowrap}
.st i{width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 6px currentColor}
.st.no{background:rgba(139,144,155,.12);color:var(--mut)}.st.no i{box-shadow:none}
.tile{flex:1;background:#0b0d11;border:1px solid var(--line);border-radius:10px;padding:9px 11px}
.tile b{font-family:var(--disp);font-size:18px;font-weight:600;display:block;line-height:1.15;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.tile span{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
.cnt{font-family:var(--disp);font-size:11px;font-weight:600;color:var(--fg2);background:var(--s2);border:1px solid var(--line);
border-radius:6px;padding:1px 8px;vertical-align:middle;margin-left:4px;letter-spacing:0;text-transform:none}
"""


def inject(html: str) -> str:
    """템플릿 HTML에 공통 폰트(+head)와 토큰/컴포넌트(+style 말미)를 끼워 넣는다.

    - 폰트는 첫 `<style>` 앞에
    - 토큰+컴포넌트는 첫 `</style>` 앞에(템플릿 :root 보다 뒤 → 값 통일)
    """
    html = html.replace("<style>", FONT_HEAD + "<style>", 1)
    html = html.replace("</style>", TOKENS + COMPONENTS + "</style>", 1)
    return html
