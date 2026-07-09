"""모델별 아이템 메타 추출 프롬프트 · 기준: DNM 아이템 메타 계약(1312) + 인텐트·카테고리 사전 원문
+ contextual-meta-extraction.md v2.1 (모델별 쿡북 래퍼).

계약(공식 가이드): 입력 3필드(displayServiceName·title·body) 고정, **분리형 순차 4호출**
① 리드문 summary → ② 엔티티 entities → ③ 인텐트 intent(사전: 범용①·② + 서비스 분기)
→ ④ 콘텐츠 카테고리 content_category(사전: IAB Tier1/Tier2 전체 + 구분 기준).
출력 필드 4종은 다운스트림 계약 · 임의 추가·변경 금지. summary 가 빈 문자열이면 후속 호출 생략.

구조 원칙(수정 흐름):
- 코어 규칙(CALL_RULES)·골드 예시(GOLD) = **계약**. 수정은 기준 문서 개정 → 본 모듈 동기화로만.
- 모델 계열 래퍼(FAMILY_WRAPPER_DEFAULT) = **편집 단위**. 프롬프트 스튜디오에서 계열별로만 수정
  (config.family_wrappers 오버라이드, WRAPPER_OVERRIDES 로 주입).
- 최종 프롬프트 = 래퍼 템플릿에 {ROLE}/{SCHEMA}/{RULES}/{EXAMPLES}/{SELF_CHECK}/{LEARNED} 삽입.
"""
from __future__ import annotations
from . import dictionaries as D

CALLS = ("summary", "entities", "intent", "category")
CALL_LABELS = {"summary": "① 리드문", "entities": "② 엔티티", "intent": "③ 인텐트", "category": "④ 콘텐츠 카테고리"}

# ── 코어 규칙(계약 원문 · 갱신은 기준 문서 동기화로만) ──
CALL_RULES = {
    "summary": """# 리드문 정의
- 콘텐츠의 소비 맥락(왜·어떻게 소비되는가)을 콘텐츠 내용으로 구체화하여 생성한 핵심 문장 1개.
- 사용자 관점의 소비 목적과 CP 관점의 노출·전환 의도를 함께 반영.
- 본문 문장의 인용·발췌가 아니라 생성문. 이후 인텐트·콘텐츠 카테고리 판단의 기반 재료.

# 규칙
- 정확히 1문장, 평서형 종결.
- 핵심 고유명사·사실을 포함하되 과장·추측·평가 금지.
- body가 비어있으면 title만으로 생성. title·body 모두 비어 의미 생성이 불가능하면
  빈 문자열("")을 출력한다(하위 호출 생략 신호).""",
    "entities": """# 엔티티 정의
- 콘텐츠 내 대표적·반복적으로 사용된 명사. 단일 명사가 원칙이며,
  고유명사(인명·지명·조직명·작품명 등)는 단일 단위로 취급. 구문구·수식 명사구 금지.
- 복합 명사(둘 이상의 명사가 결합해 하나의 개념을 이루는 말 · 예: 전기차 보조금,
  국민연금 개혁, 반도체 클러스터)는 분해하지 말고 원형 그대로 하나의 엔티티로 취급한다.

# 정제 규칙 (순서대로 적용)
1. 행위어·상태어 제거 (예: 협상, 결렬, 발표, 논의).
2. 단독 식별력이 약한 일반어 제거 (예: 노사, 사후조정, 회의).
3. "인용 출처 vs 핵심 주체" 구분: 데이터·발언을 인용한 기관·기업은 제외.
   단, 기사의 행위 주체이거나 사건의 당사자이면 포함.
   판단 기준: "이 콘텐츠가 X에 관한 것인가, X를 인용한 것인가?"
4. 남은 후보 중 대표성 없는 것을 제거하고 핵심 엔티티만 남긴다.
   개수 상한은 없다(콘텐츠가 요구하는 만큼) · 대표성 높은 순으로 정렬한다.
- 동명이인은 고려하지 않는다. 표기 정규화·개체 연결은 적재 단계 소관.""",
    "intent": """# 절차
1. displayServiceName으로 서비스 카테고리를 분기한다(사전 정의 10개 정의값 · 아래 목록에 분기 반영됨).
   미정의 값이면 PGC 그룹으로 처리하고 범용 분류값만 부여한다(서비스 분류값 미부여).
2. 리드문이 나타내는 소비 맥락을 분류값에 매핑한다.

# 부여 원칙
- 범용 분류값(소비 방식 ①·형식 ② 합산) 0~2개 + 서비스 카테고리 분류값 0~2개 동시 매핑
  (총 1~3개 권장, 대표 첫 번째). 범용만 또는 서비스 분류값만 부여도 허용. ①과 ②는 교차 부여 가능.
- 아래 목록에 정의된 분류값 중에서만 선택한다. 자유 생성·임의 변형 금지.
- 근거가 명확하지 않으면 부여하지 않는다(과잉 매핑 방지). 동일 패턴은 동일 판정.
- 서비스 카테고리 경계 콘텐츠는 양쪽 분류값 동시 부여 가능.""",
    "category": """# 부여 원칙
- 콘텐츠에 실재하는 도메인을 N개(1개 이상) 부여한다.
  본문 핵심 도메인을 대표로 첫 번째에 배치하고, 부가 도메인을 뒤에 나열한다.
- 아래 사전에 정의된 항목(21개 Tier 1 + Custom Tier 2) 중에서만 선택한다.
  Tier 1 / Tier 2 까지만 표기. 자유 생성·Tier 3 표기 금지.
- 우선순위: 콘텐츠 맥락 > 개별 엔티티 고유 도메인.
  리드문·인텐트·엔티티와 본문 핵심을 종합해 콘텐츠 전체의 도메인을 결정한다.
- 근거가 약한 도메인은 부여하지 않는다(과잉 매핑 방지).
  사전에 없는 도메인이면 미분류로 두고 운영자 검토를 기다린다.""",
}

