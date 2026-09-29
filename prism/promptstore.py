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
#
# 문구 원천은 품질 정책 세 쪽이다(319783633 화이트리스트·서비스별 특화 · 278036632 공통 정량
# 기준·ad 3축·graphic 선행 · 277118998 2단계 절차). 정책이 바뀌면 여기만 고치면 두 내장 버전이
# 함께 따라온다 · _seed_stamp 지문이 달라져 기존 설치의 quality.json 도 다음 로드에 재시드된다.
def _seed_v31() -> dict:
    return {
        "intro": ("너는 콘텐츠 2차 품질 필터다. 입력 콘텐츠가 유통 가능(G)인지 불가(R)인지 판정한다.\n"
                  "서비스 그룹: {service_group}."),
        # 0순위 화이트리스트(319783633) · 11개 메타 판정보다 먼저 G 를 확정한다.
        "whitelist": ("[0순위 · 강제 G 화이트리스트: 아래 메타 판정보다 먼저 본다]\n"
                      "어느 하나에 해당하면 메타별 신호가 표면적으로 보여도 R 로 가지 않는다. "
                      'finalGrade="G" · reasons=[] 로 즉시 끝낸다.\n'
                      "- 국제분쟁·전쟁·외교 분쟁의 객관 보도(이스라엘-팔레스타인·우크라이나-러시아·"
                      "중동 정세·남중국해 분쟁 등 사실 위주 전달)\n"
                      "- 외신·국제기구 객관 인용(AP·AFP·로이터·BBC·UN·WHO·IMF·IAEA 등 출처 명시 + 인용·발화 동사 다수)\n"
                      "- 재난·사고의 단순 사실 전달(인명피해 수·발생 시각·장소·구조 진행 등 사실 위주 · 묘사 과잉 없음)\n"
                      "- 정부·공공기관·국제기구의 공식 발표(정책·공고·법안 통과·외교 성명·인사·예산)\n"
                      "- 학술·연구·통계 발표(논문·조사 결과·공식 통계 인용)\n"
                      "- 방송사·SMR 송출 영상 콘텐츠의 텍스트 부분(지상파·종편·케이블·MCN 공식 송출 · "
                      "방송사명·프로그램명·SMR 시그널이 본문에 자명할 때)\n"
                      "제외(화이트리스트를 적용하지 않고 일반 판정으로 넘긴다): 묘사 신호 2개 이상 · "
                      "강한 성적 묘사 · 한국 정당·진영명과 결합한 비방 멸칭·선동·음모론 톤 · "
                      "구매·가입·이용 권유가 본문 핵심(ad 3축 충족)."),
        # 서비스 그룹별 특화 규칙(319783633 '서비스별 특화 룰' · 278036632 화보 처리 원칙).
        # 공통 규칙과 충돌하면 공통 규칙이 이긴다.
        "group_rules": {
            "media": ("[전문 생성(PGC) 그룹 특화 규칙]\n"
                      "- format·political·hate 는 검사 자체를 하지 않고 FALSE 로 둔다. 본문·제목에 "
                      "정치인명·정당명·진영명·차별 키워드가 나와도 마찬가지다(정치·차별 표현 보도는 이 그룹의 정상 콘텐츠다).\n"
                      "- 연예 화보·포토·시상식·콘서트·티저·포스터·스틸컷은 정상 콘텐츠로 통과가 기본이며 "
                      "ad·sexual·shallow 에서 FALSE 로 둔다.\n"
                      "- 기업 실적·인사·MOU·인수합병·정책 보도는 광고성이 아니다(ad 3축 중 수익 귀속 또는 B2C 대상 미충족)."),
            "ugc": ("[사용자 생성(UGC) 그룹 특화 규칙]\n"
                    "- 11개 메타를 모두 검사한다.\n"
                    "- 단축 URL(bit.ly·t.ly·tinyurl 등)과 권유 멘트가 함께 있으면 강한 광고 신호다. "
                    "단축 URL 이 본문의 유일한 정보면 ad 3축을 모두 충족한 것으로 본다.\n"
                    "- body 가 없고 title 이 15자 미만이면 format 을 TRUE 로 둔다. title 이 충분하면 통과가 기본이다.\n"
                    "- political 은 한국 정당·정치인·진영을 비방·낙인·선동할 때만 부여한다. "
                    "사실 전달·객관 보도 옮김에는 부여하지 않는다."),
        },
        # 공통 정량 기준(278036632) · 메타별 판정이 같은 정의를 공유한다.
        "quant": ("[공통 정량 기준: 메타별 판정이 공유하는 정의]\n"
                  "- 본문 도입부: body 첫 3문장 또는 본문 분량 상위 20% 중 짧은 쪽.\n"
                  "- 본문 결론부: body 마지막 2문장 또는 본문 분량 하위 15% 중 짧은 쪽.\n"
                  "- 본문 핵심: 본문 도입부와 본문 결론부의 합집합.\n"
                  "- 신호 카운트: 그 메타의 TRUE 근거 표현이 body 에 등장한 횟수(제목 제외).\n"
                  "- 결합 조건: 강한 신호 단독 발동이 아니면 같은 문장 또는 인접 2문장 안에서 "
                  "다른 조건과 함께 나올 때만 TRUE 로 본다.\n"
                  "- 본문이 3문장 이하로 매우 짧으면 '본문 도입부 = 본문 전체'로 처리한다.\n"
                  "- 경계에서 정성 직관과 정량 기준이 충돌하면 정량 기준을 따른다."),
        "meta_rules": dict(D.QUALITY_METAS),   # 메타별 짧은 정의
        "procedure": ("[판정 절차: 증거 먼저, 답 나중]\n"
                      "0. 0순위 화이트리스트에 해당하면 여기서 끝낸다(finalGrade=G · reasons=[]).\n"
                      "1. 1단계 제목 선별: title 만으로 명확히 TRUE 인 메타를 먼저 고른다"
                      "(모든 그룹 sexual·profanity·gambling·clickbait · UGC 그룹은 format·political 추가 · "
                      "political 은 한국 정치인·정당명과 강한 비방·낙인·선동 톤이 제목에 직접 드러날 때만).\n"
                      "2. 2단계 제목+본문 정밀 판정: 1단계에서 걸리지 않은 콘텐츠는 body 까지 포함해 "
                      "각 메타의 트리거 근거를 수집한다(없으면 없음으로 둔다). body 가 비면 title 만으로 판정한다.\n"
                      "3. 본문에 묘사 신호가 2개 이상이면 ad·political·shallow 보다 graphic 을 먼저 평가해 확정한다.\n"
                      "4. 트리거가 임계(노골적/반복적/유통부적합)를 충족하면 해당 메타를 reasons 에 넣는다.\n"
                      "5. reasons 가 1개 이상이면 finalGrade=R, 0개면 finalGrade=G.\n"
                      "6. 복수 메타는 동시 부여가 원칙(발동한 메타는 모두 reasons 에 포함). "
                      "단일 대표를 골라야 하는 경계 케이스만 상황부 우선 규칙을 따른다: {priority}."),
        "consistency": "[정합성] reasons 가 비면 finalGrade 는 반드시 G, 1개 이상이면 반드시 R.",
        "format": ('[출력 형식]\n'
                   '{{"finalGrade": "G|R", "reasons": ["메타ID", ...], "evidence": "근거 한 줄"}}'),
    }


