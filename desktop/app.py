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


def _qa_mode() -> bool:
    """QA 빌드 여부: 번들 마커(qa.flag) 또는 PRISM_QA=1. 로컬 sqlite + mock + 목업 시드."""
    base = getattr(sys, "_MEIPASS", "")
    if base and os.path.exists(os.path.join(base, "qa.flag")):
        return True
    return os.environ.get("PRISM_QA") == "1"


def _start_server():
    global _httpd
    qa = _qa_mode()
    if qa:                                     # QA: 팀/실호출 배제 · 별도 DB · 목업 시드
        os.environ["PRISM_BACKEND"] = "sqlite"
        from prism.config import DEFAULT_CONFIG_PATH as _CFGP
        os.environ.setdefault("PRISM_DB", os.path.join(os.path.dirname(_CFGP), "qa.db"))
    else:
        _enable_supabase()                     # 키파일 있으면 팀 모드(로그인/가입)로 전환
    from prism.serve import (Handler, load_persisted_key, load_dict_overrides,
                             get_store, sync_prompt, start_ingest_scheduler, start_learning_scheduler)
    load_persisted_key()                       # ~/.prism_key 자동 로드
    load_dict_overrides()                       # 사전 편집(overrides) 적용
    sync_prompt()                               # 단계 프롬프트·학습 보정 반영
    get_store()                                 # 로컬 영속 저장소(SQLite) 초기화
    if qa:
        Handler.server_mock = True             # 실호출 없이 전 기능 QA(모의 추출)
        try:
            from prism import qa_seed
            qa_seed.seed()                     # 비어 있으면 콘텐츠 10건 + 예시 데이터 적재
        except Exception:
            pass
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
    # 데스크탑 셸 옵션(시스템 설정 · 데스크탑 카드에서 조정, 재시작 시 적용)
    from prism.config import Config
    cfg = Config.load()
    allow_dl = bool(getattr(cfg, "desktop_allow_downloads", True))
    persist = bool(getattr(cfg, "desktop_persist_storage", True))
    # 다운로드 허용: 템플릿(xlsx/csv)·엑셀 내보내기 앵커가 WKWebView 에서 동작하도록 (기본 False 면 무시됨)
    webview.settings["ALLOW_DOWNLOADS"] = allow_dl
    webview.create_window("Prism QA" if _qa_mode() else "Prism", URL, width=1240, height=860, min_size=(900, 600))
    # localStorage 영속: private_mode=True 는 재시작마다 로그인 토큰·아이디/비밀번호 저장·배지 기준선을 지움
    if persist:
        storage = os.path.expanduser("~/Library/Application Support/Prism/webview")
        os.makedirs(storage, exist_ok=True)
        webview.start(private_mode=False, storage_path=storage)   # macOS: Cocoa/WKWebView 백엔드
    else:
        webview.start()


if __name__ == "__main__":
    main()