CALL_ROLES = {
    "summary": "너는 콘텐츠의 '리드문'을 생성하는 추출기다.",
    "entities": "너는 콘텐츠의 대표 '엔티티'를 추출·정제하는 추출기다.",
    "intent": "너는 리드문을 기반으로 콘텐츠의 세부 종류·속성 분류값('인텐트')을 부여하는 분류기다.",
    "category": "너는 리드문·인텐트·엔티티를 종합하여 콘텐츠의 대표 IAB 카테고리를 부여하는 분류기다.",
}

CALL_SCHEMAS = {
    "summary": '{"summary": string}  (생성 불가 시 빈 문자열)',
    "entities": '{"entities": string[]}  (핵심만 · 대표 첫 번째)',
    "intent": '{"intent": string[]}  (대표 첫 번째 · 목록 외 값 금지)',
    "category": '{"content_category": string[]}  ("Tier 1 / Tier 2" 표기만 · 대표 첫 번째)',
}

CALL_SELF_CHECK = {
    "summary": "1. 정확히 1문장, 평서형 종결인가\n2. 과장·추측·평가가 없는가\n3. CRITICAL: 출력이 JSON 한 줄뿐인가",
    "entities": "1. 정제 규칙 1→2→3→4를 순서대로 적용했는가\n2. 인용 출처가 섞이지 않았는가\n3. 모든 항목이 핵심(대표성)인가 · 복합 명사를 분해하지 않았는가\n4. CRITICAL: JSON 한 줄뿐인가",
    "intent": "1. 모든 값이 목록 내 표기와 정확히 일치하는가 (CRITICAL)\n2. 대표 분류값이 첫 번째인가\n3. 근거 약한 매핑이 없는가",
    "category": "1. 모든 값이 사전 내 경로와 정확히 일치하는가 (CRITICAL)\n2. 대표 도메인이 첫 번째인가\n3. 콘텐츠 맥락을 엔티티 고유 도메인보다 우선했는가",
}

