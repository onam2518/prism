"""내부 검수 보조(트랙 A) 도구 · 검수자가 화면에서 판정을 빨리 내리도록 돕는다.

검수를 대신하는 기능이 아니다. 검수 결과는 **사람의 판단을 재는 값**이라, 보조가 답을
흘리는 순간 측정 대상이 사람이 아니게 된다. 그래서 아래 넷은 편의 요구가 아니라 계약이다.

1. **판정 전에는 근거·기준만.** `stage='before'` 응답에는 추천성 키가 아예 없다(빈 값이
   아니라 키 부재). 비워서 내려보내면 클라이언트가 언젠가 그 키를 읽고, 그때부터 조용히
   답이 샌다. 모르는 stage 값은 'before' 로 수렴한다(모르면 덜 주는 쪽).
2. **추천·수정 제안, 그리고 남이 내린 판정은 판정 뒤에만.** `stage='after'` 에서만
   suggestions 가 붙고, 선례(`verdict_precedents`)·다른 검수자 의견(`reviewer_dissent`)은
   아예 거절된다. 남의 판정을 먼저 보면 그 값이 기준점이 된다 — 프리즘은 검수자끼리의
   일치도로 신뢰도를 재는데, B 가 A 의 판정을 보고 정하면 그 일치는 독립된 근거가 아니다.
   지표가 조용히 부풀고, 부풀었다는 것을 알 방법이 없다. 그래서 화면이 아니라 **서버에서**
   막는다(규칙이 클라이언트에 있으면 다음 클라이언트가 어긴다).
3. **골드 문항이 응답에서 티 나지 않는다.** 골드는 검수자 신뢰도를 재는 장치라 보조가
   골드를 알려 주면 측정이 무너진다. 그런데 **가리는 방식도 신호가 된다** · 골드에서만
   오류가 뜨거나 골드에서만 빈칸이 생기면 검수자가 그것으로 골드를 알아보고, 알아보는
   순간 골드가 재려던 것(평소의 검수)이 사라진다.

   2026-08-13 에 큐가 뒤집는 값이 등급에서 **카테고리**로 옮겨졌다(`reviewops.GOLD_FLIP_ELEMENT`).
   등급을 뒤집던 시절에는 저장된 근거(`quality_meta.evidence`)가 곧 그 등급의 이유 문장이라
   저장된 행으로 브리핑을 만들면 화면은 G·브리핑은 R 이 되어 안전하게 만들 방법이 없었고,
   그래서 `content_brief` 는 골드를 거절했다. 지금은 어긋날 수 있는 자리가 **한 요소뿐**이라
   그 자리만 다루면 된다. 거절을 유지하면 오히려 골드만 "저장된 근거 없음" 이 되고, 근거
   적재율이 오를수록 그 빈칸이 골드를 가리킨다(그 완화는 기능이 성공할수록 사라진다).

   그래서 지금 규칙은 이렇다.
     · 골드 합성 해시는 **밑에 깔린 콘텐츠로 풀어** 평소대로 답한다(`_resolve`).
     · 큐가 뒤집는 그 요소는 **전 콘텐츠에서** 사실로 말하지 않는다(값·요약·제안·유사도 축).
       골드에서만 가리면 그 차이가 다음 신호이고, 골드에서만 다르게 답하려면 뒤집기 규칙을
       이 모듈에 두 번째로 구현해야 한다(그러면 한쪽만 바뀔 때 그 어긋남이 새 오라클이 된다).
     · 결과: `content_brief(gold:bad:<h>)` 와 `content_brief(<h>)` 의 응답이 **완전히 같다.**
       패널이 안 그리는 필드까지 같아야 한다 · `/assist-ask`(자유질문)가 같은 응답을 모델에
       통째로 먹이므로 화면 밖 필드에서 갈려도 모델의 답이 갈린다.
     · `reviewer_dissent` 만 골드에서 닫힌다. 골드 원본은 '정확' 다수결로 골든이 된 콘텐츠라
       그 의견 목록이 곧 정답이다. 대신 **오류가 아니라 빈 결과**를 준다(운영에서 의견이
       갈린 콘텐츠는 13.0% 뿐이라 흔한 빈칸이다).
4. **근거를 지어내지 않는다.** 저장된 값을 조립할 뿐 모델을 새로 돌리지 않는다. 판정 근거를
   사후에 재생성하면 그럴듯한 창작이 된다(모델은 자기 추론 과정에 접근하지 못한다).
   `quality_meta.evidence` 가 비어 있으면 없다고 말한다 — 2026-08-12 신설 필드라 그 이전
   운영 데이터는 대부분 비어 있고, 그 빈칸을 메우려는 순간 이 도구는 거짓말을 시작한다.

## 이 규칙들을 어떻게 검증하나 (여기 손대는 다음 사람에게)

**"골드 자료를 안 담았다" 를 단언하는 테스트로는 부족하다.** 위 규칙은 전부 *차이가 보이나*에
관한 것이라, 자료를 안 담고도 깨진다. 2026-08-12 작업에서 실제로 세 번 같은 모양으로 밟았다.

  · 골드에 오류를 돌려줌 → 오류 문구가 화면에 떠서 그게 곧 "이건 골드다" 신호
  · 화면이 빈 제목을 `(제목 없음)` 으로 그림 → 가려진 항목에 오히려 표시가 붙음
  · 평범한 콘텐츠 전부에 근거가 생기면 → 골드의 "근거 없음" 한 줄이 신호

셋 다 "골드 자료 미포함" 단언은 통과했다. **가리는 행위 자체가 표시가 된다** — 규칙의 문자는
지키면서 목적은 어기는 자리다.

그래서 응답 수준 규칙은 포함/미포함이 아니라 **동일성**으로 적는다: "골드 응답이 평범한
콘텐츠 응답과 같다"(`test_list_tools_return_empty_not_an_error_for_gold` 가 그 모양). 첫째
경우가 정확히 이걸로 잡힌다.

**나머지 둘은 서버 테스트로 잡을 수 없다.** 둘째는 클라이언트의 렌더 선택이고(서버 응답은
양쪽 다 `title=""` 로 같다), 셋째는 한 응답이 아니라 콘텐츠 전체에 근거가 얼마나 찼는가 하는
분포의 성질이라 단건 단언이 닿지 않는다. 이 계층의 회귀가 **테스트 통과로 확인되지 않는**
이유가 이것이다. 위 셋과 `stage` 필수 누락까지 넷 다 목이 아니라 실 백엔드를 붙여 화면에
그려 본 뒤에 나왔다(정적으로는 전부 "골드를 막고 있다" 로 읽혔다).

다만 "그려 봐야 보인다" 가 "자동화하지 않아도 된다" 는 뜻은 아니다 — **잡을 수 있는 자리가
옮겨간 것뿐**이다. 둘째는 서버에서 못 잡을 뿐 클라이언트에서는 잡힌다(화면 조각을 실제로
실행해 골드 사본과 평범한 콘텐츠의 출력 동일성을 묻는 테스트가 UI 쪽에 있다 · 2026-08-12).
사람이 매번 그려 보는 것은 두 세션이 붙어 있을 때만 되므로, 규칙이 어느 계층에서든 실행으로
표현될 수 있으면 거기에 고정한다. 사람 손에 남기는 것은 셋째처럼 **어느 한 실행으로도
드러나지 않는 것**뿐이다.

공통 가드(팀 강제·상한·잘림 보고·예외 은닉)와 디스패치는 `prismtools` 를 그대로 쓴다.
도구 계층 규칙이 두 벌이 되면 반드시 어긋나고, 어긋난 쪽이 조용히 틀린 답을 낸다.

앞단은 `serve.py` 의 `/assist`(gate=team) 하나. 팀은 세션에서 해석한 값만 들어온다.
"""
from __future__ import annotations

from . import dictionaries as D
from . import feedback_loop as FL      # 교정 요소 id·한글 라벨 단일 원천(검수 화면과 같은 사전)
from . import prismtools as PT

_SV = None                                   # serve 주입(컴포지션 루트)

STAGES = ("before", "after")

# stage='before' 응답에서 존재 자체가 금지된 키. 코드가 실수로 담아도 마지막에 떨어낸다
# (테스트뿐 아니라 런타임에서도 막는 이중 방어 · 이 목록이 독립성의 경계선이다).
SUGGESTIVE_KEYS = ("suggestions", "recommendation", "recommended", "answer", "verdict_hint", "advice")

GOLD_MSG = "골드 문항에는 검수 보조를 제공하지 않습니다"
NOT_FOUND_MSG = "콘텐츠를 찾지 못했습니다"
# 남의 판정(선례·다른 검수자 의견)은 판정 뒤에만 나간다. 다음 행동을 알 수 있게 쓴다.
AFTER_ONLY_MSG = "판정을 낸 뒤에 조회할 수 있습니다 · 판정 후 stage=\"after\" 로 다시 부르세요"
# 근거가 없을 때 내려보내는 문장. '없다' 를 말하는 것이 이 도구의 정확성이다.
NO_EVIDENCE = "저장된 모델 판정 근거가 없습니다(근거 저장 이전 데이터) · 근거는 지어내지 않습니다"

