"""입출력 스키마. pydantic 없이 stdlib dataclass 로: 즉시 실행 가능(의존성 0)."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import hashlib
import html as _html
import re
import unicodedata

# 제어문자(탭/개행 제외) 제거 패턴
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_text(s) -> str:
    """입력 견고성: None 안전, 유니코드 NFC, 제어문자 제거, 공백 정리."""
    if s is None:
        return ""
    s = str(s)
    s = unicodedata.normalize("NFC", s)
    s = _CTRL.sub(" ", s)
    s = re.sub(r"[ \t ]+", " ", s)        # 연속 공백 축약
    s = re.sub(r"\n{3,}", "\n\n", s)            # 과도 개행 축약
    return s.strip()


# HTML 마크업 제거 ---------------------------------------------------------
# 배경(2026-08-03 운영 400건 실측): 티스토리·다음카페 UGC 24건의 body 에 마크업·CSS·
# 이미지 URL 이 정제 없이 저장돼 있었다(최악 1건: 본문 49,039자 중 라틴 33,311자 ·
# 한글 1,895자). 마크업은 리드문·엔티티 추출을 흐리고 LLM 토큰만 먹는다 → 모델에
# 들어가기 전 정규화 단계에서 걷어낸다.
#
# 정규식 선택 이유: html.parser.HTMLParser 는 입력을 '문서' 로 가정해 평문의 부등호를
# 태그 시작으로 오인하고(`a <b 이면` → 태그 추정), 미닫힘 태그 뒤 본문을 통째로 삼킨다.
# 우리 입력의 절대다수는 평문이고 '평문 무손상' 이 1순위 요구라, 알려진 HTML 태그명이
# 실제로 나타날 때만 도는 화이트리스트 정규식을 쓴다(평문은 아예 경로를 타지 않는다).

# 태그 속성부: `이름`·`이름="값"`·`이름='값'`·`이름=값` 과 자기닫힘 `/` 만 허용한다.
# 아무 문자나 받으면 평문 `a<b 이고 b>c` 가 '속성 이고 b 를 가진 <b> 태그' 로 오인된다
# (실측 실패 케이스 · 속성명을 ASCII 로 못박아 막는다). 따옴표 안의 '>' 는 허용.
_ATTR = r"""[a-zA-Z_:][-\w:.]*(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'<>=`]+))?"""
# 끝은 `/?\s*` 한 벌만 둔다. `\s*/?\s*` 처럼 공백 별표를 두 번 겹치면 `<div` + 공백 2만개
# 입력에서 분할 경우의 수가 폭발해 O(n²)이 된다(실측 539ms · 본문은 외부 입력이라 DoS).
_ATTRS = r"(?:\s+(?:" + _ATTR + r"|/))*/?\s*"         # 끝의 `/?` = 공백 없는 `<br/>`

# 블록 경계 태그 — 개행으로 치환해 앞뒤 단어가 붙지 않게 한다.
_BLOCK_NAMES = (
    "address|article|aside|blockquote|br|caption|center|col|colgroup|dd|dir|div|dl|dt|"
    "fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|header|hr|iframe|legend|"
    "li|main|menu|nav|noframes|ol|option|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul|xmp"
)
# 인라인·기타 태그 — 제거만 하고 공백을 넣지 않는다(`<b>강</b>조` → `강조`).
_INLINE_NAMES = (
    "a|abbr|acronym|area|audio|b|base|basefont|bdi|bdo|big|blink|body|button|canvas|circle|"
    "cite|code|data|datalist|del|details|dfn|em|embed|font|g|head|html|i|img|input|ins|kbd|"
    "label|link|map|mark|marquee|meta|meter|nobr|noscript|object|optgroup|output|param|path|"
    "picture|plaintext|progress|q|rect|rp|rt|ruby|s|samp|script|select|small|source|span|"
    "strike|strong|style|sub|summary|sup|svg|template|textarea|time|title|track|tt|u|var|"
    "video|wbr"
)
# 네임스페이스 태그(`<o:p>`·`<v:shape>` 등 워드/오피스 붙여넣기 잔재)도 태그로 본다.
_NS_NAME = r"[a-zA-Z][a-zA-Z0-9]*:[a-zA-Z][a-zA-Z0-9.\-]*"
_ANY_NAME = "(?:" + _BLOCK_NAMES + "|" + _INLINE_NAMES + "|" + _NS_NAME + ")"

# 판정: 알려진 태그가 '온전한 형태' 로 나올 때만 HTML 로 본다.
# `3 < 5 이고 10 > 7`, `영화 <Parasite> 리뷰`, `a<b` 같은 평문은 여기 걸리지 않는다.
_HTML_TAG = re.compile(r"<\s*/?\s*" + _ANY_NAME + r"\b" + _ATTRS + r">", re.I)
# 속성도 슬래시도 없는 홑태그(`<p>`·`<br>`). 이것만 한둘 있는 글은 마크업이 아니라
# 'HTML 을 설명하는 평문'(예: "HTML 에서 <p> 는 문단, <br> 은 줄바꿈") 일 가능성이 크다.
_BARE_TAG = re.compile(r"\A<\s*[a-zA-Z][a-zA-Z0-9:._-]*\s*>\Z")
# 세미콜론까지 갖춘 엔티티만 인정(`&not` 처럼 반쪽짜리에 unescape 가 오작동하는 것 방지).
_ENTITY = re.compile(r"&(?:#\d{1,7}|#[xX][0-9a-fA-F]{1,6}|[a-zA-Z][a-zA-Z0-9]{1,31});")

# script·style 은 태그만 벗기면 CSS·JS 본문이 텍스트로 남는다 → 내용까지 통째로 제거.
_DROP_BLOCK = re.compile(
    r"<\s*(script|style|noscript|template)\b" + _ATTRS + r">.*?(?:<\s*/\s*\1\s*>|\Z)",
    re.I | re.S)
_CDATA = re.compile(r"<!\[CDATA\[.*?(?:\]\]>|\Z)", re.S)
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.S)
_DECL = re.compile(r"<![a-zA-Z][^<>]*>")                      # <!doctype html> 등
_BLOCK_TAG = re.compile(r"<\s*/?\s*(?:" + _BLOCK_NAMES + r")\b" + _ATTRS + r">", re.I)
_ANY_TAG = re.compile(r"<\s*/?\s*" + _ANY_NAME + r"\b" + _ATTRS + r">", re.I)
# 잘린 꼬리 태그(`… <div class="`). 여기서도 속성 문법을 지켜야 평문 꼬리를 안 먹는다.
_HALF_ATTR = r"""[a-zA-Z_:][-\w:.]*(?:\s*=\s*(?:"[^"]*|'[^']*|[^\s"'<>=`]*))?"""
_DANGLING = re.compile(r"<\s*/?\s*" + _ANY_NAME + r"(?:\s+(?:" + _HALF_ATTR + r"|/))*/?\s*\Z",
                       re.I)