# ── 골드 예시(계약 원문 1312 예시 1~4 + 경계 보강 · 분류값은 사전 최신 표기) ──
GOLD = [
    {"in": 'displayServiceName="스포츠" · title="\'나는 유로파의 제왕이 아니다\'… \'UEL 통산 4회 우승\' 에메리 감독의 겸손, \'빌라와 함께 우승한다\'"',
     "summary": "아스톤 빌라의 우나이 에메리 감독이 UEFA 유로파리그 결승전을 앞두고 과거 통산 4회 우승의 '유로파리그 왕' 별명을 부인하며, 빌라와 함께 프라이부르크를 꺾고 30년 만의 메이저 트로피를 차지하겠다는 각오를 기자회견을 통해 밝힌 내용을 전한다.",
     "entities": ["우나이 에메리", "아스톤 빌라", "유로파리그"],
     "intent": ["인터뷰", "경기 프리뷰"],
     "category": ["Sports / Soccer (International)"],
     "note": "감독 인터뷰는 범용② '인터뷰' · 해외 감독·대회 → Soccer (International)"},
    {"in": 'displayServiceName="뉴스" · title="삼성전자 노사 협상 끝내 결렬, 노조 21일부터 총파업" · body="중앙노동위원회 2차 사후조정 최종회의에서 노사가 합의에 이르지 못하고 협상이 결렬됐다. 노동조합은 다음 날인 21일부터 총파업에 돌입하기로 결정했다."',
     "summary": "삼성전자 노사가 중앙노동위원회 2차 사후조정 최종회의에서 끝내 합의에 이르지 못하고 협상이 결렬됨에 따라, 노동조합이 다음 날인 21일부터 총파업에 돌입하기로 결정한 사실을 속보로 전한다.",
     "entities": ["삼성전자", "총파업", "중앙노동위원회"],
     "intent": ["속보·단신", "노동·사회 이슈"],
     "category": ["Business and Finance / Industries", "News and Politics / Society"],
     "note": "엔티티 후보 8개 중 정제(협상·결렬=행위어, 노사·사후조정=일반어) · 기업 엔티티의 노사 맥락 → Industries 대표"},
    {"in": 'displayServiceName="뉴스" · title="고령운전자 급가속 사고 막는다…페달 오조작 방지장치 2차 보급 본격화"',
     "summary": "경찰청·손해보험협회·한국교통안전공단이 고령운전자 페달 오조작 사고 예방을 위해 전국 7개 광역시 759명을 대상으로 방지장치 설치를 완료하고, 오는 6월부터 주행 데이터 기반 효과 검증에 돌입하는 2차 보급사업의 추진 현황과 정책 배경을 전한다.",
     "entities": ["페달 오조작 방지장치", "고령운전자", "손해보험협회"],
     "intent": ["정책·행정", "노동·사회 이슈"],
     "category": ["News and Politics / Politics", "News and Politics / Society"],
     "note": "자동차 안전 '정책·보급사업' → Politics 대표(Automotive 아님) · 고령운전자=인구 그룹 → Society 부가"},
    {"in": 'displayServiceName="뉴스" · title="Z세대 \'집보다 새 차가 더 현실적\'…자동차 관심 없다는 통념 깨져"',
     "summary": "마쓰다 설문조사 결과를 바탕으로 Z세대가 주택 구매보다 신차 구매를 우선시하며, 첨단 안전 사양·직관적 기술·프리미엄 오디오 시스템을 핵심 평가 기준으로 삼아 차량을 디지털 라이프스타일이 결합된 공간으로 인식하는 세대별 소비 트렌드를 분석한다.",
     "entities": ["Z세대", "마쓰다", "신차"],
     "intent": ["심층 분석", "트렌드·시장 분석"],
     "category": ["Automotive / Auto Type"],
     "note": "설문 인용 기관(마쓰다)이지만 조사 주체·소재로 본문 핵심 → 포함 · 차종·신차 맥락 대표 → Auto Type"},
    {"in": 'displayServiceName="뉴스" · title="성폭행 의혹 제기 美 의원, 캘리포니아 주지사 출마 포기(종합)"',
     "summary": "성폭행 의혹이 제기된 미국 민주당 하원의원 에릭 스월웰이 캘리포니아 주지사 선거 출마를 포기한 사실과 당내 파장을 전한다.",
     "entities": ["에릭 스월웰", "캘리포니아", "민주당"],
     "intent": ["속보·사건 추적", "사건 경과 보도"],
     "category": ["News and Politics / Politics", "News and Politics / Crime"],
     "note": "모호 엔티티(의혹·선거)는 본문 맥락으로 분기 · 주지사 선거 대표 → Politics, 성폭행 의혹 → Crime 부가"},
]

