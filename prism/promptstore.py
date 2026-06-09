"""프롬프트 외부화 + 버전관리."""
from __future__ import annotations
import json
import os

from . import dictionaries as D

HOME = os.path.dirname(os.path.dirname(__file__))
PROMPTS_DIR = os.path.join(HOME, "prompts")
QUALITY_PATH = os.path.join(PROMPTS_DIR, "quality.json")


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
                      "4. 복수일 때 대표 우선순위: {priority}."),
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


def ensure_seeded():
    os.makedirs(PROMPTS_DIR, exist_ok=True)
    if not os.path.exists(QUALITY_PATH):
        data = {"active": "v31", "versions": {"v31": _seed_v31(), "v32": _seed_v32()}}
        _save(data)


def _load() -> dict:
    ensure_seeded()
    with open(QUALITY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    os.makedirs(PROMPTS_DIR, exist_ok=True)
    with open(QUALITY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    data = _load()
    if version not in data["versions"]:
        raise KeyError(version)
    data["active"] = version
    _save(data)


def save_version(version: str, body: dict, activate: bool = False):
    data = _load()
    data["versions"][version] = body
    if activate:
        data["active"] = version
    _save(data)


def new_from(base: str, new_version: str) -> dict:
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
    priority = " > ".join([p for p in D.QUALITY_PRIORITY if p in active_metas])
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
