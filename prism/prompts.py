"""프롬프트 빌더.

원천(단계 지시)은 STAGE_DIRECTIVE 기본값을 두고, 프롬프트 스튜디오에서 단계별로
직접 편집(override)한다. 사전·스키마 같은 구조 비계(scaffolding)는 코드가 자동으로
덧붙여 깨지지 않게 한다. 보완은 배치 결과의 콘텐츠별 피드백이 LEARNED 로 누적되어
다음 추출부터 자동 반영된다(학습 루프).
"""
from . import dictionaries as D

QMETA_VERSION = "qmeta@v31"
IMETA_VERSION = "imeta@v9"   # v9: 기준 문서(contextual-meta-extraction v2.1) 코어 규칙 + 모델 계열 래핑
LEGAL_VERSION = "legal@v3"

_JSON_GUARD = (
    "\n\n[출력 규칙] 반드시 유효한 JSON 객체 하나만 출력한다. "
    "코드펜스·설명·서론·후기를 일절 붙이지 않는다. 사전에 없는 값은 생성 금지."
)


# ── 원천 단계 지시(편집 대상). 기본값 = 현재 동작 보존 ──
#   extract=추출(대식·신호해석) · analyze=분석(용희·메타) · review=검수(복실·품질) · judge=판정(딱지·법령)
#   extract/analyze 기본값 = 기준 문서(contextual-meta-extraction.md v2.1)의 코어 규칙 C1~C4 원문.
#   문서 갱신 시 meta_prompts.C*_RULES 와 함께 동기화한다.
STAGE_DIRECTIVE_DEFAULT = {
    "extract": None,   # 아래에서 meta_prompts 코어로 채움(C1 리드문 + C2 엔티티)
    "analyze": None,   # C3 인텐트 + C4 콘텐츠 카테고리

    "review": (
        "너는 콘텐츠 품질 필터다. 증거를 먼저 수집한 뒤, 임계를 충족한 메타만 골라낸다. "
        "사전에 정의된 메타 외에는 판정하지 않는다."
    ),
    "judge": (
        "너는 콘텐츠의 법령 위반 가능성을 판정한다. 사전에 정의된 위반유형만 사용하고, "
        "임계 미만은 제외한다. 제재수준 가중치는 법정형 기준으로 보수적으로 부여한다."
    ),
}

from . import meta_prompts as MP                     # 기준 문서 코어(순환 없음: MP는 dictionaries만 의존)
STAGE_DIRECTIVE_DEFAULT["extract"] = MP.C1_RULES + "\n\n" + MP.C2_RULES
STAGE_DIRECTIVE_DEFAULT["analyze"] = MP.C3_RULES + "\n\n" + MP.C4_RULES

# 단계별 override(프롬프트 스튜디오에서 채움). 빈 값이면 기본값 사용.
STAGE_DIRECTIVE = {"extract": "", "analyze": "", "review": "", "judge": ""}
# 학습 루프: 배치 결과 피드백에서 누적된 보정 지시(자동 반영). 사람이 직접 쓰지 않음.
LEARNED = {"extract": "", "analyze": "", "review": "", "judge": ""}

# 하위호환 별칭
EXTRA = STAGE_DIRECTIVE
EXTRA_INSTRUCTION = ""


def directive(stage: str) -> str:
    """단계 원천 지시(override 우선, 없으면 기본값)."""
    v = (STAGE_DIRECTIVE.get(stage) or "").strip()
    return v if v else STAGE_DIRECTIVE_DEFAULT.get(stage, "")


def stage_defaults() -> dict:
    """프롬프트 스튜디오 초기 표시용 기본 원천 지시."""
    return dict(STAGE_DIRECTIVE_DEFAULT)


def _learned(stage: str) -> str:
    v = (LEARNED.get(stage) or "").strip()
    return f"\n\n[학습 보정 · {stage}] 아래는 과거 평가 피드백에서 누적된 교정 지침이다. 우선 반영한다.\n{v}" if v else ""