_FIELD_KEY = {"summary": "summary", "entities": "entities", "intent": "intent", "category": "content_category"}


def gold_examples(call: str | None = None) -> str:
    """골드 예시 텍스트. call 지정 시 해당 호출의 입·출력만, None 이면 4필드 통합."""
    out = []
    for i, ex in enumerate(GOLD, 1):
        if call is None:
            body = (f'출력: {{"summary": "{ex["summary"]}", "entities": {_ja(ex["entities"])}, '
                    f'"intent": {_ja(ex["intent"])}, "content_category": {_ja(ex["category"])}}}')
        else:
            v = ex["summary"] if call == "summary" else ex[call if call != "category" else "category"]
            body = f'출력: {{"{_FIELD_KEY[call]}": ' + (f'"{v}"' if call == "summary" else _ja(v)) + "}"
        out.append(f"[예시 {i}] 입력: {ex['in']}\n{body}\n({ex['note']})")
    return "\n\n".join(out)


def _ja(xs) -> str:
    import json
    return json.dumps(xs, ensure_ascii=False)


# ── 사전 주입 텍스트 ──

def intent_dictionary_text(display_service_name: str) -> str:
    """③ 인텐트 사전: 범용①(소비 방식) + 범용②(형식·전달) + 서비스 분기분(설명 병기)."""
    svc = D._service_key(display_service_name)
    lines = ["[범용 ① 소비 방식 · 전 서비스 공통]", " / ".join(D.INTENT_CATEGORIES_UNIVERSAL),
             "- 관점 축 구분: 한쪽 논조가 뚜렷하면 옹호·지지(지지 논조) 또는 반박·비판(반대·비판 논조),"
             " 찬반이 병렬로 오가면 의견·논쟁."
             " 기존 정책·주장을 반박하며 대안을 옹호하는 콘텐츠(사설·칼럼 전형)는 비판 대상이"
             " 논지의 출발점이므로 반박·비판을 우선한다.",
             "", "[범용 ② 형식·전달 형태 · 전 서비스 공통 · ①과 교차 부여 가능]",
             " / ".join(D.INTENT_FORM_UNIVERSAL)]
    svc_vals = D.INTENT_CATEGORIES_BY_SERVICE.get(svc, [])
    if svc_vals:
        lines += ["", f"[서비스 카테고리 분류값 · {svc}]"]
        for v in svc_vals:
            desc = D.INTENT_VALUE_DEFS.get(v, "")
            lines.append(f"- {v}" + (f": {desc}" if desc else ""))
    else:
        lines += ["", "[서비스 카테고리 분류값] displayServiceName 미정의 → 범용 분류값만 부여"]
    return "\n".join(lines)


def iab_dictionary_text(with_criteria: bool = True) -> str:
    """④ IAB 카테고리 사전: Tier1(국문 설명) + Tier2 경로 전체 + 카테고리별 구분 기준."""
    lines = []
    for t1 in D.IAB_TIER1:
        desc = getattr(D, "IAB_TIER1_DESC", {}).get(t1, "")
        t2s = D.CONTENT_CATEGORY_TIER2.get(t1) or []
        lines.append(f"- {t1}" + (f" ({desc})" if desc else "") + (f": {' · '.join(t2s)}" if t2s else ""))
    txt = "\n".join(lines)
    crit = getattr(D, "CATEGORY_CRITERIA", {})
    if with_criteria and crit:
        c = "\n".join(f"- {k}: {v}" for k, v in crit.items())
        txt += f"\n\n[구분 기준 · 경계 판정 규칙]\n{c}"
    return txt


