"""Prism 데스크탑 앱 진입점 (pywebview 네이티브 창).

로컬 Prism 서버를 백그라운드 스레드로 띄우고, 그 UI 를 네이티브 WKWebView 창에 표시.
키는 ~/.prism_key 에서 자동 로드(설정 UI 에서 입력·저장).
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import webview

HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}/"

_httpd = None


def _seed_user_config():
    """첫 실행 시 번들 기본 config.json 을 사용자 디렉터리로 복사(번들은 읽기전용)."""
    from prism.config import DEFAULT_CONFIG_PATH
    if os.path.exists(DEFAULT_CONFIG_PATH):
        return
    base = getattr(sys, "_MEIPASS", None)
    src = os.path.join(base, "config.json") if base else None
    if src and os.path.exists(src):
        os.makedirs(os.path.dirname(DEFAULT_CONFIG_PATH), exist_ok=True)
        shutil.copyfile(src, DEFAULT_CONFIG_PATH)


def _enable_supabase():
    """~/.prism_supabase_key(서비스키) + URL 이 있으면 팀(supabase) 모드로 기동 —
    로그인/가입·팀 참가·관리자. 없으면 로컬 sqlite 단독(닉네임만).
    GUI 앱은 셸 env 를 못 물려받으므로 키파일에서 직접 로드한다."""
    keyfile = os.path.expanduser("~/.prism_supabase_key")
    urlfile = os.path.expanduser("~/.prism_supabase_url")
    if not os.path.exists(keyfile):
        return
    try:
        key = open(keyfile, encoding="utf-8").read().strip()
    except Exception:
        return
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    if not url and os.path.exists(urlfile):
        url = open(urlfile, encoding="utf-8").read().strip()
    if key and url:
        os.environ["SUPABASE_URL"] = url
        os.environ["SUPABASE_SERVICE_KEY"] = key
        os.environ["PRISM_BACKEND"] = "supabase"


def _start_server():
    global _httpd
    _enable_supabase()                         # 키파일 있으면 팀 모드(로그인/가입)로 전환
    from prism.serve import (Handler, load_persisted_key, load_dict_overrides,
                             get_store, sync_prompt, start_ingest_scheduler, start_learning_scheduler)
    load_persisted_key()                       # ~/.prism_key 자동 로드
    load_dict_overrides()                       # 사전 편집(overrides) 적용
    sync_prompt()                               # 단계 프롬프트·학습 보정 반영
    get_store()                                 # 로컬 영속 저장소(SQLite) 초기화
    start_ingest_scheduler()                    # 활성 소스 자동 폴링(백그라운드)
    start_learning_scheduler()                  # 매일 04:00 학습 일배치
    _httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    _httpd.serve_forever()


def _wait_ready(timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(URL, timeout=0.5)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def main():
    _seed_user_config()
    threading.Thread(target=_start_server, daemon=True).start()
    _wait_ready()
    webview.create_window("Prism", URL, width=1240, height=860, min_size=(900, 600))
    webview.start()                            # macOS: Cocoa/WKWebView 백엔드


if __name__ == "__main__":
    main()
