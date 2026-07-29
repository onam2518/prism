"""엔티티 관련성 라벨 · 확신도 가중치 최적화의 정답을 모은다.

왜 필요한가(2026-07-29 실측): 엔티티 확신도 가중치를 어떻게 조합해도 판별력이
AUC 0.61 로 평평했다. 조합을 바꾸면 0.5 선의 위치만 움직일 뿐 순서가 안 바뀐다.
최적화가 의미를 가지려면 **'이 엔티티가 이 콘텐츠와 관련 있나'** 의 정답이 있어야
하는데, 지금 있는 신호는 둘 다 못 쓴다.

  · 검수 교정 기록(patch_log): 5건뿐이고 **전부 추가** — 삭제 사례가 0 이다.
  · 엔티티 사전 상태(active/unlisted): '실존 개체인가'를 재는 것이지
    '이 기사에서 중요한가'가 아니다. 대리 정답으로 썼더니 AUC 0.61.

그래서 라벨을 직접 받는다. **콘텐츠를 바꾸지 않는 별도 원장**이라는 점이 핵심이다 —
엔티티 삭제는 정답셋에 반영되는 무거운 결정이라 검수자가 잘 누르지 않지만, 라벨은
"관련 있나요"에 답만 하는 것이라 부담이 없다.

수집 지점: 상세 화면의 '연관 낮음' 영역. 거기 접혀 있는 게 정확히 경계선 근처
표본이라 판별력을 올리는 데 가장 값어치가 크다(능동학습에서 불확실 구간 우선 라벨링).

저장: reports kind(팀 스코프 · DDL 불필요). 한 표본당 검수자별 1표라
같은 사람이 다시 눌러도 뒤집히기만 하고 중복되지 않는다 — 합의율을 잴 수 있다.
"""
from __future__ import annotations

import time

_SV = None                      # serve 모듈 객체(컴포지션 루트) · serve import 시 주입

LABEL_KIND = "entity_labels"
MAX_ITEMS = 4000                # 원장 blob 상한 · 넘으면 오래된 표본부터 덜어낸다
YES, NO = "y", "n"


def _key(content_hash: str, entity: str) -> str:
    return f"{(content_hash or '').strip()}|{(entity or '').strip()}"


def _load(team=None) -> dict:
    try:
        return dict((_SV._report_get(LABEL_KIND, team, {}) or {}).get("items") or {})
    except Exception:
        return {}


def put(content_hash: str, entity: str, label: str, uid: str, team=None) -> dict:
    """라벨 1개 기록. 같은 검수자가 다시 누르면 **덮어쓴다**(중복 집계 방지).

    label 이 'y'/'n' 이 아니면 그 사람의 표를 취소한다(오클릭 되돌리기)."""
    ch = (content_hash or "").strip()
    ent = (entity or "").strip()
    uid = (uid or "").strip()
    if not (ch and ent and uid):
        return {"ok": False, "error": "콘텐츠·엔티티·검수자가 있어야 합니다"}
    lab = str(label or "").strip().lower()[:1]
    items = _load(team)
    k = _key(ch, ent)
    row = dict(items.get(k) or {"by": {}, "ts": 0})
    by = dict(row.get("by") or {})
    if lab in (YES, NO):
        by[uid] = lab
    else:
        by.pop(uid, None)
    if not by:                                      # 아무 표도 안 남으면 표본 자체를 지운다
        items.pop(k, None)
    else:
        row["by"] = by
        row["ts"] = time.time()
        items[k] = row
    if len(items) > MAX_ITEMS:                      # 오래된 표본부터 정리(원장 무한 성장 방지)
        for old in sorted(items, key=lambda x: (items[x] or {}).get("ts") or 0)[:len(items) - MAX_ITEMS]:
            items.pop(old, None)
    try:
        _SV._report_save(LABEL_KIND, {"items": items}, team)
    except Exception:
        return {"ok": False, "error": "저장하지 못했습니다"}
    cur = items.get(k) or {}
    return {"ok": True, "hash": ch, "entity": ent, "mine": (cur.get("by") or {}).get(uid, ""),
            "counts": tally(cur)}


def tally(row: dict) -> dict:
    by = (row or {}).get("by") or {}
    y = sum(1 for v in by.values() if v == YES)
    return {"yes": y, "no": len(by) - y, "n": len(by)}


def for_content(content_hash: str, uid: str = "", team=None) -> dict:
    """상세 화면용: 이 콘텐츠의 엔티티별 내 표와 집계."""
    ch = (content_hash or "").strip()
    if not ch:
        return {"ok": True, "labels": {}}
    out = {}
    pre = ch + "|"
    for k, row in _load(team).items():
        if not k.startswith(pre):
            continue
        ent = k[len(pre):]
        out[ent] = {"mine": ((row or {}).get("by") or {}).get(uid, ""), **tally(row)}
    return {"ok": True, "labels": out}


def summary(team=None) -> dict:
    """수집 현황 + 합의 상태. 가중치 재최적화를 언제 시작할지 판단하는 근거."""
    items = _load(team)
    n = len(items)
    votes = sum(len((r or {}).get("by") or {}) for r in items.values())
    agreed_y = agreed_n = split = 0
    for r in items.values():
        t = tally(r)
        if t["yes"] and t["no"]:
            split += 1
        elif t["yes"]:
            agreed_y += 1
        else:
            agreed_n += 1
    return {"ok": True, "samples": n, "votes": votes, "cap": MAX_ITEMS,
            "agreed_yes": agreed_y, "agreed_no": agreed_n, "split": split,
            "reviewers": len({u for r in items.values() for u in ((r or {}).get("by") or {})})}


def export_rows(team=None) -> list:
    """최적화용 내보내기: 합의된 표본만(의견이 갈린 건 정답으로 쓸 수 없다)."""
    out = []
    for k, r in _load(team).items():
        ch, _, ent = k.partition("|")
        t = tally(r)
        if not ent or (t["yes"] and t["no"]):
            continue
        out.append({"hash": ch, "entity": ent, "label": 1 if t["yes"] else 0, "votes": t["n"]})
    return out
