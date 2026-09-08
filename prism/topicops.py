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
    paused_auto = cfg.get("paused_auto") if isinstance(cfg.get("paused_auto"), list) else []
    return {"custom": custom, "settings": settings, "exclusions": exclusions, "paused_auto": paused_auto}


def _save_studio_config(cfg: dict):
    st = _SV.get_store()
    if st:
        st.save_report("topic_studio", {"custom": cfg.get("custom") or [],
                                        "settings": cfg.get("settings") or {},
                                        "exclusions": cfg.get("exclusions") or {},
                                        "paused_auto": cfg.get("paused_auto") or []})


TOPIC_STATUS = ("active", "paused", "draft", "archived")      # 운영자 상태 · 기간 0건 자동 비활성(스냅샷)과 별개
_LOG_CAP = 30


def _log_add(d: dict, what: str, who: str = ""):
    d["log"] = ((d.get("log") or [])[-(_LOG_CAP - 1):]) + [{"ts": time.time(), "who": (who or "")[:80], "what": what[:120]}]


def _row_stats(ids, rows, now=None):
    """묶인 콘텐츠 인덱스 → 오늘 · 7일 · 지난 7일 건수와 신호(정체 N일 · 급감). 적재 시각(_ts) 기준 · 시각 없는 행은 집계 제외."""
    now = now or time.time()
    ts = [float(rows[i].get("_ts") or 0) for i in ids if 0 <= i < len(rows)]
    ts = [t for t in ts if t > 0]
    today = sum(1 for t in ts if t >= now - 86400)
    d7 = sum(1 for t in ts if t >= now - 7 * 86400)
    prev7 = sum(1 for t in ts if now - 14 * 86400 <= t < now - 7 * 86400)
    last = max(ts) if ts else 0
    stall = int((now - last) // 86400) if last else 0
    signal = ""
    if prev7 >= 20 and d7 < prev7 * 0.5:
        signal = "급감"
    elif stall >= 3:
        signal = "정체 %d일" % stall
    return {"today": today, "d7": d7, "prev7": prev7, "stall_days": stall, "signal": signal}


def topics_data(team=None) -> dict:
    """토픽 모듈 데이터(팀별 30s 캐시). 드릴다운 클릭마다 전체 재클러스터링하던 비용 제거 —
    쓰기(추출·스튜디오 변경)는 _agg_bump 로 즉시 무효화된다.
    team 은 결과 행(results_rows)의 스코프다 — 넘기지 않으면 supabase 에서 팀 필터가 생략돼
    응답 titles 에 전 팀 콘텐츠 제목이 실린다(형제 라우트는 전부 팀을 넘긴다)."""
    return _SV._agg_cached(("topics", team), lambda: _topics_compute(team))


def _topics_compute(team=None) -> dict:
    """토픽 모듈: 적재된 결과에서 엔티티형·사건형·조건형 토픽 + 사용자 정의 토픽 빌드."""
    rows = _SV.results_rows(team=team)
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
            # 초안 · 보관은 매칭하지 않는다(정의만 보존) · 일시정지는 매칭·건수 유지(유통만 멈춤 · 소비처가 status 로 거른다)
            live = [d for d in cfg["custom"] if (d.get("status") or "active") in ("active", "paused")]
            out = TP.build_topics(rpath, custom_defs=live, settings=cfg["settings"],
                                  exclusions=cfg["exclusions"], ent_index=_ent_index())
            out["exclusions"] = cfg["exclusions"]
            _attach_status(out, cfg, rows)
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


def _attach_status(out: dict, cfg: dict, rows: list):
    """토픽 행에 운영자 상태 · 변경 기록 · 오늘/7일/신호를 붙이고, 초안 · 보관 정의는 건수 없는 행으로 덧붙인다."""
    from . import topic as TP
    defs = {d.get("id"): d for d in cfg["custom"]}
    paused = set(cfg.get("paused_auto") or [])
    now = time.time()
    for g in out.get("custom") or []:
        d = defs.get(g.get("id")) or {}
        g["status"] = d.get("status") or "active"
        g["log"] = (d.get("log") or [])[-3:]
        g["feed_chips"] = TP.feed_labels(d.get("feed"))
        core = next((b.get("content_ids") or [] for b in (g.get("bundles") or []) if b.get("kind") == "core"), [])
        g.update(_row_stats(core, rows, now))
    seen = {g.get("id") for g in out.get("custom") or []}
    for d in cfg["custom"]:
        if d.get("id") in seen or (d.get("status") or "active") in ("active", "paused"):
            continue
        out.setdefault("custom", []).append({
            "id": d.get("id"), "type": "custom", "origin": "user", "name": d.get("name") or "(무제 토픽)",
            "prompt": d.get("prompt") or "", "must": [], "opt": [],
            "neg": [{"dim": k, "v": v} for k in TP._DIMS for v in ((d.get("neg") or {}).get(k) or [])],
            "bundles": [], "n_bundles": 0, "core_count": 0, "status": d.get("status"),
            "log": (d.get("log") or [])[-3:], "feed_chips": TP.feed_labels(d.get("feed")),
            "today": 0, "d7": 0, "prev7": 0, "stall_days": 0, "signal": ""})
    for key in ("single", "composite"):
        for t in out.get(key) or []:
            t["status"] = "paused" if t.get("cluster_id") in paused else "active"
            t.update(_row_stats(t.get("content_ids") or [], rows, now))


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


def topic_snapshot(team=None) -> dict:
    """토픽 현황 스냅샷 적재(성과 시계열 기초 · reports kind='topic_snapshots').
    직전 스냅샷 대비 변화(신규·소멸·건수 증감)를 계산해 함께 저장 → /topics 가 배지로 노출.
    ⚠️ 계산·적재·조회가 같은 team 버킷이어야 한다 — 전역으로 적재하면 /topics(팀 스코프)가
    영영 빈 배지를 보거나, 반대로 전 팀 콘텐츠에서 파생된 토픽 라벨(last_delta)이 새어 나간다."""
    _SV._agg_bump()                                        # 강제 재계산: 열어둔 화면 낡음(수동 새로고침 의존) 해소
    brief = _topic_rows_brief(_SV.topics_data(team))
    rep = _SV._report_get("topic_snapshots", team, {}) or {}
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
                                     "last_delta": delta}, team)
    return delta


