"""QA 목업 시드: 콘텐츠 12건(확정 10 + 검수 대기 2) + 검수·교정·정답·평가 판정 예시를 로컬 스토어에 적재.

QA 점검(scripts/seed_qa.py)에서 사용. mock LLM 기준으로
등급(G/R)·검수 대기(YELLOW)·의견 갈림·용도(검수/평가)·골든·버전(v2)·판정까지 전 화면에
데이터가 보이도록 구성한다. 스토어가 비어 있을 때만 시드(멱등).
"""
from __future__ import annotations

QA_REVIEWERS = [("데모 관리자", "boksil"), ("검수자 A", "yonghee"), ("검수자 B", "ddakji")]

# 10건: 서비스 다양화 + 등급 유도(mock 키워드: 충격=clickbait · 쿠폰/최저가=ad · 짧은 본문=shallow)
QA_CONTENTS = [
    {"displayServiceName": "뉴스", "title": "삼성전자 노사 협상 끝내 결렬, 노조 21일부터 총파업",
     "body": "중앙노동위원회 2차 사후조정 최종회의에서 노사가 합의에 이르지 못하고 협상이 결렬됐다. 노동조합은 다음 날인 21일부터 총파업에 돌입하기로 결정했다."},
    {"displayServiceName": "뉴스", "title": "한국은행 기준금리 동결 결정",
     "body": "한국은행 금융통화위원회가 기준금리를 현 수준에서 동결하기로 결정했다. 물가와 가계부채 추이를 지켜보겠다는 취지라고 설명했다."},
    {"displayServiceName": "뉴스", "title": "고령운전자 페달 오조작 방지장치 2차 보급 본격화",
     "body": "경찰청과 유관 기관이 고령운전자 사고 예방을 위한 방지장치 2차 보급 사업을 시작했다. 주행 데이터 기반 효과 검증도 병행한다."},
    {"displayServiceName": "연예", "title": "인기 그룹, 8개월 만에 정규 4집으로 컴백",
     "body": "인기 그룹이 8개월 만에 정규 4집을 발표하며 컴백했다. 타이틀곡은 발매 직후 주요 차트 상위권에 진입했다."},
    {"displayServiceName": "스포츠", "title": "손흥민 시즌 10호골, 토트넘 홈 승리 견인",
     "body": "토트넘이 홈 경기에서 승리했다. 손흥민이 후반 결승골을 터뜨리며 시즌 10호골을 기록했고 팀은 연승을 이어갔다."},
    {"displayServiceName": "스포츠", "title": "구단, 간판 수비수와 3년 재계약 공식 발표",
     "body": "구단이 간판 수비수와 3년 재계약을 공식 발표했다. 지난 시즌 리그 최다 태클을 기록한 핵심 자원이라는 평가다."},
    {"displayServiceName": "콘텐츠", "title": "한눈에 보는 이번 주 경제 브리핑 5선",
     "body": "이번 주 놓치면 안 될 경제 이슈 다섯 가지를 카드 형식으로 정리했다. 금리·환율·부동산 흐름을 3분 안에 볼 수 있다."},
    {"displayServiceName": "커뮤니티", "title": "제주 캠핑장 3곳 다녀온 솔직 후기",
     "body": "지난달 제주 캠핑장 세 곳을 직접 다녀왔다. 예약 팁과 준비물, 자리별 장단점을 사진과 함께 정리해 공유한다."},
    # R 유도 2건: clickbait · ad 키워드(mock 품질 휴리스틱)
    {"displayServiceName": "커뮤니티", "title": "충격 이것 모르면 손해, 결국 발칵 뒤집힌 이유",
     "body": "충격적인 이것을 모르면 결국 손해라는 소문이 발칵 퍼졌다. 자세한 내용은 링크를 확인하라는 식의 전형적인 낚시성 구성이다."},
    {"displayServiceName": "블로그", "title": "최저가 쿠폰 총정리, 지금 구매하면 할인",
     "body": "최저가 쿠폰과 할인 링크를 모았다. 지금 구매하면 추가 할인을 받을 수 있다는 광고성 문구가 반복된다."},
]

# 검수 대기(YELLOW) 2건: mock 은 임베딩 프리필터가 없어 자연 발생하지 않으므로 직접 표기.
# 진척 게이지 분모(yellow_count)·검수 대기 큐(/queue) 화면 검증용.
QA_YELLOW = [
    ({"displayServiceName": "뉴스", "title": "전기요금 개편안 발표, 가구별 영향은",
      "body": "정부가 전기요금 개편안을 발표했다. 사용량 구간별 요율이 조정되며 가구별 영향은 사용 패턴에 따라 갈린다."},
     "신뢰도 중간대역(모의 · conf=0.62)"),
    ({"displayServiceName": "콘텐츠", "title": "신형 전기차 시승기, 겨울철 주행거리 실측",
      "body": "신형 전기차를 일주일간 시승했다. 공인 주행거리와 겨울철 실측치 차이, 충전 요금까지 표로 정리했다."},
     "불일치: emb=G vs llm=R(모의)"),
]


