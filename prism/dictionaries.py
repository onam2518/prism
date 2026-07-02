"""출력 어휘 사전: 자유생성 금지. 토픽·사용자 메타 공유 사전과 정합되어야 한다."""

# 품질 메타 11종 · DNM 1311/품질 메타 구분 및 정의(278036632) 기준.
# 정의는 분류기 프롬프트에 주입. 발동 ≥1 → finalGrade R, 0 → G(통과 우선 default).
# (stale 제거: 시의성은 어드민 freshness 필터 책무. format·political·hate 는 UGC 한정.)
QUALITY_METAS = {
    "ad":        "광고성: 제품·서비스 홍보 + 명시적 구매·가입 유도가 본문 핵심(3축 AND · 수익 귀속·명시 CTA·B2C 대상 모두 충족 시에만)",
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

# 메타명(한글 짧은 라벨) · 사전 모듈 표시용
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
    "hate_speech":          {"label": "혐오표현", "article": "포괄 규정(개별 조문 없음)"},
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


# 인텐트 사전 · DNM 1312/인텐트 정의 및 구분(278856094) 기준.
# displayServiceName 분기 후 [범용 + 해당 서비스 카테고리] 만 프롬프트에 주입.
# 범용(전 서비스 공통, 소비 방식 축): 8종
INTENT_CATEGORIES_UNIVERSAL = [
    "속보·사건 추적", "심층 분석", "팬덤·화제성", "실용 정보",
    "감성·공감", "오락·유머", "의견·논쟁", "학술·전문",
]
# 범용 ② 형식·전달 형태(전 서비스 공통): 8종 — 기획 확정(2026-07-02 · 계약 v2, 범용①·② 합산 0~2개 권장)
INTENT_FORM_UNIVERSAL = [
    "인터뷰", "현장취재·르포", "그래픽·인포그래픽", "포토·영상 중심",
    "보도자료·공식발표", "후기·리뷰·비평", "해설·팩트체크", "정형정보",
]
# 서비스 카테고리별(세부 종류·속성 축) · 원문 사전(인텐트 정의 및 구분, 2026-07 개편) 동기.
# 분기: displayServiceName 정의값 10개 → 서비스 카테고리 8종. 미정의 값 = PGC 폴백(범용만 부여).
INTENT_CATEGORIES_BY_SERVICE = {
    "뉴스":   ["속보·단신", "사건 경과 보도", "정책·행정", "노동·사회 이슈",
              "인물·사연", "국제·외교 보도", "트렌드·시장 분석"],
    "연예":   ["열애·결혼·이혼", "컴백·신작 발매", "예고", "논란·해명", "방송 출연",
              "SNS·인플루언서", "캐스팅·공식소식", "이슈 편승(재조명)", "연재"],
    "스포츠": ["경기 프리뷰", "경기 라인업·중계", "경기 결과·리뷰", "이적·계약 보도",
              "선수 분석 기사", "전술·데이터 분석", "부상·복귀 근황", "팬·응원 문화"],
    "콘텐츠뷰": ["카드뉴스·인포그래픽", "큐레이션·모음", "랭킹·리스트", "요약·브리핑",
                "비교·분석", "반응·리액션", "생활·실용정보", "트렌드", "뉴스·소식", "칼럼"],
    "음악":   ["신곡·앨범 발매", "차트·랭킹", "아티스트 소식",
              "라이브·공연", "음악 큐레이션", "음원 리뷰·분석"],
    "커뮤니티": ["후기·리뷰", "질문·답변", "정보 공유", "의견·토론",
                "일상·잡담", "창작·작품 공유", "뉴스·소식"],
    "티스토리": ["에세이·산문", "가이드·튜토리얼", "기술·개발",
                "리뷰·분석", "일상·여행", "창작·연재"],
    "TV":     ["클립·하이라이트", "풀영상·본방", "예고편·티저",
              "인터뷰·비하인드", "라이브 중계", "교양·다큐"],
}

# 분류값 설명(사전 원문) · 호버 정의·프롬프트 참고용. 범용①·②는 별도 정의.
INTENT_VALUE_DEFS = {
    "속보·단신": "사건 발생 직후 팩트 위주 전달", "사건 경과 보도": "진행 중인 사건의 후속·경과 전달",
    "정책·행정": "정책·사업 소개 + 법안 통과 + 행정 발표 통합", "노동·사회 이슈": "노사·파업·고용 + 교통·산업·재난·생활 안전 통합",
    "인물·사연": "일반인·시민의 삶·사연, 인물 조명", "국제·외교 보도": "해외 사건·외교·국제 관계 전달",
    "트렌드·시장 분석": "세대·소비·라이프스타일·시장 변화 분석 + 설문 기반 + 시장 동향 통합",
    "열애·결혼·이혼": "연예인 교제·결혼·파경 관련 소식", "컴백·신작 발매": "앨범·영화·드라마·예능 신규 출시",
    "예고": "방송·유튜브·공연 신작/차회 예고", "논란·해명": "연예인 관련 의혹·논란과 대응",
    "방송 출연": "예능·토크쇼·리얼리티 출연 관련", "SNS·인플루언서": "스타 SNS·라이브 소통, 인플루언서 동향",
    "캐스팅·공식소식": "캐스팅·발탁 등 공식 소식", "이슈 편승(재조명)": "과거 영상·이력 재조명 등 이슈 편승",
    "연재": "연재·기획 시리즈",
    "경기 프리뷰": "경기 앞두고 전망·관전 포인트", "경기 라인업·중계": "라인업 공개 및 실시간 진행 상보",
    "경기 결과·리뷰": "경기 스코어·하이라이트·경기 분석", "이적·계약 보도": "선수 이적·FA·트레이드·재계약 관련",
    "선수 분석 기사": "선수 개인의 폼·기록·경기력 분석", "전술·데이터 분석": "전술 해설·통계 기반 경기 분석",
    "부상·복귀 근황": "선수 부상 상태·재활·복귀 정보", "팬·응원 문화": "팬 반응·응원 문화·원정 관전",
    "카드뉴스·인포그래픽": "시각 중심 정보 전달 콘텐츠", "큐레이션·모음": "여러 소스를 엮은 주제별 모음",
    "랭킹·리스트": "순위형·나열형 콘텐츠", "요약·브리핑": "긴 기사·보고서의 핵심 요약",
    "비교·분석": "제품·정책·인물 등 비교형 콘텐츠", "반응·리액션": "특정 이슈에 대한 반응 모음",
    "생활·실용정보": "일상 활용 정보·팁", "트렌드": "인물·이슈·스타일 트렌드 소개",
    "뉴스·소식": "신제품·동향·공식 발표 등 소식 전달", "칼럼": "화자 관점이 강한 해설·주장",
    "신곡·앨범 발매": "신곡·앨범·EP 발매 및 발매 예고", "차트·랭킹": "차트 진입·상위·역주행 등 순위 변동",
    "아티스트 소식": "아티스트 활동·근황·인터뷰", "라이브·공연": "콘서트·페스티벌·라이브 출연",
    "음악 큐레이션": "플레이리스트·테마곡 모음·시대별 추천", "음원 리뷰·분석": "음원·앨범에 대한 비평·해설·트랙 분석",
    "후기·리뷰": "제품·서비스·장소 사용 경험 공유", "질문·답변": "특정 주제에 대한 질의와 답변",
    "정보 공유": "유용한 정보·팁·노하우 공유", "의견·토론": "특정 이슈에 대한 의견 개진과 토론",
    "일상·잡담": "일상 공유, 가벼운 대화", "창작·작품 공유": "팬픽·그림·사진 등 창작물 공유",
    "에세이·산문": "개인 경험·감상 기반 글", "가이드·튜토리얼": "단계별 안내·설명 콘텐츠",
    "기술·개발": "프로그래밍·IT·기술 관련 글", "리뷰·분석": "제품·서비스·콘텐츠 리뷰 및 분석",
    "일상·여행": "일상 기록·여행기·맛집 탐방", "창작·연재": "소설·시·연재물·번역 등 창작 콘텐츠",
    "클립·하이라이트": "방송·경기의 핵심 장면 발췌", "풀영상·본방": "방송 전체 또는 긴 분량 영상",
    "예고편·티저": "신작·차회 예고 영상", "인터뷰·비하인드": "출연자·제작진 인터뷰, 비하인드",
    "라이브 중계": "실시간 방송·중계", "교양·다큐": "교육·교양·다큐멘터리 영상",
    "인터뷰": "인물의 발언·문답 중심(감독·선수·연예인·일반인 통합)", "현장취재·르포": "현장 방문·취재 기반 보도",
    "그래픽·인포그래픽": "도표·그래픽이 핵심 전달 수단", "포토·영상 중심": "이미지·영상 자체가 본문(텍스트형 한정)",
    "보도자료·공식발표": "공식 채널 발 발표·공고·캐스팅·스폰서·프로모션", "후기·리뷰·비평": "사용·관람·체험 기반 평가",
    "해설·팩트체크": "쟁점 검증·배경 해설", "정형정보": "일정·부고·인사·시황·날씨·기록 등 주기적 정형 기사",
}

def intent_categories_for(display_name: str) -> list:
    """주입·검증 공용 인텐트 후보 = 범용①(소비 방식) + 범용②(형식·전달) + 서비스 분기."""
    svc = _service_key(display_name)
    return (INTENT_CATEGORIES_UNIVERSAL + INTENT_FORM_UNIVERSAL
            + INTENT_CATEGORIES_BY_SERVICE.get(svc, []))


# displayServiceName 정의값(10) → 서비스 카테고리. 구 명칭·레거시 UI 그룹명 공존 매핑.
_SERVICE_NAME_MAP = {
    "뉴스": "뉴스", "연예": "연예", "스포츠": "스포츠",
    "콘텐츠뷰 (일반)": "콘텐츠뷰", "콘텐츠뷰(일반)": "콘텐츠뷰",
    "멜론": "음악",
    "다음카페": "커뮤니티", "콘텐츠뷰 (커뮤니티)": "커뮤니티", "콘텐츠뷰(커뮤니티)": "커뮤니티",
    "티스토리": "티스토리",
    "VOD": "TV", "루프": "TV",
    "카카오TV": "TV", "카카오비디오": "TV",     # 마이그레이션 기간 구 명칭 공존
    # 레거시 UI 콘텐츠 그룹명 호환
    "콘텐츠": "콘텐츠뷰", "블로그": "티스토리", "동영상": "TV", "음악": "음악", "커뮤니티": "커뮤니티",
}


def _service_key(display_name: str) -> str:
    """displayServiceName → 서비스 카테고리 키. 미정의 값은 빈 문자열(PGC 폴백 · 범용만 부여)."""
    n = (display_name or "").strip()
    if n in _SERVICE_NAME_MAP:
        return _SERVICE_NAME_MAP[n]
    if n in INTENT_CATEGORIES_BY_SERVICE:
        return n
    for k, v in _SERVICE_NAME_MAP.items():      # 느슨한 포함 매칭(공백 변형 등)
        if k and k in n:
            return v
    if "music" in n.lower() or "뮤직" in n:
        return "음악"
    if "카페" in n or "커뮤" in n or "포럼" in n:
        return "커뮤니티"
    if "blog" in n.lower() or "블로그" in n:
        return "티스토리"
    if "tv" in n.lower() or "비디오" in n or "video" in n.lower():
        return "TV"
    return ""


# 콘텐츠 카테고리 · DNM 1312/콘텐츠 카테고리 정의(365789408) 기준.
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
    "Hobbies and Interests": ["Arts and Crafts", "Collecting", "Outdoors", "Military"],
    "Health and Fitness": ["Healthy Living", "Exercise and Fitness"],
    "Home and Garden": ["Interior Decorating", "Gardening", "Home Improvement", "Shopping", "Lifestyle"],
    "Pets": ["Dogs", "Cats", "Birds", "Fish", "Other Pets"],
    "Style and Fashion": ["Fashion Trends", "Personal Care", "Accessories"],
    "Automotive": ["Auto Type", "Auto Repair", "Auto Shows"],
    "Video Gaming": ["Video Games", "eSports"],
    "Science": ["Space and Astronomy", "Biology", "Physics", "Environment", "General Science"],
    "Careers": ["Job Search", "Career Advice"],
    "Religion and Spirituality": ["Religion", "Spirituality"],
}
IAB_TIER2 = CONTENT_CATEGORY_TIER2   # 하위 호환 별칭

