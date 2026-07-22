"""미디어 실험실 도메인 (serve 에서 분리 · 라우트 분리 4차).

자막 파싱(T1)·S5 메타추출 모델 A/B·네이티브 비디오(T4) 실험 액션 디스패치.
결과를 저장하지 않는다(persist=False) · 트랙 분해 실체는 mediaext 모듈.

컴포지션: run_pipeline·mock 플래그는 serve 가 `_SV` 로 주입(learnops 와 동일 관례).
"""
from __future__ import annotations

from .config import Config

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입


def media_action(data: dict) -> dict:
    """\ubbf8\ub514\uc5b4 \uba54\ud0c0 \ud30c\uc774\ud504\ub77c\uc778(\ud3ec\ud1a0\u00b7\uc601\uc0c1 \ud14d\uc2a4\ud2b8\ud654) \uc561\uc158 \ub514\uc2a4\ud328\uce58.

    \uc99d\ubd84 1: T1 \uc790\ub9c9 \ud30c\uc2f1(\ub8f0\u00b7\ubaa8\ub378 0\uac74)\ub9cc \uc2e4\ub3d9\uc791. T2 \uc624\ub514\uc624 \uc804\uc0ac\u00b7T3 \ube44\uc8fc\uc5bc \ubb18\uc0ac\ub294
    \ub77c\uc6b0\ud130/\ubbf8\ub514\uc5b4 \ubd84\ud574 \uacb0\uc815 \ud6c4 \ubcc4\ub3c4 \uc99d\ubd84\uc5d0\uc11c \ubd99\uc778\ub2e4(mediaext \ubaa8\ub4c8\uc5d0 \ud2b8\ub799\uc740 \uc774\ubbf8 \uc874\uc7ac)."""
    from . import mediaext as MX
    action = (data.get("action") or "subtitles").strip()
    if action == "subtitles":
        raw = data.get("raw") or ""
        fmt = (data.get("fmt") or "").strip()
        if not raw.strip():
            return {"ok": False, "error": "\uc790\ub9c9 \uc6d0\ubb38\uc744 \uc785\ub825\ud558\uc138\uc694"}
        return {"ok": True, **MX.parse_subtitles(raw, fmt)}
    if action == "s5ab":                              # S5 메타추출 모델 A/B(미저장)
        text = (data.get("text") or "").strip()
        models = data.get("models") or []
        if not text:
            return {"ok": False, "error": "\ud1b5\ud569 \uc6d0\uace0(\ud14d\uc2a4\ud2b8)\ub97c \uc785\ub825\ud558\uc138\uc694"}
        if not models:
            return {"ok": False, "error": "\ud6c4\ubcf4 \ubaa8\ub378\uc744 1\uac1c \uc774\uc0c1 \uc120\ud0dd\ud558\uc138\uc694"}
        return media_s5ab(text, models, caption=data.get("caption", ""))
    return {"ok": False, "error": "\uc54c \uc218 \uc5c6\ub294 \ub3d9\uc791(\uc790\ub9c9 \ud30c\uc2f1\uc740 media_action, \uc601\uc0c1\uc740 media_native)"}


def media_s5ab(text: str, models: list, *, caption: str = "") -> dict:
    """S5 \uba54\ud0c0\ucd94\ucd9c \ubaa8\ub378 A/B(\uc2e4\ud5d8\uc2e4 \u00b7 \ubbf8\uc800\uc7a5). \uac19\uc740 \ud1b5\ud569 \uc6d0\uace0\ub97c \ud6c4\ubcf4 \ubaa8\ub378\ub4e4\uc5d0 \ud0dc\uc6cc
    \uc544\uc774\ud15c \uba54\ud0c0(\ub9ac\ub4dc\ubb38\u00b7\uc778\ud150\ud2b8\u00b7\uc5d4\ud2f0\ud2f0\u00b7IAB)\ub97c \ub098\ub780\ud788 \ube44\uad50 \u2192 '\uc120\uc815 \ub300\uae30 \uc2ac\ub86f'\uc758 \ubaa8\ub378
    \uad50\uccb4 \uc790\uc720\ub97c \uc2e4\uce21\uc73c\ub85c \uc99d\uba85. \ubaa8\ub378 \ub77c\uc6b0\ud305\uc740 llm_for_model \uc7ac\uc0ac\uc6a9(solar \uc9c1\uc811\u00b7\ub77c\uc6b0\ud130).

    \ubb34\ud0a4(\ub610\ub294 \uc11c\ubc84 mock) \uc2dc route=mock \ub85c \ub3d9\uc77c \uc0b0\ucd9c \u2014 \uc2e4\ud0a4 \uc5f0\uacb0 \uc2dc \ubaa8\ub378\ubcc4\ub85c \uac08\ub9b0\ub2e4.
    """
    body = (caption.strip() + "\n" + text).strip() if caption.strip() else text
    results = []
    for m in list(dict.fromkeys(str(x) for x in models))[:6]:   # 중복 제거 · 상한 6
        try:
            res = _SV.run_pipeline({"displayServiceName": "\uc601\uc0c1", "title": "", "subtitle": "", "body": body},
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
    """T4 \ub124\uc774\ud2f0\ube0c \ube44\ub514\uc624 \uc2e4\ud5d8(\uc2e4\ud5d8\uc2e4 \u00b7 \ubbf8\uc800\uc7a5). \uc601\uc0c1 \ud1b5\uc9dc \u2192 \ub77c\uc6b0\ud130 \uc704\uc784 \ud2b8\ub799 \u2192
    S4 \ubcd1\ud569 \u2192 \ud569\uc131 Content \u2192 \uae30\uc874 \ucd94\ucd9c(S5) \u2192 ItemMeta. results \uc5d0 \uc800\uc7a5\ud558\uc9c0 \uc54a\ub294\ub2e4.

    \ube44\uc804 \uc2ac\ub86f\uc774 \ub77c\uc6b0\ud130\uba74 \uadf8 \uc11c\ube44\uc2a4/\ubaa8\ub378\ub85c \ub124\uc774\ud2f0\ube0c \ud638\ucd9c, \uc544\ub2c8\uba74(\ub610\ub294 \uc11c\ubc84 mock) mock \ud3f4\ubc31.
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
