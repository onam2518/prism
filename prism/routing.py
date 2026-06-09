"""Dispatcher: 서비스 그룹 / 콘텐츠 트랙 / 활성 메타 세트 결정."""
from .schema import Content, Routing
from . import dictionaries as D


def dispatch(content: Content) -> Routing:
    name = (content.displayServiceName or "").strip()

    # 서비스 그룹: 정확 매칭 → 부분 매칭 → 보수적 default(media)
    group = D.SERVICE_GROUP.get(name)
    if group is None:
        for k, v in D.SERVICE_GROUP.items():
            if k and k in name:
                group = v
                break
    if group is None:
        group = D.SERVICE_GROUP_DEFAULT

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