_TOPIC_SNAP_TEAM_CAP = 50                             # 한 주기에 스냅샷을 뜨는 팀 수 상한


def snapshot_teams() -> list:
    """스냅샷 대상 팀 목록. sqlite(로컬 단일 팀)는 [None] · supabase 는 teams 목록(1왕복).
    상한을 넘기면 잘렸다는 사실을 로그로 남긴다(조용히 자르면 뒤쪽 팀 배지가 이유 없이 빈다)."""
    st = _SV.get_store()
    if not (st and hasattr(st, "team_ids")):
        return [None]
    try:
        teams = list(st.team_ids(_TOPIC_SNAP_TEAM_CAP + 1) or [])
    except Exception as e:
        print(f"  [warn] 팀 목록 조회 실패 · 이번 주기 토픽 스냅샷 건너뜀: {e}")
        return []
    if len(teams) > _TOPIC_SNAP_TEAM_CAP:
        print(f"  [warn] 팀 {len(teams)}개 중 {_TOPIC_SNAP_TEAM_CAP}개만 토픽 스냅샷 적재 "
              f"(상한 _TOPIC_SNAP_TEAM_CAP) · 나머지 팀은 이번 주기 배지가 갱신되지 않는다")
        teams = teams[:_TOPIC_SNAP_TEAM_CAP]
    return teams


def topic_snapshot_all() -> int:
    """팀별 스냅샷 1주기. 한 팀에서 터져도 나머지 팀은 계속 돈다. 반환 = 적재 성공 팀 수."""
    done = 0
    for t in snapshot_teams():
        try:
            topic_snapshot(t)
            done += 1
        except Exception as e:
            print(f"  [warn] 토픽 스냅샷 실패(team={t or '-'}): {e}")
    return done


_topic_sched_started = False


def start_topic_scheduler(interval_min: int = 60):
    """토픽 자동 리프레시(기본 1시간): 재계산 + 팀별 스냅샷 적재. 서버당 1회 · 데몬 스레드."""
    global _topic_sched_started
    if _topic_sched_started:
        return
    _topic_sched_started = True

    def _loop():
        while True:
            try:
                time.sleep(max(300, int(interval_min) * 60))
                topic_snapshot_all()
            except Exception as e:
                print(f"  [warn] 토픽 스냅샷 주기 실패: {e}")

    threading.Thread(target=_loop, daemon=True).start()


