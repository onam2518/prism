"""출력 어휘 사전: 자유생성 금지. 토픽·사용자 메타 공유 사전과 정합되어야 한다."""

QUALITY_METAS = {
    "ad":        "노골적 상업 광고 / 협찬 미고지 / 외부 구매 유도",
    "sexual":    "선정성: 성적 묘사·암시·노출 유도",
    "profanity": "욕설·비속어·혐오 표현(언어적)",
    "gambling":  "도박·사행성(복권·로또·베팅) 유도",
    "clickbait": "낚시성 제목: 제목과 본문 불일치, 과장 후킹",
    "format":    "형식 파괴: 깨진 마크업·도배·가독 불가 (UGC 한정)",
    "shallow":   "정보가치 부족: 빈약한 본문, 알맹이 없음",
    "spam":      "스팸: 반복 도배·감성 스토리텔링·저품질 감정 후킹",
    "graphic":   "잔혹/혐오 시각 묘사: 폭행·사고·사체 등",
    "political":  "정치 선동 (UGC 한정)",
    "hate":      "특정 집단 비하·혐오 은어 (UGC 한정)",
}

# normal = 트리거 0개일 때의 사유값
QUALITY_NORMAL = "normal"

MEDIA_DISABLED_METAS = {"format", "political", "hate"}

QUALITY_PRIORITY = [
    "graphic", "sexual", "hate", "political", "gambling",
    "profanity", "ad", "spam", "clickbait", "shallow", "format",
]


LEGAL_HARM_TYPES = {
    "defamation":           {"label": "명예훼손", "article": "형법 §307"},
    "insult":               {"label": "모욕", "article": "형법 §311"},
    "obscenity":            {"label": "음란물", "article": "정보통신망법 §44-7"},
    "sexual_violence":      {"label": "성폭력(촬영물 등)", "article": "성폭력처벌법 §14"},
    "privacy_violation":    {"label": "사생활 침해", "article": "개인정보보호법"},
    "stalking":             {"label": "스토킹", "article": "스토킹처벌법"},
    "hate_speech":          {"label": "혐오표현", "article": ":"},
    "copyright":            {"label": "저작권 침해", "article": "저작권법"},
    "fraud":                {"label": "사기", "article": "형법 §347"},
    "election_interference": {"label": "선거 개입", "article": "공직선거법"},
    "ad_fraud":             {"label": "허위·과장 광고", "article": "표시광고법"},
    "gambling":             {"label": "도박 개장/유도", "article": "형법 §247"},
    "drug_weapon":          {"label": "마약·무기 거래", "article": "마약류관리법 등"},
}

# 스코어 = A(구성요건 0~40) + B(제재 0~30) + C(맥락 0~30)
LEGAL_SCORE_BOUNDS = {"a": (0, 40), "b": (0, 30), "c": (0, 30)}

# 등급 임계: GREEN 0~19 / YELLOW 20~69 / RED 70~100
def legal_grade(total: int) -> str:
    if total >= 70:
        return "RED"
    if total >= 20:
        return "YELLOW"
    return "GREEN"


# 기본 프로파일(예시). 회사별 서비스 체계는 --profile 로 교체.
# media: 보도·편집 중심 / ugc: 사용자 생성 콘텐츠
SERVICE_GROUP = {
    "뉴스": "media",
    "연예": "media",
    "스포츠": "media",
    "콘텐츠": "media",
    "커뮤니티": "ugc",
    "블로그": "ugc",
    "음악": "ugc",
    "동영상": "ugc",
}
SERVICE_GROUP_DEFAULT = "media"


def active_quality_metas(service_group: str) -> list:
    """서비스 그룹별 활성 품질 메타 세트. routing 이 프롬프트 규칙적재를 좁히는 핵심."""
    metas = [m for m in QUALITY_METAS if m != "format" or service_group == "ugc"]
    if service_group == "media":
        metas = [m for m in metas if m not in MEDIA_DISABLED_METAS]
    return metas


