#!/usr/bin/env python3
"""프리즘 패치노트 슬랙 발송기 · 표준 라이브러리만 사용 (Python 3.8+).

사용법:
  python3 send_slack.py payload.json                # 웹훅으로 POST
  python3 send_slack.py --dry-run payload.json      # 검증만 하고 전송 안 함
  python3 send_slack.py --mark "PR #60~#68" payload.json
                                                    # 성공 시 ~/.prism_patchnote_last 에 범위 기록

웹훅 URL 은 PRISM_SLACK_WEBHOOK 환경변수 또는 ~/.prism_slack_webhook 파일(권장 · chmod 600).
payload.json 은 Slack Incoming Webhook 본문 그대로: {"text": "...", "blocks": [...]}
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

WEBHOOK_FILE = Path.home() / ".prism_slack_webhook"
MARK_FILE = Path.home() / ".prism_patchnote_last"
HEADER_MAX = 150     # Slack header 블록 text 제한
SECTION_MAX = 3000   # Slack section 블록 text 제한


def fail(msg):
    print("오류: " + msg, file=sys.stderr)
    sys.exit(1)


def load_webhook_url():
    url = os.environ.get("PRISM_SLACK_WEBHOOK", "").strip()
    if not url and WEBHOOK_FILE.exists():
        url = WEBHOOK_FILE.read_text(encoding="utf-8").strip()
    if not url:
        fail(
            "웹훅 URL 이 없습니다. Slack 앱의 Incoming Webhook URL 을 발급해\n"
            "  echo 'https://hooks.slack.com/services/…' > ~/.prism_slack_webhook\n"
            "  chmod 600 ~/.prism_slack_webhook\n"
            "으로 저장하세요 (또는 PRISM_SLACK_WEBHOOK 환경변수)."
        )
    if not url.startswith("https://hooks.slack.com/"):
        fail("웹훅 URL 형식이 아닙니다 (https://hooks.slack.com/ 로 시작해야 함): " + url[:40] + "…")
    return url


def validate(payload):
    if not isinstance(payload, dict):
        fail("payload 최상위는 JSON 객체여야 합니다.")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        fail('최상위 "text"(알림 폴백용 요약 문자열)가 필요합니다.')
    blocks = payload.get("blocks")
    if blocks is None:
        return
    if not isinstance(blocks, list) or not blocks:
        fail('"blocks" 는 비어있지 않은 배열이어야 합니다.')
    if len(blocks) > 50:
        fail("blocks 는 최대 50개입니다 (현재 %d개)." % len(blocks))
    for i, b in enumerate(blocks):
        btype = b.get("type") if isinstance(b, dict) else None
        btext = ""
        if isinstance(b, dict) and isinstance(b.get("text"), dict):
            btext = b["text"].get("text", "")
        if btype == "header" and len(btext) > HEADER_MAX:
            fail("blocks[%d] header 가 %d자 제한을 넘습니다 (%d자)." % (i, HEADER_MAX, len(btext)))
        if btype == "section" and len(btext) > SECTION_MAX:
            fail(
                "blocks[%d] section 이 %d자 제한을 넘습니다 (%d자). 블록을 분할하세요."
                % (i, SECTION_MAX, len(btext))
            )


def post(url, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        fail("네트워크 오류: %s" % e.reason)


def main(argv):
    dry_run = False
    mark = None
    paths = []
    it = iter(argv)
    for a in it:
        if a == "--dry-run":
            dry_run = True
        elif a == "--mark":
            mark = next(it, None)
            if not mark:
                fail("--mark 뒤에 범위 문자열이 필요합니다. 예: --mark \"PR #60~#68\"")
        elif a.startswith("-"):
            fail("알 수 없는 옵션: " + a)
        else:
            paths.append(a)
    if len(paths) != 1:
        fail("payload JSON 파일 경로 1개가 필요합니다.\n" + (__doc__ or ""))

    p = Path(paths[0])
    if not p.exists():
        fail("파일이 없습니다: %s" % p)
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail("JSON 파싱 실패: %s" % e)

    validate(payload)
    if dry_run:
        print("검증 통과 (dry-run · 전송 안 함): blocks %d개" % len(payload.get("blocks") or []))
        return

    url = load_webhook_url()
    status, body = post(url, payload)
    if status == 200 and body.strip() == "ok":
        print("발송 성공")
        if mark:
            stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            MARK_FILE.write_text("%s\t%s\n" % (stamp, mark), encoding="utf-8")
            print("마지막 발송 기록 갱신: %s → %s" % (mark, MARK_FILE))
    else:
        fail("슬랙 응답 이상 (HTTP %s): %s" % (status, body[:300]))


if __name__ == "__main__":
    main(sys.argv[1:])