def _ent_index() -> dict:
    """토픽 매칭용 개체 속성 인덱스({content_hash: [속성 dict]}) · 사전 미구축이면 빈 dict.
    개체 사전은 전역이라 인덱스도 전역(team="").
    30s 집계 캐시: 원천(supastore.ent_attr_index)이 entities 2만행 + content_entities 5만행을
    필터 없이 통째로 내려받는데, 스튜디오 미리보기·제안은 타이핑 디바운스(260ms)마다 이걸
    호출한다 — 캐시가 붙는 _topics_compute 와 달리 미리보기 경로만 무캐시로 비대칭이었다.
    등재·수정·보강(dictops.entdict_action)은 _agg_bump 로 즉시 무효화한다."""
    st = _SV.get_store()
    if not (st and hasattr(st, "ent_attr_index")):
        return {}
    from . import entdict as ED
    return _SV._agg_cached(("entidx", ""), lambda: ED.attr_index(st, team=""))


def _sanitize_def(d: dict, existing_ids=None) -> dict:
    """사용자 정의 정규화·검증. id 없으면 생성(중복 회피)."""
    from . import topic as TP
    name = (d.get("name") or "").strip()[:60]
    prompt = (d.get("prompt") or "").strip()[:600]           # 말로 만들기: 문장 여러 개를 ' / ' 로 이어 보존

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
    srcs = _strlist(d.get("srcs"))                          # 출처 축: 서비스 · 매체명
    # 개체 속성 조건: 허용 키('key:value')만 · 항상 필수(같은 개체 AND) · 최대 10개
    from . import entdict as ED
    eattrs = [s for s in _strlist(d.get("eattrs"), n=10) if ED.parse_eattr(s)]
    # 제외(neg): 선택과 독립인 배제 조건. 같은 값이 선택에도 있으면 선택을 우선(자기모순 방지).
    ng = d.get("neg") or {}
    neg = {k: [v for v in _strlist(ng.get(k))
               if v not in {"cats": cats, "intents": intents, "keywords": keywords, "srcs": srcs}[k]]
           for k in TP._DIMS}
    # 필수(req): 선택된 값의 부분집합만 인정(값 없으면 하위호환으로 topic 이 '전부 필수' 처리)
    rq = d.get("req") or {}
    sel = {"cats": set(cats), "intents": set(intents), "keywords": set(keywords), "srcs": set(srcs)}
    req = {k: [v for v in _strlist(rq.get(k)) if v in sel[k]] for k in TP._DIMS}
    status = d.get("status") if d.get("status") in TOPIC_STATUS else "active"
    feed = TP.sanitize_feed(d.get("feed"))
    cid = (d.get("id") or "").strip()
    if not cid:
        base = "U-" + (TP._slug(name or prompt or "topic") or "topic")
        cid, n = base, 2
        ids = set(existing_ids or [])
        while cid in ids:
            cid = base + "-" + str(n); n += 1
    return {"id": cid, "name": name or "(무제 토픽)", "prompt": prompt,
            "cats": cats, "intents": intents, "keywords": keywords, "srcs": srcs, "eattrs": eattrs,
            "req": req, "neg": neg, "feed": feed, "status": status,
            "talk_model": (d.get("talk_model") or "").strip()[:80]}


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
    allow_src = [c["k"] for c in (cat.get("srcs") or [])]
    # 모델 계열 쿡북 래퍼로 조립(필수/선택 설계자 역할) · 스튜디오 오버라이드 상속
    sysp = MP.topic_suggest_system(model, cats_ko, allow_int, data_cats, data_int, eattrs=e_prompt, srcs=allow_src)
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
    # 출처(srcs): 데이터 실재 출처만(대소문자 무시) · 제외가 우선
    smap = {s.lower(): s for s in allow_src}
    srcs = [smap[str(x).lower()] for x in (obj.get("srcs") or []) if str(x).lower() in smap]
    neg["srcs"] = [smap[str(x).lower()] for x in (obj.get("neg_srcs") or []) if str(x).lower() in smap]
    srcs = [x for x in dict.fromkeys(srcs) if x not in neg["srcs"]]
    if not srcs and not neg["srcs"]:                       # 모델이 출처를 비우면 문장 휴리스틱(사전 실재값)으로 보강
        srcs, neg["srcs"] = TP.parse_srcs_text(text, cat)
    # 원천 조건(feed): 허용 값만 · 모델이 비우면 문장 휴리스틱으로 보강
    feed = TP.sanitize_feed(obj.get("feed")) or TP.parse_feed_text(text, cat)
    sug = {"cats": cats, "intents": intents, "keywords": keywords, "srcs": srcs, "eattrs": eattrs,
           "req": {"cats": [c for c in m_cats if c in cats], "intents": [i for i in m_int if i in intents],
                   "keywords": [k for k in m_kw if k in keywords], "srcs": []},
           "neg": neg, "feed": feed}
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


