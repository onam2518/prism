"""docs/demo.html 생성 — 현재 앱(serve.PAGE)을 그대로 담은 자체완결 정적 스냅샷.

목적: '최종 구현 현황 그대로' 보이는 데모. 서버·키 없이 브라우저에서 열면
3분할 콘솔(Pretendard·설정 패널·결과)이 실제 구현과 동일하게 렌더된다.

변환:
  · /vendor/* 와 폰트 링크 → CDN(온라인 데모)
  · fetch(/config·/vocab) 를 합성 응답으로 스텁(서버 불필요)
  · 샘플 결과를 미리 주입해 결과 화면까지 보여줌
실행: python3 scripts/make_demo.py
"""
from __future__ import annotations

import json
import os

from prism.serve import PAGE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 샘플 결과(이미지 → 시각 이해 → 메타). 실제 출력 스키마와 동일 ──
DEMO_RESULT = {
    "source": "image",
    "mock": False,
    "content": {
        "displayServiceName": "뉴스",
        "title": "삼성전자 노조 임금 협상 결렬",
        "subtitle": "",
        "body": "기자회견장에서 노조 집행부가 발언하는 장면. 중앙노동위원회 조정에서 합의 불성립.",
    },
    "signals": [{
        "vision": "이미지는 실내 기자회견장을 담고 있다. 노동조합 집행부로 보이는 인물들이 단상에 앉아 "
                  "발언하고 있으며, 배경 현수막에 협상 관련 문구가 보인다. 전체적으로 노사 협상 결렬을 "
                  "알리는 공식 발표 현장의 분위기다. [엔티티] 삼성전자, 전국삼성전자노동조합, 중앙노동위원회 "
                  "[장면] 노사 협상 결렬 기자회견",
        "ocr": "중앙노동위원회 조정 불성립 · 임금 인상 협상 결렬",
        "latency_ms": 2840,
    }],
    "output": {
        "item_meta": {
            "summary": "삼성전자가 중앙노동위 조정에서 노조와 합의에 이르지 못했다",
            "entities": ["삼성전자", "전국삼성전자노동조합", "중앙노동위원회"],
            "intent": ["사실 전달", "분석·해설"],
            "content_category": {
                "삼성전자": "Business / Industries",
                "전국삼성전자노동조합": "Law, Govt & Politics",
                "중앙노동위원회": "Law, Govt & Politics",
            },
        },
        "quality_meta": {"finalGrade": "G"},
        "routing": {"content_track": "text"},
    },
}

DEMO_CONFIG = {
    "hasKey": True, "persisted": True, "model": "solar-pro3-260323",
    "baseUrl": "https://api.upstage.ai/v1/solar", "reasoning": "default",
    "systemPrompt": "", "configured": True, "forcedMock": False,
    "hasBizKey": False, "bizPersisted": False, "hasTimelyKey": False, "timelyPersisted": False,
    "textProvider": "solar", "textModel": "",
    "visionProvider": "upstage_ie", "visionModel": "",
}
DEMO_VOCAB = {"groups": ["뉴스", "연예", "스포츠", "콘텐츠", "커뮤니티", "블로그", "음악", "동영상"]}

# CDN 매핑(자체완결 온라인 데모)
CDN_TAILWIND = "https://cdn.tailwindcss.com/3.4.16"
CDN_ALPINE = "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js"
CDN_PRETENDARD = ("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/"
                  "dist/web/static/pretendard.min.css")

STUB = """<script>
  // 정적 데모: 서버 호출을 합성 응답으로 스텁(키·서버 불필요)
  window.__DEMO_RESULT__ = %s;
  (function () {
    const J = (o) => ({ ok: true, json: () => Promise.resolve(o), text: () => Promise.resolve('') });
    const CFG = %s, VOCAB = %s;
    const real = window.fetch ? window.fetch.bind(window) : null;
    window.fetch = function (url, opt) {
      const u = String(url);
      if (u.indexOf('/config') === 0 || u.indexOf('/config') > -1) return Promise.resolve(J(CFG));
      if (u.indexOf('/vocab') > -1) return Promise.resolve(J(VOCAB));
      if (u.indexOf('/models') > -1) return Promise.resolve(J({ ok: true, models: ['solar-pro3-260323', 'solar-pro2-251215'] }));
      if (u.indexOf('/ping') > -1) return Promise.resolve(J({ ok: true, detail: 'solar-pro3-260323 응답 정상' }));
      if (u.indexOf('/run') > -1) return Promise.resolve(J(window.__DEMO_RESULT__));
      return real ? real(url, opt) : Promise.resolve(J({}));
    };
  })();
</script>
""" % (json.dumps(DEMO_RESULT, ensure_ascii=False),
       json.dumps(DEMO_CONFIG, ensure_ascii=False),
       json.dumps(DEMO_VOCAB, ensure_ascii=False))

BANNER = ('<div style="position:fixed;left:18px;bottom:16px;z-index:70;padding:6px 12px;border-radius:8px;'
          'font:600 12px/1 Pretendard,system-ui,sans-serif;color:#c8c3ff;background:rgba(91,82,255,.16);'
          'border:1px solid rgba(91,82,255,.35);backdrop-filter:blur(6px)">정적 데모 · 샘플 결과 미리보기</div>')


def build() -> str:
    html = PAGE
    # 폰트·벤더 → CDN
    html = html.replace('<link href="/vendor/pretendard.css" rel="stylesheet">',
                        f'<link href="{CDN_PRETENDARD}" rel="stylesheet">')
    html = html.replace('<script src="/vendor/tailwind.js"></script>',
                        f'<script src="{CDN_TAILWIND}"></script>')
    html = html.replace('<script defer src="/vendor/alpine.js"></script>',
                        STUB + f'<script defer src="{CDN_ALPINE}"></script>')
    # Pretendard 폰트 패밀리는 'Pretendard Variable' 가변 → 정적 CDN 은 'Pretendard'
    html = html.replace('"Pretendard Variable",Pretendard,', '"Pretendard",')
    html = html.replace("'\\\"Pretendard Variable\\\"', 'Pretendard',",
                        "'Pretendard',")
    # 샘플 결과 주입(결과 화면까지 보여줌)
    html = html.replace('result: null,', 'result: (window.__DEMO_RESULT__ || null),')
    # 데모 배너
    html = html.replace('</body>', BANNER + '\n</body>')
    return html


def main():
    out = os.path.join(ROOT, "docs", "demo.html")
    html = build()
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {out} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
