"""대시보드 생성: 결과(results.jsonl)로 메타 현황·관계도 HTML 출력."""
from __future__ import annotations
import json
import html

from . import dictionaries as D
from . import theme as TH


# IAB v3.0 골격을 21개로 압축: Entertainment 가 Movies/Music/TV/Fine Art 흡수,
# Business and Finance 가 Personal Finance/Real Estate 흡수 등.
IAB_TIER1_KO = [
    "News and Politics", "Entertainment", "Business and Finance", "Sports",
    "Food and Drink", "Travel", "Family and Relationships", "Education",
    "Technology and Computing", "Books and Literature", "Medical Health",
    "Hobbies and Interests", "Health and Fitness", "Home and Garden", "Pets",
    "Style and Fashion", "Automotive", "Video Gaming", "Science", "Careers",
    "Religion and Spirituality",
]
_REMAP = {
    "Movies": "Entertainment", "Pop Culture": "Entertainment",
    "Music and Audio": "Entertainment", "Music": "Entertainment",
    "Television": "Entertainment", "Fine Art": "Entertainment",
    "Performing Arts": "Entertainment", "Entertainment": "Entertainment",
    "Personal Finance": "Business and Finance", "Real Estate": "Business and Finance",
    "Business and Finance": "Business and Finance",
    "Healthy Living": "Health and Fitness", "Health & Fitness": "Health and Fitness",
    "Style & Fashion": "Style and Fashion",
    "Technology & Computing": "Technology and Computing",
    "Home & Garden": "Home and Garden",
    "Food & Drink": "Food and Drink",
    "Hobbies & Interests": "Hobbies and Interests",
    "Events and Attractions": "Travel", "Travel": "Travel",
    "Medical/Health": "Medical Health", "Medical Health": "Medical Health",
    "Video Gaming": "Video Gaming", "Pets": "Pets", "Careers": "Careers",
    "Religion and Spirituality": "Religion and Spirituality",
}


def tier1_remap(cat: str) -> str:
    """현재 데이터의 카테고리 라벨을 자사 21개 Tier1 로 비파괴 정규화."""
    c = str(cat or "").split("/")[0].strip()
    if c in _REMAP:
        return _REMAP[c]
    if c in IAB_TIER1_KO:
        return c
    return c if c and c != "Unclassified" else "Unclassified"


def render(results_path: str, title: str = "아이템 메타 현황", notice: str = "") -> tuple[str, dict]:
    """콘텐츠 대시보드 HTML 문자열 + info 반환(통합 빌더가 재사용)."""
    rows = _read_jsonl(results_path)
    agg = _aggregate(rows)
    nodes, links, gstats = _graph(rows)
    payload = {"agg": agg, "nodes": nodes, "links": links, "gstats": gstats,
               "rows": _table_rows(rows), "full": rows, "title": title}
    # <script> 조기 종료 방어: DATA(콘텐츠 제목·본문·엔티티·검수노트 = 사용자 통제)에 '</script>' 가
    # 있어도 태그를 닫지 못하게 json 문자열에만 '</'→'<\/' 치환(전체 HTML 아님 · graphviz 와 동일 규약).
    _data_js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    htmltext = _HTML.replace("/*__DATA__*/", _data_js)
    htmltext = htmltext.replace("/*__FORCEGRAPH__*/", _vendor_js())
    htmltext = TH.inject(htmltext)
    if notice:
        htmltext = htmltext.replace("<body>", "<body>" + notice, 1)
    return htmltext, {"contents": len(rows), "entities": agg["entity_count"]}


def _vendor_js() -> str:
    """벤더링한 force-graph(MIT) UMD 인라인: 단일 HTML·오프라인 유지."""
    import os
    p = os.path.join(os.path.dirname(__file__), "vendor", "force-graph.min.js")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def build(results_path: str, out_path: str, title: str = "아이템 메타 현황", notice: str = "") -> dict:
    htmltext, info = render(results_path, title, notice)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(htmltext)
    info["out"] = out_path
    return info


def _logo_data_uri(name: str) -> str:
    """벤더 로고를 data URI 로 인라인(외부 요청 0). 서빙·로컬파일·CLI 모든 컨텍스트에서
    항상 표시된다(<img src=/vendor/…> 는 서버 서빙 시에만 로드돼 로컬 파일에선 깨졌음).

    반드시 PNG 를 쓴다: 태그라인 로고 SVG 의 'PRISM' 워드마크는 <text> 요소인데 폰트 지정이
    없어 <img> 격리 렌더 시 세리프(Times)로 폴백된다(SVG 는 페이지 폰트를 못 씀). PNG 는
    브랜드 폰트(GmarketSans)가 래스터로 구워져 있어 앱과 동일하게 정확히 표시된다."""
    import base64
    import os
    p = os.path.join(os.path.dirname(__file__), "vendor", os.path.basename(name))
    mime = "image/png" if name.lower().endswith(".png") else "image/svg+xml"
    try:
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return "data:" + mime + ";base64," + b64
    except OSError:
        return ""


def build_integrated(results_path: str, out_path: str,
                     title: str = "Prism", n_users: int = 6,
                     logs_path: str = None, demo: bool = False, notice: str = "") -> dict:
    """아이템 메타 + 사용자 메타(목업)를 탭 전환 단일 HTML 로 통합.
    각 패널은 iframe(srcdoc)으로 격리: 변수/ID 충돌 없이 기존 빌더 그대로 재사용."""
    from . import usermeta as UM
    from . import topic as TP
    nt = notice
    ctitle = "아이템 메타 (DEMO)" if nt else "아이템 메타 현황"
    content_html, cinfo = render(results_path, ctitle, notice=nt)
    topic_html = TP.render_html(results_path, notice=nt)
    user_html = UM.render_html(results_path, n_users=n_users, logs_path=logs_path, demo=demo, notice=nt)

    def esc(h):
        return h.replace("&", "&amp;").replace('"', "&quot;")

    page = TH.inject(_INTEGRATED).replace("__TITLE__", html.escape(title)) \
        .replace("__FAVICON__", _logo_data_uri("prism-favicon.svg")) \
        .replace("__LOGO_LIGHT__", _logo_data_uri("prism-logo-tagline-light.png")) \
        .replace("__LOGO_DARK__", _logo_data_uri("prism-logo-tagline-dark.png")) \
        .replace("__CONTENT_SRCDOC__", esc(content_html)) \
        .replace("__TOPIC_SRCDOC__", esc(topic_html)) \
        .replace("__USER_SRCDOC__", esc(user_html))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    return {"out": out_path, "contents": cinfo["contents"]}


