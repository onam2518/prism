"""퀘스트 독려 봇 · 진척도 낮은 검수자에게 슬랙 DM.

검수 목표(퀘스트)가 진행 중일 때, **진척도가 팀 평균 미만**인 검수자를 골라
슬랙 DM 으로 독려 메시지를 보낸다. 의존성 0(stdlib urllib)·dual-mode 스토어 재사용.

식별자 매핑(이메일 자동조회):
  프리즘 검수자(auth uuid) → 이메일(Supabase Auth Admin API)
                          → 슬랙 user id(Slack users.lookupByEmail) → DM.

필요한 것:
  - 스토어(운영과 동일): PRISM_BACKEND=supabase · SUPABASE_URL · SUPABASE_SERVICE_KEY.
    (이메일 자동조회가 Supabase Auth 를 쓰므로 supabase 백엔드가 전제. sqlite 로컬은
     이메일 원천이 없어 --members 매핑 파일로 우회해야 한다.)
  - 슬랙 봇 토큰: env SLACK_BOT_TOKEN 또는 ~/.prism_slack_bot_token (xoxb-…).
    필요 스코프: chat:write, users:read.email
  - (선택) 수동 매핑: ~/.prism_slack_members (JSON) — {"<uuid 또는 이름 또는 이메일>": "U0…"}.
    자동조회 실패 시 폴백. 이메일이 없는 로컬 sqlite 에선 이 파일이 유일 경로.

실행:
  python3 -m prism.questbot --team <team_id> [--dry-run]
  # 기준 조정: --threshold-ratio 1.0 (팀 평균×비율 미만이 대상 · 1.0=평균 미만)
  # 재발송 허용: --force (기본은 같은 퀘스트에 1인 1회)

크론(예 · 매일 오전 10시 KST, Fly 머신 내부):
  0 1 * * *  cd /app && SLACK_BOT_TOKEN=xoxb-… python3 -m prism.questbot --team <id>
  (UTC 기준 01:00 = KST 10:00. 슬랙/스토어 env 는 머신 시크릿으로 주입)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

APP_URL = "https://prism-item.fly.dev"
SLACK_API = "https://slack.com/api/"
_BOT_TOKEN_FILE = os.path.expanduser("~/.prism_slack_bot_token")
_MEMBERS_FILE = os.path.expanduser("~/.prism_slack_members")


# ── 슬랙 ─────────────────────────────────────────────────────────────────────
def bot_token() -> str:
    """봇 토큰 조회: env SLACK_BOT_TOKEN 우선, 없으면 ~/.prism_slack_bot_token."""
    tok = (os.environ.get("SLACK_BOT_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        with open(_BOT_TOKEN_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _slack(method: str, token: str, payload: dict) -> dict:
    """Slack Web API 호출(POST JSON). 응답 dict 반환(ok 포함). 네트워크 오류도 dict 로."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(SLACK_API + method, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP{e.code}"}
    except Exception as e:                                # noqa: BLE001 (경계: 발송 실패는 흐름 유지)
        return {"ok": False, "error": str(e)[:200]}


def lookup_slack_id(token: str, email: str) -> str:
    """이메일 → 슬랙 user id. 실패(미가입·권한 없음)면 빈 문자열."""
    if not email:
        return ""
    r = _slack("users.lookupByEmail", token, {"email": email})
    if r.get("ok"):
        return ((r.get("user") or {}).get("id")) or ""
    return ""


def send_dm(token: str, user_id: str, text: str, blocks=None) -> dict:
    """검수자에게 DM 발송. channel 에 user id 를 주면 슬랙이 DM 채널을 자동 개설한다."""
    payload = {"channel": user_id, "text": text}
    if blocks:
        payload["blocks"] = blocks
    return _slack("chat.postMessage", token, payload)


