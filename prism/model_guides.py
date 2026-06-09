"""모델별 프롬프팅 레시피."""
from __future__ import annotations

# 모델 prefix → 레시피. resolve() 가 prefix 매칭.
GUIDES = {
    "solar-pro3": {
        "label": "Solar Pro 3",
        "source": "UpstageAI/solar-prompt-cookbook",
        "role_first": True,          # Ch5: 역할을 맨 앞에 명확히
        "five_focal": True,          # Ch3: 역할·지시·맥락·예시·형식 5요소 구조
        "structured": True,          # Ch6: 구조화 프롬프트(섹션 구분)
        "cot": "evidence_first",     # Ch7: 증거→답 2단계
        "few_shot_k": 6,             # Ch4: 분류는 few-shot 권장. 경계 보완용 예시 수
        "few_shot_focus": ["ad", "spam", "gambling"],  # 취약 버킷 예시 우선
        "temperature": 0.0,          # 분류=결정적
        "notes": ("Solar 는 명확·구체 지시에 강하게 반응하나 룰 추론 용량이 작다. "
                  "→ 룰 나열보다 '경계 사례 few-shot'으로 판단 기준을 예시화하는 것이 효과적."),
    },
    "solar-pro2": {
        "label": "Solar Pro 2", "source": "UpstageAI/solar-prompt-cookbook",
        "role_first": True, "five_focal": True, "structured": True,
        "cot": "evidence_first", "few_shot_k": 4, "few_shot_focus": ["ad", "spam"],
        "temperature": 0.0, "notes": "Pro2: 구조화·few-shot 권장.",
    },
    "default": {
        "label": "default", "source": "generic",
        "role_first": True, "five_focal": False, "structured": True,
        "cot": "evidence_first", "few_shot_k": 0, "few_shot_focus": [],
        "temperature": 0.0, "notes": "기본 레시피(few-shot 없음).",
    },
}


def resolve(model: str) -> dict:
    m = (model or "").lower()
    for key, guide in GUIDES.items():
        if key != "default" and m.startswith(key):
            return guide
    return GUIDES["default"]


def few_shot_k(model: str) -> int:
    return resolve(model).get("few_shot_k", 0)


def few_shot_focus(model: str) -> list:
    return resolve(model).get("few_shot_focus", [])
