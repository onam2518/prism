"""이미지 인제스트 어댑터 (방식 A): 이미지 → (OCR + DocVision) → 합성 Content.

Prism 코어 무수정. 이미지에서 추출한 신호로 4필드 Content(title/body)를 만들어
기존 텍스트 파이프라인(pipeline.extract)에 그대로 태운다. 리드문·엔티티·카테고리는
Prism 의 run_item 이 뽑는다.

의존성 0: urllib(stdlib)만 사용. Upstage 전용(OCR=document-digitization, 시각=solar-docvision).
키가 없으면 mock 신호를 돌려줘 UI 가 키 없이도 동작한다.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

OCR_URL = "https://api.upstage.ai/v1/document-digitization"
# 이미지 이해(시각): Upstage Information Extraction. 이미지를 멀티모달로 읽어
# 스키마(설명·보이는 텍스트·엔티티·장면)를 한 번에 채운다. Solar 챗은 이미지 입력
# 미지원이라(=Image input is not allowed) 이 엔드포인트로 '본다'.
IE_URL = "https://api.upstage.ai/v1/information-extraction"
IE_MODEL = "information-extract"

# 시각 신호 스키마: 한국어 강제. description=한 방향 요약, visible_text=이미지 내 텍스트.
_VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": ("이미지가 무엇을 담고 있는지 한국어 3~5문장으로 서술. "
                            "등장 인물·사물·장소·브랜드·로고, 전체 상황·분위기, "
                            "콘텐츠 성격(뉴스·연예·스포츠 등 추정)을 구체적으로 포함. "
                            "확인되지 않는 내용은 추측 금지."),
        },
        "visible_text": {
            "type": "string",
            "description": "이미지에 보이는 텍스트 전체를 한국어 그대로. 없으면 빈 문자열.",
        },
        "entities": {
            "type": "array", "items": {"type": "string"},
            "description": "핵심 개체(인물·기관·브랜드·사물) 1~5개. 고유명사 우선.",
        },
        "scene": {
            "type": "string",
            "description": "장소·상황·콘텐츠 성격을 한국어 한 줄로.",
        },
    },
    "required": ["description", "visible_text", "entities", "scene"],
}


# ── 다중 이미지 통합 정책 (DNM 1312/134 기준 후속) ──
#   장수 상한: 한 콘텐츠에 이미지가 너무 많이 붙으면 비전 호출 비용·합성 본문이
#   폭주하므로 상한을 둔다(비전 호출 전에 적용해야 비용이 잡힌다).
#   가중치: 첫 장을 '대표'로 보고 제목·분류 신호를 우선 끌어온다(업로드 순서 = 중요도).
#   상한 초과분은 조용히 버리지 않고 본문에 명시한다.
MAX_IMAGES = 8


def cap_images(images: list) -> tuple:
    """장수 상한 적용 → (처리할 이미지, 초과로 버린 수). 첫 장이 대표(가중치 우선)."""
    if len(images) <= MAX_IMAGES:
        return list(images), 0
    return list(images[:MAX_IMAGES]), len(images) - MAX_IMAGES


def _api_key() -> str:
    """Upstage Solar 키(OCR · Information Extraction · Solar 텍스트용)."""
    return os.environ.get("UPSTAGE_API_KEY", os.environ.get("PRISM_API_KEY", "")).strip()


# 통합 라우터(OpenAI 호환) 레지스트리. 텍스트·비전 슬롯이 공유한다.
#   base   : OpenAI SDK 용 base(…/v1). chat 호출은 base + /chat/completions.
#   key_env: 프로세스 환경변수 이름(키 비밀값).
#   model  : public id 형식(bizrouter=provider/model, timely=bare).
ROUTERS = {
    "bizrouter": {"label": "BizRouter", "base": "https://bizrouter.ai/api/v1",
                  "key_env": "PRISM_BIZROUTER_KEY", "key_alt": "PRISM_ROUTER_KEY"},
    "timely":    {"label": "Timely",    "base": "https://router.stg.timelyai.io/v1",
                  "key_env": "PRISM_TIMELY_KEY", "key_alt": ""},
}


def is_router(provider: str) -> bool:
    return provider in ROUTERS


def router_key(service: str) -> str:
    info = ROUTERS.get(service)
    if not info:
        return ""
    k = os.environ.get(info["key_env"], "").strip()
    if not k and info.get("key_alt"):
        k = os.environ.get(info["key_alt"], "").strip()
    return k


def router_chat_url(service: str) -> str:
    info = ROUTERS.get(service) or ROUTERS["bizrouter"]
    return info["base"].rstrip("/") + "/chat/completions"


# 라우터 멀티모달 프롬프트: 스키마와 동일한 JSON 객체 하나만 출력하도록 강제(response_format
# 미지원 모델까지 호환). 파싱은 호출부에서.
_ROUTER_VISION_PROMPT = (
    "이 이미지를 보고 아래 JSON 객체 하나만 출력하라. 코드펜스·설명 금지.\n"
    '{"description":"이미지가 무엇을 담는지 한국어 3~5문장(인물·사물·장소·브랜드·로고·'
    '상황·분위기·콘텐츠 성격 포함, 추측 금지)","visible_text":"이미지에 보이는 텍스트 전체'
    '(없으면 빈 문자열)","entities":["핵심 개체 1~5개, 고유명사 우선"],"scene":"장소·상황·'
    '콘텐츠 성격 한 줄"}'
)


def _multipart_body(filename: str, content: bytes, mime: str):
    """OCR 업로드용 multipart/form-data 바디 구성(아웃바운드)."""
    boundary = "----prismimg" + base64.urlsafe_b64encode(os.urandom(9)).decode()
    nl = "\r\n"
    head = (
        f"--{boundary}{nl}"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"{nl}'
        f"Content-Type: {mime}{nl}{nl}"
    ).encode() + content + nl.encode()
    head += (
        f"--{boundary}{nl}"
        f'Content-Disposition: form-data; name="model"{nl}{nl}ocr{nl}'
        f"--{boundary}--{nl}"
    ).encode()
    return head, boundary


def ocr_image(content: bytes, filename: str = "image.png", mime: str = "image/png",
              timeout: int = 60) -> str:
    """Document OCR 로 이미지 내 텍스트 추출. 실패/무키 시 빈 문자열."""
    key = _api_key()
    if not key:
        return ""
    body, boundary = _multipart_body(filename, content, mime)
    req = urllib.request.Request(OCR_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return (payload.get("text") or "").strip()


class NoTextInImage(Exception):
    """Information Extraction 이 이미지에서 텍스트 요소를 찾지 못한 경우."""


def vision_understand(content: bytes, mime: str = "image/png", timeout: int = 90) -> dict:
    """Information Extraction 으로 이미지를 시각 이해해 스키마(설명·텍스트·엔티티·장면) 산출.

    반환: {"description","visible_text","entities","scene"}. 실패/무키 시 빈 dict.
    텍스트 미검출(순수 사진 등)이면 NoTextInImage 발생.
    """
    key = _api_key()
    if not key:
        return {}
    data_url = f"data:{mime};base64,{base64.b64encode(content).decode()}"
    body = {
        "model": IE_MODEL,
        "messages": [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "image_meta", "schema": _VISION_SCHEMA},
        },
    }
    req = urllib.request.Request(IE_URL, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        if "No text elements" in detail:
            raise NoTextInImage(detail) from e
        raise
    choices = payload.get("choices") or []
    raw = (choices[0]["message"]["content"] if choices else "") or ""
    try:
        obj = json.loads(raw)
    except Exception:
        obj = {}
    return obj if isinstance(obj, dict) else {}


def _parse_json_lax(raw: str) -> dict:
    """모델 출력에서 첫 JSON 객체를 관대하게 추출."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):] if "{" in raw else raw
    try:
        return json.loads(raw)
    except Exception:
        i, j = raw.find("{"), raw.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(raw[i:j + 1])
            except Exception:
                return {}
        return {}


def vision_via_router(content: bytes, mime: str, model: str, service: str = "bizrouter",
                      timeout: int = 90) -> dict:
    """통합 라우터(OpenAI 호환) 멀티모달 모델로 이미지 이해 → 스키마 dict.

    service: bizrouter | timely. model: 각 라우터의 public id. 키는 라우터별 env.
    """
    key = router_key(service)
    if not key or not model:
        return {}
    data_url = f"data:{mime};base64,{base64.b64encode(content).decode()}"
    body = {
        "model": model,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": _ROUTER_VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "stream": False,
    }
    req = urllib.request.Request(router_chat_url(service), data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    raw = (choices[0]["message"]["content"] if choices else "") or ""
    if not raw.strip():
        raise ValueError("빈 응답(HTTP 200 · content 공백)")   # 침묵 실패 → 명시 실패(호출부 except 경로)
    obj = _parse_json_lax(raw)
    # 모델이 스키마를 못 지키고 평문만 주면 description 으로라도 보존
    if not obj:
        obj = {"description": raw.strip(), "visible_text": "", "entities": [], "scene": ""}
    return obj if isinstance(obj, dict) else {}


def _vision_cfg():
    try:
        from .config import Config
        c = Config.load()
        return c.vision_provider or "upstage_ie", c.vision_model or ""
    except Exception:
        return "upstage_ie", ""


# 순수 사진 폴백 기본 라우터 시각 모델. Upstage IE(문서 시각 이해)는 텍스트 요소가 없는
# 순수 사진을 못 읽어(NoTextInImage) 장면 인식 0건이 된다. 이때 라우터 멀티모달로 1회
# 자동 폴백한다. 모델은 리포트(2026-07-22 · 샘플 20장)에서 장면 인식 20/20 · 고유명사
# 최고로 검증된 gemini-3.5-flash 를 쓴다(검증 안 된 라우터는 자동 폴백 대상에서 제외).
DEFAULT_ROUTER_VISION = {"timely": "gemini-3.5-flash"}


def _router_fallback():
    """순수 사진 폴백용 (service, model). 검증된 라우터 키가 연결돼 있으면 그 기본 모델, 없으면 None."""
    for svc, model in DEFAULT_ROUTER_VISION.items():
        if router_key(svc):
            return svc, model
    return None


def _mock_signal(idx: int) -> dict:
    """키 없이 UI 흐름을 검증하기 위한 결정론적 합성 신호."""
    return {
        "ocr": f"[mock-OCR {idx}] 이미지 내 텍스트 자리표시자",
        "vision": (f"[mock-Vision {idx}] 인물/사물/배경이 담긴 이미지로, "
                   "현장 상황과 분위기를 전달하는 비주얼 콘텐츠로 보인다."),
    }


def _compose_vision(obj: dict) -> str:
    """IE 산출(설명·엔티티·장면)을 Solar 입력용 한 덩어리로 합친다."""
    parts = []
    if obj.get("description"):
        parts.append(obj["description"].strip())
    ents = [e for e in (obj.get("entities") or []) if e]
    if ents:
        parts.append("[엔티티] " + ", ".join(ents))
    if obj.get("scene"):
        parts.append("[장면] " + obj["scene"].strip())
    return " ".join(parts)


def extract_signals(images: list, *, mock: bool = False, vision=None) -> list:
    """이미지 목록 → 이미지별 {ocr, vision, latency_ms, note?} 신호.

    images: [{"bytes": b"...", "filename": "a.png", "mime": "image/png"}, ...]
    vision: (provider, model) per-call 시각 슬롯 오버라이드. None 이면 전역 config(_vision_cfg).
    각 이미지는 선택된 슬롯으로 시각 이해(설명·엔티티·장면·보이는 텍스트).
    IE 선택 시 순수 사진(텍스트 미검출)이면 라우터 멀티모달로 1회 자동 폴백,
    그래도 비면 Document OCR 로 텍스트 확보 시도. mock=True/무키 시 mock.
    """
    provider, vmodel = vision if vision else _vision_cfg()
    router = is_router(provider)
    # 비전 슬롯에 필요한 키가 있는지로 mock 판단(라우터=라우터키, upstage_ie=Solar키)
    have_vision = (router_key(provider) and vmodel) if router else bool(_api_key())
    use_mock = mock or (not have_vision and not _api_key())
    fb = None if router else _router_fallback()   # IE 선택 시에만 순수 사진 라우터 폴백 준비
    out = []
    for i, im in enumerate(images, 1):
        if use_mock:
            sig = _mock_signal(i)
            sig["latency_ms"] = 0
            out.append(sig)
            continue
        t0 = time.time()
        mime = im.get("mime", "image/png")
        name = im.get("filename", f"image{i}.png")
        vision_txt, ocr, note = "", "", ""
        try:
            if router and router_key(provider) and vmodel:
                obj = vision_via_router(im["bytes"], mime, vmodel, provider)
            else:
                obj = vision_understand(im["bytes"], mime)   # Upstage IE
            vision_txt = _compose_vision(obj)
            ocr = (obj.get("visible_text") or "").strip()
        except NoTextInImage:
            if fb:                                            # 순수 사진 → 라우터 멀티모달 1회 폴백(리포트 #2)
                try:
                    obj = vision_via_router(im["bytes"], mime, fb[1], fb[0])
                    vision_txt = _compose_vision(obj)
                    ocr = (obj.get("visible_text") or "").strip()
                    note = f"순수 사진 · {fb[1]}({fb[0]}) 라우터 폴백"
                except Exception as e:
                    note = "라우터 폴백 시각 이해에 실패했습니다."
                    print(f"  [warn] 라우터 폴백 실패({name}): {e}")
            else:
                note = "이미지에서 텍스트가 검출되지 않아 Upstage 시각 이해를 적용하지 못했습니다(순수 사진은 라우터 멀티모달 권장)."
                print(f"  [warn] 시각 이해 불가({name}): no text elements")
        except Exception as e:
            note = "시각 이해 호출에 실패했습니다."
            print(f"  [warn] 비전({provider}) 실패({name}): {e}")
        # vision 이 비었으면 Document OCR 로라도 텍스트 확보 시도(Solar 키 있을 때)
        if not vision_txt and not ocr and _api_key():
            try:
                ocr = ocr_image(im["bytes"], name, mime)
            except Exception as e:
                print(f"  [warn] OCR 폴백 실패({name}): {e}")
        sig = {"ocr": ocr, "vision": vision_txt,
               "latency_ms": int((time.time() - t0) * 1000)}
        if note:
            sig["note"] = note
        out.append(sig)
    return out


def build_content(signals: list, *, displayServiceName: str = "포토",
                  title: str = "", caption: str = "", dropped: int = 0) -> dict:
    """이미지 신호(들)를 Prism 4필드 Content 로 합성.

    여러 이미지는 하나의 콘텐츠로 통합: body 에 이미지별 신호를 순서대로 누적.
    가중치: 제목 미지정 시 '대표(앞 장 우선)' 신호에서 한 줄을 끌어온다(분류 신호 확보).
    dropped>0(장수 상한 초과로 버린 수)이면 본문에 명시해 누락을 숨기지 않는다.
    """
    blocks = []
    for i, s in enumerate(signals, 1):
        parts = []
        if s.get("vision"):
            parts.append(s["vision"])
        if s.get("ocr"):
            parts.append(f"[이미지 텍스트] {s['ocr']}")
        if parts:
            blocks.append(f"(이미지 {i}) " + " ".join(parts))
    body = "\n".join(blocks)
    if caption:
        body = f"{caption}\n{body}".strip()
    if dropped > 0:
        body += (f"\n(첨부 {len(signals) + dropped}장 중 상한 {MAX_IMAGES}장만 반영, "
                 f"{dropped}장 제외)")

    if not title:
        # 대표=앞 장 우선. 단 텅 빈 대표(순수 사진 등)는 건너뛰고 다음 신호로.
        lead = next((s.get("vision") or s.get("ocr") for s in signals
                     if (s.get("vision") or s.get("ocr"))), "")
        title = lead.split(".")[0][:60] if lead else "이미지 콘텐츠"

    return {
        "displayServiceName": displayServiceName or "포토",
        "title": title,
        "subtitle": "",
        "body": body,
    }
