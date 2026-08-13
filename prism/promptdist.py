"""외부 파트너용 **추출 프롬프트 배포**와 **결과 규칙 검증** (트랙 B · 외부 MCP).

## 왜 이 자리인가

외부 파트너가 프리즘 MCP 를 붙이면 지금은 분류 체계·정책 예시·개체 사전만 조회할 수 있다.
그걸로 파트너의 LLM 이 메타를 만들 수는 있지만 **기준 해석을 그 모델이 하므로 프리즘 메타가
아니다.** 모델마다 결과가 갈리고, 어떤 프롬프트로 만든 것인지 남지 않아 되짚을 수 없다.

반대쪽 끝(프리즘이 대신 추출을 실행)은 남의 콘텐츠를 우리 비용으로 도는 자리라 과금 장치가
먼저다. 그건 다음 단계다. 이 모듈은 그 사이에 있다 · **기준(프롬프트)은 프리즘이 주고 실행은
클라이언트가 한다.** 결과가 프리즘 정의를 따르고, 버전이 응답에 남아 재현이 되고, 비용은 부른
쪽이 낸다.

## 이 모듈이 지키는 규칙

1. **프롬프트를 새로 쓰지 않는다.** 조립은 `meta_prompts.call_system` 한 곳뿐이다. 규칙·사전·
   골드 예시·자가점검이 이미 거기 모여 있고, 여기서 따로 쓰면 그 순간 기준이 두 벌이 된다.
   (두 벌이 되면 반드시 어긋나고, 어긋난 쪽이 조용히 틀린다 · `prismtools` 독스트링과 같은 이유.)
2. **학습 보정(learned)은 싣지 않는다.** 이유 둘. ① 팀 검수 이력에서 뽑은 내부 운영 데이터라
   팀 밖으로 나가면 안 된다. ② 버전에 안 잡히고 배치마다 바뀌므로, 실으면 같은 버전이 같은
   프롬프트를 뜻하지 않게 되어 이 도구의 존재 이유(재현성)가 사라진다. → `prompts.call_system`
   (learned 병기)이 아니라 `meta_prompts.call_system` 을 쓴다. **보정의 분량조차 응답에 싣지
   않는다** · 길이도 팀 데이터의 신호다.
3. **버전과 지문을 반드시 싣는다.** 버전 없이 프롬프트만 주면 '파트너가 알아서' 와 다를 바 없다.
   운영자가 계열 래퍼를 편집하면(`WRAPPER_OVERRIDES`) 버전 태그는 그대로인데 프롬프트가 달라지므로,
   본문 해시(fingerprint)를 함께 실어 '같은 버전 = 같은 프롬프트' 를 파트너가 스스로 대조할 수 있게 한다.
4. **실제 실행과 다를 수 있다는 것을 응답에 적는다**(`differs_from_prism_run`). 파트너가 결과
   차이를 우리 탓으로 오해하지 않게, 그리고 우리가 무엇을 뺐는지 숨기지 않기 위해.
5. **검증은 판정하지 않는다.** 등급·점수·'좋다/나쁘다' 를 내지 않는다. 우리가 돌리지도 않은
   결과에 품질 판정을 붙이면 그 판정에는 아무 근거가 없다. 사전·규칙으로 기계적으로 갈리는
   위반만 사실로 보고하고, 위반이 없으면 '규칙 위반 없음' 이지 '정확하다' 가 아니다.
   근거를 봐야 갈리는 것(같은 근거인지 아닌지)은 `kind="check"` 로 자리만 알린다.

## 팀 데이터

두 도구 모두 팀 데이터를 읽지 않는다(사전·계약 원문·모듈 상수뿐). 그래도 `prismtools.call` 의
공통 팀 강제는 그대로 지난다 · 파트너 키에 팀이 없으면 애초에 부를 수 없어야 한다(감사 H1).
"""
from __future__ import annotations

import hashlib
import json

