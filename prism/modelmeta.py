"""모델 표시 정보: 원본 id → 읽기 좋은 이름 · 제공자 계열 · 비용 등급.

모델 선택 드롭다운이 `claude-opus-4-8` 같은 원본 id 대신 `Claude Opus 4.8` 로 보이게 하고,
제공자 아이콘과 비용 등급 배지를 붙이기 위한 원천. serve 가 /config(관리자)에 실어 보낸다.

등급은 **실제 누적 비용 원장(cost_rollup)** 에서 계산한다(사용자 결정 2026-07-28) —
고정 가격표를 코드에 박으면 제공자 단가 변경 때 조용히 틀려지지만, 우리 원장은 우리가 실제로
쓴 값이라 늘 현행이다. 대신 한 번도 안 돌린 모델은 등급이 비어 있다(표본 없음 = 무표기).
"""
import re

_ALPHA_NUM = re.compile(r"^([A-Za-z]+)(\d+(?:\.\d+)?)$")


def _is_num(s: str) -> bool:
    return bool(s) and s.replace(".", "", 1).isdigit()

# 건당 평균 비용(USD) 경계. 우리 원장 기준 Opus 계열이 $0.0065~0.0069/건 ·
# Solar Pro 3 이 $0.0044/건 이라 그 사이를 갈랐다. 표본이 적으면 등급을 붙이지 않는다.
TIER_HIGH_USD = 0.005
TIER_LOW_USD = 0.001
TIER_MIN_SAMPLES = 20

# 제공자 계열(아이콘·그룹 표시용). 접두 매칭 · 가장 긴 접두가 이긴다.
_FAMILY_PREFIX = {
    "claude": "anthropic", "gpt": "openai", "o1": "openai", "o3": "openai", "o4": "openai",
    "gemini": "google", "deepseek": "deepseek", "solar": "upstage",
    "mistral": "mistral", "magistral": "mistral", "devstral": "mistral", "codestral": "mistral",
    "grok": "xai", "llama": "meta", "qwen": "alibaba",
    "kimi": "moonshot", "glm": "zhipu", "laguna": "laguna",
}

FAMILY_LABEL = {
    "anthropic": "Anthropic", "openai": "OpenAI", "google": "Google", "deepseek": "DeepSeek",
    "upstage": "Upstage", "mistral": "Mistral", "xai": "Grok", "meta": "Meta",
    "alibaba": "Qwen", "moonshot": "Kimi", "zhipu": "GLM", "laguna": "Laguna", "": "기타",
}

# 이름 다듬기: 원본 id 를 그대로 못 쓰는 조각들(대소문자·표기). 그 외는 규칙으로 만든다.
_WORD = {
    "gpt": "GPT", "o1": "o1", "o3": "o3", "o4": "o4",
    "claude": "Claude", "opus": "Opus", "sonnet": "Sonnet", "haiku": "Haiku", "fable": "Fable",
    "gemini": "Gemini", "flash": "Flash", "pro": "Pro", "mini": "mini", "preview": "preview",
    "deepseek": "DeepSeek", "chat": "Chat", "solar": "Solar", "grok": "Grok",
    "mistral": "Mistral", "magistral": "Magistral", "devstral": "Devstral", "codestral": "Codestral",
    "llama": "Llama", "qwen": "Qwen", "ie": "IE", "reasoning": "Reasoning", "fast": "Fast", "non": "Non",
    "open": "Open", "mini": "mini", "nano": "nano", "lite": "Lite", "sol": "Sol", "terra": "Terra",
    "luna": "Luna", "kimi": "Kimi", "glm": "GLM", "laguna": "Laguna", "k3": "K3",
}


def family(model_id: str) -> str:
    """모델 id → 제공자 계열 키. 라우터 접두(anthropic/…)가 붙어 있으면 그쪽을 우선."""
    mid = (model_id or "").strip().lower()
    if not mid:
        return ""
    if "/" in mid:                                   # bizrouter 형식(provider/model)
        head = mid.split("/", 1)[0]
        if head in FAMILY_LABEL:
            return head
        mid = mid.split("/", 1)[1]
    best, out = "", ""
    for pre, fam in _FAMILY_PREFIX.items():
        if mid.startswith(pre) and len(pre) > len(best):
            best, out = pre, fam
    return out


