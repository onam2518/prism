"""모델별 아이템 메타 추출 프롬프트 — 기준 문서: contextual-meta-extraction.md v2.1 (2026-07-02).

원천 계약(아이템 메타 프롬프트 가이드): 입력 3필드(displayServiceName·title·body) 고정,
분리형 순차 4호출(① 리드문 summary → ② 엔티티 entities → ③ 인텐트 intent → ④ 콘텐츠 카테고리
content_category). 출력 필드 4종은 다운스트림 계약 — 임의 추가·변경 금지.

현 파이프라인(agents.run_item)은 단일 호출로 4필드를 통합 추출하므로, 본 모듈은 문서의
코어 규칙(C1~C4)·골드 예시를 원문 유지한 채 모델 계열별 래핑(GPT/Gemini/Claude/Solar
쿡북 관례)만 적용해 시스템 프롬프트를 만든다. 분리형 4호출 전환 시 C*_RULES·GOLD_EXAMPLES
를 그대로 콜 단위로 재사용한다.

사전 주입은 코드가 담당: 인텐트(범용①·② + displayServiceName 서비스 분기 · dictionaries
.intent_categories_for)와 IAB 카테고리(Tier1/Tier2 전체 · iab_dictionary_text).
"""
from . import dictionaries as D

# ── 코어 규칙(문서 §2 원문 · 갱신 시 이 블록만 동기화하면 전 모델 반영) ──

C1_RULES = """# 리드문 정의
- 콘텐츠의 소비 맥락(왜·어떻게 소비되는가)을 콘텐츠 내용으로 구체화하여 생성한 핵심 문장 1개.
- 사용자 관점의 소비 목적과 CP 관점의 노출·전환 의도를 함께 반영.
- 본문 문장의 인용·발췌가 아니라 생성문. 이후 인텐트·콘텐츠 카테고리 판단의 기반 재료.

# 리드문 규칙
- 정확히 1문장, 평서형 종결.
- 핵심 고유명사·사실을 포함하되 과장·추측·평가 금지.
- body가 비어있으면 title만으로 생성. title·body 모두 비어 의미 생성이 불가능하면
  빈 문자열("")을 출력한다(이 경우 나머지 필드도 빈 값)."""

C2_RULES = """# 엔티티 정의
- 콘텐츠 내 대표적·반복적으로 사용된 명사. 단일 명사가 원칙이며,
  고유명사(인명·지명·조직명·작품명 등)는 단일 단위로 취급. 구문구·수식 명사구 금지.

# 엔티티 정제 규칙 (순서대로 적용)
1. 행위어·상태어 제거 (예: 협상, 결렬, 발표, 논의).
2. 단독 식별력이 약한 일반어 제거 (예: 노사, 사후조정, 회의).
3. "인용 출처 vs 핵심 주체" 구분: 데이터·발언을 인용한 기관·기업은 제외.
   단, 기사의 행위 주체이거나 사건의 당사자이면 포함.
   판단 기준: "이 콘텐츠가 X에 관한 것인가, X를 인용한 것인가?"
4. 대표성 높은 순으로 최종 1~3개로 축소.
- 동명이인은 고려하지 않는다. 표기 정규화·개체 연결은 적재 단계 소관."""

C3_RULES = """# 인텐트 절차
1. displayServiceName으로 서비스 카테고리를 분기한다(사전 정의 서비스 분기 · 아래 목록에 반영됨).
   미정의 값이면 PGC 그룹으로 처리하고 범용 분류값만 부여한다.
2. 리드문이 나타내는 소비 맥락을 분류값에 매핑한다.

# 인텐트 부여 원칙
- 범용 분류값(소비 방식 ①·형식 ② 합산) 0~2개 + 서비스 카테고리 분류값 0~2개 동시 매핑
  (총 1~3개 권장, 대표 첫 번째). 범용만 또는 서비스 분류값만 부여도 허용. ①과 ②는 교차 부여 가능.
- 아래 목록에 정의된 분류값 중에서만 선택한다. 자유 생성·임의 변형 금지.
- 근거가 명확하지 않으면 부여하지 않는다(과잉 매핑 방지). 동일 패턴은 동일 판정.
- 서비스 카테고리 경계 콘텐츠는 양쪽 분류값 동시 부여 가능."""

