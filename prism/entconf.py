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

가중치 근거(2026-07-29 실데이터 재조정 · 표본 1,990개):
- **제목 없는 엔티티가 구조적으로 막혀 있었다**: 예전 가중치는 제목을 뺀 합이
  0.2+0.1+0.15 = 0.45 라, 리드문·첫문단·본문 반복이 전부 걸려도 0.5 를 넘을 수 없었다.
  운영 표본에서 제목 밖 엔티티 1,889개가 **한 개도** 기본 노출을 통과하지 못했다.
  확신도가 사실상 '제목에 있나?'의 다른 표현이 돼 있었다.
- 제목 포함(W_TITLE=0.5): 단독으로 경계선. 실측 실개체율은 제목 있음 36% ·
  리드문만 30% 로 차이가 12%p 남짓이라, 예전 0.55 만큼의 우위는 근거가 없었다.
- 리드문 포함(W_SUMMARY=0.25): 리드문은 **우리 모델이 만든 산출물**이라 엔티티 목록과
  같은 호출에서 나온다. 자기참조라 제목(원문 사실)보다 높일 수는 없다.
- 본문 빈도(W_FREQ=0.2 · FREQ_CAP=4) · 첫 문단(W_FIRST=0.15): 원문 근거라 함께 올렸다.
  제목 밖 합이 0.6 이 되어, **리드문에 더해 원문 근거(첫문단·반복)가 받쳐줄 때만** 통과한다
  (리드문에만 스치면 0.25 로 여전히 접힌다).

⚠ 이 가중치는 판별력을 올리지 못한다. 어떤 조합을 써도 AUC 0.61 로 평평했고(대리 정답 =
사전 타입 확정 여부), 바뀌는 것은 0.5 선이 분포의 어디에 떨어지느냐뿐이다. 진짜 최적화는
'이 엔티티가 이 콘텐츠와 관련 있나'의 검수 라벨이 쌓인 뒤에 해야 한다.
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
import unicodedata

W_TITLE = 0.5       # 제목 포함(단독으로 경계선 · 0.5)
W_SUMMARY = 0.25    # 리드문(summary) 포함
W_FIRST = 0.15      # 첫 문단(본문 앞 FIRST_RATIO) 포함
W_FREQ = 0.2        # 본문 등장 빈도(FREQ_CAP 에서 포화)
FREQ_CAP = 4        # 빈도 가점 상한(회)
FIRST_RATIO = 0.2   # '첫 문단'으로 보는 본문 앞부분 비율
RANK_STEP = 0.04    # 모델 정렬 순위 1계단당 감쇠
RANK_FLOOR = 0.7    # 순위 감쇠 바닥(제목 엔티티 보호선)

_STRIP = re.compile(r"[\s·]+")   # 공백 전부 + 가운뎃점


def _norm(s) -> str:
    """가벼운 정규화: 유니코드 NFC + casefold + 공백·가운뎃점 제거(부분일치 판정용).

    NFC 가 필요한 이유: 엔티티·리드문은 NFC 정규화된 입력(schema.normalize_text)에서 나오는데
    저장 payload 의 content_ref 4필드는 재실행 해시 안정성 때문에 **원본 그대로** 되박힌다
    (store._payload_with_identity · 의도된 계약). 원본이 NFD(맥OS 발 파일 등)면 같은 한글이라도
    코드포인트가 달라 제목·본문 신호가 전부 0 이 됐다 — 실측 conf(삼성전자) NFD 0.25 / NFC 1.0
    으로, 정상 엔티티가 통째로 '연관 낮음'(<0.5)에 접혔다(2026-08 감사 T5)."""
    return _STRIP.sub("", unicodedata.normalize("NFC", str(s or ""))).casefold()


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
