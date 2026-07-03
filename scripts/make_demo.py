"""docs/demo.html 생성 · 현재 앱(serve.PAGE)을 그대로 담은 자체완결 정적 스냅샷.

목적: '최종 구현 현황 그대로' 보이는 데모. 서버·키 없이 브라우저에서 열면
3분할 콘솔(Pretendard·설정 패널·결과)이 실제 구현과 동일하게 렌더된다.

변환:
  · /vendor/* 와 폰트 링크 → CDN(온라인 데모)
  · fetch(/config·/vocab) 를 합성 응답으로 스텁(서버 불필요)
  · 샘플 결과를 미리 주입해 결과 화면까지 보여줌
실행: python3 scripts/make_demo.py
"""
from __future__ import annotations

import json
import os
import time as _time0


def _dt_learn_next():
    """데모: 검수 목표(퀘스트) 일시 = 빌드 시점 + 2일(D-2 표시)."""
    return _time0.strftime("%Y-%m-%dT%H:%M", _time0.localtime(_time0.time() + 2 * 86400 + 3600))


from prism.serve import PAGE, dict_data as _dict_data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 샘플 결과(이미지 → 시각 이해 → 메타). 실제 출력 스키마와 동일 ──
DEMO_RESULT = {
    "source": "image",
    "mock": False,
    "content": {
        "displayServiceName": "뉴스",
        "title": "삼성전자 노조 임금 협상 결렬",
        "subtitle": "",
        "body": "기자회견장에서 노조 집행부가 발언하는 장면. 중앙노동위원회 조정에서 합의 불성립.",
    },
    "signals": [{
        "vision": "이미지는 실내 기자회견장을 담고 있다. 노동조합 집행부로 보이는 인물들이 단상에 앉아 "
                  "발언하고 있으며, 배경 현수막에 협상 관련 문구가 보인다. 전체적으로 노사 협상 결렬을 "
                  "알리는 공식 발표 현장의 분위기다. [엔티티] 삼성전자, 전국삼성전자노동조합, 중앙노동위원회 "
                  "[장면] 노사 협상 결렬 기자회견",
        "ocr": "중앙노동위원회 조정 불성립 · 임금 인상 협상 결렬",
        "latency_ms": 2840,
    }],
    "output": {
        "item_meta": {
            "summary": "삼성전자가 중앙노동위 조정에서 노조와 합의에 이르지 못했다",
            "entities": ["삼성전자", "전국삼성전자노동조합", "중앙노동위원회"],
            "intent": ["사실 전달", "분석·해설"],
            "content_category": ["Business / Industries", "Law, Govt & Politics"],
        },
        "quality_meta": {"finalGrade": "G"},
        "routing": {"content_track": "text"},
    },
}

DEMO_CONFIG = {
    "hasKey": True, "persisted": True, "model": "solar-pro3-260323",
    "baseUrl": "https://api.upstage.ai/v1/solar", "reasoning": "default",
    "systemPrompt": "", "configured": True, "forcedMock": False,
    "hasBizKey": False, "bizPersisted": False, "hasTimelyKey": False, "timelyPersisted": False,
    "textProvider": "solar", "textModel": "",
    "visionProvider": "upstage_ie", "visionModel": "",
    # 운영 모델(현재 버전): Supabase 백엔드 · 키는 서버(관리자) 관리 → API 설정 UI 숨김.
    # 데모는 팀 관리자 시점으로 표시(자동 인입·팀 관리 노출). authRequired=false 로 로그인 벽 생략.
    "backend": "supabase", "authRequired": False, "keyManagedByServer": True,
    "learnNextAt": _dt_learn_next(),
}

# 기준 계약·계열 래퍼(코드 원천에서 그대로) → 데모 스튜디오도 실제 계약을 표시
from prism import meta_prompts as _MP
DEMO_CONFIG.update({
    "availableModels": ["solar-pro3-260323", "gpt-5.4-mini", "gpt-5.4", "claude-sonnet-4.6", "gemini-2.5-pro"],
    "metaFourCalls": True, "metaCallModels": {"category": "gpt-5.4"}, "familyWrappers": {},
    "familyWrapperDefaults": dict(_MP.FAMILY_WRAPPER_DEFAULT),
    "metaCalls": list(_MP.CALLS),
    "metaContract": {"rules": dict(_MP.CALL_RULES), "examples": _MP.gold_examples(None)},
})

