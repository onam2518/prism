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
    """\ud1a0\ud53d \uc2a4\ud29c\ub514\uc624 \uc124\uc815(\uc0ac\uc6a9\uc790 \uc815\uc758 \uc870\uac74\ud615 \ud1a0\ud53d + \ud074\ub7ec\uc2a4\ud130\ub9c1 \ud29c\ub2dd) \ub85c\ub4dc.
    \ud1a0\ud53d\uc740 \uc804\uc5ed(\ubb34\ud300 results_rows) \ubdf0\ub77c \uc124\uc815\ub3c4 \uc804\uc5ed(team="")\uc5d0 \uc601\uc18d\ud55c\ub2e4."""
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
    """\ud1a0\ud53d \ubaa8\ub4c8 \ub370\uc774\ud130(30s \uce90\uc2dc). \ub4dc\ub9b4\ub2e4\uc6b4 \ud074\ub9ad\ub9c8\ub2e4 \uc804\uccb4 \uc7ac\ud074\ub7ec\uc2a4\ud130\ub9c1\ud558\ub358 \ube44\uc6a9 \uc81c\uac70 \u2014
    \uc4f0\uae30(\ucd94\ucd9c\u00b7\uc2a4\ud29c\ub514\uc624 \ubcc0\uacbd)\ub294 _agg_bump \ub85c \uc989\uc2dc \ubb34\ud6a8\ud654\ub41c\ub2e4."""
    return _SV._agg_cached(("topics",), _topics_compute)


