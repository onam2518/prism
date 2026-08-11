"""프롬프트 외부화 + 버전관리."""
from __future__ import annotations
import functools
import hashlib
import json
import os
import sys
import tempfile
import threading

from . import dictionaries as D

HOME = os.path.dirname(os.path.dirname(__file__))
PROMPTS_DIR = os.path.join(HOME, "prompts")
QUALITY_PATH = os.path.join(PROMPTS_DIR, "quality.json")

# 저장·시드 직렬화. 서버는 ThreadingHTTPServer(요청당 스레드) · 평가는 ThreadPoolExecutor 라
# 콜드스타트에 여러 스레드가 동시에 시드를 시도한다. 종전에는 open(w) 로 truncate 한 사이에
# 다른 스레드가 0바이트 파일을 읽어 JSONDecodeError 가 났다(20회 중 12회 재현 · 2026-08-11).
# RLock 인 이유: ensure_seeded → _reseed_builtins → _save 가 같은 스레드에서 중첩된다.
_LOCK = threading.RLock()
_SEEDED = set()                 # 이 프로세스에서 시드를 확인한 경로(핫패스에서 제외)


# 기본 시드 (v31 = 현행, 코드에서 추출)
def _seed_v31() -> dict:
    return {
        "intro": ("너는 콘텐츠 2차 품질 필터다. 입력 콘텐츠가 유통 가능(G)인지 불가(R)인지 판정한다.\n"
                  "서비스 그룹: {service_group}."),
        "meta_rules": dict(D.QUALITY_METAS),   # 메타별 짧은 정의
        "procedure": ("[판정 절차: 증거 먼저, 답 나중]\n"
                      "1. 본문/제목에서 각 메타의 트리거 근거를 먼저 수집한다(없으면 없음으로 둔다).\n"
                      "2. 트리거가 임계(노골적/반복적/유통부적합)를 충족하면 해당 메타를 reasons 에 넣는다.\n"
                      "3. reasons 가 1개 이상이면 finalGrade=R, 0개면 finalGrade=G.\n"
                      "4. 복수 메타는 동시 부여가 원칙(발동한 메타는 모두 reasons 에 포함). "
                      "단일 대표를 골라야 하는 경계 케이스만 상황부 우선 규칙을 따른다: {priority}."),
        "consistency": "[정합성] reasons 가 비면 finalGrade 는 반드시 G, 1개 이상이면 반드시 R.",
        "format": ('[출력 형식]\n'
                   '{{"finalGrade": "G|R", "reasons": ["메타ID", ...], "evidence": "근거 한 줄"}}'),
    }


# v32 = ad/spam 보강 (PromptTuner=강한 메타모델 개정, 실패진단 반영)
def _seed_v32() -> dict:
    v = _seed_v31()
    v["meta_rules"] = dict(v["meta_rules"])
    v["meta_rules"]["ad"] = (
        "노골적 광고뿐 아니라 '기사형 홍보(네이티브 광고)'도 포함. 트리거: "
        "①특정 기업/브랜드의 신제품·솔루션·서비스 '공개/출시/선보임' 홍보, "
        "②분양·청약·재건축의 '프리미엄·조망·세대수' 등 분양 셀링포인트 강조, "
        "③브랜드 화보·시즌 컬렉션·라인업 소개, "
        "④자사 실적·수상·기술력을 홍보 어조로 나열, "
        "⑤구매·가입·예약·이용·다운로드 유도 멘트나 링크. "
        "제외(=ad 아님): 정부·공공 정책, 단순 CSR·MOU 사실 보도, 스포츠 선수 근황, "
        "객관적 시장·종목 분석(홍보 어조 없이 사실·수치 중심), 사건·사고 보도."
    )
    v["meta_rules"]["spam"] = (
        "반복 도배뿐 아니라 '저품질 감정 후킹·감성 스토리텔링'도 포함. 트리거: "
        "①'충격/발칵/오열/소름/경악' 등 감정 자극어로 시작하거나 제목-본문이 사연 나열로 끌기, "
        "②호기심 갭('의외의 이 음식', '충격적인 이유', '결국 ~한 사연') 뒤 알맹이가 빈약, "
        "③개인 사연·미담을 감정적으로 늘어놓아 공유·반응을 유도, "
        "④AI 생성 흔적이 본문에 노출('undefined', '기사를 작성합니다', '수집된 정보를 바탕으로' 등 메타텍스트), "
        "⑤'공유 부탁/꼭 보세요' 류 확산 유도. "
        "제외: 출처·근거가 분명하고 정보가치가 충분한 정상 기사·후기."
    )
    v["meta_rules"]["gambling"] = (
        "복권·로또·토토·베팅·카지노 유도/참여 권유. 트리거: 당첨번호·판매점·조합 '기대감 자극', "
        "베팅 참여 권유, 사행 수익 인증. 제외: 당첨 사실의 단순 보도, 도박 폐해 경고·정책 기사."
    )
    return v


# 코드가 소유하는 내장 버전. 사용자 튜닝은 new_from 으로 만든 별도 키에 하고,
# 내장 버전(v31·v32)은 시드 변경 시 코드 기준으로 재동기화된다.
_BUILTIN_SEEDS = {"v31": _seed_v31, "v32": _seed_v32}