# 데모 관리자 컨텍스트(/admin 스텁) · isAdmin=true 로 자동 인입·팀 관리 노출
DEMO_ADMIN = {
    "ok": True, "isAdmin": True, "isSysAdmin": True,
    "team": {"name": "데모팀", "invite_code": "DEMO-1234", "created_by": "demo-admin"},
    "members": [
        {"id": "demo-admin", "name": "데모 관리자", "char": "boksil", "avatar": "boksil", "is_admin": True},
        {"id": "m2", "name": "검수자 A", "char": "yonghee", "avatar": "yonghee", "is_admin": True},
        {"id": "m3", "name": "검수자 B", "char": "ddakji", "avatar": "ddakji", "is_admin": False},
    ],
    "goldenCount": 24,
}
import time as _time
DEMO_ARENA = {
    "next_version": 3, "next_batch_at": _time.time() + 2 * 86400 + 3600,   # 데모: 시한 D-2 표시
    "accuracy": 0.91, "good": 10, "bad": 2, "reviews": 62, "week_reviews": 18,
    "accuracy_delta": 0.04, "target": 0.9, "queue": 3, "total_targets": 80, "team_progress": 0.50,
    "leaderboard": [
        {"reviewer": "데모 관리자", "name": "데모 관리자", "char": "boksil", "level": 4, "points": 640, "reviews": 62, "corrections": 9, "streak": 7, "week_points": 180, "last_week_points": 120, "progress": 0.78,
         "gold_n": 12, "gold_acc": 0.92, "quality_mult": 0.96, "consensus_matches": 34, "split_reviews": 6, "patches": 5, "golden_contribs": 11, "agree_rate": 0.94},
        {"reviewer": "검수자 A", "name": "검수자 A", "char": "yonghee", "level": 3, "points": 420, "reviews": 41, "corrections": 5, "streak": 3, "week_points": 150, "last_week_points": 160, "progress": 0.51,
         "gold_n": 8, "gold_acc": 0.88, "quality_mult": 0.94, "consensus_matches": 22, "split_reviews": 4, "patches": 3, "golden_contribs": 7, "agree_rate": 0.9},
        {"reviewer": "검수자 B", "name": "검수자 B", "char": "ddakji", "level": 2, "points": 180, "reviews": 17, "corrections": 1, "streak": 1, "week_points": 40, "last_week_points": 90, "progress": 0.21,
         "gold_n": 3, "gold_acc": 0.67, "quality_mult": 1.0, "consensus_matches": 8, "split_reviews": 1, "patches": 1, "golden_contribs": 2, "agree_rate": 0.78},
    ],
    "missions": [
        {"id": "daily5", "label": "오늘의 검수", "total": 5, "bonus": 20, "done": 3, "completed": False},
        {"id": "gold1", "label": "골드 정답", "total": 1, "bonus": 15, "done": 1, "completed": True},
        {"id": "split1", "label": "불일치 재검토", "total": 1, "bonus": 15, "done": 0, "completed": False},
        {"id": "fill1", "label": "분류 채우기", "total": 1, "bonus": 10, "done": 0, "completed": False},
    ],
}

