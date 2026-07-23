"""엔티티 확신도 · 읽기 시점 결정적 산출(주간회의 결정 · 0.5 기본 필터의 원천).

배경: item_meta.entities 는 문자열 배열이고 수치 확신도는 어디에도 저장되지 않는다.
추출 출력 4필드는 다운스트림 계약이라 형태 변경 금지(meta_prompts.py 헤더).
따라서 확신도는 저장·마이그레이션 없이, 읽기 시점에 콘텐츠 원문과 대조해
결정적으로 산출한다. 같은 입력이면 항상 같은 값 · 기존 데이터에도 그대로 소급 적용.

산식(가중 합 x 순위 감쇠 · 0~1):

    base = W_TITLE  * [제목 포함]
         + W_SUMMARY* [리드문(summary) 포함]
         + W_FIRST  * [첫 문단(본문 앞 FIRST_RATIO) 포함]
         + W_FREQ   * min(본문 등장 횟수, FREQ_CAP) / FREQ_CAP
    conf = round(min(1, base) * max(RANK_FLOOR, 1 - RANK_STEP * 순위), 3)

가중치 근거:
- 제목 포함(W_TITLE=0.55): 단독으로도 0.5 를 넘는 강한 가점. 제목에 오른 엔티티는
  콘텐츠의 주제 그 자체라 기본 노출(conf > 0.5) 대상이어야 한다.
- 리드문 포함(W_SUMMARY=0.2): 리드문은 핵심 고유명사를 담도록 생성되는 계약이므로
  (meta_prompts CALL_RULES.summary) 포함 여부가 대표성의 좋은 근사.
- 본문 빈도(W_FREQ=0.15 · FREQ_CAP=4): 반복 등장은 가점하되 상한으로 포화시켜
  나열형·롱폼 문서에서 빈도만으로 점수가 부풀지 않게 한다.
- 첫 문단(W_FIRST=0.1 · FIRST_RATIO=0.2): 기사류는 역피라미드로 핵심을 앞에 쓴다.
  본문 앞 20% 등장 여부를 가점.
- 순위 감쇠(RANK_STEP=0.04 · RANK_FLOOR=0.7): 모델이 대표성 높은 순으로 정렬해
  출력하는 계약(meta_prompts CALL_RULES.entities)이므로 뒤 순위일수록 감쇠.
  바닥 0.7 은 제목 포함 엔티티가 순위만으로 0.5 미만이 되지 않게 하는 안전선.

검증 사례(주간회의): 엔비디아 기사 본문 후반에 스치듯 1회 등장한 '박민우' 같은
저연관 엔티티는 제목·리드문·첫 문단 신호가 전부 0 이라 빈도 가점(0.15 * 1/4)에
순위 감쇠만 남아 0.05 안팎 · 0.5 미만으로 접힌다(tests/test_entconf.py 가 고정).

포함 판정은 가벼운 정규화(casefold · 공백/중점 제거) 뒤 부분 문자열 일치.
표기 정규화·개체 연결은 적재 단계 소관(추출 계약)이라 여기서는 하지 않는다.
"""
from __future__ import annotations

import re

W_TITLE = 0.55      # 제목 포함(강한 가점 · 단독 0.5 초과)
W_SUMMARY = 0.2     # 리드문(summary) 포함
W_FIRST = 0.1       # 첫 문단(본문 앞 FIRST_RATIO) 포함
W_FREQ = 0.15       # 본문 등장 빈도(FREQ_CAP 에서 포화)
FREQ_CAP = 4        # 빈도 가점 상한(회)
FIRST_RATIO = 0.2   # '첫 문단'으로 보는 본문 앞부분 비율
RANK_STEP = 0.04    # 모델 정렬 순위 1계단당 감쇠
RANK_FLOOR = 0.7    # 순위 감쇠 바닥(제목 엔티티 보호선)

_STRIP = re.compile(r"[\s·]+")   # 공백 전부 + 가운뎃점


def _norm(s) -> str:
    """가벼운 정규화: casefold + 공백·가운뎃점 제거(부분일치 판정용)."""
    return _STRIP.sub("", str(s or "")).casefold()


def entity_confidence(entity: str, content_ref: dict, item_meta: dict) -> float:
    """엔티티 1개의 확신도(0~1). 모듈 독스트링의 산식 · 순수 함수(결정적)."""
    e = _norm(entity)
    if not e:
        return 0.0
    ref = content_ref or {}
    im = item_meta or {}
    score = 0.0
    title = _norm(ref.get("title", ""))
    if title and e in title:
        score += W_TITLE
    summary = _norm(im.get("summary", ""))
    if summary and e in summary:
        score += W_SUMMARY
    body = _norm(ref.get("body", ""))
    if body:
        freq = body.count(e)
        if freq:
            score += W_FREQ * min(freq, FREQ_CAP) / float(FREQ_CAP)
        if e in body[:int(len(body) * FIRST_RATIO)]:
            score += W_FIRST
    names = [_norm(x) for x in (im.get("entities") or [])]
    rank = names.index(e) if e in names else len(names)
    decay = max(RANK_FLOOR, 1.0 - RANK_STEP * rank)
    return round(min(1.0, score) * decay, 3)


def scored_entities(item_meta: dict, content_ref: dict) -> list:
    """item_meta.entities 전체를 [{name, conf}] 로(순서 보존 · 기존 키 형태 불변).

    entities 자체는 다운스트림 계약이라 건드리지 않고, 읽기 API 가 이 결과를
    별도 키(entities_scored)로 병행 노출한다(_detail_row · raw_rows)."""
    im = item_meta or {}
    out = []
    for e in (im.get("entities") or []):
        name = str(e)
        out.append({"name": name, "conf": entity_confidence(name, content_ref, im)})
    return out