def label(model_id: str) -> str:
    """모델 id → 사람이 읽는 이름. `claude-opus-4-8` → `Claude Opus 4.8`.

    버전 조각(숫자와 -)은 점으로 잇는다: 4-8 → 4.8 · 4-5 → 4.5.
    날짜형 꼬리(260323 처럼 6자리)는 이름에서 떼지 않고 그대로 둔다(같은 계열 구분에 필요).
    """
    mid = (model_id or "").strip()
    if not mid:
        return ""
    mid = mid.split("/")[-1]                          # 라우터 접두 제거(표시용)
    parts = []
    for p in mid.replace("_", "-").split("-"):
        if not p:
            continue
        m = _ALPHA_NUM.match(p)                       # pro2 → pro + 2 (붙어 있는 이름·버전 분리)
        if m and m.group(1).lower() in _WORD:
            parts.extend([m.group(1), m.group(2)])
        else:
            parts.append(p)
    out, i = [], 0
    while i < len(parts):
        p = parts[i]
        if _is_num(p):                                # 연속 숫자 조각은 버전으로 묶어 점 연결
            ver = [p]
            while i + 1 < len(parts) and parts[i + 1].isdigit() and len(parts[i + 1]) <= 2:
                ver.append(parts[i + 1]); i += 1
            out.append(".".join(ver))
        else:
            key = p.lower()
            out.append(_WORD.get(key, p if p[:1].isupper() else p.capitalize()))
        i += 1
    # GPT·o 시리즈는 브랜드 표기가 하이픈(GPT-5.4) — 이름과 버전을 붙여 쓴다
    for i in range(len(out) - 1):
        if out[i] in ("GPT", "o1", "o3", "o4") and _is_num(out[i + 1]):
            out[i] = out[i] + "-" + out[i + 1]
            out.pop(i + 1)
            break
    return " ".join(out)


def tiers_from_cost(cost_report: dict) -> dict:
    """비용 원장 → {model: {tier, avg_usd, n}}. tier ∈ 'high'|'low'|'' (표본 부족은 '').

    cost_rollup 구조: days[YYYY-MM-DD].models[model] = {"n": 실행건수, "cost": USD,
    "n_billed": 과금된 실행 수(신규 키 · 구 원장에는 없다)}.
    건당 평균 = Σcost / Σn_billed. 실행이 적은 모델은 평균이 튀므로 등급을 비운다.

    **비용 0 인 묶음은 통째로 뺀다**: 라우터가 402(잔액 부족)로 전건 거절하면 실행 건수만
    쌓이고 과금은 0 이라, 그대로 평균에 넣으면 비싼 모델이 싸 보인다(2026-07-28 실제 사고 —
    Opus 실제 $0.0067/건이 실패 600건 때문에 $0.0034/건으로 계산됨).
    그런데 그 방어는 '전건 실패한 날'만 막았다: 성공과 실패가 **같은 날 섞이면** 묶음 cost 가
    0 보다 커서 제외되지 않고 실패분 n 만 분모에 남아 평균이 희석된다(실패율 26% 면 등급 소멸).
    → 분모를 `n_billed`(과금된 실행 수)로 바꾼다. 구 원장에는 이 키가 없으므로
    `get(...) or 0` 으로 읽고 없으면 종전대로 n 을 쓴다(하위호환 · 그날부터 정확해진다).
    """
    agg = {}
    for _day, v in ((cost_report or {}).get("days") or {}).items():
        for m, mv in ((v or {}).get("models") or {}).items():
            m = (m or "").strip()
            mv = mv or {}
            cost = float(mv.get("cost") or 0.0)
            if not m or cost <= 0:                    # 과금 0 = 실제로 돌지 않은 실행(실패분)
                continue
            n_day = int(mv.get("n") or 0)
            billed = int(mv.get("n_billed") or 0)     # 신규 키(없으면 구 원장 = n 으로 폴백)
            cur = agg.setdefault(m, {"n": 0, "billed": 0, "cost": 0.0})
            cur["n"] += n_day
            cur["billed"] += (billed if billed > 0 else n_day)
            cur["cost"] += cost
    out = {}
    for m, v in agg.items():
        n = int(v["n"])
        billed = int(v["billed"]) or n                # 분모 = 과금된 실행 수
        avg = (v["cost"] / billed) if billed else 0.0
        tier = ""
        if billed >= TIER_MIN_SAMPLES and avg > 0:    # 표본이 쌓인 모델만 등급을 붙인다
            tier = "high" if avg >= TIER_HIGH_USD else ("low" if avg <= TIER_LOW_USD else "")
        out[m] = {"tier": tier, "avg_usd": round(avg, 6), "n": n, "n_billed": billed}
    return out


TIER_LABEL = {"high": "고비용", "low": "저비용"}