# Tier1 국문 설명(원문 사전) · 프롬프트 주입·UI 참고
IAB_TIER1_DESC = {
    "News and Politics": "뉴스·시사·정치·사회·국제·기상", "Entertainment": "연예·예능·방송·영화·음악·공연",
    "Business and Finance": "비즈니스·경제·산업·개인금융·부동산", "Sports": "스포츠·경기·선수",
    "Food and Drink": "음식·요리·외식", "Travel": "여행·관광·숙박",
    "Family and Relationships": "가족·연애·육아", "Education": "교육·학습",
    "Technology and Computing": "테크·컴퓨팅·전자제품", "Books and Literature": "도서·문학·에세이",
    "Medical Health": "의료·질병", "Hobbies and Interests": "취미·관심사",
    "Health and Fitness": "건강·운동", "Home and Garden": "홈·인테리어·가드닝·쇼핑·라이프스타일",
    "Pets": "반려동물", "Style and Fashion": "패션·뷰티", "Automotive": "자동차·모빌리티",
    "Video Gaming": "비디오 게임·e스포츠", "Science": "과학·자연·환경",
    "Careers": "직업·채용·커리어", "Religion and Spirituality": "종교·운세·영성",
}

# 카테고리별 구분 기준(원문 사전 · ④ 콘텐츠 카테고리 호출의 시스템 메시지에 주입 · 캐싱 대상)
CATEGORY_CRITERIA = {
    "News and Politics": ("정책·법안·정치인 발언·정부기관·정책 사업명은 Politics, 정책 외 사회 현상·통계·일반 사회 기사는 Society. "
        "노동·파업·노사 갈등은 Society(거시지표 중심 경제는 Business and Finance / Economy). "
        "단, 기업 엔티티가 노사·파업 맥락으로 등장하면 Business and Finance / Industries 우선 · Society 는 총파업·고령운전자 등 일반명사에 적용. "
        "세대·인구 그룹은 Society. 지역 단위 행정·이슈는 Local News, 전국 단위 사회 이슈는 Society 우선. "
        "교육 정책은 Politics, 학습·교육 콘텐츠는 Education, 사회 현상으로서 교육 이슈는 Society. "
        "자연재해 피해 보도는 Disasters, 기상 현상·예보는 Weather. 국내 사안은 도메인별 Tier 2, 해외 사안은 International News 우선. "
        "모호 엔티티(의혹·선거 등)는 본문 맥락으로 분기(성폭행 맥락 → Crime, 정치 맥락 → Politics)."),
    "Entertainment": ("인물 중심은 Celebrity News 계열, 작품·프로그램 중심은 Drama TV / TV Shows / Movies / Music. "
        "국적 분기: 한국 인물 → Celebrity News, 해외 인물 → Celebrity News (Foreign). "
        "프로그램 단위 세부 분류는 카테고리가 아닌 엔티티(프로그램명) 단위로 처리."),
    "Business and Finance": ("기업 엔티티는 주력 산업 기준: 테크·플랫폼 → Business, 제조·에너지 → Industries, 금융 → Banking. "
        "삼성전자 분기: 반도체 투자·실적·노사 보도 → Industries, 갤럭시 신제품 보도 → Technology and Computing / Consumer Electronics. "
        "유통 산업 분석·이커머스 기업 동향은 Industries, 소비자 관점 쇼핑 정보는 Home and Garden / Shopping. "
        "부동산은 정책(규제·세제) vs 매물(분양·시세) 분기."),
    "Sports": ("선수·팀·리그·감독·대회 모두 해당 종목 Tier 2로 직접 매핑. 축구·야구만 국내·해외 분기, 그 외 통합. "
        "해외 리그·클럽·대회(EPL·UEL·UCL 등)는 Soccer (International). e스포츠는 제외 · Video Gaming / eSports 로 단일화."),
    "Food and Drink": "가정 요리(레시피) → Cooking, 외식·맛집 → Dining Out. 브랜드 단위면 Dining Out, 음료 제품 중심이면 Beverages.",
    "Travel": "단기 여행 콘텐츠는 Travel, 이민·장기체류·외국생활은 Home and Garden / Lifestyle 로 분류.",
    "Technology and Computing": ("AI 는 Computing 산하(별도 Tier 2 없음). "
        "통신사·반도체 부품 기업이 산업 분석 맥락이면 Business and Finance / Industries 우선."),
    "Books and Literature": "커뮤니티·블로그의 일반 일상 글(UGC)은 Essays 로 1차 분류.",
    "Medical Health": ("선수 부상 보도는 경기 맥락이면 Sports, 의학 정보 중심이면 Diseases and Conditions. "
        "의학·보건·질병 콘텐츠는 Medical Health, 건강관리·운동·식단 등 일상 건강은 Health and Fitness."),
    "Hobbies and Interests": "군대·군인·무기 등 밀리터리 취향 콘텐츠는 Military, 국방·전쟁·안보 시사 기사는 News and Politics.",
    "Health and Fitness": "건강관리·다이어트·운동은 Health and Fitness, 질병·의료·보건·의학계 소식은 Medical Health.",
    "Home and Garden": ("Shopping 은 소비자 관점 쇼핑 정보에 한정(유통 산업 분석은 Business and Finance / Industries). "
        "귀촌·전원생활·외국생활·이민·살림·생활정보는 Lifestyle, 순수 인테리어·공간 꾸미기는 Interior Decorating."),
    "Automotive": ("자동차 브랜드·차종·신차 보도는 Auto Type 통합. 정비·부품은 Auto Repair. "
        "자동차 안전 정책·정부 보급사업은 News and Politics / Politics, 운전자 인구 그룹·사회 이슈는 News and Politics / Society."),
    "Video Gaming": "게임 작품·플레이 콘텐츠는 Video Games, 프로 대회·선수·팀은 eSports.",
    "Religion and Spirituality": "운세·점술은 Spirituality, 제도 종교·교리·묵상·종교계 뉴스는 Religion. 종교 관련 사회 이슈는 News and Politics 우선.",
}