# 하위호환: 기존 _extra(stage) 호출부 유지(= 학습 보정으로 의미 전환)
def _extra(stage: str) -> str:
    return _learned(stage)


# 품질 메타: 활성 프롬프트 버전(promptstore)에서 렌더. 코드 수정 없이 룰 편집 가능.
def quality_system(active_metas: list, service_group: str, version: str | None = None,
                   examples: str = "") -> str:
    from . import promptstore
    base = promptstore.render_quality_system(active_metas, service_group, _JSON_GUARD,
                                             version, examples)
    return f"[검수 지시] {directive('review')}\n\n{base}{_learned('review')}"


def quality_version() -> str:
    from . import promptstore
    return "qmeta@" + promptstore.active_name()


def quality_user(content) -> str:
    return _content_block(content)


# 분해형(옵션, A/B용): 메타를 4개 관심사 묶음으로 쪼개 좁은 규칙만 주입
QUALITY_GROUPS = {
    "commerce":  ["ad", "spam"],
    "harm":      ["sexual", "profanity", "graphic"],
    "format_val": ["format", "clickbait", "shallow"],
    "politic":   ["political", "hate", "gambling"],
}


def quality_group_system(group_key: str, active_metas: list, service_group: str) -> str:
    metas = [m for m in QUALITY_GROUPS[group_key] if m in active_metas]
    if not metas:
        return ""
    rules = "\n".join(f"  - {m}: {D.QUALITY_METAS[m]}" for m in metas)
    return f"""너는 콘텐츠 품질 필터의 '{group_key}' 전담 판정기다. 아래 메타만 평가한다.
서비스 그룹: {service_group}.

[담당 메타: 이 밖은 평가하지 않음]
{rules}

증거를 먼저 수집한 뒤, 임계 충족 메타만 골라낸다.
[출력] {{"reasons": ["메타ID", ...], "evidence": "근거"}}{_JSON_GUARD}""" + _learned("review")


def item_system(content, model: str = "") -> str:
    """아이템 메타(4필드 통합) 시스템 프롬프트.
    원천 지시(기본=기준 문서 C1~C4, 스튜디오 override 가능)를 모델 계열별 래퍼(meta_prompts)로 감싼다.
    사전 주입: 인텐트(범용①·② + 서비스 분기) · IAB Tier1/Tier2 전체."""
    intents = " / ".join(D.intent_categories_for(content.displayServiceName))
    iab = MP.iab_dictionary_text()
    core = f"{directive('extract')}\n\n{directive('analyze')}"
    learned = _learned("extract") + _learned("analyze")
    return MP.item_system(model, core, intents, iab, learned)


def item_user(content) -> str:
    return _content_block(content)


# 법령 메타 (옵션)
def legal_router_system() -> str:
    types = "\n".join(
        f"  - {c}: {v['label']} ({v['article']})" for c, v in D.LEGAL_HARM_TYPES.items()
    )
    return f"""{directive('judge')}
아래 13종 중 의심되는 유형을 confidence(0~1)와 함께 모두 나열한다(복수 허용). 임계 0.3 미만은 제외.

[위반유형 사전: 밖은 금지]
{types}

[출력] {{"harm_types":[{{"code":"...","confidence":0.0}}]}}{_JSON_GUARD}""" + _learned("judge")


def legal_scorer_system(code: str) -> str:
    info = D.LEGAL_HARM_TYPES[code]
    return f"""너는 '{info['label']}'({info['article']}) 위반 정도를 스코어링한다.
A(구성요건 0~40) + B(제재 0~30) + C(맥락 0~30) 으로 채점한다.
B(제재수준)는 법정형 기준 고정 가중치이므로 보수적으로 부여한다.

[출력] {{"a":0,"b":0,"c":0,"evidence":"근거"}}{_JSON_GUARD}""" + _learned("judge")


def _content_block(content) -> str:
    return (                                          # 입력 3필드 고정(1312): subtitle 미사용
        f"displayServiceName: {content.displayServiceName}\n"
        f"title: {content.title}\n"
        f"body: {content.body}"
    )