# ── 이메일 원천(Supabase Auth Admin API) ─────────────────────────────────────
def auth_emails() -> dict:
    """검수자 uuid → 이메일. Supabase Auth Admin API 를 서비스 키로 조회(운영 env 재사용).
    supabase 미설정이면 빈 dict(호출측이 매핑 파일로 폴백)."""
    base = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    if not (base and key):
        return {}
    out = {}
    page = 1
    while True:
        url = f"{base}/auth/v1/admin/users?page={page}&per_page=200"
        req = urllib.request.Request(url, method="GET")
        req.add_header("apikey", key)
        req.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:                                # noqa: BLE001
            break
        users = data.get("users") if isinstance(data, dict) else data
        if not users:
            break
        for u in users:
            uid, email = u.get("id"), u.get("email")
            if uid and email:
                out[uid] = email
        if len(users) < 200:                             # 마지막 페이지
            break
        page += 1
        if page > 50:                                    # 안전 상한(1만 명)
            break
    return out


def _members_map() -> dict:
    """수동 매핑 파일(폴백): {uuid|이름|이메일: 슬랙 user id}. 없으면 빈 dict."""
    try:
        with open(_MEMBERS_FILE, encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


# ── 진척도 계산(serve 의 퀘스트 창 로직과 동일 관점) ─────────────────────────
def _fb_epoch(ts) -> float:
    """feedback ts → epoch. sqlite=float · supabase=timestamptz 문자열."""
    try:
        return float(ts)
    except (TypeError, ValueError):
        try:
            return time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
        except (TypeError, ValueError):
            return 0.0


def compute_progress(store, team=None, learn_next_at: str = "") -> dict:
    """검수자별 퀘스트 진척(유효 검수 건수)·팀 평균을 계산.

    유효 검수 = '그 콘텐츠의 현재 초안 생성 이후'의 표(퀘스트 창). serve.py 홈 히어로의
    팀 진척율과 같은 기준. 반환:
      {active, next_at, per_done:{uid:int}, names:{uid:name}, avg:float, members:int}
    """
    from . import learnops as LO
    next_at = LO.next_batch_time(learn_next_at)
    active = next_at > time.time()

    names = store.reviewers_map(team) if hasattr(store, "reviewers_map") else {}
    name_of = {uid: (m.get("name") or uid) for uid, m in names.items()}

    try:
        dts = store.draft_times(team) if hasattr(store, "draft_times") else {}
    except Exception:                                    # noqa: BLE001
        dts = {}
    fm = store.feedback_map(team=team) if hasattr(store, "feedback_map") else {}

    per = {}                                             # 검수자 uid → 유효 검수한 콘텐츠 집합
    for ch, e in fm.items():
        base = float(dts.get(ch) or 0)
        for v in e.get("verdicts") or []:
            if _fb_epoch(v.get("ts")) >= base:
                rid = v.get("reviewer_id") or v.get("reviewer") or ""
                if rid:
                    per.setdefault(rid, set()).add(ch)

    # 로스터 = 팀원 전원 ∪ 검수 기록 보유자(검수 0건 팀원도 평균·대상에 포함)
    roster = set(name_of) | set(per)
    per_done = {uid: len(per.get(uid, ())) for uid in roster}
    members = len(roster)
    total = sum(per_done.values())
    avg = (total / members) if members else 0.0
    return {"active": active, "next_at": next_at, "per_done": per_done,
            "names": name_of, "avg": avg, "members": members}


def select_laggards(progress: dict, ratio: float = 1.0) -> list:
    """진척도가 (팀 평균 × ratio) 미만인 검수자. done 오름차순(가장 뒤처진 순).
    ratio=1.0 → 평균 미만 전원. 평균이 0(아무도 시작 안 함)이면 대상 없음."""
    avg = progress.get("avg") or 0.0
    if avg <= 0:
        return []
    cut = avg * ratio
    out = [{"uid": uid, "done": done, "name": progress["names"].get(uid, uid)}
           for uid, done in progress["per_done"].items() if done < cut]
    out.sort(key=lambda r: (r["done"], r["name"]))
    return out


# ── 메시지 ───────────────────────────────────────────────────────────────────
def _fmt_deadline(next_at: float) -> str:
    if not next_at:
        return ""
    return time.strftime("%m/%d %H:%M", time.localtime(next_at))


def compose(name: str, done: int, avg: float, next_at: float) -> tuple:
    """(text, blocks) 한국어 독려 메시지. 해요체·비난 없이 넛지."""
    avg_i = round(avg)
    dl = _fmt_deadline(next_at)
    gap = max(0, avg_i - done)
    lead = f"{name} 님, 검수 퀘스트가 진행 중이에요. 🔭"
    if done == 0:
        line = "아직 이번 퀘스트 검수를 시작하지 않으셨어요. 한 건만 검수해도 팀 진척에 큰 힘이 돼요."
    else:
        line = (f"지금까지 {done}건 검수하셨어요. 팀 평균은 {avg_i}건이라, "
                f"{gap}건만 더 하면 평균을 따라잡아요.")
    tail = f"마감은 {dl} 이에요. 지금 이어서 검수해요." if dl else "틈날 때 이어서 검수해요."
    text = f"{lead}\n{line}\n{tail}"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{lead}*\n{line}\n{tail}"}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "지금 검수하러 가기"},
             "url": APP_URL, "style": "primary"}]},
    ]
    return text, blocks


