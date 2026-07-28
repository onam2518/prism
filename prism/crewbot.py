"""검수운영 자동 점검(크론) · 새 사이클이면 여력만큼 나눠 맡기고, 기한이 가까우면 멈춘 일을 넘긴다.

왜 크론인가: 매주 사람이 잊지 않고 눌러야 돌아가는 운영은 결국 안 돌아간다(운영 실측
2026-07-28: 900슬롯을 한 번에 뿌린 뒤 11일간 293건이 그대로 묵었다). 화면을 열면
POST /crew-auto 로도 같은 점검이 돌지만, 아무도 안 열어도 도는 길이 하나 있어야 한다.

무엇을 하나(팀 설정 crew_settings 에서 켠 것만 · 기본은 둘 다 꺼짐):
  · auto_wave      사이클 시작(기본 월 10시)에 아직 아무도 안 맡은 것을 여력만큼 배분 + 기한 설정
  · auto_rebalance 기한 하루 전부터, 오래 멈춘 일과 자리 비운 사람 몫을 여유 있는 사람에게 이관
사이클당 1회만 실행된다(회차 키가 reports 에 남는다) — 자주 돌려도 중복되지 않는다.

필요한 것(운영과 동일): PRISM_BACKEND=supabase · SUPABASE_URL · SUPABASE_SERVICE_KEY.
로컬 sqlite 로도 돌아간다(PRISM_DB).

실행:
  python3 -m prism.crewbot --team <team_id> [--dry-run]

크론(예 · 매일 오전 10시 KST, Fly 머신 내부):
  0 1 * * *  cd /app && python3 -m prism.crewbot --team <id>
  (UTC 01:00 = KST 10:00. 매일 돌려도 사이클당 1회만 실제로 동작한다)
"""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="검수운영 자동 점검(웨이브 발행 · 멈춘 일 이관)")
    ap.add_argument("--team", default="", help="팀 id(운영 supabase) · 로컬 sqlite 는 생략")
    ap.add_argument("--dry-run", action="store_true", help="무엇을 할지만 출력하고 바꾸지 않음")
    args = ap.parse_args(argv)

    # serve 를 import 하면 crewops 에 서버 컴포지션(_SV)이 주입된다(모듈 로드만 · 서버는 안 뜬다).
    from . import serve as S

    team = args.team or None
    r = S.CRW.auto_tick(team=team, apply=not args.dry_run)
    head = "점검만(dry-run)" if args.dry_run else "실행"
    print(f"[crewbot] {head} · 사이클 {r['cycle']} · 자동배분 {'켬' if r['auto_wave'] else '끔'}"
          f" · 자동이관 {'켬' if r['auto_rebalance'] else '끔'}")
    w = r.get("wave")
    if w:
        who = " · ".join(f"{p['name']} {p['n']}건" for p in (w.get("plan") or {}).values())
        print(f"  나눠 맡김: {w['n']}건" + (f" ({who})" if who else "")
              + (f" · 사유 {w['error']}" if w.get("error") else ""))
    rb = r.get("rebalance")
    if rb:
        who = " · ".join(f"{k} +{v}" for k, v in (rb.get("to") or {}).items())
        print(f"  멈춘 일 넘김: {rb['n']}건" + (f" ({who})" if who else "")
              + (f" · 사유 {rb['error']}" if rb.get("error") else ""))
    if not (w or rb):
        print("  이번 사이클에 할 일이 없습니다(이미 처리했거나 자동 운영이 꺼져 있습니다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