TITLE_MAX, NOTE_MAX, EVIDENCE_SNIP = 60, 200, 200
CRITERIA_MAX = 12
PRECEDENT_DEFAULT, PRECEDENT_MAX = 5, 20
# 불일치 집계 3줄의 둘째 줄(지적 요소)에 나열할 요소 수 상한. 넘으면 truncated 로 밝힌다.
DIGEST_ELEM_MAX = 4
PATCH_SCAN, SUGGEST_MAX = 400, 5

# 확정 선례의 최소 판정 인원. 1인 판정을 '확정 선례' 로 되먹이면 그 한 사람의 편향이 증폭되고,
# 무엇보다 '선례' 라는 말이 거짓이 된다. 저장 계층의 agree 는 n=1 에서도 참이라 여기서 막는다.
PRECEDENT_MIN_N = 2

# 골든 원본 선례에서 지우는 필드 = '어느 콘텐츠인지 알아보는 손잡이'.
#
# 골드 문항의 원본(`gold:ok:<h>` 의 h)은 골든셋에 올라간 확정 검수 콘텐츠라 results 에
# 평범한 행으로 있다. strip_gold 는 합성 해시만 거르지 이 원본은 못 거른다. 그대로 두면
# 검수자가 선례에서 본 제목을 나중에 큐의 골드 문항에서 알아보고 정답을 안다 —
# 선례에는 확정 판정과 등급이 실려 있으니 그게 곧 정답 해설이다.
#
# 그렇다고 골든 원본을 선례에서 통째로 빼면 기능 값이 크게 깎인다(골든이 곧 '확정된 좋은
# 선례'다). 그래서 판단 재료(verdict·n·why_similar)는 남기고 식별자만 지운다. reason 은
# 검수자가 쓴 자유 문장이라 소재를 그대로 부를 수 있어 함께 지운다.
# 키는 남기고 값만 비운다 — 항목 모양이 조건에 따라 달라지면 클라이언트가 갈라진다.
#
# 이 가리기는 골드 원본을 **빠짐없이** 덮는다. 두 가지가 맞물려서다.
#   ① 골드 문항은 골든셋에서만 만들어진다(`_inject_gold` 가 `get_golden` 만 돈다).
#   ② 골든 행의 content_hash 컬럼과 `_inject_gold` 가 쓰는 `_chash(content)` 가 같은 값이다
#      — 등록 경로가 전부 같은 content_hash() 로 키와 본문을 함께 넣기 때문이다
#      (learnops.build_golden_from_reviews · supastore.register_golden).
# 둘 중 하나라도 깨지면(골든 아닌 콘텐츠로 골드를 만들거나, 키와 본문이 따로 놀면)
# 이 방어에 구멍이 생긴다. 그때는 여기가 아니라 그 전제를 고쳐야 한다.
GOLDEN_BLIND_FIELDS = ("hash", "title", "reason")
# 제안 노출 최소 건수. 1건은 선례가 아니라 한 사람의 판단이다.
SUGGEST_MIN = 2

# 교정 로그(patch_log)가 쓰는 요소 키 → 이 콘텐츠의 현재 값을 읽어 올 자리.
# 여기 없는 키는 제안 대상이 아니다(모르는 필드로 추측하지 않는다).
PATCH_FIELDS = ("summary", "entities", "intent", "content_category", "topic",
                "finalGrade", "reasons")
# 목록으로 저장되는 필드 = 요소 단위로 비교한다(목록 전체 일치를 요구하면 운영에서 거의 안 걸린다).
LIST_FIELDS = ("entities", "intent", "content_category", "reasons")
# 교정 로그 요소 키 → _values 의 키(이름이 다른 것만).
_VAL_KEY = {"finalGrade": "grade"}

# ── 큐가 뒤집는 자리는 이 모듈이 사실로 말하지 않는다 ────────────────────────
#
# 큐는 골드 문항에서 **한 요소만** 참값과 다르게 보여 준다(reviewops.GOLD_FLIP_ELEMENT ·
# 2026-08-13 부터 카테고리 · 그전에는 등급이었다). 나머지는 전부 참값이라, 이 모듈이
# 저장된 값을 그대로 답해도 화면과 어긋나지 않는다. 어긋날 수 있는 자리는 **그 한 요소뿐**이다.
#
# 그 한 요소는 **모든 콘텐츠에서** 뺀다. 골드일 때만 빼지 않는다. 이유가 셋이다.
#   ① 골드에서만 빠지면 그 차이가 다음 신호다. 우리가 지금까지 세 번 밟은 모양이 전부
#      "가리는 행위 자체가 표시가 된다" 였다.
#   ② 뒤집기 규칙이 이 모듈에 두 번째로 구현되면(골드일 때만 다르게 답하려면 결국
#      그래야 한다) 나중에 한쪽만 바뀔 때 그 어긋남이 새 오라클이 된다.
#   ③ 애초에 **참이 아닐 수 있는 값을 사실로 말하면 안 된다.** 그 값이 뒤집힌 문항이 같은
#      큐에 섞여 있다. 검수자는 그 값을 화면에서 이미 보고 있으므로 이 모듈이 다시 말해
#      줄 필요도 없다.
#
# 이 규칙은 도구 응답 전체에 걸린다. 패널이 안 그리는 필드에도 걸어야 한다. `/assist-ask`
# (자유질문)가 같은 도구 응답을 **모델에 통째로 먹이기** 때문에, 화면이 안 그리는 값에서
# 갈리면 모델의 답이 갈리고 그 답이 곧 골드 표시가 된다(2026-08-13 · PR #440 의존).
#
# 결과: `content_brief(gold:bad:<h>)` 는 `content_brief(<h>)` 와 **완전히 같은 응답**이다.
# 골드에서 달라지는 것은 '어느 행을 읽는가' 뿐이고(_resolve), 읽은 뒤 동작은 하나다.
#
# 뒤집는 요소가 또 바뀌어도 여기는 따라간다. 원천은 reviewops 의 상수 하나다.
_ELEM_VAL_KEY = {"summary": "summary", "entities": "entities", "intent": "intent",
                 "category": "content_category", "grade": "grade", "quality": "reasons"}


def flip_blind_key() -> str:
    """사실로 말하지 않을 값 키(_values·PATCH_FIELDS 어휘). 모르는 요소면 빈 문자열."""
    from . import reviewops as RV
    return _ELEM_VAL_KEY.get(getattr(RV, "GOLD_FLIP_ELEMENT", ""), "")


def _blank_like(v):
    """같은 자리에 들어갈 '값 없음'. 모양은 유지한다(목록은 목록, 문자열은 문자열)."""
    return [] if isinstance(v, (list, tuple)) else ""


def _resolve(ch: str) -> tuple:
    """조회에 쓸 콘텐츠 해시. 골드 합성 해시(`gold:<ok|bad>:<hash>`)는 밑에 깔린 콘텐츠로 푼다.

    반환 (조회 해시, 골드 여부). **풀 수 없는 골드 해시는 ("", True)** 로 닫는다.
    형식이 다른 골드 접두 해시를 그대로 조회하면 골드 행 자체를 자료로 내주게 된다."""
    if not PT.is_gold(ch):
        return str(ch or "").strip(), False
    parts = str(ch or "").split(":")
    under = parts[2].strip() if len(parts) >= 3 else ""
    return ("", True) if (not under or PT.is_gold(under)) else (under, True)


