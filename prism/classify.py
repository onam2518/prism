"""임베딩 기반 결정론 분류기 (LLM 의존을 줄인 라우팅·카테고리 분류)."""
from __future__ import annotations
from . import dictionaries as D
from .embed import rank_by_cosine


# 1) 인텐트 카테고리
def intent_category_anchors(emb, display_name: str) -> dict:
    cats = D.intent_categories_for(display_name)
    return {c: emb.embed(c, is_query=False) for c in cats}


def intent_category_classify(emb, content, top_k=2, min_margin=0.0) -> tuple[list, float]:
    anchors = intent_category_anchors(emb, content.displayServiceName)
    q = emb.embed(_content_text(content), is_query=True)
    ranked = rank_by_cosine(q, anchors)
    if not ranked:
        return [], 0.0
    margin = ranked[0][1] - (ranked[top_k][1] if len(ranked) > top_k else 0.0)
    picks = [c for c, s in ranked[:top_k]]
    return picks, round(margin, 4)


# 2) 엔티티 카테고리 (IAB Tier1): KB(임베딩 후보) + LLM 판단 하이브리드
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


def recover_with_context(llm, items: list) -> dict:
    """2차 복구: 무문맥 1차에서 Unclassified 된 '고유명사'를 제목 문맥으로 식별해 분류.
    items=[(entity, 대표 제목)] → {entity: Tier1}. (일반명사는 1차에서 이미 분류돼 여기 안 옴)"""
    if not items:
        return {}
    out = {}
    B = 14
    for k in range(0, len(items), B):
        batch = items[k:k + B]
        lines = "\n".join(
            f"{i+1}. {e}  (등장 기사: {(c or '')[:50]})" for i, (e, c) in enumerate(batch)
        )
        sys = (
            "각 엔티티가 '무엇인지' 기사 제목 문맥으로 식별해 아래 IAB Tier1 중 1개로 분류한다"
            "(목록 이름 그대로).\n"
            f"[목록] {' / '.join(D.IAB_TIER1)}\n"
            "- 인물: 배우→Movies, 가수·아이돌·예능인·방송인→Pop Culture, 선수→Sports, "
            "정치인·검사·관료→News and Politics.\n"
            "- 기관: 정부부처·법원·경찰→News and Politics, 기업→Business and Finance.\n"
            "- 지명·행사→Events and Attractions 또는 News and Politics. 작품→Movies/Books/Music.\n"
            "- 그래도 식별 안 되면 'Unclassified'.\n"
            '[출력] {"results":[{"entity":"...","category":"..."}]} JSON 한 줄만.'
        )
        obj, _ = llm.complete_json(sys, lines, tag="recover")
        res = obj.get("results") if isinstance(obj, dict) else None
        bk = {e for e, _ in batch}
        for r in (res or []):
            if isinstance(r, dict) and r.get("entity") in bk:
                out[r["entity"]] = _norm_cat(r.get("category"))
    return out


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


def hybrid_entity_categories(emb, llm, items: list, k: int = 3) -> dict:
    """KB(임베딩 후보) + LLM 판단. items=[(entity, context)] → {entity: Tier1}.
    원칙: '명확히 맞을 때만' 부여하고, 애매하거나 안 맞으면 Unclassified 로 '제외'.
      - 익명/일반/수치 엔티티는 LLM 호출 전 제외(Unclassified)
      - LLM 은 후보 중 명확히 맞는 것만 선택, 아니면 Unclassified(억지 부여 금지)"""
    if not items:
        return {}
    out = {}
    todo = []
    for e, c in items:
        if is_vague_entity(e):
            out[e] = "Unclassified"          # 익명/일반/수치는 제외
        else:
            todo.append((e, c))
    if todo:
        # '엔티티 본질'로만 판단(문맥 미주입 → 혼합주제 콘텐츠에 오염되지 않음).
        # 본질이 불명확한 무명 고유명사는 Unclassified 로 제외(틀린 라벨보다 제외 우선).
        cand = {e: entity_category_candidates(emb, e, "", k) for e, c in todo}
        lines = "\n".join(
            f"{i+1}. {e}  참고후보: {cand[e]}" for i, (e, c) in enumerate(todo)
        )
        sys = (
            "각 엔티티를 아래 IAB Tier1 목록 중 정확히 1개로 분류한다(목록 이름 그대로 사용). "
            "엔티티 자체의 본질로만 판단한다.\n"
            f"[목록] {' / '.join(D.IAB_TIER1)}\n[분류 기준]\n"
            "동물→Science / 식물→Home & Garden / 의약품·질병·운동·다이어트→Healthy Living / "
            "검사·판사·법원·국방·군사·정부기관·국회용어·정치인·사건사고·지역행정(시·군·구청)→News and Politics / "
            "선수·구단·리그→Sports / 배우·영화·드라마·애니메이션·OTT→Movies / 가수·아이돌·예능인·방송사→Pop Culture / "
            "SNS·플랫폼·기기·AI·SW→Technology & Computing / 기업·증시·금융·은행→Business and Finance / "
            "음식·식품·맛집→Food & Drink / 자동차·부품→Automotive / 도서·문학·도서관→Books and Literature / "
            "부동산·아파트·분양→Real Estate / 복권·도박·취미→Hobbies & Interests.\n"
            "- '참고후보'는 임베딩 추천일 뿐 본질과 다르면 무시한다.\n"
            "- 본질이 불명확하거나 익명(A씨)·수치/금액이면 'Unclassified'(억지 분류 금지).\n"
            '[출력] {"results":[{"entity":"...","category":"..."}]} JSON 한 줄만.'
        )
        obj, _ = llm.complete_json(sys, lines, tag="ecat")
        res = obj.get("results") if isinstance(obj, dict) else None
        for r in (res or []):
            if not isinstance(r, dict):
                continue
            e, c = r.get("entity"), r.get("category")
            if e in cand:
                out[e] = _norm_cat(c)
        for e, _c in todo:                   # LLM 누락/오형식만 보수적으로 Unclassified
            out.setdefault(e, "Unclassified")
    return out


# (구) 임베딩 단독: 폴백/호환용
def entity_category_classify(emb, entity: str, context: str = "") -> tuple[str, float]:
    anchors = tier1_anchors(emb)
    q = emb.embed(f"{entity} {context}".strip(), is_query=True)
    ranked = rank_by_cosine(q, anchors)
    if not ranked:
        return "Unclassified", 0.0
    top, score = ranked[0]
    margin = score - (ranked[1][1] if len(ranked) > 1 else 0.0)
    return top, round(margin, 4)


def entities_to_categories(emb, entities: list, context: str = "") -> dict:
    return {e: entity_category_classify(emb, e, context)[0] for e in entities}


# 3) 품질 사전필터: 라벨 예시 kNN (하이브리드 에스컬레이션)
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
