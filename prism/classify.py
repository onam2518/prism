"""임베딩 기반 결정론 분류기 (LLM 의존을 줄인 라우팅·카테고리 분류)."""
from __future__ import annotations
from . import dictionaries as D
from .embed import rank_by_cosine


# 1) 인텐트
# 관점 축(논조 판정)은 라벨 임베딩 kNN으로 판별 불가 → 후보에서 제외하고 LLM 판정을 병합 보존(harness).
PERSPECTIVE_INTENTS = ("옹호·지지", "반박·비판")


def intent_category_anchors(emb, display_name: str) -> dict:
    cats = [c for c in D.intent_categories_for(display_name) if c not in PERSPECTIVE_INTENTS]
    return {c: emb.embed(c, is_query=False) for c in cats}


def merge_perspective(emb_intents: list, llm_intents: list, cap: int = 2) -> list:
    """임베딩 kNN 인텐트에 LLM의 관점 축 판정을 병합. 논조는 kNN이 못 보는 축이라 LLM 값을 보존한다."""
    persp = [v for v in (llm_intents or []) if v in PERSPECTIVE_INTENTS]
    if not persp:
        return list(emb_intents or [])
    base = [c for c in (emb_intents or []) if c not in PERSPECTIVE_INTENTS]
    return (base[: max(0, cap - 1)] + persp[:1])[:cap]


def intent_category_classify(emb, content, top_k=2, min_margin=0.0) -> tuple[list, float]:
    anchors = intent_category_anchors(emb, content.displayServiceName)
    q = emb.embed(_content_text(content), is_query=True)
    ranked = rank_by_cosine(q, anchors)
    if not ranked:
        return [], 0.0
    margin = ranked[0][1] - (ranked[top_k][1] if len(ranked) > top_k else 0.0)
    picks = [c for c, s in ranked[:top_k]]
    return picks, round(margin, 4)


# 2) 콘텐츠 카테고리 (IAB Tier1): KB(임베딩 후보) + LLM 판단 하이브리드
# 한글 키워드 보강 앵커: 한글 엔티티 vs 영문 IAB 라벨의 교차언어 약매칭 보완.
_IAB_KO = {
    "Automotive": "자동차 차량 모빌리티 전기차",
    "Books and Literature": "책 도서 문학 소설 출판",
    "Business and Finance": "기업 경제 산업 비즈니스 증시 주가",
    "Careers": "취업 채용 직장 커리어",
    "Education": "교육 학교 학습 입시 대학",
    "Events and Attractions": "행사 축제 공연 전시 관광지",
    "Family and Relationships": "가족 연애 결혼 육아 관계",
    "Food & Drink": "음식 요리 맛집 식음료",
    "Healthy Living": "건강 운동 다이어트 웰니스 의료",
    "Hobbies & Interests": "취미 여가 게임 복권 로또 도박",
    "Home & Garden": "집 인테리어 주거 가전",
    "Movies": "영화 영상 작품 배우 감독 드라마",
    "Music and Audio": "음악 노래 가수 앨범 콘서트",
    "News and Politics": "뉴스 정치 사회 사건 정부 선거",
    "Personal Finance": "개인 재테크 복권 당첨 대출 보험 투자",
    "Pop Culture": "연예 아이돌 셀럽 가십 방송",
    "Real Estate": "부동산 아파트 분양 매매 전세",
    "Science": "과학 연구 우주 생물",
    "Sports": "스포츠 경기 선수 축구 야구 리그",
    "Style & Fashion": "패션 의류 뷰티 스타일 화장품",
    "Technology & Computing": "기술 IT 인공지능 소프트웨어 전자기기",
}


def _tier1_anchor_text(t1: str) -> str:
    return f"{t1} {_IAB_KO.get(t1, '')}".strip()


def tier1_anchors(emb) -> dict:
    return {t1: emb.embed(_tier1_anchor_text(t1), is_query=False) for t1 in D.IAB_TIER1}


def entity_category_candidates(emb, entity: str, context: str = "", k: int = 3) -> list:
    """임베딩(한글앵커)으로 후보 Tier1 상위 k개(KB 후보 narrowing)."""
    anchors = tier1_anchors(emb)
    q = emb.embed(f"{entity} {context}".strip(), is_query=True)
    return [c for c, _ in rank_by_cosine(q, anchors)[:k]]