from . import dictionaries as D
from . import meta_prompts as MP
from .prompts import IMETA_VERSION      # 버전 태그의 단일 원천(prompts.py 소유)
#   ↑ prompts 를 들이지만 **prompts.call_system 을 쓰지 않는다.** 그건 learned 를 병기하는
#     운영 실행용이다(규칙 2). 여기서 필요한 것은 버전 상수뿐이다.

# 응답 상한. 프롬프트는 **자르지 않는다** · 반쯤 잘린 기준은 쓸모없는 정도가 아니라 조용히
# 다른 기준이 되어 결과를 오염시킨다. 상한을 넘으면 본문을 빼고 넘었다는 사실을 알린다.
PROMPT_MAX_BYTES = 60000
ISSUE_LIMIT = 60                        # 검증 결과(위반·확인) 항목 상한

CALLS = MP.CALLS                        # 분리형 순차 4콜(재수출 · 등록부 enum 의 원천)
CONTRACT_FIELDS = ("summary", "entities", "intent", "content_category")
PRIOR_FIELDS = ("summary", "entities", "intent")     # 앞 콜의 출력을 뒤 콜이 받는 자리

# 자리표 렌더용(입력 계약을 파트너에게 보여줄 때만 쓴다 · 실제 추출 입력이 아니다)
_PH_BODY = "{body}"
_PH_LEN = "본문 글자수: {본문 글자수}"
_PH_IMG = "이미지 수: {이미지 수 · 모르면 '정보 없음' 이라고 쓴다 · '0장' 과 다르다}"

NOT_A_VERDICT = (
    "이 도구는 사전·규칙으로 기계적으로 갈리는 위반만 봅니다. 위반 0건은 '규칙 위반 없음' 이지 "
    "'메타가 정확하다' 는 뜻이 아닙니다 · 값이 콘텐츠에 맞는지는 검사하지 않으며 등급·점수도 내지 않습니다."
)
CHECK_MEANING = (
    "kind=violation 은 사전·계약으로 확정되는 위반이고, kind=check 는 '같은 근거인지' 처럼 "
    "콘텐츠를 봐야 갈리는 자리라 판단하지 않고 자리만 알리는 것입니다."
)


