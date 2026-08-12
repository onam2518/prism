"""모델별 아이템 메타 추출 프롬프트 · 기준: DNM 아이템 메타 계약(1312) + 인텐트·카테고리 사전 원문
+ contextual-meta-extraction.md v2.1 (모델별 쿡북 래퍼).

계약(공식 가이드): 입력 3필드(displayServiceName·title·body) 고정, **분리형 순차 4호출**
① 리드문 summary → ② 엔티티 entities → ③ 인텐트 intent(사전: 범용①·② + 서비스 분기)
→ ④ 콘텐츠 카테고리 content_category(사전: IAB Tier1/Tier2 전체 + 구분 기준).
출력 필드 4종은 다운스트림 계약 · 임의 추가·변경 금지. summary 가 빈 문자열이면 후속 호출 생략.

입력 계약 해석 개정(2026-08-03 · imeta@v16 · 왜 바꿨나):
- '입력 3필드 고정'은 **추출 입력 원천**이 이 3필드뿐이라는 뜻이며, 콜별로 그 3필드를 어떻게
  투영해 넣을지까지 고정한 규정이 아니다(② 엔티티는 애초에 dsn 을 넣지 않는 등 콜별 투영은 원래 가변).
- ③ 인텐트는 종래 dsn+summary 만 받아, 형식·전달 수단을 보는 범용② 분류값('포토·영상 중심'·
  '현장취재·르포' 등)의 판정 근거가 구조적으로 없었다. → title · 본문 글자수 · 본문 도입부 발췌를
  추가 투영한다(원천은 여전히 3필드 · 새 필드를 인입에 요구하지 않는다).
- 추가로 **참조 필드** image_urls(정체성 해시 불포함 · source_url 과 같은 참조 패턴)에서 파생한
  이미지 수를 신호로 덧붙인다. 값이 비어 있으면 **'0장'으로 단정하지 않고 '정보 없음'으로 전달**한다
  (운영 인입이 아직 image_urls 를 채우지 않는 상태 · '정보 없음' ≠ '0장' · 오판정 방지).
- **출력 4필드 다운스트림 계약은 그대로다.** 바뀐 것은 ③ 콜의 user 메시지 투영뿐이다.

구조 원칙(수정 흐름):
- 코어 규칙(CALL_RULES)·골드 예시(GOLD) = **계약**. 수정은 기준 문서 개정 → 본 모듈 동기화로만.
- 모델 계열 래퍼(FAMILY_WRAPPER_DEFAULT) = **편집 단위**. 프롬프트 스튜디오에서 계열별로만 수정
  (config.family_wrappers 오버라이드, WRAPPER_OVERRIDES 로 주입).
- 최종 프롬프트 = 래퍼 템플릿에 {ROLE}/{SCHEMA}/{RULES}/{EXAMPLES}/{SELF_CHECK}/{LEARNED} 삽입.
"""
from __future__ import annotations
import functools
import json

from . import dictionaries as D

CALLS = ("summary", "entities", "intent", "category")

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
- 범용 분류값(소비 방식 ①·형식 ②)과 서비스 카테고리 분류값을 동시 매핑한다.
  개수 상한은 없다(근거가 명확한 만큼 · 핵심만) · 대표성 높은 순으로 정렬한다(대표 첫 번째).
  범용만 또는 서비스 분류값만 부여도 허용. ①과 ②는 교차 부여 가능.
