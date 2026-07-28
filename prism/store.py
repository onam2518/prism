"""SQLite 영속성 (운영 하드닝). 결과·usage·판정사례를 파일 DB에 적재."""
from __future__ import annotations
import json
import os
import sqlite3
import threading
import time
import hashlib

_local = threading.local()
_EVENT_ONCE_LOCK = threading.Lock()   # log_event_once 의 check-then-insert 직렬화(미션 보상 이중 지급 방지)


def content_hash(content: dict) -> str:
    s = (content.get("displayServiceName", "") + "\x1f" + content.get("title", "")
         + "\x1f" + content.get("subtitle", "") + "\x1f" + content.get("body", ""))
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _tz_sec() -> int:
    """일 경계 타임존 오프셋(초). PRISM_TZ_MIN(분) · 기본 540 = KST(UTC+9).
    운영 서버(fly · UTC)가 서버 로컬 날짜로 버킷팅하면 검수팀(KST)의 자정~09시
    활동이 전날 막대에 붙는다 → 일별 집계는 팀 로컬 날짜로 고정한다."""
    try:
        return int(os.environ.get("PRISM_TZ_MIN", "540")) * 60
    except ValueError:
        return 540 * 60


def day_key(ts=None) -> str:
    """epoch → 팀 타임존 기준 'YYYY-MM-DD'. 일별 롤업·활동 추이의 공통 버킷 키."""
    t = time.time() if ts is None else float(ts or 0)
    return time.strftime("%Y-%m-%d", time.gmtime(t + _tz_sec()))


LEVEL_MAX = 50


def level_floor(level: int) -> int:
    """레벨 도달에 필요한 누적 pt. 구간 요구치 = 100 + 80×(레벨-1)씩 증가(등차).
    만렙(50) = 98,980pt ≈ 검수 1만 건(건당 10pt): 개인 1만 건 검수 완주 설계."""
    return (level - 1) * 100 + 40 * (level - 1) * (level - 2)


def level_of(points: int) -> int:
    """누적 pt → 레벨(1~LEVEL_MAX). 초반 짧고 후반 길어지는 커브(Flow: 잦은 초기 보상)."""
    lvl = 1
    while lvl < LEVEL_MAX and points >= level_floor(lvl + 1):
        lvl += 1
    return lvl


# 최종판정(2층 최종검수) 1건당 점수 · 산식 최상위 가중(기본검수 10 · 교정 25 대비 4배/1.6배).
# 근거: 판정 1건이 의견 갈림을 종결하고 정답(골든) 편입 여부를 결정 — 콘텐츠 1건에서 가장
# 무거운 단일 행동. 종전에는 final_verdicts 원장에만 남아 점수 기여가 0이라 최종검수자의
# 랭킹이 오르지 않았다(2026-07-23 수정). 원장에 과거 판정이 보존돼 있어 소급 반영된다.
FINAL_VERDICT_POINTS = 40


def final_verdict_counts(store, team=None) -> tuple:
    """final_verdicts 원장 → (판정자별 누적, 이번주, 지난주) 카운트.
    원장은 reports kind='final_verdicts' 의 {hash: {verdict, by, ts}} · 철회분은 원장에서 빠져 자동 제외.
    SQLite·Supabase 양쪽 arena_stats 가 공유(산식 드리프트 방지)."""
    try:
        items = (store.get_report("final_verdicts", team=team) or {}).get("items") or {}
    except Exception:
        return {}, {}, {}
    DAY = 86400.0
    now = time.time()
    week_ago, prev_ago = now - 7 * DAY, now - 14 * DAY
    total, wk, pv = {}, {}, {}
    for v in items.values():
        rv = ((v or {}).get("by") or "").strip()
        if not rv:
            continue                                   # 판정자 미상 = 개인 점수 귀속 불가
        total[rv] = total.get(rv, 0) + 1
        t = float(v.get("ts") or 0)
        if t >= week_ago:
            wk[rv] = wk.get(rv, 0) + 1
        elif t >= prev_ago:
            pv[rv] = pv.get(rv, 0) + 1
    return total, wk, pv