_BARE_TAG_TOLERANCE = 3        # 홑태그만 이 개수 미만이면 평문으로 본다


def _looks_like_html(s: str) -> bool:
    """마크업 판정. 닫는 태그·자기닫힘·속성 있는 태그가 하나라도 있으면 즉시 마크업으로,
    홑태그(`<p>`)뿐이면 3개 이상일 때만 마크업으로 본다(HTML 설명 평문 보호)."""
    n = 0
    for m in _HTML_TAG.finditer(s):
        if not _BARE_TAG.match(m.group(0)):
            return True
        n += 1
        if n >= _BARE_TAG_TOLERANCE:
            return True
    return False


def strip_html(s) -> str:
    """HTML 마크업·엔티티 제거. **평문은 손대지 않는다**(원문 문자열 그대로 반환).

    · `<script>`·`<style>` 은 내용까지 제거(태그만 벗기면 CSS·JS 가 본문으로 남는다)
    · 블록 경계(`</p>`·`<br>`·`</div>` …)는 개행으로, 인라인 태그는 빈 문자열로 치환
    · `&nbsp;` 등 엔티티는 태그 제거 '뒤' 에 복원 — 그래야 원문이 `&lt;div&gt;` 로
      이스케이프해 둔 코드 예시가 태그로 오인돼 지워지지 않는다
    """
    if s is None:
        return ""
    s = str(s)
    if not s:
        return s
    has_tag = _looks_like_html(s)
    if not (has_tag or _ENTITY.search(s)):
        return s                                  # 평문 → 무손상 통과
    if has_tag:
        s = _DROP_BLOCK.sub(" ", s)
        s = _CDATA.sub(" ", s)
        s = _COMMENT.sub(" ", s)
        s = _DECL.sub(" ", s)
        s = _BLOCK_TAG.sub("\n", s)
        s = _ANY_TAG.sub("", s)
        s = _DANGLING.sub(" ", s)
    s = _html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t\xa0]*\n[ \t\xa0]*", "\n", s)   # 태그 자리에 남은 들여쓰기 정리
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s