def seed(team=None, verbose=True) -> dict:
    """스토어가 비어 있으면 QA 목업을 적재. 반환: 시드 요약 카운트."""
    from . import serve
    from .store import content_hash

    st = serve.get_store()
    if not st:
        return {"ok": False, "error": "store unavailable"}
    try:
        if st.recent(5):
            return {"ok": True, "skipped": True, "reason": "이미 데이터가 있어 시드 생략"}
    except Exception:
        pass

    serve.sync_prompt()
    # ① 콘텐츠 10건(모의 추출 · 초안 v1)
    for c in QA_CONTENTS:
        serve.run_pipeline(dict(c), mock=True, team=team)

    # ①-b 검수 대기(YELLOW) 2건: 추출 후 review 플래그를 표기해 영속(진척 게이지·대기 큐 검증)
    yellow_pairs = []
    for c, reason in QA_YELLOW:
        r = serve.run_pipeline(dict(c), mock=True, team=team, persist=False)
        out = r.get("output") or {}
        qm = out.setdefault("quality_meta", {})
        qm["review"] = "yellow"
        qm["review_reason"] = reason
        yellow_pairs.append((r["content"], out))
    serve.store_save(yellow_pairs, source="qa", team=team)

    hashes = {c["title"]: content_hash({"displayServiceName": c["displayServiceName"],
                                        "title": c["title"], "subtitle": "", "body": c["body"]})
              for c in QA_CONTENTS}

    # ② 검수자 3인 등록 + 검수 의견(합의 2건 · 의견 갈림 1건 · 오답 1건)
    import time as _t
    now = _t.time()
    for name, char in QA_REVIEWERS:
        try:
            if hasattr(st, "set_reviewer"):
                st.set_reviewer(name, name, char)
        except Exception:
            pass
    fb = [
        ("삼성전자 노사 협상 끝내 결렬, 노조 21일부터 총파업", [("데모 관리자", "good"), ("검수자 A", "good")]),
        ("한국은행 기준금리 동결 결정", [("데모 관리자", "good"), ("검수자 B", "good")]),
        ("손흥민 시즌 10호골, 토트넘 홈 승리 견인", [("데모 관리자", "good"), ("검수자 A", "bad")]),   # 의견 갈림
        ("충격 이것 모르면 손해, 결국 발칵 뒤집힌 이유", [("검수자 B", "bad")]),
    ]
    for title, votes in fb:
        c = next(x for x in QA_CONTENTS if x["title"] == title)
        for rv, v in votes:
            st.save_feedback(hashes[title], c["displayServiceName"], title, v, "review", "", now, reviewer=rv)

    # ③ 교정 1건(DPO 원천) + 골드 문항 응답 + 평가 판정(집단 지성)
    st.log_patch(hashes["한국은행 기준금리 동결 결정"], "데모 관리자", "summary",
                 "한국은행 관련 내용을 정리", "한국은행이 기준금리를 동결하고 물가·가계부채 추이를 관망하기로 했다.")
    st.save_gold_check("goldqa000001", "데모 관리자", "G", "good", True)
    st.save_gold_check("goldqa000002", "데모 관리자", "R", "good", False)
    st.save_eval_check(hashes["충격 이것 모르면 손해, 결국 발칵 뒤집힌 이유"], "데모 관리자", "reject", "R", "G")

    # ④ 용도: 평가용 홀드아웃 2건(검수 목록 제외 확인용)
    st.set_purpose([hashes["고령운전자 페달 오조작 방지장치 2차 보급 본격화"],
                    hashes["구단, 간판 수비수와 3년 재계약 공식 발표"]], "eval", team=team)

    # ⑤ 학습 반영 1회(골든 승격 + batch_seq → 다음 실행 버전 v2 표기)
    batch = serve.learning_batch(team)
    if not batch.get("ok"):
        return batch

    out = {"ok": True, "contents": len(QA_CONTENTS) + len(QA_YELLOW), "yellow": len(QA_YELLOW),
           "feedback": sum(len(v) for _, v in fb),
           "eval_purpose": 2, "golden": (st.golden_count(team) if hasattr(st, "golden_count") else None)}
    if verbose:
        print(f"  [qa] 시드 완료 · 콘텐츠 {out['contents']}(대기 {out['yellow']}) · 검수 {out['feedback']} · "
              f"평가용 2 · 골든 {out['golden']}")
    return out