C4_RULES = """# 콘텐츠 카테고리 부여 원칙
- 콘텐츠에 실재하는 도메인을 N개(1개 이상) 부여한다.
  본문 핵심 도메인을 대표로 첫 번째에 배치하고, 부가 도메인을 뒤에 나열한다.
- 아래 사전에 정의된 항목(Tier 1 + Tier 2) 중에서만 선택한다.
  Tier 1 / Tier 2 까지만 표기. 자유 생성·Tier 3 표기 금지.
- 우선순위: 콘텐츠 맥락 > 개별 엔티티 고유 도메인.
  리드문·인텐트·엔티티와 본문 핵심을 종합해 콘텐츠 전체의 도메인을 결정한다.
- 근거가 약한 도메인은 부여하지 않는다(과잉 매핑 방지).
  사전에 없는 도메인이면 미분류로 두고 운영자 검토를 기다린다."""

# 골드 예시(문서 §2.5 · 예시 A=공식 가이드, 예시 B=경계 케이스)
GOLD_EXAMPLES = """[예시 A]
입력: displayServiceName="뉴스"
      title="삼성전자 노사 협상 끝내 결렬, 노조 21일부터 총파업"
      body="중앙노동위원회 2차 사후조정 최종회의에서 노사가 합의에 이르지 못하고 협상이 결렬됐다. 노동조합은 다음 날인 21일부터 총파업에 돌입하기로 결정했다."
출력: {"summary": "삼성전자 노사가 중앙노동위원회 2차 사후조정 최종회의에서 끝내 합의에 이르지 못하고 협상이 결렬됨에 따라, 노동조합이 다음 날인 21일부터 총파업에 돌입하기로 결정한 사실을 속보로 전한다.",
      "entities": ["삼성전자", "총파업", "중앙노동위원회"],
      "intent": ["속보·단신", "노동·사회 이슈"],
      "content_category": ["Business and Finance / Industries", "News and Politics / Society"]}
(엔티티 후보 8개 중 정제: 협상·결렬=행위어 제거, 노사·사후조정=일반어 제거 · 기업 엔티티의 노사 맥락 → Industries 대표)

[예시 B]
입력: displayServiceName="뉴스"
      title="성폭행 의혹 제기 美 의원, 캘리포니아 주지사 출마 포기(종합)"
      body="(에릭 스월웰 민주당 하원의원의 출마 포기 경위와 당내 파장)"
출력: {"summary": "성폭행 의혹이 제기된 미국 민주당 하원의원 에릭 스월웰이 캘리포니아 주지사 선거 출마를 포기한 사실과 당내 파장을 전한다.",
      "entities": ["에릭 스월웰", "캘리포니아", "민주당"],
      "intent": ["속보·사건 추적", "사건 경과 보도"],
      "content_category": ["News and Politics / Politics", "News and Politics / Crime"]}
(의혹·선거·출마=일반어/행위어 제거 · 주지사 선거 맥락 대표 → Politics, 성폭행 의혹 → Crime 부가)"""

# 통합 1콜 출력 계약(4필드 · 다운스트림 계약, 임의 변경 금지)
OUTPUT_SCHEMA = ('{"summary": string, "entities": string[] (1~3개), "intent": string[], '
                 '"content_category": string[] ("Tier 1 / Tier 2" 표기)}')


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


def iab_dictionary_text() -> str:
    """IAB 카테고리 사전 주입 텍스트: Tier1 전체 + Tier1/Tier2 경로 전체."""
    lines = []
    for t1 in D.IAB_TIER1:
        t2s = D.CONTENT_CATEGORY_TIER2.get(t1) or []
        lines.append(f"- {t1}" + (f": {' · '.join(t2s)}" if t2s else ""))
    return "\n".join(lines)


def _dict_block(intents_txt: str, iab_txt: str) -> str:
    return (f"[사전 · 인텐트: 이 목록의 값만 사용]\n{intents_txt}\n\n"
            f"[사전 · IAB 콘텐츠 카테고리(Tier 1: Tier 2 목록): 이 사전의 경로만 사용]\n{iab_txt}")