# ── 골든셋·학습·평가 예시(리포트·모델 비교·로우 데이터까지 데모에서 보이도록) ──
DEMO_EXTRAS = {
    "gstat": {"ok": True, "total": 24, "source_counts": {"review": 18, "manual": 6},
              "last_batch": {"confirmed": 18, "new": 3, "demoted": 1, "need_category": 2, "disagree": 3, "min_good": 1},
              "need_list": [{"hash": "n1", "title": "집에서 만드는 김치볶음밥 레시피", "service": "블로그"},
                            {"hash": "n2", "title": "주말 캠핑장 예약 꿀팁", "service": "커뮤니티"}],
              "ts": 1782950000},
    "glist": {"ok": True, "total": 24, "source_counts": {"review": 18, "manual": 6}, "items": [
        {"hash": "g1", "title": "삼성전자 노조 임금 협상 결렬", "service": "뉴스", "grade": "G", "category": ["News and Politics / Society"], "source": "review", "flagged": False, "model": "solar-pro3-260323", "version": 2},
        {"hash": "g2", "title": "한국은행 기준금리 동결 결정", "service": "뉴스", "grade": "G", "category": ["Business and Finance / Economy"], "source": "review", "flagged": False, "model": "solar-pro3-260323", "version": 2},
        {"hash": "g3", "title": "낚시성 제목 사례", "service": "커뮤니티", "grade": "R", "category": ["Entertainment"], "source": "manual", "flagged": True, "model": "", "version": None},
        {"hash": "g4", "title": "손흥민 시즌 15호골", "service": "스포츠", "grade": "G", "category": ["Sports / Soccer (International)"], "source": "review", "flagged": False, "model": "gpt-5.4-mini", "version": 1, "fix_needed": True}]},
    "next_batch_at": _time.time() + 2 * 86400 + 3600,   # 데모: 반영 일정 카드 '다음 예정' 예시(D-2)
    "lreport": {"ok": True, "ts": 1782950000, "grade_accuracy": 0.87,
                "golden": {"ok": True, "confirmed": 18, "new": 3, "demoted": 1, "total": 24, "need_category": 2, "disagree": 3, "min_good": 1},
                "eval": {"ok": True, "n": 24, "grade_accuracy": 0.87, "grade_ci": {"lo": 0.7365, "hi": 1.0, "n": 24}},
                "improve": {"results": {
                    "analyze": {"directive": "콘텐츠 카테고리는 인물보다 본문 주제 기준으로 부여하라. 연예 인물의 스포츠 활동은 Sports 로 분류.", "ambiguities": ["연예·스포츠 겹침 처리 기준"]},
                    "judge": {"directive": "구매 링크·쿠폰 문구가 본문에 있으면 ad 사유를 우선 검토하라.", "ambiguities": []}}}},
    "ldata": {"ok": True, "golden_n": 24, "grade_dist": {"G": 19, "R": 5},
              "coverage": [{"cls": "News and Politics", "have": 9, "lack": 0}, {"cls": "Business and Finance", "have": 6, "lack": 2},
                           {"cls": "Sports", "have": 4, "lack": 4}, {"cls": "Entertainment", "have": 3, "lack": 5},
                           {"cls": "Food and Drink", "have": 1, "lack": 7}, {"cls": "Travel", "have": 1, "lack": 7},
                           {"cls": "Technology and Computing", "have": 0, "lack": 8}, {"cls": "Medical Health", "have": 0, "lack": 8}],
              "covered": 1, "class_total": 21, "per_class_target": 8,
              "alpha": 0.72, "agreement": 0.88, "multi_units": 14,
              "reviewers": [{"reviewer": "데모 관리자", "n": 62, "agree_rate": 0.94, "gold_n": 12, "gold_acc": 0.92, "ds_error": 0.06},
                            {"reviewer": "검수자 A", "n": 41, "agree_rate": 0.9, "gold_n": 8, "gold_acc": 0.88, "ds_error": 0.09},
                            {"reviewer": "검수자 B", "n": 17, "agree_rate": 0.78, "gold_n": 3, "gold_acc": 0.67, "ds_error": 0.21}],
              "acc_ci": {"acc": 0.87, "n": 24, "lo": 0.7365, "hi": 1.0},
              "label_flags": [{"hash": "g3", "title": "낚시성 제목 사례", "expected": "R", "got": "G"}],
              "split": [{"hash": "s1", "n": 3, "good": 1, "bad": 2}], "split_n": 3,
              "extractable": {"sft": 24, "dpo": 11, "rationale": 9},
              "requirements": [
                  {"kind": "분류 부트스트랩(클래스당 8)", "target": 168, "have": 24, "lack": 121, "basis": "SetFit · Tunstall et al. 2022 · arXiv:2209.11055"},
                  {"kind": "SFT 정렬(고품질)", "target": 1000, "have": 24, "lack": 976, "basis": "LIMA · Zhou et al. 2023 · arXiv:2305.11206"},
                  {"kind": "운영급 분류 LLM", "target": 13500, "have": 24, "lack": 13476, "basis": "Llama Guard · Inan et al. 2023 · arXiv:2312.06674"},
                  {"kind": "선호쌍(DPO · 참고 상한)", "target": 33000, "have": 11, "lack": 32989, "basis": "DPO · Rafailov et al. 2023 + InstructGPT RM 33k · Ouyang et al. 2022"},
                  {"kind": "평가셋(큐레이션)", "target": 100, "have": 24, "lack": 76, "basis": "tinyBenchmarks · Maia Polo et al. 2024 + Miller 2024(CI 병기)"}]},
    "cmp": {"ok": True, "golden_n": 24, "best": "solar-pro3-260323",
            "models": [
                {"model": "solar-pro3-260323", "route": "solar", "real": True, "n": 24, "grade_accuracy": 0.87, "reason_jaccard": 0.81, "reason_exact_match": 0.71, "empty_rate": 0.0, "cost_usd": 0.0102, "tokens": {"in": 48210, "out": 6120}},
                {"model": "gpt-5.4-mini", "route": "timely", "real": True, "n": 24, "grade_accuracy": 0.83, "reason_jaccard": 0.77, "reason_exact_match": 0.63, "empty_rate": 0.0, "cost_usd": 0.0089, "tokens": {"in": 47100, "out": 5480}},
                {"model": "claude-haiku-4-5", "route": "timely", "real": True, "n": 24, "grade_accuracy": 0.79, "reason_jaccard": 0.74, "reason_exact_match": 0.58, "empty_rate": 0.04, "cost_usd": 0.0075, "tokens": {"in": 46800, "out": 5010}}],
            "skipped": [{"model": "gemini-2.5-pro", "reason": "라우터 키 없음(BizRouter·Timely)"}]},
    "evalg": {"basis": {"model": "solar-pro3-260323", "version": 3, "scope": "all"}, "min_good": 1, "detail": [{"hash": "g3", "title": "낚시성 제목 사례", "expected": "R", "got": "G", "judge": {"adopt": 0, "reject": 2, "reviewers": {}}}, {"hash": "g4", "title": "손흥민 시즌 15호골", "expected": "G", "got": "R", "judge": {"adopt": 2, "reject": 0, "reviewers": {}}}], "ok": True, "grade_accuracy": 0.87, "grade_ci": {"lo": 0.7365, "hi": 1.0, "n": 24},
              "reason_jaccard": 0.81, "harm_miss_rate": 0.04, "empty_rate": 0.0, "evaluated": 24,
              "by_reason_bucket": {"normal": {"n": 15, "grade_acc": 0.93}, "clickbait": {"n": 5, "grade_acc": 0.8}, "ad": {"n": 4, "grade_acc": 0.75}}},
    "raw": {"ok": True, "n": 3, "items": [
        {"hash": "d1", "service": "뉴스", "title": "삼성전자 노조 임금 협상 결렬", "grade": "G", "reasons": [], "category": ["News and Politics / Society"], "model": "solar-pro3-260323", "version": 2, "review": "", "split": False, "fb": {"verdict": "good", "n": 2, "ts": 1782800000}, "body": "삼성전자가 중앙노동위원회 조정에서 노조와 합의에 이르지 못했다.",
         "item_meta": {"summary": "삼성전자가 중앙노동위 조정에서 노조와 합의에 이르지 못했다", "entities": ["삼성전자", "전국삼성전자노동조합", "중앙노동위원회"], "intent": ["사건 경과 보도"], "content_category": ["News and Politics / Society"]},
         "quality_meta": {"finalGrade": "G", "reasons": [], "review": "", "confidence": 0.91}},
        {"hash": "d2", "service": "뉴스", "title": "한국은행 기준금리 동결 결정", "grade": "G", "reasons": [], "category": ["Business and Finance / Economy"], "model": "solar-pro3-260323", "version": 2, "review": "", "split": False, "fb": {"verdict": "", "n": 0, "ts": 0}, "body": "한국은행이 기준금리를 현 수준에서 동결하기로 결정했다.",
         "item_meta": {"summary": "한국은행이 기준금리를 현 수준에서 동결하기로 결정했다", "entities": ["한국은행", "금리"], "intent": ["사건 경과 보도"], "content_category": ["Business and Finance / Economy"]},
         "quality_meta": {"finalGrade": "G", "reasons": [], "review": "", "confidence": 0.88}},
        {"hash": "d3", "service": "커뮤니티", "title": "낚시성 제목 사례", "grade": "R", "reasons": ["clickbait"], "category": [], "model": "gpt-5.4-mini", "version": 1, "review": "yellow", "split": True, "fb": {"verdict": "", "n": 2, "ts": 0}, "body": "본문과 무관한 자극적 제목으로 클릭을 유도한 사례.",
         "item_meta": {"summary": "제목과 본문 괴리로 클릭을 유도한 사례", "entities": [], "intent": ["흥미·화제"], "content_category": []},
         "quality_meta": {"finalGrade": "R", "reasons": ["clickbait"], "review": "yellow", "confidence": 0.52, "review_reason": "제목·본문 불일치 확신 낮음"}}]},
    "rqueue": {"ok": True, "n": 3, "items": [
        {"hash": "q1", "service": "커뮤니티", "title": "낚시성 제목 사례", "grade": "R", "review_reason": "제목·본문 불일치 확신 낮음", "reviewed": False, "split": False, "confidence": 0.52, "model": "solar-pro3-260323"},
        {"hash": "q2", "service": "뉴스", "title": "연예인 A·B 열애설 보도", "grade": "G", "review_reason": "사생활 보도 경계 사례", "reviewed": True, "split": True, "confidence": 0.66, "model": "solar-pro3-260323"},
        {"hash": "gold:ok:demo1", "service": "뉴스", "title": "국회 예산안 표결 처리", "grade": "G", "review_reason": "", "reviewed": False, "split": False, "confidence": None, "model": "solar-pro3-260323"}]},
    "mstats": {"ok": True, "models": [
        {"model": "solar-pro3-260323", "version": 2, "key": "solar-pro3-260323 · v2", "n": 6, "gPct": 92, "avgLead": 40,
         "intents": ["사건 경과 보도 (4)", "분석·해설 (2)"], "categories": ["News and Politics (4)"], "reasons": []},
        {"model": "solar-pro3-260323", "version": 1, "key": "solar-pro3-260323 · v1", "n": 3, "gPct": 78, "avgLead": 36,
         "intents": ["사건 경과 보도 (1)", "분석·해설 (2)"], "categories": ["Business and Finance (3)"], "reasons": ["clickbait (1)"]},
        {"model": "gpt-5.4-mini", "version": 1, "key": "gpt-5.4-mini · v1", "n": 3, "gPct": 67, "avgLead": 34,
         "intents": ["흥미·화제 (2)"], "categories": ["Entertainment (2)"], "reasons": ["clickbait (1)"]}]},
    "drafts": {"ok": True, "n": 2, "items": [
        {"label": "solar-pro3-260323 · v2 (현재)", "model": "solar-pro3-260323", "version": 2,
         "item_meta": {"summary": "삼성전자가 중앙노동위 조정에서 노조와 합의에 이르지 못했다", "entities": ["삼성전자", "전국삼성전자노동조합"], "intent": ["사건 경과 보도"], "content_category": ["News and Politics / Society"]},
         "quality_meta": {"finalGrade": "G", "reasons": []}},
        {"label": "gpt-5.4-mini · 이전(rerun)", "model": "gpt-5.4-mini", "version": None,
         "item_meta": {"summary": "삼성 노사가 임금 협상에서 결렬됐다", "entities": ["삼성전자"], "intent": ["속보·사건 추적"], "content_category": ["Business and Finance / Business"]},
         "quality_meta": {"finalGrade": "G", "reasons": []}}]},
    "ingest": {"running": True, "scheduler": True, "jobs": [
        {"id": "src1", "name": "뉴스 수집 API", "running": True, "trigger": "auto", "total": 40, "done": 25, "last_msg": "25/40 처리 중", "endpoint": "https://crawler.example/items"},
        {"id": "job2", "name": "엑셀 일괄 추출", "running": True, "trigger": "manual", "total": 12, "done": 7, "last_msg": "7/12 처리 중", "endpoint": ""}]},
}
DEMO_VOCAB = {"groups": ["뉴스", "연예", "스포츠", "콘텐츠", "커뮤니티", "블로그", "음악", "동영상"]}