def normalize_rich_text(s) -> str:
    """마크업이 섞여 들어올 수 있는 텍스트 필드(제목·부제·본문)용 정규화.

    적용 지점 주의: 이 함수는 `Content`(=LLM 에 들어가는 값)만 정제한다.
    콘텐츠 정체성 키 `store.content_hash` 는 정규화 이전의 '원본' dict 로 만들고,
    적재 payload 도 `store._payload_with_identity` 가 원본 4필드를 되박으므로
    기존 적재분의 해시·행 정체성은 바뀌지 않는다(tests/test_html_strip.py 가 가드).
    """
    return normalize_text(strip_html(s))


# 참조용 이미지 URL 상한(검수 표시 목적 · 과대 페이로드 방지)
MAX_IMAGE_URLS = 8


def normalize_image_urls(v) -> list:
    """참조용 이미지 URL 정규화: 단일 문자열·콤마 구분 문자열·목록 모두 수용.
    http/https 만 허용(스크립트 스킴의 저장형 XSS 차단 · serve._safe_url 과 같은 정책),
    중복 제거, 상위 MAX_IMAGE_URLS 개까지."""
    if v is None:
        return []
    items = v if isinstance(v, (list, tuple)) else str(v).split(",")
    out = []
    for u in items:
        u = str(u or "").strip()
        low = u.lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            continue
        if u not in out:
            out.append(u)
        if len(out) >= MAX_IMAGE_URLS:
            break
    return out


@dataclass
class Content:
    """입력 4필드 고정. 4필드 외는 받지도 추론하지도 않는다."""
    displayServiceName: str
    title: str
    subtitle: str = ""
    body: str = ""
    source_url: str = ""      # 참조용 원문 링크(추출 입력 아님 · 있으면 상세에서 '원문' 링크)
    # 참조용 이미지 URL 목록(추출 입력 아님 · source_url 과 같은 참조 패턴 ·
    # 회원 전용 원문의 사진 확인용으로 검수 상세에 표시). 해시·정체성(4필드)에는 불포함.
    image_urls: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Content":
        return cls(
            displayServiceName=normalize_text(d.get("displayServiceName", "")),
            # 제목·부제·본문은 크롤 원문이 마크업째 실려오는 필드 → HTML 제거까지 한다.
            # (displayServiceName·source_url 은 값/URL 이라 마크업 제거 대상 아님)
            title=normalize_rich_text(d.get("title", "")),
            subtitle=normalize_rich_text(d.get("subtitle", "")),
            body=normalize_rich_text(d.get("body", "")),
            source_url=normalize_text(d.get("source_url", "") or d.get("url", "")),
            image_urls=normalize_image_urls(d.get("image_urls") or d.get("images")),
        )

    def body_hash(self) -> str:
        return hashlib.sha1(self.body.encode("utf-8")).hexdigest()[:12]

    def ref(self) -> dict:
        return {
            "displayServiceName": self.displayServiceName,
            "title": self.title,
            "subtitle": self.subtitle,
            "source_url": self.source_url,
            "image_urls": list(self.image_urls or []),
            "body": self.body,
            "body_hash": self.body_hash(),
        }