def item_system(model: str, core: str, intents_txt: str, iab_txt: str, learned: str = "") -> str:
    """모델 계열별 통합 추출 시스템 프롬프트(문서 §3~§6 래핑 관례).

    core = 원천 지시(기본 C1~C4 · 프롬프트 스튜디오 override 가능)."""
    fam = family_of(model)
    dicts = _dict_block(intents_txt, iab_txt)
    if fam == "solar":
        # §6: `#` 헤더 구조 + CRITICAL 강조 + 자가 검증(핵심 제약 앞뒤 반복 배치)
        return f"""# 역할
너는 콘텐츠의 아이템 메타(리드문·엔티티·인텐트·콘텐츠 카테고리)를 추출하는 추출기다.

# CRITICAL — 출력 규칙
출력은 반드시 JSON 한 줄. 다른 텍스트·설명·코드펜스 절대 금지.
스키마: {OUTPUT_SCHEMA}
MUST: intent·content_category 는 아래 사전에 정의된 값만 사용한다. 사전 외 값 생성 금지.

# 규칙
{core}

{dicts}

# 예시
{GOLD_EXAMPLES}

# 자가 검증 (출력 전 확인, 과정은 출력하지 않는다)
1. summary 가 정확히 1문장 평서형이고 과장·추측이 없는가
2. entities 가 정제 규칙 1→2→3→4를 거친 1~3개인가
3. intent·content_category 의 모든 값이 사전 내 표기와 정확히 일치하는가 (CRITICAL)
4. 각 배열의 대표 값이 첫 번째인가
5. CRITICAL: 출력이 JSON 한 줄뿐인가{learned}"""
    if fam == "claude":
        # §5: <background> 로 이유 제공 + 담백한 평서형(과격 지시 금지) + <output>
        return f"""너는 콘텐츠의 아이템 메타(리드문·엔티티·인텐트·콘텐츠 카테고리)를 추출하는 어시스턴트다.

<background>
리드문은 이후 인텐트·카테고리 판단의 기반 재료이고, 엔티티는 개체 사전 적재와 토픽 클러스터링의
입력 신호다. 인텐트·카테고리는 사전 외 값이 생성되면 다운스트림 매칭이 전부 실패하므로,
목록 내 선택이 유일한 규칙이다. 개별 엔티티의 고유 도메인보다 콘텐츠 맥락이 우선한다.
</background>

<rules>
{core}

{dicts}
</rules>

<examples>
{GOLD_EXAMPLES}
</examples>

<output>
JSON 한 줄만 출력한다: {OUTPUT_SCHEMA}
각 배열은 대표 값을 첫 번째에 둔다. 위 규칙은 매 콘텐츠에 동일하게 적용한다.
</output>{learned}"""
    if fam == "gemini":
        # §4: 직설·구조화, 명시적 스키마 재명시(데이터 선행 앵커링은 user 블록 소관)
        return f"""너는 콘텐츠의 아이템 메타(리드문·엔티티·인텐트·콘텐츠 카테고리)를 추출하는 추출기다.
항상 JSON 한 줄만 출력한다. 출력 스키마: {OUTPUT_SCHEMA}

<rules>
{core}

{dicts}
</rules>

<examples>
{GOLD_EXAMPLES}
</examples>

입력에 근거하여 4필드를 추출하라. 사전에 없는 intent·content_category 값은 사용하지 않는다.
각 배열은 대표 값을 첫 번째에 둔다. JSON 외 텍스트 금지.{learned}"""
    # gpt · default — §3: role → output_contract → rules(사전 포함) → examples (캐시 프리픽스 안정화)
    return f"""너는 콘텐츠의 아이템 메타(리드문·엔티티·인텐트·콘텐츠 카테고리)를 추출하는 추출기다.

<output_contract>
출력은 JSON 한 줄만. 다른 텍스트·설명·코드펜스 금지.
스키마: {OUTPUT_SCHEMA}
intent·content_category 는 아래 사전 목록에 없는 값을 출력하지 않는다.
각 배열은 대표 값을 첫 번째에 둔다.
</output_contract>

<rules>
{core}

{dicts}
</rules>

<examples>
{GOLD_EXAMPLES}
</examples>

<success_criteria>
- summary: 1문장 평서형 · 과장·추측·평가 없음
- entities: 정제 규칙 순서 적용 후 1~3개
- intent·content_category: 모든 값이 사전 내 표기와 일치 · 콘텐츠 맥락 우선
</success_criteria>{learned}"""
