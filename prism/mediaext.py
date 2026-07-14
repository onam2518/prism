"""미디어(영상·오디오) 텍스트화 어댑터 — Gemini Flash 파이프라인 v1 증분 1.

설계안(포토·영상 메타 추출 파이프라인 v1)의 '텍스트화 트랙' 3종을 담는다.
imagext(방식 A, 이미지)와 형제 모듈로, 산출물은 트랙별 '원고(transcript/묘사)'다.
병합(S4)·메타추출(S5)은 후속 증분에서 이 원고들을 받아 처리한다.

  T1 자막 파싱   : SRT/VTT → 타임스탬프 원고. 룰·모델 0건. 최우선 경로.
                   (설계안: 자막 보유율 실측이 비용 계획의 기준점)
  T2 오디오 전사 : 라우터(OpenAI 호환) input_audio 파트 → Gemini Flash 구조화 전사.
  T3 비주얼 묘사 : 제공된 썸네일/키프레임 k장 단일 호출 → 묘사 + 화면 텍스트.

의존성 0: urllib(stdlib)만. 라우터 유틸(ROUTERS·키·URL·lax 파서)은 imagext 재사용.
로컬 미디어 디코딩(ffmpeg) 불가 원칙 → raw 영상에서의 오디오 추출·키프레임 추출은
이 모듈이 하지 않는다. 입력은 '이미 분해된' 미디어(자막 텍스트·오디오 바이트·프레임 이미지)다.
키가 없으면 결정론적 mock 을 돌려줘 UI 가 키 없이도 동작한다(imagext 동일 규약).
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.request

from .imagext import (
    ROUTERS, is_router, router_key, router_chat_url, _parse_json_lax,
)

# ─────────────────────────────────────────────────────────────────────────
# T1 자막 파싱 (룰 · 모델 0건)
# ─────────────────────────────────────────────────────────────────────────

# SRT: "00:00:01,000 --> 00:00:04,000"  ·  VTT: "00:00:01.000 --> 00:00:04.000"
_TS = r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})"
_CUE_RE = re.compile(_TS + r"\s*-->\s*" + _TS)


def _ts_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _strip_tags(text: str) -> str:
    """VTT 인라인 태그(<c>, <00:00:00.000> 등)·SRT 잔여 마크업 제거."""
    return re.sub(r"<[^>]+>", "", text).strip()


def parse_subtitles(raw: str, fmt: str = "") -> dict:
    """SRT/VTT 자막 → 타임스탬프 원고. 룰 기반, 모델 호출 0건.

    반환: {segments:[{start,end,text}], transcript:str, cue_count:int, format:str}
    fmt 미지정 시 헤더('WEBVTT')로 자동 판별. 파싱 실패 세그먼트는 조용히 건너뛴다.
    """
    raw = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    detected = fmt or ("vtt" if raw.lstrip().upper().startswith("WEBVTT") else "srt")
    segments = []
    # 블록 = 빈 줄로 구분. 각 블록에서 타임코드 줄을 찾고, 그 아래 줄들을 텍스트로.
    for block in re.split(r"\n\s*\n", raw):
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        cue_idx = next((i for i, ln in enumerate(lines) if _CUE_RE.search(ln)), None)
        if cue_idx is None:
            continue
        m = _CUE_RE.search(lines[cue_idx])
        start = _ts_to_sec(*m.group(1, 2, 3, 4))
        end = _ts_to_sec(*m.group(5, 6, 7, 8))
        text = _strip_tags(" ".join(lines[cue_idx + 1:]))
        if text:
            segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    transcript = "\n".join(f"[{_fmt_ts(s['start'])}] {s['text']}" for s in segments)
    return {"segments": segments, "transcript": transcript,
            "cue_count": len(segments), "format": detected}


def _fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


# ─────────────────────────────────────────────────────────────────────────
# T2 오디오 전사 (라우터 → Gemini Flash · input_audio)
# ─────────────────────────────────────────────────────────────────────────

# 라우터 audio 프롬프트: 스키마와 동일한 JSON 하나만. response_format 미지원 모델까지 호환.
_AUDIO_PROMPT = (
    "이 오디오를 듣고 아래 JSON 객체 하나만 출력하라. 코드펜스·설명 금지. 한국어로.\n"
    '{"language":"주 언어(ko/en 등)","has_speech":true/false(발화 존재 여부),'
    '"transcript":"타임스탬프 없는 전체 전사 원고","segments":[{"start":초(number),'
    '"end":초(number),"speaker":"화자 턴 라벨(S1·S2 등, 불명확 시 빈 문자열)",'
    '"text":"해당 구간 발화"}]}'
)

# mime → OpenAI input_audio format 라벨
_AUDIO_FMT = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/webm": "webm", "audio/ogg": "ogg", "audio/flac": "flac", "audio/aac": "aac",
    "audio/mp4": "mp4", "audio/m4a": "m4a",
}


def _audio_format(mime: str) -> str:
    return _AUDIO_FMT.get((mime or "").lower(), (mime or "").split("/")[-1] or "mp3")


def transcribe_audio(content: bytes, mime: str, model: str, service: str = "bizrouter",
                     timeout: int = 180) -> dict:
    """라우터(OpenAI 호환) input_audio → Gemini Flash 구조화 전사.

    반환: {language,has_speech,transcript,segments:[{start,end,speaker,text}]}.
    무키/무모델 시 빈 dict. 스키마 미준수 평문은 transcript 로 보존.
    """
    key = router_key(service)
    if not key or not model or not content:
        return {}
    body = {
        "model": model,
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": _AUDIO_PROMPT},
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(content).decode(),
                    "format": _audio_format(mime)}},
            ]},
        ],
        "stream": False,
    }
    req = urllib.request.Request(router_chat_url(service), data=json.dumps(body).encode(),
                                 method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    rawtxt = (choices[0]["message"]["content"] if choices else "") or ""
    obj = _parse_json_lax(rawtxt)
    if not obj and rawtxt.strip():
        obj = {"transcript": rawtxt.strip(), "has_speech": True, "segments": [], "language": ""}
    return obj if isinstance(obj, dict) else {}


# ─────────────────────────────────────────────────────────────────────────
# T3 비주얼 묘사 (라우터 → Gemini Flash · 다중 프레임 단일 호출)
# ─────────────────────────────────────────────────────────────────────────

# 상시 트랙: 시각 주도 카테고리 누수 방지 목적. 숏폼 1~3장·롱폼 샷 기반 n장(상한).
MAX_FRAMES = 8

_VISUAL_PROMPT = (
    "이 프레임들(영상 대표 장면 순서)을 보고 아래 JSON 객체 하나만 출력하라. "
    "코드펜스·설명 금지. 한국어로.\n"
    '{"description":"영상이 무엇을 보여주는지 3~6문장(인물·사물·장소·브랜드·로고·'
    '행동·상황·분위기·콘텐츠 성격 포함, 프레임 간 변화도 반영, 추측 금지)",'
    '"on_screen_text":"화면에 보이는 텍스트/자막 전체(없으면 빈 문자열)",'
    '"entities":["핵심 개체 1~8개, 고유명사 우선"],'
    '"scene":"장소·상황·콘텐츠 성격 한 줄"}'
)


def cap_frames(frames: list) -> tuple:
    """프레임 상한 적용 → (처리할 프레임, 초과로 버린 수)."""
    if len(frames) <= MAX_FRAMES:
        return list(frames), 0
    return list(frames[:MAX_FRAMES]), len(frames) - MAX_FRAMES


def describe_visual(frames: list, model: str, service: str = "bizrouter",
                    timeout: int = 120) -> dict:
    """제공된 썸네일/키프레임 k장 단일 호출 → 묘사 + 화면 텍스트.

    frames: [{"bytes": b"...", "mime": "image/jpeg"}, ...] (업로드 순 = 시간 순)
    반환: {description,on_screen_text,entities,scene,frame_count}. 무키/무프레임 시 빈 dict.
    """
    key = router_key(service)
    if not key or not model or not frames:
        return {}
    kept, dropped = cap_frames(frames)
    parts = [{"type": "text", "text": _VISUAL_PROMPT}]
    for fr in kept:
        mime = fr.get("mime", "image/jpeg")
        data_url = f"data:{mime};base64,{base64.b64encode(fr['bytes']).decode()}"
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    body = {"model": model, "messages": [{"role": "user", "content": parts}], "stream": False}
    req = urllib.request.Request(router_chat_url(service), data=json.dumps(body).encode(),
                                 method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    choices = payload.get("choices") or []
    rawtxt = (choices[0]["message"]["content"] if choices else "") or ""
    obj = _parse_json_lax(rawtxt)
    if not obj and rawtxt.strip():
        obj = {"description": rawtxt.strip(), "on_screen_text": "", "entities": [], "scene": ""}
    if isinstance(obj, dict):
        obj["frame_count"] = len(kept)
        if dropped:
            obj["dropped_frames"] = dropped
    return obj if isinstance(obj, dict) else {}


# ─────────────────────────────────────────────────────────────────────────
# mock (키 없이 UI 흐름 검증 · 결정론적)
# ─────────────────────────────────────────────────────────────────────────

def _mock_transcribe() -> dict:
    return {
        "language": "ko", "has_speech": True,
        "transcript": "[mock-T2] 오디오 전사 자리표시자. 발화가 담긴 구간으로 가정.",
        "segments": [
            {"start": 0.0, "end": 3.2, "speaker": "S1", "text": "[mock] 첫 번째 발화 구간."},
            {"start": 3.2, "end": 7.5, "speaker": "S2", "text": "[mock] 두 번째 발화 구간."},
        ],
    }


def _mock_visual(n: int) -> dict:
    return {
        "description": ("[mock-T3] 인물·사물·배경이 담긴 영상 프레임들로, 현장 상황과 "
                        "분위기를 전달하는 비주얼 콘텐츠로 보인다."),
        "on_screen_text": "[mock] 화면 텍스트 자리표시자",
        "entities": ["mock개체A", "mock개체B"],
        "scene": "[mock] 실내/야외 현장, 정보성 콘텐츠 추정",
        "frame_count": n,
    }


def _have_router(service: str, model: str) -> bool:
    return bool(is_router(service) and router_key(service) and model)


def transcribe_track(content: bytes, mime: str, model: str = "", service: str = "bizrouter",
                     *, mock: bool = False) -> dict:
    """T2 실행 래퍼 — 키/모델 없거나 mock 시 결정론적 mock. latency_ms 부착."""
    use_mock = mock or not _have_router(service, model) or not content
    if use_mock:
        sig = _mock_transcribe()
        sig["latency_ms"] = 0
        sig["mock"] = True
        return sig
    t0 = time.time()
    try:
        obj = transcribe_audio(content, mime, model, service)
    except Exception as e:
        return {"language": "", "has_speech": False, "transcript": "", "segments": [],
                "note": f"오디오 전사 호출 실패: {e}", "latency_ms": int((time.time() - t0) * 1000)}
    obj["latency_ms"] = int((time.time() - t0) * 1000)
    return obj


def visual_track(frames: list, model: str = "", service: str = "bizrouter",
                 *, mock: bool = False) -> dict:
    """T3 실행 래퍼 — 키/모델 없거나 mock 시 결정론적 mock. latency_ms 부착."""
    use_mock = mock or not _have_router(service, model) or not frames
    if use_mock:
        sig = _mock_visual(len(frames or []) or 1)
        sig["latency_ms"] = 0
        sig["mock"] = True
        return sig
    t0 = time.time()
    try:
        obj = describe_visual(frames, model, service)
    except Exception as e:
        return {"description": "", "on_screen_text": "", "entities": [], "scene": "",
                "note": f"비주얼 묘사 호출 실패: {e}", "latency_ms": int((time.time() - t0) * 1000)}
    obj["latency_ms"] = int((time.time() - t0) * 1000)
    return obj