@dataclass
class Routing:
    service_group: str            # media | ugc
    content_track: str            # text | image_only
    active_quality_metas: list = field(default_factory=list)


@dataclass
class HarmType:
    code: str
    routed_article: str
    scores: dict                  # {a,b,c,total}
    grade: str                    # GREEN | YELLOW | RED


@dataclass
class LegalMeta:
    enabled: bool = False
    harm_types: list = field(default_factory=list)
    representative_grade: str = "GREEN"
    representative_score: int = 0
    failed: bool = False          # 라우터/스코어러 호출 실패 → fail-closed(GREEN 유통 금지·사람 검수 보류)


@dataclass
class QualityMeta:
    finalGrade: str = "G"         # G | R (YELLOW 시 provisional)
    reasons: list = field(default_factory=list)   # 11종 ID 배열
    review: str = "auto"          # auto | yellow(사람 검수 필요)
    confidence: float | None = None   # 0~1 (YELLOW 판단 근거)
    review_reason: str = ""       # YELLOW 사유(불일치/중간대역)


@dataclass
class ItemMeta:
    # DNM 메타 체계(13. 프로젝트 기획 / 1312. 아이템 메타) 기준 필드명
    summary: str = ""                                     # 리드문 (생성 문장)
    entities: list = field(default_factory=list)          # 엔티티
    intent: list = field(default_factory=list)            # 인텐트 (속성 분류값)
    content_category: list = field(default_factory=list)  # 콘텐츠 카테고리(콘텐츠 단위 N개·복수 매핑)
    # 3차 메타(생성 시점 부여) · 사건형 토픽 식별·연결 신호. 1차 추출에서는 빈 값
    topic: str = ""                                       # 토픽 (사안 명사구)
    topic_categories: list = field(default_factory=list)  # 토픽 카테고리


@dataclass
class Trace:
    prompt_version: str = ""
    model: str = ""                        # 초안을 생성한 모델(검수·피드백 귀속용)
    agent_verdicts: list = field(default_factory=list)
    fallbacks: list = field(default_factory=list)
    latency_ms: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    tokens: dict = field(default_factory=dict)
    by_call: dict = field(default_factory=dict)   # 호출 태그별 {n·cost·in·out·ms} · 콜별 모델 구성 근거
    fails: list = field(default_factory=list)     # 콜 실패 표면화 [{tag,kind}] · 빈 산출 원인 진단(모델 A/B 등)


@dataclass
class Output:
    content_ref: dict
    routing: Routing
    legal_meta: LegalMeta
    quality_meta: QualityMeta
    item_meta: ItemMeta | None
    trace: Trace

    def to_dict(self, slim: bool = False) -> dict:
        """slim=True → 운영 출력(품질 2필드 + 아이템 4키). False → 전체(디버그)."""
        if slim:
            out = {
                "quality_meta": {
                    "finalGrade": self.quality_meta.finalGrade,
                    "reasons": self.quality_meta.reasons,
                },
            }
            if self.item_meta is not None:
                out["item_meta"] = asdict(self.item_meta)
            return out
        d = {
            "content_ref": self.content_ref,
            "routing": asdict(self.routing),
            "legal_meta": asdict(self.legal_meta),
            "quality_meta": asdict(self.quality_meta),
            "item_meta": asdict(self.item_meta) if self.item_meta is not None else None,
            "trace": asdict(self.trace),
        }
        return d
