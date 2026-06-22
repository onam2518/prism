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
import urllib.request

OCR_URL = "https://api.upstage.ai/v1/document-digitization"
CHAT_URL = "https://api.upstage.ai/v1/chat/completions"
DOCVISION_MODEL = "solar-docvision"

_VISION_PROMPT = (
    "이 이미지의 핵심 내용을 한국어로 요약하라. 등장 인물/사물/장소/브랜드/로고, "
    "이미지에 보이는 텍스트, 전체 상황·분위기를 구체적으로 포함하라. "
    "확인되지 않는 내용은 추측하지 말고 3~5문장으로 작성하라."
)


def _api_key() -> str:
    return os.environ.get("UPSTAGE_API_KEY", os.environ.get("PRISM_API_KEY", "")).strip()


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


def docvision_describe(content: bytes, mime: str = "image/png", timeout: int = 60) -> str:
    """DocVision(solar-docvision) 으로 이미지 시각 내용 요약. 실패/무키 시 빈 문자열."""
    key = _api_key()
    if not key:
        return ""
    data_url = f"data:{mime};base64,{base64.b64encode(content).decode()}"
    body = {
        "model": DOCVISION_MODEL,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": _VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    req = urllib.request.Request(CHAT_URL, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    return (choices[0]["message"]["content"] if choices else "").strip()


def _mock_signal(idx: int) -> dict:
    """키 없이 UI 흐름을 검증하기 위한 결정론적 합성 신호."""
    return {
        "ocr": f"[mock-OCR {idx}] 이미지 내 텍스트 자리표시자",
        "vision": (f"[mock-Vision {idx}] 인물/사물/배경이 담긴 이미지로, "
                   "현장 상황과 분위기를 전달하는 비주얼 콘텐츠로 보인다."),
    }


def extract_signals(images: list, *, mock: bool = False) -> list:
    """이미지 목록 → 이미지별 {ocr, vision, latency_ms} 신호.

    images: [{"bytes": b"...", "filename": "a.png", "mime": "image/png"}, ...]
    mock=True 또는 키 없음이면 mock 신호.
    """
    use_mock = mock or not _api_key()
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
        try:
            ocr = ocr_image(im["bytes"], name, mime)
        except Exception as e:
            ocr = ""
            print(f"  [warn] OCR 실패 ({name}): {e}")
        try:
            vision = docvision_describe(im["bytes"], mime)
        except Exception as e:
            vision = ""
            print(f"  [warn] DocVision 실패 ({name}): {e}")
        out.append({"ocr": ocr, "vision": vision,
                    "latency_ms": int((time.time() - t0) * 1000)})
    return out


def build_content(signals: list, *, displayServiceName: str = "포토",
                  title: str = "", caption: str = "") -> dict:
    """이미지 신호(들)를 Prism 4필드 Content 로 합성.

    여러 이미지는 하나의 콘텐츠로 통합: body 에 이미지별 신호를 순서대로 누적.
    title 미지정 시 첫 신호에서 한 줄을 끌어와 채운다(분류 신호 확보).
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

    if not title:
        first = (signals[0].get("vision") or signals[0].get("ocr") or "") if signals else ""
        title = first.split(".")[0][:60] if first else "이미지 콘텐츠"

    return {
        "displayServiceName": displayServiceName or "포토",
        "title": title,
        "subtitle": "",
        "body": body,
    }