# ── 재발송 방지(1인 1회 · 퀘스트 단위) ───────────────────────────────────────
_SENT_KIND = "questbot_sent"


def _sent_state(store, team, next_at: float) -> dict:
    """이번 퀘스트(next_at)에 이미 DM 보낸 uid 집합 상태. 다른 퀘스트 기록은 무시."""
    rec = None
    try:
        if hasattr(store, "get_report"):
            rec = store.get_report(_SENT_KIND, team=team)
    except Exception:                                    # noqa: BLE001
        rec = None
    if not isinstance(rec, dict) or float(rec.get("next_at") or 0) != float(next_at):
        return {"next_at": next_at, "sent": {}}
    rec.setdefault("sent", {})
    return rec


def _persist_sent(store, team, state: dict):
    try:
        if hasattr(store, "save_report"):
            store.save_report(_SENT_KIND, state, team=team)
    except Exception:                                    # noqa: BLE001
        pass


# ── 오케스트레이션 ───────────────────────────────────────────────────────────
def run(store, team=None, *, learn_next_at: str = "", dry_run: bool = False,
        ratio: float = 1.0, force: bool = False, token: str = "",
        log=print) -> dict:
    """퀘스트 독려 1회 실행. 반환: 요약 dict(sent/skipped/targets 등).

    store: dual-mode 스토어 인스턴스. learn_next_at: Config.learn_next_at('YYYY-MM-DDTHH:MM').
    """
    prog = compute_progress(store, team, learn_next_at)
    if not prog["active"]:
        log("퀘스트가 진행 중이 아니에요(검수 목표 일시 미설정 또는 이미 지남). 발송하지 않아요.")
        return {"active": False, "targets": 0, "sent": 0, "skipped": 0, "results": []}

    laggards = select_laggards(prog, ratio)
    log(f"퀘스트 진행 중 · 팀 평균 {prog['avg']:.1f}건 · 로스터 {prog['members']}명 "
        f"· 평균 미만 대상 {len(laggards)}명 (기준 ×{ratio}).")
    if not laggards:
        return {"active": True, "targets": 0, "sent": 0, "skipped": 0, "avg": prog["avg"], "results": []}

    emails = auth_emails()                               # uuid → email
    manual = _members_map()                              # uuid|이름|email → slack id (폴백)

    def resolve_slack(uid: str, name: str) -> tuple:
        """(slack_id, how) 해석. 자동조회 우선, 실패 시 수동 매핑."""
        email = emails.get(uid, "")
        if not dry_run and token and email:
            sid = lookup_slack_id(token, email)
            if sid:
                return sid, f"email:{email}"
        for key in (uid, name, email):
            if key and key in manual:
                return manual[key], "manual"
        if dry_run and email:                            # dry-run: 실제 조회 없이 이메일만 표시
            return "", f"email?:{email}"
        return "", ("email?:" + email if email else "미해결")

    state = _sent_state(store, team, prog["next_at"])
    results = []
    sent = skipped = 0
    for lg in laggards:
        uid, name, done = lg["uid"], lg["name"], lg["done"]
        already = uid in state["sent"]
        if already and not force:
            results.append({"uid": uid, "name": name, "done": done, "status": "skip:already"})
            skipped += 1
            continue
        sid, how = resolve_slack(uid, name)
        text, blocks = compose(name, done, prog["avg"], prog["next_at"])
        if dry_run:
            results.append({"uid": uid, "name": name, "done": done,
                            "status": "dry", "slack": sid or how, "text": text})
            log(f"  [dry] {name}({done}건) → {sid or how}")
            continue
        if not sid:
            results.append({"uid": uid, "name": name, "done": done, "status": f"skip:{how}"})
            log(f"  [skip] {name}: 슬랙 사용자 미해결({how})")
            skipped += 1
            continue
        r = send_dm(token, sid, text, blocks)
        if r.get("ok"):
            state["sent"][uid] = time.time()
            sent += 1
            results.append({"uid": uid, "name": name, "done": done, "status": "sent", "slack": sid})
            log(f"  [sent] {name}({done}건) → {sid}")
        else:
            results.append({"uid": uid, "name": name, "done": done,
                            "status": "fail", "error": r.get("error")})
            log(f"  [fail] {name}: {r.get('error')}")
    if sent and not dry_run:
        _persist_sent(store, team, state)
    return {"active": True, "targets": len(laggards), "sent": sent, "skipped": skipped,
            "avg": prog["avg"], "results": results}