- 아래 목록에 정의된 분류값 중에서만 선택한다. 자유 생성·임의 변형 금지.
- 근거가 명확하지 않으면 부여하지 않는다(과잉 매핑 방지). 동일 패턴은 동일 판정.
- 서비스 카테고리 경계 콘텐츠는 양쪽 분류값 동시 부여 가능.
- 포토·영상 중심(2026-08-03 운영파트 합의 · 260715 '캡션-본문 연관' 규칙을 대체):
  이 분류값의 목적은 이미지 구좌(편성)에 넣을 콘텐츠를 골라내는 것이다. 다른 분류값으로도 쉽게
  갈리는 판정은 효용이 없으므로 사진 장수·본문 분량 같은 정량 임계로 판정하지 않는다.
  · 부여: 기자·필자가 직접 촬영한 것으로 읽히는 현장 사진이 콘텐츠의 핵심 전달 수단인 것.
    사진이 한 장뿐이어도 그 사진 자체가 콘텐츠의 목적이면 부여한다(예: '오늘의 1면 사진'·
    '고양이 눈' 류 사진 코너). 본문이 길든 짧든, 중간에 그래픽·도표가 섞여 있든 현장·심층 취재
    성격이고 사진이 축이면 부여한다. 화보형·직접 촬영 사진이 여러 장인 인터뷰도 포함한다.
  · 미부여(아래 여섯 유형은 부여하지 않는다):
    (1) 글쓴이 프로필 사진이 대표 이미지인 칼럼·시론
    (2) 이미지가 있어도 사진이 내용의 주가 아닌 것(해설·팩트체크 등 텍스트 논지가 중심)
    (3) 직접 촬영이 아닌 사진(제공·자료·외부 출처)으로만 이루어진 인터뷰
    (4) 생성형 AI 이미지만 있는 것
    (5) 사진 품질이 낮거나 사진이 콘텐츠에서 중요하지 않은 것
    (6) 현장성 없는 단순 보도
  · 판정 근거는 입력에서 실제로 읽어낼 수 있는 것에 한정한다: 제목의 사진 코너 표지
    ('[현장]'·'[포착]'·'사진'·'화보' 류), 리드문·본문의 현장 방문·직접 취재·촬영 정황 서술.
    직접 촬영 여부·사진 품질·생성형 AI 여부를 입력에서 확인할 수 없으면 추측하지 말고
    부여하지 않는다(위 '근거가 명확하지 않으면 부여하지 않는다' 원칙 적용).
  · 함께 주어지는 본문 글자수·이미지 수는 참고 신호일 뿐 임계가 아니다. 이미지 수가
    '정보 없음'이면 이미지가 없다는 뜻이 아니므로, 그것만을 이유로 미부여하지 않는다.
- 포토·영상 중심의 경계(무분별 병기 금지 · 변별력 확보):
  · '현장취재·르포'는 취재 방식(현장 방문), '포토·영상 중심'은 전달 수단(사진)이다.
    현장 취재라도 텍스트 서술이 전달의 중심이면 '현장취재·르포'만 부여한다.
    현장에서 직접 찍은 사진이 전달의 축일 때만 두 값을 함께 부여한다(자동 병기 금지).
  · '그래픽·인포그래픽'·(콘텐츠뷰)'카드뉴스·인포그래픽'과는 배타로 본다. 제작 도표·그래픽이
    핵심 전달 수단이면 그쪽만, 촬영 사진이 핵심이고 그래픽이 보조면 '포토·영상 중심'만 부여한다.
  · '인터뷰'와는 병기 가능하되, 직접 촬영으로 읽히는 사진이 함께 전달의 축일 때만 병기한다.
    제공 사진·자료 사진만 붙은 인터뷰는 '인터뷰'만 부여한다.
- 보도자료·공식발표: 공식 채널의 발표·보도자료는 광고성으로 엄격히 보지 말고 '보도자료·공식발표'를
  우선 부여한다(260715 회의 · 광고성 판정은 검수 단계 소관).
- 칼럼은 사설을 포함한다(사설·칼럼 통합 운영 · 260715 회의). 화자 관점이 강한 해설·주장은 '칼럼'으로 부여.
- 사설/칼럼은 서비스와 무관하게 범용 '의견·논쟁'(관점이 뚜렷하면 '반박·비판' 또는 '옹호·지지')을 우선 부여하고,
  콘텐츠뷰면 서비스값 '칼럼'을 함께 부여한다(서비스별 라벨 불일치 방지 · 공통축 우선).