def topic_studio_action(data: dict, mock: bool = False, team=None, who: str = "") -> dict:
    """토픽 스튜디오 변경/조회: save·delete·settings·preview·suggest.
    rows·topics_data 는 topics_data(team) 인덱스와 정합해야 하므로 같은 team 으로 통일한다."""
    from . import topic as TP
    action = (data.get("action") or "").strip()
    if action not in ("preview", "suggest"):
        _SV._agg_bump()                                   # 변경성 액션(save·delete·settings·exclude 등) → 토픽 캐시 무효화
    rows = _SV.results_rows(team=team)
    svc = TP._service_names(rows) if rows else set()

    if action == "preview":
        d = _sanitize_def(data.get("def") or {})
        # 개체 속성 조건이 없으면 인덱스 자체가 필요 없다(_content_dims 가 ent_index=None 이면
        # 빈 속성 목록을 쓴다) — 타이핑 중 대부분의 미리보기가 사전 조회를 아예 건너뛴다.
        eidx = _ent_index() if d.get("eattrs") else None
        pv = (TP.preview_definition(rows, svc, d, ent_index=eidx) if rows else
              {"n_total": 0, "bundles": [], "must_n": 0, "opt_n": 0})
        # 표본을 상세 화면 계약(_detail_row)으로 확장: 미리보기 배지 클릭 → 공통 스플릿뷰로 바로 열람
        for b in pv.get("bundles") or []:
            if b.get("samples"):
                b["samples"] = [_SV._detail_row(rows[s["i"]]) for s in b["samples"]
                                if isinstance(s.get("i"), int) and 0 <= s["i"] < len(rows)]
        pv["feed_chips"] = TP.feed_labels(TP.sanitize_feed((data.get("def") or {}).get("feed")))
        return {"ok": True, "preview": pv}

    if action == "suggest":
        text = data.get("text") or ""
        if not rows:
            return {"ok": True, "via": "none",
                    "suggest": {"cats": [], "intents": [], "keywords": [], "srcs": [], "eattrs": [],
                                "req": {"cats": [], "intents": [], "keywords": [], "srcs": []},
                                "neg": {"cats": [], "intents": [], "keywords": [], "srcs": []},
                                "feed": TP.parse_feed_text(text)}}
        model = (data.get("model") or "").strip()          # "" = 기본 실행 모델
        via, route, sug = "llm", "", None
        try:
            sug, route = _studio_llm_suggest(text, model, rows, svc, mock)
        except Exception as e:
            sug, route = None, str(e)[:80]
        # 모델 호출 불가·실패·빈 결과 → 휴리스틱(즉시·의존성 0) 폴백. 버튼이 헛돌지 않게.
        core_empty = not sug or not (sug.get("cats") or sug.get("intents") or sug.get("keywords") or sug.get("eattrs")
                                     or any(v for k, v in (sug.get("neg") or {}).items() if k != "srcs"))
        if core_empty:                               # 메타 축이 비면 휴리스틱 · 모델이 준 출처 · 원천 조건은 살린다
            h = TP.suggest_dims(text, rows, svc, eattr_cands=TP.eattr_catalog(_ent_index()))
            if sug:
                h["srcs"] = sug.get("srcs") or h["srcs"]
                h["neg"]["srcs"] = (sug.get("neg") or {}).get("srcs") or h["neg"]["srcs"]
                h["feed"] = sug.get("feed") or h["feed"]
            sug, via = h, "heuristic"
        sug.setdefault("eattrs", []); sug.setdefault("srcs", []); sug.setdefault("feed", {})
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
        prev = custom[idx] if idx >= 0 else {}
        d["log"] = list(prev.get("log") or [])
        # 활성 잠금: 지금 데이터에 0건이면 활성으로 저장하지 않는다(초안) · 일시정지·보관 요청은 그대로
        locked = False
        if d["status"] == "active" and rows:
            pv = TP.preview_definition(rows, svc, d, sample=0, ent_index=_ent_index())
            if not any(b.get("count") for b in pv["bundles"] if b.get("kind") == "core"):
                d["status"], locked = "draft", True
        _log_add(d, ("수정" if idx >= 0 else "만듦") + (" · 말로" if data.get("talk") else "")
                 + (" · 0건이라 초안" if locked else "") + (" · " + d["status"] if d["status"] != "active" else ""), who)
        if idx >= 0:
            custom[idx] = d
        else:
            custom.append(d)
        _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions,
                             "paused_auto": cfg["paused_auto"]})
        out = dict(_SV.topics_data(team))
        out["similar"] = dups
        out["saved"] = {"id": d["id"], "status": d["status"], "locked": locked}
        return out
    elif action == "status":
        # 운영자 스위치: 활성 ↔ 일시정지 · 초안 → 활성(0건이면 잠금) · 보관 ↔ 복구. 자동 토픽은 cluster_id 로 일시정지를 기억
        cid, st = (data.get("id") or "").strip(), (data.get("status") or "").strip()
        if not cid or st not in TOPIC_STATUS:
            return {"ok": False, "error": "토픽 id 와 상태(active · paused · draft · archived)가 필요합니다"}
        idx = next((i for i, c in enumerate(custom) if c.get("id") == cid), -1)
        paused_auto = [x for x in cfg["paused_auto"] if x != cid]
        if idx < 0:
            if st == "paused":
                paused_auto.append(cid)
            _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions,
                                 "paused_auto": paused_auto})
            return dict(_SV.topics_data(team), ok=True, saved={"id": cid, "status": st, "locked": False})
        d = dict(custom[idx])
        locked = False
        if st == "active" and rows:
            pv = TP.preview_definition(rows, svc, d, sample=0, ent_index=_ent_index())
            if not any(b.get("count") for b in pv["bundles"] if b.get("kind") == "core"):
                st, locked = "draft", True
        _log_add(d, "상태 " + (d.get("status") or "active") + " → " + st + (" · 0건이라 잠금" if locked else ""), who)
        d["status"] = st
        custom[idx] = d
        _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions,
                             "paused_auto": paused_auto})
        return dict(_SV.topics_data(team), ok=True, saved={"id": cid, "status": st, "locked": locked})
    elif action == "delete":
        cid = (data.get("id") or "").strip()
        custom = [c for c in custom if c.get("id") != cid]
        exclusions.pop(cid, None)               # 토픽 삭제 시 그 토픽의 제외 목록도 정리
        _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions,
                             "paused_auto": cfg["paused_auto"]})
    elif action == "settings":
        s = data.get("settings") or {}
        settings = dict(cfg["settings"])
        if s.get("co_min") is not None:
            settings["co_min"] = max(1, min(6, int(s.get("co_min") or 2)))
        if s.get("entity_min") is not None:
            settings["entity_min"] = max(1, min(10, int(s.get("entity_min") or 2)))
        _save_studio_config({"custom": custom, "settings": settings, "exclusions": exclusions,
                             "paused_auto": cfg["paused_auto"]})
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
        _save_studio_config({"custom": custom, "settings": cfg["settings"], "exclusions": exclusions,
                             "paused_auto": cfg["paused_auto"]})
    else:
        return {"ok": False, "error": "알 수 없는 동작"}
    return _SV.topics_data(team)



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
    rows 는 _SV.topics_data(team) 의 content_ids 인덱스와 정합해야 하므로 같은 team 으로 뽑는다
    (예전엔 둘 다 무필터라 팀원이 타 팀 콘텐츠를 드릴다운으로 열 수 있었다)."""
    rows = _SV.results_rows(team=team)
    if not rows or not cluster_id:
        return {"ok": True, "kind": "topic", "value": cluster_id or "", "items": [], "n": 0}
    td = _SV.topics_data(team)                    # single/composite(각 content_ids) · custom(그룹→bundles)
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