DEMO_DASH = {
    "n": 12, "g": 10, "r": 2, "gPct": 83, "entities": 31, "avgLead": 38,
    "intents": [{"k": "사건 경과 보도", "v": 7, "pct": 58}, {"k": "분석·해설", "v": 5, "pct": 42},
                {"k": "인물 동향", "v": 3, "pct": 25}, {"k": "흥미·화제", "v": 2, "pct": 17}],
    "categories": [{"k": "News and Politics", "v": 6, "pct": 50}, {"k": "Sports", "v": 4, "pct": 33},
                   {"k": "Business and Finance", "v": 3, "pct": 25}],
    "qualityReasons": [{"k": "clickbait", "v": 2, "pct": 17}],
    "feedback": {"total": 62, "good": 48, "bad": 14, "learned": 9, "contents": 12, "reviewers": 3, "split": 3},
    "contents": [
        {"hash": "d1", "title": "삼성전자 노조 임금 협상 결렬", "service": "뉴스", "grade": "G", "source": "자동 인입",
         "summary": "삼성전자가 중앙노동위 조정에서 노조와 합의에 이르지 못했다",
         "entities": ["삼성전자", "전국삼성전자노동조합"], "intent": ["사건 경과 보도"],
         "category": ["News and Politics / Society"], "reasons": [], "model": "solar-pro3-260323", "version": 2, "purpose": "review", "fb": {"verdict": "good", "ts": 1782800000}},
        {"hash": "d2", "title": "한국은행 기준금리 동결 결정", "service": "뉴스", "grade": "G", "source": "배치",
         "summary": "한국은행이 기준금리를 현 수준에서 동결하기로 결정했다",
         "entities": ["한국은행", "금리"], "intent": ["사건 경과 보도"],
         "category": ["Business and Finance / Economy"], "reasons": [], "model": "solar-pro3-260323", "version": 2, "purpose": "eval", "fb": {}},
        {"hash": "d3", "title": "낚시성 제목 사례", "service": "커뮤니티", "grade": "R", "source": "단건",
         "summary": "제목과 본문 괴리로 클릭을 유도한 사례", "entities": [], "intent": ["흥미·화제"],
         "category": [], "reasons": ["clickbait"], "model": "gpt-5.4-mini", "version": 1, "purpose": "review", "fb": {"verdict": "bad", "ts": 1782800000}},
    ],
}
DEMO_TOPICS = {
    "n_contents": 12, "summary": {"single": 2, "composite": 1, "filter": 8},
    "single": [{"cluster_id": "S-samsung", "entities": ["삼성전자", "노동조합"], "n_contents": 3},
               {"cluster_id": "S-rate", "entities": ["한국은행", "금리"], "n_contents": 2}],
    "composite": [{"cluster_id": "C-labor", "rep_entities": ["삼성전자", "중앙노동위"], "n_contents": 4}],
    "filter": [{"cluster_id": "F-fin", "name": "재테크 × 심층 분석", "active": True, "n_contents": 3},
               {"cluster_id": "F-ent", "name": "연예 × 화제성", "active": False}],
}
DEMO_USER = {
    "source": "실 행동 로그 → 소비 형태·강도 (데모)", "n_contents": 12,
    "users": [
        {"user_id": "u1", "persona": "정독러", "form": {"세션 길이": "장", "체류·완주": "고", "전환·이동": "느림", "깊이": "몰입", "시간대": "평일 야간"},
         "intensity": {"심층 분석": "고", "정책·사업 소개": "고", "속보·단신": "저"},
         "affinity_entities": [["삼성전자", 4], ["금리", 3], ["재건축", 2]],
         "engagement": {"views": 18, "clicks": 14, "click_rate": 0.78, "avg_dwell_sec": 52.4}},
        {"user_id": "u2", "persona": "스낵러", "form": {"세션 길이": "단", "체류·완주": "저", "전환·이동": "빠름", "깊이": "훑기", "시간대": "출퇴근"},
         "intensity": {"흥미·화제": "중", "속보·단신": "저"},
         "affinity_entities": [["손흥민", 2]],
         "engagement": {"views": 22, "clicks": 5, "click_rate": 0.23, "avg_dwell_sec": 9.1}},
    ],
    "personas_def": [
        {"id": 1, "name": "정독러", "full": "깊이 정독러", "desc": "한 주제를 파고들어 정독·저장", "form": {"깊이": "몰입", "체류·완주": "고"}},
        {"id": 2, "name": "스낵러", "full": "가벼운 스낵러", "desc": "짧은 세션·빠른 전환", "form": {"깊이": "훑기", "체류·완주": "저"}},
    ],
    "formula": "소비 강도 = 맥락(인텐트)별 Σ(체류/30 × 클릭가중)의 상대 등급(저/중/고)",
}