# 모델별 공시 단가(USD / 1M 토큰) = (입력, 출력, 캐시 읽기). 캐시 읽기는 세 제공자 모두
# 입력가의 0.1 배로 공시돼 있어 그 값을 적었다.
# 출처(2026-09-08 확인): Anthropic 공식 모델·가격표 · OpenAI API 가격(gpt-5.6 sol/terra/luna ·
# gpt-5.4 계열) · Google Gemini API 가격 · Upstage Solar 가격.
# 등급(tier)은 우리 원장에서 뽑지만 **단가는 남의 공시가라 원장으로 대체할 수 없다** —
# 모델 비교표의 비용 축은 이 표가 유일한 원천이다(없으면 설정 단가 하나로 계산돼 토큰 수 순위가 된다).
# **모르는 모델은 넣지 않는다**: 단가 미상 → 비용 None → 화면 '·' · '합격 최저 비용'에서 제외.
# 라우터 접두(provider/)와 버전 표기(4.6 vs 4-6)는 조회에서 흡수한다.
_PRICES = {k.replace(".", "-"): v for k, v in {
    # Anthropic
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-opus-5": (5.0, 25.0, 0.5),
    "claude-opus-4-8": (5.0, 25.0, 0.5),
    "claude-opus-4-7": (5.0, 25.0, 0.5),
    "claude-opus-4-6": (5.0, 25.0, 0.5),
    "claude-sonnet-5": (2.0, 10.0, 0.2),
    "claude-sonnet-4-6": (3.0, 15.0, 0.3),
    "claude-haiku-4-5": (1.0, 5.0, 0.1),
    # OpenAI
    "gpt-5.6-sol": (4.0, 20.0, 0.4),
    "gpt-5.6-terra": (2.0, 12.0, 0.2),
    "gpt-5.6-luna": (0.20, 1.20, 0.02),
    "gpt-5.4": (2.50, 15.0, 0.25),
    "gpt-5.4-mini": (0.75, 4.50, 0.075),
    "gpt-5.4-nano": (0.20, 1.25, 0.02),
    "gpt-5-mini": (0.25, 2.0, 0.025),
    # Google (장문 할증 구간은 미반영 — 3.1 Pro 는 200K 초과 시 $4/$18)
    "gemini-3.5-flash": (1.50, 9.0, 0.15),
    "gemini-3.1-pro-preview": (2.0, 12.0, 0.20),
    "gemini-2.5-pro": (1.25, 10.0, 0.125),
    "gemini-2.5-flash": (0.15, 1.25, 0.015),
    # Upstage
    "solar-pro3": (0.15, 0.60, 0.015),
}.items()}


def prices(model_id: str):
    """모델 id → (입력, 출력, 캐시읽기) USD/1M. 표에 없으면 None(= 단가 미상)."""
    return _PRICES.get((model_id or "").strip().lower().split("/")[-1].replace(".", "-"))


# 라우터가 제공하는 모델 목록(표시 전용). 한 번도 안 돌린 모델은 비용 원장에 없어서
# 이름·아이콘을 만들 근거가 없다 — 이 목록이 있어야 처음 고를 때부터 제대로 보인다.
# 실제 호출 대상 목록은 화면(vendor/app-02 modelCatalog)이 원천이고 여기는 그 사본이다.
# 두 목록이 어긋나면 원본 id 가 그대로 노출되므로 테스트(test_modelmeta)가 일치를 지킨다.
KNOWN_ROUTER_MODELS = [
    # timely(bare id) · 2026-07-29 api.timelyrouter.ai GET /v1/models 실목록 기준
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-fable-5",
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
    "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano",
    "gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-3.1-flash-lite",
    "solar-pro3", "solar-open2", "solar-pro2", "solar-mini",
    "deepseek-v4-pro", "kimi-k3", "laguna-s-2.1", "glm-5.2", "qwen3-235b-a22b",
    # bizrouter(provider/model)
    "openai/gpt-5.4", "openai/gpt-5.4-mini", "openai/gpt-5-mini", "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.6", "google/gemini-2.5-pro", "google/gemini-2.5-flash",
    "deepseek/deepseek-v3.2",
]


def model_meta(cost_report: dict, models=None) -> dict:
    """드롭다운이 쓰는 표시 정보 묶음. models 를 주면 그 목록도 빠짐없이 채운다
    (한 번도 안 돌려 원장에 없는 모델도 이름·계열은 필요하다)."""
    tiers = tiers_from_cost(cost_report)
    ids = set(tiers) | {str(m).strip() for m in (models or []) if str(m).strip()}
    out = {}
    for mid in ids:
        t = tiers.get(mid) or {"tier": "", "avg_usd": 0.0, "n": 0, "n_billed": 0}
        out[mid] = {"label": label(mid), "family": family(mid),
                    "familyLabel": FAMILY_LABEL.get(family(mid), "기타"),
                    "tier": t["tier"], "tierLabel": TIER_LABEL.get(t["tier"], ""),
                    "avgUsd": t["avg_usd"], "runs": t["n"],
                    "billedRuns": int(t.get("n_billed") or 0)}   # 평균의 분모(과금된 실행 수)
    return out
