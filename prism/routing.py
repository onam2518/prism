"""Dispatcher: 서비스 그룹 / 콘텐츠 트랙 / 활성 메타 세트 결정."""
from .schema import Content, Routing
from . import dictionaries as D


def dispatch(content: Content) -> Routing:
    name = (content.displayServiceName or "").strip()

    # 서비스 그룹: 정의값·구 명칭·표기 변형 흡수는 사전 한 곳(service_group_of)에서 한다.
    # 종전의 SERVICE_GROUP 직접 매칭은 원문("다음카페"·"티스토리"·"VOD")을 못 잡아
    # UGC 서비스가 전부 media 로 떨어졌다(위키 278036632).
    group = D.service_group_of(name)

    # 콘텐츠 트랙: 텍스트(제목/부제/본문)가 하나라도 있으면 text.
    # image_only 는 텍스트가 전혀 없는 '이미지 단독'만(분류기 호출 전 분기: v28).
    # 제목만 있는 건도 text 로 처리(제목이 분류 신호를 담음).
    has_text = any((getattr(content, f) or "").strip()
                   for f in ("title", "subtitle", "body"))
    track = "text" if has_text else "image_only"

    return Routing(
        service_group=group,
        content_track=track,
        active_quality_metas=D.active_quality_metas(group),
    )
