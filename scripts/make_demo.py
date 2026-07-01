"""docs/demo.html 생성 · 현재 앱(serve.PAGE)을 그대로 담은 자체완결 정적 스냅샷.

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

from prism.serve import PAGE, dict_data as _dict_data

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
            "content_category": ["Business / Industries", "Law, Govt & Politics"],
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
    # 운영 모델(현재 버전): Supabase 백엔드 · 키는 서버(관리자) 관리 → API 설정 UI 숨김.
    # 데모는 팀 관리자 시점으로 표시(자동 인입·팀 관리 노출). authRequired=false 로 로그인 벽 생략.
    "backend": "supabase", "authRequired": False, "keyManagedByServer": True,
}

# 데모 관리자 컨텍스트(/admin 스텁) · isAdmin=true 로 자동 인입·팀 관리 노출
DEMO_ADMIN = {
    "ok": True, "isAdmin": True,
    "team": {"name": "데모팀", "invite_code": "DEMO-1234", "created_by": "demo-admin"},
    "members": [
        {"id": "demo-admin", "name": "데모 관리자", "char": "boksil", "avatar": "boksil", "is_admin": True},
        {"id": "m2", "name": "검수자 A", "char": "yonghee", "avatar": "yonghee", "is_admin": True},
        {"id": "m3", "name": "검수자 B", "char": "ddakji", "avatar": "ddakji", "is_admin": False},
    ],
    "goldenCount": 24,
}
DEMO_ARENA = {
    "accuracy": 0.91, "good": 10, "bad": 2, "reviews": 62, "week_reviews": 18,
    "accuracy_delta": 0.04, "target": 0.9, "queue": 3,
    "leaderboard": [
        {"reviewer": "데모 관리자", "name": "데모 관리자", "char": "boksil", "level": 6, "points": 640, "reviews": 62, "corrections": 9, "streak": 7},
        {"reviewer": "검수자 A", "name": "검수자 A", "char": "yonghee", "level": 4, "points": 420, "reviews": 41, "corrections": 5, "streak": 3},
        {"reviewer": "검수자 B", "name": "검수자 B", "char": "ddakji", "level": 2, "points": 180, "reviews": 17, "corrections": 1, "streak": 1},
    ],
}
DEMO_VOCAB = {"groups": ["뉴스", "연예", "스포츠", "콘텐츠", "커뮤니티", "블로그", "음악", "동영상"]}

DEMO_DASH = {
    "n": 12, "g": 10, "r": 2, "gPct": 83, "entities": 31, "avgLead": 38,
    "intents": [{"k": "사건 경과 보도", "v": 7, "pct": 58}, {"k": "분석·해설", "v": 5, "pct": 42},
                {"k": "인물 동향", "v": 3, "pct": 25}, {"k": "흥미·화제", "v": 2, "pct": 17}],
    "categories": [{"k": "News and Politics", "v": 6, "pct": 50}, {"k": "Sports", "v": 4, "pct": 33},
                   {"k": "Business and Finance", "v": 3, "pct": 25}],
    "qualityReasons": [{"k": "clickbait", "v": 2, "pct": 17}],
}
DEMO_TOPICS = {
    "n_contents": 12, "summary": {"single": 2, "composite": 1, "filter": 8},
    "single": [{"cluster_id": "S-samsung", "entities": ["삼성전자", "노동조합"], "n_contents": 3},
               {"cluster_id": "S-rate", "entities": ["한국은행", "금리"], "n_contents": 2}],
    "composite": [{"cluster_id": "C-labor", "rep_entities": ["삼성전자", "중앙노동위"], "n_contents": 4}],
    "filter": [{"cluster_id": "F-fin", "name": "재테크 × 심층 분석", "active": True, "n_contents": 3},
               {"cluster_id": "F-ent", "name": "연예 × 화제성", "active": False}],
}
DEMO_USER = {
    "source": "실 행동 로그 → 소비 형태·강도 (데모)", "n_contents": 12,
    "users": [
        {"user_id": "u1", "persona": "정독러", "form": {"세션 길이": "장", "체류·완주": "고", "전환·이동": "느림", "깊이": "몰입", "시간대": "평일 야간"},
         "intensity": {"심층 분석": "고", "정책·사업 소개": "고", "속보·단신": "저"},
         "affinity_entities": [["삼성전자", 4], ["금리", 3], ["재건축", 2]],
         "engagement": {"views": 18, "clicks": 14, "click_rate": 0.78, "avg_dwell_sec": 52.4}},
        {"user_id": "u2", "persona": "스낵러", "form": {"세션 길이": "단", "체류·완주": "저", "전환·이동": "빠름", "깊이": "훑기", "시간대": "출퇴근"},
         "intensity": {"흥미·화제": "중", "속보·단신": "저"},
         "affinity_entities": [["손흥민", 2]],
         "engagement": {"views": 22, "clicks": 5, "click_rate": 0.23, "avg_dwell_sec": 9.1}},
    ],
    "personas_def": [
        {"id": 1, "name": "정독러", "full": "깊이 정독러", "desc": "한 주제를 파고들어 정독·저장", "form": {"깊이": "몰입", "체류·완주": "고"}},
        {"id": 2, "name": "스낵러", "full": "가벼운 스낵러", "desc": "짧은 세션·빠른 전환", "form": {"깊이": "훑기", "체류·완주": "저"}},
    ],
    "formula": "소비 강도 = 맥락(인텐트)별 Σ(체류/30 × 클릭가중)의 상대 등급(저/중/고)",
}

# CDN 매핑(자체완결 온라인 데모)
CDN_TAILWIND = "https://cdn.tailwindcss.com/3.4.16"
CDN_ALPINE = "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js"
CDN_PRETENDARD = ("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/"
                  "dist/web/static/pretendard.min.css")

STUB = """<script>
  // 정적 데모: 서버 호출을 합성 응답으로 스텁(키·서버 불필요)
  // 홈 위젯 레이아웃 시드(쇼케이스 · 실제 앱은 빈 상태로 시작)
  try { localStorage.setItem('prism_home', JSON.stringify(['launch-run','launch-batch','launch-dict','metrics','quality','intents','categories','process'])); } catch (e) {}
  // 데모 로그인 시드(관리자) → 로그인 벽 생략 + 자동 인입·팀 관리 노출
  try { localStorage.setItem('prism_reviewer', '데모 관리자'); localStorage.setItem('prism_reviewer_char', 'boksil'); localStorage.setItem('prism_token', 'demo'); } catch (e) {}
  window.__DEMO_RESULT__ = %s;
  (function () {
    const J = (o) => ({ ok: true, json: () => Promise.resolve(o), text: () => Promise.resolve('') });
    const CFG = %s, VOCAB = %s, ADMIN = %s, ARENA = %s;
    const real = window.fetch ? window.fetch.bind(window) : null;
    window.fetch = function (url, opt) {
      const u = String(url);
      if (u.indexOf('/config') === 0 || u.indexOf('/config') > -1) return Promise.resolve(J(CFG));
      if (u.indexOf('/vocab') > -1) return Promise.resolve(J(VOCAB));
      if (u.indexOf('/admin') > -1) return Promise.resolve(J(ADMIN));
      if (u.indexOf('/arena') > -1) return Promise.resolve(J(ARENA));
      if (u.indexOf('/drill') > -1) { const qp = new URLSearchParams((u.split('?')[1]||'')); return Promise.resolve(J({ ok: true, kind: qp.get('kind')||'intent', value: qp.get('value')||'', items: [
        {hash:'d1', title:'삼성전자 노조 임금 협상 결렬', subtitle:'중앙노동위 조정 불성립', service:'뉴스', grade:'G', summary:'삼성전자가 중앙노동위 조정에서 노조와 합의에 이르지 못했다', entities:['삼성전자','전국삼성전자노동조합','중앙노동위원회'], intent:['사건 경과 보도'], category:['News and Politics / Society'], reasons:[], url:''},
        {hash:'d2', title:'한국은행 기준금리 동결 결정', subtitle:'', service:'뉴스', grade:'G', summary:'한국은행이 기준금리를 현 수준에서 동결하기로 결정했다', entities:['한국은행','금리'], intent:['사건 경과 보도'], category:['Business and Finance / Economy'], reasons:[], url:''},
        {hash:'d3', title:'낚시성 제목 사례', subtitle:'', service:'커뮤니티', grade:'R', summary:'제목과 본문 괴리로 클릭을 유도한 사례', entities:[], intent:['흥미·화제'], category:[], reasons:['clickbait'], url:''}
      ], n: 3 })); }
      if (u.indexOf('/badges') > -1) { let e = []; try { e = JSON.parse((opt&&opt.body)||'{}').earned || []; } catch (x) {} return Promise.resolve(J({ ok: true, badges: e })); }
      if (u.indexOf('/reviewer') > -1) return Promise.resolve(J({ ok: true, team: { invite_code: ADMIN.team.invite_code } }));
      if (u.indexOf('/auth') > -1) return Promise.resolve(J({ ok: true, access_token: 'demo' }));
      if (u.indexOf('/models') > -1) return Promise.resolve(J({ ok: true, models: ['solar-pro3-260323', 'solar-pro2-251215'] }));
      if (u.indexOf('/ping') > -1) return Promise.resolve(J({ ok: true, detail: 'solar-pro3-260323 응답 정상' }));
      if (u.indexOf('/dashboard') > -1) return Promise.resolve(J(%s));
      if (u.indexOf('/topics') > -1) return Promise.resolve(J(%s));
      if (u.indexOf('/usermeta') > -1) return Promise.resolve(J(%s));
      if (u.indexOf('/dict') > -1) return Promise.resolve(J(%s));
      if (u.indexOf('/run') > -1) return Promise.resolve(J(window.__DEMO_RESULT__));
      if (u.indexOf('/queue') > -1 || u.indexOf('/ingest') > -1) return Promise.resolve(J({ ok: true, jobs: [], sources: [] }));
      return real ? real(url, opt) : Promise.resolve(J({}));
    };
  })();