# ── 소소한 도구 ──────────────────────────────────────────────────────────────
def _clip(s, n: int) -> str:
    s = str(s or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _names(vs) -> list:
    """엔티티 등 문자열/객체 혼재 목록을 표시 문자열 목록으로."""
    out = []
    for v in vs or []:
        n = (v.get("name") or v.get("text") or "") if isinstance(v, dict) else str(v or "")
        n = str(n).strip()
        if n:
            out.append(n)
    return out


def _epoch(ts) -> float:
    """정렬용 epoch. sqlite=float · supabase=ISO 문자열 혼재를 흡수(reviewops 이력과 같은 해석)."""
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _norm(v) -> str:
    """값 비교용 정규화. 목록은 순서를 지켜 잇는다(순서도 교정 대상이라 정렬하지 않는다)."""
    if isinstance(v, (list, tuple)):
        return " · ".join(str(x).strip() for x in v if str(x).strip())
    return str(v if v is not None else "").strip()


def _stage(v) -> str:
    """단계 해석. 모르는 값은 'before' 로 수렴한다 — 오타·구버전 클라이언트가 판정 뒤 자료를
    열지 못하게 하는 쪽이 안전하다(모르면 덜 주는 쪽)."""
    s = str(v or "").strip().lower()
    return s if s in STAGES else "before"


def _reason_label(rid: str) -> str:
    return (getattr(D, "QUALITY_META_NAMES", {}) or {}).get(rid, rid)


def _index(team) -> dict:
    """팀 스코프 콘텐츠 해시 → 저장 행. 팀 필터는 저장 계층이 건다(호출자 팀만 넘긴다)."""
    if not _SV:
        return {}
    try:
        rows = _SV.results_rows(team=team) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            k = _SV._row_key(r.get("content_ref") or {})
        except Exception:
            continue
        if k:
            out[k] = r
    return out


def _golden(team):
    """골든셋에 올라간 콘텐츠 해시. 모르면 None(= 전부 골든으로 취급).

    실패를 빈 집합으로 돌려주면 조회가 한 번 흔들릴 때마다 식별자가 그대로 나간다 —
    안전장치가 오류에 열리면 안전장치가 아니다. 못 읽으면 다 가리는 쪽으로 닫는다."""
    st = _SV.get_store() if _SV else None
    if not (st and hasattr(st, "golden_hashes")):
        return None
    try:
        return set(st.golden_hashes(team=team) or ())
    except Exception:
        return None


def _feedback(team) -> dict:
    st = _SV.get_store() if _SV else None
    if not (st and hasattr(st, "feedback_map")):
        return {}
    try:
        return st.feedback_map(team=team) or {}
    except Exception:
        return {}


def _values(row: dict) -> dict:
    """부여된 값 묶음. 저장된 것만 담는다(빈 값은 빈 값으로 남긴다)."""
    ref = row.get("content_ref") or {}
    qm = row.get("quality_meta") or {}
    im = row.get("item_meta") or {}
    reasons = [str(r) for r in (qm.get("reasons") or []) if r]
    return {
        "service": str(ref.get("displayServiceName", "") or ""),
        "title": str(ref.get("title", "") or ""),
        "grade": str(qm.get("finalGrade", "") or ""),
        "reasons": reasons,
        "reason_labels": [_reason_label(r) for r in reasons],
        "review": str(qm.get("review", "") or ""),
        "review_reason": str(qm.get("review_reason", "") or ""),
        "confidence": qm.get("confidence"),
        "intent": _names(im.get("intent")),
        "content_category": _names(im.get("content_category")),
        "entities": _names(im.get("entities")),
        "summary": str(im.get("summary", "") or ""),
        "topic": str(im.get("topic", "") or ""),
    }


# ── content_brief ────────────────────────────────────────────────────────────
def _summary3(vals: dict, evidence: str) -> list:
    """3줄 요약. 저장된 값을 잇는 것뿐이라 문장이 늘거나 줄지 않는다(항상 3줄).

    큐가 뒤집는 요소는 여기서도 말하지 않는다. 값을 비워 놓고 "미부여" 라고 쓰면 화면에는
    값이 그려져 있는 골드에서 앞뒤가 안 맞으므로, 그 자리는 **문장에서 통째로 뺀다**
    (전 콘텐츠 공통 · 검수자는 그 값을 화면에서 이미 보고 있다)."""
    blind = flip_blind_key()
    head = [vals["service"] or "서비스 미상", _clip(vals["title"], TITLE_MAX)]
    if blind != "content_category":
        head.append(" · ".join(vals["content_category"]) or "카테고리 미부여")
    l1 = " · ".join(head)
    rs = ", ".join(vals["reason_labels"]) or "지적된 품질 사유 없음"
    l2 = (f"모델 초안: 등급 {vals['grade'] or '미상'} · {rs}" if blind != "grade"
          else f"모델 초안: {rs}")
    if vals["review"] == "yellow":
        l2 += " · 사람 검수 필요" + (f"({vals['review_reason']})" if vals["review_reason"] else "")
    l3 = ("모델이 남긴 판정 근거: " + _clip(evidence, EVIDENCE_SNIP)) if evidence else NO_EVIDENCE
    return [l1, l2, l3]


def _criteria(vals: dict, team) -> list:
    """이 콘텐츠에 걸린 분류 기준 발췌. 정의문 원천은 INTENT_VALUE_DEFS·QUALITY_METAS 하나뿐이라
    prismtools.get_taxonomy 를 그대로 재사용한다(검수 화면·추출 프롬프트와 같은 문장)."""
    tx = PT.get_taxonomy("intent", service=vals["service"], team=team) or {}
    defs = {v.get("key"): v.get("desc", "") for v in (tx.get("values") or [])}
    picked = [k for k in vals["intent"] if k in defs]
    items = [{"key": k, "desc": defs.get(k, "")} for k in picked]

    rtx = PT.get_taxonomy("reason", team=team) or {}
    rdefs = {v.get("key"): (v.get("label") or v.get("key"), v.get("desc", ""))
             for v in (rtx.get("values") or [])}
    for r in vals["reasons"]:
        lbl, desc = rdefs.get(r, (_reason_label(r), ""))
        items.append({"key": lbl, "desc": desc})

    if not picked:              # 부여된 인텐트가 없으면 그 서비스의 후보 기준을 보여 준다(추천 아님)
        room = max(0, CRITERIA_MAX - len(items))
        items += [{"key": k, "desc": defs.get(k, "")} for k in list(defs)[:room]]

    out, seen = [], set()
    for it in items:
        if it["key"] and it["key"] not in seen:
            seen.add(it["key"])
            out.append(it)
    return out[:CRITERIA_MAX]


def _elems(v) -> list:
    """목록 필드의 요소들. 스칼라는 1개짜리 목록으로 본다."""
    if isinstance(v, (list, tuple)):
        return [s for s in (str(x).strip() for x in v) if s]
    s = str(v if v is not None else "").strip()
    return [s] if s else []


def _observations(field: str, bv, av, cur) -> list:
    """교정 1건에서 읽어 낼 (출발값 → 도착값) 관찰. **짝이 분명한 것만** 센다.

    목록 필드는 요소 단위로 본다 — 목록 전체가 같아야 한다면 운영에서 거의 안 걸려 기능이
    없는 것과 같다. 다만 여러 개가 한꺼번에 바뀐 교정은 무엇이 무엇으로 바뀌었는지 알 수
    없으므로 짝짓지 않는다. 짝을 지어내는 것이 곧 없는 근거를 만드는 것이다."""
    if field not in LIST_FIELDS:
        b, a = _norm(bv), _norm(av)
        return [(b, a)] if (a and a != b and b == _norm(cur)) else []
    bl, al, cl = _elems(bv), _elems(av), _elems(cur)
    removed = [x for x in bl if x not in al]
    added = [x for x in al if x not in bl]
    if len(removed) == 1 and len(added) == 1:     # 한 요소를 다른 요소로 바꾼 교정
        return [(removed[0], added[0])] if removed[0] in cl else []
    if added and not removed and not bl and not cl:
        return [("", a) for a in added]           # 비어 있던 필드를 채운 교정(대상도 비어 있을 때만)
    return []


def _suggestions(ch: str, vals: dict, idx: dict, team, skip: str = "") -> list:
    """수정 제안 = 같은 서비스에서 **같은 출발값을 같게 고친 과거 교정**의 집계.

    새로 만들어 내는 값이 없다. 셀 뿐이다. 그래서 다음 셋을 지킨다.
      · 출발값이 같을 때만 센다(다른 값에서 출발한 교정은 이 콘텐츠의 선례가 아니다)
      · SUGGEST_MIN 건 이상 쌓여야 내보낸다 — 1건은 선례가 아니라 한 사람의 판단이다
      · 센 건수와 검수자 수를 함께 실어 무게는 검수자가 스스로 단다
    basis 에는 **세어진 사실만** 적는다. "이렇게 고치세요" 는 이 도구가 할 말이 아니다.

    skip: 제외할 필드(골드에서 큐가 뒤집는 자리). 그 자리는 vals 가 비워져 있어 그냥 두면
    '비어 있던 필드를 채운 교정' 제안이 붙는데, 화면에는 값이 그려져 있어 앞뒤가 안 맞는다.

    판정 뒤에만 부른다(content_brief 가 stage 로 분기)."""
    st = _SV.get_store() if _SV else None
    if not (st and hasattr(st, "patch_rows")):
        return []
    try:
        rows = st.patch_rows(limit=PATCH_SCAN, team=team) or []
    except Exception:
        return []
    svc_of = {h: str((r.get("content_ref") or {}).get("displayServiceName", "") or "")
              for h, r in idx.items()}
    cur = {k: vals[_VAL_KEY.get(k, k)] for k in PATCH_FIELDS if _VAL_KEY.get(k, k) != skip}
    tally = {}
    for pr in PT.strip_gold(rows):
        h = str(pr.get("hash") or "")
        if not h or h == ch or svc_of.get(h) != vals["service"]:
            continue                              # 다른 서비스·자기 자신의 교정은 선례가 아니다
        before, after = pr.get("before"), pr.get("after")
        if not (isinstance(before, dict) and isinstance(after, dict)):
            continue
        who = str(pr.get("reviewer") or "")
        for k, av in after.items():
            if k not in cur:
                continue
            for b, a in _observations(k, before.get(k), av, cur[k]):
                e = tally.setdefault((k, b, a), {"n": 0, "who": set()})
                e["n"] += 1
                if who:
                    e["who"].add(who)
    out = []
    for (k, b, a), e in sorted(tally.items(),
                               key=lambda kv: (-kv[1]["n"], -len(kv[1]["who"]), kv[0])):
        if e["n"] < SUGGEST_MIN:                  # 1건은 선례가 아니라 한 사람의 판단이다
            continue
        n, w = e["n"], len(e["who"])
        basis = (f"이 서비스({vals['service']})에서 같은 출발값을 이렇게 고친 교정 {n}건" if b
                 else f"이 서비스({vals['service']})에서 비어 있던 {k} 를 이렇게 채운 교정 {n}건")
        out.append({"field": k, "from": b, "to": a, "count": n, "reviewers": w,
                    "basis": f"{basis} · 검수자 {w}명"})
        if len(out) >= SUGGEST_MAX:
            break
    return out


def content_brief(hash: str = "", stage: str = "before", team=None) -> dict:
    """이 콘텐츠가 왜 이렇게 판정됐나 · 3줄 요약 + 저장된 근거 + 부여된 값 + 분류 기준.

    골드 문항도 평소대로 답한다(2026-08-13). 종전에는 거절했는데, 큐가 **등급을** 뒤집던
    시절에는 저장된 근거가 곧 뒤집힘의 해설이라 안전하게 만들 방법이 없었기 때문이다.
    지금 큐가 뒤집는 것은 카테고리 한 자리뿐이고 등급·근거·리드문·엔티티·인텐트는 참값이라,
    **그 한 자리만 비우면** 나머지는 화면과 그대로 맞는다. 거절을 유지하면 골드에서만
    "저장된 근거 없음" 이 되고, 근거 적재율이 오를수록 그 빈칸이 골드를 가리킨다."""
    blocked = PT.need_team(team)
    if blocked:
        return blocked
    ch = str(hash or "").strip()
    if not ch:
        return {"error": "콘텐츠를 지정해 주세요"}
    stage = _stage(stage)
    idx = _index(team)
    look, _is_gold = _resolve(ch)                 # 골드면 밑에 깔린 콘텐츠로 읽는다
    row = idx.get(look) if look else None
    if row is None:
        return {"error": NOT_FOUND_MSG}
    vals = _values(row)
    blind = flip_blind_key()                      # 큐가 뒤집는 그 한 자리(전 콘텐츠 공통)
    if blind and blind in vals:
        vals[blind] = _blank_like(vals[blind])    # 참이 아닐 수 있는 값을 사실로 말하지 않는다
    ev = str((row.get("quality_meta") or {}).get("evidence") or "").strip()
    out = {
        "stage": stage,
        "summary3": _summary3(vals, ev),          # 비운 값 기준(문구도 화면과 어긋나지 않게)
        "evidence": ev or None,                   # 없으면 없다고 말한다(빈 문자열로 얼버무리지 않는다)
        "has_evidence": bool(ev),
        "values": vals,
        "criteria": _criteria(vals, team),
    }
    if stage == "after":
        out["suggestions"] = _suggestions(look, vals, idx, team, skip=blind)
    # 이 지점에서 응답은 골드든 아니든 완전히 같다(다른 것은 어느 행을 읽었는가뿐).
    else:
        for k in SUGGESTIVE_KEYS:                 # 이중 방어: 판정 전에는 키 자체가 없어야 한다
            out.pop(k, None)
    return out


# ── verdict_precedents ───────────────────────────────────────────────────────
# 선례 유사도 축: (검수 요소 id, _values 키, 화면 문구, 가중치, 값 표시 변환).
# 등급은 목록이 아니라 스칼라 비교라 아래 표가 아니라 따로 다룬다(_axis_on 으로 같은 규칙 적용).
_AXES = (
    ("quality", "reasons", "같은 품질 사유", 3, _reason_label),
    ("intent", "intent", "같은 인텐트", 2, None),
    ("category", "content_category", "같은 카테고리", 2, None),
)


def _axis_on(element: str) -> bool:
    """그 요소를 유사도 근거로 써도 되는가.

    큐가 뒤집는 요소는 **모든 콘텐츠에서** 뺀다. 골드에서만 빼면 그 차이가 다음 신호이고,
    참이 아닐 수 있는 값을 '비슷함' 의 근거로 삼는 것 자체가 성립하지 않는다(그 값이
    뒤집힌 문항이 같은 큐에 섞여 있다). 무엇보다 `why_similar` 는 화면에 그대로 뜨는
    문구라 `같은 카테고리: …` 가 뒤집힌 화면과 어긋나면 그 어긋남이 곧 정답이다."""
    from . import reviewops as RV
    return element != getattr(RV, "GOLD_FLIP_ELEMENT", "")


def _similarity_axes() -> tuple:
    return tuple(a for a in _AXES if _axis_on(a[0]))


def verdict_precedents(hash: str = "", stage: str = "before", limit=None, team=None) -> dict:
    """비슷한 과거 판정(선례). 무엇이 '비슷함'인지(why_similar)를 함께 실어 검수자가 스스로 본다.

    **판정 뒤에만 나간다.** 남이 내린 판정을 먼저 보면 그 값이 기준점이 된다. 프리즘은
    검수자끼리의 일치도로 신뢰도를 재는데, B 가 A 의 판정을 보고 정하면 그 일치는 독립된
    근거가 아니다 — 지표가 조용히 부풀고, 부풀었다는 것을 알 방법이 없다.

    확정된 것만 센다 = PRECEDENT_MIN_N 인 이상이 갈리지 않고 같은 판정을 낸 건. 갈린 건은
    reviewer_dissent 쪽이고, 1인 판정은 어느 쪽도 아니다(그냥 근거가 부족한 것이다)."""
    blocked = PT.need_team(team)
    if blocked:
        return blocked
    ch = str(hash or "").strip()
    empty = {"items": [], "total": 0, "truncated": False}
    if _stage(stage) != "after":
        return dict(empty, error=AFTER_ONLY_MSG)
    if not ch:
        return dict(empty, error="콘텐츠를 지정해 주세요")
    lim = PT.qint(limit, PRECEDENT_DEFAULT, 1, PRECEDENT_MAX)
    idx = _index(team)
    # 골드는 밑에 깔린 콘텐츠로 읽는다. 선례는 **자기 자신을 뺀 남의 판정**이라 정답이 새지
    # 않고(골든 원본은 아래에서 식별자가 가려진다), 골드만 늘 비어 있으면 그 빈칸이 신호다
    # (운영 실측 2026-08-13: 평범한 콘텐츠는 1,451/1,451 이 선례를 갖는다).
    ch, _is_gold = _resolve(ch)
    if not ch:
        return dict(empty)                        # 못 푸는 골드 해시 = 빈 결과(오류는 곧 신호)
    row = idx.get(ch)
    if row is None:
        return dict(empty, error=NOT_FOUND_MSG)
    me = _values(row)
    axes = _similarity_axes()
    mine = {key: set(me[key]) for _el, key, _lbl, _w, _fmt in axes}
    fbm = _feedback(team)

    ranked = []
    for h, r in idx.items():
        if h == ch or PT.is_gold(h):              # 골드는 선례로도 나가지 않는다
            continue
        v = _values(r)
        if v["service"] != me["service"]:
            continue
        fb = fbm.get(h) or {}
        n = int(fb.get("n") or 0)
        # 확정 = 최소 인원 이상이 갈리지 않고 같게 본 것. 저장 계층의 agree 는 n=1 에서도
        # 참이라 여기서 인원을 함께 본다(1인 판정을 선례라 부르면 그 말이 거짓이 된다).
        if n < PRECEDENT_MIN_N or not fb.get("agree"):
            continue
        verdict = str(fb.get("consensus") or fb.get("verdict") or "")
        if verdict not in ("good", "bad"):
            continue
        why, score = [], 0
        for _el, key, label, weight, fmt in axes:
            shared = mine[key] & set(v[key])
            if shared:
                why.append(f"{label}: " + ", ".join((fmt or str)(x) for x in sorted(shared)))
                score += weight
        if not score:                             # 서비스만 같은 건 '비슷함' 이 아니다
            continue
        if _axis_on("grade") and v["grade"] and v["grade"] == me["grade"]:
            why.append(f"같은 등급: {v['grade']}")
            score += 1
        last = (fb.get("verdicts") or [{}])[-1]
        ts = _epoch(last.get("ts"))
        ranked.append((score, ts, {
            "hash": h,
            "title": _clip(v["title"], TITLE_MAX),
            "verdict": verdict,
            "n": n,                               # 몇 사람이 같게 봤나(화면이 '3인 일치' 로 쓴다)
            "reason": _clip(fb.get("note") or last.get("note") or "", NOTE_MAX),
            "ts": ts,
            "why_similar": " · ".join([f"같은 서비스: {v['service']}"] + why),
        }))
    ranked.sort(key=lambda x: (-x[0], -x[1]))
    items = [it for _, _, it in ranked]
    gold = _golden(team)                          # None = 모름 → 전부 가린다(fail-closed)
    for it in items:
        if gold is None or it["hash"] in gold:
            for f in GOLDEN_BLIND_FIELDS:
                it[f] = ""
    return PT.envelope(items, lim)


# ── reviewer_dissent ─────────────────────────────────────────────────────────
def _elem_tally(ch: str, verdicts: list, team) -> tuple:
    """요소별로 **몇 사람이** 지적했나. (라벨→인원, 잘림) · 이름은 세는 데만 쓰고 내보내지 않는다.

    두 축을 합친다: 판정 시 고른 교정 요소(feedback.element)와 실제 교정 로그(patch_log.element).
    라벨 원천은 `feedback_loop.ELEM_KO` 하나 — 검수 화면이 `/dict` 로 받는 것과 같은 사전이라
    여기서 새로 지으면 같은 요소가 화면과 다른 이름으로 불린다."""
    people = {}
    for v in verdicts or []:
        who = str(v.get("reviewer") or "")
        for e in _elem_ids(v.get("element")):
            people.setdefault(e, set()).add(who)
    st = _SV.get_store() if _SV else None
    if st and hasattr(st, "patch_rows"):
        try:
            for pr in (st.patch_rows(team=team, content_hash=ch) or []):
                if pr.get("hash") != ch:
                    continue
                for e in _elem_ids(pr.get("element")):
                    people.setdefault(e, set()).add(str(pr.get("reviewer") or ""))
        except Exception:
            pass
    ranked = sorted(people.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return ([(FL.ELEM_KO.get(e, e), len(who)) for e, who in ranked[:DIGEST_ELEM_MAX]],
            len(ranked) > DIGEST_ELEM_MAX)


def _elem_ids(raw) -> list:
    """저장된 요소 문자열 → 아는 요소 id 목록. 모르는 것은 버린다(추측해서 만들지 않는다).

    두 축의 표기가 다르다: 판정은 FL.ELEMENTS id 를 쉼표로 잇고, 교정 로그는 item_meta 키를
    잇는다(`content_category` 는 이미 'category' 로 접힌 채 들어온다). rerun·undo 는 검수자의
    요소 지적이 아니라 운영 기록이라 세지 않는다."""
    out = []
    for tok in str(raw or "").split(","):
        e = _ELEM_ALIAS.get(tok.strip(), tok.strip())
        if e in FL.ELEMENTS and e not in out:
            out.append(e)
    return out


_ELEM_ALIAS = {"content_category": "category", "reasons": "quality", "finalGrade": "grade"}


def reviewer_dissent(hash: str = "", stage: str = "before", team=None) -> dict:
    """다른 검수자 의견을 **3줄 집계**로 준다(사람별 나열이 아니라).

    사람별로 이름·판정·사유 원문을 늘어놓으면 읽는 데 시간이 걸리고 특정 사람의 문장에
    끌려간다. 검수자가 자기 판단을 마친 뒤 참고하는 자리라 '대략 어떻게 갈렸나' 면 족하다.

    **이름과 사유 원문은 담지 않는다.** 이름이 보이면 누가 그렇게 봤는지가 판단에 섞이고,
    사유 원문은 가장 자세해서 가장 끌려가기 쉽다. 대신 몇 건을 집계했는지(n)를 실어 화면이
    "N명 의견을 모았습니다" 라고 말할 수 있게 한다 — 조용히 줄이면 그것도 조용한 절단이다.

    **세 줄은 저장된 값에서 기계적으로 조립한다.** 모델을 돌려 요약하지 않는다. 판정을 재는
    자리 옆에 생성된 문장을 놓으면 그것이 미묘하게 틀렸을 때 아무도 확인하지 않고, 없는 합의를
    있는 것처럼 쓰면 그 문장이 곧 새 오라클이 된다(이 모듈 4번 규칙 그대로).

    재료가 없으면 자리를 채우지 않는다 — 집계할 의견이 없으면 `lines` 는 빈 목록이다.
    판정이 하나라도 있으면 세 줄 모두 실재하는 사실이라 항상 3줄이 된다(분포·지적 요소·갈린
    정도). 즉 `lines` 의 길이는 0 아니면 3이다.

    **판정 뒤에만 나간다**(verdict_precedents 와 같은 이유 · 남의 판정은 기준점이 된다)."""
    blocked = PT.need_team(team)
    if blocked:
        return blocked
    ch = str(hash or "").strip()
    # 골드와 '의견 없는 평범한 콘텐츠' 가 같은 모양이어야 한다. 골드에서만 다른 모양이 되면
    # 그 차이가 곧 "이건 골드다" 신호다(2026-08-12 에 세 번 밟은 함정).
    empty = {"lines": [], "n": 0, "split": False, "truncated": False}
    if _stage(stage) != "after":
        return dict(empty, error=AFTER_ONLY_MSG)
    if not ch:
        return dict(empty, error="콘텐츠를 지정해 주세요")
    # 여기만 골드에서 닫힌다. content_brief·verdict_precedents 는 골드를 밑 콘텐츠로 풀어
    # 평소대로 답하지만(2026-08-13), 이 도구는 풀면 안 된다. 골드 원본은 '정확' 다수결로
    # 골든이 된 콘텐츠라 **그 의견 목록이 곧 정답**이다. 대신 오류가 아니라 빈 결과를 준다
    # (운영 실측: 의견이 갈린 콘텐츠는 13.0% 뿐이라 빈 요약은 흔한 모양이다).
    if PT.is_gold(ch):
        return dict(empty)
    fb = _feedback(team).get(ch) or {}
    verdicts = list(fb.get("verdicts") or [])
    if not verdicts:
        return dict(empty)
    good, bad = int(fb.get("good") or 0), int(fb.get("bad") or 0)

    dist = " · ".join([s for s in (f"정확 {good}명" if good else "",
                                   f"수정 필요 {bad}명" if bad else "") if s])
    elems, cut = _elem_tally(ch, verdicts, team)
    picked = "지적한 요소: " + " · ".join(f"{lbl} {cnt}명" for lbl, cnt in elems) \
        if elems else "지적한 요소 없음"
    split = bool(good) and bool(bad)
    agree = f"의견 갈림 · 소수 의견 {min(good, bad)}명" if split else "의견 일치"
    return {"lines": [f"판정 {dist}", picked, agree],
            "n": len(verdicts), "split": split, "truncated": cut}


# ── 도구 등록부(트랙 A 전용) ─────────────────────────────────────────────────
# scope 는 전부 internal — 이 도구들은 팀 콘텐츠와 검수자 판정을 읽는다. 외부 MCP(트랙 B)에
# 열리면 파트너 키로 팀 검수 이력이 통째로 나간다.
# after_only=True 는 '판정 뒤에만 나가는 도구' 표시다. 실제 차단은 각 도구 함수가 하고
# (직접 호출도 막아야 한다) 이 플래그는 그 사실을 밖에서 읽을 수 있게 하는 선언이다.
_STAGE_PROP = {"type": "string", "enum": list(STAGES),
               "description": "before=판정 전 · after=판정 뒤"}

TOOLS = {
    "content_brief": {
        "scope": "internal",
        "title": "판정 근거 브리핑",
        "desc": "이 콘텐츠가 왜 그렇게 판정됐는지 3줄로 준다. 저장된 모델 근거·부여된 값·해당 분류 기준. "
                "판정 전(before)에는 근거와 기준만, 판정 뒤(after)에만 수정 제안이 붙는다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hash": {"type": "string", "description": "콘텐츠 해시"},
                "stage": {"type": "string", "enum": list(STAGES),
                          "description": "before=판정 전(근거·기준만) · after=판정 뒤(수정 제안 포함)"},
            },
            "required": ["hash"],
            "additionalProperties": False,
        },
        "fn": content_brief,
    },
    "verdict_precedents": {
        "scope": "internal",
        "after_only": True,
        "title": "비슷한 과거 판정",
        "desc": f"같은 서비스에서 {PRECEDENT_MIN_N}인 이상이 같게 확정한 과거 판정을 준다. "
                "무엇이 비슷한지(why_similar)와 몇 사람이 같게 봤는지(n)를 함께 주므로 판단은 검수자가 한다. "
                "**검수자가 판정을 낸 뒤에만**(stage=after) 쓸 수 있다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hash": {"type": "string", "description": "콘텐츠 해시"},
                "stage": _STAGE_PROP,
                "limit": {"type": "integer", "minimum": 1, "maximum": PRECEDENT_MAX,
                          "description": f"가져올 수(기본 {PRECEDENT_DEFAULT} · 최대 {PRECEDENT_MAX})"},
            },
            "required": ["hash", "stage"],
            "additionalProperties": False,
        },
        "fn": verdict_precedents,
    },
    "reviewer_dissent": {
        "scope": "internal",
        "after_only": True,
        "title": "검수자 의견 집계",
        "desc": "다른 검수자들의 의견을 3줄로 집계해 준다(판정 분포 · 지적한 요소 · 갈린 정도). "
                "검수자 이름과 사유 원문은 담지 않는다 — 누가 그렇게 봤는지가 판단에 섞이고, "
                "원문은 가장 끌려가기 쉬운 부분이다. 집계한 인원(n)은 함께 준다. "
                "**검수자가 판정을 낸 뒤에만**(stage=after) 쓸 수 있다.",
        "inputSchema": {
            "type": "object",
            "properties": {"hash": {"type": "string", "description": "콘텐츠 해시"},
                           "stage": _STAGE_PROP},
            "required": ["hash", "stage"],
            "additionalProperties": False,
        },
        "fn": reviewer_dissent,
    },
}


