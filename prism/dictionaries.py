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


# displayServiceName 분기 후 [범용 + 해당 서비스] 만 프롬프트에 주입한다.
INTENT_CATEGORIES_UNIVERSAL = [
    "사건 경과 보도", "분석·해설", "인물 동정", "정보 전달/팁",
    "리뷰·평가", "의견·논평", "공지·안내", "흥미·화제",
]
INTENT_CATEGORIES_BY_SERVICE = {
    "뉴스":   ["속보", "단독", "기획·심층", "팩트체크"],
    "연예":   ["가십·루머", "작품 홍보", "스케줄·출연", "팬 반응"],
    "스포츠": ["경기 결과", "이적·계약", "선수 인터뷰", "기록·통계"],
    "콘텐츠": ["라이프스타일", "취미·DIY", "큐레이션"],
    "음악":   ["신곡·발매", "차트·순위", "아티스트 소식"],
    "커뮤니티": ["일상 공유", "질문·고민", "정보 공유", "유머"],
    "블로그": ["리뷰·후기", "튜토리얼", "일상 기록"],
    "동영상": ["방송 클립", "예고·하이라이트", "리액션"],
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


# 콘텐츠 카테고리: IAB Content Taxonomy v3.0 기반 자사 사전 (Tier1 21개)
IAB_TIER1 = [
    "Automotive", "Books and Literature", "Business and Finance",
    "Careers", "Education", "Events and Attractions", "Family and Relationships",
    "Food & Drink", "Healthy Living", "Hobbies & Interests", "Home & Garden",
    "Movies", "Music and Audio", "News and Politics", "Personal Finance",
    "Pop Culture", "Real Estate", "Science", "Sports",
    "Style & Fashion", "Technology & Computing",
]
# Tier1 → 대표 Tier2 (자유생성 금지용 화이트리스트 일부; 미수록 Tier2는 검증기가 Tier1만 강제)
IAB_TIER2 = {
    "Business and Finance": ["Industries", "Economy", "Business"],
    "News and Politics": ["Politics", "Society", "Law", "International News"],
    "Technology & Computing": ["Artificial Intelligence", "Consumer Electronics", "Software"],
    "Sports": ["Soccer", "Baseball", "Basketball", "E-Sports"],
    "Pop Culture": ["Celebrity News", "Humor and Satire"],
    "Healthy Living": ["Wellness", "Nutrition"],
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
