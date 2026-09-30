"""토픽 쓰기 테스트도 실제 미리보기 승인 계약을 거친다."""
def approved_action(data, mock=False, team=None, who=''):
    from prism import serve as S
    if data.get('action') in ('preview', 'suggest'):
        return S.topic_studio_action(data, mock=mock, team=team, who=who)
    old = S.get_store().get_report('topic_studio') or {}
    request = dict(data, expected_revision=int(old.get('revision') or 0))
    preview = S.topic_studio_action({'action': 'preview_action', 'request': request},
                                    mock=mock, team=team, who=who)
    if not preview.get('ok'):
        return preview
    return S.topic_studio_action(dict(request, preview_token=preview['preview_token']),
                                 mock=mock, team=team, who=who)