# CDN 매핑(자체완결 온라인 데모)
CDN_TAILWIND = "https://cdn.tailwindcss.com/3.4.16"
CDN_ALPINE = "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js"
CDN_PRETENDARD = ("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/"
                  "dist/web/static/pretendard.min.css")
# GmarketSans woff(projectnoonnu/jsdelivr) → @font-face 인라인. 로컬 번들과 동일 소스.
_GM = "https://fastly.jsdelivr.net/gh/projectnoonnu/noonfonts_2001@1.1"
GMARKET_CDN_CSS = (
    "<style>"
    f"@font-face{{font-family:'GmarketSans';font-weight:300;font-display:swap;src:url('{_GM}/GmarketSansLight.woff') format('woff')}}"
    f"@font-face{{font-family:'GmarketSans';font-weight:500;font-display:swap;src:url('{_GM}/GmarketSansMedium.woff') format('woff')}}"
    f"@font-face{{font-family:'GmarketSans';font-weight:700;font-display:swap;src:url('{_GM}/GmarketSansBold.woff') format('woff')}}"
    "</style>")

STUB = """<script>
  // 정적 데모: 서버 호출을 합성 응답으로 스텁(키·서버 불필요)
  // 홈 위젯 레이아웃 시드(쇼케이스 · 실제 앱은 빈 상태로 시작)
  try { localStorage.setItem('prism_home', JSON.stringify(['launch-run','launch-batch','launch-dict','metrics','quality','intents','categories','process'])); } catch (e) {}
  // 데모 로그인 시드(관리자) → 로그인 벽 생략 + 자동 인입·팀 관리 노출
  try { localStorage.setItem('prism_reviewer', '데모 관리자'); localStorage.setItem('prism_reviewer_char', 'boksil'); localStorage.setItem('prism_token', 'demo'); } catch (e) {}
  window.__DEMO_RESULT__ = %s;
  (function () {
    const J = (o) => ({ ok: true, json: () => Promise.resolve(o), text: () => Promise.resolve('') });
    const CFG = %s, VOCAB = %s, ADMIN = %s, ARENA = %s, EX = %s;
    const PV_SAMPLE = %s;
    const real = window.fetch ? window.fetch.bind(window) : null;
    window.fetch = function (url, opt) {
      const u = String(url);
      if (u.indexOf('/config') === 0 || u.indexOf('/config') > -1) return Promise.resolve(J(CFG));
      if (u.indexOf('/vocab') > -1) return Promise.resolve(J(VOCAB));
      if (u.indexOf('/admin') > -1) return Promise.resolve(J(ADMIN));
      if (u.indexOf('/arena') > -1) return Promise.resolve(J(ARENA));
      if (u.indexOf('/drill') > -1) { const qp = new URLSearchParams((u.split('?')[1]||'')); return Promise.resolve(J({ ok: true, kind: qp.get('kind')||'intent', value: qp.get('value')||'', items: [
        {hash:'d1', title:'삼성전자 노조 임금 협상 결렬', subtitle:'중앙노동위 조정 불성립', service:'뉴스', grade:'G', fb:{verdict:'good', ts: 1782800000}, summary:'삼성전자가 중앙노동위 조정에서 노조와 합의에 이르지 못했다', entities:['삼성전자','전국삼성전자노동조합','중앙노동위원회'], intent:['사건 경과 보도'], category:['News and Politics / Society'], reasons:[], url:''},
        {hash:'d2', title:'한국은행 기준금리 동결 결정', subtitle:'', service:'뉴스', grade:'G', summary:'한국은행이 기준금리를 현 수준에서 동결하기로 결정했다', entities:['한국은행','금리'], intent:['사건 경과 보도'], category:['Business and Finance / Economy'], reasons:[], url:''},
        {hash:'d3', title:'낚시성 제목 사례', subtitle:'', service:'커뮤니티', grade:'R', summary:'제목과 본문 괴리로 클릭을 유도한 사례', entities:[], intent:['흥미·화제'], category:[], reasons:['clickbait'], url:''}
      ], n: 3 })); }
      if (u.indexOf('/topic-drill') > -1) { const qp = new URLSearchParams((u.split('?')[1]||'')); return Promise.resolve(J({ ok: true, kind: 'topic', value: qp.get('cluster')||'토픽', items: [
        {hash:'t1', title:'삼성전자 노조 임금 협상 결렬', subtitle:'중앙노동위 조정 불성립', service:'뉴스', grade:'G', fb:{verdict:'good', ts: 1782800000}, summary:'삼성전자가 중앙노동위 조정에서 노조와 합의에 이르지 못했다', entities:['삼성전자','전국삼성전자노동조합'], intent:['사건 경과 보도'], category:['News and Politics / Society'], reasons:[], url:''},
        {hash:'t2', title:'삼성전자 3분기 실적 발표', subtitle:'', service:'뉴스', grade:'G', summary:'삼성전자가 3분기 잠정 실적을 발표했다', entities:['삼성전자'], intent:['사건 경과 보도'], category:['Business and Finance / Economy'], reasons:[], url:''}
      ], n: 2 })); }
      if (u.indexOf('/badges') > -1) { let e = []; try { e = JSON.parse((opt&&opt.body)||'{}').earned || []; } catch (x) {} return Promise.resolve(J({ ok: true, badges: e })); }
      if (u.indexOf('/reviewer') > -1) return Promise.resolve(J({ ok: true, team: { invite_code: ADMIN.team.invite_code } }));
      if (u.indexOf('/auth') > -1) return Promise.resolve(J({ ok: true, access_token: 'demo' }));
      if (u.indexOf('/models') > -1) return Promise.resolve(J({ ok: true, models: ['solar-pro3-260323', 'solar-pro2-251215'] }));
      if (u.indexOf('/ping') > -1) return Promise.resolve(J({ ok: true, detail: 'solar-pro3-260323 응답 정상' }));
      if (u.indexOf('/dashboard') > -1) return Promise.resolve(J(%s));
      if (u.indexOf('/topics') > -1) return Promise.resolve(J(%s));
      if (u.indexOf('/usermeta') > -1) return Promise.resolve(J(%s));
      if (u.indexOf('/dict') > -1) return Promise.resolve(J(%s));
      if (u.indexOf('/rerun') > -1 || u.indexOf('/run') > -1) return Promise.resolve(J(window.__DEMO_RESULT__));
      // 골든셋·학습·평가 예시(리포트 · 모델 비교 · 로우 데이터)
      if (u.indexOf('/golden-status') > -1) return Promise.resolve(J(EX.gstat));
      if (u.indexOf('/golden-list') > -1) return Promise.resolve(J(EX.glist));
      if (u.indexOf('/golden-remove') > -1 || u.indexOf('/golden') > -1) return Promise.resolve(J({ ok: true }));
      if (u.indexOf('/learn-report') > -1) return Promise.resolve(J({ ok: true, report: EX.lreport, next_batch_at: EX.next_batch_at }));
      if (u.indexOf('/learn-batch') > -1) return Promise.resolve(J(EX.lreport));
      if (u.indexOf('/learn-data') > -1) return Promise.resolve(J(EX.ldata));
      if (u.indexOf('/learn-export') > -1) return Promise.resolve(J({ ok: true }));
      if (u.indexOf('/compare-models') > -1) return Promise.resolve(J(EX.cmp));
      if (u.indexOf('/learn-spec') > -1) {
        const md = '# (데모) 파인튜닝 소요서 예시' + String.fromCharCode(10) + '실서비스에서는 축적 현황과 논문 기준치를 대비한 소요서가 생성됩니다.';
        return Promise.resolve({ ok: true, blob: () => Promise.resolve(new Blob([md], { type: 'text/markdown' })), json: () => Promise.resolve({ ok: true }), text: () => Promise.resolve(md) });
      }
      if (u.indexOf('/prompt-snapshot') > -1) return Promise.resolve(J({ ok: false, snapshot: null }));
      if (u.indexOf('/prompt-preview') > -1) {
        const q = new URLSearchParams(u.split('?')[1] || '');
        return Promise.resolve(J({ ok: true, family: 'solar', call: q.get('call') || 'summary',
          system: PV_SAMPLE, user: 'displayServiceName: 뉴스' + String.fromCharCode(10) + 'title: (미리보기)' + String.fromCharCode(10) + 'body: (미리보기 본문)' }));
      }
      if (u.indexOf('/purpose') > -1) return Promise.resolve(J({ ok: true, n: 1 }));
      if (u.indexOf('/eval-judge') > -1) return Promise.resolve(J({ ok: true, judge: { adopt: 1, reject: 0, reviewers: {} } }));
      if (u.indexOf('/eval-golden') > -1) return Promise.resolve(J(EX.evalg));
      if (u.indexOf('/raw') > -1) return Promise.resolve(J(EX.raw));
      if (u.indexOf('/drafts') > -1) return Promise.resolve(J(EX.drafts));
      if (u.indexOf('/model-stats') > -1) return Promise.resolve(J(EX.mstats));
      if (u.indexOf('/patch-meta') > -1 || u.indexOf('/feedback') > -1) return Promise.resolve(J({ ok: true, feedback: { total: 62, good: 48, bad: 14, learned: 9, contents: 12, reviewers: 3, split: 3 } }));
      if (u.indexOf('/reap') > -1) return Promise.resolve(J({ ok: true, items: [] }));
      if (u.indexOf('/queue') > -1) return Promise.resolve(J(EX.rqueue));
      if (u.indexOf('/ingest') > -1) return Promise.resolve(J(EX.ingest));
      return real ? real(url, opt) : Promise.resolve(J({}));
    };
  })();
</script>
""" % (json.dumps(DEMO_RESULT, ensure_ascii=False),
       json.dumps(DEMO_CONFIG, ensure_ascii=False),
       json.dumps(DEMO_VOCAB, ensure_ascii=False),
       json.dumps(DEMO_ADMIN, ensure_ascii=False),
       json.dumps(DEMO_ARENA, ensure_ascii=False),
       json.dumps(DEMO_EXTRAS, ensure_ascii=False),
       json.dumps(_MP.call_system("solar-pro3-260323", "summary"), ensure_ascii=False),
       json.dumps(DEMO_DASH, ensure_ascii=False),
       json.dumps(DEMO_TOPICS, ensure_ascii=False),
       json.dumps(DEMO_USER, ensure_ascii=False),
       json.dumps(_dict_data(), ensure_ascii=False))