def registry() -> dict:
    """이 앞단(/assist)이 노출할 도구 = 트랙 A 전용 + prismtools 의 internal 스코프.

    `/assist` 의 소비자는 검수 화면만이 아니라 **내부 검수 보조 에이전트**다(트랙 A의 본체).
    에이전트가 판정 근거를 읽을 때 분류 체계·엔티티 사전을 함께 봐야 하고, 그 도구들은 이미
    `prismtools` 에 한 벌 있다. `tools_for("internal")` 이 바로 이 앞단을 위한 계약이라
    복제하지 않고 그대로 받는다 — 도구가 두 벌이 되면 어긋나고, 어긋난 쪽이 조용히 틀린다.

    공용 도구는 해시를 받지 않는다(`get_taxonomy` 는 kind·service, `lookup_entity` 는 이름).
    어떤 콘텐츠를 보고 있는지 서버에 알리지 않고 응답도 콘텐츠와 무관하므로, 골드 문항을
    보는 중에 불려도 그 사실이 새지 않는다. 팀 강제는 `call` 이 공통으로 건다.

    이름이 겹치면 트랙 A 정의가 이긴다."""
    reg = dict(PT.tools_for("internal"))
    reg.update(TOOLS)
    return reg


def call(name, args, team=None) -> dict:
    """도구 실행 진입점. 가드·디스패치는 prismtools.call 을 그대로 쓴다(규칙 단일 원천).

    team 은 **호출자가 세션에서 해석한 값**이다. args 의 team 은 무시된다(스키마에 없다).
    이름·인자의 타입은 여기서 강제한다 — 앞단이 HTTP 본문이라 문자열도 dict 도 아닌 값이
    그대로 들어올 수 있고, 그러면 도구 오류가 아니라 500 이 난다(내부 노출)."""
    return PT.call(str(name or ""), args if isinstance(args, dict) else {},
                   team=team, registry=registry())