def call_dictionary(call: str, display_service_name: str = "") -> str:
    """호출별 사전(어느 호출도 두 사전을 동시 적재하지 않음 · 캐싱 설계)."""
    if call == "intent":
        return "[사전 · 인텐트 분류값: 이 목록의 값만 사용]\n" + intent_dictionary_text(display_service_name)
    if call == "category":
        return "[사전 · IAB 콘텐츠 카테고리(Tier 1: Tier 2 목록): 이 사전의 경로만 사용]\n" + iab_dictionary_text()
    return ""


# ── 모델 계열 래퍼(편집 단위 · 프롬프트 스튜디오에서 계열별 수정) ──
FAMILIES = ("gpt", "gemini", "claude", "solar", "default")

FAMILY_WRAPPER_DEFAULT = {
    "gpt": """{ROLE}

<output_contract>
출력은 JSON 한 줄만. 다른 텍스트·설명·코드펜스 금지.
스키마: {SCHEMA}
사전이 주어진 경우 목록 외 값을 출력하지 않는다. 배열은 대표 값을 첫 번째에 둔다.
</output_contract>

<rules>
{RULES}
</rules>

<examples>
{EXAMPLES}
</examples>

<success_criteria>
{SELF_CHECK}
</success_criteria>{LEARNED}""",
    "gemini": """{ROLE} 항상 JSON 한 줄만 출력한다. 출력 스키마: {SCHEMA}

<rules>
{RULES}
</rules>

<examples>
{EXAMPLES}
</examples>

입력에 근거하여 추출하라. 사전이 주어진 경우 목록 외 값은 사용하지 않는다.
배열은 대표 값을 첫 번째에 둔다. JSON 외 텍스트 금지.{LEARNED}""",
    "claude": """{ROLE}

<background>
리드문은 이후 분류 판단의 기반 재료이고, 엔티티는 개체 사전 적재와 토픽 클러스터링의 입력 신호다.
인텐트·카테고리는 사전 외 값이 생성되면 다운스트림 매칭이 전부 실패하므로 목록 내 선택이 유일한 규칙이며,
개별 엔티티의 고유 도메인보다 콘텐츠 맥락이 우선한다.
</background>

<rules>
{RULES}
</rules>

<examples>
{EXAMPLES}
</examples>

<output>
JSON 한 줄만 출력한다: {SCHEMA}
배열은 대표 값을 첫 번째에 둔다. 위 규칙은 매 콘텐츠에 동일하게 적용한다.
</output>{LEARNED}""",
    "solar": """# 역할
{ROLE}

# CRITICAL · 출력 규칙
출력은 반드시 JSON 한 줄. 다른 텍스트·설명·코드펜스 절대 금지.
스키마: {SCHEMA}
MUST: 사전이 주어진 경우 정의된 값만 사용한다. 목록 외 값 생성 금지.

# 규칙
{RULES}

# 예시
{EXAMPLES}

# 자가 검증 (출력 전 확인, 과정은 출력하지 않는다)
{SELF_CHECK}{LEARNED}""",
}
FAMILY_WRAPPER_DEFAULT["default"] = FAMILY_WRAPPER_DEFAULT["gpt"]

# 프롬프트 스튜디오(관리자) 오버라이드: {family: template}. serve.sync_prompt 가 config 에서 주입.
WRAPPER_OVERRIDES: dict = {}


