"""운영 알림(슬랙 Incoming Webhook) · 장애·비용을 운영자에게 push.

기존 관측(fail_rollup·cost_rollup)은 관리자가 화면을 열어야 보이는 조회형 리포트라
장애를 능동 인지할 경로가 없었다. 본 모듈은 그 원장 적재 지점에 얹혀 임계 초과 시
슬랙으로 1회성 통지를 보낸다(원장·판정 로직에는 관여하지 않음).

계약:
- PRISM_ALERT_WEBHOOK(슬랙 Incoming Webhook URL) 미설정이면 전 기능 무동작(무비용).
- 통지는 키별 쿨다운으로 **실제 발송된 것**의 중복을 막는다(스팸 방지 · 기본 1시간).
  발송이 실패하면 쿨다운을 소진하지 않는다 — 웹훅 순단 1회로 장애 통지가 몇 시간
  침묵하면 "가장 필요한 순간에 알림이 안 오는" 실패 모드가 된다(60초 뒤 재시도 가능).
- 발송 실패는 삼킨다 — 알림이 본 기능(추출·검수)을 절대 막지 않는다.
- 비용 알림의 '오늘은 이미 보냄' 판단은 **팀별**이다(비용 원장 자체가 팀 스코프).

임계(환경변수 · 미설정 시 기본값):
- PRISM_ALERT_FAIL_N  : 최근 1시간 콜 실패 누적 임계(기본 20)
- PRISM_ALERT_COST_USD: 당일 비용 임계(USD · 미설정이면 비용 알림 없음)
"""
from __future__ import annotations
import json
import os
import threading
import time
import urllib.request

_LOCK = threading.Lock()
_LAST_SENT = {}                                   # key → 마지막 발송 ts (쿨다운)
_FAILS = []                                       # 최근 실패 ts 목록(1시간 슬라이딩 윈도)
_COST_ALERTED = {}                                # 팀 → 비용 알림을 이미 보낸 날짜(팀별 일 1회)

_WINDOW = 3600


def _webhook() -> str:
    return (os.environ.get("PRISM_ALERT_WEBHOOK") or "").strip()


def enabled() -> bool:
    return bool(_webhook())


def notify(key: str, text: str, cooldown_sec: int = 3600) -> bool:
    """쿨다운 지난 키만 슬랙 발송. 반환 = 실제 발송 여부. 실패는 삼킨다.

    쿨다운은 발송 **전에 선점**한다(스레드 여러 개가 같은 통지를 동시에 쏘지 않게).
    다만 발송이 실패하면 선점을 되돌린다 — 보낸 적이 없는데 쿨다운만 소진되면
    웹훅 순단 1회로 이후 몇 시간의 통지가 통째로 사라진다. 되돌린 뒤에도 60초는
    남겨 실패가 반복될 때 재시도 폭주를 막는다."""
    url = _webhook()
    if not url or not (text or "").strip():
        return False
    now = time.time()
    cooldown = max(60, int(cooldown_sec))
    with _LOCK:
        if now - _LAST_SENT.get(key, 0) < cooldown:
            return False
        _LAST_SENT[key] = now                     # 선점(동시 중복 발송 방지)
    try:
        req = urllib.request.Request(
            url, method="POST",
            data=json.dumps({"text": f"[Prism] {text}"}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except Exception:
        with _LOCK:                               # 발송 0회 → 선점 해제(60초 뒤 재시도 가능)
            if _LAST_SENT.get(key) == now:        # 그 사이 실제로 나간 발송은 건드리지 않는다
                _LAST_SENT[key] = now - cooldown + 60
        return False


def on_fail(n: int, kind: str = "", model: str = ""):
    """콜 실패 n건 기록 · 1시간 윈도 누적이 임계 이상이면 통지(시간당 1회)."""
    if not enabled() or n <= 0:
        return
    try:
        limit = int(os.environ.get("PRISM_ALERT_FAIL_N", "20") or 20)
    except ValueError:
        limit = 20
    if limit <= 0:
        return
    now = time.time()
    with _LOCK:
        _FAILS.extend([now] * min(int(n), 1000))
        while _FAILS and now - _FAILS[0] > _WINDOW:
            _FAILS.pop(0)
        total = len(_FAILS)
    if total >= limit:
        hint = f" · 최근: {kind or '?'} @ {model or '?'}" if (kind or model) else ""
        notify("fail_spike",
               f"⚠️ LLM 콜 실패 급증 · 최근 1시간 {total}건(임계 {limit}){hint}\n"
               f"관리자 화면 > 실패 원장(fail_rollup)에서 종류별 확인 필요",
               cooldown_sec=_WINDOW)


def on_cost(day: str, day_cost_usd: float, team=None):
    """당일 누적 비용이 임계 이상이면 통지(팀별 일 1회). PRISM_ALERT_COST_USD 미설정 시 무동작.

    비용 원장(cost_rollup)이 팀 스코프라 넘어오는 누적도 팀별 값이다 — '보냈음' 표시가
    전역이면 그날 임계를 먼저 넘은 팀 하나만 알림을 받고 나머지 팀의 초과는 조용히 사라진다.
    '보냈음' 확정은 **발송이 성공한 뒤**다(실패한 통지가 그날을 소진하지 않게)."""
    if not enabled():
        return
    raw = (os.environ.get("PRISM_ALERT_COST_USD") or "").strip()
    if not raw:
        return
    try:
        limit = float(raw)
    except ValueError:
        return
    if limit <= 0 or day_cost_usd < limit:
        return
    tkey = str(team or "-")
    with _LOCK:
        if _COST_ALERTED.get(tkey) == day:
            return
    where = f" · 팀 {team}" if team else ""
    if notify(f"cost_{tkey}_{day}",
              f"💸 당일 LLM 비용 임계 초과{where} · {day} 누적 ${day_cost_usd:.2f} (임계 ${limit:.2f})\n"
              f"관리자 화면 > 비용 롤업(cost_rollup)에서 모델·콜별 확인 필요",
              cooldown_sec=6 * 3600):
        with _LOCK:
            _COST_ALERTED[tkey] = day


def on_batch_fail(err: str):
    """학습 일배치 실패 통지(6시간 쿨다운) — 조용한 학습 중단 방지."""
    if not enabled():
        return
    notify("learn_batch_fail",
           f"🛑 학습 배치 실패 · 자동 재시도 대기 중\n오류: {str(err)[:300]}",
           cooldown_sec=6 * 3600)