# ══════════════════════════════════════════════════════════════════════════════
# 자유질문(/assist-ask) · 칩으로 안 되는 것을 검수자가 직접 묻는 경로
# ══════════════════════════════════════════════════════════════════════════════
# 검수 보조가 접이식 패널에서 대화창이 된다. 칩(위의 도구들)은 모델을 거치지 않고 도구를
# 그대로 부르고, 칩으로 안 되는 것만 여기로 온다. **모델이 등장하는 유일한 자리**라
# 이 모듈의 규칙이 가장 쉽게 무너지는 곳이기도 하다. 그래서 아래 여섯을 구조로 박는다.
#
# 1. **판정 뒤에만 열린다.** 판정 전에 열면 반드시 "그래서 이거 맞아 틀려" 가 나오고,
#    모델이 근거와 기준만으로 사실상 판정을 해 버린다. 그 순간 우리가 재는 대상이 사람이
#    아니라 모델이 된다. 이 기능 전체의 전제라 타협 대상이 아니다(`_stage` 로 수렴 · 모르는
#    값은 before 로 떨어져 거절된다).
# 2. **콘텐츠 해시는 서버가 못 박는다.** 모델이 도구 인자로 다른 해시를 넣을 수 있으면
#    그게 곧 남의 콘텐츠 통로다. team 을 도구 스키마에서 뺀 것과 같은 이유다. 팀은 세션에서,
#    해시는 요청 본문에서 오고 둘 다 모델 손이 닿지 않는다(`_ask_tool`).
# 3. **모델에게는 우리 도구 결과만 준다.** 웹 검색·파일 읽기·외부 페치가 없다. 도구가 답을
#    못 주면 거기서 끝낸다. 재료가 비면 **모델을 부르지 않는다**(프롬프트로 부탁하는 게
#    아니라 호출 자체가 일어나지 않는다).
# 4. **출처 없는 문장은 나가지 못한다.** 응답의 답변은 문자열이 아니라 {text, sources} 목록이고,
#    서버가 실재하지 않는 id 를 지운 뒤 남은 id 가 없는 항목을 통째로 버린다. 화면은 출처 없는
#    문장을 그릴 방법이 없다. 검수자가 대조할 수 없는 문장은 보조가 아니라 또 하나의 추측이다.
# 5. **골드 문항을 특별 취급하지 않는다.** 이 경로에는 `is_gold` 분기가 없다. 골드는 도구가
#    이미 빈 결과를 주므로 '자료 없는 콘텐츠' 와 같은 자리에서 같은 모양으로 끝난다.
#    `content_brief` 의 골드 거절이 걷히면(그 거절은 골드가 등급을 뒤집던 시절의 잔재다 ·
#    `_ask_terms` 주석) 골드에도 자료가 채워져 '평범한 콘텐츠' 와 같아진다. **이 파일은 그때
#    고칠 것이 없다.** 분기가 없다는 것이 곧 두 상태 모두에서 맞다는 뜻이다.
# 6. **어떤 모델이 답했는지 남긴다.** 응답에도 싣고 롤링 로그에도 적는다. 설정이 바뀌면 답의
#    성격도 바뀌는데 기록이 없으면 나중에 "그때 왜 이렇게 답했지" 를 되짚을 수 없다.
#
# import 를 파일 끝에 두는 이유: 이 블록은 통째로 뒤에 붙은 것이라 상단 import 를 건드리면
# 같은 파일을 만지는 다른 작업과 첫 줄부터 충돌한다. 모듈 수준 import 는 위치 제약이 없다.
import threading as _threading                    # noqa: E402  (아래 상한 카운터 전용)
import time as _time                              # noqa: E402
from . import modelmeta as _MM                    # noqa: E402  (답한 모델 이름 표기)

