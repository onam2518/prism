"""Prism 앱 HTML 마크업(단일 페이지 · serve 에서 분리 · 라우트 분리 3차).

마크업은 화면 섹션 조각(prism/ui/NN-*.html)을 파일명 순으로 이어붙여 PAGE 로 합성한다
(마크업 분할 5차: 섹션 파일이 편집·충돌 단위 — 여러 세션이 서로 다른 화면을 동시 수정 가능.
새 화면 모듈은 새 조각 파일로 추가 · 조각 순서 = 파일명 숫자 접두).
앱 스크립트·스타일은 /vendor/app.js·app.css(분리 파일)를 참조한다.
정적 데모는 scripts/make_demo.py 가 이 마크업을 원천으로 재인라인·치환한다.
"""
import os

_UI_DIR = os.path.join(os.path.dirname(__file__), "ui")


def _compose() -> str:
    names = sorted(n for n in os.listdir(_UI_DIR) if n.endswith(".html"))
    parts = []
    for n in names:
        with open(os.path.join(_UI_DIR, n), encoding="utf-8") as f:
            parts.append(f.read())
    return "".join(parts)


PAGE = _compose()