- 재미·유머 중심 콘텐츠는 범용 '오락·유머'로 부여한다(정형정보로 매핑하지 않는다). 날씨·운세 등 주기적·전달목적물만 '정형정보'.
- 인텐트는 출처(공식 언론사·개인 블로그·카페)와 무관하게 내용 성격으로만 부여한다(게시판 #7).
  출처가 UGC 라는 이유로 '팬덤·화제성'을 부여하지 않는다. 팬 반응·화제가 글의 중심일 때만 부여.
- '실용 정보'는 독자가 따라 할 수 있는 방법·팁·가이드에 한정한다. 이적 소식·경기 일정 등
  관심사 소비형 정보는 소식 전달·추적이면 '속보·사건 추적', 배경·데이터 해설이면 '심층 분석'.
- 범용 '후기·리뷰·비평'(직접 체험 기반 평가)과 티스토리 '리뷰·분석'(직접 체험 없는 정보·스펙·시장
  분석)의 구분은 아래 사전의 각 분류값 정의문을 따른다(주간회의 260722 · 정의문에 병기됨).
  같은 근거로 두 값을 중복 부여하지 않는다. 둘 다 명확한 근거가 있을 때만 병기.""",
    "category": """# 부여 원칙
- 콘텐츠에 실재하는 도메인을 N개(1개 이상) 부여한다.
  본문 핵심 도메인을 대표로 첫 번째에 배치하고, 부가 도메인을 뒤에 나열한다.
- 대표 도메인 1개는 항상 부여하되, 부가(2번째 이후) 도메인은 확신도 50% 이상일 때만 추가한다
  (복수 카테고리 매핑의 정량 기준 · 260715 회의 확정). 확신이 서지 않으면 대표 1개만 부여한다.
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


@functools.lru_cache(maxsize=8)
def gold_examples(call: str | None = None) -> str:
    """골드 예시 텍스트. call 지정 시 해당 호출의 입·출력만, None 이면 4필드 통합.

    입력이 모듈 상수(GOLD)뿐인 순수 함수라 반환값이 절대 변하지 않는다 → 메모이즈.
    (요청당 15회 json.dumps · /config x200 프로파일에서 핸들러 시간의 19% 를 먹던 자리)"""
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
    return json.dumps(xs, ensure_ascii=False)      # import 는 모듈 상단(호출당 import 조회 제거)


# ── 사전 주입 텍스트 ──

def _defs_lines(values) -> list:
    """분류값 목록 → '- 값: 정의문' 줄. 정의문 원천은 dictionaries.INTENT_VALUE_DEFS 단일본
    (검수 UI 호버·정책 도움말·모바일 정의 시트와 같은 원천 · 정의문만 고치면 프롬프트가 따라온다)."""
    out = []
    for v in values:
        desc = D.INTENT_VALUE_DEFS.get(v, "")
        out.append(f"- {v}" + (f": {desc}" if desc else ""))
    return out


def intent_dictionary_text(display_service_name: str) -> str:
    """③ 인텐트 사전: 범용①(소비 방식) + 범용②(형식·전달 · 설명 병기) + 서비스 분기분(설명 병기).

    2026-08-03: 범용②도 서비스 분기값과 같이 정의문을 병기한다(종래에는 값 이름만 나열해
    INTENT_VALUE_DEFS 가 프롬프트에 도달하지 못했다 · '포토·영상 중심'처럼 경계가 미묘한 값이
    이름만으로 판정되던 원인).

    2026-08-12: 범용①도 같은 이유로 정의문을 병기한다. 종전에는 이름만 나열했는데
    (근거: CALL_RULES 가 정의를 소유한다는 전제) 실제로 CALL_RULES 에는 흩어진 경계 지침만
    있고 10종 중 8종은 정의가 아예 없었다. 운영 실측에서 검수 지적의 60.4% 가 인텐트였고
    오탐 1위 '심층 분석'·4위 '실용 정보'가 그 8종에 속했다. 이제 INTENT_VALUE_DEFS 가
    10종 전부를 갖는다(문구는 검수자 메모 730건에서 뽑은 경계 규칙 · CALL_RULES 와 충돌하지
    않게 맞췄다)."""
    svc = D._service_key(display_service_name)
    lines = ["[범용 ① 소비 방식 · 전 서비스 공통]"]
    lines += _defs_lines(D.INTENT_CATEGORIES_UNIVERSAL)
    lines += ["- 관점 축 구분: 한쪽 논조가 뚜렷하면 옹호·지지(지지 논조) 또는 반박·비판(반대·비판 논조),"
              " 찬반이 병렬로 오가면 의견·논쟁."
              " 기존 정책·주장을 반박하며 대안을 옹호하는 콘텐츠(사설·칼럼 전형)는 비판 대상이"
              " 논지의 출발점이므로 반박·비판을 우선한다.",
              "", "[범용 ② 형식·전달 형태 · 전 서비스 공통 · ①과 교차 부여 가능]"]
    lines += _defs_lines(D.INTENT_FORM_UNIVERSAL)
    svc_vals = D.INTENT_CATEGORIES_BY_SERVICE.get(svc, [])
    if svc_vals:
        lines += ["", f"[서비스 카테고리 분류값 · {svc}]"] + _defs_lines(svc_vals)
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
#
# 프롬프트 캐시 프리픽스 안정성(2026-08-09):
#   시스템 프롬프트는 입력 토큰의 약 74% 이고 같은 서비스·모델이면 모든 콘텐츠에 동일하다 =
#   캐시 프리픽스. 캐시는 **접두 일치**로만 붙으므로 앞쪽이 1자라도 달라지면 뒤 전체가 무효다.
#   → 콘텐츠마다 달라지는 유일한 조각인 {LEARNED}(학습 보정 · 누적되며 변함)는 반드시
#     **템플릿 최말단**에 둔다. 아래 기본 래퍼 5종은 모두 {LEARNED} 로 끝난다
#     (회귀 가드: tests/test_prompt_cache.py::TestPrefixStability).
#
# 캐시를 깨는 실제 위험 3가지(코드로 막은 것 · 못 막아 문서로 남기는 것):
#   ① 구조화 출력(response_format) 토글 — 스키마는 프리픽스 맨 앞에 붙는다. 라우터 400 을
#      배치 중간에 학습하면 그 순간 전체 프리픽스가 갈린다. [완화] _PARAM_ADAPT 프로세스 캐시로
#      모델당 1회로 제한 + gemini 계열은 선제 시드 + 캐시 키 지문에 json on/off 포함(llm.py).
#      [못 막음] 첫 400 이 나는 그 1회의 무효화 자체는 피할 수 없다.
#   ② reasoning_effort 변경 — 프리픽스가 분기한다. [완화] 캐시 키 지문에 포함(키가 프리픽스를
#      앞지르지 않는다). [못 막음] 값을 바꾸면 캐시는 처음부터 다시 쌓인다 = 배치 도중 변경 금지.
#   ③ 프롬프트 스튜디오 편집(WRAPPER_OVERRIDES · STAGE_DIRECTIVE) — 래퍼 '중간'을 고치면
#      그 뒤 전부가 무효다. [완화] 캐시 키를 system 본문 해시로 만들어 편집 즉시 새 키가 되게 했다
#      (옛 키에 새 프리픽스가 얹히는 혼선은 없다). [못 막음] 편집 = 캐시 재구축. 배치 중 편집 금지.
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


# ── 토픽 스튜디오: 조건값 자동생성(필수/선택 설계) · 모델 계열 쿡북 래퍼 재사용 ──
# 추출 콜과 동일한 FAMILY_WRAPPER 로 조립 → 계열별 프롬프트 + 스튜디오 오버라이드(WRAPPER_OVERRIDES) 상속.
TOPIC_SUGGEST_ROLE = (
    "너는 콘텐츠 큐레이션 토픽 설계자다. 사용자의 자연어 설명을 읽고, 이 토픽을 규정하는 "
    "'필수 조건'(반드시 만족 · 토픽의 정체성=주제·대상)과, 관련 콘텐츠를 넓히는 '선택 조건'"
    "(각각이 별도 '관련 묶음'이 됨 · 관점·형식 등 곁가지)으로 나눠 조건값을 설계한다.")
TOPIC_SUGGEST_SCHEMA = (
    '{"must": {"cats": string[], "intents": string[], "keywords": string[]}, '
    '"optional": {"cats": string[], "intents": string[], "keywords": string[]}, '
    '"exclude": {"cats": string[], "intents": string[], "keywords": string[]}, '
    '"eattrs": string[] ("key:value" · 개체 속성 후보 목록의 값만 · 해당 없으면 빈 배열)}')
TOPIC_SUGGEST_RULES = (
    "- 목적: 하나의 토픽을 '핵심 묶음(필수+모든 선택)'과 '관련 묶음(필수+선택 하나씩)'으로 펼칠 수 있게 조건을 설계한다.\n"
    "- must(필수): 이 토픽이 무엇에 관한 것인지 규정하는 축. 보통 주제 카테고리·대상 키워드 1~2개. 비우지 않는다.\n"
    "- optional(선택): 관련 콘텐츠를 넓히는 관점·형식·세부 유형. 인텐트가 여기 오는 경우가 많다. 1~3개 제안해 묶음이 풍부해지게 한다.\n"
    "- exclude(제외): 설명에 '빼줘/제외/말고/없이' 같은 배제 표현이 붙은 값. 걸리면 모든 묶음에서 탈락한다. "
    "배제 대상은 must/optional 에 절대 넣지 않는다. 배제 표현이 없으면 빈 배열.\n"
    "- cats·intents 는 아래 허용 목록의 값만 쓴다(목록 외 생성 절대 금지). keywords 는 인물·기업·작품 등 고유명사만 자유(최대 5).\n"
    "- 현재 데이터에 있는 값을 우선하되, 설명에 부합하면 데이터에 아직 없는 값도 가능(미래 매칭).\n"
    "- eattrs(개체 속성): 설명이 '개체의 불변 속성'(성별·직업·국적·소속·타입)을 조건으로 삼을 때만 사용. "
    "예) '여성 스포츠인' → [\"gender:여성\", \"occupation:스포츠인\"]. 조건 전부를 한 개체가 만족해야 하며 항상 필수 취급. "
    "아래 개체 속성 후보 목록의 값만 쓴다(목록 외 생성 금지 · 후보가 없으면 keywords 로 대신하지 말고 빈 배열).")
TOPIC_SUGGEST_SELF_CHECK = (
    "- must 최소 1개인가 · must+optional 이 설명의 핵심을 담는가 · 허용 목록 외 cats/intents 를 만들지 않았는가 · "
    "관련 묶음이 생기도록 optional 을 최소 1개 제안했는가 · 배제 표현('빼줘' 등)의 대상을 exclude 로만 보냈는가 · "
    "개체 속성 조건이 설명에 있으면 eattrs 후보 목록의 값으로 옮겼는가(목록 외 값 생성 금지)")
TOPIC_SUGGEST_EXAMPLES = (
    '설명: "스포츠 주제의 인물들에 대한 콘텐츠 모아줘"\n'
    '→ {"must":{"cats":["Sports"],"intents":[],"keywords":[]},'
    '"optional":{"cats":[],"intents":["인물·사연","인터뷰","선수 분석 기사"],"keywords":[]},'
    '"exclude":{"cats":[],"intents":[],"keywords":[]}}\n'
    '설명: "삼성전자 관련 경제 심층분석만"\n'
    '→ {"must":{"cats":["Business and Finance"],"intents":[],"keywords":["삼성전자"]},'
    '"optional":{"cats":[],"intents":["심층 분석","트렌드·시장 분석"],"keywords":[]},'
    '"exclude":{"cats":[],"intents":[],"keywords":[]}}\n'
    '설명: "경제·산업 심층분석만 모으고 속보는 빼줘"\n'
    '→ {"must":{"cats":["Business and Finance"],"intents":[],"keywords":[]},'
    '"optional":{"cats":[],"intents":["심층 분석","트렌드·시장 분석"],"keywords":[]},'
    '"exclude":{"cats":[],"intents":["속보","사건 경과 보도"],"keywords":[]}}')


def topic_suggest_system(model: str, cats_ko, intents, data_cats, data_int, eattrs=None) -> str:
    """조건값 자동생성 시스템 프롬프트. 모델 계열 쿡북 래퍼로 조립 + 허용 목록(전체 분류·데이터 우선) 주입.
    eattrs = 엔티티 사전 실재 속성 후보('key:value (라벨 · N건)') · 개체 속성 조건 축."""
    rules = (TOPIC_SUGGEST_RULES
             + "\n\n[전체 카테고리 Tier1 · 한글=영문]: " + _ja(cats_ko)
             + "\n[전체 인텐트]: " + _ja(intents)
             + "\n[현재 데이터에 있는 값(우선)] 카테고리: " + _ja(data_cats) + " · 인텐트: " + _ja(data_int)
             + "\n[개체 속성 후보(엔티티 사전 실재값 · 이 목록의 key:value 만)]: "
             + (_ja(eattrs) if eattrs else "(없음 · eattrs 는 빈 배열)"))
    return _compose(family_of(model), TOPIC_SUGGEST_ROLE, TOPIC_SUGGEST_SCHEMA, rules,
                    TOPIC_SUGGEST_EXAMPLES, TOPIC_SUGGEST_SELF_CHECK, "")


def topic_suggest_user(text: str) -> str:
    return "설명: " + (text or "").strip()


INTENT_BODY_HEAD = 500          # ③ 인텐트 콜에 넣는 본문 도입부 길이(자) · 근거는 _intent_body_excerpt

_IMG_UNKNOWN = "이미지 수: 정보 없음(입력에 이미지 목록이 없음 · '이미지가 0장'이라는 뜻이 아니다)"


def _intent_image_line(content) -> str:
    """③ 인텐트 콜의 이미지 수 신호.

    원천은 content.image_urls(참조 필드). 운영 인입은 아직 이 필드를 채우지 않아 대부분 빈 목록이므로,
    비어 있을 때 '0장'이라고 단정하면 모델이 '사진 없음'으로 오판한다 → '정보 없음'으로 명시 구분한다.
    (정보 없음 ≠ 0장. 인입이 image_urls 를 채우기 시작하면 값이 그대로 신호가 된다.)"""
    urls = getattr(content, "image_urls", None)
    if not isinstance(urls, (list, tuple)):
        return _IMG_UNKNOWN
    n = len([u for u in urls if str(u or "").strip()])
    return f"이미지 수: {n}" if n else _IMG_UNKNOWN


def _intent_body_excerpt(body: str) -> str:
    """본문 도입부 발췌. 본문 전문을 넣지 않는 이유:
    ① 리드문(summary)이 이미 본문 전량을 요약해 들어오므로 전문은 대부분 중복 토큰이다.
    ② 인텐트 판정에 필요한 형식·취재 정황 단서(현장 방문·직접 촬영 서술, 코너 표지)는
       제목과 도입부에 몰려 있다. ③ 인텐트는 전 아이템 필수 콜이라 전문 주입 시 비용 증가가
       배치 전체에 곱해진다. → 제목 + 리드문 + 도입부 + 분량·이미지 신호의 조합을 택했다."""
    b = (body or "").strip()
    return b if len(b) <= INTENT_BODY_HEAD else b[:INTENT_BODY_HEAD] + " …(이하 생략)"


def call_user(call: str, content, prior: dict) -> str:
    """분리형 호출의 user 메시지(문서 §2.6 주입 변수).

    ③ 인텐트는 2026-08-03(imeta@v16)부터 dsn+summary 에 title·본문 글자수·이미지 수·본문 도입부를
    더해 받는다. 종래 입력으로는 '포토·영상 중심'·'현장취재·르포' 같은 형식·전달 수단 분류값의
    판정 근거가 없었다(모듈 docstring '입력 계약 해석 개정' 참조). 원천은 여전히 계약 3필드다."""
    dsn = getattr(content, "displayServiceName", "") or ""
    title = getattr(content, "title", "") or ""
    body = getattr(content, "body", "") or ""
    if call == "summary":
        return f"displayServiceName: {dsn}\ntitle: {title}\nbody: {body}"
    if call == "entities":
        return f"title: {title}\nbody: {body}"
    if call == "intent":
        return (f"displayServiceName: {dsn}\ntitle: {title}\n"
                f"summary: {prior.get('summary', '')}\n"
                f"본문 글자수: {len(body)}\n{_intent_image_line(content)}\n"
                f"본문 도입부: {_intent_body_excerpt(body)}")
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