# ── 카테고리 사전화: LLM 자유 출력 → 고정 사전 스냅 ──
#   임베딩 kNN 경로가 없을 때(LLM 직접 생성)도 content_category 가 항상 사전값이
#   되도록 강제한다. 미매칭은 'Unclassified'. 사전 정식 값엔 멱등(그대로 통과).
_T1_LOOKUP = {t.lower(): t for t in IAB_TIER1}
_T2_LOOKUP = {t2.lower(): (t1, t2)                       # tier2(소문자) → (tier1, 정식 tier2)
              for t1, t2s in CONTENT_CATEGORY_TIER2.items() for t2 in t2s}
# LLM 자유 표기 별칭 → 정식 Tier2 스냅 회복(2026-07-02 gold 표본 실측 손실 기준).
# 사전 자체(1312 정의)는 불변 · 별칭만 정식 값으로 흡수.
_T2_LOOKUP.setdefault("world news", ("News and Politics", "International News"))


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
            hit = _T2_LOOKUP.get(parts[1].lower())       # 별칭 회복(같은 Tier1 한정)
            if hit and hit[0] == t1:
                return f"{hit[0]} / {hit[1]}"
        return t1
    # Tier1 미매칭: 조각 중 하나가 Tier2 사전에 있으면 그 Tier1 로 복구
    for p in parts:
        hit = _T2_LOOKUP.get(p.lower())
        if hit:
            return f"{hit[0]} / {hit[1]}"
    return "Unclassified"


def normalize_categories(cmap: dict) -> dict:
    """{엔티티: 카테고리문자열} 전체를 사전화. (구 형식 호환용)"""
    return {e: normalize_content_category(c) for e, c in (cmap or {}).items()}


def normalize_category_list(cats) -> list:
    """콘텐츠 단위 카테고리 N개(1312) → 사전 정식 경로 리스트.
    중복·Unclassified 제거, 순서 보존. 구 dict 형식이 와도 값만 추려 호환."""
    if isinstance(cats, dict):                       # 구 형식(엔티티별) 호환
        cats = list(cats.values())
    if isinstance(cats, str):
        cats = [cats]
    out: list = []
    for c in (cats or []):
        n = normalize_content_category(c)
        if n and n != "Unclassified" and n not in out:
            out.append(n)
    return out

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
