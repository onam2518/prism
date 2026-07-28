"""토픽 도메인 (serve 에서 분리 · 라우트 분리 4차).

토픽 빌드(자동·사용자 정의)·스튜디오 액션(save/delete/settings/preview/suggest/exclude)·
중복 감지(similar_topics)·성과 스냅샷 시계열·드릴다운·타겟 페르소나를 담당한다.
HTTP 디스패치는 serve 가 유지.

컴포지션: 서버 환경(스토어·결과 뷰·집계 캐시·리포트 영속·LLM 라우팅·mock 플래그)은
serve 가 기동 시 `_SV`(자기 모듈 객체)로 주입한다(learnops 와 동일 관례 · 순환 import 없음).
테스트가 serve.topics_data 등을 몽키패치하므로 공개 함수의 상호 호출도 `_SV.` 경유가 계약.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time

from .config import Config

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


def _studio_config() -> dict:
    """토픽 스튜디오 설정(사용자 정의 조건형 토픽 + 클러스터링 튜닝) 로드.
    토픽은 전역(무팀 results_rows) 뷰라 설정도 전역(team="")에 영속한다."""
    st = _SV.get_store()
    cfg = (st.get_report("topic_studio") if st else None) or {}
    custom = cfg.get("custom") if isinstance(cfg.get("custom"), list) else []
    settings = cfg.get("settings") if isinstance(cfg.get("settings"), dict) else {}
    exclusions = cfg.get("exclusions") if isinstance(cfg.get("exclusions"), dict) else {}
    return {"custom": custom, "settings": settings, "exclusions": exclusions}


def _save_studio_config(cfg: dict):
    st = _SV.get_store()
    if st:
        st.save_report("topic_studio", {"custom": cfg.get("custom") or [],
                                        "settings": cfg.get("settings") or {},
                                        "exclusions": cfg.get("exclusions") or {}})


def topics_data() -> dict:
    """토픽 모듈 데이터(30s 캐시). 드릴다운 클릭마다 전체 재클러스터링하던 비용 제거 —
    쓰기(추출·스튜디오 변경)는 _agg_bump 로 즉시 무효화된다."""
    return _SV._agg_cached(("topics",), _topics_compute)


def _topics_compute() -> dict:
    """토픽 모듈: 적재된 결과에서 엔티티형·사건형·조건형 토픽 + 사용자 정의 토픽 빌드."""
    rows = _SV.results_rows()
    cfg = _studio_config()
    if not rows:
        return {"n_contents": 0, "single": [], "composite": [], "custom": [],
                "customDefs": cfg["custom"], "settings": cfg["settings"], "exclusions": cfg["exclusions"],
                "catalog": {"intents": [], "cats": [], "keywords": [], "eattrs": []}, "summary": {}}
    from . import topic as TP
    with tempfile.TemporaryDirectory() as d:
        rpath = os.path.join(d, "r.jsonl")
        with open(rpath, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        try:
            out = TP.build_topics(rpath, custom_defs=cfg["custom"], settings=cfg["settings"],
                                  exclusions=cfg["exclusions"], ent_index=_ent_index())
            out["exclusions"] = cfg["exclusions"]
            try:                                     # 추천 카드 원천: 최근 48시간 언급 급증 엔티티(전역)
                st = _SV.get_store()
                out["trending"] = (st.ent_trending(hours=48, limit=8, team="")
                                   if (st and hasattr(st, "ent_trending")) else [])
            except Exception:
                out["trending"] = []
            return out
        except Exception as e:
            return {"error": str(e)[:200], "n_contents": len(rows),
                    "single": [], "composite": [], "custom": [],
                    "customDefs": cfg["custom"], "settings": cfg["settings"],
                    "exclusions": cfg["exclusions"], "summary": {}}


# ── 토픽 자동 리프레시 + 성과 스냅샷 ────────────────────────────────────────
_TOPIC_SNAP_CAP = 90                                  # 보관 스냅샷 수(시간별 약 4일 · 추이 원천)


def _topic_rows_brief(data: dict) -> dict:
    """토픽 데이터 → 스냅샷 요약 {key: {label,type,n}} · 전 체계(엔티티·사건·사용자 정의) 공통 키."""
    out = {}
    for p in (data.get("single") or []) + (data.get("composite") or []):
        k = p.get("cluster_id") or p.get("name") or ""
        if k:
            out[k] = {"label": p.get("name") or "", "type": p.get("type") or "",
                      "n": int(p.get("count") or 0)}
    for g in (data.get("custom") or []):
        if g.get("id"):
            out[g["id"]] = {"label": g.get("name") or "", "type": "custom",
                            "n": int(g.get("core_count") or 0)}
    return out


def topic_snapshot() -> dict:
    """토픽 현황 스냅샷 적재(성과 시계열 기초 · reports kind='topic_snapshots' · 토픽은 무팀 뷰).
    직전 스냅샷 대비 변화(신규·소멸·건수 증감)를 계산해 함께 저장 → /topics 가 배지로 노출."""
    _SV._agg_bump()                                        # 강제 재계산: 열어둔 화면 낡음(수동 새로고침 의존) 해소
    brief = _topic_rows_brief(_SV.topics_data())
    rep = _SV._report_get("topic_snapshots", None, {}) or {}
    entries = rep.get("entries") or []
    prev = ((entries[-1] or {}).get("topics") or {}) if entries else {}
    changed = []
    for k, v in brief.items():
        pn = int((prev.get(k) or {}).get("n") or 0)
        if v["n"] != pn:
            changed.append({"id": k, "label": v["label"], "type": v["type"],
                            "from": pn, "to": v["n"]})
    gone = [{"id": k, "label": (v or {}).get("label") or ""}
            for k, v in prev.items() if k not in brief]
    delta = {"ts": time.time(), "changed": changed[:100], "gone": gone[:50],
             "new_n": sum(1 for c in changed if not c["from"]),
             "changed_n": len(changed), "gone_n": len(gone)}
    entries.append({"ts": delta["ts"], "topics": brief})
    _SV._report_save("topic_snapshots", {"entries": entries[-_TOPIC_SNAP_CAP:],
                                     "last_delta": delta}, None)
    return delta


_topic_sched_started = False


def start_topic_scheduler(interval_min: int = 60):
    """토픽 자동 리프레시(기본 1시간): 재계산 + 스냅샷 적재. 서버당 1회 · 데몬 스레드."""
    global _topic_sched_started
    if _topic_sched_started:
        return
    _topic_sched_started = True

    def _loop():
        while True:
            try:
                time.sleep(max(300, int(interval_min) * 60))
                topic_snapshot()
            except Exception as e:
                print(f"  [warn] 토픽 스냅샷 실패: {e}")

    threading.Thread(target=_loop, daemon=True).start()


def _ent_index() -> dict:
    """토픽 매칭용 개체 속성 인덱스({content_hash: [속성 dict]}) · 사전 미구축이면 빈 dict.
    토픽은 전역(무팀 results_rows) 뷰라 인덱스도 전역(team="")."""
    st = _SV.get_store()
    if not (st and hasattr(st, "ent_attr_index")):
        return {}
    from . import entdict as ED
    return ED.attr_index(st, team="")


def _sanitize_def(d: dict, existing_ids=None) -> dict:
    """사용자 정의 정규화·검증. id 없으면 생성(중복 회피)."""
    from . import topic as TP
    name = (d.get("name") or "").strip()[:60]
    prompt = (d.get("prompt") or "").strip()[:280]

    def _strlist(v, n=20, ln=60):
        out, seen = [], set()
        for x in (v or []):
            s = str(x).strip()[:ln]
            if s and s not in seen:
                seen.add(s); out.append(s)
            if len(out) >= n:
                break
        return out

    cats = _strlist(d.get("cats"))
    intents = _strlist(d.get("intents"))
    keywords = _strlist(d.get("keywords"))
    # 개체 속성 조건: 허용 키('key:value')만 · 항상 필수(같은 개체 AND) · 최대 10개
    from . import entdict as ED
    eattrs = [s for s in _strlist(d.get("eattrs"), n=10) if ED.parse_eattr(s)]
    # 제외(neg): 선택과 독립인 배제 조건. 같은 값이 선택에도 있으면 선택을 우선(자기모순 방지).
    ng = d.get("neg") or {}
    neg = {k: [v for v in _strlist(ng.get(k))
               if v not in {"cats": cats, "intents": intents, "keywords": keywords}[k]]
           for k in ("cats", "intents", "keywords")}
    # 필수(req): 선택된 값의 부분집합만 인정(값 없으면 하위호환으로 topic 이 '전부 필수' 처리)
    rq = d.get("req") or {}
    sel = {"cats": set(cats), "intents": set(intents), "keywords": set(keywords)}
    req = {k: [v for v in _strlist(rq.get(k)) if v in sel[k]] for k in ("cats", "intents", "keywords")}
    cid = (d.get("id") or "").strip()
    if not cid:
        base = "U-" + (TP._slug(name or prompt or "topic") or "topic")
        cid, n = base, 2
        ids = set(existing_ids or [])
        while cid in ids:
            cid = base + "-" + str(n); n += 1
    return {"id": cid, "name": name or "(무제 토픽)", "prompt": prompt,
            "cats": cats, "intents": intents, "keywords": keywords, "eattrs": eattrs,
            "req": req, "neg": neg}


def _studio_llm_suggest(text: str, model: str, rows, svc, mock: bool):
    """자연어 설명 → 토픽 차원(카테고리·인텐트·키워드)을 선택 모델로 매핑.
    허용 목록(현재 데이터의 실재 값)으로만 제약 · 실패 시 (None, 사유) 반환(호출부에서 휴리스틱 폴백)."""
    from . import topic as TP, meta_prompts as MP
    tax = TP.meta_taxonomy()                       # 시스템 전체 아이템메타 분류(데이터 유무 무관)
    cat = TP.studio_catalog(rows, svc)             # 현재 데이터에 실재하는 값(우선)
    data_cats = [c["k"] for c in cat["cats"]]
    data_int = [c["k"] for c in cat["intents"]]
    allow_cats = list(dict.fromkeys((tax["cats"] or []) + data_cats))   # 전체 ∪ 데이터
    allow_int = list(dict.fromkeys((tax["intents"] or []) + data_int))
    tier1_ko = getattr(TP, "_TIER1_KO", {}) or {}
    cats_ko = [((tier1_ko.get(c) or c) + "=" + c) for c in allow_cats]  # 영문 Tier1 + 한글 병기
    # 개체 속성 후보(엔티티 사전 실재값): '여성 스포츠인'류 설명 → eattrs 조건 자동생성
    ecat = TP.eattr_catalog(_ent_index())
    e_allow = [c["k"] for c in ecat]
    e_prompt = [f'{c["k"]} ({c["label"]} · {c["v"]}건)' for c in ecat[:60]]
    # 모델 계열 쿡북 래퍼로 조립(필수/선택 설계자 역할) · 스튜디오 오버라이드 상속
    sysp = MP.topic_suggest_system(model, cats_ko, allow_int, data_cats, data_int, eattrs=e_prompt)
    userp = MP.topic_suggest_user(text)
    llm, route = _SV.llm_for_model(model, mock)
    if llm is None:
        return None, route
    obj, _res = llm.complete_json(sysp, userp, tag="topic_suggest")
    if not isinstance(obj, dict) or obj.get("_fail"):
        return None, (isinstance(obj, dict) and obj.get("_fail_kind")) or "fail"
    ac, ai = set(allow_cats), set(allow_int)
    must, opt = obj.get("must") or {}, obj.get("optional") or {}

    def _cv(dd, key, allow):
        return [v for v in (dd.get(key) or []) if v in allow]

    def _kw(dd):
        return [str(k).strip()[:60] for k in (dd.get("keywords") or []) if str(k).strip()]

    def _uniq(a, b):
        out = list(a)
        for x in b:
            if x not in out:
                out.append(x)
        return out

    m_cats, m_int, m_kw = _cv(must, "cats", ac), _cv(must, "intents", ai), _kw(must)
    cats = _uniq(m_cats, _cv(opt, "cats", ac))
    intents = _uniq(m_int, _cv(opt, "intents", ai))
    keywords = _uniq(m_kw, _kw(opt))[:5]
    # 제외(exclude → neg): 허용 목록으로 검증 · 선택과 겹치면 선택에서 뺀다(배제 의도 우선)
    exc = obj.get("exclude") or {}
    neg = {"cats": _cv(exc, "cats", ac), "intents": _cv(exc, "intents", ai),
           "keywords": _kw(exc)[:5]}
    cats = [x for x in cats if x not in neg["cats"]]
    intents = [x for x in intents if x not in neg["intents"]]
    keywords = [x for x in keywords if x not in neg["keywords"]]
    # 개체 속성(eattrs): 실재 후보 목록으로만 검증 · 항상 필수 취급이라 req 분리 불필요
    ea = set(e_allow)
    eattrs = [str(x).strip() for x in (obj.get("eattrs") or []) if str(x).strip() in ea][:6]
    sug = {"cats": cats, "intents": intents, "keywords": keywords, "eattrs": eattrs,
           "req": {"cats": [c for c in m_cats if c in cats], "intents": [i for i in m_int if i in intents],
                   "keywords": [k for k in m_kw if k in keywords]},
           "neg": neg}
    return sug, route


def _def_signature(d: dict) -> str:
    """토픽 정의 → 비교용 서명 텍스트(이름·설명·조건값 전부)."""
    parts = [d.get("name") or "", d.get("prompt") or ""]
    for k in ("cats", "intents", "keywords"):
        parts.extend(d.get(k) or [])
        parts.extend((d.get("req") or {}).get(k) or [])
    return " ".join(str(p) for p in parts if p).strip()


def _sig_tokens(s: str) -> set:
    return {t for t in re.split(r"[^0-9A-Za-z\uac00-\ud7a3]+", (s or "").lower()) if len(t) >= 2}


def similar_topics(new_def: dict, custom: list, threshold: float = 0.86) -> list:
    """저장하려는 정의와 비슷한 기존 사용자 토픽(중복 경고 후보 · 상위 3).
    임베딩(키 있으면 · embed.py 캐시 재사용) 우선, 무키면 토큰 자카드(임계 0.5) 폴백.
    실패는 조용히 빈 목록 — 저장을 막지 않는다(경고 전용)."""
    sig = _def_signature(new_def)
    others = [c for c in (custom or []) if c.get("id") != new_def.get("id")]
    if not sig or not others:
        return []
    out = []
    try:
        from .embed import EmbeddingClient, cosine
        emb = EmbeddingClient(cache_path=Config.load().emb_cache_path)
        if not emb.mock:                            # mock(휴리스틱) 임베딩으로는 유사도 판단 금지
            qv = emb.embed(sig, is_query=True)
            for c in others:
                s = cosine(qv, emb.embed(_def_signature(c), is_query=False))
                if s >= threshold:
                    out.append({"id": c.get("id"), "name": c.get("name") or "",
                                "score": round(s, 3), "via": "embedding"})
            out.sort(key=lambda x: -x["score"])
            return out[:3]
    except Exception:
        pass
    qt = _sig_tokens(sig)
    if not qt:
        return []
    for c in others:
        ct = _sig_tokens(_def_signature(c))
        if not ct:
            continue
        j = len(qt & ct) / len(qt | ct)
        if j >= 0.5:
            out.append({"id": c.get("id"), "name": c.get("name") or "",
                        "score": round(j, 3), "via": "token"})
    out.sort(key=lambda x: -x["score"])
    return out[:3]


def topic_studio_action(data: dict, mock: bool = False) -> dict:
    """토픽 스튜디오 변경/조회: save·delete·settings·preview·suggest."""
    from . import topic as TP
    action = (data.get("action") or "").strip()
    if action not in ("preview", "suggest"):
        _SV._agg_bump()                                   # 변경성 액션(save·delete·settings·exclude 등) → 토픽 캐시 무효화
    rows = _SV.results_rows()
    svc = TP._service_names(rows) if rows else set()

    if action == "preview":
        d = _sanitize_def(data.get("def") or {})
        pv = (TP.preview_definition(rows, svc, d, ent_index=_ent_index()) if rows else
              {"n_total": 0, "bundles": [], "must_n": 0, "opt_n": 0})
        # 표본을 상세 화면 계약(_detail_row)으로 확장: 미리보기 배지 클릭 → 공통 스플릿뷰로 바로 열람
        for b in pv.get("bundles") or []:
            if b.get("samples"):
                b["samples"] = [_SV._detail_row(rows[s["i"]]) for s in b["samples"]
                                if isinstance(s.get("i"), int) and 0 <= s["i"] < len(rows)]
        return {"ok": True, "preview": pv}

    if action == "suggest":
        text = data.get("text") or ""
        if not rows:
            return {"ok": True, "via": "none",
                    "suggest": {"cats": [], "intents": [], "keywords": [], "eattrs": [],
                                "req": {"cats": [], "intents": [], "keywords": []},
                                "neg": {"cats": [], "intents": [], "keywords": []}}}
        model = (data.get("model") or "").strip()          # "" = 기본 실행 모델
        via, route, sug = "llm", "", None
        try:
            sug, route = _studio_llm_suggest(text, model, rows, svc, mock)
        except Exception as e:
            sug, route = None, str(e)[:80]
        # 모델 호출 불가·실패·빈 결과 → 휴리스틱(즉시·의존성 0) 폴백. 버튼이 헛돌지 않게.
        if not sug or not (sug.get("cats") or sug.get("intents") or sug.get("keywords")
                           or sug.get("eattrs") or any((sug.get("neg") or {}).values())):
            sug = TP.suggest_dims(text, rows, svc, eattr_cands=TP.eattr_catalog(_ent_index()))
            via = "heuristic"
        sug.setdefault("eattrs", [])
        return {"ok": True, "suggest": sug, "via": via, "model": model, "route": route}

    cfg = _studio_config()
    custom = list(cfg["custom"])
    exclusions = {k: list(v or []) for k, v in (cfg["exclusions"] or {}).items()}

    if action == "save":
        if not isinstance(data.get("def"), dict) or not data["def"]:
            # def 누락(키 오타 포함)이 조용히 '(무제 토픽)' 을 만드는 것 방지 — 명시 에러로 반환
            return {"ok": False, "error": "토픽 정의(def)가 필요합니다"}
        d = _sanitize_def(data.get("def") or {}, existing_ids=[c.get("id") for c in custom])
        dups = similar_topics(d, custom)             # 저장 전 기존 정의와 비교(경고 전용 · 저장은 진행)
        idx = next((i for i, c in enumerate(custom) if c.get("id") == d["id"]), -1)
        if idx >= 0:
            custom[idx] = d
        else:
            custom.append(d)
        _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions})
        out = dict(_SV.topics_data())
        out["similar"] = dups
        return out
    elif action == "delete":
        cid = (data.get("id") or "").strip()
        custom = [c for c in custom if c.get("id") != cid]
        exclusions.pop(cid, None)               # 토픽 삭제 시 그 토픽의 제외 목록도 정리
        _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions})
    elif action == "settings":
        s = data.get("settings") or {}
        settings = dict(cfg["settings"])
        if s.get("co_min") is not None:
            settings["co_min"] = max(1, min(6, int(s.get("co_min") or 2)))
        if s.get("entity_min") is not None:
            settings["entity_min"] = max(1, min(10, int(s.get("entity_min") or 2)))
        _save_studio_config({"custom": custom, "settings": settings, "exclusions": exclusions})
    elif action in ("exclude", "restore"):
        # 큐레이션 오버레이: 토픽(자동=cluster_id · 사용자=그룹 id)에서 콘텐츠(hash) 개별 제외/복구.
        # 매칭 정의는 그대로 두는 편집 판단 — 메타 교정(검수)·정의 수정과 구분되는 세 번째 수단.
        tid = (data.get("id") or "").strip()[:80]
        h = (data.get("hash") or "").strip()[:80]
        if not tid or not h:
            return {"ok": False, "error": "토픽 id 와 콘텐츠 hash 가 필요합니다"}
        lst = [e for e in (exclusions.get(tid) or [])
               if (e.get("h") if isinstance(e, dict) else e) != h]
        if action == "exclude":
            lst.append({"h": h, "title": str(data.get("title") or "")[:80],
                        "topic": str(data.get("topic") or "")[:60], "ts": time.time()})
            lst = lst[-300:]                     # 토픽당 상한(폭주 방지)
        if lst:
            exclusions[tid] = lst
        else:
            exclusions.pop(tid, None)
        _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions})
    else:
        return {"ok": False, "error": "알 수 없는 동작"}
    return _SV.topics_data()



def topic_personas(entities: list, team=None) -> list:
    """엔티티 목록 → 타겟 페르소나 추천(상위 2 · 점유율). 엔티티×페르소나 친화도 행렬
    (usermeta · '타겟팅 실계산 근거'로 이미 산출)을 소비 측으로 개통 — 데이터 없으면 빈 목록."""
    try:
        um = _SV.usermeta_data(team=team) or {}
        ep = (um.get("aggregate") or {}).get("entity_persona") or {}
    except Exception:
        return []
    pnames = ep.get("personas") or []
    weights = {r[0]: r[2] for r in (ep.get("rows") or [])
               if isinstance(r, (list, tuple)) and len(r) >= 3}
    if not (pnames and weights and entities):
        return []
    totals = [0.0] * len(pnames)
    hit = False
    for e in entities:
        w = weights.get(e)
        if w:
            hit = True
            for i, v in enumerate(w[:len(pnames)]):
                totals[i] += float(v or 0)
    s = sum(totals)
    if not hit or s <= 0:
        return []
    ranked = sorted(zip(pnames, totals), key=lambda x: -x[1])
    return [{"persona": p, "share": round(v / s, 3)} for p, v in ranked[:2] if v > 0]


def topic_drill(cluster_id: str, team=None, reviewer: str = "") -> dict:
    """토픽 드릴다운: 해당 토픽(클러스터)에 묶인 콘텐츠 목록. 배치 결과 드릴다운과 동일 shape.
    ⚠️ rows 는 _SV.topics_data() 의 content_ids 인덱스와 정합해야 해서 무필터 유지 · 피드백 부착만
    팀 스코프. 토픽 자체의 팀 파라미터화(topics_data)는 후속(실험실 메뉴 · 관리자용)."""
    rows = _SV.results_rows()
    if not rows or not cluster_id:
        return {"ok": True, "kind": "topic", "value": cluster_id or "", "items": [], "n": 0}
    td = _SV.topics_data()                        # single/composite(각 content_ids) · custom(그룹→bundles)
    cluster, topic_id = None, cluster_id      # topic_id = 제외(큐레이션) 키 · 사용자 토픽은 그룹 id
    for grp in ("single", "composite"):
        for c in td.get(grp, []):
            if c.get("cluster_id") == cluster_id:
                cluster = c
                break
        if cluster:
            break
    if not cluster:                            # 사용자 토픽: 그룹의 묶음(핵심·관련) 중에서 찾음
        for g in td.get("custom", []):
            for b in (g.get("bundles") or []):
                if b.get("cluster_id") == cluster_id:
                    cluster = dict(b)
                    cluster["name"] = (g.get("name") or "") + " · " + (b.get("label") or "")
                    topic_id = g.get("id") or cluster_id   # 제외는 그룹 전체(모든 묶음)에 적용
                    break
            if cluster:
                break
    if not cluster:
        return {"ok": False, "kind": "topic", "value": cluster_id, "items": [], "n": 0,
                "error": "토픽을 찾을 수 없습니다(데이터가 갱신되었을 수 있음)"}
    ids = cluster.get("content_ids") or []
    out = _SV._attach_fb([_SV._detail_row(rows[i]) for i in ids if 0 <= i < len(rows)], team, reviewer)
    name = cluster.get("name") or cluster.get("label") or cluster_id
    ents = {}                                  # 타겟 페르소나: 이 토픽 콘텐츠의 빈발 엔티티로 추정
    for i in ids[:100]:
        if 0 <= i < len(rows):
            for e in ((rows[i].get("item_meta") or {}).get("entities") or []):
                ents[e] = ents.get(e, 0) + 1
    top_ents = [e for e, _n in sorted(ents.items(), key=lambda x: -x[1])[:8]]
    return {"ok": True, "kind": "topic", "value": name, "items": out, "n": len(out),
            "topic_id": topic_id, "personas": topic_personas(top_ents, team=team)}
