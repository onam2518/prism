"""Pure Spectrum runtime primitives adapted for Prism's compare_report store.

Source: spectrum/runtime.py, https://github.com/onam2518/spectrum-development/pull/1,
head 5c847b39608142a0a7c4160ac6698a0a18c5d94a,
merge c37c88e3c3a4937040e052886097ff586d95e1ab, verified 2026-10-28.
The original local working copy was byte-identical to this merged PR source.
Source SHA256: ea3cf4c287c08740916a6ef69679c33e6679f692ede9734c4423398e4841b85d.
_json, _hash, _clean and _usage below are preserved from that source. Admission
and usage_summary adapt Runtime._admit/summary to persisted JSON attempts. Prism
owns authorization, storage, provider clients, leases and scoring; this module
has no imports from an external checkout and performs no I/O or global patches.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

SOURCE_REVISION = '5c847b39608142a0a7c4160ac6698a0a18c5d94a'
SOURCE_SHA256 = 'ea3cf4c287c08740916a6ef69679c33e6679f692ede9734c4423398e4841b85d'


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _clean(value: Any) -> None:
    # Do not accept common credential fields; commands inherit credentials via the environment.
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower().replace('-', '_')
            if name in ('api_key', 'secret_key', 'password', 'access_token', 'authorization'):
                raise ValueError('credentials must not be supplied in job data')
            _clean(item)
    elif isinstance(value, list):
        for item in value:
            _clean(item)


def _usage(value: Any, seconds: float, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError('usage must be an object')
    _clean(value)
    result = dict(value)
    for name in ('tokens', 'cost_usd'):
        v = result.get(name)
        if v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0 or
                              (name == 'tokens' and type(v) is not int)):
            raise ValueError('invalid ' + name)
        result[name] = v
    result['seconds'] = seconds
    reported_source = result.get('source', 'reported')
    if source == 'reported' and reported_source not in ('reported', 'estimated', 'unknown'):
        raise ValueError('invalid usage source')
    result['source'] = reported_source if source == 'reported' else source
    _json(result)
    return result


class RuntimeStopped(RuntimeError):
    pass


def validate_limits(limits):
    """Only enforceable limits are accepted; a document is not a provider call."""
    limits = dict(limits or {})
    if set(limits) - {'max_calls'}:
        raise ValueError('지원하지 않는 한도입니다. max_calls를 사용하세요')
    if 'max_calls' in limits and (type(limits['max_calls']) is not int or not 1 <= limits['max_calls'] <= 100000):
        raise ValueError('max_calls는 1~100000 사이의 정수여야 합니다')
    return limits


def admit(attempts, limits):
    """Adapted from Runtime._admit: every retry reserves an actual call first."""
    if 'max_calls' in limits and len(attempts) >= limits['max_calls']:
        raise RuntimeStopped('call budget exhausted')


def job_identity(operation, payload, executor):
    return _hash({'operation': operation, 'payload_hash': _hash(payload), 'executor_hash': _hash(executor)})


def usage_summary(attempts):
    """Adapt Runtime.summary without recovery or writes on the read path."""
    values = [(a.get('usage') or {}).get('cost_usd') for a in attempts]
    known = [v for v in values if v is not None]
    unknown = len(values) - len(known)
    return {'status': 'partial' if unknown and known else 'unknown' if unknown or not values else 'known',
            'known_usd': round(sum(known), 8) if known else None,
            'total_usd': round(sum(known), 8) if values and not unknown else None,
            'calls': len(attempts), 'unknown_calls': unknown,
            'coverage': {'model_calls': 'recorded', 'embedding_calls': 'not_requested'},
            'sources': sorted({(a.get('usage') or {}).get('source', 'unknown') for a in attempts})}