ASK_STAGE_MSG = "판정을 낸 뒤에 물어볼 수 있습니다 · 먼저 판정해 주세요"
ASK_EMPTY_Q = "무엇이 궁금한지 적어 주세요"
ASK_NO_CONTENT = "콘텐츠를 지정해 주세요"
# 자료가 없을 때 문구. '골드라서' 가 아니라 '자료가 없어서' 다. 골드도, 자료 없는 평범한
# 콘텐츠도 같은 문구로 끝난다(둘을 구분하는 문장을 만드는 순간 그게 골드 신호다).
ASK_NO_SOURCE = "가지고 있는 자료로는 답할 수 없습니다 · 사전·정책 탭에서 확인해 주세요"
# 모델 호출 실패 문구는 **어디를 봐야 하는지** 가리킨다. 선택지에 라우터 키가 없는 모델도
# 뜨므로 '설정은 멀쩡한데 호출만 실패' 하는 경우가 실제로 생긴다. "답하지 못했습니다" 로
# 끝내면 검수자도 관리자도 갈 곳을 모른다(도구 오류를 다음 행동이 보이게 쓰는 것과 같은 이유).
_NO_MODEL_HEAD = "설정된 검수 보조 모델%s을 부를 수 없습니다"
_NO_MODEL_TAIL = " · 시스템 설정에서 다시 골라 주세요 · 아래 자료는 그대로 보실 수 있습니다"
ASK_NO_MODEL = (_NO_MODEL_HEAD % "") + _NO_MODEL_TAIL


def _ask_no_model(mid: str) -> str:
    """어느 모델이 안 되는지까지 적는다. 관리자가 그 이름을 설정에서 찾아 바꾸면 된다.
    라우터가 돌려준 사유 원문은 싣지 않는다(내부 구현 노출)."""
    return (_NO_MODEL_HEAD % ("(%s)" % _clip(mid, 60) if mid else "")) + _NO_MODEL_TAIL
ASK_QUOTA_MSG = "오늘 물어볼 수 있는 횟수를 다 썼습니다"
ASK_UNGROUNDED = "근거를 댈 수 있는 답이 나오지 않았습니다 · 아래 자료를 직접 봐 주세요"

ASK_Q_MAX = 300                                   # 질문 길이 상한(프롬프트 주입 면적 제한)
ASK_SRC_TEXT_MAX = 400                            # 자료 1건 길이 상한
ASK_SRC_MAX = 12                                  # 자료 건수 상한(넘으면 truncated 로 밝힌다)
ASK_LINE_MAX = 300                                # 답변 1줄 길이 상한
ASK_LINES_MAX = 5                                 # 답변 줄 수 상한
ASK_CITE_MAX = 4                                  # 한 줄에 다는 출처 수 상한
ASK_TERM_MAX = 4                                  # 사전에서 끌어올 분류값 수 상한
# 사용자당 하루 질문 수(모델 호출 = 과금). 값은 초안이고 실사용을 보고 조정한다.
# ⚠️ **실제로는 "하루 30건" 이 아니라 "재시작 사이 30건" 이다.** 카운터(_ASK_HITS)가 프로세스
# 메모리라 배포·재시작마다 0 으로 돌아가는데, 이 저장소는 main 머지마다 자동 배포라 재시작이
# 잦다. 단단한 일일 상한으로 믿고 과금을 계산하면 틀린다. 원장으로 올리는 것은 자유질문이
# 실제로 얼마나 쓰이는지 본 뒤에 정한다(지금 올리면 쓰이지도 않는 것에 저장 계층을 늘린다).
ASK_DAILY_MAX = 30


# ── 모델 선택 자리 ───────────────────────────────────────────────────────────
# 모델은 **시스템 설정의 검수 보조 모델 항목**이 정한다. 해석은 `prism.config.assist_model`
# 하나뿐이고(serve 가 같은 이름으로 재수출) 미설정·잘못된 값·정상값 어디서도 항상 쓸 수 있는
# 모델명을 돌려준다. 그래서 이 파일에는 기본값 표도 분기도 없다. 두 벌이 되면 반드시
# 어긋나고, 어긋난 쪽이 조용히 다른 모델로 과금한다.
#
# 다른 컴포지션 루트(테스트 등)에서 갈아끼우려면 한 줄:  RA.ASK_MODEL_RESOLVER = <함수>
ASK_MODEL_RESOLVER = None


