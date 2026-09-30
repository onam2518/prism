"""DNM 관리·이벤트 인입·현재 발행 조회의 HTTP 도메인."""
from .dnm import Runtime, prepare_policy


def action(server, data, team, who, mock=False):
    runtime = Runtime(server.get_store(), team)
    command = data.get('action')
    try:
        if command == 'prepare_policy':
            llm, route = server.llm_for_model(str(data.get('model') or ''), mock)
            if llm is None:
                return {'ok': False, 'error': '모델 연결 실패: ' + route}
            return {'ok': True, 'policy': prepare_policy(llm)}
        if command == 'configure':
            return {'ok': True, 'control': runtime.configure(data.get('registry'), data.get('policy'), data.get('expected_revision'))}
        if command == 'approve':
            return {'ok': True, 'control': runtime.approve(data.get('evidence'), data.get('expected_revision'))}
        if command == 'event':
            result = runtime.execute(data.get('event'), lambda model: server.llm_for_model(model, mock)[0])
            return {'ok': True, 'publication': result}
        if command == 'manual':
            result = runtime.manual(data.get('item_unique_key'), data.get('field'), data.get('value'),
                                    data.get('input_revision'), data.get('policy_version'), who,
                                    automatic=data.get('automatic') is True)
            return {'ok': True, 'publication': result}
        if command == 'golden':
            row = runtime.golden(data.get('item_unique_key'))
            count = server.get_store().register_golden(team, [row], replace=False, source='dnm_review')
            return {'ok': True, 'count': count}
        if command == 'rollback':
            return {'ok': True, 'publication': runtime.rollback(data.get('item_unique_key'),
                    data.get('policy_version'), data.get('expected_publication_revision'))}
        return {'ok': False, 'error': '지원하지 않는 DNM 작업입니다'}
    except (ValueError, TypeError, KeyError) as exc:
        return {'ok': False, 'error': str(exc)[:250]}