# 인텐트 사전 — DNM 1312/인텐트 정의 및 구분(278856094) 기준.
# displayServiceName 분기 후 [범용 + 해당 서비스 카테고리] 만 프롬프트에 주입.
# 범용(전 서비스 공통, 소비 방식 축): 8종
INTENT_CATEGORIES_UNIVERSAL = [
    "속보·사건 추적", "심층 분석", "팬덤·화제성", "실용 정보",
    "감성·공감", "오락·유머", "의견·논쟁", "학술·전문",
]
# 서비스 카테고리별(세부 종류·속성 축). UI 콘텐츠 그룹 키 ↔ 기획서 서비스 카테고리:
#   콘텐츠=콘텐츠뷰 · 음악=멜론 · 커뮤니티=다음카페 · 블로그=티스토리 · 동영상=카카오TV
INTENT_CATEGORIES_BY_SERVICE = {
    "뉴스":   ["속보·단신", "사건 경과 보도", "정책·사업 소개", "행정 발표",
              "법안 통과 보도", "노동 이슈 보도", "사회 안전 보도", "인물 동정",
              "사설·칼럼", "국제·외교 보도", "트렌드 분석", "설문조사 기반 분석"],
    "연예":   ["열애·결혼·이혼", "컴백·신작 발매", "논란·해명",
              "화보·시상식", "방송 출연", "팬 소통"],
    "스포츠": ["감독 인터뷰", "경기 프리뷰", "경기 결과·리뷰", "이적·계약 보도",
              "선수 분석 기사", "전술·데이터 분석", "부상·복귀 근황", "팬·응원 문화"],
    "콘텐츠": ["카드뉴스·인포그래픽", "큐레이션·모음", "랭킹·리스트",
              "요약·브리핑", "반응·리액션", "비교·분석"],
    "음악":   ["신곡·앨범 발매", "차트·랭킹", "아티스트 소식",
              "라이브·공연", "음악 큐레이션", "음원 리뷰·분석"],
    "커뮤니티": ["후기·리뷰", "질문·답변", "정보 공유",
                "의견·토론", "일상·잡담", "창작·작품 공유"],
    "블로그": ["에세이·산문", "가이드·튜토리얼", "기술·개발",
              "리뷰·분석", "일상·여행", "창작·연재"],
    "동영상": ["클립·하이라이트", "풀영상·본방", "예고편·티저",
              "인터뷰·비하인드", "라이브 중계", "교양·다큐"],
}


def intent_categories_for(display_name: str) -> list:
    svc = _service_key(display_name)
    return INTENT_CATEGORIES_UNIVERSAL + INTENT_CATEGORIES_BY_SERVICE.get(svc, [])


def _service_key(display_name: str) -> str:
    """프롬프트 주입용 서비스 키 정규화."""
    n = (display_name or "").strip()
    if n in INTENT_CATEGORIES_BY_SERVICE:
        return n
    # 느슨한 매핑
    for k in INTENT_CATEGORIES_BY_SERVICE:
        if k in n:
            return k
    if "music" in n.lower() or "뮤직" in n:
        return "음악"
    if "카페" in n or "커뮤" in n or "포럼" in n:
        return "커뮤니티"
    if "blog" in n.lower() or "블로그" in n:
        return "블로그"
    if "tv" in n.lower() or "비디오" in n or "video" in n.lower():
        return "동영상"
    return ""


# 콘텐츠 카테고리 — DNM 1312/콘텐츠 카테고리 정의(365789408) 기준.
# IAB Content Taxonomy v3.0 골격을 운영 효율 위해 Tier1 21개로 압축한 자사 사전(매핑 비중 순).
IAB_TIER1 = [
    "News and Politics", "Entertainment", "Business and Finance", "Sports",
    "Food and Drink", "Travel", "Family and Relationships", "Education",
    "Technology and Computing", "Books and Literature", "Medical Health",
    "Hobbies and Interests", "Health and Fitness", "Home and Garden", "Pets",
    "Style and Fashion", "Automotive", "Video Gaming", "Science", "Careers",
    "Religion and Spirituality",
]
# Tier1 → Tier2 (자사 사전). ★ 표시는 IAB 비표준 Custom Tier2(정의 페이지 명시).
CONTENT_CATEGORY_TIER2 = {
    "News and Politics": ["Politics", "Society", "Local News", "Crime", "Disasters",
                          "Law", "International News", "Weather"],
    "Entertainment": ["Celebrity News", "Celebrity News (Foreign)", "Drama TV", "TV Shows",
                      "Movies", "Music", "Visual Art", "Performing Arts", "Humor"],
    "Business and Finance": ["Economy", "Industries", "Business", "Investing", "Banking",
                             "Insurance", "Real Estate Policy", "Real Estate Listings"],
    "Sports": ["Soccer (Domestic)", "Soccer (International)", "Baseball (Domestic)",
               "Baseball (International)", "Basketball", "Volleyball", "Golf",
               "Martial Arts", "Other Sports"],
    "Food and Drink": ["Cooking", "Food", "Beverages", "Dining Out"],
    "Travel": ["Domestic Travel", "International Travel", "Hotels", "Air Travel", "Travel Preparation"],
    "Family and Relationships": ["Parenting", "Family", "Dating", "Weddings"],
    "Education": ["Primary Education", "Secondary Education", "Higher Education",
                  "Language Learning", "Adult Education"],
    "Technology and Computing": ["Computing", "Internet", "Information Security", "Consumer Electronics"],
    "Books and Literature": ["Fiction", "Non-Fiction", "Biographies", "Essays"],
    "Medical Health": ["Diseases and Conditions", "Wellness"],
    "Hobbies and Interests": ["Arts and Crafts", "Collecting", "Outdoors"],
    "Health and Fitness": ["Healthy Living", "Exercise and Fitness"],
    "Home and Garden": ["Interior Decorating", "Gardening", "Home Improvement", "Shopping"],
    "Pets": ["Dogs", "Cats", "Birds", "Fish", "Other Pets"],
    "Style and Fashion": ["Fashion Trends", "Personal Care", "Accessories"],
    "Automotive": ["Auto Type", "Auto Repair", "Auto Shows"],
    "Video Gaming": ["Video Games", "eSports"],
    "Science": ["Space and Astronomy", "Biology", "Physics", "Environment", "General Science"],
    "Careers": ["Job Search", "Career Advice"],
    "Religion and Spirituality": ["Religion", "Spirituality"],
}
IAB_TIER2 = CONTENT_CATEGORY_TIER2   # 하위 호환 별칭