@functools.lru_cache(maxsize=1)
def _seed_stamp() -> str:
    """내장 시드 내용에서 파생한 지문. 시드를 고치면 값이 자동으로 달라져,
    기존 설치의 quality.json 도 다음 로드 때 재시드된다(수동 버전 범프 불필요)."""
    payload = json.dumps({n: fn() for n, fn in _BUILTIN_SEEDS.items()},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _default_data() -> dict:
    return {"active": "v31", "seed_stamp": _seed_stamp(),
            "versions": {n: fn() for n, fn in _BUILTIN_SEEDS.items()}}


def _reseed_builtins(data: dict):
    """내장 버전만 최신 코드 시드로 덮어쓴다. active 선택과 사용자 생성 버전은 보존."""
    versions = data.setdefault("versions", {})
    for name, fn in _BUILTIN_SEEDS.items():
        versions[name] = fn()
    if data.get("active") not in versions:   # active 가 사라졌으면 기본으로 복귀
        data["active"] = "v31"
    data["seed_stamp"] = _seed_stamp()
    _save(data)


def _read_raw():
    """quality.json 읽기. 파일이 없거나 **손상**이면 None(=재시드 필요).

    손상 파일은 `.bad` 로 밀어내고 재시드한다 — 종전에는 '파일이 없을 때만' 시드해서
    쓰기 도중 죽어 절단된 파일이 영구 방치되고 이후 모든 품질 호출이 예외로 죽었다."""
    try:
        with open(QUALITY_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeDecodeError, OSError) as e:      # JSONDecodeError ⊂ ValueError
        with _LOCK:
            try:
                os.replace(QUALITY_PATH, QUALITY_PATH + ".bad")  # 원인 분석용 보존(덮어씀)
            except OSError:
                pass
        sys.stderr.write("[prism] WARNING: quality.json 손상(%s) → .bad 백업 후 재시드\n" % e)
        return None
    if not isinstance(data, dict) or not isinstance(data.get("versions"), dict):
        return None                                              # 구조 파손도 재시드 대상
    return data


def ensure_seeded():
    """파일이 없거나 손상이면 시드 생성. 있으면 시드 지문이 바뀐 경우에만 내장 버전을
    코드 시드로 재동기화한다(시드 문구 개정이 기존 설치에도 전파되도록)."""
    with _LOCK:
        os.makedirs(PROMPTS_DIR, exist_ok=True)
        data = _read_raw()
        if data is None:
            _save(_default_data())
            return
        if data.get("seed_stamp") != _seed_stamp():
            _reseed_builtins(data)


def _seed_once():
    """시드 확인은 프로세스(경로)당 1회. 종전에는 품질 콜마다 돌아 콜드스타트 경합의
    원천이었다. CLI 로 파일을 직접 고친 경우는 재시드 대상이 아니므로(내장 버전만 동기화)
    기동 1회로 충분하다 · 파일이 사라지거나 손상되면 _load 가 그 자리에서 복구한다."""
    if QUALITY_PATH in _SEEDED:
        return
    with _LOCK:
        if QUALITY_PATH in _SEEDED:
            return
        ensure_seeded()
        _SEEDED.add(QUALITY_PATH)


def _load() -> dict:
    _seed_once()
    data = _read_raw()          # os.replace 원자 교체라 '옛 파일 or 새 파일' 둘 중 하나만 보인다
    if data is None:            # 시드 후 삭제·손상 → 그 자리에서 복구(예외로 배치를 죽이지 않는다)
        with _LOCK:
            data = _read_raw()
            if data is None:
                data = _default_data()
                _save(data)
    return data


def _save(data: dict):
    """임시파일 + os.replace 원자 교체. truncate 상태를 다른 스레드가 읽는 창을 없앤다."""
    with _LOCK:
        os.makedirs(PROMPTS_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=PROMPTS_DIR, prefix=".quality-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, QUALITY_PATH)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# 공개 API
def active_name() -> str:
    return _load().get("active", "v31")


def get(version: str | None = None) -> dict:
    data = _load()
    v = version or data["active"]
    return data["versions"][v]


def list_versions() -> list:
    data = _load()
    return [(k, k == data["active"]) for k in sorted(data["versions"])]


def set_active(version: str):
    with _LOCK:                                   # 읽기-수정-쓰기 원자화(동시 편집 유실 방지)
        data = _load()
        if version not in data["versions"]:
            raise KeyError(version)
        data["active"] = version
        _save(data)



def new_from(base: str, new_version: str) -> dict:
    with _LOCK:
        data = _load()
        body = json.loads(json.dumps(data["versions"][base]))  # deep copy
        data["versions"][new_version] = body
        _save(data)
    return body


# 렌더링 (prompts.py 가 호출)
def render_quality_system(active_metas: list, service_group: str,
                          json_guard: str, version: str | None = None,
                          examples: str = "") -> str:
    body = get(version)
    rules = "\n".join(f"  - {m}: {body['meta_rules'].get(m, D.QUALITY_METAS.get(m, ''))}"
                      for m in active_metas)
    priority = D.priority_rules_text(active_metas) or "해당 규칙 없음(동시 부여 원칙만 적용)"
    # 5 Focal Elements(쿡북 Ch3): 역할(intro)·맥락(룰)·예시(few-shot)·지시(절차)·형식
    parts = [
        body["intro"].format(service_group=service_group),
        "\n[평가 대상 메타: 이 목록 밖은 절대 사용 금지]\n" + rules,
    ]
    if examples:
        parts.append("\n" + examples)
    parts += [
        "\n" + body["procedure"].format(priority=priority),
        "\n" + body["consistency"],
        "\n" + body["format"],
    ]
    return "\n".join(parts) + json_guard