def family_of(model: str) -> str:
    """모델 id → 프롬프팅 계열. 라우터 경로(provider/model)와 bare id 모두 처리."""
    m = (model or "").lower().rsplit("/", 1)[-1]
    if m.startswith(("gpt", "o1", "o3", "o4")):
        return "gpt"
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith("claude"):
        return "claude"
    if m.startswith("solar"):
        return "solar"
    return "default"


def wrapper_for(family: str) -> str:
    ov = (WRAPPER_OVERRIDES.get(family) or "").strip()
    return ov if ov else FAMILY_WRAPPER_DEFAULT.get(family, FAMILY_WRAPPER_DEFAULT["default"])


def _compose(family: str, role: str, schema: str, rules: str, examples: str,
             self_check: str, learned: str) -> str:
    t = wrapper_for(family)
    for k, v in (("{ROLE}", role), ("{SCHEMA}", schema), ("{RULES}", rules),
                 ("{EXAMPLES}", examples), ("{SELF_CHECK}", self_check), ("{LEARNED}", learned)):
        t = t.replace(k, v)
    return t


def call_system(model: str, call: str, display_service_name: str = "", learned: str = "",
                rules_override: str = "") -> str:
    """분리형 호출(①~④)의 시스템 프롬프트. rules_override 는 스튜디오 원천 지시 교체 시."""
    rules = (rules_override or CALL_RULES[call]).strip()
    d = call_dictionary(call, display_service_name)
    if d:
        rules = f"{rules}\n\n{d}"
    return _compose(family_of(model), CALL_ROLES[call], CALL_SCHEMAS[call], rules,
                    gold_examples(call), CALL_SELF_CHECK[call], learned)


def call_user(call: str, content, prior: dict) -> str:
    """분리형 호출의 user 메시지(문서 §2.6 주입 변수)."""
    dsn = getattr(content, "displayServiceName", "") or ""
    title = getattr(content, "title", "") or ""
    body = getattr(content, "body", "") or ""
    if call == "summary":
        return f"displayServiceName: {dsn}\ntitle: {title}\nbody: {body}"
    if call == "entities":
        return f"title: {title}\nbody: {body}"
    if call == "intent":
        return f"displayServiceName: {dsn}\nsummary: {prior.get('summary', '')}"
    return (f"summary: {prior.get('summary', '')}\nintent: {_ja(prior.get('intent', []))}\n"
            f"entities: {_ja(prior.get('entities', []))}\nbody: {body}")


MERGED_SCHEMA = ('{"summary": string, "entities": string[] (핵심만 · 개수 상한 없음, 대표 첫 번째), '
                 '"intent": string[], "content_category": string[] ("Tier 1 / Tier 2" 표기)}')
MERGED_SELF_CHECK = ("1. summary 가 정확히 1문장 평서형이고 과장·추측이 없는가\n"
                     "2. entities 가 정제 규칙 1→2→3→4를 거쳐 핵심만 남았는가(개수 상한 없음 · 대표성 높은 순)\n"
                     "3. intent·content_category 의 모든 값이 사전 내 표기와 정확히 일치하는가 (CRITICAL)\n"
                     "4. 각 배열의 대표 값이 첫 번째인가\n5. CRITICAL: 출력이 JSON 한 줄뿐인가")


def item_system(model: str, core: str, intents_txt: str, iab_txt: str, learned: str = "") -> str:
    """통합 1콜(폴백 모드) 시스템 프롬프트. core = 원천 지시(기본 C1~C4)."""
    dicts = (f"[사전 · 인텐트: 이 목록의 값만 사용]\n{intents_txt}\n\n"
             f"[사전 · IAB 콘텐츠 카테고리(Tier 1: Tier 2 목록): 이 사전의 경로만 사용]\n{iab_txt}")
    role = "너는 콘텐츠의 아이템 메타(리드문·엔티티·인텐트·콘텐츠 카테고리)를 추출하는 추출기다."
    return _compose(family_of(model), role, MERGED_SCHEMA, f"{core.strip()}\n\n{dicts}",
                    gold_examples(None), MERGED_SELF_CHECK, learned)