import re as _re
_VAGUE_WORDS = ("씨", "모씨", "피해자", "가해자", "네티즌", "누리꾼", "시민", "남성", "여성",
                "남편", "아내", "부모", "자녀", "엄마", "아빠", "경찰", "누나", "동생",
                "유튜버", "기자", "관계자", "직원", "회원", "사람", "일당", "용의자")


def is_vague_entity(e: str) -> bool:
    """카테고리 부여가 무의미한 익명/일반/수치 엔티티 → 분류 제외(Unclassified)."""
    e = (e or "").strip()
    if len(e) <= 1:
        return True
    if _re.fullmatch(r"[A-Za-z]씨", e):                       # A씨, B씨
        return True
    if _re.search(r"(^|\s)(김|이|박|최|정)?모\s?씨$", e):       # 김모 씨, 모 씨
        return True
    if _re.fullmatch(r"[\d,.\s]+(원|％|%|년|월|일|명|개|위|호|회|억|만|천)?", e):  # 수치·회차·금액
        return True
    if any(e == w or e.endswith(w) for w in _VAGUE_WORDS) and len(e) <= 5:
        return True
    return False



def _norm_cat(c: str) -> str:
    """LLM이 약식으로 답한 카테고리명을 IAB Tier1 정식명으로 정규화. 불일치는 Unclassified."""
    valid = list(D.IAB_TIER1)
    if c in valid:
        return c
    cl = (c or "").strip().lower()
    if not cl or cl in ("unclassified", "none", "n/a"):
        return "Unclassified"
    for v in valid:                                  # 접두/부분 일치
        vl = v.lower()
        if vl.startswith(cl) or cl.startswith(vl) or cl in vl or vl.split(" ")[0] == cl:
            return v
    if "tech" in cl:
        return "Technology & Computing"
    if "news" in cl or "politic" in cl:
        return "News and Politics"
    if "finance" in cl or "business" in cl:
        return "Business and Finance"
    return "Unclassified"



def entity_category_classify(emb, entity: str, context: str = "") -> tuple[str, float]:
    anchors = tier1_anchors(emb)
    q = emb.embed(f"{entity} {context}".strip(), is_query=True)
    ranked = rank_by_cosine(q, anchors)
    if not ranked:
        return "Unclassified", 0.0
    top, score = ranked[0]
    margin = score - (ranked[1][1] if len(ranked) > 1 else 0.0)
    return top, round(margin, 4)



class QualityExemplars:
    """라벨된 예시(gold)로 구성한 kNN 인덱스.
    label = {finalGrade, reasons}. 콘텐츠를 임베딩해 최근접 예시들로 투표."""

    def __init__(self, emb):
        self.emb = emb
        self.items = []  # (vec, finalGrade, reasons)

    def fit(self, gold_rows: list):
        from .schema import Content
        for row in gold_rows:
            c = Content.from_dict(row["content"])
            v = self.emb.embed(_content_text(c), is_query=False)
            exp = row.get("expected", {})
            self.items.append((v, exp.get("finalGrade", "G"),
                               tuple(exp.get("reasons", []))))
        return self

    def predict(self, content, k=5):
        """반환: (verdict|None, confidence, reasons). confidence 낮으면 None→LLM 에스컬레이션."""
        if not self.items:
            return None, 0.0, []
        q = self.emb.embed(_content_text(content), is_query=True)
        from .embed import cosine
        scored = sorted(((cosine(q, v), g, r) for v, g, r in self.items),
                        key=lambda t: -t[0])[:k]
        if not scored:
            return None, 0.0, []
        topsim = scored[0][0]
        votes_R = sum(s for s, g, r in scored if g == "R")
        votes_G = sum(s for s, g, r in scored if g == "G")
        total = votes_R + votes_G or 1.0
        if votes_R >= votes_G:
            verdict, conf = "R", votes_R / total
        else:
            verdict, conf = "G", votes_G / total
        # 최근접 예시가 너무 멀면(topsim 낮음) 신뢰 못 함
        conf *= min(1.0, max(0.0, topsim))
        # 다수결 reasons (R 일 때만)
        reasons = []
        if verdict == "R":
            cnt = {}
            for s, g, r in scored:
                if g == "R":
                    for x in r:
                        cnt[x] = cnt.get(x, 0) + s
            reasons = [x for x, _ in sorted(cnt.items(), key=lambda kv: -kv[1])[:3]]
        return verdict, round(conf, 4), reasons


def _content_text(content) -> str:
    return f"{content.title} {content.subtitle} {content.body}".strip()