_INTEGRATED = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<link rel="icon" type="image/svg+xml" href="__FAVICON__">
<style>
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--ds-canvas);color:var(--ds-ink);
font:14px var(--ds-font-body);-webkit-font-smoothing:antialiased}
.tabbar{display:flex;align-items:center;gap:6px;height:62px;padding:0 20px;
background:var(--ds-surface);border-bottom:1px solid var(--ds-hairline)}
.tabbar .brand{margin-right:18px;display:flex;align-items:center;height:28px}
.tabbar .brand .lg{height:44px;width:auto;display:none}
.tabbar .brand .lg-light{display:block}
@media (prefers-color-scheme:dark){.tabbar .brand .lg-light{display:none}.tabbar .brand .lg-dark{display:block}}
[data-theme=light] .tabbar .brand .lg-light{display:block}[data-theme=light] .tabbar .brand .lg-dark{display:none}
[data-theme=dark] .tabbar .brand .lg-light{display:none}[data-theme=dark] .tabbar .brand .lg-dark{display:block}
.tab{padding:8px 16px;border-radius:var(--ds-radius-md);cursor:pointer;color:var(--ds-muted);font-size:13px;font-weight:600;transition:.12s}
.tab:hover{color:var(--ds-ink);background:var(--ds-state-hover)}
.tab.on{background:var(--ds-primary-tint);color:var(--ds-primary-deep)}
.tab .badge{font-size:9.5px;background:var(--ds-state-hover);color:var(--ds-warning);border-radius:5px;padding:2px 6px;margin-left:7px;font-weight:700;letter-spacing:.03em;text-transform:uppercase}
.tab .badge2{font-size:9.5px;background:var(--ds-state-hover);color:var(--ds-muted);border-radius:5px;padding:2px 6px;margin-left:6px;font-weight:700}
.help{margin-left:auto;cursor:pointer;color:var(--ds-muted);font-size:13px;font-weight:600;border:1px solid var(--ds-hairline);border-radius:8px;padding:6px 12px}
.help:hover{color:var(--ds-ink);border-color:var(--ds-border-input-hover)}
.wrap{position:absolute;top:63px;left:0;right:0;bottom:0;background:var(--ds-canvas)}
iframe{width:100%;height:100%;border:0;display:none}iframe.on{display:block}
#hov{position:fixed;inset:0;background:var(--ds-scrim,rgba(0,0,0,.48));display:none;z-index:20}
#hp{position:fixed;top:0;right:0;height:100%;width:min(520px,96vw);background:var(--ds-surface);border-left:1px solid var(--ds-hairline);
 overflow:auto;transform:translateX(100%);transition:transform .2s;z-index:21;padding:22px 24px;box-shadow:var(--ds-shadow-high)}
#hp.on{transform:none}#hp .x{position:absolute;top:16px;right:18px;color:var(--ds-muted);cursor:pointer;font-size:20px}
#hp h2{font-family:var(--ds-font-display);font-size:18px;margin:0 0 4px;letter-spacing:-.4px;color:var(--ds-ink)}#hp .s{color:var(--ds-muted);font-size:12px;margin-bottom:16px}
#hp h3{font-family:var(--ds-font-display);font-size:13px;color:var(--ds-primary-deep);margin:18px 0 6px}
#hp dl{margin:0}#hp dt{font-weight:600;font-size:13px;margin-top:9px;color:var(--ds-ink)}
#hp dd{margin:2px 0 0;color:var(--ds-body);font-size:12.5px;line-height:1.5}
#hp .pl{border-left:2px solid var(--ds-hairline);padding-left:10px;margin:6px 0}
</style></head><body>
<div class="tabbar"><span class="brand" aria-label="Prism"><img class="lg lg-light" src="__LOGO_LIGHT__" alt="Prism"><img class="lg lg-dark" src="__LOGO_DARK__" alt="Prism"></span>
 <span class="tab on" data-t="content">아이템 메타</span>
 <span class="tab" data-t="topic">토픽</span>
 <span class="tab" data-t="user">사용자 메타</span>
 <span class="help" onclick="document.getElementById('hov').style.display='block';document.getElementById('hp').classList.add('on')">? 용어·구조</span>
</div>
<div class="wrap">
 <iframe id="f-content" class="on" sandbox="allow-scripts" srcdoc="__CONTENT_SRCDOC__"></iframe>
 <iframe id="f-topic" sandbox="allow-scripts" srcdoc="__TOPIC_SRCDOC__"></iframe>
 <iframe id="f-user" sandbox="allow-scripts" srcdoc="__USER_SRCDOC__"></iframe>