def ask_model() -> str:
    """자유질문에 쓸 모델 id. **해석은 설정 계층이 한다.**

    미설정일 때 판정 모델(`cfg.model`)을 따라가지 않는 것도 설정 계층의 계약이다. 이 설정을
    따로 둔 이유가 자기 판정을 자기가 변호하지 않게 하려는 것이라서다.

      ① `ASK_MODEL_RESOLVER`: 명시 주입(테스트·다른 컴포지션 루트)
      ② `_SV.assist_model`: 설정 계층의 해석 함수(정상 경로 · `config.assist_model` 재수출)
      ③ `""`: ②가 없을 때만 닿는 마지막 가지. `llm_for_model` 이 기존 설정 모델로 해석한다.
         PR #439 로 ②가 들어와 지금은 죽은 가지다. **여기에 모델 이름을 적어 넣지 말 것.**
         설정 계층의 기본값과 갈라지는 순간 화면에 표시된 모델과 실제 과금 모델이 달라진다.

    ②를 하드 import 하지 않고 `_SV` 로 더듬는 이유: 이 브랜치를 딸 때는 설정 항목이 머지 전이라
    import 하면 모듈이 통째로 못 떴다. 이름만 맞으면 붙게 해 둔 덕에 #439 머지 뒤 이 파일을
    한 줄도 안 고치고 연결됐다(실측: `ask_model()` → `solar-pro2`). 그대로 둔다."""
    for fn in (ASK_MODEL_RESOLVER, getattr(_SV, "assist_model", None)):
        if not callable(fn):
            continue
        try:
            mid = str(fn() or "").strip()
        except Exception:
            continue                              # 해석이 흔들려도 답은 나간다
        if mid:
            return mid
    return ""


# ── 사용자당 상한 ────────────────────────────────────────────────────────────
_ASK_HITS = {}          # (day, uid) -> 사용 횟수 · **프로세스 메모리**(재시작마다 0 · ASK_DAILY_MAX 주석)
_ASK_LOCK = _threading.Lock()


def _ask_quota(uid) -> tuple:
    """(허용?, 오늘 남은 수). 모델 호출은 과금이라 상한이 없으면 한 사람이 예산을 다 쓴다.

    **자료가 있든 없든 접수된 질문은 모두 센다.** 자료 없는 질문만 공짜로 두면 남은 횟수가
    줄지 않는 것 자체가 "이건 자료가 없는 문항(=골드)" 신호가 된다. 셈이 답의 내용에 따라
    갈리지 않게 하는 것이 이 함수의 계약이다."""
    key = (_time.strftime("%Y-%m-%d"), str(uid or "?")[:64])
    with _ASK_LOCK:
        if len(_ASK_HITS) > 4096:                 # 지난 날짜 정리(장기 가동 시 무한 성장 방지)
            for k in [k for k in _ASK_HITS if k[0] != key[0]]:
                _ASK_HITS.pop(k, None)
        n = int(_ASK_HITS.get(key, 0))
        if n >= ASK_DAILY_MAX:
            return False, 0
        _ASK_HITS[key] = n + 1
        return True, max(0, ASK_DAILY_MAX - (n + 1))


# ── 자료 수집 ────────────────────────────────────────────────────────────────
def _ask_tool(name: str, args: dict, ch: str, team) -> dict:
    """자유질문 경로의 도구 호출. **해시는 서버가 못 박는다.**

    args 에 hash 가 무엇으로 들어오든 화면이 열고 있는 콘텐츠로 덮어쓴다. 지금은 서버가 도구
    순서를 정하지만 나중에 모델이 도구를 고르게 되어도 이 함수만 지나면 해시는 고정된다.
    그래서 덮어쓰기를 호출부마다가 아니라 여기 한 곳에 둔다(호출부에 두면 새 호출부가 빠뜨린다).

    **도구 오류는 자료 없음과 똑같이 다룬다.** 문구를 밖으로 내보내지 않는다: 골드 문항에서
    `content_brief` 는 거절 문구를 돌려주는데 그게 답변이나 자료 목록에 실리면 그 한 줄이 곧
    "이건 골드다" 신호다. 찾지 못한 콘텐츠·팀 밖 콘텐츠도 같은 모양으로 끝난다."""
    a = {k: v for k, v in (args or {}).items() if k != "team"}
    spec = registry().get(name) or {}
    if "hash" in ((spec.get("inputSchema") or {}).get("properties") or {}):
        a["hash"] = ch
    r = call(name, a, team=team)
    return r if (isinstance(r, dict) and not r.get("error")) else {}


def _ask_terms(q: str) -> tuple:
    """질문에 이름이 그대로 등장한 분류값 (인텐트, 카테고리).

    이 조회에는 **해시가 들어가지 않는다**(공용 사전 = 콘텐츠와 무관). 그래서 골드 문항에서도
    평범한 콘텐츠와 똑같이 자료가 잡히고, 정책을 묻는 질문은 자료 유무로 갈리지 않는다.
    골드 경로를 평범한 경로와 같게 유지하는 장치가 이것이다.

    남은 자리와 그 자리를 닫는 곳: "이건 왜 R 이야" 처럼 **콘텐츠에만 답이 있는 질문**은
    `content_brief` 가 골드를 거절하는 동안 자료가 비고, 자료가 비면 빨리 끝난다.
    **이 함수가 고칠 자리가 아니다.** 그 거절은 골드가 **등급**을 뒤집던 시절의 잔재이고,
    2026-08-13 부터 골드는 카테고리를 뒤집는다(PR #438). 등급과 저장된 근거가 참값이 됐으니
    숨길 이유가 사라졌다. 후속 작업이 거절을 걷고 카테고리 관련 두 자리만 비우면, 골드에도
    평소대로 자료가 채워져 분포 자체가 같아진다.

    그러니 여기서 억지로 메우지 말 것. 없는 자료를 지어내면 4번 규칙이 깨지고, 가짜 지연은
    거짓말이지 방어가 아니다. **화면이 골드에서 서버를 안 부르게 하는 방향도 아니다.**
    무응답이 다시 골드 신호가 되고, 무엇보다 지금 없애는 중인 단락을 되살리는 일이 된다."""
    s = str(q or "")
    pool = list(D.INTENT_VALUE_DEFS) + [k for k in D.INTENT_EXAMPLES
                                        if k not in D.INTENT_VALUE_DEFS]
    intents = [k for k in pool if k and k in s]
    cats = [k for k, ko in (getattr(D, "TIER2_KO", {}) or {}).items()
            if (k and k in s) or (ko and ko in s)]
    return intents[:ASK_TERM_MAX], cats[:ASK_TERM_MAX]


def _ask_sources(ch: str, question: str, team) -> tuple:
    """(자료 목록, 잘림). 답의 재료를 **서버가** 모아 온다.

    모델은 이 목록 밖을 볼 수 없다. 도구도 웹도 파일도 주지 않고 여기서 만든 문자열만
    프롬프트에 실린다. '지어내지 말라' 가 부탁이 아니라 구조가 되는 자리다."""
    out, seen = [], set()

    def add(tool, field, label, text):
        """같은 문장은 한 번만 싣는다. 분류 기준(content_brief.criteria)과 분류 정의
        (get_examples.desc)는 같은 사전에서 오므로 그대로 두면 자료 자리를 둘씩 먹는다."""
        t = _clip(text, ASK_SRC_TEXT_MAX)
        if t and t not in seen:
            seen.add(t)
            out.append({"id": "s%d" % (len(out) + 1), "tool": tool, "field": field,
                        "label": label, "text": t})

    # ① 이 콘텐츠(해시는 _ask_tool 이 못 박는다 · 여기는 이미 판정 뒤라 stage=after)
    brief = _ask_tool("content_brief", {"stage": "after"}, ch, team)
    vals = brief.get("values") or {}
    if brief.get("has_evidence"):
        add("content_brief", "evidence", "모델이 남긴 판정 근거", brief.get("evidence"))
    grade, rl = str(vals.get("grade") or ""), ", ".join(vals.get("reason_labels") or [])
    if grade or rl:
        add("content_brief", "values.grade", "부여된 등급·품질 사유",
            "등급 %s · 품질 사유 %s" % (grade or "미상", rl or "없음"))
    if vals.get("intent") or vals.get("content_category"):
        add("content_brief", "values.intent", "부여된 인텐트·카테고리",
            "인텐트 %s · 카테고리 %s" % (" · ".join(vals.get("intent") or []) or "없음",
                                     " · ".join(vals.get("content_category") or []) or "없음"))
    for c in (brief.get("criteria") or [])[:ASK_TERM_MAX]:
        add("content_brief", "criteria.%s" % (c.get("key") or ""),
            "분류 기준 · %s" % (c.get("key") or ""), c.get("desc"))
    for sg in (brief.get("suggestions") or [])[:3]:
        add("content_brief", "suggestions.%s" % (sg.get("field") or ""), "과거 교정 집계",
            "%s: %s → %s · %s" % (sg.get("field") or "", sg.get("from") or "(빈값)",
                                  sg.get("to") or "", sg.get("basis") or ""))

    # ② 남이 내린 판정 · 판정 뒤에만 나가는 도구다(after_only). 여기는 이미 판정 뒤다.
    prec = _ask_tool("verdict_precedents", {"stage": "after", "limit": 3}, ch, team)
    for it in (prec.get("items") or [])[:3]:
        add("verdict_precedents", "items.verdict", "비슷한 과거 판정",
            "%s · %s인 일치 · %s" % (it.get("verdict") or "", it.get("n") or 0,
                                  it.get("why_similar") or ""))
    dis = _ask_tool("reviewer_dissent", {"stage": "after"}, ch, team)
    for i, ln in enumerate((dis.get("lines") or [])[:3]):
        add("reviewer_dissent", "lines.%d" % i, "다른 검수자 의견 집계", ln)

    # ③ 정책 사전(콘텐츠와 무관) = 화면에 걸린 값 + 질문에 등장한 값.
    #    질문에서 뽑은 쪽은 골드에서도 잡히는 자료라 경로가 갈리는 것을 줄인다(_ask_terms 주석).
    qi, qc = _ask_terms(question)
    svc = str(vals.get("service") or "")
    for kind, want in (("intent", list(vals.get("intent") or []) + qi),
                       ("category", list(vals.get("content_category") or []) + qc)):
        want = [w for w in dict.fromkeys(want) if w][:ASK_TERM_MAX]
        if not want:
            continue
        ex = _ask_tool("get_examples", {"kind": kind, "values": want, "service": svc}, ch, team)
        for it in (ex.get("items") or [])[:ASK_TERM_MAX]:
            key, lbl = it.get("key") or "", it.get("label") or it.get("key") or ""
            if it.get("has_example"):
                # 초안 표시를 자료 문장에 함께 싣는다. 모델이 확정 정책처럼 옮겨 적지 못하게.
                add("get_examples", "items.%s.example" % key, "정책 예시(초안) · %s" % lbl,
                    "%s · %s" % (it.get("example") or "", PT.EXAMPLES_DRAFT_NOTE))
            elif it.get("desc"):
                add("get_examples", "items.%s.desc" % key, "분류 정의 · %s" % lbl, it.get("desc"))

    rows, truncated, _total = PT.cap(out, ASK_SRC_MAX)
    return rows, truncated


