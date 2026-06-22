"""프롬프트 빌더."""
from . import dictionaries as D

QMETA_VERSION = "qmeta@v31"
IMETA_VERSION = "imeta@v8"
LEGAL_VERSION = "legal@v3"

_JSON_GUARD = (
    "\n\n[출력 규칙] 반드시 유효한 JSON 객체 하나만 출력한다. "
    "코드펜스·설명·서론·후기를 일절 붙이지 않는다. 사전에 없는 값은 생성 금지."
)


# 품질 메타: 활성 프롬프트 버전(promptstore)에서 렌더. 코드 수정 없이 룰 편집 가능.
def quality_system(active_metas: list, service_group: str, version: str | None = None,
                   examples: str = "") -> str:
    from . import promptstore
    return promptstore.render_quality_system(active_metas, service_group, _JSON_GUARD,
                                             version, examples)


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
[출력] {{"reasons": ["메타ID", ...], "evidence": "근거"}}{_JSON_GUARD}"""


# 아이템 메타
def item_system(content) -> str:
    intents = " / ".join(D.intent_categories_for(content.displayServiceName))
    tier1 = " / ".join(D.IAB_TIER1)
    return f"""너는 유통 가능(G) 콘텐츠의 아이템 메타를 추출한다. 4단계를 순서대로 수행한다.

1) summary: 이 콘텐츠가 '무엇을 어떤 관점에서 다루는지' 한 문장으로 요약(주어+대상+관점).
2) entities: 핵심 단일 명사 1~3개(인물·기관·브랜드·개념). 고유명사 우선.
3) intent: 아래 사전값 중 1~2개만. 자유 생성 금지.
   [{intents}]
4) content_category: 각 엔티티를 IAB Tier1(필요시 Tier1/Tier2)로. Tier1 사전:
   [{tier1}]

[출력 형식]
{{"summary":"...","entities":["..."],"intent":["..."],
  "content_category":{{"엔티티":"Tier1 / Tier2"}}}}{_JSON_GUARD}"""


def item_user(content) -> str:
    return _content_block(content)


# 법령 메타 (옵션)
def legal_router_system() -> str:
    types = "\n".join(
        f"  - {c}: {v['label']} ({v['article']})" for c, v in D.LEGAL_HARM_TYPES.items()
    )
    return f"""너는 콘텐츠의 법령 위반 가능성을 라우팅한다. 아래 13종 중 의심되는 유형을
confidence(0~1)와 함께 모두 나열한다(복수 허용). 임계 0.3 미만은 제외.

[위반유형 사전: 밖은 금지]
{types}

[출력] {{"harm_types":[{{"code":"...","confidence":0.0}}]}}{_JSON_GUARD}"""


def legal_scorer_system(code: str) -> str:
    info = D.LEGAL_HARM_TYPES[code]
    return f"""너는 '{info['label']}'({info['article']}) 위반 정도를 스코어링한다.
A(구성요건 0~40) + B(제재 0~30) + C(맥락 0~30) 으로 채점한다.
B(제재수준)는 법정형 기준 고정 가중치이므로 보수적으로 부여한다.

[출력] {{"a":0,"b":0,"c":0,"evidence":"근거"}}{_JSON_GUARD}"""


def _content_block(content) -> str:
    return (
        f"displayServiceName: {content.displayServiceName}\n"
        f"title: {content.title}\n"
        f"subtitle: {content.subtitle}\n"
        f"body: {content.body}"
    )
