"""벤더 자산 번들 · 조각 파일을 요청 시 메모리에서 이어붙여 단일 URL 로 낸다(2026-08-03).

배경: UI 를 조각으로 나눈 뒤(2026-07-17) 첫 로드에 vendor JS 15개 + CSS 6개가 각각 왕복했다.
서버 응답 자체는 40ms대인데 왕복 수 때문에 로드가 1.7초까지 늘어났다.

빌드 도구를 새로 들이지 않는다(의존성 0 원칙). 디스크의 조각 파일은 그대로 두고 —
편집·리뷰·git blame 은 조각 단위 그대로 — 서버가 요청 시 이어붙여 한 URL 로 낸다.
결과는 (조각 최신 mtime) 키로 캐시되므로 부팅당 1회만 만든다.

순서가 곧 계약이다:
  · JS  = app-NN-*.js 파일명 순 → 로더 app.js. 로더는 조각들이 PRISM_APP_PARTS 에
          등록을 마친 뒤 실행돼야 한다. alpine.js 는 번들 밖에 남는다(그 뒤에 실행).
  · CSS = 캐스케이드 순서 그대로. tw.css 는 preflight(리셋)라 반드시 마지막.
"""
import glob
import os

VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")

JS_BUNDLE = "app-bundle.js"
CSS_BUNDLE = "app-bundle.css"

# CSS 는 순서가 의미를 가지므로 글롭하지 않고 명시한다(마지막 = 가장 세게 이긴다).
_CSS_ORDER = [
    "pretendard.css",
    "gmarket.css",
    "ds-theme.css",
    "ds-components.css",
    "app.css",
    "tw.css",
]


def _js_parts():
    """app-NN-*.js 조각(파일명 순) + 로더. 조각을 새로 추가하면 자동으로 번들에 들어온다."""
    frag = sorted(
        os.path.basename(p) for p in glob.glob(os.path.join(VENDOR_DIR, "app-*.js"))
        if not os.path.basename(p).startswith("app-bundle")
    )
    return frag + ["app.js"]


def parts(name):
    """번들 이름이면 조각 목록, 아니면 None(= 일반 정적 파일)."""
    if name == JS_BUNDLE:
        return _js_parts()
    if name == CSS_BUNDLE:
        return list(_CSS_ORDER)
    return None


def build(name):
    """조각을 순서대로 이어붙인다.

    경계마다 개행을 보장한다 — 조각 마지막 줄이 `// 주석`으로 끝나면 개행 없이 붙을 때
    다음 조각의 첫 줄이 통째로 주석에 먹힌다."""
    out = bytearray()
    for fn in parts(name) or []:
        with open(os.path.join(VENDOR_DIR, fn), "rb") as f:
            out += f.read()
        if not out.endswith(b"\n"):
            out += b"\n"
    return bytes(out)


def mtime(name):
    """조각 중 가장 최근 수정시각 = 번들 캐시 키. 하나만 바뀌어도 다시 만든다."""
    return max(os.path.getmtime(os.path.join(VENDOR_DIR, fn)) for fn in parts(name))