BANNER = ('<div style="position:fixed;left:18px;bottom:16px;z-index:70;padding:6px 12px;border-radius:8px;'
          'font:600 12px/1 Pretendard,system-ui,sans-serif;color:#c8c3ff;background:rgba(91,82,255,.16);'
          'border:1px solid rgba(91,82,255,.35);backdrop-filter:blur(6px)">정적 데모 · 샘플 결과 미리보기</div>')


def build() -> str:
    html = PAGE
    # 폰트·벤더 → CDN
    html = html.replace('<link href="/vendor/pretendard.css" rel="stylesheet">',
                        f'<link href="{CDN_PRETENDARD}" rel="stylesheet">')
    # 게임형 디스플레이 폰트(GmarketSans) → CDN @font-face 인라인(데모 자체완결)
    html = html.replace('<link href="/vendor/gmarket.css" rel="stylesheet">', GMARKET_CDN_CSS)
    html = html.replace('<script src="/vendor/tailwind.js"></script>',
                        f'<script src="{CDN_TAILWIND}"></script>')
    html = html.replace('<script defer src="/vendor/alpine.js"></script>',
                        STUB + f'<script defer src="{CDN_ALPINE}"></script>')
    # 디자인 시스템 CSS 인라인(정적 데모 자체완결 · file:// 에서도 라이트 위젯홈 렌더)
    theme_css = open(os.path.join(ROOT, "prism", "vendor", "ds-theme.css"), encoding="utf-8").read()
    comp_css = open(os.path.join(ROOT, "prism", "vendor", "ds-components.css"), encoding="utf-8").read()
    html = html.replace('<link href="/vendor/ds-theme.css" rel="stylesheet">', f'<style>{theme_css}</style>')
    html = html.replace('<link href="/vendor/ds-components.css" rel="stylesheet">', f'<style>{comp_css}</style>')
    # 앱 CSS·JS(분리 파일) 인라인 · 이후의 /vendor/·폰트·result 치환이 앱 코드에도 닿도록 여기서 병합
    app_css = open(os.path.join(ROOT, "prism", "vendor", "app.css"), encoding="utf-8").read()
    app_js = open(os.path.join(ROOT, "prism", "vendor", "app.js"), encoding="utf-8").read()
    # 공개 표면(Pages) 정책: 내부 기준 문서 식별자가 든 주석 라인은 데모에서 제거
    _internal = ("DNM", "1311", "1312", "278036632", "278856094", "365789408", "364314733")
    app_js = "\n".join(l for l in app_js.splitlines()
                       if not (l.lstrip().startswith("//") and any(m in l for m in _internal)))
    html = html.replace('<link href="/vendor/app.css" rel="stylesheet">', f'<style>{app_css}</style>')
    html = html.replace('<script src="/vendor/app.js"></script>', f'<script>{app_js}</script>')
    # 벤더 에셋(캐릭터·로고 SVG) → docs/demo-assets/ (Pages 루트 내부, main() 에서 복사)
    #   ../prism/vendor 는 Pages(docs=루트)에서 사이트 밖으로 나가 404 → 루트 내부 상대경로로.
    # src="/vendor/ 뿐 아니라 charOptions 의 JS 경로('/vendor/…')까지 포함해 전역 치환
    # (CSS·폰트·CDN 스크립트는 위에서 이미 태그 통째 치환됨 → 남은 /vendor/ 는 캐릭터 SVG 뿐)
    html = html.replace('/vendor/', 'demo-assets/')
    # Pretendard 폰트 패밀리는 'Pretendard Variable' 가변 → 정적 CDN 은 'Pretendard'
    html = html.replace('"Pretendard Variable",Pretendard,', '"Pretendard",')
    html = html.replace("'\\\"Pretendard Variable\\\"', 'Pretendard',",
                        "'Pretendard',")
    # 샘플 결과 주입(결과 화면까지 보여줌)
    html = html.replace('result: null,', 'result: (window.__DEMO_RESULT__ || null),')
    # 데모 배너
    html = html.replace('</body>', BANNER + '\n</body>')
    return html


