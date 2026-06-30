"""출력 어휘 사전: 자유생성 금지. 토픽·사용자 메타 공유 사전과 정합되어야 한다."""

# 품질 메타 11종 — DNM 1311/품질 메타 구분 및 정의(278036632) 기준.
# 정의는 분류기 프롬프트에 주입. 발동 ≥1 → finalGrade R, 0 → G(통과 우선 default).
# (stale 제거: 시의성은 어드민 freshness 필터 책무. format·political·hate 는 UGC 한정.)
QUALITY_METAS = {
    "ad":        "광고성: 제품·서비스 홍보 + 명시적 구매·가입 유도가 본문 핵심(3축 AND — 수익 귀속·명시 CTA·B2C 대상 모두 충족 시에만)",
    "sexual":    "선정적 텍스트: 성적 표현이 과도해 정보 전달보다 자극이 목적(단어 경계·콜로케이션, 강한 신호+정량 기준)",
    "profanity": "저속 텍스트: 욕설·비속어를 과도 사용해 불쾌감 유발(단어 경계, 마스킹 포함)",
    "gambling":  "사행성: 로또·토토·카지노 등 조장·당첨 기대감 자극(시점성+권유·예측 톤 결합)",
    "clickbait": "낚시성: 제목-본문 괴리·정보 은닉으로 호기심 자극 클릭 유도(제목↔본문 정합성)",
    "format":    "형식 불만족: 극단적으로 짧거나 구조 불완전해 피드 UX 저해(UGC 한정)",
    "shallow":   "낮은 정보가치: 본문 무관 인물·발언을 끌어와 포장, 화제성·검색 노출 노림",
    "spam":      "저품질 생산자: AI 양산·템플릿화 등 비정상 생산 패턴(문체 부자연·사실 불일치)",
    "graphic":   "사건·재난·범죄 과잉묘사: 수법·피해를 구체·지속 묘사(묘사 신호 2개 이상 시 R)",
    "political": "정치·이념 거론: 한국 특정 정당·정치인·진영을 비방·낙인·선동(UGC 한정)",
    "hate":      "차별·혐오: 인종·성별·지역·연령·세대·국적·계층 비하·혐오 표현(UGC 한정)",
}

# 메타명(한글 짧은 라벨) — 사전 모듈 표시용
QUALITY_META_NAMES = {
    "ad": "광고성", "sexual": "선정적 텍스트", "profanity": "저속 텍스트", "gambling": "사행성",
    "clickbait": "낚시성", "format": "형식 불만족", "shallow": "낮은 정보가치",
    "spam": "저품질 생산자", "graphic": "사건·재난·범죄 과잉묘사", "political": "정치·이념 거론",
    "hate": "차별·혐오",
}
# 적용 그룹: both(미디어+UGC) | ugc(UGC 한정)
QUALITY_META_APPLIES = {m: ("ugc" if m in ("format", "political", "hate") else "both")
                        for m in QUALITY_METAS}

# normal = 트리거 0개일 때의 사유값
QUALITY_NORMAL = "normal"

# 미디어 그룹 자동 비활성(검사 자체 미수행): format·political·hate (+ stale 제거됨)
MEDIA_DISABLED_METAS = {"format", "political", "hate"}

# 인입 정책: ITEM TYPE 별 필터·처리 정책(어드민 편집 가능). 131 체계.
INTAKE_POLICY = {
    "텍스트형": {"filter": "O", "method": "정상 분류(품질 메타 부여)", "status": "구현"},
    "이미지형": {"filter": "△", "method": "GREEN 일괄 + 캡션 텍스트(시각 이해)", "status": "PoC"},
    "영상형": {"filter": "X", "method": "GREEN 일괄(Argos 별도)", "status": "계획"},
    "SNS형": {"filter": "X", "method": "서비스 자체 필터 후 인입", "status": "계획"},
    "묶음형 · 데이터형": {"filter": "X", "method": "GREEN 일괄(고도화 과제)", "status": "계획"},
}

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


# ── 카테고리 사전화: LLM 자유 출력 → 고정 사전 스냅 ──
#   임베딩 kNN 경로가 없을 때(LLM 직접 생성)도 content_category 가 항상 사전값이
#   되도록 강제한다. 미매칭은 'Unclassified'. 사전 정식 값엔 멱등(그대로 통과).
_T1_LOOKUP = {t.lower(): t for t in IAB_TIER1}
_T2_LOOKUP = {t2.lower(): (t1, t2)                       # tier2(소문자) → (tier1, 정식 tier2)
              for t1, t2s in CONTENT_CATEGORY_TIER2.items() for t2 in t2s}


def normalize_content_category(raw: str) -> str:
    """'Tier1 / Tier2' 또는 'Tier1' 자유 문자열 → 사전 정식 경로. 미매칭은 'Unclassified'."""
    s = str(raw or "").strip()
    if not s or s == "Unclassified":
        return "Unclassified"
    parts = [p.strip() for p in s.split("/") if p.strip()]
    t1 = _T1_LOOKUP.get(parts[0].lower()) if parts else None
    if t1:
        if len(parts) > 1:
            valid = {x.lower(): x for x in CONTENT_CATEGORY_TIER2.get(t1, [])}
            t2 = valid.get(parts[1].lower())
            if t2:
                return f"{t1} / {t2}"
        return t1
    # Tier1 미매칭: 조각 중 하나가 Tier2 사전에 있으면 그 Tier1 로 복구
    for p in parts:
        hit = _T2_LOOKUP.get(p.lower())
        if hit:
            return f"{hit[0]} / {hit[1]}"
    return "Unclassified"


def normalize_categories(cmap: dict) -> dict:
    """{엔티티: 카테고리문자열} 전체를 사전화."""
    return {e: normalize_content_category(c) for e, c in (cmap or {}).items()}

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
    """회사별/운영자 프로파일로 사전을 비파괴 override. 코어 파이프라인은 그대로, 사전만 교체.
    어드민 편집(사용자 직접 수정)에서도 동일 경로 사용."""
    g = globals()
    keymap = {
        "service_group": "SERVICE_GROUP",
        "intent_universal": "INTENT_CATEGORIES_UNIVERSAL",
        "intent_by_service": "INTENT_CATEGORIES_BY_SERVICE",
        "iab_tier1": "IAB_TIER1",
        "tier2": "CONTENT_CATEGORY_TIER2",
        "quality_metas": "QUALITY_METAS",
        "legal_types": "LEGAL_HARM_TYPES",
        "domain_groups": "DOMAIN_GROUP_MAP",
        "category_iab_map": "CATEGORY_IAB_MAP",
        "intake_policy": "INTAKE_POLICY",
    }
    for pk, gk in keymap.items():
        if pk in prof:
            cur = g.get(gk)
            if isinstance(cur, dict) and isinstance(prof[pk], dict):
                cur.update(prof[pk])
            else:
                g[gk] = prof[pk]