# 자사 경로 → IAB v3.0 공식 경로(외부 광고 연동 후처리 변환용). 정의 페이지 부록.
CATEGORY_IAB_MAP = {
    "Entertainment / Celebrity News (Foreign)": "Pop Culture / Celebrity News",
    "Entertainment / Drama TV": "Television / Drama TV",
    "Entertainment / TV Shows": "Television / TV Shows",
    "Entertainment / Movies": "Movies",
    "Entertainment / Music": "Music and Audio",
    "Entertainment / Visual Art": "Fine Art",
    "Entertainment / Performing Arts": "Fine Art",
    "Business and Finance / Investing": "Personal Finance",
    "Business and Finance / Banking": "Personal Finance",
    "Business and Finance / Insurance": "Personal Finance",
    "Business and Finance / Real Estate Policy": "Real Estate / Residential Real Estate",
    "Business and Finance / Real Estate Listings": "Real Estate / Residential Real Estate",
    "News and Politics / Weather": "Weather",
    "Home and Garden / Shopping": "Shopping",
    "Sports / Soccer (Domestic)": "Sports / Soccer",
    "Sports / Soccer (International)": "Sports / Soccer",
    "Sports / Baseball (Domestic)": "Sports / Baseball",
    "Sports / Baseball (International)": "Sports / Baseball",
    "Sports / Other Sports": "Sports / Olympic Sports",
    "Video Gaming / eSports": "Video Gaming / eSports",
}

# 도메인 그룹(7) = Tier1 21개 고정 묶음(별도 LLM 판정 불요). 정의 페이지 부록.
DOMAIN_GROUP_MAP = {
    "시사": ["News and Politics"],
    "경제·산업": ["Business and Finance"],
    "엔터": ["Entertainment"],
    "스포츠": ["Sports"],
    "테크·모빌리티": ["Technology and Computing", "Automotive", "Video Gaming"],
    "라이프": ["Food and Drink", "Travel", "Family and Relationships", "Home and Garden",
              "Pets", "Style and Fashion", "Health and Fitness", "Hobbies and Interests",
              "Religion and Spirituality", "Medical Health", "Careers"],
    "지식·교양": ["Education", "Books and Literature", "Science"],
}


def all_quality_ids():
    return list(QUALITY_METAS.keys())


def apply_profile(prof: dict):
    """회사별 프로파일로 사전을 비파괴 override. 코어 파이프라인은 그대로, 사전만 교체.
    지원 키: service_group, intent_universal, intent_by_service, iab_tier1, quality_metas."""
    g = globals()
    keymap = {
        "service_group": "SERVICE_GROUP",
        "intent_universal": "INTENT_CATEGORIES_UNIVERSAL",
        "intent_by_service": "INTENT_CATEGORIES_BY_SERVICE",
        "iab_tier1": "IAB_TIER1",
        "quality_metas": "QUALITY_METAS",
    }
    for pk, gk in keymap.items():
        if pk in prof:
            cur = g.get(gk)
            if isinstance(cur, dict):
                cur.update(prof[pk])
            else:
                g[gk] = prof[pk]