# ══════════════════════════════════════════════════════════════════════════════
# 도구 1 · 추출 프롬프트 배포
# ══════════════════════════════════════════════════════════════════════════════
def _fingerprint(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


class _Slots:
    """`meta_prompts.call_user` 가 읽는 필드만 가진 자리표 콘텐츠.

    입력 계약(어느 콜에 무엇을 넣는가)을 손으로 다시 적지 않기 위해 실제 조립 함수를 그대로
    돌린다 · ③ 인텐트 콜의 투영은 2026-08-03 에 한 번 바뀌었고 또 바뀔 수 있다. 여기서 베껴
    적으면 그때 이 응답만 옛 계약을 말하게 된다."""
    displayServiceName = "{displayServiceName}"
    title = "{title}"
    body = _PH_BODY
    image_urls = None                    # 목록이 아니면 '정보 없음' 줄이 나온다(_intent_image_line)


def user_template(call: str) -> str:
    """콜별 user 메시지 자리표. 파생값(글자수·이미지 수)만 자리표로 되돌린다.

    글자수는 자리표 문자열의 길이가 그대로 찍히므로(예 '본문 글자수: 6') 반드시 되돌려야 한다.
    라벨이 바뀌면 이 치환이 조용히 no-op 이 되어 **엉뚱한 상수**가 파트너에게 나간다.
    tests/test_promptdist.py 가 자리표 존재와 상수 부재를 함께 단언한다(지우지 말 것)."""
    prior = {"summary": "{summary · ① 리드문 콜의 출력}",
             "intent": ["{intent · ③ 인텐트 콜의 출력}"],
             "entities": ["{entities · ② 엔티티 콜의 출력}"]}
    txt = MP.call_user(call, _Slots(), prior)
    txt = txt.replace("본문 글자수: %d" % len(_PH_BODY), _PH_LEN)
    return txt.replace(MP._IMG_UNKNOWN, _PH_IMG)


def _requires(template: str) -> list:
    """이 콜이 앞 콜에서 받아야 하는 출력. 자리표에서 **읽어낸다**(따로 적으면 갈린다)."""
    return [k for k in PRIOR_FIELDS if (k + ":") in template]


def _differences(customized: bool) -> list:
    """프리즘 실제 실행과 이 응답이 갈리는 지점. 숨기지 않는 것이 목적이다.

    ⚠️ 학습 보정의 **길이·건수 같은 수치를 여기 싣지 말 것.** 값을 안 실어도 분량은 팀의 교정
    이력 규모를 그대로 알려주는 신호다. 있다/없다가 아니라 '무엇이 빠졌는지' 만 말한다."""
    out = [
        "학습 보정 제외: 프리즘 운영 실행은 팀의 검수 교정 이력에서 누적된 보정 지시를 프롬프트 "
        "말미에 덧붙입니다. 이 응답에는 없습니다 · 팀 내부 운영 데이터이고, 버전에 잡히지 않아 "
        "실으면 같은 버전이 같은 프롬프트를 뜻하지 않게 됩니다. 그만큼 결과가 프리즘 실행과 다를 수 있습니다.",
        "모델 차이: 프리즘 운영이 쓰는 모델과 부르시는 모델이 다르면 같은 프롬프트라도 결과가 갈립니다. "
        "계열 래퍼만 client_model 로 맞춰 드립니다.",
        "후처리 제외: 프리즘은 추출 뒤 카테고리 표기 정규화·개체 사전 연결 같은 적재 단계 처리를 "
        "따로 합니다. 이 프롬프트에는 그 단계가 들어 있지 않습니다.",
    ]
    if customized:
        out.append("계열 래퍼 편집됨: 운영자가 프롬프트 스튜디오에서 이 모델 계열의 래퍼를 편집한 상태라 "
                   "version 태그만으로는 프롬프트가 특정되지 않습니다. fingerprint 로 대조하세요.")
    return out


def get_extraction_prompt(call: str = "", service: str = "", client_model: str = "",
                          team=None) -> dict:
    """프리즘의 **현행 추출 프롬프트**를 콜 단위로 내려준다(클라이언트가 자기 모델로 실행).

    call = 분리형 순차 4콜 중 하나(summary → entities → intent → category). 뒤 콜은 앞 콜의
    출력을 받으므로 `requires` 와 `user_template` 을 함께 준다.

    service(displayServiceName)는 ③ 인텐트 사전의 서비스 분기에 쓰인다 · 후보가 서비스마다
    다르므로 이 값이 프롬프트를 바꾼다. 미정의 값이면 프리즘 운영과 **똑같이** PGC 폴백으로
    수렴한다(범용 분류값만 · `service_resolved` 가 빈 문자열로 그 사실을 알린다).

    client_model 은 계열 래퍼(gpt·gemini·claude·solar·default)를 고르는 데만 쓴다. 모르는
    이름이면 조용히 default 로 수렴하고 실패하지 않는다(`meta_prompts.family_of` 의 원래 동작)."""
    c = str(call or "").strip()
    if c not in MP.CALLS:
        return {"error": "call 은 %s 중 하나입니다(순차 4콜)" % " · ".join(MP.CALLS)}
    svc = str(service or "").strip()
    model = str(client_model or "").strip()
    family = MP.family_of(model)

    # ★ meta_prompts.call_system · prompts.call_system 이 아니다(learned 병기 없음 · 규칙 2).
    system = MP.call_system(model, c, svc, learned="")
    size = len(system.encode("utf-8"))
    template = user_template(c)
    customized = bool((MP.WRAPPER_OVERRIDES.get(family) or "").strip())
    out = {
        "call": c,
        "call_order": list(MP.CALLS),
        "requires": _requires(template),
        "service": svc,
        "service_resolved": D._service_key(svc),
        "client_model": model,
        "family": family,
        "version": IMETA_VERSION,
        "fingerprint": _fingerprint(system),
        "wrapper_customized": customized,
        "output_schema": MP.CALL_SCHEMAS[c],
        "input_contract": [
            "입력 원천은 계약 3필드(displayServiceName · title · body)뿐입니다. 콜마다 그중 필요한 것만 투영합니다.",
            "③ 인텐트 콜의 '본문 도입부' 는 body 앞 %d자입니다(그보다 길면 뒤를 자릅니다)." % MP.INTENT_BODY_HEAD,
            "이미지 수를 모르면 '정보 없음' 이라고 적으세요. '0장' 으로 단정하면 형식 분류값 판정이 틀어집니다.",
            "① 리드문이 빈 문자열이면 뒤 콜(②③④)을 부르지 않는 것이 계약입니다.",
        ],
        "user_template": template,
        "differs_from_prism_run": _differences(customized),
        "bytes": size,
        "truncated": False,
    }
    if size > PROMPT_MAX_BYTES:
        # 자른 프롬프트는 주지 않는다. 반쪽 기준으로 돌린 결과는 '프리즘 기준' 이 아니면서
        # 그렇게 보이기까지 한다 · 조용한 절단이 가장 나쁜 자리다(감사 P3 의 취지).
        out["truncated"] = True
        out["error"] = ("프롬프트가 응답 상한(%d바이트)을 넘어 본문을 싣지 않았습니다. "
                        "자른 프롬프트는 프리즘 기준이 아니라서 드리지 않습니다 · 운영자에게 알려 주세요."
                        % PROMPT_MAX_BYTES)
        return out
    out["system"] = system
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 도구 2 · 결과 규칙 검증
# ══════════════════════════════════════════════════════════════════════════════
# 함께 못 쓰는 조합. **문구는 사전·계약 원문에서 그대로 가져온다** · 여기서 요약해 적으면
# 사전이 바뀌었을 때 이 표만 옛 규칙을 말하게 된다. `rule_source` 로 원문 자리를 되짚을 수
# 있게 두고, 테스트가 '원문에 이 문장이 실재하는가' 를 단언한다(사전이 바뀌면 테스트가 깨진다).
#   kind=violation : 사전이 '배타' · '같이 부여하지 않는다' 로 못 박은 쌍
#   kind=check     : '같은 근거로 중복 부여하지 않는다' · 근거가 각각 명확하면 병기가 허용된다.
#                    우리는 근거를 볼 수 없으므로 판단하지 않고 자리만 알린다.
PAIR_RULES = (
    ("포토·영상 중심", "그래픽·인포그래픽", "violation", ("CALL_RULES", "intent"),
     "'그래픽·인포그래픽'·(콘텐츠뷰)'카드뉴스·인포그래픽'과는 배타로 본다"),
    ("포토·영상 중심", "카드뉴스·인포그래픽", "violation", ("CALL_RULES", "intent"),
     "'그래픽·인포그래픽'·(콘텐츠뷰)'카드뉴스·인포그래픽'과는 배타로 본다"),
    ("실용 정보", "생활·실용정보", "violation", ("INTENT_VALUE_DEFS", "실용 정보"),
     "콘텐츠뷰의 '생활·실용정보'와 같이 부여하지 않는다"),
    ("후기·리뷰·비평", "리뷰·분석", "check", ("INTENT_VALUE_DEFS", "후기·리뷰·비평"),
     "체험 없는 정보·스펙 분석은 티스토리 '리뷰·분석' · 같은 근거로 두 값을 중복 부여하지 않음"),
    ("정보 공유", "가이드·튜토리얼", "check", ("INTENT_VALUE_DEFS", "정보 공유"),
     "같은 근거로 셋을 중복 부여하지 않는다"),
    ("정보 공유", "실용 정보", "check", ("INTENT_VALUE_DEFS", "정보 공유"),
     "같은 근거로 셋을 중복 부여하지 않는다"),
)

# 나머지 규칙 문구도 같은 원칙이다 · 계약 원문에서 그대로 따온 조각만 쓴다(요약·의역 금지).
FIELD_RULE = "출력 필드 4종은 다운스트림 계약 · 임의 추가·변경 금지"
EMPTY_SUMMARY_RULE = '빈 문자열("")을 출력한다(하위 호출 생략 신호)'
INTENT_ONLY_RULE = "아래 목록에 정의된 분류값 중에서만 선택한다. 자유 생성·임의 변형 금지."
CATEGORY_ONLY_RULE = "아래 사전에 정의된 항목(21개 Tier 1 + Custom Tier 2) 중에서만 선택한다."
CATEGORY_MIN_RULE = "콘텐츠에 실재하는 도메인을 N개(1개 이상) 부여한다"
CATEGORY_TIER_RULE = "Tier 1 / Tier 2 까지만 표기. 자유 생성·Tier 3 표기 금지."

# (응답에 싣는 문구, 원문이 있는 자리). 테스트가 전건을 원문과 대조한다 · 사전·계약이 바뀌면
# 여기 문구가 유령이 되는데, 그건 파트너에게 '없는 규칙' 을 규칙이라고 말하는 것이다.
RULE_TEXTS = (
    (FIELD_RULE, ("MODULE_DOC", "")),
    (EMPTY_SUMMARY_RULE, ("CALL_RULES", "summary")),
    (INTENT_ONLY_RULE, ("CALL_RULES", "intent")),
    (CATEGORY_ONLY_RULE, ("CALL_RULES", "category")),
    (CATEGORY_MIN_RULE, ("CALL_RULES", "category")),
    (CATEGORY_TIER_RULE, ("CALL_RULES", "category")),
) + tuple((rule, src) for _a, _b, _k, src, rule in PAIR_RULES)


def rule_source(src) -> str:
    """규칙 문구의 근거 원문. 표의 문구가 원문에 실재하는지 대조하는 데 쓴다."""
    kind, key = src
    if kind == "CALL_RULES":
        return MP.CALL_RULES.get(key, "")
    if kind == "CALL_SCHEMAS":
        return MP.CALL_SCHEMAS.get(key, "")
    if kind == "MODULE_DOC":
        return MP.__doc__ or ""
    return D.INTENT_VALUE_DEFS.get(key, "")


def _issue(code: str, kind: str, field: str, value, message: str, rule: str = "", fix: str = "") -> dict:
    it = {"code": code, "kind": kind, "field": field, "value": value, "message": message}
    if rule:
        it["rule"] = rule
    if fix:
        it["fix"] = fix
    return it


def _known_intents() -> tuple:
    """(서비스 후보 밖까지 포함한 전 사전값, {값: [소유 서비스…]}).

    사전은 런타임 오버라이드(dictionaries.apply_profile)로 바뀔 수 있어 import 시점에 굳히지
    않는다 · 굳히면 프로필을 적용한 팀에서 멀쩡한 값이 '모르는 값' 으로 나간다."""
    owners: dict = {}
    for svc, vals in (D.INTENT_CATEGORIES_BY_SERVICE or {}).items():
        for v in vals:
            owners.setdefault(v, []).append(svc)
    known = (set(D.INTENT_CATEGORIES_UNIVERSAL) | set(D.INTENT_FORM_UNIVERSAL)
             | set(owners) | set(D.INTENT_VALUE_DEFS))
    return known, owners


def _check_intent(values: list, service: str) -> list:
    pool = set(D.intent_categories_for(service))
    known, owners = _known_intents()
    svc_key = D._service_key(service)
    out = []
    for v in values:
        if v in pool:
            continue
        if v in known:
            own = owners.get(v) or []
            where = (" · 이 값은 %s 서비스 후보입니다" % "·".join(own)) if own else ""
            out.append(_issue(
                "intent_not_in_service", "violation", "intent", v,
                "사전에 있는 값이지만 이 서비스(%s)의 후보가 아닙니다%s"
                % (svc_key or "미정의 → 범용 분류값만", where), INTENT_ONLY_RULE))
        else:
            out.append(_issue(
                "intent_unknown", "violation", "intent", v,
                "인텐트 사전에 없는 값입니다(비슷한 값으로 갈음하지 않습니다 · get_taxonomy 로 후보를 확인하세요)",
                INTENT_ONLY_RULE))
    return out


def _cat_maps() -> tuple:
    t2_parent = {t2: t1 for t1, lst in (D.CONTENT_CATEGORY_TIER2 or {}).items() for t2 in lst}
    return set(D.IAB_TIER1 or []), t2_parent


def _check_category(values: list) -> list:
    tier1, parent = _cat_maps()
    renamed = getattr(D, "RENAMED_TIER2", {}) or {}
    out = []
    for v in values:
        parts = [p.strip() for p in str(v).split("/") if p.strip()]
        fix = D.normalize_content_category(v)
        fix = "" if fix == "Unclassified" else fix
        if len(parts) >= 3:
            out.append(_issue("category_tier3", "violation", "content_category", v,
                              "Tier 3 이하까지 표기했습니다", CATEGORY_TIER_RULE, fix))
            continue
        if len(parts) == 2:
            t1, t2 = parts
            new2 = renamed.get(t2, t2)
            if t1 in tier1 and parent.get(new2) == t1:
                if new2 != t2:
                    out.append(_issue("category_outdated", "violation", "content_category", v,
                                      "개명 전 구표기입니다(사전 최신 표기가 아닙니다)",
                                      CATEGORY_ONLY_RULE, "%s / %s" % (t1, new2)))
                continue
            if new2 in parent:
                # fix 는 정규화 결과가 아니라 **Tier 2 를 살린 정식 경로**다. 정규화는 소속이
                # 어긋나면 Tier 2 를 버리고 Tier 1 만 남기는데(normalize_content_category),
                # 그 값을 고침안이라고 주면 파트너가 판정한 세부 도메인이 조용히 사라진다.
                out.append(_issue("category_wrong_tier1", "violation", "content_category", v,
                                  "Tier 2 는 사전에 있지만 소속 Tier 1 이 다릅니다(실제 소속: %s)" % parent[new2],
                                  CATEGORY_ONLY_RULE, "%s / %s" % (parent[new2], new2)))
            else:
                out.append(_issue("category_unknown", "violation", "content_category", v,
                                  "사전에 없는 카테고리 경로입니다", CATEGORY_ONLY_RULE, fix))
            continue
        one = parts[0] if parts else ""
        new1 = renamed.get(one, one)
        if one in tier1:
            # 프리즘 적재는 Tier 1 단독도 그대로 받는다. 계약 표기는 'Tier 1 / Tier 2' 라
            # 어긋나지만 위반으로 확정할 근거가 없어 자리만 알린다(판정하지 않는다).
            out.append(_issue("category_tier1_only", "check", "content_category", v,
                              "Tier 2 없이 Tier 1 만 있습니다. 계약 표기는 'Tier 1 / Tier 2' 입니다"
                              "(프리즘 적재는 Tier 1 단독도 받습니다)",
                              MP.CALL_SCHEMAS["category"]))
        elif new1 in parent:
            out.append(_issue("category_missing_tier1", "violation", "content_category", v,
                              "Tier 1 없이 Tier 2 만 있습니다", MP.CALL_SCHEMAS["category"],
                              "%s / %s" % (parent[new1], new1)))
        else:
            out.append(_issue("category_unknown", "violation", "content_category", v,
                              "사전에 없는 카테고리 값입니다", CATEGORY_ONLY_RULE, fix))
    return out


def _check_pairs(values: list) -> list:
    have = set(values)
    out = []
    for a, b, kind, src, rule in PAIR_RULES:
        if a in have and b in have:
            msg = ("정의상 함께 부여할 수 없는 쌍입니다" if kind == "violation" else
                   "같은 근거로는 함께 부여할 수 없는 쌍입니다 · 근거가 각각 명확하면 병기가 허용되므로 "
                   "여기서는 판단하지 않고 자리만 알립니다")
            out.append(_issue("pair_" + ("exclusive" if kind == "violation" else "same_basis"),
                              kind, "intent", [a, b], msg, rule))
    return out


def _schema_of(field: str) -> str:
    """필드 → 그 필드를 내는 콜의 출력 스키마 원문(계약 그대로 인용하기 위한 매핑)."""
    return MP.CALL_SCHEMAS["category" if field == "content_category" else field]


def _as_list(field: str, raw) -> tuple:
    """(문자열 목록, 형식 위반). 배열이 아니거나 원소가 문자열이 아니면 값 검사로 넘기지 않는다."""
    if isinstance(raw, str):
        return [], [_issue("wrong_type", "violation", field, raw,
                           "배열이어야 하는데 문자열입니다", _schema_of(field))]
    if not isinstance(raw, list):
        return [], [_issue("wrong_type", "violation", field, raw,
                           "배열이어야 합니다(받은 형식: %s)" % type(raw).__name__)]
    vals, bad = [], []
    for x in raw:
        (vals if isinstance(x, str) else bad).append(x)
    out = [_issue("wrong_type", "violation", field, x, "배열 원소는 문자열이어야 합니다") for x in bad]
    seen = set()
    for v in vals:
        if v in seen:
            out.append(_issue("duplicate", "violation", field, v, "같은 값이 두 번 들어 있습니다"))
        seen.add(v)
    return vals, out


def validate_result(result=None, service: str = "", team=None) -> dict:
    """클라이언트가 만든 메타를 **프리즘 규칙으로** 검사한다. 판정하지 않는다.

    보는 것: 허용값 밖의 값 · 그 서비스에 없는 값 · 형식 오류 · 정의상 함께 못 쓰는 조합 ·
    계약에 적힌 수량·표기 규칙. 전부 사전과 계약 원문에서 기계적으로 갈리는 것뿐이다.

    보지 않는 것: 값이 콘텐츠에 맞는지. 우리는 그 콘텐츠를 읽지 않았고 추출을 돌리지도 않았다.
    그 자리에 등급·점수를 붙이면 근거 없는 판정이 된다 · 응답 어디에도 등급·점수 키가 없다."""
    if isinstance(result, str):                  # 문자열로 싸서 보내는 클라이언트 흡수
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            return {"error": "result 를 JSON 으로 읽지 못했습니다 · 메타 JSON 객체를 그대로 넣어 주세요"}
    if not isinstance(result, dict):
        return {"error": "result 는 메타 JSON 객체여야 합니다(키: %s)" % " · ".join(CONTRACT_FIELDS)}
    svc = str(service or "").strip()

    items, checked = [], []
    for k in result:
        if k not in CONTRACT_FIELDS:
            items.append(_issue("unknown_field", "violation", k, k,
                                "계약에 없는 출력 필드입니다(4필드 고정)", FIELD_RULE))

    if "summary" in result:
        checked.append("summary")
        s = result.get("summary")
        if not isinstance(s, str):
            items.append(_issue("wrong_type", "violation", "summary", s,
                                "문자열이어야 합니다(받은 형식: %s)" % type(s).__name__,
                                MP.CALL_SCHEMAS["summary"]))
        elif not s.strip() and any(result.get(f) for f in CONTRACT_FIELDS if f != "summary"):
            items.append(_issue("summary_empty_but_others_filled", "violation", "summary", s,
                                "리드문이 빈 문자열인데 뒤 콜의 출력이 채워져 있습니다"
                                "(빈 리드문은 하위 호출 생략 신호입니다)", EMPTY_SUMMARY_RULE))

    if "entities" in result:
        checked.append("entities")
        _, bad = _as_list("entities", result.get("entities"))
        items += bad

    if "intent" in result:
        checked.append("intent")
        vals, bad = _as_list("intent", result.get("intent"))
        items += bad + _check_intent(vals, svc) + _check_pairs(vals)

    if "content_category" in result:
        checked.append("content_category")
        vals, bad = _as_list("content_category", result.get("content_category"))
        items += bad + _check_category(vals)
        if not bad and not vals:
            items.append(_issue("category_empty", "violation", "content_category", vals,
                                "최소 1개(대표 도메인)는 부여해야 합니다", CATEGORY_MIN_RULE))

    from . import prismtools as PT               # 봉투 규칙 단일 원천(순환 회피: 함수 안 import)
    return PT.envelope(items, ISSUE_LIMIT, service=svc, service_resolved=D._service_key(svc),
                       checked_fields=checked, version=IMETA_VERSION,
                       note=NOT_A_VERDICT, kinds=CHECK_MEANING)
