"""콜별 user 메시지 자리표 · 학습 핸드오프 번들(`learnops`)이 '모델이 받은 입력 계약' 을 보여줄 때 쓴다.

입력 계약을 손으로 다시 적지 않고 실제 조립 함수(`meta_prompts.call_user` · `prompts.quality_user`)를
자리표 콘텐츠로 돌려 뽑는다. (외부 파트너용 프롬프트 배포·결과 검증 도구는 MCP 와 함께 2026-10-09 제거)
"""
from __future__ import annotations

from . import meta_prompts as MP

CALLS = MP.CALLS                        # 독립 4콜(재수출)

# 자리표 렌더용(입력 계약을 보여줄 때만 쓴다 · 실제 추출 입력이 아니다)
_PH_BODY = "{body}"
_PH_LEN = "본문 글자수: {본문 글자수}"
_PH_IMG = "이미지 수: {이미지 수 · 모르면 '정보 없음' 이라고 쓴다 · '0장' 과 다르다}"


class _Slots:
    """`meta_prompts.call_user`(메타 4콜)와 `prompts.quality_user`(품질 · learnops 내려받기)가
    읽는 필드만 가진 자리표 콘텐츠. 메타 4콜은 2026-09-22 부터 서비스명을 읽지 않지만 품질
    판정은 계속 읽으므로 displayServiceName 자리는 남긴다.

    입력 계약(어느 콜에 무엇을 넣는가)을 손으로 다시 적지 않기 위해 실제 조립 함수를 그대로
    돌린다 · ③ 인텐트 콜의 투영은 2026-08-03 에 한 번 바뀌었고 또 바뀔 수 있다. 여기서 베껴
    적으면 그때 이 응답만 옛 계약을 말하게 된다."""
    displayServiceName = "{displayServiceName}"   # 품질 콜 전용 · 메타 4콜은 읽지 않는다
    title = "{title}"
    body = _PH_BODY
    image_urls = None                    # 목록이 아니면 '정보 없음' 줄이 나온다(_intent_image_line)


def user_template(call: str) -> str:
    """콜별 user 메시지 자리표. 파생값(글자수·이미지 수)만 자리표로 되돌린다.

    글자수는 자리표 문자열의 길이가 그대로 찍히므로(예 '본문 글자수: 6') 반드시 되돌려야 한다.
    라벨이 바뀌면 이 치환이 조용히 no-op 이 되어 **엉뚱한 상수**가 핸드오프 번들에 나간다.
    tests/test_promptdist.py 가 자리표 존재와 상수 부재를 함께 단언한다(지우지 말 것)."""
    txt = MP.call_user(call, _Slots())
    txt = txt.replace("본문 글자수: %d" % len(_PH_BODY), _PH_LEN)
    return txt.replace(MP._IMG_UNKNOWN, _PH_IMG)