# ── 모델 호출 ────────────────────────────────────────────────────────────────
ASK_SYSTEM = (
    "너는 콘텐츠 검수자를 돕는 보조다. 검수자는 **이미 판정을 냈고** 지금은 왜 그런지 확인하는 중이다.\n"
    "\n"
    "규칙(어기면 그 문장은 서버가 버린다):\n"
    "1. 아래 [자료] 에 적힌 것만 근거로 쓴다. 자료 밖의 지식·짐작·일반 상식을 쓰지 않는다.\n"
    "2. 문장마다 근거가 된 자료의 id 를 단다. 근거를 달 수 없는 문장은 아예 쓰지 않는다.\n"
    "3. 자료가 질문에 답하지 못하면 answer 를 빈 목록으로 두고 unknown 에 무엇이 없는지 한 줄로 적는다.\n"
    "   모르는 것을 채우지 않는다. 없다고 말하는 것이 이 보조의 정확성이다.\n"
    "4. 등급을 새로 판정하지 않는다. 맞다·틀리다를 말하지 말고 자료가 무엇을 말하는지만 옮긴다.\n"
    "5. 자료에 '초안' 이라고 적혀 있으면 그 사실을 그대로 옮긴다(확정 정책처럼 쓰지 않는다).\n"
    "6. 한국어로 짧게 쓴다. 한 문장에 한 가지만 담는다.\n"
    "\n"
    '반드시 이 JSON 만 출력한다: {"answer":[{"text":"한 문장","sources":["s1"]}],"unknown":""}'
)


def _ask_user(question: str, sources: list) -> str:
    lines = ["[%s] (%s · %s) %s: %s" % (s["id"], s["tool"], s["field"], s["label"], s["text"])
             for s in sources]
    return "# 질문\n%s\n\n# 자료 (이 밖의 것은 쓸 수 없다)\n%s" % (question, "\n".join(lines))


def _ask_llm(model: str, mock: bool) -> tuple:
    """(llm, 실제 모델 id). 부를 수 없으면 (None, ""): 사유 원문은 응답에 싣지 않는다."""
    if _SV is None:
        return None, ""
    try:
        llm, _route = _SV.llm_for_model(str(model or ""), bool(mock))
    except Exception:
        return None, ""
    if llm is None:
        return None, ""
    return llm, str(getattr(llm, "model", "") or model or "")


def _ask_clean(obj, sources: list) -> list:
    """모델 응답 → 화면에 그릴 수 있는 답. **출처 없는 문장은 여기서 사라진다.**

    검사가 프롬프트가 아니라 여기 있는 이유: 프롬프트는 지켜 달라는 부탁이고 이 함수는 지키지
    않은 문장을 못 나가게 하는 문이다. 없는 id 를 붙였거나(환각 인용) id 를 안 붙인 문장은
    통째로 버린다. 검수자가 대조할 수 없는 문장은 보조가 아니라 또 하나의 추측이다."""
    ok = {s["id"] for s in sources}
    rows = (obj.get("answer") if isinstance(obj, dict) else None) or []
    if not isinstance(rows, (list, tuple)):
        return []
    out = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        raw = it.get("sources")
        raw = [raw] if isinstance(raw, str) else (raw if isinstance(raw, (list, tuple)) else [])
        ids = [x for x in dict.fromkeys(str(v).strip() for v in raw) if x in ok]
        text = _clip(it.get("text"), ASK_LINE_MAX)
        if not (text and ids):
            continue
        out.append({"text": text, "sources": ids[:ASK_CITE_MAX]})
        if len(out) >= ASK_LINES_MAX:
            break
    return out


def _ask_log(team, uid, ch: str, model: str, question: str, n_src: int, generated: bool):
    """무엇으로 답했는지 남긴다(롤링 200건 · handoff_log 관례).

    설정이 바뀌면 답의 성격도 바뀌는데 기록이 없으면 나중에 "그때 왜 이렇게 답했지" 를
    되짚을 수 없다. 실패해도 흐름은 계속한다. 기록 때문에 답이 막히지는 않는다."""
    if _SV is None:
        return
    try:
        with _ASK_LOCK:
            rep = _SV._report_get("assist_ask_log", team, {}) or {}
            entries = list(rep.get("entries") or [])
            entries.append({"ts": _time.time(), "uid": str(uid or "")[:64],
                            "hash": str(ch or "")[:32], "model": str(model or ""),
                            "sources": int(n_src), "generated": bool(generated),
                            "q": _clip(question, 80)})
            _SV._report_save("assist_ask_log", {"entries": entries[-200:]}, team)
    except Exception:
        pass


def ask(hash: str = "", stage: str = "", question: str = "", team=None,
        uid="", mock=False) -> dict:
    """자유질문 1건. 계약은 이 블록 머리말의 여섯 항목이다.

    응답(화면 계약):
      stage · question(서버가 자른 실제 질문) · answer[{text, sources[]}] ·
      sources[{id, tool, field, label, text}] · generated(모델을 불렀나) ·
      model · model_label · note(사람이 읽을 한 줄) · remaining(오늘 남은 질문 수) · truncated
    answer 의 각 항목은 **반드시** 실재하는 sources id 를 하나 이상 갖는다(없으면 서버가 버렸다)."""
    blocked = PT.need_team(team)
    if blocked:
        return blocked
    # 판정 전 거절이 먼저다. 해시·질문 검증보다 앞에 두어 '그 해시가 있기는 한가' 같은 부수
    # 정보도 판정 전에는 새지 않게 한다(모르는 stage 는 _stage 가 before 로 수렴시킨다).
    if _stage(stage) != "after":
        return {"error": ASK_STAGE_MSG}
    ch = str(hash or "").strip()
    if not ch:
        return {"error": ASK_NO_CONTENT}
    q = _clip(question, ASK_Q_MAX)
    if not q:
        return {"error": ASK_EMPTY_Q}
    allowed, remaining = _ask_quota(uid)
    if not allowed:
        return {"error": ASK_QUOTA_MSG}

    sources, truncated = _ask_sources(ch, q, team)
    out = {"stage": "after", "question": q, "answer": [], "sources": sources,
           "truncated": truncated, "generated": False, "model": "", "model_label": "",
           "note": "" if sources else ASK_NO_SOURCE, "remaining": remaining}
    if not sources:
        # 도구가 답을 못 주면 여기서 끝낸다. 모델을 부르지 않으므로 지어낼 자리가 없다.
        _ask_log(team, uid, ch, "", q, 0, False)
        return out

    want = ask_model()
    llm, used = _ask_llm(want, mock)
    if llm is None:
        # 자료는 그대로 준다(빈손으로 돌려보내지 않는다) + 어디를 봐야 하는지 가리킨다.
        # 로그에는 **부르려던 모델**을 남긴다. generated=False 와 함께 읽으면
        # "그 모델로 시도했는데 안 됐다" 가 되고, 그게 나중에 되짚을 수 있는 유일한 단서다.
        out["note"] = _ask_no_model(want)
        _ask_log(team, uid, ch, want, q, len(sources), False)
        return out
    out["model"], out["model_label"] = used, _MM.label(used)
    out["generated"] = True
    try:
        obj, _res = llm.complete_json(ASK_SYSTEM, _ask_user(q, sources), tag="assist_ask")
    except Exception:
        obj = None                                # 예외 원문은 응답에 싣지 않는다(내부 노출)
    out["answer"] = _ask_clean(obj, sources)
    if not out["answer"]:
        out["note"] = ASK_UNGROUNDED
    _ask_log(team, uid, ch, used, q, len(sources), True)
    return out