def _copy_demo_assets():
    """캐릭터·로고 SVG 를 docs/demo-assets/ 로 복사(Pages 루트 내부).
    demo.html 이 src="demo-assets/*.svg" 로 참조 → Pages·htmlpreview·file:// 모두 해석."""
    import shutil
    src_dir = os.path.join(ROOT, "prism", "vendor")
    dst_dir = os.path.join(ROOT, "docs", "demo-assets")
    os.makedirs(dst_dir, exist_ok=True)
    n = 0
    for fn in os.listdir(src_dir):
        if fn.lower().endswith((".svg", ".png", ".jpg", ".gif", ".webp")):
            shutil.copy2(os.path.join(src_dir, fn), os.path.join(dst_dir, fn))
            n += 1
    return n, dst_dir


def main():
    out = os.path.join(ROOT, "docs", "demo.html")
    html = build()
    report_stub_coverage(html)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    n, dst = _copy_demo_assets()
    print(f"wrote {out} ({len(html):,} bytes)")
    print(f"copied {n} assets → {dst}")


def report_stub_coverage(html: str):
    """서버 라우트 대비 데모 스텁 커버리지 경고(누락 라우트 = 데모에서 실호출·무동작 위험)."""
    import re as _re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prism", "serve.py"), encoding="utf-8").read()
    routes = set(_re.findall(r'self\.path(?:\.startswith\(|\s*==\s*)"(/[a-zA-Z0-9\-_]+)"', src))
    stubs = set(_re.findall(r"u\.indexOf\('(/[a-zA-Z0-9\-_]+)'\)", html))
    missing = sorted(r for r in routes if not any(r.startswith(st) or st.startswith(r) for st in stubs))
    allow = {"/events", "/presence", "/report", "/store", "/prompt-defaults", "/meta-compile"}   # 데모 비노출 허용 목록
    warn = [m for m in missing if m not in allow]
    if warn:
        print(f"  [warn] 데모 스텁 미커버 라우트(버튼 노출 시 무동작): {warn}")
    else:
        print(f"  [ok] 데모 스텁 커버리지 정상(허용 예외 {len(missing)}종)")


if __name__ == "__main__":
    main()