# ── CLI ──────────────────────────────────────────────────────────────────────
def _load_store():
    """운영과 동일 dual-mode 스토어 인스턴스. 실패 시 None."""
    backend = (os.environ.get("PRISM_BACKEND") or "").lower()
    try:
        if backend == "supabase":
            from . import supastore
            return supastore.SupabaseStore()
        from .store import Store
        return Store(os.environ.get("PRISM_DB") or os.path.expanduser("~/.prism/prism.db"))
    except Exception as e:                               # noqa: BLE001
        print(f"[error] 스토어 초기화 실패: {e}", file=sys.stderr)
        return None


def _learn_next_at() -> str:
    try:
        from .config import Config
        return getattr(Config.load(), "learn_next_at", "") or ""
    except Exception:                                    # noqa: BLE001
        return os.environ.get("PRISM_LEARN_NEXT_AT", "")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="퀘스트 진척도 낮은 검수자 슬랙 DM 독려 봇")
    p.add_argument("--team", default=os.environ.get("PRISM_TEAM") or None,
                   help="팀 id(멀티테넌시). 미지정 시 전역")
    p.add_argument("--dry-run", action="store_true", help="발송 없이 대상·메시지만 출력")
    p.add_argument("--threshold-ratio", type=float, default=1.0,
                   help="팀 평균×비율 미만이 대상(기본 1.0=평균 미만)")
    p.add_argument("--force", action="store_true", help="같은 퀘스트 재발송 허용(1인 1회 무시)")
    p.add_argument("--learn-next-at", default="",
                   help="검수 목표 일시 override(YYYY-MM-DDTHH:MM). 기본은 Config 에서 로드")
    args = p.parse_args(argv)

    store = _load_store()
    if store is None:
        return 2
    token = bot_token()
    if not token and not args.dry_run:
        print("[error] 슬랙 봇 토큰이 없어요. env SLACK_BOT_TOKEN 또는 ~/.prism_slack_bot_token "
              "(xoxb-…, 스코프 chat:write·users:read.email) 를 설정하세요.", file=sys.stderr)
        return 2

    summary = run(store, team=args.team,
                  learn_next_at=args.learn_next_at or _learn_next_at(),
                  dry_run=args.dry_run, ratio=args.threshold_ratio,
                  force=args.force, token=token)
    print(f"\n요약: 대상 {summary['targets']}명 · 발송 {summary['sent']} · "
          f"건너뜀 {summary['skipped']}"
          + ("" if summary["active"] else " · (퀘스트 비활성)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