class Store:
    def __init__(self, path: str):
        self.path = path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        # 스레드별 커넥션(ThreadPool 동시 쓰기 안전)
        c = getattr(_local, "conn", None)
        if c is None or getattr(_local, "path", None) != self.path:
            c = sqlite3.connect(self.path, timeout=30)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            _local.conn = c
            _local.path = self.path
        return c

    def _init(self):
        c = self._conn()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS results(
          content_hash TEXT PRIMARY KEY, run_id TEXT, service TEXT, title TEXT,
          final_grade TEXT, reasons TEXT, item_meta TEXT, payload TEXT,
          cost_usd REAL, fail_kind TEXT, created_at REAL, source TEXT);
        CREATE TABLE IF NOT EXISTS usage(
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT, n INTEGER,
          cost_usd REAL, tokens_in INTEGER, tokens_out INTEGER);
        CREATE TABLE IF NOT EXISTS runs(
          run_id TEXT PRIMARY KEY, started_at REAL, finished_at REAL,
          n INTEGER, config TEXT, metrics TEXT);
        CREATE TABLE IF NOT EXISTS cases(
          content_hash TEXT PRIMARY KEY, service TEXT, title TEXT,
          final_grade TEXT, reasons TEXT, source TEXT, manual_review INTEGER,
          created_at REAL);
        -- 팀 HITL: 검수자별 다중 의견 보존(PK = content_hash + reviewer).
        CREATE TABLE IF NOT EXISTS feedback(
          content_hash TEXT, reviewer TEXT, service TEXT, title TEXT,
          verdict TEXT, stage TEXT, note TEXT, ts REAL,
          PRIMARY KEY(content_hash, reviewer));
        -- 검수자 등록: 이름 → 선택 캐릭터(아바타) 매핑.
        CREATE TABLE IF NOT EXISTS reviewers(reviewer TEXT PRIMARY KEY, char TEXT, ts REAL);
        -- 골든셋: 검수(정확) 확정 콘텐츠 = 정답셋. content_hash 로 upsert.
        CREATE TABLE IF NOT EXISTS golden(
          content_hash TEXT PRIMARY KEY, content TEXT, expected TEXT, ts REAL);
        -- 교정 로그(append-only): patch 전/후 보존 → 선호쌍(DPO) 데이터 원천.
        CREATE TABLE IF NOT EXISTS patch_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT, content_hash TEXT, reviewer TEXT,
          element TEXT, before TEXT, after TEXT, ts REAL);
        -- 골드 문항 응답: 정답 알려진 검증 문항에 대한 검수자 판정(품질 측정 원천).
        CREATE TABLE IF NOT EXISTS gold_checks(
          id INTEGER PRIMARY KEY AUTOINCREMENT, content_hash TEXT, reviewer TEXT,
          expected TEXT, verdict TEXT, correct INTEGER, ts REAL);
        -- 이벤트 로그(append-only): 미션 달성 등 1회성 보상·감사 추적.
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, reviewer TEXT, kind TEXT,
          day INTEGER, bonus INTEGER, meta TEXT, ts REAL);
        -- 운영 리포트 영속(kind×team): 최근 학습 반영·평가 상세 등 재시작에도 유지.
        CREATE TABLE IF NOT EXISTS reports(
          kind TEXT, team TEXT NOT NULL DEFAULT '', payload TEXT, ts REAL,
          PRIMARY KEY(kind, team));
        -- 평가 판정(집단 지성): 평가 불일치 건에 대한 검수자 판정. adopt=모델 결과 채택(정답 교정 후보)
        -- / reject=탈락(정답 유지 · 모델 오답 확정). 1인 1표 upsert.
        CREATE TABLE IF NOT EXISTS eval_checks(
          content_hash TEXT, reviewer TEXT, verdict TEXT, expected TEXT, got TEXT, ts REAL,
          PRIMARY KEY(content_hash, reviewer));
        -- 콘텐츠 용도: review(검수용, 기본)=검수·골든 축적 / eval(평가용)=평가 전용 홀드아웃.
        CREATE TABLE IF NOT EXISTS content_purpose(
          content_hash TEXT PRIMARY KEY, purpose TEXT, ts REAL);
        -- 초안 이력: (콘텐츠, 모델, 버전) 별 산출 스냅샷. 결과 비교 팝업의 전체 이력 원천.
        CREATE TABLE IF NOT EXISTS drafts(
          content_hash TEXT, team TEXT NOT NULL DEFAULT '', model TEXT, version INTEGER,
          item_meta TEXT, quality_meta TEXT, ts REAL,
          PRIMARY KEY(content_hash, team, model, version));
        -- 피드백 라우팅(append-only): 교정 원문을 요소·단계별 개선 지시로 재분류한 결과.
        CREATE TABLE IF NOT EXISTS feedback_routes(
          id INTEGER PRIMARY KEY AUTOINCREMENT, content_hash TEXT, reviewer TEXT,
          element TEXT, stage TEXT, directive TEXT, model TEXT, ts REAL);
        -- 게시판(팀 스코프): 기능개선 제안 · 오류 제보 수집. status = open|doing|done.
        CREATE TABLE IF NOT EXISTS board(
          id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT NOT NULL DEFAULT '',
          kind TEXT, title TEXT, body TEXT, reviewer TEXT, status TEXT, ts REAL);
        -- 콘텐츠별 검수 담당 배정(팀 스코프). 배정되면 담당자에게만 큐 노출(배타적).
        CREATE TABLE IF NOT EXISTS assignments(
          content_hash TEXT, reviewer TEXT, team TEXT NOT NULL DEFAULT '', ts REAL,
          PRIMARY KEY(content_hash, reviewer, team));
        -- 콘텐츠별 최소 검수인원(통과 기준 N). 배정 있을 때만 유효 · 미설정 기본 1.
        CREATE TABLE IF NOT EXISTS assignment_cfg(
          content_hash TEXT, team TEXT NOT NULL DEFAULT '', min_reviewers INTEGER,
          PRIMARY KEY(content_hash, team));
        -- 엔티티 사전: 개체 고유키·타입(NER 6종)·타입별 속성. 타입·속성은 적재 부여 메타(재판정 없음).
        CREATE TABLE IF NOT EXISTS entities(
          entity_id TEXT PRIMARY KEY, name TEXT, type TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'pending', attrs TEXT, attr_meta TEXT,
          external_ids TEXT, merged_into TEXT NOT NULL DEFAULT '',
          created_at REAL, updated_at REAL);
        -- 별칭 조회 테이블(이형 표기 → 개체) · 표시용 별칭 목록도 여기서 파생.
        CREATE TABLE IF NOT EXISTS entity_aliases(
          alias TEXT PRIMARY KEY, entity_id TEXT);
        -- 콘텐츠 ↔ 개체 링크. item_meta.entities(추출 산출물)는 불변 · 링크만 추가.
        CREATE TABLE IF NOT EXISTS content_entities(
          content_hash TEXT, entity_id TEXT, surface TEXT, team TEXT NOT NULL DEFAULT '',
          ts REAL, PRIMARY KEY(content_hash, entity_id, team));
        -- 평가 런(이력): 골든셋 평가 실행 단위(Atelier eval_runs 이식). 청크마다 cursor 갱신 → 진행률·재개.
        CREATE TABLE IF NOT EXISTS eval_runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT NOT NULL DEFAULT '',
          model TEXT, scope TEXT, status TEXT, cursor INTEGER NOT NULL DEFAULT 0,
          total INTEGER NOT NULL DEFAULT 0, metrics TEXT, error TEXT,
          created_by TEXT, ts REAL, finished REAL,
          rubric_status TEXT NOT NULL DEFAULT '', rubric_cursor INTEGER NOT NULL DEFAULT 0,
          rubric TEXT);
        -- 평가 런 건별 결과: 기대 vs 실제 등급·사유 스냅샷(불일치 감사·재개 판별 원천).
        -- rubric = 4축 저지 채점(accuracy/format/policy/conciseness/note · Atelier 이식).
        CREATE TABLE IF NOT EXISTS eval_results(
          run_id INTEGER, content_hash TEXT, title TEXT,
          expected TEXT, got TEXT, passed INTEGER, error TEXT, ts REAL, rubric TEXT,
          PRIMARY KEY(run_id, content_hash));
        -- 오토파일럿 런: 자동 개선 루프(라운드=learning_batch) 상태머신 · 이력(Atelier autopilot 이식).
        CREATE TABLE IF NOT EXISTS autopilot_runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT NOT NULL DEFAULT '',
          status TEXT, target REAL, max_rounds INTEGER, round INTEGER NOT NULL DEFAULT 0,
          start_accuracy REAL, best_accuracy REAL, last_accuracy REAL,
          history TEXT, stop_reason TEXT, error TEXT, created_by TEXT,
          ts REAL, heartbeat REAL, finished REAL);
        -- 프롬프트 배포: 스냅샷 버전을 slug 에 pin · 외부가 Bearer 키로 당겨 씀(Atelier deployments 이식).
        CREATE TABLE IF NOT EXISTS deployments(
          id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT NOT NULL DEFAULT '',
          slug TEXT UNIQUE, name TEXT, version INTEGER NOT NULL DEFAULT 0,
          active INTEGER NOT NULL DEFAULT 1, created_by TEXT, ts REAL, updated REAL);
        -- 배포 API 키: sha256 해시만 저장(평문 미보관) · revoked 로 무효화.
        CREATE TABLE IF NOT EXISTS deployment_keys(
          id INTEGER PRIMARY KEY AUTOINCREMENT, deployment_id INTEGER,
          key_hash TEXT, key_prefix TEXT, revoked INTEGER NOT NULL DEFAULT 0,
          ts REAL, last_used REAL);
        CREATE INDEX IF NOT EXISTS ix_depkeys_dep ON deployment_keys(deployment_id);
        -- 프롬프트 라이브러리: 잘 나온 프롬프트 패턴 저장·재사용(Atelier prompt_library 이식).
        CREATE TABLE IF NOT EXISTS prompt_library(
          id INTEGER PRIMARY KEY AUTOINCREMENT, team TEXT NOT NULL DEFAULT '',
          name TEXT, domain TEXT, prompt TEXT, note TEXT, source TEXT,
          pinned INTEGER NOT NULL DEFAULT 0, created_by TEXT, ts REAL);
        CREATE INDEX IF NOT EXISTS ix_centities_ent ON content_entities(entity_id);
        CREATE INDEX IF NOT EXISTS ix_ealias_ent ON entity_aliases(entity_id);
        CREATE INDEX IF NOT EXISTS ix_assign_team ON assignments(team, reviewer);
        CREATE INDEX IF NOT EXISTS ix_results_run ON results(run_id);
        CREATE INDEX IF NOT EXISTS ix_gold_reviewer ON gold_checks(reviewer);
        CREATE INDEX IF NOT EXISTS ix_events_reviewer ON events(reviewer, kind, day);
        """)
        c.commit()
        self._migrate_feedback(c)
        if "source" not in [r[1] for r in c.execute("PRAGMA table_info(results)")]:
            c.execute("ALTER TABLE results ADD COLUMN source TEXT"); c.commit()   # 출처 필터
        if "source" not in [r[1] for r in c.execute("PRAGMA table_info(golden)")]:
            c.execute("ALTER TABLE golden ADD COLUMN source TEXT DEFAULT 'review'"); c.commit()   # 골든 출처(review|manual)
        if "rubric" not in [r[1] for r in c.execute("PRAGMA table_info(eval_results)")]:
            c.execute("ALTER TABLE eval_results ADD COLUMN rubric TEXT"); c.commit()   # 루브릭 채점(Atelier 이식)
        if "rubric_status" not in [r[1] for r in c.execute("PRAGMA table_info(eval_runs)")]:
            c.execute("ALTER TABLE eval_runs ADD COLUMN rubric_status TEXT NOT NULL DEFAULT ''")
            c.execute("ALTER TABLE eval_runs ADD COLUMN rubric_cursor INTEGER NOT NULL DEFAULT 0")
            c.execute("ALTER TABLE eval_runs ADD COLUMN rubric TEXT"); c.commit()
        bcols = [r[1] for r in c.execute("PRAGMA table_info(board)")]
        if "answer" not in bcols:
            c.execute("ALTER TABLE board ADD COLUMN answer TEXT"); c.commit()          # 게시판 관리자 답변
        if "answered_at" not in bcols:
            c.execute("ALTER TABLE board ADD COLUMN answered_at REAL"); c.commit()

    def _migrate_feedback(self, c):
        """구 스키마(PK=content_hash, 단일 의견) → 신 스키마(PK=content_hash+reviewer) 이행.
        기존 1건은 reviewer='(이전)'으로 보존. 신규 DB 엔 영향 없음.
        + REAP 컬럼(remember/explain/ask/plan) 추가(없으면 ALTER)."""
        cols = [r[1] for r in c.execute("PRAGMA table_info(feedback)")]
        if "reviewer" not in cols:
            c.executescript("""
            ALTER TABLE feedback RENAME TO feedback_legacy;
            CREATE TABLE feedback(
              content_hash TEXT, reviewer TEXT, service TEXT, title TEXT,
              verdict TEXT, stage TEXT, note TEXT, ts REAL,
              PRIMARY KEY(content_hash, reviewer));
            INSERT INTO feedback(content_hash,reviewer,service,title,verdict,stage,note,ts)
              SELECT content_hash,'(이전)',service,title,verdict,stage,note,ts FROM feedback_legacy;
            DROP TABLE feedback_legacy;
            """)
            c.commit()
            cols = [r[1] for r in c.execute("PRAGMA table_info(feedback)")]
        for col in ("remember", "explain", "ask", "plan", "element"):   # REAP 산출 + 교정 요소
            if col not in cols:
                c.execute(f"ALTER TABLE feedback ADD COLUMN {col} TEXT")
        c.commit()

    # 결과 upsert / resume
    def done_hashes(self, only_ok: bool = True) -> set:
        """이미 처리된 content_hash 집합. only_ok=True 면 실패건은 미처리로 간주(재시도)."""
        c = self._conn()
        q = "SELECT content_hash FROM results"
        if only_ok:
            q += " WHERE fail_kind IS NULL OR fail_kind=''"
        return {r[0] for r in c.execute(q)}

    def set_source_url(self, content_hash, url, team=None) -> bool:
        """원문 링크 백필: payload.content_ref.source_url 만 교체(초안·판정·적재 시각 불변).
        해시는 서비스+제목+부제+본문으로만 계산되므로 링크 교체는 콘텐츠 정체성을 바꾸지 않는다.
        team 은 supabase 와 시그니처 통일용(sqlite 단일팀이라 미사용)."""
        c = self._conn()
        row = c.execute("SELECT payload FROM results WHERE content_hash=?", (content_hash,)).fetchone()
        if not row:
            return False
        try:
            payload = json.loads(row[0])
        except (TypeError, ValueError):
            return False
        ref = payload.get("content_ref") or {}
        ref["source_url"] = url
        payload["content_ref"] = ref
        c.execute("UPDATE results SET payload=? WHERE content_hash=?",
                  (json.dumps(payload, ensure_ascii=False), content_hash))
        c.commit()
        return True

    def set_ops_hold(self, content_hash, on, team=None) -> bool:
        """운영자 수동 노출제한 플래그: payload.quality_meta.ops_hold 에 저장.
        품질 라벨(finalGrade·reasons)이 아니라 별도 키라 학습 루프(골든·피드백)가 읽지 않는다(학습 미포함)."""
        c = self._conn()
        row = c.execute("SELECT payload FROM results WHERE content_hash=?", (content_hash,)).fetchone()
        if not row:
            return False
        try:
            payload = json.loads(row[0])
        except (TypeError, ValueError):
            return False
        qm = payload.get("quality_meta") or {}
        qm["ops_hold"] = bool(on)
        payload["quality_meta"] = qm
        c.execute("UPDATE results SET payload=? WHERE content_hash=?",
                  (json.dumps(payload, ensure_ascii=False), content_hash))
        c.commit()
        return True

    def set_source_status(self, content_hash, state, by, team=None) -> bool:
        """원문 소실 신고 플래그(게시판 #10): payload.content_ref.source_status 에 저장.
        state = "gone"(원문 확인 불가) 또는 ""(해제) · by = 신고자 · ts = 기록 시각.
        품질 라벨(finalGrade·reasons)과 별개 키라 학습 루프가 읽지 않는다(ops_hold 와 동일 설계).
        team 은 supabase 와 시그니처 통일용(sqlite 단일팀이라 미사용)."""
        state = (state or "").strip()
        if state not in ("", "gone"):
            return False
        c = self._conn()
        row = c.execute("SELECT payload FROM results WHERE content_hash=?", (content_hash,)).fetchone()
        if not row:
            return False
        try:
            payload = json.loads(row[0])
        except (TypeError, ValueError):
            return False
        ref = payload.get("content_ref") or {}
        ref["source_status"] = {"state": state, "by": by or "", "ts": time.time()}
        payload["content_ref"] = ref
        c.execute("UPDATE results SET payload=? WHERE content_hash=?",
                  (json.dumps(payload, ensure_ascii=False), content_hash))
        c.commit()
        return True

    def save_result(self, content: dict, out: dict, run_id: str):
        ch = content_hash(content)
        qm = out.get("quality_meta", {})
        tr = out.get("trace", {})
        fbs = tr.get("fallbacks", [])
        fail_kind = ""
        for f in fbs:
            s = str(f)
            if "HTTP" in s or "_fail" in s or "예외" in s:
                fail_kind = "api"
                break
        c = self._conn()
        c.execute("""INSERT INTO results
          (content_hash,run_id,service,title,final_grade,reasons,item_meta,payload,cost_usd,fail_kind,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(content_hash) DO UPDATE SET
            run_id=excluded.run_id, final_grade=excluded.final_grade,
            reasons=excluded.reasons, item_meta=excluded.item_meta,
            payload=excluded.payload, cost_usd=excluded.cost_usd,
            fail_kind=excluded.fail_kind, created_at=excluded.created_at""",
          (ch, run_id, content.get("displayServiceName", ""), content.get("title", ""),
           qm.get("finalGrade", ""), json.dumps(qm.get("reasons", []), ensure_ascii=False),
           json.dumps(out.get("item_meta"), ensure_ascii=False),
           json.dumps(out, ensure_ascii=False), tr.get("cost_usd", 0.0),
           fail_kind, time.time()))
        c.commit()

    def get_by_hashes(self, hashes) -> dict:
        """content_hash → payload(dict). resume 시 skip 한 건의 기존 결과 회수."""
        if not hashes:
            return {}
        c = self._conn()
        out = {}
        hl = list(hashes)
        for k in range(0, len(hl), 500):
            chunk = hl[k:k + 500]
            ph = ",".join("?" * len(chunk))
            for h, payload in c.execute(
                    f"SELECT content_hash,payload FROM results WHERE content_hash IN ({ph})",
                    chunk):
                out[h] = json.loads(payload)
        return out

    # ── 배치 저장(단일 트랜잭션) + UI 조회/집계 ──
    def save_many(self, pairs, run_id: str, source: str = "", team=None, include_all: bool = False):
        """pairs: [(content, out), …] 를 단일 트랜잭션으로 upsert(멱등). 반환: 건수.
        source: 출처(자동 인입·단건·배치 등) · 결과 화면 필터용.
        include_all 은 supabase 와의 시그니처 계약용(sqlite 는 원래 전량 저장)."""
        rows = []
        for content, out in pairs:
            ch = content_hash(content)
            qm = out.get("quality_meta", {}) or {}
            tr = out.get("trace", {}) or {}
            fail_kind = ""
            for f in tr.get("fallbacks", []) or []:
                s = str(f)
                if "HTTP" in s or "_fail" in s or "예외" in s:
                    fail_kind = "api"; break
            rows.append((ch, run_id, content.get("displayServiceName", ""), content.get("title", ""),
                         qm.get("finalGrade", ""), json.dumps(qm.get("reasons", []), ensure_ascii=False),
                         json.dumps(out.get("item_meta"), ensure_ascii=False),
                         json.dumps(out, ensure_ascii=False), tr.get("cost_usd", 0.0),
                         fail_kind, time.time(), source))
        if not rows:
            return 0
        c = self._conn()
        c.executemany("""INSERT INTO results
          (content_hash,run_id,service,title,final_grade,reasons,item_meta,payload,cost_usd,fail_kind,created_at,source)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(content_hash) DO UPDATE SET
            run_id=excluded.run_id, final_grade=excluded.final_grade, reasons=excluded.reasons,
            item_meta=excluded.item_meta, payload=excluded.payload, cost_usd=excluded.cost_usd,
            fail_kind=excluded.fail_kind, created_at=excluded.created_at, source=excluded.source""", rows)
        c.commit()
        return len(rows)

    def save_dedup(self, pairs, run_id: str, source: str = "", team=None) -> dict:
        """적재 정책: content_hash 기준 멱등.
        · 신규 → insert  · 기존인데 메타(등급·item_meta·reasons) 변경 → update
        · 동일 콘텐츠 + 결과 무변경 → 적재 제외(skip, DB 미기록).
        (trace·cost 같은 실행 부산물은 비교에서 제외 · 매 실행 달라지므로)
        반환: {inserted, updated, skipped}"""
        c = self._conn()
        ins = upd = skip = 0
        rows = []
        for content, out in pairs:
            ch = content_hash(content)
            qm = out.get("quality_meta", {}) or {}
            tr = out.get("trace", {}) or {}
            new_im = json.dumps(out.get("item_meta"), ensure_ascii=False, sort_keys=True)
            new_gr = qm.get("finalGrade", "")
            new_rs = json.dumps(qm.get("reasons", []), ensure_ascii=False, sort_keys=True)
            cur = c.execute("SELECT item_meta, final_grade, reasons FROM results WHERE content_hash=?", (ch,)).fetchone()
            if cur is not None:
                try: old_im = json.dumps(json.loads(cur[0]), ensure_ascii=False, sort_keys=True)
                except Exception: old_im = cur[0] or ""
                try: old_rs = json.dumps(json.loads(cur[2]), ensure_ascii=False, sort_keys=True)
                except Exception: old_rs = cur[2] or ""
                if old_im == new_im and (cur[1] or "") == new_gr and old_rs == new_rs:
                    skip += 1
                    continue                      # 동일 콘텐츠·결과 → 적재 제외
                upd += 1
            else:
                ins += 1
            fail_kind = ""
            for f in tr.get("fallbacks", []) or []:
                s = str(f)
                if "HTTP" in s or "_fail" in s or "예외" in s:
                    fail_kind = "api"; break
            rows.append((ch, run_id, content.get("displayServiceName", ""), content.get("title", ""),
                         new_gr, json.dumps(qm.get("reasons", []), ensure_ascii=False),
                         json.dumps(out.get("item_meta"), ensure_ascii=False),
                         json.dumps(out, ensure_ascii=False), tr.get("cost_usd", 0.0),
                         fail_kind, time.time(), source))
        if rows:
            c.executemany("""INSERT INTO results
              (content_hash,run_id,service,title,final_grade,reasons,item_meta,payload,cost_usd,fail_kind,created_at,source)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(content_hash) DO UPDATE SET
                run_id=excluded.run_id, final_grade=excluded.final_grade, reasons=excluded.reasons,
                item_meta=excluded.item_meta, payload=excluded.payload, cost_usd=excluded.cost_usd,
                fail_kind=excluded.fail_kind, created_at=excluded.created_at, source=excluded.source""", rows)
            c.commit()
        return {"inserted": ins, "updated": upd, "skipped": skip}

    def recent(self, limit: int = 5000, team=None) -> list:
        """최근 적재 결과(payload)를 시간순(오래된→최신)으로. content_id = 리스트 인덱스.
        team 은 supabase 와 시그니처 통일용(sqlite 단일팀이라 미사용)."""
        c = self._conn()
        rows = [json.loads(r[0]) for r in c.execute(
            "SELECT payload FROM results ORDER BY created_at DESC LIMIT ?", (int(limit),))]
        rows.reverse()
        return rows

    def count(self) -> int:
        c = self._conn()
        return int(c.execute("SELECT COUNT(*) FROM results").fetchone()[0])

    def clear(self):
        c = self._conn()
        c.execute("DELETE FROM results"); c.execute("DELETE FROM usage"); c.commit()

    def recent_meta(self, limit: int = 200, team=None) -> list:
        """배치 결과 콘텐츠별 행(피드백 부착용): content_hash·서비스·제목·등급·요약·카테고리."""
        c = self._conn()
        rows = []
        for ch, svc, ti, grade, im, src, payload in c.execute(
                "SELECT content_hash,service,title,final_grade,item_meta,source,payload FROM results ORDER BY created_at DESC LIMIT ?",
                (int(limit),)):
            try:
                imd = json.loads(im) if im else {}
            except Exception:
                imd = {}
            model, version, sstat = "", 1, {}
            try:
                pl = json.loads(payload) if payload else {}
                tr = pl.get("trace") or {}
                model = tr.get("model", "") or ""
                version = int(tr.get("version") or 1)
                sstat = (pl.get("content_ref") or {}).get("source_status") or {}
            except Exception:
                pass
            cat = " · ".join((imd or {}).get("content_category") or [])
            rows.append({"hash": ch, "service": svc or "", "title": ti or "",
                         "grade": grade or "", "summary": (imd or {}).get("summary", ""),
                         "category": cat, "source": src or "단건", "model": model, "version": version,
                         "source_status": sstat})
        pm = self.purpose_map(team)
        for r in rows:
            r["purpose"] = pm.get(r["hash"], "review")
        return rows

    # ── 평가 피드백 / 학습 루프 ──
    def save_feedback(self, content_hash, service, title, verdict, stage, note, ts, reviewer="(익명)", team=None, element=""):
        """검수자별 평가 피드백 upsert(검수자당 1건 · 같은 검수자는 자기 의견을 갱신).
        element = 교정 대상 요소(리드문·엔티티·인텐트·카테고리·등급·품질사유).
        team 은 supabase 와 시그니처 통일용(sqlite 단일팀이라 미사용)."""
        c = self._conn()
        c.execute("""INSERT INTO feedback(content_hash,reviewer,service,title,verdict,stage,note,ts,element)
          VALUES(?,?,?,?,?,?,?,?,?)
          ON CONFLICT(content_hash,reviewer) DO UPDATE SET
            verdict=excluded.verdict, stage=excluded.stage, note=excluded.note, ts=excluded.ts,
            service=excluded.service, title=excluded.title, element=excluded.element""",
          (content_hash, reviewer or "(익명)", service or "", title or "",
           verdict or "", stage or "analyze", note or "", ts, element or ""))
        c.commit()

    def delete_feedback(self, content_hash, reviewer, team=None) -> str:
        """판정 실행취소: 해당 검수자의 표 행을 삭제. 빈 표로 upsert 하면 팀 표 수(n)가
        부풀어 표기가 오염된다(정확 0 · 수정 1 인데 표 3개). 반환: 삭제된 이전 판정('' = 행 없음).
        team 은 supabase 와 시그니처 통일용(sqlite 단일팀이라 미사용)."""
        c = self._conn()
        row = c.execute("SELECT verdict FROM feedback WHERE content_hash=? AND reviewer=?",
                        (content_hash, reviewer or "(익명)")).fetchone()
        if not row:
            return ""
        c.execute("DELETE FROM feedback WHERE content_hash=? AND reviewer=?",
                  (content_hash, reviewer or "(익명)"))
        c.commit()
        return row[0] or ""

    # ── 교정 로그(append-only) · 골드 문항 · 이벤트 ──
    def log_patch(self, content_hash, reviewer, element, before, after, team=None):
        """검수자 구조화 교정의 전/후를 보존(선호쌍 데이터 원천 · 다중 요소 교정 무손실)."""
        c = self._conn()
        c.execute("INSERT INTO patch_log(content_hash,reviewer,element,before,after,ts) VALUES(?,?,?,?,?,?)",
                  (content_hash, reviewer or "(익명)", element or "",
                   json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), time.time()))
        c.commit()

    def patch_rows(self, limit: int = 5000, team=None) -> list:
        c = self._conn()
        out = []
        for ch, rv, el, bf, af, ts in c.execute(
                "SELECT content_hash,reviewer,element,before,after,ts FROM patch_log ORDER BY ts DESC LIMIT ?",
                (int(limit),)):
            try:
                out.append({"hash": ch, "reviewer": rv, "element": el or "",
                            "before": json.loads(bf or "{}"), "after": json.loads(af or "{}"), "ts": ts})
            except Exception:
                pass
        return out

    def patch_counts(self, team=None) -> dict:
        """reviewer → 구조화 교정 건수(patch_log)."""
        c = self._conn()
        return {rv: n for rv, n in c.execute(
            "SELECT reviewer, COUNT(*) FROM patch_log GROUP BY reviewer")}

    def save_gold_check(self, content_hash, reviewer, expected, verdict, team=None) -> bool:
        """골드 문항(정답 알려진 검증 문항) 응답 기록. 반환: 정답 여부."""
        ok = (verdict or "") == (expected or "")
        c = self._conn()
        c.execute("INSERT INTO gold_checks(content_hash,reviewer,expected,verdict,correct,ts) VALUES(?,?,?,?,?,?)",
                  (content_hash, reviewer or "(익명)", expected or "", verdict or "", int(ok), time.time()))
        c.commit()
        return ok

    def gold_stats(self, team=None) -> dict:
        """reviewer → {n, correct, acc}(골드 문항 정확도)."""
        c = self._conn()
        out = {}
        for rv, n, corr in c.execute(
                "SELECT reviewer, COUNT(*), SUM(correct) FROM gold_checks GROUP BY reviewer"):
            n = int(n or 0)
            corr = int(corr or 0)
            out[rv] = {"n": n, "correct": corr, "acc": round(corr / n, 4) if n else 0.0}
        return out

    def gold_stats_since(self, since_ts: float, team=None) -> dict:
        """reviewer → {n, correct, acc} · since_ts(epoch) 이후 응답만(주간 추세 계산용)."""
        c = self._conn()
        out = {}
        for rv, n, corr in c.execute(
                "SELECT reviewer, COUNT(*), SUM(correct) FROM gold_checks WHERE ts>=? GROUP BY reviewer",
                (float(since_ts),)):
            n = int(n or 0)
            corr = int(corr or 0)
            out[rv] = {"n": n, "correct": corr, "acc": round(corr / n, 4) if n else 0.0}
        return out

    def activity_daily(self, days: int = 30, team=None) -> list:
        """일별 검수 활동(최근 days일 · 빈 날 포함 연속): [{day, reviews, corrections, gold_n, gold_correct}].
        day = 팀 타임존(day_key) 기준 · 검수=판정(good/bad) 수 · 교정=bad 수 · 골드=검증 문항 응답.
        주의: feedback 은 (콘텐츠,검수자)당 1행 upsert 라 재검수하면 과거 활동이 최신 날짜로
        이동한다 — 화면 추이는 dashops.activity_daily_data 가 append-only 롤업과 병합해 보정."""
        import calendar
        days = max(1, min(90, int(days or 30)))
        now = time.time()
        keys = [day_key(now - i * 86400) for i in range(days - 1, -1, -1)]
        start_ts = calendar.timegm(time.strptime(keys[0], "%Y-%m-%d")) - _tz_sec()
        buckets = {}

        def _b(ts):
            d = day_key(ts)
            return buckets.setdefault(d, {"day": d, "reviews": 0, "corrections": 0,
                                          "gold_n": 0, "gold_correct": 0})

        c = self._conn()
        for v, ts in c.execute(
                "SELECT verdict, ts FROM feedback WHERE ts>=? AND verdict IN ('good','bad')",
                (start_ts,)):
            e = _b(ts)
            e["reviews"] += 1
            if v == "bad":
                e["corrections"] += 1
        for corr, ts in c.execute("SELECT correct, ts FROM gold_checks WHERE ts>=?", (start_ts,)):
            e = _b(ts)
            e["gold_n"] += 1
            e["gold_correct"] += int(corr or 0)
        return [buckets.get(k) or {"day": k, "reviews": 0, "corrections": 0,
                                   "gold_n": 0, "gold_correct": 0} for k in keys]

    def gold_answered(self, reviewer, team=None) -> set:
        """검수자가 이미 응답한 골드 문항 content_hash 집합(재출제 방지)."""
        c = self._conn()
        return {r[0] for r in c.execute(
            "SELECT DISTINCT content_hash FROM gold_checks WHERE reviewer=?", (reviewer or "(익명)",))}

    def log_event_once(self, reviewer, kind, day, bonus, meta="", team=None) -> bool:
        """(reviewer, kind, day) 당 1회만 기록(미션 보상 중복 방지). 신규 기록 시 True.
        check-then-insert 는 멀티스레드 서버에서 이중 지급 레이스가 있어 프로세스 락으로 직렬화."""
        with _EVENT_ONCE_LOCK:
            c = self._conn()
            cur = c.execute("SELECT 1 FROM events WHERE reviewer=? AND kind=? AND day=?",
                            (reviewer or "(익명)", kind, int(day))).fetchone()
            if cur:
                return False
            c.execute("INSERT INTO events(reviewer,kind,day,bonus,meta,ts) VALUES(?,?,?,?,?,?)",
                      (reviewer or "(익명)", kind, int(day), int(bonus), meta or "", time.time()))
            c.commit()
            return True

    def event_bonus(self, team=None) -> dict:
        """reviewer → {total, week}(미션 등 이벤트 보너스 합)."""
        c = self._conn()
        week_ago = time.time() - 7 * 86400.0
        out = {}
        for rv, ts, bonus in c.execute("SELECT reviewer,ts,bonus FROM events"):
            e = out.setdefault(rv, {"total": 0, "week": 0})
            e["total"] += int(bonus or 0)
            if (ts or 0) >= week_ago:
                e["week"] += int(bonus or 0)
        return out

    def batch_seq(self, team=None) -> int:
        """학습 반영(일배치) 누적 회차 → 초안 버전 = batch_seq + 1."""
        c = self._conn()
        return int(c.execute("SELECT COUNT(*) FROM events WHERE kind='learn_batch'").fetchone()[0])

    def feedback_today(self, reviewer, team=None) -> int:
        """검수자의 오늘(UTC 일 단위) 피드백 건수(미션 판정용)."""
        day_start = (int(time.time() // 86400)) * 86400.0
        c = self._conn()
        return int(c.execute("SELECT COUNT(*) FROM feedback WHERE reviewer=? AND ts>=?",
                             (reviewer or "(익명)", day_start)).fetchone()[0])

    def gold_today(self, reviewer, team=None) -> dict:
        """검수자의 오늘 골드 문항 {n, correct}(미션 판정용)."""
        day_start = (int(time.time() // 86400)) * 86400.0
        c = self._conn()
        n, corr = c.execute("SELECT COUNT(*), COALESCE(SUM(correct),0) FROM gold_checks WHERE reviewer=? AND ts>=?",
                            (reviewer or "(익명)", day_start)).fetchone()
        return {"n": int(n or 0), "correct": int(corr or 0)}

    def split_reviewed_today(self, reviewer, team=None) -> int:
        """검수자가 오늘 의견 갈린(split) 콘텐츠에 판정한 건수(불일치 재검토 미션 판정용)."""
        day_start = (int(time.time() // 86400)) * 86400.0
        c = self._conn()
        return int(c.execute("""
          SELECT COUNT(*) FROM feedback f WHERE f.reviewer=? AND f.ts>=? AND f.content_hash IN (
            SELECT content_hash FROM feedback WHERE verdict IN('good','bad')
            GROUP BY content_hash HAVING COUNT(DISTINCT verdict)>1)""",
          (reviewer or "(익명)", day_start)).fetchone()[0])

    def patches_today(self, reviewer, team=None) -> int:
        """검수자의 오늘 구조화 교정(분류 채우기 등) 건수(미션 판정용)."""
        day_start = (int(time.time() // 86400)) * 86400.0
        c = self._conn()
        return int(c.execute("SELECT COUNT(*) FROM patch_log WHERE reviewer=? AND ts>=?",
                             (reviewer or "(익명)", day_start)).fetchone()[0])

    def feedback_map(self, team=None) -> dict:
        """content_hash → 합의 집계. 다중 검수자 의견을 모아 합의/불일치 표시.
        반환: {verdicts:[{reviewer,verdict,stage,note,ts}], n, good, bad,
               consensus('good'|'bad'|'split'|''), agree(만장일치), verdict/stage/note(대표=합의·최신, 하위호환)}"""
        c = self._conn()
        out = {}
        for ch, rv, v, s, nt, ts, el in c.execute(
                "SELECT content_hash,reviewer,verdict,stage,note,ts,element FROM feedback ORDER BY ts"):
            if v not in ("good", "bad"):           # 과거 취소가 남긴 빈 표는 집계 제외(표 수 정합)
                continue
            e = out.setdefault(ch, {"verdicts": [], "good": 0, "bad": 0})
            e["verdicts"].append({"reviewer": rv, "verdict": v, "stage": s, "note": nt, "ts": ts,
                                  "element": el or ""})
            if v == "good":
                e["good"] += 1
            elif v == "bad":
                e["bad"] += 1
        for e in out.values():
            g, b = e["good"], e["bad"]
            e["n"] = len(e["verdicts"])
            e["consensus"] = ("good" if g > b else "bad" if b > g
                              else ("split" if (g or b) else ""))
            e["agree"] = e["n"] > 0 and (g == 0 or b == 0)
            last = e["verdicts"][-1]                      # 하위호환 대표 필드(합의 우선, 없으면 최신)
            e["verdict"] = e["consensus"] or last["verdict"]
            e["stage"], e["note"] = last["stage"], last["note"]
        return out

    def save_reap(self, content_hash, reviewer, reap: dict):
        """REAP 산출(remember/explain/ask/plan)을 해당 검수자 피드백 행에 기록."""
        c = self._conn()
        c.execute("""UPDATE feedback SET remember=?, explain=?, ask=?, plan=?
          WHERE content_hash=? AND reviewer=?""",
          (reap.get("remember", ""), reap.get("explain", ""), reap.get("ask", ""),
           reap.get("plan", ""), content_hash, reviewer or "(익명)"))
        c.commit()

    def get_reap(self, content_hash) -> list:
        """콘텐츠의 검수자별 REAP 산출 목록(UI 표시용)."""
        c = self._conn()
        out = []
        for rv, rm, ex, ak, pl, st in c.execute(
                "SELECT reviewer,remember,explain,ask,plan,stage FROM feedback "
                "WHERE content_hash=? AND (plan IS NOT NULL AND plan!='')", (content_hash,)):
            out.append({"reviewer": rv, "remember": rm, "explain": ex, "ask": ak,
                        "plan": pl, "stage": st})
        return out

    def save_report(self, kind: str, payload, team=None):
        """운영 리포트 upsert(JSON 직렬화 · 재시작 영속 · 팀 스코프)."""
        c = self._conn()
        c.execute("INSERT INTO reports(kind,team,payload,ts) VALUES(?,?,?,?) "
                  "ON CONFLICT(kind,team) DO UPDATE SET payload=excluded.payload, ts=excluded.ts",
                  (kind, team or "", json.dumps(payload, ensure_ascii=False), time.time()))
        c.commit()

    def get_report(self, kind: str, team=None):
        c = self._conn()
        row = c.execute("SELECT payload FROM reports WHERE kind=? AND team=?",
                        (kind, team or "")).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def save_draft(self, content_hash: str, model: str, version, item_meta, quality_meta, team=None):
        """(콘텐츠, 모델, 버전) 초안 스냅샷 upsert · 결과 비교 팝업의 전체 이력 원천."""
        c = self._conn()
        c.execute("INSERT INTO drafts(content_hash,team,model,version,item_meta,quality_meta,ts) "
                  "VALUES(?,?,?,?,?,?,?) "
                  "ON CONFLICT(content_hash,team,model,version) DO UPDATE SET "
                  "item_meta=excluded.item_meta, quality_meta=excluded.quality_meta, ts=excluded.ts",
                  (content_hash, team or "", model or "", int(version or 1),
                   json.dumps(item_meta or {}, ensure_ascii=False),
                   json.dumps(quality_meta or {}, ensure_ascii=False), time.time()))
        c.commit()

    def draft_times(self, team=None) -> dict:
        """콘텐츠별 최신 초안 생성 시각(epoch) · '현재 초안 이후 검수' 유효성 판정 원천."""
        c = self._conn()
        return {ch: float(ts or 0) for ch, ts in c.execute(
            "SELECT content_hash, MAX(ts) FROM drafts WHERE team=? GROUP BY content_hash",
            (team or "",))}

    # ── 콘텐츠별 검수 담당 배정(배타적 노출 · 진척 개인화) ─────────────────
    def set_assignees(self, content_hash, reviewers, min_reviewers=1, team=None):
        """콘텐츠 검수 담당자 배정(교체) + 최소 검수인원 N 설정.
        reviewers=[] (빈 목록)이면 배정·N 모두 해제(오픈 큐로 복귀)."""
        c = self._conn()
        tm = team or ""
        c.execute("DELETE FROM assignments WHERE content_hash=? AND team=?", (content_hash, tm))
        now = time.time()
        rvs = [r for r in dict.fromkeys(reviewers or []) if r]   # 중복 제거·순서 보존
        for rv in rvs:
            c.execute("INSERT INTO assignments(content_hash,reviewer,team,ts) VALUES(?,?,?,?)",
                      (content_hash, rv, tm, now))
        if rvs:
            n = max(1, min(len(rvs), int(min_reviewers or 1)))   # N 은 배정 인원 이하로 클램프
            c.execute("INSERT INTO assignment_cfg(content_hash,team,min_reviewers) VALUES(?,?,?) "
                      "ON CONFLICT(content_hash,team) DO UPDATE SET min_reviewers=excluded.min_reviewers",
                      (content_hash, tm, n))
        else:
            c.execute("DELETE FROM assignment_cfg WHERE content_hash=? AND team=?", (content_hash, tm))
        c.commit()

    def clear_assignees(self, content_hash, team=None):
        self.set_assignees(content_hash, [], team=team)

    def set_assignees_bulk(self, hashes, reviewers, min_reviewers=1, team=None) -> int:
        """여러 콘텐츠에 같은 담당자·N 을 일괄 배정(덮어쓰기). 단일 트랜잭션.
        reviewers=[] 이면 대상 전체 배정 해제. 반환=처리한 콘텐츠 수."""
        c = self._conn()
        tm = team or ""
        hs = [h for h in dict.fromkeys(hashes or []) if h]       # 중복 제거
        rvs = [r for r in dict.fromkeys(reviewers or []) if r]
        n = max(1, min(len(rvs), int(min_reviewers or 1))) if rvs else 1
        now = time.time()
        for h in hs:
            c.execute("DELETE FROM assignments WHERE content_hash=? AND team=?", (h, tm))
            c.execute("DELETE FROM assignment_cfg WHERE content_hash=? AND team=?", (h, tm))
            for rv in rvs:
                c.execute("INSERT INTO assignments(content_hash,reviewer,team,ts) VALUES(?,?,?,?)",
                          (h, rv, tm, now))
            if rvs:
                c.execute("INSERT INTO assignment_cfg(content_hash,team,min_reviewers) VALUES(?,?,?)",
                          (h, tm, n))
        c.commit()
        return len(hs)

    def assignees(self, team=None) -> dict:
        """콘텐츠별 배정 현황 {hash: {"reviewers":[...], "min":N}} · 큐·진척 산정 주입용.
        배정된 콘텐츠만 키로 포함(미배정 콘텐츠는 오픈 큐)."""
        c = self._conn()
        tm = team or ""
        out = {}
        for ch, rv in c.execute(
                "SELECT content_hash,reviewer FROM assignments WHERE team=? ORDER BY rowid", (tm,)):
            out.setdefault(ch, {"reviewers": [], "min": 1})["reviewers"].append(rv)
        for ch, n in c.execute("SELECT content_hash,min_reviewers FROM assignment_cfg WHERE team=?", (tm,)):
            if ch in out:
                out[ch]["min"] = max(1, min(len(out[ch]["reviewers"]), int(n or 1)))
        return out

    def assignment_times(self, team=None) -> dict:
        """(content_hash, reviewer) → 배정 시각(epoch) · 검수운영의 정체 일수 산정용.
        '언제 배정됐는지'가 있어야 '며칠째 손 안 댔는지'를 말할 수 있다(crewops)."""
        c = self._conn()
        return {(ch, rv): float(ts or 0) for ch, rv, ts in c.execute(
            "SELECT content_hash,reviewer,ts FROM assignments WHERE team=?", (team or "",))}

    def assignment_load(self, team=None) -> dict:
        """검수자별 미완료 배정 부하 {reviewer: n} · 균등 분배 배정의 가중 원천.
        부하 = 배정됐지만 그 검수자가 아직 판정하지 않은 콘텐츠 수(완료분은 부하 아님)."""
        c = self._conn()
        tm = team or ""
        done = {(ch, rv) for ch, rv in c.execute(
            "SELECT content_hash,reviewer FROM feedback WHERE verdict IN ('good','bad')")}
        out = {}
        for ch, rv in c.execute(                      # 고아 배정(삭제된 콘텐츠)은 부하 아님(분배 왜곡 방지)
                "SELECT a.content_hash, a.reviewer FROM assignments a "
                "JOIN results r ON r.content_hash = a.content_hash WHERE a.team=?", (tm,)):
            if (ch, rv) not in done:
                out[rv] = out.get(rv, 0) + 1
        return out

    def draft_history(self, content_hash: str, team=None, limit: int = 20) -> list:
        c = self._conn()
        rows = c.execute("SELECT model,version,item_meta,quality_meta,ts FROM drafts "
                         "WHERE content_hash=? AND team=? ORDER BY ts DESC LIMIT ?",
                         (content_hash, team or "", int(limit))).fetchall()
        out = []
        for m, v, im, qm, ts in rows:
            def _load(s):
                try:
                    return json.loads(s or "{}")
                except Exception:
                    return {}
            out.append({"model": m or "", "version": int(v or 1),
                        "item_meta": _load(im), "quality_meta": _load(qm), "ts": ts})
        return out

    def save_eval_check(self, content_hash, reviewer, verdict, expected="", got="", team=None) -> bool:
        """평가 불일치 건 판정 upsert(1인 1표 · 재판정 허용). verdict: adopt|reject."""
        if verdict not in ("adopt", "reject") or not content_hash:
            return False
        c = self._conn()
        c.execute("INSERT INTO eval_checks(content_hash,reviewer,verdict,expected,got,ts) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(content_hash,reviewer) DO UPDATE SET verdict=excluded.verdict, "
                  "expected=excluded.expected, got=excluded.got, ts=excluded.ts",
                  (content_hash, reviewer or "(익명)", verdict, expected or "", got or "", time.time()))
        c.commit()
        return True

    def eval_check_counts(self, team=None) -> dict:
        """{hash: {adopt, reject, reviewers:{reviewer: verdict}}} · 합의 판단 원천."""
        c = self._conn()
        out = {}
        for ch, rv, v in c.execute("SELECT content_hash,reviewer,verdict FROM eval_checks"):
            d = out.setdefault(ch, {"adopt": 0, "reject": 0, "reviewers": {}})
            d[v] = d.get(v, 0) + 1
            d["reviewers"][rv] = v
        return out

    def menu_perms(self, team=None) -> dict:
        """로컬(sqlite) 단독 = 전체 접근이라 메뉴 권한 매트릭스는 무의미 → {}."""
        return {}

    def set_menu_perms(self, team, perms) -> bool:
        return True

    def clear_team_feedback(self, team=None):
        """평가 피드백 전체 삭제(로컬 단일 팀). 시스템 설정 · 데이터 관리."""
        c = self._conn()
        c.execute("DELETE FROM feedback")
        c.commit()

    def clear_team_contents(self, team=None):
        """검토 콘텐츠(추출 결과) 전체 삭제(로컬 단일 팀). 초안 이력·배정도 함께 비운다.
        (배정을 남기면 고아 배정이 진척 분모·'내 담당' 수를 오염)"""
        c = self._conn()
        c.execute("DELETE FROM results")
        c.execute("DELETE FROM drafts")
        c.execute("DELETE FROM assignments")
        c.execute("DELETE FROM assignment_cfg")
        c.commit()

    def remove_content(self, content_hash: str, team=None) -> bool:
        """콘텐츠 개별 삭제(관리자): 결과 + 파생(초안 이력·용도·검수 피드백·평가 판정) 연쇄 삭제.
        골든(확정 정답)과 patch_log(교정 이력·DPO 원천)는 보존한다."""
        h = (content_hash or "").strip()
        if not h:
            return False
        c = self._conn()
        cur = c.execute("DELETE FROM results WHERE content_hash=?", (h,))
        c.execute("DELETE FROM drafts WHERE content_hash=?", (h,))
        c.execute("DELETE FROM content_purpose WHERE content_hash=?", (h,))
        c.execute("DELETE FROM feedback WHERE content_hash=?", (h,))
        c.execute("DELETE FROM eval_checks WHERE content_hash=?", (h,))
        c.execute("DELETE FROM assignments WHERE content_hash=?", (h,))     # 유령 배정 잔존 시 팀 진척 분모 오염
        c.execute("DELETE FROM assignment_cfg WHERE content_hash=?", (h,))
        c.commit()
        return cur.rowcount > 0

    def set_purpose(self, hashes, purpose, team=None) -> int:
        """콘텐츠 용도 지정: review(검수용)|eval(평가용). 평가용은 검수 대상에서 제외(홀드아웃 보존)."""
        if purpose not in ("review", "eval"):
            return 0
        hs = [h for h in (hashes or []) if h]
        if not hs:
            return 0
        c = self._conn()
        now = time.time()
        c.executemany("INSERT INTO content_purpose(content_hash,purpose,ts) VALUES(?,?,?) "
                      "ON CONFLICT(content_hash) DO UPDATE SET purpose=excluded.purpose, ts=excluded.ts",
                      [(h, purpose, now) for h in hs])
        c.commit()
        return len(hs)

    def purpose_map(self, team=None) -> dict:
        """{content_hash: purpose}. 미지정은 review 취급(호출부 기본값)."""
        c = self._conn()
        return {h: p for h, p in c.execute("SELECT content_hash,purpose FROM content_purpose")}

    def save_routes(self, content_hash, reviewer, items, team=None, model=""):
        """오케스트레이터 재분류 결과 append(초안 생성 모델 귀속 포함)."""
        c = self._conn()
        if "model" not in [r[1] for r in c.execute("PRAGMA table_info(feedback_routes)")]:
            c.execute("ALTER TABLE feedback_routes ADD COLUMN model TEXT")
        now = time.time()
        c.executemany("INSERT INTO feedback_routes(content_hash,reviewer,element,stage,directive,model,ts) VALUES(?,?,?,?,?,?,?)",
                      [(content_hash, reviewer or "(익명)", it.get("element", ""), it.get("stage", "analyze"),
                        it.get("directive", ""), model or "", now) for it in items if it.get("directive")])
        c.commit()

    def routes_by_stage(self, limit_per_stage: int = 20, team=None, exclude=None) -> dict:
        """공통(모델 미기록) 라우트만 · 모델 귀속 라우트는 routes_by_stage_model 로
        해당 모델 프롬프트에만 병기한다(타 모델 오염·중복 방지).
        exclude: 관리자가 끈 지시 원문 집합 · 다음 컴파일부터 제외(원본 행은 보존)."""
        c = self._conn()
        out = {}
        seen = set()
        ex = exclude or set()
        try:
            rows = c.execute("SELECT stage,directive FROM feedback_routes "
                             "WHERE COALESCE(directive,'')!='' AND COALESCE(model,'')='' "
                             "ORDER BY ts DESC").fetchall()
        except sqlite3.OperationalError:               # 구 스키마(model 컬럼 없음)
            rows = c.execute("SELECT stage,directive FROM feedback_routes "
                             "WHERE COALESCE(directive,'')!='' ORDER BY ts DESC").fetchall()
        for stage, directive in rows:
            st = stage if stage in ("extract", "analyze", "review", "judge") else "analyze"
            d = directive.strip()
            if d in ex:
                continue
            if (st, d) in seen:                        # 동일 지시 반복 제거(표시·프롬프트 병기 모두)
                continue
            seen.add((st, d))
            lst = out.setdefault(st, [])
            if len(lst) < limit_per_stage:
                lst.append(d)
        return out

    def routes_by_stage_model(self, limit_per_stage: int = 20, team=None, exclude=None) -> dict:
        """모델 귀속 라우트: {model: {stage: [directive, …]}} · 모델별 learned 계층의 원천."""
        c = self._conn()
        out = {}
        seen = set()
        ex = exclude or set()
        try:
            rows = c.execute("SELECT stage,directive,COALESCE(model,'') FROM feedback_routes "
                             "WHERE COALESCE(directive,'')!='' AND COALESCE(model,'')!='' "
                             "ORDER BY ts DESC").fetchall()
        except sqlite3.OperationalError:
            return {}
        for stage, directive, model in rows:
            st = stage if stage in ("extract", "analyze", "review", "judge") else "analyze"
            d, m = directive.strip(), model.strip()
            if d in ex:
                continue
            if (m, st, d) in seen:
                continue
            seen.add((m, st, d))
            lst = out.setdefault(m, {}).setdefault(st, [])
            if len(lst) < limit_per_stage:
                lst.append(d)
        return out

    def learned_by_stage(self, limit_per_stage: int = 20, team=None, exclude=None) -> dict:
        """단계별 학습 보정 텍스트: 오케스트레이터 라우팅(요소 재분류 지시) 우선 + REAP plan/메모 보완.
        team 은 통일용(sqlite 무시) · exclude 는 관리자가 끈 지시 원문(라우트·메모 공통 제외)."""
        c = self._conn()
        ex = exclude or set()
        out = {"extract": [], "analyze": [], "review": [], "judge": []}
        routed = self.routes_by_stage(limit_per_stage, exclude=ex)
        for st, items in routed.items():
            out[st].extend(f"- {t}" for t in items)
        for stage, note, plan in c.execute(
                "SELECT stage,note,plan FROM feedback "
                "WHERE verdict='bad' AND (COALESCE(plan,'')!='' OR COALESCE(note,'')!='') "
                "ORDER BY ts DESC"):
            st = stage if stage in out else "analyze"
            text = (plan or "").strip() or (note or "").strip()
            if text in ex:
                continue
            line = f"- {text}"
            if text and len(out[st]) < limit_per_stage and line not in out[st]:
                out[st].append(line)
        return {k: "\n".join(v) for k, v in out.items() if v}

    def feedback_stats(self, team=None) -> dict:
        c = self._conn()
        n = int(c.execute("SELECT COUNT(*) FROM feedback").fetchone()[0])
        bad = int(c.execute("SELECT COUNT(*) FROM feedback WHERE verdict='bad'").fetchone()[0])
        good = int(c.execute("SELECT COUNT(*) FROM feedback WHERE verdict='good'").fetchone()[0])
        learned = int(c.execute("SELECT COUNT(*) FROM feedback WHERE verdict='bad' "
                                "AND (COALESCE(plan,'')!='' OR COALESCE(note,'')!='')").fetchone()[0])
        contents = int(c.execute("SELECT COUNT(DISTINCT content_hash) FROM feedback").fetchone()[0])
        reviewers = int(c.execute("SELECT COUNT(DISTINCT reviewer) FROM feedback").fetchone()[0])
        # 불일치: 한 콘텐츠에 good·bad 가 모두 달린 건수(팀 합의 점검용)
        split = int(c.execute("""SELECT COUNT(*) FROM (
            SELECT content_hash FROM feedback WHERE verdict IN('good','bad')
            GROUP BY content_hash
            HAVING COUNT(DISTINCT verdict) > 1)""").fetchone()[0])
        return {"total": n, "good": good, "bad": bad, "learned": learned,
                "contents": contents, "reviewers": reviewers, "split": split}

    def set_reviewer(self, reviewer, name=None, avatar="boksil"):
        """검수자 등록/갱신. sqlite 는 검수자 키=이름(name 인자는 supabase 와 시그니처 통일용)."""
        c = self._conn()
        c.execute("""INSERT INTO reviewers(reviewer,char,ts) VALUES(?,?,?)
          ON CONFLICT(reviewer) DO UPDATE SET char=excluded.char, ts=excluded.ts""",
          (reviewer or "(익명)", avatar or "boksil", time.time()))
        c.commit()

    def reviewers_map(self, team=None) -> dict:
        """reviewer → char(아바타 id)."""
        c = self._conn()
        return {rv: (ch or "boksil") for rv, ch in c.execute("SELECT reviewer,char FROM reviewers")}

    # ── 게시판(기능개선·오류 제보 · 팀 스코프) ─────────────────────────────
    def board_add(self, kind, title, body, reviewer, team=None) -> int:
        c = self._conn()
        cur = c.execute("INSERT INTO board(team,kind,title,body,reviewer,status,ts) VALUES(?,?,?,?,?,?,?)",
                        (team or "", kind, title, body, reviewer or "(익명)", "open", time.time()))
        c.commit()
        return cur.lastrowid

    def board_list(self, team=None, limit: int = 200) -> list:
        c = self._conn()
        rows = c.execute("SELECT id,kind,title,body,reviewer,status,ts,answer,answered_at FROM board "
                         "WHERE team=? ORDER BY id DESC LIMIT ?", (team or "", int(limit)))
        return [{"id": r[0], "kind": r[1], "title": r[2], "body": r[3],
                 "author_id": r[4], "status": r[5], "ts": r[6],
                 "answer": r[7] or "", "answered_at": r[8] or 0} for r in rows]

    def board_get(self, bid: int, team=None):
        c = self._conn()
        r = c.execute("SELECT id,kind,title,body,reviewer,status,ts,answer,answered_at FROM board WHERE id=? AND team=?",
                      (int(bid), team or "")).fetchone()
        return ({"id": r[0], "kind": r[1], "title": r[2], "body": r[3],
                 "author_id": r[4], "status": r[5], "ts": r[6],
                 "answer": r[7] or "", "answered_at": r[8] or 0} if r else None)

    def board_answer(self, bid: int, answer: str, team=None) -> bool:
        """게시판 글에 관리자 답변 저장(문의 응답)."""
        c = self._conn()
        n = c.execute("UPDATE board SET answer=?, answered_at=? WHERE id=? AND team=?",
                      (answer or "", time.time(), int(bid), team or "")).rowcount
        c.commit()
        return n > 0

    def board_set_status(self, bid: int, status: str, team=None) -> bool:
        c = self._conn()
        n = c.execute("UPDATE board SET status=? WHERE id=? AND team=?",
                      (status, int(bid), team or "")).rowcount
        c.commit()
        return bool(n)

    def board_delete(self, bid: int, team=None) -> bool:
        c = self._conn()
        n = c.execute("DELETE FROM board WHERE id=? AND team=?", (int(bid), team or "")).rowcount
        c.commit()
        return bool(n)

    def rename_reviewer(self, old: str, new: str) -> dict:
        """닉네임 변경(로컬): 검수자 키=이름이므로 이력 테이블의 키를 함께 이관."""
        c = self._conn()
        if c.execute("SELECT 1 FROM reviewers WHERE reviewer=?", (new,)).fetchone():
            return {"ok": False, "error": "이미 사용 중인 닉네임입니다"}
        moved = 0
        for t in ("reviewers", "feedback", "patch_log", "gold_checks",
                  "events", "eval_checks", "feedback_routes",
                  "assignments", "board"):                # 배정(배타 큐 노출 키)·게시글 작성자도 이관
            moved += c.execute(f"UPDATE {t} SET reviewer=? WHERE reviewer=?", (new, old)).rowcount
        c.commit()
        return {"ok": True, "moved": moved}

    # ── 평가 런(이력) · Atelier eval_runs 이식 · supastore 와 동일 계약 ──────
    def eval_run_create(self, team, model, scope, total, created_by="") -> int:
        c = self._conn()
        cur = c.execute("INSERT INTO eval_runs(team,model,scope,status,cursor,total,created_by,ts) "
                        "VALUES(?,?,?,?,0,?,?,?)",
                        (team or "", model or "", scope or "all", "running",
                         int(total), created_by or "", time.time()))
        c.commit()
        return int(cur.lastrowid)

    def eval_run_update(self, run_id, team=None, **fields):
        """부분 갱신(status·cursor·total·metrics·error·finished·rubric_*). json 필드는 직렬화."""
        sets, vals = [], []
        for k in ("status", "cursor", "total", "error", "finished",
                  "rubric_status", "rubric_cursor"):
            if k in fields:
                sets.append(f"{k}=?")
                vals.append(fields[k])
        for k in ("metrics", "rubric"):
            if k in fields:
                sets.append(f"{k}=?")
                vals.append(json.dumps(fields[k], ensure_ascii=False))
        if not sets:
            return
        vals.append(int(run_id))
        c = self._conn()
        c.execute(f"UPDATE eval_runs SET {', '.join(sets)} WHERE id=?", vals)
        c.commit()

    def _eval_run_row(self, r) -> dict:
        def _j(v):
            try:
                return json.loads(v) if v else None
            except Exception:
                return None
        return {"id": r[0], "model": r[2] or "", "scope": r[3] or "all", "status": r[4] or "",
                "cursor": int(r[5] or 0), "total": int(r[6] or 0), "metrics": _j(r[7]),
                "error": r[8] or "", "created_by": r[9] or "", "ts": r[10], "finished": r[11],
                "rubric_status": r[12] or "", "rubric_cursor": int(r[13] or 0), "rubric": _j(r[14])}

    _EVAL_RUN_COLS = ("id,team,model,scope,status,cursor,total,metrics,error,created_by,"
                      "ts,finished,rubric_status,rubric_cursor,rubric")

    def eval_run_get(self, run_id, team=None):
        c = self._conn()
        r = c.execute(f"SELECT {self._EVAL_RUN_COLS} FROM eval_runs WHERE id=?",
                      (int(run_id),)).fetchone()
        return self._eval_run_row(r) if r else None

    def eval_runs_list(self, team=None, limit=20) -> list:
        c = self._conn()
        return [self._eval_run_row(r) for r in c.execute(
            f"SELECT {self._EVAL_RUN_COLS} FROM eval_runs ORDER BY id DESC LIMIT ?", (int(limit),))]

    def eval_results_add(self, run_id, rows, team=None):
        """건별 결과 일괄 upsert(재개 시 같은 건 재실행돼도 안전)."""
        if not rows:
            return
        c = self._conn()
        now = time.time()
        c.executemany(
            "INSERT INTO eval_results(run_id,content_hash,title,expected,got,passed,error,ts) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id,content_hash) DO UPDATE SET "
            "title=excluded.title, expected=excluded.expected, got=excluded.got, "
            "passed=excluded.passed, error=excluded.error, ts=excluded.ts",
            [(int(run_id), r.get("hash") or "", r.get("title") or "",
              json.dumps(r.get("expected"), ensure_ascii=False),
              json.dumps(r.get("got"), ensure_ascii=False),
              int(bool(r.get("passed"))), r.get("error") or "", now) for r in rows])
        c.commit()

    def eval_results_list(self, run_id, team=None, only_fail=False, limit=2000) -> list:
        c = self._conn()
        q = ("SELECT content_hash,title,expected,got,passed,error,rubric FROM eval_results "
             "WHERE run_id=?" + (" AND passed=0" if only_fail else "") + " LIMIT ?")
        out = []
        for r in c.execute(q, (int(run_id), int(limit))):
            def _j(v):
                try:
                    return json.loads(v) if v else None
                except Exception:
                    return None
            out.append({"hash": r[0], "title": r[1] or "", "expected": _j(r[2]),
                        "got": _j(r[3]), "passed": bool(r[4]), "error": r[5] or "",
                        "rubric": _j(r[6])})
        return out

    def eval_results_missing_rubric(self, run_id, team=None, limit=2000) -> list:
        """루브릭 미채점 건(hash·expected·got) · 재실행 시 남은 건만 채점하는 원천."""
        c = self._conn()
        out = []
        for r in c.execute("SELECT content_hash,expected,got FROM eval_results "
                           "WHERE run_id=? AND rubric IS NULL LIMIT ?", (int(run_id), int(limit))):
            def _j(v):
                try:
                    return json.loads(v) if v else None
                except Exception:
                    return None
            out.append({"hash": r[0], "expected": _j(r[1]), "got": _j(r[2])})
        return out

    def eval_result_rubric_set(self, run_id, content_hash, rubric, team=None):
        c = self._conn()
        c.execute("UPDATE eval_results SET rubric=? WHERE run_id=? AND content_hash=?",
                  (json.dumps(rubric, ensure_ascii=False), int(run_id), content_hash or ""))
        c.commit()

    def eval_result_hashes(self, run_id, team=None) -> set:
        c = self._conn()
        return {r[0] for r in c.execute(
            "SELECT content_hash FROM eval_results WHERE run_id=?", (int(run_id),))}

    # ── 오토파일럿 런 · Atelier autopilot 이식 · supastore 와 동일 계약 ─────
    def autopilot_create(self, team, target, max_rounds, created_by="") -> int:
        c = self._conn()
        cur = c.execute("INSERT INTO autopilot_runs(team,status,target,max_rounds,round,created_by,ts) "
                        "VALUES(?,?,?,?,0,?,?)",
                        (team or "", "running", float(target), int(max_rounds),
                         created_by or "", time.time()))
        c.commit()
        return int(cur.lastrowid)

    def autopilot_update(self, run_id, team=None, **fields):
        sets, vals = [], []
        for k in ("status", "round", "start_accuracy", "best_accuracy", "last_accuracy",
                  "stop_reason", "error", "heartbeat", "finished"):
            if k in fields:
                sets.append(f"{k}=?")
                vals.append(fields[k])
        if "history" in fields:
            sets.append("history=?")
            vals.append(json.dumps(fields["history"], ensure_ascii=False))
        if not sets:
            return
        vals.append(int(run_id))
        c = self._conn()
        c.execute(f"UPDATE autopilot_runs SET {', '.join(sets)} WHERE id=?", vals)
        c.commit()

    _PILOT_COLS = ("id,team,status,target,max_rounds,round,start_accuracy,best_accuracy,"
                   "last_accuracy,history,stop_reason,error,created_by,ts,heartbeat,finished")

    def _pilot_row(self, r) -> dict:
        try:
            history = json.loads(r[9]) if r[9] else []
        except Exception:
            history = []
        return {"id": r[0], "status": r[2] or "", "target": r[3], "max_rounds": int(r[4] or 0),
                "round": int(r[5] or 0), "start_accuracy": r[6], "best_accuracy": r[7],
                "last_accuracy": r[8], "history": history, "stop_reason": r[10] or "",
                "error": r[11] or "", "created_by": r[12] or "", "ts": r[13],
                "heartbeat": r[14], "finished": r[15]}

    def autopilot_latest(self, team=None):
        c = self._conn()
        r = c.execute(f"SELECT {self._PILOT_COLS} FROM autopilot_runs "
                      "ORDER BY id DESC LIMIT 1").fetchone()
        return self._pilot_row(r) if r else None

    # ── 프롬프트 라이브러리 · Atelier prompt_library 이식 · supastore 동일 계약 ─
    def lib_add(self, team, name, domain, prompt, note="", source="manual",
                created_by="") -> int:
        c = self._conn()
        cur = c.execute("INSERT INTO prompt_library(team,name,domain,prompt,note,source,"
                        "created_by,ts) VALUES(?,?,?,?,?,?,?,?)",
                        (team or "", name or "", domain or "", prompt or "",
                         note or "", source or "manual", created_by or "", time.time()))
        c.commit()
        return int(cur.lastrowid)

    def lib_list(self, team=None, limit=200) -> list:
        c = self._conn()
        return [{"id": r[0], "name": r[1] or "", "domain": r[2] or "",
                 "prompt": r[3] or "", "note": r[4] or "", "source": r[5] or "",
                 "pinned": bool(r[6]), "ts": r[7]}
                for r in c.execute("SELECT id,name,domain,prompt,note,source,pinned,ts "
                                   "FROM prompt_library ORDER BY pinned DESC, id DESC "
                                   "LIMIT ?", (int(limit),))]

    def lib_remove(self, lib_id, team=None) -> bool:
        c = self._conn()
        n = c.execute("DELETE FROM prompt_library WHERE id=?", (int(lib_id),)).rowcount
        c.commit()
        return bool(n)

    def lib_pin(self, lib_id, pinned, team=None) -> bool:
        c = self._conn()
        n = c.execute("UPDATE prompt_library SET pinned=? WHERE id=?",
                      (int(bool(pinned)), int(lib_id))).rowcount
        c.commit()
        return bool(n)

    # ── 프롬프트 배포 · Atelier deployments 이식 · supastore 와 동일 계약 ───
    def _deploy_row(self, r) -> dict:
        return {"id": r[0], "team": r[1] or "", "slug": r[2] or "", "name": r[3] or "",
                "version": int(r[4] or 0), "active": bool(r[5]),
                "created_by": r[6] or "", "ts": r[7], "updated": r[8]}

    _DEPLOY_COLS = "id,team,slug,name,version,active,created_by,ts,updated"

    def deploy_save(self, team, dep_id=None, slug="", name="", version=0,
                    active=True, created_by="") -> int:
        c = self._conn()
        now = time.time()
        if dep_id:
            c.execute("UPDATE deployments SET slug=?, name=?, version=?, active=?, updated=? "
                      "WHERE id=?", (slug, name, int(version), int(bool(active)), now, int(dep_id)))
            c.commit()
            return int(dep_id)
        cur = c.execute("INSERT INTO deployments(team,slug,name,version,active,created_by,ts,updated) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (team or "", slug, name, int(version), int(bool(active)),
                         created_by or "", now, now))
        c.commit()
        return int(cur.lastrowid)

    def deploy_get(self, dep_id, team=None):
        c = self._conn()
        r = c.execute(f"SELECT {self._DEPLOY_COLS} FROM deployments WHERE id=?",
                      (int(dep_id),)).fetchone()
        return self._deploy_row(r) if r else None

    def deploy_by_slug(self, slug):
        c = self._conn()
        r = c.execute(f"SELECT {self._DEPLOY_COLS} FROM deployments WHERE slug=?",
                      (slug or "",)).fetchone()
        return self._deploy_row(r) if r else None

    def deploys_list(self, team=None) -> list:
        c = self._conn()
        return [self._deploy_row(r) for r in c.execute(
            f"SELECT {self._DEPLOY_COLS} FROM deployments ORDER BY id DESC")]

    def deploy_remove(self, dep_id, team=None) -> bool:
        c = self._conn()
        n = c.execute("DELETE FROM deployments WHERE id=?", (int(dep_id),)).rowcount
        c.execute("DELETE FROM deployment_keys WHERE deployment_id=?", (int(dep_id),))
        c.commit()
        return bool(n)

    def deploy_key_add(self, dep_id, key_hash, key_prefix) -> int:
        c = self._conn()
        cur = c.execute("INSERT INTO deployment_keys(deployment_id,key_hash,key_prefix,ts) "
                        "VALUES(?,?,?,?)", (int(dep_id), key_hash, key_prefix, time.time()))
        c.commit()
        return int(cur.lastrowid)

    def deploy_keys_for(self, dep_id, meta_only=False) -> list:
        c = self._conn()
        out = []
        for r in c.execute("SELECT id,key_hash,key_prefix,revoked,ts,last_used "
                           "FROM deployment_keys WHERE deployment_id=? ORDER BY id", (int(dep_id),)):
            row = {"id": r[0], "prefix": r[2] or "", "revoked": bool(r[3]),
                   "ts": r[4], "last_used": r[5]}
            if not meta_only:
                row["hash"] = r[1] or ""
            out.append(row)
        return out

    def deploy_key_revoke(self, key_id, dep_id) -> bool:
        c = self._conn()
        n = c.execute("UPDATE deployment_keys SET revoked=1 WHERE id=? AND deployment_id=?",
                      (int(key_id), int(dep_id))).rowcount
        c.commit()
        return bool(n)

    def deploy_key_touch(self, key_id):
        c = self._conn()
        c.execute("UPDATE deployment_keys SET last_used=? WHERE id=?", (time.time(), int(key_id)))
        c.commit()

    def yellow_hashes(self, team=None) -> set:
        """검수 대상(YELLOW) 해시 집합 · 진척율/퀘스트의 분자·분모가 공유하는 모집단.
        json_extract 미지원 빌드는 전체 해시로 폴백(yellow_count 와 동일 계약)."""
        c = self._conn()
        try:
            return {r[0] for r in c.execute(
                "SELECT content_hash FROM results WHERE json_extract(payload,'$.quality_meta.review')='yellow'")}
        except Exception:
            return {r[0] for r in c.execute("SELECT content_hash FROM results")}

    def yellow_count(self) -> int:
        """검수 대상(YELLOW) 총량. json_extract 미지원 빌드는 전체 수로 폴백."""
        return len(self.yellow_hashes())

    def review_targets(self, team=None) -> set:
        """진척율·퀘스트의 모집단 = 현재 YELLOW ∪ (배정된 살아있는 콘텐츠).
        일괄 배정 운영은 자동통과(auto) 콘텐츠도 배정해 검수시키므로 배정분이 곧 팀의
        검수 목표다. 삭제된 콘텐츠의 고아 배정은 제외(분모 오염 방지)."""
        c = self._conn()
        live = {r[0] for r in c.execute("SELECT content_hash FROM results")}
        assigned = set(self.assignees(team) or {})
        return self.yellow_hashes() | (assigned & live)

    def target_models(self, team=None) -> list:
        """검수 대상(YELLOW) 초안을 생성한 모델 목록(중복 제거 · 퀘스트 카드 provenance)."""
        c = self._conn()
        try:
            rows = c.execute(
                "SELECT payload FROM results WHERE json_extract(payload,'$.quality_meta.review')='yellow'")
        except Exception:
            rows = c.execute("SELECT payload FROM results")
        out = []
        for (payload,) in rows:
            try:
                m = ((json.loads(payload) if payload else {}).get("trace") or {}).get("model", "") or ""
            except Exception:
                m = ""
            if m and m not in out:
                out.append(m)
        return out

    def arena_stats(self, target: float = 0.9, team=None) -> dict:
        """평가 아레나(게임화) 지표 · 품질 가중.
        점수 = (검수 10 + 교정 25 + 구조화 교정 5 + 합의 일치 5 + 골드 응답 10
                + 최종판정 40) × 품질 배율 + 미션 보너스.
        품질 배율 = 0.5 + 0.5 × 골드 정확도(응답 5건 이상일 때, 그 외 1.0). [Oleson 2011 · Snow 2008]"""
        c = self._conn()
        DAY = 86400.0
        now = time.time()
        week_ago = now - 7 * DAY
        prev_ago = now - 14 * DAY                     # 지난주 창(리그 승급/강등 비교)
        today = int(now // DAY)
        good = bad = wk_good = wk_bad = pv_good = pv_bad = 0
        board = {}
        days_by = {}
        by_content = {}                               # 합의·불일치 산정용 {hash: [(reviewer, verdict)]}
        reviewed_pairs = set()                        # (hash, reviewer) · 담당 진척 산정용
        for ch, rv, verdict, plan, ts in c.execute("SELECT content_hash,reviewer,verdict,plan,ts FROM feedback"):
            rv = rv or "(익명)"
            b = board.setdefault(rv, {"reviews": 0, "corrections": 0,
                                      "wk_reviews": 0, "wk_corr": 0, "pv_reviews": 0, "pv_corr": 0})
            b["reviews"] += 1
            reviewed_pairs.add((ch, rv))
            g, d = (verdict == "good"), (verdict == "bad")
            if g or d:
                by_content.setdefault(ch, []).append((rv, verdict))
            t = ts or 0
            this_wk = t >= week_ago
            last_wk = week_ago > t >= prev_ago
            if this_wk:
                b["wk_reviews"] += 1
            elif last_wk:
                b["pv_reviews"] += 1
            if g:
                good += 1
            elif d:
                bad += 1
                if (plan or "").strip():
                    b["corrections"] += 1            # 채택된 개선(REAP plan) = 가산점
                    if this_wk:
                        b["wk_corr"] += 1
                    elif last_wk:
                        b["pv_corr"] += 1
            if t >= week_ago:
                wk_good += int(g); wk_bad += int(d)
            else:
                pv_good += int(g); pv_bad += int(d)
            days_by.setdefault(rv, set()).add(int(t // DAY))
        total = good + bad
        accuracy = round(good / total, 4) if total else 0.0
        pv_total = pv_good + pv_bad
        pv_acc = round(pv_good / pv_total, 4) if pv_total else accuracy

        # 합의 일치·불일치 참여·합의 대비 일치율(n>=2 콘텐츠만) [von Ahn 2004 · Dawid-Skene 1979 근사]
        cons_match, split_part, agree_hit, agree_n = {}, {}, {}, {}
        for ch, votes in by_content.items():
            if len(votes) < 2:
                continue
            gn = sum(1 for _, v in votes if v == "good")
            bn = len(votes) - gn
            cons = "good" if gn > bn else ("bad" if bn > gn else "split")
            for rv, v in votes:
                others_g = gn - (1 if v == "good" else 0)
                others_b = bn - (1 if v == "bad" else 0)
                if others_g and others_b:            # 남들 의견이 갈린 콘텐츠에 참여 = 불일치 재검토
                    split_part[rv] = split_part.get(rv, 0) + 1
                if cons != "split":
                    agree_n[rv] = agree_n.get(rv, 0) + 1
                    if v == cons:
                        agree_hit[rv] = agree_hit.get(rv, 0) + 1
                        cons_match[rv] = cons_match.get(rv, 0) + 1

        def _streak(days):
            d = today
            if d not in days and (d - 1) not in days:
                return 0
            if d not in days:
                d -= 1                               # 오늘 미검수면 어제부터 인정
            s = 0
            while d in days:
                s += 1; d -= 1
            return s

        chars = self.reviewers_map()
        targets = self.review_targets(team)              # 검수 대상 = YELLOW ∪ 배정(분자·분모 공통 모집단)
        total_targets = len(targets)
        tgt_done = {}                                    # 검수자 → 현재 검수 대상 중 검수한 건수
        for ch, rv in reviewed_pairs:
            if ch in targets:
                tgt_done[rv] = tgt_done.get(rv, 0) + 1
        gold = self.gold_stats()
        patches = self.patch_counts()
        bonuses = self.event_bonus()
        gcontrib = self.golden_contrib_counts()          # 골든 확정 기여(가시화·배지 · 점수는 이벤트 보너스로 지급)
        fin_n, fin_wk, fin_pv = final_verdict_counts(self, team)   # 최종판정(2층) · 점수 산입

        # 담당 배정: 개인 진척 분모 = 내 담당 콘텐츠 수, 완료 = 내가 검수한 담당 콘텐츠 수
        # 삭제된 콘텐츠의 고아 배정은 제외(분모·'내 담당' 수 오염 방지)
        asg = {ch: a for ch, a in (self.assignees(team) or {}).items() if ch in targets}
        mine_total, mine_done = {}, {}
        for ch, a in asg.items():
            for rv in a["reviewers"]:
                mine_total[rv] = mine_total.get(rv, 0) + 1
                if (ch, rv) in reviewed_pairs:
                    mine_done[rv] = mine_done.get(rv, 0) + 1

        def _prog(rv):
            denom = mine_total.get(rv)                   # 배정 있는 검수자 → 개인 분모
            if denom:
                return round(mine_done.get(rv, 0) / denom, 4)
            rc = tgt_done.get(rv, 0)                     # 미배정 → 현재 검수 대상 중 검수한 건수(누적 아님)
            return round(min(rc, total_targets) / total_targets, 4) if total_targets else 0.0

        def _mult(rv):
            gs = gold.get(rv) or {}
            return round(0.5 + 0.5 * gs["acc"], 4) if gs.get("n", 0) >= 5 else 1.0

        leaderboard = []
        # 보너스(적립·초기화 오프셋)만 있는 검수자도 포함: 피드백 전체 삭제 후에도 보존 점수가 보이게
        # 최종판정만 한 검수자(기초 피드백 0)도 포함 — 없으면 리더보드에서 아예 빠진다
        ids = set(board) | {k for k, b in bonuses.items() if k and (b or {}).get("total")} | set(fin_n)
        for rv in ids:
            v = board.get(rv) or {"reviews": 0, "corrections": 0,
                                  "wk_reviews": 0, "wk_corr": 0, "pv_reviews": 0, "pv_corr": 0}
            gs = gold.get(rv) or {"n": 0, "acc": 0.0}
            mult = _mult(rv)
            base = (v["reviews"] * 10 + v["corrections"] * 25 + patches.get(rv, 0) * 5
                    + cons_match.get(rv, 0) * 5 + gs["n"] * 10
                    + fin_n.get(rv, 0) * FINAL_VERDICT_POINTS)
            # 초기화 오프셋(음수 이벤트)로 합이 음수가 될 수 있어 0 하한(레벨·리그 표시 정합)
            pts = max(0, round(base * mult) + (bonuses.get(rv) or {}).get("total", 0))
            wk_base = (v["wk_reviews"] * 10 + v["wk_corr"] * 25
                       + fin_wk.get(rv, 0) * FINAL_VERDICT_POINTS)
            pv_base = (v["pv_reviews"] * 10 + v["pv_corr"] * 25
                       + fin_pv.get(rv, 0) * FINAL_VERDICT_POINTS)
            leaderboard.append({"reviewer": rv, "reviewer_id": rv, "reviews": v["reviews"],
                                "corrections": v["corrections"], "points": pts,
                                "level": level_of(pts), "streak": _streak(days_by.get(rv, set())),
                                "char": chars.get(rv, "boksil"), "progress": _prog(rv),
                                # 홈 히어로 캡션용: 현재 검수 대상 기준(누적 reviews 와 분리)
                                "target_reviews": tgt_done.get(rv, 0),
                                "assigned_total": mine_total.get(rv, 0),
                                "assigned_done": mine_done.get(rv, 0),
                                "week_points": max(0, round(wk_base * mult) + (bonuses.get(rv) or {}).get("week", 0)),
                                "last_week_points": round(pv_base * mult),
                                "gold_n": gs["n"], "gold_acc": gs["acc"], "quality_mult": mult,
                                "consensus_matches": cons_match.get(rv, 0),
                                "split_reviews": split_part.get(rv, 0),
                                "patches": patches.get(rv, 0),
                                "golden_contribs": gcontrib.get(rv, 0),
                                "final_verdicts": fin_n.get(rv, 0),
                                "agree_rate": (round(agree_hit.get(rv, 0) / agree_n[rv], 4)
                                               if agree_n.get(rv) else None)})
        leaderboard.sort(key=lambda x: -x["points"])
        if asg:
            # 배정 기준 팀 진척 = Σ 콘텐츠별 min(검수인원, N)/N ÷ 배정 콘텐츠 수(부분 크레딧 합산)
            tot = 0.0
            for ch, a in asg.items():
                n = a["min"] or 1
                done = sum(1 for rv in a["reviewers"] if (ch, rv) in reviewed_pairs)
                tot += min(done, n) / n
            team_progress = round(tot / len(asg), 4)
        else:
            members = set(board.keys()) | set(chars.keys())  # 검수 이력 없는 팀원도 평균에 포함
            team_progress = round(sum(_prog(m) for m in members) / len(members), 4) if (members and total_targets) else 0.0
        return {"accuracy": accuracy, "good": good, "bad": bad, "reviews": total,
                "week_reviews": wk_good + wk_bad, "accuracy_delta": round(accuracy - pv_acc, 4),
                "target": target, "leaderboard": leaderboard,
                "total_targets": total_targets, "team_progress": team_progress}

    def review_queue(self, limit: int = 100, only_unreviewed: bool = True, team=None, reviewer=None,
                     see_all: bool = False) -> list:
        """검수 대기 큐: YELLOW(사람검수 티어) 콘텐츠.
        정렬 = 모델 확신 낮은 순(불확실성 샘플링, Lewis & Gale 1994) → 최신순.
        only_unreviewed 여도 의견이 갈린(split) 콘텐츠는 재검토 대상으로 포함(Aroyo & Welty 2015).
        배정된 콘텐츠(assignees)는 담당자에게만 노출(배타적) · 담당자는 자기가 아직 검수 안 한 것만 봄.
        see_all=True(생성자·슈퍼관리자)는 배정 배타 규칙을 우회해 남의 담당 콘텐츠도 큐에 노출한다."""
        c = self._conn()
        reviewed = {r[0] for r in c.execute("SELECT DISTINCT content_hash FROM feedback")}
        asg = self.assignees(team)                    # {hash: {"reviewers", "min"}} · 배정 콘텐츠만
        mine = set()                                  # 이 검수자가 이미 판정한 콘텐츠
        if reviewer:
            mine = {r[0] for r in c.execute(
                "SELECT content_hash FROM feedback WHERE reviewer=?", (reviewer,))}
        split = set()                                 # good·bad 공존 콘텐츠(조정 필요)
        for (ch,) in c.execute("""SELECT content_hash FROM feedback WHERE verdict IN('good','bad')
                GROUP BY content_hash HAVING COUNT(DISTINCT verdict) > 1"""):
            split.add(ch)
        out = []
        for ch, svc, ti, grade, payload, ts in c.execute(
                "SELECT content_hash,service,title,final_grade,payload,created_at "
                "FROM results ORDER BY created_at DESC LIMIT ?", (max(limit * 6, 200),)):
            try:
                qm = (json.loads(payload) if payload else {}).get("quality_meta") or {}
            except Exception:
                qm = {}
            if (qm.get("review") or "") != "yellow":
                continue
            is_reviewed = ch in reviewed
            is_split = ch in split
            a = asg.get(ch)
            if a and not see_all:                     # 배정 콘텐츠 = 담당자 전용(배타적) · 생성자는 예외
                if not reviewer or reviewer not in a["reviewers"]:
                    continue                          # 담당 아님(또는 미인증) → 숨김
                if only_unreviewed and ch in mine and not is_split:
                    continue                          # 내 몫은 이미 검수함
            elif only_unreviewed and is_reviewed and not is_split:
                continue                              # 미배정 오픈 큐 · 생성자 전체 열람도 검수완료분은 동일 규칙
            conf = qm.get("confidence")
            try:
                model = ((json.loads(payload) if payload else {}).get("trace") or {}).get("model", "") or ""
            except Exception:
                model = ""
            out.append({"hash": ch, "service": svc or "", "title": ti or "",
                        "grade": grade or "", "review_reason": qm.get("review_reason", ""),
                        "reviewed": is_reviewed, "split": is_split, "model": model,
                        "confidence": conf, "ts": ts,
                        "assignees": (a or {}).get("reviewers", []),
                        "min_reviewers": (a or {}).get("min", 0)})
            if len(out) >= limit * 2:                 # 정렬 전 여유 수집
                break
        # split 재검토 우선 → 저확신 순 → 최신순
        out.sort(key=lambda r: (0 if r["split"] else 1,
                                r["confidence"] if isinstance(r.get("confidence"), (int, float)) else 1.0,
                                -(r["ts"] or 0)))
        return out[:limit]

    def contents_by_hash(self, team=None, limit: int = 5000) -> dict:
        """content_hash → 콘텐츠 dict(학습데이터 추출용). sqlite payload 에 body 가 없으면 빈 값."""
        c = self._conn()
        out = {}
        for ch, svc, ti, payload in c.execute(
                "SELECT content_hash,service,title,payload FROM results ORDER BY created_at DESC LIMIT ?",
                (int(limit),)):
            body = ""
            try:
                ref = (json.loads(payload) if payload else {}).get("content_ref") or {}
                body = ref.get("body", "") or ""
            except Exception:
                pass
            out[ch] = {"displayServiceName": svc or "", "title": ti or "", "subtitle": "", "body": body}
        return out

    def get_item_meta(self, content_hash) -> dict | None:
        """저장된 item_meta 조회(교정 로그의 before 스냅샷용). 없으면 None."""
        c = self._conn()
        row = c.execute("SELECT item_meta FROM results WHERE content_hash=?", (content_hash,)).fetchone()
        if not row:
            return None
        try:
            v = json.loads(row[0]) if row[0] else {}
        except Exception:
            v = {}
        return v if isinstance(v, dict) else {}

    def clear_feedback(self):
        c = self._conn()
        c.execute("DELETE FROM feedback"); c.commit()

    def update_item_meta(self, content_hash, patch: dict) -> bool:
        """검수자 구조화 교정: item_meta 패치(예: 빈 content_category 채우기).
        recent() 가 payload 를 읽으므로 item_meta 컬럼 + payload.item_meta 둘 다 갱신."""
        c = self._conn()
        row = c.execute("SELECT item_meta,payload FROM results WHERE content_hash=?", (content_hash,)).fetchone()
        if not row:
            return False

        def _load(s):
            try:
                v = json.loads(s) if s else {}
            except Exception:
                v = {}
            return v if isinstance(v, dict) else {}

        im = _load(row[0]); im.update(patch or {})
        pl = _load(row[1])
        pim = pl.get("item_meta"); pim = pim if isinstance(pim, dict) else {}
        pim.update(patch or {}); pl["item_meta"] = pim
        c.execute("UPDATE results SET item_meta=?, payload=? WHERE content_hash=?",
                  (json.dumps(im, ensure_ascii=False), json.dumps(pl, ensure_ascii=False), content_hash))
        c.commit()
        return True

    def update_quality(self, content_hash, grade: str, reasons=None):
        """최종검수자 등급 교정: final_grade 컬럼 + payload.quality_meta 동시 갱신.
        반환 = 이전 등급 문자열(행 없으면 None) · patch_log 의 교정 전/후 기록용."""
        c = self._conn()
        row = c.execute("SELECT final_grade,payload FROM results WHERE content_hash=?", (content_hash,)).fetchone()
        if not row:
            return None
        prev = row[0] or ""
        try:
            pl = json.loads(row[1]) if row[1] else {}
        except Exception:
            pl = {}
        pl = pl if isinstance(pl, dict) else {}
        qm = pl.get("quality_meta")
        qm = qm if isinstance(qm, dict) else {}
        qm["finalGrade"] = grade
        if reasons is not None:
            qm["reasons"] = reasons
        pl["quality_meta"] = qm
        if reasons is not None:
            c.execute("UPDATE results SET final_grade=?, reasons=?, payload=? WHERE content_hash=?",
                      (grade, json.dumps(reasons, ensure_ascii=False), json.dumps(pl, ensure_ascii=False), content_hash))
        else:
            c.execute("UPDATE results SET final_grade=?, payload=? WHERE content_hash=?",
                      (grade, json.dumps(pl, ensure_ascii=False), content_hash))
        c.commit()
        return prev

    # ── 골든셋(검수 확정 정답셋 · 누적) ──
    def upsert_golden(self, content_hash, content, expected, team=None, source="review"):
        """골든 엔트리 upsert(누적). content_hash 키 · source = review(검수 유래)|manual(관리자 등록)."""
        c = self._conn()
        c.execute("""INSERT INTO golden(content_hash,content,expected,ts,source) VALUES(?,?,?,?,?)
          ON CONFLICT(content_hash) DO UPDATE SET content=excluded.content, expected=excluded.expected,
            ts=excluded.ts, source=excluded.source""",
          (content_hash, json.dumps(content, ensure_ascii=False), json.dumps(expected, ensure_ascii=False),
           time.time(), source or "review"))
        c.commit()

    def register_golden(self, team, rows, replace=True, source="manual"):
        """골든셋 등록. replace=True 면 전체 교체, False 면 기존에 병합(upsert).
        rows: [{content, expected}]. content_hash 로 키."""
        c = self._conn()
        if replace:
            c.execute("DELETE FROM golden")
            c.commit()
        n = 0
        for r in rows:
            if r.get("content") and r.get("expected"):
                self.upsert_golden(content_hash(r["content"]), r["content"], r["expected"], source=source)
                n += 1
        return n

    def get_golden(self, team=None, limit=1000):
        c = self._conn()
        out = []
        for content, expected in c.execute("SELECT content,expected FROM golden LIMIT ?", (int(limit),)):
            try:
                out.append({"content": json.loads(content), "expected": json.loads(expected)})
            except Exception:
                pass
        return out

    def golden_hashes(self, team=None) -> set:
        c = self._conn()
        return {r[0] for r in c.execute("SELECT content_hash FROM golden")}

    def golden_rows(self, team=None, limit=300) -> list:
        """관리자 골든 브라우저용: 제목·등급·카테고리·출처·시각."""
        c = self._conn()
        out = []
        for ch, content, expected, ts, src in c.execute(
                "SELECT content_hash,content,expected,ts,source FROM golden ORDER BY ts DESC LIMIT ?",
                (int(limit),)):
            try:
                ct = json.loads(content) if content else {}
                ex = json.loads(expected) if expected else {}
            except Exception:
                continue
            out.append({"hash": ch, "title": ct.get("title", ""), "service": ct.get("displayServiceName", ""),
                        "grade": ex.get("finalGrade", ""), "category": ex.get("content_category", []) or [],
                        "source": src or "review", "ts": ts})
        return out

    def golden_source_counts(self, team=None) -> dict:
        c = self._conn()
        return {(src or "review"): n for src, n in
                c.execute("SELECT source, COUNT(*) FROM golden GROUP BY source")}

    def remove_golden(self, content_hash, team=None) -> bool:
        c = self._conn()
        cur = c.execute("DELETE FROM golden WHERE content_hash=?", (content_hash,))
        c.commit()
        return cur.rowcount > 0

    def golden_count(self, team=None):
        c = self._conn()
        return c.execute("SELECT COUNT(*) FROM golden").fetchone()[0]

    def clear_golden(self, team=None):
        c = self._conn()
        c.execute("DELETE FROM golden"); c.commit()

    def golden_contrib_counts(self, team=None) -> dict:
        """reviewer → 골든 확정 기여 수(events kind='golden:*', 확정 1회 보상 기록 기반)."""
        c = self._conn()
        return {rv: n for rv, n in c.execute(
            "SELECT reviewer, COUNT(*) FROM events WHERE kind LIKE 'golden:%' GROUP BY reviewer")}

    # ── 엔티티 사전 ──────────────────────────────────────────────────────
    _ENT_JSON = ("attrs", "attr_meta", "external_ids")

    def _ent_row(self, r) -> dict:
        e = {"entity_id": r[0], "name": r[1], "type": r[2] or "", "status": r[3] or "pending",
             "attrs": r[4], "attr_meta": r[5], "external_ids": r[6],
             "merged_into": r[7] or "", "created_at": r[8], "updated_at": r[9]}
        for k in self._ENT_JSON:
            try:
                e[k] = json.loads(e[k]) if e[k] else {}
            except (TypeError, ValueError):
                e[k] = {}
        return e

    _ENT_SEL = "SELECT entity_id,name,type,status,attrs,attr_meta,external_ids,merged_into,created_at,updated_at FROM entities"

    def ent_upsert(self, e: dict):
        c = self._conn()
        c.execute("""INSERT INTO entities(entity_id,name,type,status,attrs,attr_meta,external_ids,merged_into,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(entity_id) DO UPDATE SET
            name=excluded.name, type=excluded.type, status=excluded.status,
            attrs=excluded.attrs, attr_meta=excluded.attr_meta, external_ids=excluded.external_ids,
            merged_into=excluded.merged_into, updated_at=excluded.updated_at""",
          (e["entity_id"], e.get("name", ""), e.get("type", ""), e.get("status", "pending"),
           json.dumps(e.get("attrs") or {}, ensure_ascii=False),
           json.dumps(e.get("attr_meta") or {}, ensure_ascii=False),
           json.dumps(e.get("external_ids") or {}, ensure_ascii=False),
           e.get("merged_into", ""), e.get("created_at") or time.time(),
           e.get("updated_at") or time.time()))
        c.commit()

    def ent_update(self, entity_id: str, fields: dict) -> bool:
        """부분 갱신. dict 필드는 JSON 직렬화 · 없는 개체는 False."""
        allowed = ("name", "type", "status", "attrs", "attr_meta", "external_ids",
                   "merged_into", "updated_at")
        sets, vals = [], []
        for k in allowed:
            if k not in fields:
                continue
            v = fields[k]
            sets.append(f"{k}=?")
            vals.append(json.dumps(v, ensure_ascii=False) if k in self._ENT_JSON else v)
        if not sets:
            return False
        c = self._conn()
        cur = c.execute(f"UPDATE entities SET {','.join(sets)} WHERE entity_id=?", (*vals, entity_id))
        c.commit()
        return cur.rowcount > 0

    def ent_get(self, entity_id: str):
        c = self._conn()
        r = c.execute(self._ENT_SEL + " WHERE entity_id=?", (entity_id,)).fetchone()
        return self._ent_row(r) if r else None

    def ent_id_by_alias(self, name: str) -> str:
        c = self._conn()
        r = c.execute("SELECT entity_id FROM entity_aliases WHERE alias=?", (name,)).fetchone()
        return r[0] if r else ""

    def ent_alias_add(self, alias: str, entity_id: str):
        c = self._conn()
        c.execute("INSERT OR IGNORE INTO entity_aliases(alias,entity_id) VALUES(?,?)", (alias, entity_id))
        c.commit()

    def ent_aliases(self, entity_id: str) -> list:
        c = self._conn()
        return [a for a, in c.execute("SELECT alias FROM entity_aliases WHERE entity_id=? ORDER BY alias",
                                      (entity_id,))]

    def ent_link(self, content_hash, entity_id, surface="", team=None):
        c = self._conn()
        c.execute("""INSERT OR IGNORE INTO content_entities(content_hash,entity_id,surface,team,ts)
                     VALUES(?,?,?,?,?)""", (content_hash, entity_id, surface, team or "", time.time()))
        c.commit()

    def ent_list(self, q: str = "", type_: str = "", status: str = "", limit: int = 300) -> list:
        """목록(+콘텐츠 등장 수). q 는 이름·별칭 부분일치.
        status: ''=미등재 제외(기본) · 'all'=전부 · 그 외 해당 상태만.
        미등재(unlisted) 조회는 등장 수 내림차순 — 다빈도 미등재 = 진짜 개체(수동 확정 후보)."""
        c = self._conn()
        cond, vals = [], []
        if q:
            cond.append("(name LIKE ? OR entity_id IN (SELECT entity_id FROM entity_aliases WHERE alias LIKE ?))")
            vals += [f"%{q}%", f"%{q}%"]
        if type_:
            cond.append("type=?"); vals.append(type_)
        if status and status != "all":
            cond.append("status=?"); vals.append(status)
        elif not status:
            cond.append("status<>'unlisted'")              # 기본 목록에서 미등재 분리
        where = (" WHERE " + " AND ".join(cond)) if cond else ""
        rows = [self._ent_row(r) for r in c.execute(
            self._ENT_SEL + where + " ORDER BY updated_at DESC LIMIT ?", (*vals, int(limit)))]
        if rows:
            ids = [e["entity_id"] for e in rows]
            ph = ",".join("?" * len(ids))
            counts = {eid: n for eid, n in c.execute(
                f"SELECT entity_id, COUNT(DISTINCT content_hash) FROM content_entities WHERE entity_id IN ({ph}) GROUP BY entity_id", ids)}
            for e in rows:
                e["n_contents"] = counts.get(e["entity_id"], 0)
            if status == "unlisted":
                rows.sort(key=lambda e: -e["n_contents"])
        return rows

    def ent_trending(self, hours: int = 48, limit: int = 8, team=None) -> list:
        """언급 급증 엔티티 [{id,name,recent,prev}]: 최근 hours시간 vs 그 전 같은 창 비교.
        추천 토픽 카드의 원천 · 최소 2건 + 증가분 있는 것만 · 증가폭 내림차순."""
        c = self._conn()
        now = time.time()
        cut1 = now - hours * 3600.0
        cut0 = now - 2 * hours * 3600.0
        rec, prev = {}, {}
        for eid, surface, ts in c.execute(
                "SELECT entity_id, surface, ts FROM content_entities WHERE ts>=? AND team=?",
                (cut0, team or "")):
            b = rec if (ts or 0) >= cut1 else prev
            e = b.setdefault(eid, {"n": 0, "surface": surface or eid})
            e["n"] += 1
        names = {}
        if rec:
            ids = list(rec)
            ph = ",".join("?" * len(ids))
            names = {i: n for i, n in c.execute(
                f"SELECT entity_id, name FROM entities WHERE entity_id IN ({ph})", ids)}
        out = []
        for eid, e in rec.items():
            pv = (prev.get(eid) or {}).get("n", 0)
            if e["n"] >= 2 and e["n"] > pv:
                out.append({"id": eid, "name": names.get(eid) or e["surface"],
                            "recent": e["n"], "prev": pv})
        out.sort(key=lambda x: (-(x["recent"] - x["prev"]), -x["recent"]))
        return out[:max(1, int(limit))]

    def ent_stats(self) -> dict:
        c = self._conn()
        total = c.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        by_type = {t or "(보류)": n for t, n in c.execute("SELECT type, COUNT(*) FROM entities GROUP BY type")}
        pending = c.execute("SELECT COUNT(*) FROM entities WHERE status='pending'").fetchone()[0]
        unlisted = c.execute("SELECT COUNT(*) FROM entities WHERE status='unlisted'").fetchone()[0]
        enriched = c.execute("SELECT COUNT(*) FROM entities WHERE external_ids LIKE '%wikidata%' OR external_ids LIKE '%namuwiki%'").fetchone()[0]
        links = c.execute("SELECT COUNT(*) FROM content_entities").fetchone()[0]
        return {"total": total, "byType": by_type, "pending": pending, "unlisted": unlisted,
                "enriched": enriched, "links": links}

    def ent_mark_unlisted(self) -> int:
        """기존 데이터 정규화(1회성): 보강 미스 기록이 있는 보류 개체 → 미등재로 이행."""
        c = self._conn()
        cur = c.execute("""UPDATE entities SET status='unlisted'
                           WHERE status='pending' AND attr_meta LIKE '%"result": "miss"%'""")
        c.commit()
        return cur.rowcount

    def ent_purge_unlisted(self) -> int:
        """미등재 일괄 정리(링크·별칭 포함 삭제) · 관리자 버튼."""
        c = self._conn()
        ids = [r[0] for r in c.execute("SELECT entity_id FROM entities WHERE status='unlisted'")]
        for eid in ids:
            c.execute("DELETE FROM entities WHERE entity_id=?", (eid,))
            c.execute("DELETE FROM entity_aliases WHERE entity_id=?", (eid,))
            c.execute("DELETE FROM content_entities WHERE entity_id=?", (eid,))
        c.commit()
        return len(ids)

    def ent_pending_ids(self, limit: int = 200) -> list:
        """보강 대상: 위키데이터 조회 이력(_enrich) 자체가 없는 개체(미스 기록은 재조회 제외)."""
        c = self._conn()
        return [r[0] for r in c.execute(
            "SELECT entity_id FROM entities WHERE attr_meta IS NULL OR attr_meta NOT LIKE '%_enrich%' ORDER BY created_at LIMIT ?",
            (int(limit),))]

    def ent_ids(self, limit: int = 5000) -> list:
        """전체 개체 id(등록 순) · 전체 재보강 대상."""
        c = self._conn()
        return [r[0] for r in c.execute("SELECT entity_id FROM entities ORDER BY created_at LIMIT ?",
                                        (int(limit),))]

    def ent_by_names(self, names) -> dict:
        """{표기(별칭 포함): 개체 dict} · 검수 화면에서 콘텐츠 엔티티 → 사전 정보 표시용."""
        out = {}
        for n in list(dict.fromkeys(names or []))[:50]:
            eid = self.ent_id_by_alias(" ".join(str(n or "").split()))
            if eid:
                e = self.ent_get(eid)
                if e:
                    out[n] = e
        return out

    def ent_delete(self, entity_id: str) -> bool:
        c = self._conn()
        cur = c.execute("DELETE FROM entities WHERE entity_id=?", (entity_id,))
        c.execute("DELETE FROM entity_aliases WHERE entity_id=?", (entity_id,))
        c.execute("DELETE FROM content_entities WHERE entity_id=?", (entity_id,))
        c.commit()
        return cur.rowcount > 0

    def ent_attr_index(self, team=None) -> dict:
        """{content_hash: [개체 속성 dict(type·name 포함), …]} · 토픽 엔티티 속성 조건의 원천."""
        c = self._conn()
        ents = {}
        for r in c.execute(self._ENT_SEL):
            e = self._ent_row(r)
            ents[e["entity_id"]] = {"type": e["type"], "name": e["name"], **(e["attrs"] or {})}
        out = {}
        # falsy team = 전역(무팀 필터 없음) · supastore·recent 과 동일 규칙(운영 팀 링크 누락 방지)
        q = "SELECT content_hash, entity_id FROM content_entities"
        params = ()
        if team:
            q += " WHERE team=?"
            params = (team,)
        for ch, eid in c.execute(q, params):
            if eid in ents:
                out.setdefault(ch, []).append(ents[eid])
        return out

    def ent_contents(self, entity_id: str, limit: int = 50) -> list:
        """개체가 등장하는 콘텐츠(제목·등급) · 사전 상세 팝업용."""
        c = self._conn()
        return [{"hash": ch, "title": t or "", "grade": g or ""} for ch, t, g in c.execute(
            """SELECT ce.content_hash, r.title, r.final_grade FROM content_entities ce
               LEFT JOIN results r ON r.content_hash = ce.content_hash
               WHERE ce.entity_id=? ORDER BY ce.ts DESC LIMIT ?""", (entity_id, int(limit)))]

    # runs / usage
    def start_run(self, run_id, n, config):
        c = self._conn()
        c.execute("INSERT OR REPLACE INTO runs(run_id,started_at,n,config) VALUES(?,?,?,?)",
                  (run_id, time.time(), n, json.dumps(config, ensure_ascii=False)))
        c.commit()

    def finish_run(self, run_id, metrics):
        c = self._conn()
        c.execute("UPDATE runs SET finished_at=?, metrics=? WHERE run_id=?",
                  (time.time(), json.dumps(metrics, ensure_ascii=False), run_id))
        c.commit()

    def log_usage(self, kind, n, cost, tin, tout):
        c = self._conn()
        c.execute("INSERT INTO usage(ts,kind,n,cost_usd,tokens_in,tokens_out) VALUES(?,?,?,?,?,?)",
                  (time.time(), kind, n, cost, tin, tout))
        c.commit()

    def usage_since(self, since_ts=0):
        c = self._conn()
        return list(c.execute(
            "SELECT ts,kind,n,cost_usd,tokens_in,tokens_out FROM usage WHERE ts>=? ORDER BY ts",
            (since_ts,)))
