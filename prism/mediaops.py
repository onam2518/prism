"""미디어 실험실 도메인 (serve 에서 분리 · 라우트 분리 4차).

자막 파싱(T1)·S5 메타추출 모델 A/B·네이티브 비디오(T4) 실험 액션 디스패치.
결과를 저장하지 않는다(persist=False) · 트랙 분해 실체는 mediaext 모듈.

컴포지션: run_pipeline·mock 플래그는 serve 가 `_SV` 로 주입(learnops 와 동일 관례).
"""
from __future__ import annotations

from .config import Config

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


def media_action(data: dict) -> dict:
    """미디어 메타 파이프라인(포토·영상 텍스트화) 액션 디스패치.

    증분 1: T1 자막 파싱(룰·모델 0건)만 실동작. T2 오디오 전사·T3 비주얼 묘사는
    라우터/미디어 분해 결정 후 별도 증분에서 붙인다(mediaext 모듈에 트랙은 이미 존재)."""
    from . import mediaext as MX
    action = (data.get("action") or "subtitles").strip()
    if action == "subtitles":
        raw = data.get("raw") or ""
        fmt = (data.get("fmt") or "").strip()
        if not raw.strip():
            return {"ok": False, "error": "자막 원문을 입력하세요"}
        return {"ok": True, **MX.parse_subtitles(raw, fmt)}
    if action == "s5ab":                              # S5 메타추출 모델 A/B(미저장)
        text = (data.get("text") or "").strip()
        models = data.get("models") or []
        if not text:
            return {"ok": False, "error": "통합 원고(텍스트)를 입력하세요"}
        if not models:
            return {"ok": False, "error": "후보 모델을 1개 이상 선택하세요"}
        return media_s5ab(text, models, caption=data.get("caption") or "")
    return {"ok": False, "error": "알 수 없는 동작(자막 파싱은 media_action, 영상은 media_native)"}


def media_s5ab(text: str, models: list, *, caption: str = "") -> dict:
    """S5 메타추출 모델 A/B(실험실 · 미저장). 같은 통합 원고를 후보 모델들에 태워
    아이템 메타(리드문·인텐트·엔티티·IAB)를 나란히 비교 → '선정 대기 슬롯'의 모델
    교체 자유를 실측으로 증명. 모델 라우팅은 llm_for_model 재사용(solar 직접·라우터).

    무키(또는 서버 mock) 시 route=mock 로 동일 산출 — 실키 연결 시 모델별로 갈린다.
    """
    body = (caption.strip() + "\n" + text).strip() if caption.strip() else text
    results = []
    for m in list(dict.fromkeys(str(x) for x in models))[:6]:   # 중복 제거 · 상한 6
        try:
            res = _SV.run_pipeline({"displayServiceName": "영상", "title": "", "subtitle": "", "body": body},
                               mock=_SV.Handler.server_mock, model=m, persist=False)
        except Exception as e:
            results.append({"model": m, "error": str(e)[:120]})
            continue
        if res.get("error"):
            results.append({"model": m, "error": res["error"]})
            continue
        out = res.get("output") or {}
        im = out.get("item_meta") or {}
        tr = out.get("trace") or {}
        # 계측: 빈 산출 진단 — item_meta 가 비었는데 mock 도 아니면 실패. trace.fails 로 사유 노출
        #  (예: gemini 침묵 빈응답 → kind=parse_empty). 하네스가 '왜 빈값'을 스스로 보고한다.
        empty = not (im.get("summary") or im.get("entities") or im.get("content_category"))
        results.append({"model": m, "mock": bool(res.get("mock")),
                        "item_meta": im, "empty": empty,
                        "fails": tr.get("fails") or []})
    return {"ok": True, "results": results}


def media_native(content_bytes: bytes, mime: str, *, caption: str = "",
                 description: str = "", model: str = "", subtitles: str = "") -> dict:
    """T4 네이티브 비디오 실험(실험실 · 미저장). 영상 통짜 → 라우터 위임 트랙 →
    S4 병합 → 합성 Content → 기존 추출(S5) → ItemMeta. results 에 저장하지 않는다.

    비전 슬롯이 라우터면 그 서비스/모델로 네이티브 호출, 아니면(또는 서버 mock) mock 폴백.
    """
    from . import mediaext as MX
    cfg = Config.load()
    mock = _SV.Handler.server_mock
    subs = MX.parse_subtitles(subtitles) if (subtitles or "").strip() else {}
    if subs.get("cue_count"):
        # 자막 우선(T1 · 모델 0건): 발화 원고가 이미 있으니 영상 모델 호출을 건너뛴다(비용 0).
        # 설계안 명시("자막 보유율 실측이 비용 계획의 기준점")의 라우팅 실현 · 응답 shape 는 유지.
        nv = {"skipped": True, "skip_reason": "자막 보유 · 영상 모델 호출 생략(비용 0)",
              "audio": {"transcript": "", "has_speech": False},
              "visual": {"description": "", "on_screen_text": "", "entities": []}}
        merged = MX.merge_tracks(subtitles=subs)
    else:
        service = cfg.vision_provider if MX.is_router(cfg.vision_provider) else "bizrouter"
        vmodel = cfg.vision_model or model
        nv = MX.native_video_track(content_bytes, mime, vmodel, service, mock=mock)
        merged = MX.merge_tracks(audio=nv.get("audio"), visual=nv.get("visual"))
    content = MX.build_content(merged, caption=caption, description=description)
    # S5 = 기존 추출 재사용(imagext 동일 설계) · persist=False 로 미저장
    res = _SV.run_pipeline({"displayServiceName": content["displayServiceName"],
                        "title": content["title"], "subtitle": content["subtitle"],
                        "body": content["body"]},
                       mock=mock, model=model, persist=False)
    return {"ok": True, "mock": bool(res.get("mock")), "native": nv,
            "merged": merged, "content": content, "output": res.get("output") or {}}