def _topics_compute() -> dict:
    """\ud1a0\ud53d \ubaa8\ub4c8: \uc801\uc7ac\ub41c \uacb0\uacfc\uc5d0\uc11c \uc5d4\ud2f0\ud2f0\ud615\u00b7\uc0ac\uac74\ud615\u00b7\uc870\uac74\ud615 \ud1a0\ud53d + \uc0ac\uc6a9\uc790 \uc815\uc758 \ud1a0\ud53d \ube4c\ub4dc."""
    rows = _SV.results_rows()
    cfg = _studio_config()
    if not rows:
        return {"n_contents": 0, "single": [], "composite": [], "filter": [], "custom": [],
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
                    "single": [], "composite": [], "filter": [], "custom": [],
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
    """\uc0ac\uc6a9\uc790 \uc815\uc758 \uc815\uaddc\ud654\u00b7\uac80\uc99d. id \uc5c6\uc73c\uba74 \uc0dd\uc131(\uc911\ubcf5 \ud68c\ud53c)."""
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
    # \ud544\uc218(req): \uc120\ud0dd\ub41c \uac12\uc758 \ubd80\ubd84\uc9d1\ud569\ub9cc \uc778\uc815(\uac12 \uc5c6\uc73c\uba74 \ud558\uc704\ud638\ud658\uc73c\ub85c topic \uc774 '\uc804\ubd80 \ud544\uc218' \ucc98\ub9ac)
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
    return {"id": cid, "name": name or "(\ubb34\uc81c \ud1a0\ud53d)", "prompt": prompt,
            "cats": cats, "intents": intents, "keywords": keywords, "eattrs": eattrs,
            "req": req, "neg": neg}


def _studio_llm_suggest(text: str, model: str, rows, svc, mock: bool):
    """\uc790\uc5f0\uc5b4 \uc124\uba85 \u2192 \ud1a0\ud53d \ucc28\uc6d0(\uce74\ud14c\uace0\ub9ac\u00b7\uc778\ud150\ud2b8\u00b7\ud0a4\uc6cc\ub4dc)\uc744 \uc120\ud0dd \ubaa8\ub378\ub85c \ub9e4\ud551.
    \ud5c8\uc6a9 \ubaa9\ub85d(\ud604\uc7ac \ub370\uc774\ud130\uc758 \uc2e4\uc7ac \uac12)\uc73c\ub85c\ub9cc \uc81c\uc57d \u00b7 \uc2e4\ud328 \uc2dc (None, \uc0ac\uc720) \ubc18\ud658(\ud638\ucd9c\ubd80\uc5d0\uc11c \ud734\ub9ac\uc2a4\ud2f1 \ud3f4\ubc31)."""
    from . import topic as TP, meta_prompts as MP
    tax = TP.meta_taxonomy()                       # \uc2dc\uc2a4\ud15c \uc804\uccb4 \uc544\uc774\ud15c\uba54\ud0c0 \ubd84\ub958(\ub370\uc774\ud130 \uc720\ubb34 \ubb34\uad00)
    cat = TP.studio_catalog(rows, svc)             # \ud604\uc7ac \ub370\uc774\ud130\uc5d0 \uc2e4\uc7ac\ud558\ub294 \uac12(\uc6b0\uc120)
    data_cats = [c["k"] for c in cat["cats"]]
    data_int = [c["k"] for c in cat["intents"]]
    allow_cats = list(dict.fromkeys((tax["cats"] or []) + data_cats))   # \uc804\uccb4 \u222a \ub370\uc774\ud130
    allow_int = list(dict.fromkeys((tax["intents"] or []) + data_int))
    tier1_ko = getattr(TP, "_TIER1_KO", {}) or {}
    cats_ko = [((tier1_ko.get(c) or c) + "=" + c) for c in allow_cats]  # \uc601\ubb38 Tier1 + \ud55c\uae00 \ubcd1\uae30
    # \uac1c\uccb4 \uc18d\uc131 \ud6c4\ubcf4(\uc5d4\ud2f0\ud2f0 \uc0ac\uc804 \uc2e4\uc7ac\uac12): '\uc5ec\uc131 \uc2a4\ud3ec\uce20\uc778'\ub958 \uc124\uba85 \u2192 eattrs \uc870\uac74 \uc790\ub3d9\uc0dd\uc131
    ecat = TP.eattr_catalog(_ent_index())
    e_allow = [c["k"] for c in ecat]
    e_prompt = [f'{c["k"]} ({c["label"]} \u00b7 {c["v"]}\uac74)' for c in ecat[:60]]
    # \ubaa8\ub378 \uacc4\uc5f4 \ucfe1\ubd81 \ub798\ud37c\ub85c \uc870\ub9bd(\ud544\uc218/\uc120\ud0dd \uc124\uacc4\uc790 \uc5ed\ud560) \u00b7 \uc2a4\ud29c\ub514\uc624 \uc624\ubc84\ub77c\uc774\ub4dc \uc0c1\uc18d
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
    """\ud1a0\ud53d \uc815\uc758 \u2192 \ube44\uad50\uc6a9 \uc11c\uba85 \ud14d\uc2a4\ud2b8(\uc774\ub984\u00b7\uc124\uba85\u00b7\uc870\uac74\uac12 \uc804\ubd80)."""
    parts = [d.get("name") or "", d.get("prompt") or ""]
    for k in ("cats", "intents", "keywords"):
        parts.extend(d.get(k) or [])
        parts.extend((d.get("req") or {}).get(k) or [])
    return " ".join(str(p) for p in parts if p).strip()


def _sig_tokens(s: str) -> set:
    return {t for t in re.split(r"[^0-9A-Za-z\uac00-\ud7a3]+", (s or "").lower()) if len(t) >= 2}


def similar_topics(new_def: dict, custom: list, threshold: float = 0.86) -> list:
    """\uc800\uc7a5\ud558\ub824\ub294 \uc815\uc758\uc640 \ube44\uc2b7\ud55c \uae30\uc874 \uc0ac\uc6a9\uc790 \ud1a0\ud53d(\uc911\ubcf5 \uacbd\uace0 \ud6c4\ubcf4 \u00b7 \uc0c1\uc704 3).
    \uc784\ubca0\ub529(\ud0a4 \uc788\uc73c\uba74 \u00b7 embed.py \uce90\uc2dc \uc7ac\uc0ac\uc6a9) \uc6b0\uc120, \ubb34\ud0a4\uba74 \ud1a0\ud070 \uc790\uce74\ub4dc(\uc784\uacc4 0.5) \ud3f4\ubc31.
    \uc2e4\ud328\ub294 \uc870\uc6a9\ud788 \ube48 \ubaa9\ub85d \u2014 \uc800\uc7a5\uc744 \ub9c9\uc9c0 \uc54a\ub294\ub2e4(\uacbd\uace0 \uc804\uc6a9)."""
    sig = _def_signature(new_def)
    others = [c for c in (custom or []) if c.get("id") != new_def.get("id")]
    if not sig or not others:
        return []
    out = []
    try:
        from .embed import EmbeddingClient, cosine
        emb = EmbeddingClient(cache_path=Config.load().emb_cache_path)
        if not emb.mock:                            # mock(\ud734\ub9ac\uc2a4\ud2f1) \uc784\ubca0\ub529\uc73c\ub85c\ub294 \uc720\uc0ac\ub3c4 \ud310\ub2e8 \uae08\uc9c0
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
    """\ud1a0\ud53d \uc2a4\ud29c\ub514\uc624 \ubcc0\uacbd/\uc870\ud68c: save\u00b7delete\u00b7settings\u00b7preview\u00b7suggest."""
    from . import topic as TP
    action = (data.get("action") or "").strip()
    if action not in ("preview", "suggest"):
        _SV._agg_bump()                                   # \ubcc0\uacbd\uc131 \uc561\uc158(save\u00b7delete\u00b7settings\u00b7exclude \ub4f1) \u2192 \ud1a0\ud53d \uce90\uc2dc \ubb34\ud6a8\ud654
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
        model = (data.get("model") or "").strip()          # "" = \uae30\ubcf8 \uc2e4\ud589 \ubaa8\ub378
        via, route, sug = "llm", "", None
        try:
            sug, route = _studio_llm_suggest(text, model, rows, svc, mock)
        except Exception as e:
            sug, route = None, str(e)[:80]
        # \ubaa8\ub378 \ud638\ucd9c \ubd88\uac00\u00b7\uc2e4\ud328\u00b7\ube48 \uacb0\uacfc \u2192 \ud734\ub9ac\uc2a4\ud2f1(\uc989\uc2dc\u00b7\uc758\uc874\uc131 0) \ud3f4\ubc31. \ubc84\ud2bc\uc774 \ud5db\ub3cc\uc9c0 \uc54a\uac8c.
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
        return {"ok": False, "error": "\uc54c \uc218 \uc5c6\ub294 \ub3d9\uc791"}
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
    td = _SV.topics_data()                        # single/composite/filter(각 content_ids) · custom(그룹→bundles)
    cluster, topic_id = None, cluster_id      # topic_id = 제외(큐레이션) 키 · 사용자 토픽은 그룹 id
    for grp in ("single", "composite", "filter"):
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