</script>
""" % (json.dumps(DEMO_RESULT, ensure_ascii=False),
       json.dumps(DEMO_CONFIG, ensure_ascii=False),
       json.dumps(DEMO_VOCAB, ensure_ascii=False),
       json.dumps(DEMO_ADMIN, ensure_ascii=False),
       json.dumps(DEMO_ARENA, ensure_ascii=False),
       json.dumps(DEMO_DASH, ensure_ascii=False),
       json.dumps(DEMO_TOPICS, ensure_ascii=False),
       json.dumps(DEMO_USER, ensure_ascii=False),
       json.dumps(_dict_data(), ensure_ascii=False))

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
    # 디자인 시스템 CSS 인라인(정적 데모 자체완결 · file:// 에서도 라이트 위젯홈 렌더)
    theme_css = open(os.path.join(ROOT, "prism", "vendor", "ds-theme.css"), encoding="utf-8").read()
    comp_css = open(os.path.join(ROOT, "prism", "vendor", "ds-components.css"), encoding="utf-8").read()
    html = html.replace('<link href="/vendor/ds-theme.css" rel="stylesheet">', f'<style>{theme_css}</style>')
    html = html.replace('<link href="/vendor/ds-components.css" rel="stylesheet">', f'<style>{comp_css}</style>')
    # 벤더 에셋(캐릭터·로고 SVG) → docs/demo-assets/ (Pages 루트 내부, main() 에서 복사)
    #   ../prism/vendor 는 Pages(docs=루트)에서 사이트 밖으로 나가 404 → 루트 내부 상대경로로.
    # src="/vendor/ 뿐 아니라 charOptions 의 JS 경로('/vendor/…')까지 포함해 전역 치환
    # (CSS·폰트·CDN 스크립트는 위에서 이미 태그 통째 치환됨 → 남은 /vendor/ 는 캐릭터 SVG 뿐)
    html = html.replace('/vendor/', 'demo-assets/')
    # Pretendard 폰트 패밀리는 'Pretendard Variable' 가변 → 정적 CDN 은 'Pretendard'
    html = html.replace('"Pretendard Variable",Pretendard,', '"Pretendard",')
    html = html.replace("'\\\"Pretendard Variable\\\"', 'Pretendard',",
                        "'Pretendard',")
    # 샘플 결과 주입(결과 화면까지 보여줌)
    html = html.replace('result: null,', 'result: (window.__DEMO_RESULT__ || null),')
    # 데모 배너
    html = html.replace('</body>', BANNER + '\n</body>')
    return html


def _copy_demo_assets():
    """캐릭터·로고 SVG 를 docs/demo-assets/ 로 복사(Pages 루트 내부).
    demo.html 이 src="demo-assets/*.svg" 로 참조 → Pages·htmlpreview·file:// 모두 해석."""
    import shutil
    src_dir = os.path.join(ROOT, "prism", "vendor")
    dst_dir = os.path.join(ROOT, "docs", "demo-assets")
    os.makedirs(dst_dir, exist_ok=True)
    n = 0
    for fn in os.listdir(src_dir):
        if fn.lower().endswith((".svg", ".png", ".jpg", ".gif", ".webp")):
            shutil.copy2(os.path.join(src_dir, fn), os.path.join(dst_dir, fn))
            n += 1
    return n, dst_dir


def main():
    out = os.path.join(ROOT, "docs", "demo.html")
    html = build()
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    n, dst = _copy_demo_assets()
    print(f"wrote {out} ({len(html):,} bytes)")
    print(f"copied {n} assets → {dst}")


if __name__ == "__main__":
    main()