# v32 = ad/spam 보강 (PromptTuner=강한 메타모델 개정, 실패진단 반영)
def _seed_v32() -> dict:
    v = _seed_v31()
    v["meta_rules"] = dict(v["meta_rules"])
    # 4-23: 종전 v32 는 신제품 홍보·브랜드 화보·자사 실적을 그 자체로 광고 신호로 잡았다.
    # 현행 정책(278036632)은 3축 AND 이고 화보·실적 보도를 명시적 FALSE 로 둔다.
    v["meta_rules"]["ad"] = (
        "기사형 홍보(네이티브 광고)를 포함하되, ①수익 귀속(콘텐츠 생산자·의뢰자에게 직접 수익이 "
        "생기는 구조) ②명시적 CTA(구매·가입·신청·다운로드·방문·체험 등 행동 요청이 본문에 명시) "
        "③B2C 대상(행동 대상이 일반 독자) 세 축을 모두 충족할 때만 TRUE. 하나라도 빠지면 FALSE. "
        "CTA 가 자명하지 않은 경계에서는 (a)권유 멘트 3회 이상 (b)권유 멘트 1회 이상 + 쇼핑·제휴 마케팅 URL "
        "(c)권유 멘트가 본문 도입부 또는 본문 결론부에 등장 (d)권유성 문장 비중 30% 이상 중 하나를 "
        "충족해야 CTA 를 인정한다. 권유 멘트 1회만으로는 TRUE 가 되지 않는다. "
        "제외(=ad 아님): 브랜드 화보·시즌 컬렉션·시상식·컴백·콘서트·티저·포스터·스틸컷 등 연예 작품 홍보(명시 CTA 부재), "
        "자사·기업의 실적·수상·IR·인사·MOU·인수합병 등 통상 비즈니스 보도(B2C 대상 부재), "
        "정부·공공기관 정책·공고·법안 보도와 CSR·기부(수익 귀속 부재), 스포츠 선수 근황·이적, "
        "객관적 시장·종목 분석(홍보 어조 없이 사실·수치 중심), 사건·사고 보도, 공식 채널의 보도자료·발표."
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
    # 순서는 품질 정책의 양식 표준(319783633): 역할 → 0순위 화이트리스트 → 메타 → 그룹 특화
    # → 예시 → 공통 정량 기준 → 판정 절차 → 정합성 → 출력 형식.
    # whitelist·group_rules·quant 는 .get 으로 읽는다 · new_from 으로 만든 옛 사용자 버전에는 없다.
    parts = [body["intro"].format(service_group=service_group)]
    if body.get("whitelist"):
        parts.append("\n" + body["whitelist"])
    parts.append("\n[평가 대상 메타: 이 목록 밖은 절대 사용 금지]\n" + rules)
    group_rule = (body.get("group_rules") or {}).get(service_group)
    if group_rule:
        parts.append("\n" + group_rule)
    if examples:
        parts.append("\n" + examples)
    if body.get("quant"):
        parts.append("\n" + body["quant"])
    parts += [
        "\n" + body["procedure"].format(priority=priority),
        "\n" + body["consistency"],
        "\n" + body["format"],
    ]
    return "\n".join(parts) + json_guard
