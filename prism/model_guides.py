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


# ── 계열별 공식 프롬프팅 가이드 distilled (Atelier MODEL_PROMPTING_GUIDES 이식) ──
# 각 사 공식 cookbook/docs 의 핵심 규칙 압축본. 선언형 프롬프트 빌더(builder_compile)가
# 메타 호출에 주입해 계열별 최적화 프롬프트를 컴파일한다. 위 GUIDES(퓨샷 레시피)와 별개.
PROMPTING_GUIDES = {
    "claude": {
        "label": "Claude (Anthropic)",
        "one": "Golden Rule — 친구에게 그대로 줬을 때 따라할 수 있는가. XML 태그로 분리 + CoT.",
        "rules": [
            "역할 부여(system 첫 줄): '당신은 ~ 전문가' 명시",
            "<instructions> <input> <output_format> 등 XML 태그로 섹션 분리 — 모호함 제거",
            "복잡 판단은 '단계별로 생각하라' + <thinking>/<answer> 태그로 추론·최종답 분리",
            "Few-shot 예시는 <example> 태그로 1~3개(입력·기대 페어)",
            "JSON 출력 강제 시 키·타입·예시 JSON 블록을 프롬프트 안에 포함",
            "환각 억제: 모르는 정보는 모른다고 답하게 + 답 전에 본문 근거 인용 요구",
            "edge case 는 별도 <constraints> — 부정형보다 긍정형 규칙",
        ],
        "avoid": ["지시를 흩뿌리기(한 섹션에 모으기)", "모호한 형용사('적절히','잘')",
                  "긴 입력 안에 지시 섞기(지시와 데이터 분리)"],
        "refs": ["github.com/anthropics/anthropic-cookbook", "github.com/anthropics/courses"],
    },
    "gpt": {
        "label": "GPT (OpenAI)",
        "one": "간결·명시 지시 + 구분자 격리 + 'Let's think step by step'. 복잡 작업은 하위 단계 분해.",
        "rules": [
            "지시는 맨 앞 — 작업 → 입력 → 형식 → 제약 순",
            "사용자 입력은 ### 또는 \"\"\" 로 격리(인젝션 차단)",
            "원하는 형식은 실제 JSON 예시 한 덩어리로 명시",
            "Few-shot 1~3개 · 'if X then Y, else Z' 분기 규칙 명시 가능",
            "복잡 작업은 '단계: 1) 2) 3)' 명시 분해 · 복잡 추론엔 step-by-step 트리거",
            "답변 전에 근거 설명 요구(Explain before answering)",
            "전문가 페르소나 한 줄이면 충분 — 부풀리지 말 것",
        ],
        "avoid": ["장황한 공손어·중복 지시", "'~하지 마라' 나열(양성 지시로)",
                  "한 프롬프트에 과다 작업(분해해 체인화)"],
        "refs": ["github.com/openai/openai-cookbook"],
    },
    "gemini": {
        "label": "Gemini (Google)",
        "one": "구조화 지시 + 명시적 출력 스키마 + 명시적 prefix. 군더더기 제거가 핵심.",
        "rules": [
            "한 줄 작업 정의로 시작('~ 분류기')",
            "출력 스키마는 JSON Schema 또는 명시적 키 리스트 — 자유 서술 금지",
            "'Answer:' 'Category:' 같은 명시적 prefix 로 출력 시작 고정",
            "Few-shot 1~5개 · 입력 분포가 다양할수록 도움",
            "CoT 명시 지시 · reasoning 은 출력 안 별도 필드로",
            "입력이 모호/빈 값일 때의 base case 를 미리 정의",
            "군더더기 제거 — please·과도한 역할극·페르소나 부풀리기 지양",
        ],
        "avoid": ["다른 모델용 복잡 프롬프트 그대로 이식", "reasoning+answer+설명 혼합(키로 분리)",
                  "'즉시 답만' 강제(복잡 작업 정확도 저하)"],
        "refs": ["github.com/google-gemini/cookbook"],
    },
    "solar": {
        "label": "Solar (Upstage)",
        "one": "한국어 친화. 짧고 명확한 지시 + 동작 동사 + 5 Focal Elements + N-Shot/CoT.",
        "rules": [
            "한국어 작업은 한국어 프롬프트가 가장 안정",
            "지시문은 짧게: 작업 정의 1~2문장 → 형식 → 예시 순 · 동작 동사로 시작",
            "5 Focal Elements — Instruction/Context/Example/Input/Output 형식 구조",
            "N-Shot(3~5개)이 zero-shot 대비 크게 안정적",
            "CoT 는 '단계별로 생각해' 한국어 트리거 · 복잡 작업은 하위 작업 분해(Least-to-Most)",
            "출력 JSON 키는 영어가 더 안정",
            "긴 텍스트 분석은 '근거 인용 → 답변' 2단계로 분리 요구",
        ],
        "avoid": ["장문의 영어 system", "복잡한 XML/Markdown 중첩(### 나 --- 가 나음)",
                  "모호한 동사('도와줘','~에 대해')", "한 문장에 여러 작업"],
        "refs": ["github.com/UpstageAI/solar-prompt-cookbook"],
    },
    "deepseek": {
        "label": "DeepSeek",
        "one": "OpenAI 호환. chat 은 명료한 지시 + few-shot, reasoner 는 CoT 지시 금지.",
        "rules": [
            "deepseek-chat: system+user 형식 · JSON 은 response_format json_object 권장",
            "deepseek-reasoner: 내부 thinking 자체 처리 → 'step-by-step' 류 지시 넣지 말 것",
            "Few-shot 3~5개가 안정 · 출력 JSON 은 영어 키 + 명시 스키마(예시 1~2개)",
            "긴 추론은 reasoner · 일반 분류/요약/QA 는 chat 으로 분기",
        ],
        "avoid": ["reasoner 에 step-by-step 명시(중복 추론)", "페르소나 부풀리기"],
        "refs": ["api-docs.deepseek.com"],
    },
}


def render_guide_block(family: str) -> str:
    """한 계열의 distilled 가이드를 메타 프롬프트 주입용 텍스트 블록으로 렌더."""
    g = PROMPTING_GUIDES.get(family)
    if not g:
        return ""
    rules = "\n".join(f"  - {r}" for r in g["rules"])
    avoid = "\n".join(f"  - {r}" for r in g["avoid"])
    refs = "\n".join(f"  - {r}" for r in g["refs"])
    return (f"[{g['label']} 공식 가이드 distilled]\n한 줄 원칙: {g['one']}\n"
            f"규칙:\n{rules}\n피해야 할 관행:\n{avoid}\n출처:\n{refs}")