</div>
<div id="hov" onclick="this.style.display='none';document.getElementById('hp').classList.remove('on')"></div>
<div id="hp"><span class="x" onclick="document.getElementById('hov').style.display='none';this.parentNode.classList.remove('on')">✕</span>
<h2>용어 · 구조 도움말</h2><div class="s">content → 메타 추출 → 그룹핑 → 리포트</div>
<h3>아이템 메타 (콘텐츠 측)</h3>
<dl>
<dt>품질 메타</dt><dd>유통 가능 여부 · <b>G</b> 유통가능 · <b>R</b> 불가 · <b>YELLOW</b> 자동 판정 애매 → 사람 검수</dd>
<dt>법령 메타</dt><dd>위반 유형 스코어링으로 차단 여부 판정(옵션)</dd>
<dt>리드문 / 인텐트</dt><dd>콘텐츠를 '왜·어떻게' 소비하는지 서술(리드문) → 그 분류값(인텐트: 속보·심층 분석·팩트체크 등)</dd>
<dt>엔티티 / 콘텐츠 카테고리</dt><dd>콘텐츠 속 인물·기업·작품 등 고유 대상(엔티티) → IAB 기반 분류(콘텐츠 카테고리: News·Entertainment 등)</dd>
</dl>
<h3>토픽 (그룹핑)</h3>
<dl>
<dt>엔티티형</dt><dd class="pl">단일 엔티티 단위 · "이 인물·기업에 해당하는 콘텐츠" · 영속</dd>
<dt>사건형</dt><dd class="pl">사건 단위 · 엔티티가 여러 콘텐츠에 함께 등장(공출현)하면 자동 묶임 · 단기</dd>
<dt>수동(스튜디오)</dt><dd class="pl">조건 단위 · 운영자가 자연어+조건으로 직접 정의 · 중장기</dd>
</dl>
<h3>사용자 메타 (소비 측)</h3>
<dl>
<dt>소비 형태(FORM)</dt><dd>'무엇'이 아니라 '어떻게' 소비하는가: 세션 길이·체류/완주·전환·깊이·시간대</dd>
<dt>소비 강도</dt><dd>형태에서 산출되는 평가값 · 인텐트(소비 맥락)별 <b>저·중·고</b></dd>
<dt>페르소나</dt><dd>형태·강도를 결합한 사용자 유형(정독러·스낵러·팬덤 등) · 행동 로그 연결 시 실데이터</dd>
</dl>
</div>
<script>
const TABS=['content','topic','user'];
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x===t));
  TABS.forEach(k=>document.getElementById('f-'+k).classList.toggle('on',t.dataset.t===k));
});
</script></body></html>"""


# 집계
def _aggregate(rows):
    grades = {"G": 0, "YELLOW": 0, "R": 0}
    reasons, intents, ecats, services = {}, {}, {}, {}
    ents = set()
    for r in rows:
        qm = r.get("quality_meta", {})
        # 결정 = YELLOW(사람검수)면 YELLOW, 아니면 finalGrade
        decision = "YELLOW" if qm.get("review") == "yellow" else qm.get("finalGrade", "G")
        grades[decision] = grades.get(decision, 0) + 1
        for x in qm.get("reasons", []):
            reasons[x] = reasons.get(x, 0) + 1
        im = r.get("item_meta") or {}
        for c in im.get("intent", []):
            intents[c] = intents.get(c, 0) + 1
        for e in im.get("entities", []):
            ents.add(e)
        # 콘텐츠 단위 N개 · 계수도 콘텐츠 단위다(같은 Tier1 의 하위 분류가 여럿 붙어도 1건).
        # 그래프 경로(_graph)가 이미 집합으로 세고 있어, 태그 단위로 세면 같은 리포트 안에서
        # 카테고리 막대만 하위 분류를 잘게 쪼갠 쪽으로 부풀었다.
        for t1 in dict.fromkeys(tier1_remap(cat) for cat in (im.get("content_category") or [])):
            if t1 and t1 != "Unclassified":      # 미분류는 분포에서 제외
                ecats[t1] = ecats.get(t1, 0) + 1
        svc = r.get("content_ref", {}).get("displayServiceName", "?")
        services[svc] = services.get(svc, 0) + 1
    return {
        "total": len(rows),
        "grades": grades,
        "reasons": _sorted(reasons),
        "intent_categories": _sorted(intents),
        "entity_categories": _sorted(ecats),
        "services": _sorted(services),
        "entity_count": len(ents),
    }


import re as _re
_JUNK_WORDS = ("씨", "아내", "남편", "경찰", "네티즌", "누리꾼", "피해자", "가해자",
               "엄마", "아빠", "부모", "자녀", "남성", "여성", "시민", "유튜버", "기자")


def _is_junk_entity(e: str, service_names: set) -> bool:
    """그래프 허브로 부적합한 엔티티: 서비스명·익명/일반·수치/가격·1글자."""
    e = (e or "").strip()
    if len(e) <= 1:
        return True
    if e in service_names:
        return True
    if _re.fullmatch(r"[\d,.\s]+(원|％|%|년|월|일|명|개|위|호)?", e):
        return True
    if _re.fullmatch(r"[A-Z]씨", e) or e.endswith("씨") and len(e) <= 4:
        return True
    if any(w == e or e.endswith(w) for w in _JUNK_WORDS) and len(e) <= 5:
        return True
    return False


def _canonical_entity_categories(rows, service_names):
    """엔티티 대표 카테고리를 전역 다수결로 단일화.
    1312 이후 content_category 는 '콘텐츠 단위 N개' 라 엔티티 직접 매핑이 없다.
    따라서 엔티티가 등장한 콘텐츠들의 콘텐츠 카테고리를 공기(co-occurrence)로 모아
    다수결로 엔티티당 대표 Tier1 1개를 부여한다(그래프 is_a·토픽 묶음용)."""
    from collections import Counter
    votes = {}
    for r in rows:
        im = r.get("item_meta") or {}
        cats = [tier1_remap(c) for c in (im.get("content_category") or [])]
        cats = [t for t in cats if t and t != "Unclassified"]
        if not cats:
            continue
        for e in im.get("entities", []):
            if _is_junk_entity(e, service_names):
                continue
            for t1 in cats:
                votes.setdefault(e, Counter())[t1] += 1
    return {e: cnt.most_common(1)[0][0] for e, cnt in votes.items()}


def _graph(rows, max_nodes: int = 900, top_entities: int = 260):
    """노드: content / entity / category. 링크로 연결.
    엔티티가 여러 콘텐츠에 공유되면 degree 가 커져 허브가 된다(토픽 후보).

    매핑 정제(중요):
      - 엔티티→카테고리는 전역 다수결로 단일화 → 엔티티당 is_a 엣지 1개(노이즈 제거)
      - 서비스명·익명/일반·수치 엔티티는 정크로 제외(가짜 허브 방지)
    대량이면 표시용으로 공유 엔티티(deg≥2) 상위 top_entities + 연결 노드로 가지치기."""
    service_names = set(D.SERVICE_GROUP.keys()) | {
        r.get("content_ref", {}).get("displayServiceName", "") for r in rows}
    canon = _canonical_entity_categories(rows, service_names)

    nodes, idx, links = [], {}, []

    def node(nid, label, kind, group=""):
        if nid not in idx:
            idx[nid] = len(nodes)
            nodes.append({"id": nid, "label": label, "kind": kind, "group": group, "deg": 0})
        return nid

    for i, r in enumerate(rows):
        ref = r.get("content_ref", {})
        qm = r.get("quality_meta", {})
        decision = "YELLOW" if qm.get("review") == "yellow" else qm.get("finalGrade", "G")
        cid = f"c:{i}"
        node(cid, ref.get("title", "")[:24] or f"content {i}", "content",
             ref.get("displayServiceName", ""))
        nodes[idx[cid]]["grade"] = decision   # 그래프 등급 필터용
        im = r.get("item_meta") or {}
        # 콘텐츠 단위 카테고리(1312·N개): 콘텐츠에 직접 매핑
        content_cats = {tier1_remap(c) for c in (im.get("content_category") or [])}
        content_cats = {t for t in content_cats if t and t != "Unclassified"}
        for e in im.get("entities", []):
            if _is_junk_entity(e, service_names):
                continue
            eid = f"e:{e}"
            node(eid, e, "entity")
            links.append({"s": cid, "t": eid, "rel": "mentions"})
            # 엔티티 대표 카테고리(공기 다수결, 엔티티당 1개)
            t1 = canon.get(e)
            if t1:
                kid = f"k:{t1}"
                node(kid, t1, "category")
                links.append({"s": eid, "t": kid, "rel": "is_a"})
        # 콘텐츠 → 콘텐츠 카테고리 직접 엣지(belongs_to): 엔티티 레이어를 꺼도 매핑이 보임
        for t1 in content_cats:
            node(f"k:{t1}", t1, "category")
            links.append({"s": cid, "t": f"k:{t1}", "rel": "belongs_to"})
        for c in im.get("intent", []):
            iid = f"i:{c}"
            node(iid, c, "intent")
            links.append({"s": cid, "t": iid, "rel": "intent"})
    # is_a 링크 중복 제거(엔티티당 1개 카테고리지만 콘텐츠마다 반복 추가됨)
    seen_l = set()
    dedup = []
    for l in links:
        key = (l["s"], l["t"], l["rel"])
        if l["rel"] == "is_a":
            if key in seen_l:
                continue
            seen_l.add(key)
        dedup.append(l)
    links = dedup

    # degree 계산
    deg = {}
    for l in links:
        deg[l["s"]] = deg.get(l["s"], 0) + 1
        deg[l["t"]] = deg.get(l["t"], 0) + 1
    for n in nodes:
        n["deg"] = deg.get(n["id"], 0)

    total_entities = sum(1 for n in nodes if n["kind"] == "entity")
    # 가지치기 불필요하면 그대로
    if len(nodes) <= max_nodes:
        return nodes, links, {"shown": len(nodes), "total": len(nodes),
                              "entities_total": total_entities, "pruned": False}

    # 엔티티 → 등장 콘텐츠 맵 (엔티티는 콘텐츠에서 파생 → 항상 콘텐츠에 앵커)
    ent_contents = {}
    for l in links:
        if l["rel"] == "mentions":          # content(s) → entity(t)
            ent_contents.setdefault(l["t"], []).append(l["s"])

    # 공유 엔티티(deg≥2) 상위 선택: 콘텐츠가 있는 것만
    ents = sorted([n for n in nodes if n["kind"] == "entity" and n["deg"] >= 2
                   and ent_contents.get(n["id"])],
                  key=lambda n: -n["deg"])[:top_entities]

    keep = set()
    # 1) 각 엔티티 + 대표 콘텐츠 1개를 함께 추가(엔티티가 콘텐츠 없이 뜨지 않도록)
    for n in ents:
        if len(keep) >= max_nodes - 2:
            break
        keep.add(n["id"])
        keep.add(ent_contents[n["id"]][0])
    # 2) 남은 상한을 kept 엔티티의 추가 콘텐츠로 채움
    for n in ents:
        if n["id"] not in keep:
            continue
        for c in ent_contents[n["id"]]:
            if len(keep) >= max_nodes:
                break
            keep.add(c)
    # 3) kept 콘텐츠/엔티티에 연결된 카테고리·인텐트만 추가
    for l in links:
        if l["s"] in keep and l["t"][:2] in ("k:", "i:"):
            keep.add(l["t"])
        if l["t"] in keep and l["s"][:2] in ("k:", "i:"):
            keep.add(l["s"])

    # 4) 고아 제거 (2-pass): 콘텐츠 엣지 없는 엔티티 → 드롭 → 엣지 없는 카테고리/인텐트 드롭
    def _within(ks):
        return [l for l in links if l["s"] in ks and l["t"] in ks]
    pl = _within(keep)
    ent_ok = {l["t"] for l in pl if l["rel"] == "mentions"}
    keep = {nid for nid in keep if nid[:2] != "e:" or nid in ent_ok}
    pl = _within(keep)
    linked = set()
    for l in pl:
        linked.add(l["s"]); linked.add(l["t"])
    keep = {nid for nid in keep if nid[:2] == "c:" or nid in linked}

    pnodes = [n for n in nodes if n["id"] in keep]
    plinks = _within(keep)
    return pnodes, plinks, {"shown": len(pnodes), "total": len(nodes),
                            "entities_total": total_entities, "pruned": True,
                            "top_entities": len(ents)}


def _table_rows(rows):
    out = []
    for r in rows:
        ref = r.get("content_ref", {})
        qm = r.get("quality_meta", {})
        im = r.get("item_meta") or {}
        out.append({
            "service": ref.get("displayServiceName", ""),
            "title": ref.get("title", ""),
            "grade": qm.get("finalGrade", ""),
            "review": qm.get("review", "auto"),
            "confidence": qm.get("confidence"),
            "decision": "YELLOW" if qm.get("review") == "yellow" else qm.get("finalGrade", ""),
            "reasons": qm.get("reasons", []),
            "intent": im.get("summary", ""),
            "entities": im.get("entities", []),
            "intent_categories": im.get("intent", []),
            "entity_categories": im.get("content_category", []),   # 콘텐츠 단위 N개
        })
    return out


def _sorted(d):
    return sorted(([k, v] for k, v in d.items()), key=lambda kv: -kv[1])


def _read_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# self-contained HTML (vanilla JS, canvas force graph, no CDN)
_HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>아이템 메타 현황</title>
<style>
/* 디자인 토큰 별칭 → --ds-* (theme.inject 가 --ds-* 정의·모드전환 제공) */
:root{--bg:var(--ds-canvas,#f4f5f7);--surface:var(--ds-surface,#fff);--ink:var(--ds-ink,#000);--ink2:var(--ds-body,rgba(0,0,0,.88));--mut:var(--ds-muted,rgba(0,0,0,.48));--faint:var(--ds-placeholder,rgba(0,0,0,.32));
--line:var(--ds-hairline,rgba(0,0,0,.08));--pri:var(--ds-primary,#1e84ff);
--purple:var(--ds-cat-community,#5e47eb);--orange:var(--ds-warning,#ff9429);--teal:var(--ds-cat-sports,#5c77ff);--green:var(--ds-success,#18ba45);
--ac:var(--ds-primary,#1e84ff);
--r:var(--ds-error,#ff4e33);--ent:var(--ds-warning,#ff9429);--cat:var(--ds-cat-entertainment,#a05cff);--int:var(--ds-cat-sports,#5c77ff);
--sh:var(--ds-shadow-medium,0 1px 10px 0 rgba(0,0,0,.08));--radius:var(--ds-radius-lg,16px);--font:var(--ds-font-body,'Pretendard Variable',-apple-system,sans-serif)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--font);
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;letter-spacing:-.05px}
header{padding:24px 28px 20px;border-bottom:1px solid var(--line)}
h1{font-size:26px;margin:0;font-weight:600;letter-spacing:-.022em;color:var(--ink)}
.sub{color:var(--mut);font-size:14px;margin:5px 0 0}
.wrap{display:grid;grid-template-columns:340px 1fr;gap:24px;padding:24px 28px;max-width:1700px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px;margin-bottom:20px;box-shadow:var(--sh)}
.card h2{font-size:12px;margin:0 0 14px;color:var(--mut);text-transform:uppercase;letter-spacing:.125px;font-weight:600}
.bar{display:flex;align-items:center;gap:10px;margin:8px 0;font-size:13px}
.bar .lab{width:128px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--ink2)}
.bar .track{flex:1;background:var(--ds-surface-on);border-radius:999px;height:8px;overflow:hidden}
.bar .fill{display:block;height:100%;min-width:3px;border-radius:999px;background:var(--pri);
transition:width .5s cubic-bezier(.4,0,.2,1)}.bar .n{width:34px;text-align:right;color:var(--mut);font-variant-numeric:tabular-nums}
.grades{display:flex;gap:10px}.grades div{flex:1;text-align:center;border-radius:10px;padding:14px 8px;border:1px solid var(--line);background:var(--bg)}
.gG{color:var(--green)}.gR{color:var(--r)}.gY{color:var(--orange)}
.grades b{font-size:28px;display:block;font-weight:700;letter-spacing:-.02em;line-height:1.1}
.grades div span{font-size:12px;color:var(--mut)}
.pill.YELLOW{color:var(--orange);background:rgba(221,91,0,.1)}
#g{width:100%;height:600px;background:var(--bg);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;cursor:grab}
#g canvas{display:block}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;margin-top:10px;color:var(--mut)}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);position:sticky;top:0;background:var(--bg);font-weight:600;font-size:12px;
text-transform:uppercase;letter-spacing:.125px}
.tag{display:inline-block;background:var(--bg);border:1px solid var(--line);border-radius:5px;padding:1px 7px;margin:1px;font-size:12px;color:var(--ink2)}
.tag.r{background:rgba(229,72,77,.1);border-color:rgba(229,72,77,.2);color:var(--r)}
.gp{height:340px;overflow:auto}.tw{max-height:440px;overflow:auto;border-radius:8px}
.pill{font-weight:600;padding:2px 9px;border-radius:999px;font-size:12px}
.pill.G{color:var(--green);background:rgba(26,174,57,.1)}.pill.R{color:var(--r);background:rgba(229,72,77,.1)}
input{background:var(--ds-surface-white);border:1px solid var(--ds-hairline);color:var(--ds-ink);border-radius:var(--ds-radius-md);padding:7px 10px;width:100%;font-size:14px;font-family:var(--ds-font-body);outline:none}
input:focus{border-color:var(--ds-primary);box-shadow:var(--ds-focus-ring)}
.chips{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
.chip{cursor:pointer;border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:4px 13px;font-size:13px;color:var(--mut);user-select:none;transition:.12s}
.chip:hover{border-color:var(--ds-border-input-hover);color:var(--ink2)}
.chip.on{background:var(--ds-primary-tint);color:var(--ds-primary-deep);border-color:var(--ds-primary)}
.chip.cG.on{border-color:var(--green);color:var(--green);background:rgba(26,174,57,.1)}
.chip.cYELLOW.on{border-color:var(--orange);color:var(--orange);background:rgba(221,91,0,.1)}
.chip.cR.on{border-color:var(--r);color:var(--r);background:rgba(229,72,77,.1)}
.gnote{font-size:12px;color:var(--faint);margin-top:8px}
#tbl tbody tr{cursor:pointer}#tbl tbody tr:hover{background:var(--bg)}
/* 상세 드로어 */
#ov{position:fixed;inset:0;background:rgba(0,0,0,.35);display:none;z-index:9}
#dw{position:fixed;top:0;right:0;height:100%;width:min(700px,94vw);background:var(--surface);
 border-left:1px solid var(--line);box-shadow:rgba(0,0,0,.05) 0 23px 52px,rgba(0,0,0,.04) -8px 0 28px;overflow:auto;
 transform:translateX(100%);transition:transform .22s;z-index:10;padding:22px 24px}
#dw.open{transform:none}
#dw .x{position:absolute;top:16px;right:18px;color:var(--mut);cursor:pointer;font-size:20px}
#dw h3{margin:0 18px 2px 0;font-size:22px;font-weight:700;letter-spacing:-.25px}#dw .meta{color:var(--mut);font-size:13px;margin-bottom:14px}
.stage{border:1px solid var(--line);border-radius:8px;
 padding:13px 15px;margin:10px 0;background:var(--bg)}
.stage .h{display:flex;align-items:center;gap:8px;font-weight:600;font-size:14px;margin-bottom:7px}
.stage .h .k{margin-left:auto;font-weight:400;font-size:12px;color:var(--mut)}
.stage.skip{opacity:.6}.stage.gate{border-color:rgba(229,72,77,.5)}
.stage.ok{border-color:rgba(39,166,68,.45)}
.kv{font-size:13px;color:var(--ink2);margin:3px 0}.kv b{color:var(--mut);font-weight:500}
.flowarrow{text-align:center;color:var(--faint);font-size:13px;margin:0}
.badge{font-size:11px;padding:1px 7px;border-radius:5px;background:var(--bg);border:1px solid var(--line);margin-left:4px}
.badge.emb{background:rgba(155,89,230,.12);color:var(--purple);border-color:rgba(155,89,230,.25)}
.badge.llm{background:var(--ds-primary-tint);color:var(--ds-primary-deep);border-color:rgba(30,132,255,.25)}
.badge.rule{background:rgba(42,157,153,.12);color:var(--teal);border-color:rgba(42,157,153,.25)}
code.j{display:block;white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;
 font-size:12px;color:var(--ink2);margin-top:6px;max-height:170px;overflow:auto}
</style><script>/*__FORCEGRAPH__*/</script></head><body>
<div id="ov" onclick="closeD()"></div><div id="dw"><span class="x" onclick="closeD()">✕</span><div id="dwc"></div></div>
<header><span class="eyebrow">콘텐츠 · 아이템 메타</span><h1 id="ttl">아이템 메타 현황</h1>
<div class="sub" id="sub"></div></header>
<div class="wrap">
 <div>
  <div class="card"><h2>품질 등급</h2><div class="grades" id="grades"></div></div>
  <div class="card"><h2>붙은 품질 메타 <span class="hint" data-tip="차단·검수 사유(reason)별 분포">?</span></h2><div id="reasons"></div></div>
  <div class="card"><h2>인텐트</h2><div id="intents"></div></div>
  <div class="card"><h2>콘텐츠 카테고리 <span class="hint" data-tip="IAB Tier1 분류 기준">?</span></h2><div id="ecats"></div></div>
  <div class="card"><h2>서비스 분포</h2><div id="services"></div></div>
 </div>
 <div>
  <div class="card"><h2>관계도 <span class="hint" data-tip="콘텐츠 · 엔티티 · 카테고리 노드">?</span></h2>
   <div class="chips" id="gchips"></div>
   <div id="g"></div>
   <div class="legend">
    <span><span class="dot" style="background:var(--ac)"></span>콘텐츠</span>
    <span><span class="dot" style="background:var(--ent)"></span>엔티티</span>
    <span><span class="dot" style="background:var(--cat)"></span>콘텐츠 카테고리</span>
    <span><span class="dot" style="background:var(--int)"></span>인텐트</span>
    <span style="margin-left:auto">기본: 엔티티·카테고리만 · 콘텐츠/인텐트는 위 레이어로 켜기 · 노드 클릭=연관 강조</span>
   </div>
   <div class="gnote" id="gnote"></div>
  </div>
  <div class="card"><h2>콘텐츠별 메타</h2>
   <div class="chips" id="chips"></div>
   <input id="q" placeholder="제목·엔티티·메타 검색…">
   <div class="tw"><table id="tbl"><thead><tr>
    <th>서비스</th><th>제목</th><th>등급</th><th>reasons</th><th>엔티티</th><th>인텐트카테고리</th>
   </tr></thead><tbody></tbody></table></div>
  </div>
 </div>
</div>
<script>
const DATA = /*__DATA__*/;
const esc = s => (s||"").replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
document.getElementById('ttl').textContent = DATA.title;
document.getElementById('sub').textContent =
  `콘텐츠 ${DATA.agg.total}건 · 엔티티 ${DATA.agg.entity_count}종 · G ${DATA.agg.grades.G||0} / R ${DATA.agg.grades.R||0}`;

// 막대
function bars(el, pairs, color){
  const max = Math.max(1, ...pairs.map(p=>p[1]));
  el.innerHTML = pairs.length? pairs.map(([k,v])=>
    `<div class="bar"><span class="lab" title="${esc(k)}">${esc(k)}</span>
     <span class="track"><span class="fill" style="width:${v/max*100}%;background:${color}"></span></span>
     <span class="n">${v}</span></div>`).join('') : '<div class="sub">없음</div>';
}
const A = DATA.agg;
document.getElementById('grades').innerHTML =
 `<div class="gG"><b>${A.grades.G||0}</b>G · 유통가능</div>
  <div class="gY"><b>${A.grades.YELLOW||0}</b>YELLOW · 검수</div>
  <div class="gR"><b>${A.grades.R||0}</b>R · 불가</div>`;
bars(document.getElementById('reasons'), A.reasons, 'var(--r)');
bars(document.getElementById('intents'), A.intent_categories, 'var(--int)');
bars(document.getElementById('ecats'), A.entity_categories, 'var(--cat)');
bars(document.getElementById('services'), A.services, 'var(--ac)');

// 표 + 검색
const tb = document.querySelector('#tbl tbody');
let gradeFilter='ALL', textFilter='';
// 등급 필터 칩
const gcount=DATA.agg.grades;
const chipDefs=[['ALL','전체',DATA.rows.length],['G','G',gcount.G||0],
  ['YELLOW','YELLOW',gcount.YELLOW||0],['R','R',gcount.R||0]];
document.getElementById('chips').innerHTML=chipDefs.map(([k,lab,n])=>
  `<span class="chip c${k} ${k==='ALL'?'on':''}" data-g="${k}">${lab} ${n}</span>`).join('');
document.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
  gradeFilter=c.dataset.g;
  document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x===c));
  render();
});
function render(){
  const f=textFilter.toLowerCase();
  tb.innerHTML = DATA.rows.map((r,i)=>({r,i}))
   .filter(({r})=>(gradeFilter==='ALL'||r.decision===gradeFilter))
   .filter(({r})=>!f || (r.title+JSON.stringify(r.entities)+JSON.stringify(r.reasons)).toLowerCase().includes(f))
   .map(({r,i})=>`<tr onclick="openD(${i})" title="클릭 → 추출 로직 상세">
     <td>${esc(r.service)}</td><td>${esc(r.title)}</td>
     <td><span class="pill ${r.decision}">${r.decision}</span>${r.confidence!=null?`<br><span class="uid" style="font-size:10px">c=${r.confidence}</span>`:''}</td>
     <td>${r.reasons.map(x=>`<span class="tag r">${esc(x)}</span>`).join('')||'·'}</td>
     <td>${r.entities.map(x=>`<span class="tag">${esc(x)}</span>`).join('')||'·'}</td>
     <td>${r.intent_categories.map(x=>`<span class="tag">${esc(x)}</span>`).join('')||'·'}</td>
   </tr>`).join('');
}
render(); document.getElementById('q').oninput=e=>{textFilter=e.target.value;render();};

// ── 개별 콘텐츠 상세: 전체 추출 로직/프로세싱 파이프라인 ──
function vbadge(agent){ // 에이전트명 → 처리방식 배지
  if(/emb/i.test(agent)) return '<span class="badge emb">임베딩</span>';
  if(/prefilter/i.test(agent)) return '<span class="badge emb">임베딩 사전필터</span>';
  if(/Agent/.test(agent)) return '<span class="badge llm">Solar LLM</span>';
  return '';
}
function stage(cls,title,key,bodyHtml){
  return `<div class="stage ${cls}"><div class="h">${title}
    <span class="k">${key||''}</span></div>${bodyHtml}</div>`;
}
function openD(i){
  const full=DATA.full[i], row=DATA.rows[i];
  const ref=full.content_ref||{}, rt=full.routing||{}, lm=full.legal_meta||{},
        qm=full.quality_meta||{}, im=full.item_meta, tr=full.trace||{};
  const vd=tr.agent_verdicts||[], fb=tr.fallbacks||[];
  const find=re=>vd.find(v=>re.test(v.agent||''));
  let h=`<h3>${esc(ref.title)}</h3>
   <div class="meta">${esc(ref.displayServiceName)} · body_hash ${esc(ref.body_hash)} ·
   비용 $${(tr.cost_usd||0).toFixed(5)} · 토큰 in ${(tr.tokens||{}).in||0}/out ${(tr.tokens||{}).out||0}
   · ${tr.prompt_version||''}</div>`;

  // 1. Dispatcher (규칙)
  h+=stage('','① Dispatcher <span class="badge rule">규칙·No-LLM</span>','$0',
    `<div class="kv"><b>서비스 그룹</b> ${rt.service_group}</div>
     <div class="kv"><b>콘텐츠 트랙</b> ${rt.content_track}</div>
     <div class="kv"><b>활성 품질 메타</b> ${(rt.active_quality_metas||[]).join(', ')}</div>`);
  h+='<div class="flowarrow">↓</div>';

  // 2. Legal (옵션)
  if(lm.enabled){
    const red=lm.representative_grade==='RED';
    h+=stage(red?'gate':'ok','② 법령 메타 '+vbadge('Agent'),lm.representative_grade,
     `<div class="kv"><b>대표 등급/점수</b> ${lm.representative_grade} / ${lm.representative_score}</div>`+
     (lm.harm_types||[]).map(ht=>`<div class="kv">· ${ht.code} (${esc(ht.routed_article)})
       A${ht.scores.a}+B${ht.scores.b}+C${ht.scores.c}=${ht.scores.total} → ${ht.grade}</div>`).join('')+
     (red?'<div class="kv" style="color:var(--r)">RED → 이하 스테이지 차단</div>':''));
    h+='<div class="flowarrow">↓</div>';
  }else{
    h+=stage('skip','② 법령 메타','OFF','<div class="kv">비활성(옵션 스테이지)</div>');
    h+='<div class="flowarrow">↓</div>';
  }

  // 3. Quality (+ YELLOW)
  const qv=find(/Quality/);
  const yv=find(/Yellow/i);
  const isY = qm.review==='yellow';
  const decision = isY?'YELLOW':qm.finalGrade;
  const qmethod = qv? vbadge(qv.agent) : vbadge('Agent');
  h+=stage(isY?'gate':(qm.finalGrade==='G'?'ok':'gate'),'③ 품질 메타 '+qmethod,
    'decision '+decision,
   `<div class="kv"><b>판정</b> <span class="pill ${decision}">${decision}</span>
      ${isY?'사람 검수 필요(저신뢰)':(qm.finalGrade==='G'?'유통 가능':'유통 불가')}
      ${qm.confidence!=null?` · conf=${qm.confidence}`:''}</div>
    ${isY&&qm.review_reason?`<div class="kv" style="color:var(--ent)"><b>YELLOW 사유</b> ${esc(qm.review_reason)}</div>`:''}
    <div class="kv"><b>reasons</b> ${(qm.reasons||[]).map(x=>`<span class="tag r">${esc(x)}</span>`).join('')||'없음(normal)'}</div>
    ${qv&&qv.evidence?`<div class="kv"><b>근거</b> ${esc(qv.evidence)}</div>`:''}`);
  h+='<div class="flowarrow">↓</div>';

  // 4. Item (G 또는 YELLOW)
  if(im){
    const ic=find(/IntentCategory/), ec=find(/EntityCategory/);
    h+=stage('ok','④ 아이템 메타 (intent→entities→categories)','finalGrade=G',
     `<div class="kv"><b>intent</b> ${esc(im.intent)}</div>
      <div class="kv"><b>entities</b> ${(im.entities||[]).map(x=>`<span class="tag">${esc(x)}</span>`).join('')}
        ${vbadge('Agent')}</div>
      <div class="kv"><b>intent_categories</b> ${(im.intent_categories||[]).map(x=>`<span class="tag">${esc(x)}</span>`).join('')}
        ${ic?vbadge(ic.agent)+(ic.evidence?` <span class="k">${esc(ic.evidence)}</span>`:''):''}</div>
      <div class="kv"><b>entity_categories</b> ${ec?vbadge(ec.agent):''}</div>
      ${Object.entries(im.entity_categories||{}).map(([k,v])=>
        `<div class="kv">· ${esc(k)} → ${esc(v)}</div>`).join('')}`);
  }else{
    h+=stage('skip','④ 아이템 메타','SKIP',
     '<div class="kv">finalGrade=R(또는 image_only) → 아이템 메타 생략(유통 불가)</div>');
  }
  h+='<div class="flowarrow">↓</div>';

  // 5. Verifier / fallbacks
  h+=stage('','⑤ Verifier <span class="badge rule">규칙·No-LLM</span>','정합성·사전 강제',
    fb.length? fb.map(x=>`<div class="kv">· ${esc(String(x))}</div>`).join('')
      : '<div class="kv">보정 없음(스키마·사전·정합성 통과)</div>');

  // raw
  h+=`<div class="stage"><div class="h">원본 출력 JSON</div>
     <code class="j">${esc(JSON.stringify(full,null,2))}</code></div>`;

  document.getElementById('dwc').innerHTML=h;
  document.getElementById('dw').classList.add('open');
  document.getElementById('ov').style.display='block';
}
function closeD(){document.getElementById('dw').classList.remove('open');
  document.getElementById('ov').style.display='none';}
addEventListener('keydown',e=>{if(e.key==='Escape')closeD();});

// ── 관계도: force-graph (vendored, MIT) + 등급/레이어 필터 + 하이라이트 ──
// 색은 현재 디자인 토큰(--ds-*)에서 런타임 조회 → 라이트/다크 모두 대응
const _cssv=(n,f)=>{const v=getComputedStyle(document.documentElement).getPropertyValue(n).trim();return v||f;};
const COL={content:_cssv('--ds-primary','#1e84ff'),entity:_cssv('--ds-warning','#ff9429'),category:_cssv('--ds-cat-entertainment','#a05cff'),intent:_cssv('--ds-cat-sports','#5c77ff')};
const G_BG=_cssv('--ds-canvas','#f4f5f7'), G_INK=_cssv('--ds-ink','#000'), G_MUT=_cssv('--ds-muted','rgba(0,0,0,.48)'), G_LINE=_cssv('--ds-hairline','rgba(0,0,0,.08)');
const KLAB={content:'콘텐츠',entity:'엔티티',category:'콘텐츠 카테고리',intent:'인텐트'};
function _hexA(hex,a){const h=(hex||'#888').replace('#','');return `rgba(${parseInt(h.slice(0,2),16)},${parseInt(h.slice(2,4),16)},${parseInt(h.slice(4,6),16)},${a})`;}
// 기본 보기: 엔티티↔카테고리 구조만(콘텐츠·인텐트는 토글로): 헤어볼 방지
const gLayers={content:false,entity:true,category:true,intent:false};
const gGrades={G:true,YELLOW:true,R:true};
const gEl=document.getElementById('g');

// 불변 링크 스펙(필터마다 새 객체 생성), 인접 맵(하이라이트용)
const LINKSPEC=DATA.links.map(l=>({s:l.s,t:l.t,rel:l.rel}));
const adj={}; LINKSPEC.forEach(l=>{(adj[l.s]=adj[l.s]||new Set()).add(l.t);(adj[l.t]=adj[l.t]||new Set()).add(l.s);});
const byId=Object.fromEntries(DATA.nodes.map(n=>[n.id,n]));
let hi=null, pin=null;                // hi=유효 강조(호버 또는 고정) · pin=클릭 고정
const _lid=l=>typeof l==='object'?l.id:l;
function near(){ return hi? new Set([hi,...(adj[hi]||[])]) : null; }
// 집계+펼치기: 카테고리별 엔티티 목록 / 카테고리 크기 / 펼친 카테고리
const catEnts={}; LINKSPEC.forEach(l=>{ if(l.rel==='is_a'){ (catEnts[l.t]=catEnts[l.t]||[]).push(l.s); }});
const catSize={}; for(const c in catEnts) catSize[c]=catEnts[c].length;
const TOTAL_ENT=Object.values(catSize).reduce((a,b)=>a+b,0);
// 기본은 펼쳐서 '연결된' 엔티티↔카테고리 그래프(서버 가지치기로 보통 수백 노드 이하).
// 극단적으로 큰 경우(가지치기 해제 등)만 집계 시작 → 헤어볼 방지. 접기 버튼은 항상 제공.
const AGG_THRESHOLD=800;
let expanded=new Set(TOTAL_ENT<=AGG_THRESHOLD ? Object.keys(catSize) : []);

const Graph=ForceGraph()(gEl)
  .backgroundColor(G_BG)
  .nodeRelSize(5)
  .nodeVal(n=> n.kind==='category' ? 6+Math.min(60,(catSize[n.id]||0))*0.8 : 1+Math.min(18,(n.deg||0))*1.2)
  .nodeColor(n=>{ const ns=near(); const c=COL[n.kind]||G_MUT; return (ns&&!ns.has(n.id)) ? _hexA(c,0.3) : c; })
  .nodeLabel(n=>`<div style="font:12px var(--ds-font-body,-apple-system);padding:2px 4px"><b>${esc(n.label)}</b><br><span style="color:${G_MUT}">${KLAB[n.kind]||n.kind}${n.deg?' · 연결 '+n.deg:''}</span></div>`)
  .nodeCanvasObjectMode(()=>'after')
  .nodeCanvasObject((n,ctx,scale)=>{
    // 라벨은 허브만(카테고리·인텐트 + 연결많은 엔티티), 또는 하이라이트 시 이웃
    const ns=near();
    const hub = n.kind==='category' || n.kind==='intent' || (n.kind==='entity' && (n.deg||0)>=4);
    const show = hi ? (ns&&ns.has(n.id)) : hub;
    if(!show || scale<0.5) return;
    const dim = ns && !ns.has(n.id);
    const isCat=n.kind==='category';
    const r=isCat? (6+Math.min(60,(catSize[n.id]||0))*0.8) : Math.cbrt(1+Math.min(18,(n.deg||0)))*5;
    const lab=isCat? `${n.label} (${catSize[n.id]||0})${expanded.has(n.id)?' ▾':' ▸'}` : n.label;
    ctx.font=`${(isCat?13:11.5)/scale}px 'Pretendard Variable', -apple-system, sans-serif`;
    ctx.textBaseline='middle';
    const x=n.x+Math.cbrt(r)*3/scale+2;
    // 헤일로 = 배경색(캔버스) → 어떤 노드색 위에서도 라이트/다크 모두 읽힘
    ctx.lineWidth=3/scale; ctx.strokeStyle=G_BG; ctx.strokeText(lab, x, n.y);
    ctx.fillStyle=dim?G_MUT:G_INK; ctx.fillText(lab, x, n.y);
  })
  .linkColor(l=>{ const ns=near(); if(ns){ return (ns.has(_lid(l.source))&&ns.has(_lid(l.target)))?_hexA(COL.content,0.55):G_LINE; } return G_LINE; })
  .linkWidth(l=>{ const ns=near(); if(ns&&ns.has(_lid(l.source))&&ns.has(_lid(l.target))) return 1.8; return 0.5; })
  .linkDirectionalParticles(0)
  .autoPauseRedraw(false)            // 하이라이트가 매 프레임 반영되도록
  .onNodeClick(n=>{
    if(n.kind==='category'){ // 클릭 = 펼치기/접기(집계 드릴다운)
      if(expanded.has(n.id)) expanded.delete(n.id); else expanded.add(n.id);
      pin=null; hi=null; rebuild(); return;
    }
    if(/^c:/.test(n.id)){ openD(+n.id.slice(2)); return; }   // 콘텐츠 클릭 = 상세(관계는 호버로 미리보기)
    pin=(pin===n.id?null:n.id); hi=pin;                      // 엔티티·인텐트 클릭 = 고정 강조 토글
  })
  .onBackgroundClick(()=>{ pin=null; hi=null; })
  .onNodeHover(n=>{ gEl.style.cursor=n?'pointer':'grab'; hi=n?n.id:pin; })     // 호버 = 연결 관계 미리보기
  .cooldownTicks(220);
// 더 넓게 펼치기: 반발 강화 + 링크 거리 확대(겹침 완화)
Graph.d3Force('charge').strength(-220).distanceMax(600);
Graph.d3Force('link').distance(l=>{ const k=(l.target&&l.target.kind)||''; return k==='category'?70:42; });

function sizeGraph(){ Graph.width(gEl.clientWidth).height(gEl.clientHeight); }
sizeGraph(); addEventListener('resize',sizeGraph);

function rebuild(){
  // 집계 보기: 카테고리는 항상, 엔티티는 '펼친 카테고리' 것만. (카테고리 레이어 끄면 엔티티 전체)
  const ok=new Set();
  const allEnt=!gLayers.category;          // 카테고리 숨기면 엔티티 집계 해제(전체)
  for(const n of DATA.nodes){
    if(n.kind==='category'){ if(gLayers.category) ok.add(n.id); continue; }
    if(n.kind==='intent'){ if(gLayers.intent) ok.add(n.id); continue; }
    if(n.kind==='content'){ if(gLayers.content && gGrades[n.grade||'G']) ok.add(n.id); continue; }
    if(n.kind==='entity' && gLayers.entity){
      if(allEnt){ ok.add(n.id); }
      // 펼친 카테고리에 속한 엔티티만
      else if([...(adj[n.id]||[])].some(t=>t[0]==='k'&&expanded.has(t))) ok.add(n.id);
    }
  }
  const nodes=DATA.nodes.filter(n=>ok.has(n.id));
  const links=LINKSPEC.filter(l=>ok.has(l.s)&&ok.has(l.t)).map(l=>({source:l.s,target:l.t,rel:l.rel}));
  if(pin&&!ok.has(pin)) pin=null;
  if(hi&&!ok.has(hi)) hi=null;
  Graph.graphData({nodes,links});
  setTimeout(()=>{ try{ Graph.zoomToFit(500,40); }catch(e){} }, 350);
  // gnote: 보이는 모든 레이어를 집계해 토글이 반영되는 게 보이게
  const KN={content:'콘텐츠',entity:'엔티티',category:'카테고리',intent:'인텐트'};
  const parts=[];
  for(const k of ['content','category','entity','intent']){
    const c=nodes.filter(n=>n.kind===k).length;
    if(c) parts.push(`${KN[k]} ${c}`);
  }
  document.getElementById('gnote').textContent=
    (parts.join(' · ') || '표시할 노드 없음 (레이어를 켜세요)') + `  ·  링크 ${links.length}`
    + (gLayers.content ? '  ·  콘텐츠: 호버=관계 보기 · 클릭=상세' : '')
    + (gLayers.category && TOTAL_ENT>AGG_THRESHOLD ? '  ·  카테고리 클릭 = 엔티티 펼치기' : '');
}

// 필터 칩 UI
function renderGChips(){
  const grd=[['G','G'],['YELLOW','Y'],['R','R']].map(([k,lab])=>
    `<span class="chip c${k} ${gGrades[k]?'on':''}" data-grade="${k}">${lab}</span>`).join('');
  const lay=[['content','콘텐츠'],['entity','엔티티'],['category','콘텐츠 카테고리'],['intent','인텐트']].map(([k,lab])=>
    `<span class="chip ${gLayers[k]?'on':''}" data-layer="${k}" style="${gLayers[k]?`border-color:${COL[k]};color:${COL[k]};background:${COL[k]}26;font-weight:700`:''}">${lab}</span>`).join('');
  document.getElementById('gchips').innerHTML='<span class="uid" style="align-self:center">등급</span>'+grd
    +'<span class="uid" style="align-self:center;margin-left:8px">레이어</span>'+lay
    +'<span class="chip" data-act="expand" style="margin-left:8px">⊕ 모두 펼치기</span>'
    +'<span class="chip" data-act="collapse">⊖ 접기</span>'
    +'<span class="chip" data-act="fit">⤢ 맞춤</span>';
  document.querySelectorAll('#gchips .chip').forEach(c=>c.onclick=()=>{
    if(c.dataset.grade){gGrades[c.dataset.grade]=!gGrades[c.dataset.grade];}
    else if(c.dataset.layer){gLayers[c.dataset.layer]=!gLayers[c.dataset.layer];}
    else if(c.dataset.act==='expand'){ Object.keys(catSize).forEach(k=>expanded.add(k)); }
    else if(c.dataset.act==='collapse'){ expanded.clear(); }
    else if(c.dataset.act==='fit'){Graph.zoomToFit(500,40);return;}
    renderGChips(); rebuild();
  });
}
renderGChips(); rebuild();
</script></body></html>"""
